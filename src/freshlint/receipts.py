"""Verification receipts: the honest way to re-verify without editing content.

A receipt log is a markdown file of dated entries::

    # Verifications

    ## 2026-09-30
    - verified context/shipping.md#carriers — re-checked rates on the carrier portal
    - edited context/company.md#overview — typo fix; facts not re-verified

Entry grammar: ``- [YYYY-MM-DD ]verified|edited PATH[#slug] (— | -- | -) NOTE``.
An entry without its own date inherits the nearest ``## YYYY-MM-DD`` heading.
Paths are relative to the project root. The note is required.

* ``verified`` covers a freshness bump with no content change, when the receipt
  date equals the new verified date and it is added in the same diff.
* ``edited`` covers a content change that deliberately did not re-verify the facts.

Logs are append-only: dates must be ISO and in order (oldest first by default).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from freshlint.dates import parse_date
from freshlint.frontmatter import split_frontmatter
from freshlint.markdown import FENCE_OPEN_RE, HEADING_RE

RECEIPT_RE = re.compile(
    r"^\s*[-*+]\s+(?:(?P<date>\S+)\s+)?(?P<verb>verified|edited)\s+"
    r"(?P<target>\S+\.md(?:#\S*)?)(?P<rest>.*)$"
)
NOTE_RE = re.compile(r"^\s+(?:—|–|--|-)\s+(?P<note>\S.*)$")
EXAMPLE = "- YYYY-MM-DD verified path/to/file.md#section-slug — what you checked"


@dataclass(frozen=True)
class Receipt:
    date: dt.date
    verb: str
    path: str
    slug: str | None
    note: str
    line: int

    @property
    def target(self) -> str:
        return self.path + (f"#{self.slug}" if self.slug else "")

    @property
    def key(self) -> tuple:
        return (self.date, self.verb, self.path, self.slug, self.note)

    def render(self) -> str:
        return f"{self.date.isoformat()} {self.verb} {self.target}"


def _split_target(target: str) -> tuple[str, str | None]:
    path, _, slug = target.partition("#")
    if path.startswith("./"):
        path = path[2:]
    return path, (slug.lower() or None)


def parse_receipt_log(
    text: str,
) -> tuple[list[Receipt], list[tuple[int, str]], list[tuple[int, dt.date]]]:
    """Return ``(receipts, problems, dated_items)`` for one log file."""
    lines = [line.rstrip("\r") for line in text.splitlines()]
    _, start = split_frontmatter(lines)
    receipts: list[Receipt] = []
    problems: list[tuple[int, str]] = []
    dated: list[tuple[int, dt.date]] = []
    current: dt.date | None = None
    fence: tuple[str, int] | None = None
    for index in range(start, len(lines)):
        number = index + 1
        line = lines[index]
        if fence is not None:
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= fence[1]:
                fence = None
            continue
        opened = FENCE_OPEN_RE.match(line)
        if opened:
            fence = (opened.group(1)[0], len(opened.group(1)))
            continue
        heading = HEADING_RE.match(line)
        if heading:
            if len(heading.group(1)) == 2:
                title = (heading.group(2) or "").strip()
                current = parse_date(title)
                if current is None:
                    problems.append(
                        (
                            number,
                            f"receipt log headings must be ISO dates (## YYYY-MM-DD): {title!r}",
                        )
                    )
                else:
                    dated.append((number, current))
            continue
        match = RECEIPT_RE.match(line)
        if not match:
            continue
        note = NOTE_RE.match(match.group("rest"))
        if not note:
            problems.append((number, f"receipt needs a note after ' — ' (expected {EXAMPLE})"))
            continue
        if match.group("date"):
            day = parse_date(match.group("date"))
            if day is None:
                problems.append(
                    (number, f"receipt date {match.group('date')!r} is not an ISO date")
                )
                continue
            dated.append((number, day))
        elif current is not None:
            day = current
        else:
            problems.append((number, "receipt has no date (add one or put it under ## YYYY-MM-DD)"))
            continue
        path, slug = _split_target(match.group("target"))
        receipts.append(
            Receipt(day, match.group("verb"), path, slug, note.group("note").strip(), number)
        )
    return receipts, problems, dated


def check_order(dated: list[tuple[int, dt.date]], order: str) -> list[tuple[int, str]]:
    """Dates must be non-decreasing (ascending) or non-increasing (descending)."""
    problems: list[tuple[int, str]] = []
    best: tuple[int, dt.date] | None = None
    for line, day in dated:
        if best is not None:
            out_of_order = day < best[1] if order == "ascending" else day > best[1]
            if out_of_order:
                word = "earlier" if order == "ascending" else "later"
                expected = "oldest first" if order == "ascending" else "newest first"
                problems.append(
                    (
                        line,
                        f"date {day.isoformat()} is {word} than {best[1].isoformat()} on line "
                        f"{best[0]}; receipt logs are append-only ({expected})",
                    )
                )
                continue
        best = (line, day)
    return problems
