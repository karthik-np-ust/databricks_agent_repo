"""Evidence store: content-addressed, redacted artefacts (failure context, test output, profiles, diffs).

Blobs are stored by the sha256 of their (redacted) bytes, so storing the same thing twice is a
no-op, and an id proves the content hasn't changed. Metadata lives in the ``evidence`` table.
The local directory backend stands in for customer object storage (Azure Blob in the pilot).

Untrusted-evidence rule: logs and tickets are data, never instructions. ``as_prompt`` is the
only way evidence reaches a model, and it always frames it as untrusted.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import select

from .sdk.models import utcnow
from .security.redact import redact
from .store.db import EvidenceRow, StateStore


class BlobBackend(Protocol):
    def put(self, digest: str, data: bytes) -> None: ...
    def get(self, digest: str) -> bytes: ...


class LocalBlobs:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest

    def put(self, digest: str, data: bytes) -> None:
        path = self._path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def get(self, digest: str) -> bytes:
        return self._path(digest).read_bytes()


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    kind: str
    size: int
    redactions: int


class EvidenceStore:
    def __init__(self, store: StateStore, blobs: BlobBackend):
        self.store = store
        self.blobs = blobs

    def put_text(
        self, text: str, *, kind: str, source: str, incident_id: int | None = None, run_key: str | None = None,
        media_type: str = "text/plain",
    ) -> EvidenceRef:
        cleaned = redact(text or "")
        data = cleaned.text.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        self.blobs.put(digest, data)
        with self.store.session() as s:
            if s.get(EvidenceRow, digest) is None:
                s.add(
                    EvidenceRow(
                        id=digest, created_at=utcnow(), kind=kind, media_type=media_type, size=len(data), source=source,
                        incident_id=incident_id, run_key=run_key, redactions=cleaned.count,
                    )
                )
                s.commit()
        return EvidenceRef(digest, kind, len(data), cleaned.count)

    def get_text(self, evidence_id: str) -> str:
        data = self.blobs.get(evidence_id)
        if hashlib.sha256(data).hexdigest() != evidence_id:
            raise ValueError(f"evidence {evidence_id} failed its integrity check")
        return data.decode("utf-8")

    def meta(self, evidence_id: str) -> EvidenceRow | None:
        with self.store.session() as s:
            return s.get(EvidenceRow, evidence_id)

    def for_incident(self, incident_id: int) -> list[EvidenceRow]:
        with self.store.session() as s:
            return list(s.scalars(select(EvidenceRow).where(EvidenceRow.incident_id == incident_id).order_by(EvidenceRow.created_at)).all())

    def as_prompt(self, evidence_id: str, max_chars: int = 12_000) -> str:
        meta = self.meta(evidence_id)
        kind = meta.kind if meta else "unknown"
        body = self.get_text(evidence_id)[:max_chars]
        return f'<untrusted_evidence id="{evidence_id[:12]}" kind="{kind}">\n{body}\n</untrusted_evidence>'
