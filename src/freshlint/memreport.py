"""Text for the agent and the terminal: session report, save feedback, full scan."""

from __future__ import annotations

import json
from dataclasses import asdict

from freshlint import __version__
from freshlint.checks import NoteFinding, ScanResult

MAX_LINES = 10
MAX_ITEMS = 7
FOOTER = (
    "When one of these becomes relevant, re-read the cited code, then update or delete the memory."
)
_ORDER = {
    "gone": 0,
    "changed": 1,
    "unread": 2,
    "uncited-claim": 3,
    "inferred-changed": 4,
    "invalid-citation": 5,
    "misplaced-evidence": 6,
}


def _count(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _verb(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _sorted(findings: list[NoteFinding]) -> list[NoteFinding]:
    return sorted(findings, key=lambda f: (_ORDER.get(f.state, 9), f.note, f.target))


def _notes_with(result: ScanResult, *states: str) -> int:
    return len({f.note for f in result.findings if f.state in states})


def summary_line(result: ScanResult) -> str | None:
    """The first-session summary, or None when it has nothing to count.

    It leaves out the inferred "changed" count on purpose: a changed file mentioned by an older
    memory was too often not a real problem, so those stay in the full scan,
    ``/freshlint:memory-check``. With no missing files and no uncited claims it stays silent
    instead of ending with a call to action over two zeros.
    """
    gone = _notes_with(result, "gone")
    claims = _notes_with(result, "uncited-claim")
    if not gone and not claims:
        return None
    return (
        f"freshlint: {_count(result.notes, 'memory', 'memories')}. "
        f"{result.citing} {_verb(result.citing, 'cites', 'cite')} code: "
        f"{gone} {_verb(gone, 'cites', 'cite')} files that no longer exist. "
        f"{claims} {_verb(claims, 'claims', 'claim')} certainty "
        "without evidence. Run /freshlint:memory-check to review."
    )


def session_report(result: ScanResult, *, first_run: bool, corrupt: bool = False) -> str | None:
    items = _sorted([f for f in result.findings if f.reported])
    lines: list[str] = []
    if corrupt:
        lines.append(
            "freshlint: the evidence store could not be read and was rebuilt; earlier "
            "fingerprints are lost."
        )
    summary = summary_line(result) if first_run else None
    if summary:
        lines.append(summary)
    if not items and not lines and not result.partial:
        return None
    if items and not summary:  # the first-session summary stands in for this heading
        count = len({f.note for f in items})
        noun = _count(count, "memory needs", "memories need")
        lines.append(
            f"freshlint: {noun} re-checking before you rely on {_verb(count, 'it', 'them')}."
        )
    if items:
        budget = MAX_LINES - len(lines) - 1 - int(result.partial)
        room = min(MAX_ITEMS, budget)
        if len(items) > room:
            room = min(room, budget - 1)
        lines.extend(" ".join(f"- {f.note}: {f.message}".split()) for f in items[:room])
        if len(items) > room:
            lines.append(f"+{len(items) - room} more: run /freshlint:memory-check.")
        lines.append(FOOTER)
    if result.partial:
        lines.append(
            "(The scan stopped early to keep startup fast; "
            "run /freshlint:memory-check for the rest.)"
        )
    return "\n".join(lines)


def save_feedback(lines: list[str]) -> str:
    return "\n".join(f"freshlint: {line}" for line in lines)


def full_report(result: ScanResult) -> str:
    out = [
        f"freshlint memory scan: {_count(result.notes, 'memory', 'memories')}, "
        f"{result.citing} cite code."
    ]
    for f in _sorted(result.findings):
        tag = f.state if f.confidence == "high" else f"{f.state}, low confidence"
        out.append(f"{f.note}: [{tag}] {f.message}")
    if not result.findings:
        out.append("No findings.")
    if result.partial:
        out.append("(scan incomplete)")
    return "\n".join(out) + "\n"


def json_report(result: ScanResult) -> str:
    payload = {
        "tool": "freshlint",
        "version": __version__,
        "schema": 1,
        "notes": result.notes,
        "citing": result.citing,
        "partial": result.partial,
        "findings": [dict(asdict(f), reported=f.reported) for f in _sorted(result.findings)],
    }
    return json.dumps(payload, indent=2) + "\n"
