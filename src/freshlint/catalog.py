"""The rule catalog. Rule IDs are stable: renaming one is a breaking change."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    id: str
    default: str  # "error", "warning" or "off"
    mode: str  # "state" (any scan) or "diff" (--staged / --diff only)
    suppressible: bool
    summary: str


RULES: dict[str, Rule] = {
    rule.id: rule
    for rule in [
        Rule(
            "review-expired",
            "error",
            "state",
            True,
            "A review_by or stale_after date has passed (warning when it is close).",
        ),
        Rule(
            "missing-marker",
            "warning",
            "state",
            True,
            "A governed file or section carries no freshness metadata.",
        ),
        Rule(
            "invalid-marker",
            "error",
            "state",
            False,
            "Freshness metadata is malformed, misplaced, contradictory, or dated in the future.",
        ),
        Rule(
            "temporal-language",
            "warning",
            "state",
            True,
            "Governed content uses relative time words or a deadline that has already passed.",
        ),
        Rule(
            "suppression",
            "error",
            "state",
            False,
            "An inline suppression is malformed, has no reason, has expired, or lasts too long.",
        ),
        Rule(
            "receipt-ordering",
            "error",
            "state",
            False,
            "A receipt log entry is malformed, out of date order, or dated in the future.",
        ),
        Rule(
            "freshness-laundering",
            "error",
            "diff",
            False,
            "A freshness claim moved forward but the content it covers did not change.",
        ),
        Rule(
            "stale-refresh",
            "error",
            "diff",
            False,
            "Governed content changed but its verified date did not move.",
        ),
        Rule(
            "receipt-append-only",
            "error",
            "diff",
            False,
            "An existing verification receipt was edited or removed.",
        ),
    ]
}

DOCS_BASE = "docs/rules"


def docs_path(rule_id: str) -> str:
    return f"{DOCS_BASE}/{rule_id}.md"
