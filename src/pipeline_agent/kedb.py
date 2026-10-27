"""Known Error DB: structured, curated knowledge of failure signatures and how to recover.

Seeded from the YAML knowledge packs (core + plugins) as approved entries. Agents may only
*propose* new entries (status ``proposed``); an admin approves them in the console before
triage ever matches them: the curated write path. Matching checks approved entries in
priority order (learned < platform < generic) and returns the first hit.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from sqlalchemy import select

from .sdk.models import utcnow
from .store.db import KnownErrorRow, StateStore

RUNBOOK_BY_CLASS = {"transient": "rerun", "code": "code_fix", "infra": "escalate", "unknown": "escalate"}
PRIORITY = {"learned": 50, "platform": 100, "generic": 200}


class KnownError(BaseModel):
    id: int
    name: str
    signature: str
    platform: str
    error_class: str
    runbook: str
    diagnostics: str
    fix_hint: str
    risk: str
    rollback: str
    provenance: str
    status: str

    @classmethod
    def of(cls, row: KnownErrorRow) -> "KnownError":
        return cls.model_validate(row, from_attributes=True)


def read_pack(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Raw rules of a knowledge pack; no path = the core generic pack."""
    if path is None:
        text = resources.files("pipeline_agent").joinpath("knowledge/generic_errors.yaml").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return (yaml.safe_load(text) or {}).get("rules", [])


def plugin_packs() -> list[tuple[str, list[dict[str, Any]]]]:
    """(platform, rules) for every installed plugin that ships knowledge/errors.yaml."""
    from .sdk.registry import discover_plugins

    packs = []
    for name, cls in sorted(discover_plugins().items()):
        pack = resources.files(cls.__module__.rsplit(".", 1)[0]).joinpath("knowledge/errors.yaml")
        if pack.is_file():
            packs.append((name, (yaml.safe_load(pack.read_text(encoding="utf-8")) or {}).get("rules", [])))
    return packs


class KnownErrorDB:
    def __init__(self, store: StateStore):
        self.store = store

    # curation --------------------------------------------------------------
    def seed(self, platform: str, pack: str, rules: list[dict[str, Any]]) -> int:
        """Add a pack's rules as approved entries. Names already present for this platform are left alone (curated edits win)."""
        now = utcnow()
        added = 0
        with self.store.session() as s:
            existing = set(s.scalars(select(KnownErrorRow.name).where(KnownErrorRow.platform == platform)).all())
            for item in rules:
                if item["name"] in existing:
                    continue
                cls = item["class"]
                s.add(
                    KnownErrorRow(
                        name=item["name"], signature=item["pattern"], platform=platform,
                        priority=PRIORITY["generic" if platform == "*" else "platform"], error_class=cls,
                        runbook=item.get("runbook", RUNBOOK_BY_CLASS[cls]), diagnostics=item.get("diagnostics", ""),
                        fix_hint=item.get("fix_hint", ""), risk=item.get("risk", "medium"), rollback=item.get("rollback", ""),
                        provenance=f"seed:{pack}", status="approved", created_at=now, updated_at=now, approved_by="seed",
                    )
                )
                added += 1
            s.commit()
        return added

    def seed_all(self) -> dict[str, int]:
        """Core generic pack + every installed plugin's pack."""
        added = {"generic": self.seed("*", "generic", read_pack())}
        for platform, rules in plugin_packs():
            added[platform] = self.seed(platform, platform, rules)
        return added

    def propose(
        self, name: str, signature: str, error_class: str, provenance: str, *, platform: str = "*", runbook: str | None = None,
        diagnostics: str = "", fix_hint: str = "", risk: str = "medium", rollback: str = "",
    ) -> KnownError:
        re.compile(signature)  # reject an invalid regex up front
        now = utcnow()
        with self.store.session() as s:
            row = s.scalar(select(KnownErrorRow).where(KnownErrorRow.platform == platform, KnownErrorRow.name == name))
            if row is None:
                row = KnownErrorRow(
                    name=name, signature=signature, platform=platform, priority=PRIORITY["learned"], error_class=error_class,
                    runbook=runbook or RUNBOOK_BY_CLASS[error_class], diagnostics=diagnostics, fix_hint=fix_hint, risk=risk,
                    rollback=rollback, provenance=provenance, status="proposed", created_at=now, updated_at=now,
                )
                s.add(row)
                s.commit()
            return KnownError.of(row)

    def set_status(self, entry_id: int, status: str, by: str) -> KnownError:
        if status not in ("approved", "retired", "proposed"):
            raise ValueError(f"unknown status {status!r}")
        with self.store.session() as s:
            row = s.get(KnownErrorRow, entry_id)
            if row is None:
                raise KeyError(entry_id)
            row.status, row.updated_at = status, utcnow()
            if status == "approved":
                row.approved_by = by
            s.commit()
            return KnownError.of(row)

    def approve(self, entry_id: int, by: str) -> KnownError:
        return self.set_status(entry_id, "approved", by)

    # lookup ----------------------------------------------------------------
    def list(self, status: str | None = None) -> list[KnownError]:
        with self.store.session() as s:
            q = select(KnownErrorRow).order_by(KnownErrorRow.priority, KnownErrorRow.id)
            if status:
                q = q.where(KnownErrorRow.status == status)
            return [KnownError.of(r) for r in s.scalars(q).all()]

    def match(self, text: str, platform: str) -> KnownError | None:
        for entry in self.list(status="approved"):
            if entry.platform not in (platform, "*"):
                continue
            if re.search(entry.signature, text or "", re.IGNORECASE | re.MULTILINE):
                return entry
        return None
