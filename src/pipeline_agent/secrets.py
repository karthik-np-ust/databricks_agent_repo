"""Secret references. Config files never hold secret values, only references.

Supported now:  ``env://VAR_NAME``  (read from the environment / .env)
Planned:        ``kv://name``       (Azure Key Vault, Stage 2 pilot)
"""

from __future__ import annotations

import os


class SecretError(RuntimeError):
    pass


class EnvSecretResolver:
    def __init__(self, environ: dict[str, str] | None = None):
        self._environ = environ if environ is not None else os.environ

    def get(self, ref: str) -> str:
        if ref.startswith("env://"):
            name = ref.removeprefix("env://")
            value = self._environ.get(name)
            if not value:
                raise SecretError(f"Environment variable {name} is not set")
            return value
        if ref.startswith("kv://"):
            raise SecretError("Key Vault references (kv://) are not supported yet; planned for the Stage 2 pilot")
        raise SecretError("Secret values must be references like env://NAME, never plain values in config")

    def resolve_all(self, refs: dict[str, str]) -> dict[str, str]:
        return {key: self.get(ref) for key, ref in refs.items()}
