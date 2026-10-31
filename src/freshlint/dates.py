"""Strict date parsing. freshlint compares dates, never wall-clock times."""

from __future__ import annotations

import datetime as dt
import re

ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_date(value: object) -> dt.date | None:
    """Parse a strict ``YYYY-MM-DD`` string, or return None."""
    if not isinstance(value, str) or not ISO_DATE_RE.fullmatch(value.strip()):
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def parse_instant_date(value: object) -> dt.date | None:
    """Parse an ISO 8601 date or datetime and return its UTC calendar date.

    OKF timestamps carry an explicit offset (``2026-06-30T14:00:00Z``). A datetime
    without an offset is treated as UTC.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    plain = parse_date(text)
    if plain is not None:
        return plain
    if "T" not in text and " " not in text:
        return None
    try:
        moment = dt.datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(dt.UTC).date()
