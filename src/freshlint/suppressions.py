"""Inline suppressions: temporary, reasoned, expiring waivers.

Syntax::

    <!-- freshlint: allow=RULE[,RULE] reason="why this is fine" expires=YYYY-MM-DD -->

Scope:

* on its own line before the first heading: the whole file
* on its own line inside a section: that section, including its subsections
* at the end of a content line: that line only

Only state rules that describe a persistent condition can be suppressed. Diff rules
(laundering, stale-refresh, receipts) describe a one-time event and are resolved
with a receipt instead.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from freshlint.catalog import RULES
from freshlint.dates import parse_date
from freshlint.markdown import Document

CANDIDATE_RE = re.compile(r"<!--\s*freshlint:")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
COMMENT_RE = re.compile(r"<!--\s*freshlint:(?P<body>.*?)-->")
BODY_RE = re.compile(
    r'^\s*allow=(?P<rules>[a-z0-9,-]+)\s+reason="(?P<reason>[^"]*)"\s+expires=(?P<expires>\S+)\s*$'
)
EXAMPLE = '<!-- freshlint: allow=RULE reason="why" expires=YYYY-MM-DD -->'


@dataclass(frozen=True)
class Suppression:
    rules: tuple[str, ...]
    reason: str
    expires: dt.date
    line: int
    scope: tuple[int, int]


def _scope(doc: Document, line_number: int, own_line: bool) -> tuple[int, int]:
    if not own_line:
        return (line_number, line_number)
    section = doc.section_for_line(line_number)
    if section is None:
        return (1, len(doc.lines))
    return (section.line, section.end_line)


def parse_suppressions(
    doc: Document, today: dt.date, max_days: int
) -> tuple[list[Suppression], list[tuple[int, str]]]:
    valid: list[Suppression] = []
    problems: list[tuple[int, str]] = []
    limit = today + dt.timedelta(days=max_days)
    for index in range(doc.body_start, len(doc.lines)):
        number = index + 1
        line = INLINE_CODE_RE.sub("", doc.lines[index])
        if number in doc.code_lines or not CANDIDATE_RE.search(line):
            continue
        comments = list(COMMENT_RE.finditer(line))
        if len(comments) != len(CANDIDATE_RE.findall(line)):
            problems.append((number, f"malformed suppression (expected {EXAMPLE})"))
            continue
        own_line = not COMMENT_RE.sub("", doc.lines[index]).strip()
        for comment in comments:
            body = BODY_RE.match(comment.group("body"))
            if not body:
                problems.append((number, f"malformed suppression (expected {EXAMPLE})"))
                continue
            rules = tuple(r for r in body.group("rules").split(",") if r)
            reason = body.group("reason").strip()
            expires = parse_date(body.group("expires"))
            problem = None
            unknown = [r for r in rules if r not in RULES]
            locked = [r for r in rules if r in RULES and not RULES[r].suppressible]
            if not rules:
                problem = "suppression names no rule"
            elif unknown:
                problem = f"unknown rule {unknown[0]!r} in suppression"
            elif locked:
                problem = (
                    f"rule {locked[0]!r} cannot be suppressed inline "
                    "(diff rules are resolved with a receipt)"
                )
            elif not reason:
                problem = "suppression has no reason; say why the exception is legitimate"
            elif expires is None:
                problem = f"expires={body.group('expires')} is not an ISO date (YYYY-MM-DD)"
            elif expires < today:
                problem = (
                    f"suppression for {', '.join(rules)} expired on {expires.isoformat()}; "
                    "fix the underlying finding or renew it with a new reason"
                )
            elif expires > limit:
                problem = (
                    f"suppression for {', '.join(rules)} runs until {expires.isoformat()}, "
                    f"more than {max_days} days from today"
                )
            if problem:
                problems.append((number, problem))
                continue
            valid.append(Suppression(rules, reason, expires, number, _scope(doc, number, own_line)))
    return valid, problems


def is_suppressed(suppressions: list[Suppression], rule: str, line: int) -> bool:
    return any(rule in s.rules and s.scope[0] <= line <= s.scope[1] for s in suppressions)
