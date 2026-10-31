"""Freshness claims: section markers and file-level frontmatter.

Section marker syntax (one HTML comment on its own line)::

    <!-- memory: verified=YYYY-MM-DD review_by=YYYY-MM-DD -->
    <!-- memory: verified=YYYY-MM-DD cadence=evergreen -->

File-level frontmatter keys: ``last_verified`` (or ``last-verified``), ``review_by``
(or ``review-by``), ``cadence: evergreen``, and the OKF v0.2 trust and lifecycle
fields ``verified`` (a ``{by, at}`` mapping or a list of them), ``stale_after`` and
``status``.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from freshlint.dates import parse_date, parse_instant_date
from freshlint.frontmatter import Node

MARKER_START_RE = re.compile(r"^\s*<!--\s*memory:")
MARKER_RE = re.compile(r"^\s*<!--\s*memory:(?P<body>.*?)-->\s*$")
MARKER_KEYS = ("verified", "review_by", "cadence")
MARKER_EXAMPLE = "<!-- memory: verified=YYYY-MM-DD review_by=YYYY-MM-DD -->"


@dataclass(frozen=True)
class Freshness:
    """One freshness claim, anchored at a line."""

    line: int
    source: str  # "marker" or "frontmatter"
    verified: dt.date | None
    review_by: dt.date | None = None
    stale_after: dt.date | None = None  # first day on which the content is stale
    evergreen: bool = False
    deprecated: bool = False
    events: tuple[tuple[str, str], ...] = ()

    def expires_on(self, max_age_days: int = 0) -> dt.date | None:
        """First calendar day on which this claim is out of date, or None."""
        if self.evergreen:
            return None
        candidates = []
        if self.review_by is not None:
            candidates.append(self.review_by + dt.timedelta(days=1))
        if self.stale_after is not None:
            candidates.append(self.stale_after)
        if candidates:
            return min(candidates)
        if max_age_days and self.verified is not None:
            return self.verified + dt.timedelta(days=max_age_days + 1)
        return None

    def deadline_label(self, max_age_days: int = 0) -> str:
        expires = self.expires_on(max_age_days)
        if expires is None:
            return ""
        if self.review_by is not None and expires == self.review_by + dt.timedelta(days=1):
            return f"review_by={self.review_by.isoformat()}"
        if self.stale_after is not None and expires == self.stale_after:
            return f"stale_after={self.stale_after.isoformat()}"
        return f"max_age_days={max_age_days} from verified={self.verified.isoformat()}"


def is_marker_line(line: str) -> bool:
    return MARKER_START_RE.match(line) is not None


def parse_marker(line: str, line_number: int) -> tuple[Freshness | None, str | None]:
    """Parse a marker line. Returns ``(claim, None)`` or ``(None, error_message)``."""
    match = MARKER_RE.match(line)
    if not match:
        return None, f"marker is not closed with '-->' (expected {MARKER_EXAMPLE})"
    values: dict[str, str] = {}
    for token in match.group("body").split():
        if "=" not in token:
            return None, f"marker token {token!r} is not key=value"
        key, value = token.split("=", 1)
        if key not in MARKER_KEYS:
            return None, f"unknown key {key!r} in marker (allowed: {', '.join(MARKER_KEYS)})"
        if key in values:
            return None, f"marker sets {key}= twice"
        values[key] = value
    if "verified" not in values:
        return None, f"marker has no verified= date (expected {MARKER_EXAMPLE})"
    verified = parse_date(values["verified"])
    if verified is None:
        return None, f"verified={values['verified']} is not an ISO date (YYYY-MM-DD)"
    has_review = "review_by" in values
    has_cadence = "cadence" in values
    if has_review and has_cadence:
        return None, "marker sets both review_by= and cadence=; use one"
    if not has_review and not has_cadence:
        return None, "marker needs review_by= or cadence=evergreen"
    if has_cadence:
        if values["cadence"] != "evergreen":
            return None, f"cadence={values['cadence']} is not supported (only cadence=evergreen)"
        return Freshness(line=line_number, source="marker", verified=verified, evergreen=True), None
    review_by = parse_date(values["review_by"])
    if review_by is None:
        return None, f"review_by={values['review_by']} is not an ISO date (YYYY-MM-DD)"
    return Freshness(
        line=line_number, source="marker", verified=verified, review_by=review_by
    ), None


FRONTMATTER_KEYS = (
    "last_verified",
    "last-verified",
    "verified",
    "review_by",
    "review-by",
    "stale_after",
    "cadence",
)


def _okf_events(node: Node) -> tuple[list[tuple[str, str, dt.date]], str | None]:
    value: Any = node.value
    if isinstance(value, str):
        # Lenient: a plain date is accepted as a single anonymous verification.
        day = parse_instant_date(value)
        if day is None:
            return [], f"verified: {value!r} is not a date or a {{by, at}} mapping"
        return [("", value, day)], None
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or not value:
        return [], "verified must be a {by, at} mapping or a list of them (OKF v0.2)"
    events = []
    for item in value:
        if not isinstance(item, dict) or "at" not in item:
            return [], "each verified entry needs an 'at' timestamp (OKF v0.2)"
        day = parse_instant_date(item["at"])
        if day is None:
            return [], f"verified entry at={item['at']!r} is not an ISO 8601 timestamp"
        events.append((str(item.get("by", "")), str(item["at"]), day))
    return events, None


def freshness_from_frontmatter(
    data: dict[str, Node],
) -> tuple[Freshness | None, list[tuple[int, str]]]:
    """Build the file-level claim from frontmatter. Returns ``(claim, errors)``."""
    present = [key for key in FRONTMATTER_KEYS if key in data]
    if not present:
        return None, []
    errors: list[tuple[int, str]] = []
    verified_dates: list[dt.date] = []
    events: tuple[tuple[str, str], ...] = ()

    for key in ("last_verified", "last-verified"):
        if key in data:
            day = parse_date(str(data[key].value))
            if day is None:
                errors.append((data[key].line, f"{key}: {data[key].value!r} is not an ISO date"))
            else:
                verified_dates.append(day)
    if "verified" in data:
        parsed, error = _okf_events(data["verified"])
        if error:
            errors.append((data["verified"].line, error))
        verified_dates.extend(day for _, _, day in parsed)
        events = tuple((by, at) for by, at, _ in parsed if by)

    review_by = None
    for key in ("review_by", "review-by"):
        if key in data:
            review_by = parse_date(str(data[key].value))
            if review_by is None:
                errors.append((data[key].line, f"{key}: {data[key].value!r} is not an ISO date"))
    stale_after = None
    if "stale_after" in data:
        stale_after = parse_instant_date(str(data["stale_after"].value))
        if stale_after is None:
            errors.append(
                (data["stale_after"].line, "stale_after is not an ISO 8601 timestamp (OKF v0.2)")
            )
    evergreen = False
    if "cadence" in data:
        if data["cadence"].value == "evergreen":
            evergreen = True
        else:
            errors.append(
                (data["cadence"].line, f"cadence: {data['cadence'].value!r} (only 'evergreen')")
            )
    if evergreen and (review_by or stale_after):
        errors.append((data["cadence"].line, "cadence: evergreen cannot also set a review date"))
    has_verified_key = any(k in data for k in ("last_verified", "last-verified", "verified"))
    if review_by and not has_verified_key:
        line = data.get("review_by", data.get("review-by")).line
        errors.append((line, "review_by needs a last_verified (or verified) date"))
    if errors:
        return None, errors

    anchor_key = next(
        (k for k in ("last_verified", "last-verified", "verified") if k in data), present[0]
    )
    status = data.get("status")
    return (
        Freshness(
            line=data[anchor_key].line,
            source="frontmatter",
            verified=max(verified_dates) if verified_dates else None,
            review_by=review_by,
            stale_after=stale_after,
            evergreen=evergreen,
            deprecated=bool(status and status.value == "deprecated"),
            events=events,
        ),
        [],
    )
