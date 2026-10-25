"""Notifications: console (local dev), Microsoft Teams, Slack, or a generic JSON webhook."""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from ..security.redact import redact_text

log = logging.getLogger(__name__)


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Notification:
    title: str
    summary: str
    severity: Severity = Severity.INFO
    facts: dict[str, str] = field(default_factory=dict)
    url: str | None = None

    def redacted(self) -> "Notification":
        return Notification(
            title=redact_text(self.title),
            summary=redact_text(self.summary),
            severity=self.severity,
            facts={k: redact_text(str(v)) for k, v in self.facts.items()},
            url=self.url,
        )


class Notifier(Protocol):
    def send(self, notification: Notification) -> None: ...


class ConsoleNotifier:
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> None:
        n = notification.redacted()
        self.sent.append(n)
        facts = ", ".join(f"{k}={v}" for k, v in n.facts.items())
        log.warning("[%s] %s | %s | %s %s", n.severity.upper(), n.title, n.summary, facts, n.url or "")


def teams_payload(n: Notification) -> dict:
    # Adaptive Card, as accepted by Teams "Workflows" incoming webhooks.
    body = [
        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": n.title, "wrap": True},
        {"type": "TextBlock", "text": n.summary, "wrap": True},
        {"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in n.facts.items()]},
    ]
    card = {"type": "AdaptiveCard", "version": "1.4", "body": body}
    if n.url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open run", "url": n.url}]
    card["$schema"] = "http://adaptivecards.io/schemas/adaptive-card.json"
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


def slack_payload(n: Notification) -> dict:
    facts = "\n".join(f"*{k}:* {v}" for k, v in n.facts.items())
    link = f"\n<{n.url}|Open run>" if n.url else ""
    return {"text": f"*[{n.severity.upper()}] {n.title}*\n{n.summary}\n{facts}{link}"}


def generic_payload(n: Notification) -> dict:
    return {"title": n.title, "summary": n.summary, "severity": n.severity.value, "facts": n.facts, "url": n.url}


class WebhookNotifier:
    _builders = {"teams": teams_payload, "slack": slack_payload, "generic": generic_payload}

    def __init__(self, url: str, kind: str = "generic", timeout: float = 10.0):
        if kind not in self._builders:
            raise ValueError(f"Unknown webhook kind {kind!r}")
        self.url, self.kind, self.timeout = url, kind, timeout

    def send(self, notification: Notification) -> None:
        payload = self._builders[self.kind](notification.redacted())
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (URL comes from config)
                resp.read()
        except Exception as exc:  # a failed notification must not stop monitoring
            log.error("Webhook notification failed: %s", exc)
