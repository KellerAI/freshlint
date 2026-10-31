import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "events_summary.py"
BEFORE = dt.datetime(2026, 10, 1, 23, 59).timestamp()
AFTER = dt.datetime(2026, 10, 2, 0, 1).timestamp()
TRACEBACK = 'Traceback (most recent call last):\n  File "x.py", line 1, in f\nValueError: boom\n'


@pytest.fixture
def summary():
    spec = importlib.util.spec_from_file_location("events_summary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FRESHLINT_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir()
    return tmp_path / "cache"


def write_events(cache, *events):
    lines = [e if isinstance(e, str) else json.dumps(e) for e in events]
    (cache / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def report(shown=True, reported=1, lines=3, ts=AFTER):
    return {
        "ts": ts,
        "kind": "session_report",
        "reported": reported,
        "lines": lines,
        "shown": shown,
    }


def save(feedback=0, renewed=0, ts=AFTER):
    return {"ts": ts, "kind": "save", "feedback": feedback, "renewed": renewed}


def output(summary, capsys, *argv):
    assert summary.main(list(argv)) == 0
    return capsys.readouterr().out.splitlines()


def test_no_log_prints_zeros_and_exits_zero(summary, cache, capsys):
    assert output(summary, capsys) == [
        "events: {}",
        "corrupt lines skipped: 0",
        "session reports: 0 shown (0 logged), with findings: 0",
        "session reports over 10 lines: 0",
        "saves: 0, with feedback: 0, renewals: 0",
        "hook errors logged: 0",
    ]


def test_only_reports_the_agent_saw_are_counted(summary, cache, capsys):
    write_events(
        cache,
        report(shown=True, reported=2, lines=4),
        report(shown=True, reported=0, lines=10),
        report(shown=True, reported=1, lines=11),
        report(shown=False, reported=5, lines=40),  # de-duplicated on resume: nobody saw it
        report(shown=False, reported=0, lines=0),  # nothing to say
        {"ts": AFTER, "kind": "session_report", "reported": 9, "lines": 99},  # no `shown` field
        save(feedback=1, renewed=0),
        save(feedback=0, renewed=2),
        save(),
        {"ts": AFTER, "kind": "store_edit"},
    )
    lines = output(summary, capsys)
    assert lines[0] == "events: {'session_report': 6, 'save': 3, 'store_edit': 1}"
    assert "session reports: 3 shown (6 logged), with findings: 2" in lines
    assert "session reports over 10 lines: 1" in lines
    assert "saves: 3, with feedback: 1, renewals: 2" in lines


def test_missing_and_odd_fields_count_as_zero(summary, cache, capsys):
    write_events(
        cache,
        {"kind": "session_report", "shown": True},
        {"kind": "session_report", "shown": True, "reported": "many", "lines": None},
        {"kind": "save"},
        {"kind": "save", "feedback": True, "renewed": "2"},
        {"ts": AFTER},
    )
    lines = output(summary, capsys)
    assert "session reports: 2 shown (2 logged), with findings: 0" in lines
    assert "session reports over 10 lines: 0" in lines
    assert "saves: 2, with feedback: 0, renewals: 0" in lines
    assert lines[0] == "events: {'session_report': 2, 'save': 2, 'unknown': 1}"


def test_corrupt_lines_are_skipped_and_counted(summary, cache, capsys):
    write_events(
        cache,
        report(),
        '{"ts": 1, "kind": "sav',  # cut off mid-write
        "[1, 2]",
        "17",
        "",
        "not json at all",
        save(feedback=1),
    )
    lines = output(summary, capsys)
    assert "corrupt lines skipped: 4" in lines
    assert "session reports: 1 shown (1 logged), with findings: 1" in lines
    assert "saves: 1, with feedback: 1, renewals: 0" in lines


def test_since_keeps_only_events_from_that_day_on(summary, cache, capsys):
    write_events(
        cache,
        report(ts=BEFORE),
        report(ts=BEFORE, shown=True, reported=3),
        report(ts=AFTER, reported=1),
        save(feedback=1, ts=BEFORE),
        save(feedback=1, ts=AFTER),
        {"kind": "save", "feedback": 1},  # no timestamp: cannot be placed after the day
    )
    everything = output(summary, capsys)
    assert "session reports: 3 shown (3 logged), with findings: 3" in everything
    assert "saves: 3, with feedback: 3, renewals: 0" in everything
    lines = output(summary, capsys, "--since", "2026-10-02")
    assert lines[0] == "since: 2026-10-02"
    assert "session reports: 1 shown (1 logged), with findings: 1" in lines
    assert "saves: 1, with feedback: 1, renewals: 0" in lines


def test_a_since_that_is_not_a_date_is_refused(summary, cache):
    with pytest.raises(SystemExit) as stopped:
        summary.main(["--since", "yesterday"])
    assert stopped.value.code == 2


def stamped(day, hour=9, body=TRACEBACK):
    return f"{day}T{hour:02d}:00:00.123456 {body}"


def test_hook_errors_count_one_per_logged_failure(summary, cache, capsys):
    chained = (
        TRACEBACK
        + "\nDuring handling of the above exception, another exception occurred:\n\n"
        + TRACEBACK
    )
    (cache / "hook.log").write_text(
        stamped("2026-09-30") + stamped("2026-10-02", 9, chained) + stamped("2026-10-03"),
        encoding="utf-8",
    )
    assert "hook errors logged: 3" in output(summary, capsys)
    assert "hook errors logged since 2026-10-02: 2" in output(
        summary, capsys, "--since", "2026-10-02"
    )


def test_a_hook_log_without_timestamps_is_counted_whole_and_says_so(summary, cache, capsys):
    (cache / "hook.log").write_text(TRACEBACK + TRACEBACK, encoding="utf-8")
    assert "hook errors logged: 2" in output(summary, capsys)
    lines = output(summary, capsys, "--since", "2026-10-02")
    assert "hook errors logged: 2 (whole log: its lines carry no timestamps)" in lines


def test_a_clean_hook_log_is_zero_with_or_without_since(summary, cache, capsys):
    (cache / "hook.log").write_text("", encoding="utf-8")
    assert "hook errors logged: 0" in output(summary, capsys)
    assert "hook errors logged since 2026-10-02: 0" in output(
        summary, capsys, "--since", "2026-10-02"
    )


def test_the_log_is_read_as_utf8_whatever_the_locale(cache):
    write_events(cache, {"ts": AFTER, "kind": "save", "note": "café.md", "feedback": 1})
    body = TRACEBACK.replace("boom", "café")
    (cache / "hook.log").write_text(stamped("2026-10-02", body=body), "utf-8")
    env = {
        **os.environ,
        "FRESHLINT_CACHE_DIR": str(cache),
        "LC_ALL": "C",
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONIOENCODING": "utf-8",
    }
    done = subprocess.run(
        [sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert "saves: 1, with feedback: 1, renewals: 0" in done.stdout
    assert "hook errors logged: 1" in done.stdout
