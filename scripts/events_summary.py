"""Summarise freshlint's opt-in local event log (set FRESHLINT_EVENTS=1 to record it).

Usage: events_summary.py [--since YYYY-MM-DD]

``--since`` keeps events from that day (local time) on. Report counts include only reports the
agent saw (``shown``); a report that was de-duplicated on resume or compact, or was empty, is
logged but not counted. A field that is missing or not a number counts as 0.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from freshlint.ledger import cache_dir  # noqa: E402

# The hook logs each failure as "<local ISO time> Traceback ..." (a chained exception adds a
# second, unstamped "Traceback" line to the same entry).
_STAMPED_FAILURE = re.compile(r"^(\d{4}-\d{2}-\d{2})T\S* Traceback", re.MULTILINE)


def _number(event: dict, key: str) -> float:
    value = event.get(key, 0)
    return value if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def read_events(path: Path, since: dt.date | None) -> tuple[list[dict], int]:
    """The events on or after ``since``, and how many lines were not an event at all."""
    start = dt.datetime.combine(since, dt.time.min).timestamp() if since else None
    events: list[dict] = []
    corrupt = 0
    for line in _read(path).splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            corrupt += 1
            continue
        if not isinstance(event, dict):
            corrupt += 1
            continue
        if start is None or _number(event, "ts") >= start:
            events.append(event)
    return events, corrupt


def hook_errors(log: Path, since: dt.date | None) -> tuple[int, bool]:
    """The failures logged on or after ``since``, and whether the log could be dated.

    A log whose entries carry no timestamp cannot be filtered: it is counted whole.
    """
    text = _read(log)
    days = _STAMPED_FAILURE.findall(text)
    if not days and "Traceback" in text:
        return text.count("Traceback"), False
    count = 0
    for day in days:
        try:
            count += since is None or dt.date.fromisoformat(day) >= since
        except ValueError:  # a stamp that is not a date cannot be placed before `since`
            count += 1
    return count, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", type=dt.date.fromisoformat, metavar="YYYY-MM-DD")
    since = parser.parse_args(argv).since
    events, corrupt = read_events(cache_dir() / "events.jsonl", since)
    kinds = collections.Counter(
        e["kind"] if isinstance(e.get("kind"), str) else "unknown" for e in events
    )
    logged = [e for e in events if e.get("kind") == "session_report"]
    reports = [e for e in logged if e.get("shown") is True]
    saves = [e for e in events if e.get("kind") == "save"]
    with_findings = sum(_number(e, "reported") > 0 for e in reports)
    too_long = sum(_number(e, "lines") > 10 for e in reports)
    with_feedback = sum(_number(e, "feedback") > 0 for e in saves)
    renewals = int(sum(_number(e, "renewed") for e in saves))
    if since:
        print(f"since: {since}")
    print(f"events: {dict(kinds)}")
    print(f"corrupt lines skipped: {corrupt}")
    print(
        f"session reports: {len(reports)} shown ({len(logged)} logged), "
        f"with findings: {with_findings}"
    )
    print(f"session reports over 10 lines: {too_long}")
    print(f"saves: {len(saves)}, with feedback: {with_feedback}, renewals: {renewals}")
    errors, dated = hook_errors(cache_dir() / "hook.log", since)
    if since is None:
        print(f"hook errors logged: {errors}")
    elif dated:
        print(f"hook errors logged since {since}: {errors}")
    else:
        print(f"hook errors logged: {errors} (whole log: its lines carry no timestamps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
