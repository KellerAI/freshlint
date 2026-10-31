"""Findings and output formats: text, json, github."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from freshlint import __version__

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # "error" or "warning"
    path: str
    line: int
    message: str
    hint: str | None = None

    @property
    def sort_key(self) -> tuple:
        return (self.path, self.line, self.rule, self.message)


@dataclass(frozen=True)
class Summary:
    errors: int
    warnings: int
    files: int
    mode: str  # "full", "staged", "diff"
    range: str | None
    today: str

    @property
    def description(self) -> str:
        if self.mode == "staged":
            return "staged changes"
        if self.mode == "diff":
            return f"diff {self.range}"
        return "full scan"


def summarize(
    findings: list[Finding], files: int, mode: str, rng: str | None, today: str
) -> Summary:
    errors = sum(f.severity == "error" for f in findings)
    return Summary(errors, len(findings) - errors, files, mode, rng, today)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def format_text(findings: list[Finding], summary: Summary) -> str:
    out = []
    for f in findings:
        out.append(f"{f.path}:{f.line}: {f.severity} [{f.rule}] {f.message}")
        if f.hint:
            for index, hint_line in enumerate(f.hint.splitlines()):
                out.append(("    hint: " if index == 0 else "          ") + hint_line)
    out.append(
        f"freshlint: {_plural(summary.errors, 'error')}, {_plural(summary.warnings, 'warning')} "
        f"in {_plural(summary.files, 'file')} ({summary.description}, today {summary.today})"
    )
    return "\n".join(out) + "\n"


def format_json(findings: list[Finding], summary: Summary) -> str:
    payload = {
        "tool": "freshlint",
        "version": __version__,
        "schema": SCHEMA_VERSION,
        "mode": summary.mode,
        "range": summary.range,
        "today": summary.today,
        "summary": {"errors": summary.errors, "warnings": summary.warnings, "files": summary.files},
        "findings": [asdict(f) for f in findings],
    }
    return json.dumps(payload, indent=2) + "\n"


def _escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def format_github(findings: list[Finding], summary: Summary) -> str:
    out = []
    for f in findings:
        level = "error" if f.severity == "error" else "warning"
        message = f.message + (f"\nhint: {f.hint}" if f.hint else "")
        out.append(
            f"::{level} file={_escape_property(f.path)},line={f.line},"
            f"title={_escape_property('freshlint ' + f.rule)}::{_escape_data(message)}"
        )
    out.append(
        f"freshlint: {_plural(summary.errors, 'error')}, {_plural(summary.warnings, 'warning')} "
        f"in {_plural(summary.files, 'file')} ({summary.description}, today {summary.today})"
    )
    return "\n".join(out) + "\n"


FORMATTERS = {"text": format_text, "json": format_json, "github": format_github}
