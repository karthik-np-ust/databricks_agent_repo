"""Ed25519 signatures for the trust boundary.

The control plane holds the private key and signs every ActionRequest; the data plane holds
only public keys and refuses anything it can't verify. Payloads are signed as canonical JSON
(sorted keys, no whitespace), so the same request always produces the same bytes.

    pipeline-agent keys generate --out var/keys
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PRIVATE_KEY_FILE = "signing_key.pem"
PUBLIC_KEY_FILE = "signing_key.pub.pem"


class SignatureError(Exception):
    pass


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("ascii")


def key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


class Signer:
    def __init__(self, private_key: Ed25519PrivateKey):
        self._key = private_key
        self.key_id = key_id(private_key.public_key())

    @classmethod
    def generate(cls) -> "Signer":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_file(cls, path: Path) -> "Signer":
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise SignatureError(f"{path} is not an Ed25519 private key")
        return cls(key)

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._key.public_key()

    def sign(self, payload: dict[str, Any]) -> str:
        return base64.b64encode(self._key.sign(canonical_json(payload))).decode("ascii")

    def write(self, directory: Path) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        private = directory / PRIVATE_KEY_FILE
        public = directory / PUBLIC_KEY_FILE
        private.write_bytes(
            self._key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        )
        public.write_bytes(public_pem(self.public_key))
        return private, public


def public_pem(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


class Verifier:
    """Trusted public keys by key id (several, so keys can be rotated)."""

    def __init__(self, public_keys: list[Ed25519PublicKey]):
        self._keys = {key_id(k): k for k in public_keys}

    @classmethod
    def from_files(cls, paths: list[Path]) -> "Verifier":
        keys = []
        for path in paths:
            key = serialization.load_pem_public_key(Path(path).read_bytes())
            if not isinstance(key, Ed25519PublicKey):
                raise SignatureError(f"{path} is not an Ed25519 public key")
            keys.append(key)
        return cls(keys)

    @property
    def key_ids(self) -> list[str]:
        return sorted(self._keys)

    def verify(self, payload: dict[str, Any], signature: str, signer_key_id: str) -> None:
        key = self._keys.get(signer_key_id)
        if key is None:
            raise SignatureError(f"unknown signing key {signer_key_id}")
        try:
            key.verify(base64.b64decode(signature), canonical_json(payload))
        except (InvalidSignature, ValueError) as exc:
            raise SignatureError("signature does not match the request") from exc
