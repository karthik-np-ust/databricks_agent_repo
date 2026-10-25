"""Rule-based error classification (first pass, before any LLM is involved).

Rules come from YAML "knowledge packs": the core ships generic rules and each
plugin ships platform rules, which are checked first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from .models import ErrorClass


@dataclass(frozen=True)
class ErrorRule:
    name: str
    pattern: re.Pattern[str]
    error_class: ErrorClass


def parse_rules(data: dict) -> list[ErrorRule]:
    rules = []
    for item in data.get("rules", []):
        rules.append(
            ErrorRule(
                name=item["name"],
                pattern=re.compile(item["pattern"], re.IGNORECASE | re.MULTILINE),
                error_class=ErrorClass(item["class"]),
            )
        )
    return rules


def load_rules(path: str | Path) -> list[ErrorRule]:
    return parse_rules(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})


def default_rules() -> list[ErrorRule]:
    text = resources.files("pipeline_agent").joinpath("knowledge/generic_errors.yaml").read_text(encoding="utf-8")
    return parse_rules(yaml.safe_load(text) or {})


class ErrorClassifier:
    def __init__(self, rules: list[ErrorRule]):
        self.rules = rules

    @classmethod
    def with_defaults(cls, platform_rules: list[ErrorRule] | None = None) -> "ErrorClassifier":
        # Platform rules first: they are more specific than the generic ones.
        return cls([*(platform_rules or []), *default_rules()])

    def classify(self, text: str) -> tuple[ErrorClass, str | None]:
        for rule in self.rules:
            if rule.pattern.search(text or ""):
                return rule.error_class, rule.name
        return ErrorClass.UNKNOWN, None
