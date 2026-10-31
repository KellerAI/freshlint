"""Evidence lines: the code a memory note says it rests on.

A note cites evidence on lines such as::

    **Evidence:** `src/auth/session.ts#refreshToken`, `package.json#scripts.test`

Citation forms: ``PATH`` (whole file), ``PATH#L10`` or ``PATH#L10-L24`` (lines),
``PATH#name`` or ``PATH#Class.method`` (a symbol), and ``PATH#a.b.c`` (a key path in a
``.json`` or ``.toml`` file). ``PATH`` is relative to the project root, or starts with
``~/`` or ``/``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from freshlint.markdown import prose_lines

EVIDENCE_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*|__)?\s*evidence\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?(?P<rest>.*)$",
    re.IGNORECASE,
)
CODE_SPAN_RE = re.compile(r"`([^`]+)`")
MENTION_RE = re.compile(  # "evidence" as a word (not "my_evidence"), maybe wrapped in ** or __
    r"(?:(?<!\w)|(?<=__))evidence(?![^\W_])(?:\*\*|__)?\s*:(?P<rest>.*)$", re.IGNORECASE
)
LINES_RE = re.compile(r"^L(\d+)(?:-L?(\d+))?$")
NAME_RE = re.compile(r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$")
KEY_RE = re.compile(r"^[^.\s]+(?:\.[^.\s]+)*$")
KEY_SUFFIXES = (".json", ".toml")
MAX_LINE_DIGITS = 9  # no file has a billion lines; longer numbers are rejected before int()


class CitationError(ValueError):
    """A citation that cannot be understood."""


@dataclass(frozen=True)
class Citation:
    raw: str
    path: str
    kind: str  # "file", "lines", "symbol" or "key"
    start: int | None = None
    end: int | None = None
    name: str | None = None


@dataclass(frozen=True)
class EvidenceBlock:
    citations: tuple[Citation, ...]
    problems: tuple[tuple[int, str], ...]
    lines: frozenset[int]  # every Evidence line, parsed or not
    cited_lines: frozenset[int] = frozenset()  # the Evidence lines with a parsed citation


def _shown(text: str) -> str:
    return text if len(text) <= 60 else text[:60] + "..."


def parse_citation(raw: str) -> Citation:
    text = raw.strip()
    if not text:
        raise CitationError("empty citation")
    if "://" in text:
        raise CitationError(f"`{text}` is a URL; cite a file")
    path, _, fragment = text.partition("#")
    if path.startswith("./"):
        path = path[2:]
    if not path:
        raise CitationError(f"`{text}` has no file path")
    if not fragment:
        return Citation(text, path, "file")
    lines = LINES_RE.match(fragment)
    if lines:
        invalid = f"`{_shown(text)}` has an invalid line range"
        if any(len(number) > MAX_LINE_DIGITS for number in lines.groups() if number):
            raise CitationError(invalid)
        try:
            start = int(lines.group(1))
            end = int(lines.group(2) or start)
        except ValueError:
            raise CitationError(invalid) from None
        if start < 1 or end < start:
            raise CitationError(invalid)
        return Citation(text, path, "lines", start, end)
    if path.lower().endswith(KEY_SUFFIXES):
        if not KEY_RE.match(fragment):
            raise CitationError(f"`{text}`: `{fragment}` is not a key path")
        return Citation(text, path, "key", name=fragment)
    if not NAME_RE.match(fragment):
        raise CitationError(f"`{text}`: `{fragment}` is not a symbol name")
    return Citation(text, path, "symbol", name=fragment)


def parse_evidence(text: str) -> EvidenceBlock:
    citations: list[Citation] = []
    problems: list[tuple[int, str]] = []
    lines: set[int] = set()
    cited_lines: set[int] = set()
    seen: set[str] = set()
    for number, line in prose_lines(text):
        match = EVIDENCE_RE.match(line)
        if not match:
            continue
        lines.add(number)
        for raw in CODE_SPAN_RE.findall(match.group("rest")):
            try:
                citation = parse_citation(raw)
            except CitationError as exc:
                problems.append((number, str(exc)))
                continue
            cited_lines.add(number)
            if citation.raw not in seen:
                seen.add(citation.raw)
                citations.append(citation)
    return EvidenceBlock(
        tuple(citations), tuple(problems), frozenset(lines), frozenset(cited_lines)
    )


def misplaced_evidence_lines(text: str) -> list[tuple[int, str]]:
    """Lines that name evidence and code but are not Evidence lines, so nothing is cited.

    ``Retries wait 30s. Evidence: `src/a.py#f` returns 30.`` is the typical case: the label is
    not at the start of the line, so ``parse_evidence`` reads no citation from it.
    """
    result: list[tuple[int, str]] = []
    for number, line in prose_lines(text):
        if EVIDENCE_RE.match(line):
            continue
        mention = MENTION_RE.search(line)
        if mention and CODE_SPAN_RE.search(mention.group("rest")):
            result.append((number, line))
    return result


def resolve(path: str, project_root: Path, home: Path) -> Path:
    if path.startswith("~/"):
        return home / path[2:]
    candidate = Path(path)
    return candidate if candidate.is_absolute() else project_root / candidate
