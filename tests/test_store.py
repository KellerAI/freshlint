import datetime as dt
import os
import sys
import time

import pytest

from freshlint.fingerprint import Fingerprint
from freshlint.receipts import check_order, parse_receipt_log
from freshlint.store import (
    CitationRecord,
    InferredRecord,
    NoteRecord,
    Store,
    append_receipts,
    load_store,
    locked,
    receipts_path,
    save_store,
    state_dir,
    store_path,
)


def test_round_trip(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    original = Store(
        {
            "a.md": NoteRecord(
                {
                    "x.py": CitationRecord(Fingerprint("file", "d"), "2026-10-01"),
                    "y.py#L1-L2": CitationRecord(
                        Fingerprint("lines", "e", ("1", "2")), "2026-10-01"
                    ),
                    "z.py": CitationRecord(None, None),
                },
                {"old.md": InferredRecord("file", "git", "b", 1788264000.0)},
                "k",
                ["gone.py", "never.py"],
                "j",
            )
        }
    )
    save_store(mem, original)
    assert store_path(mem) == mem / ".freshlint" / "lock.json"
    assert load_store(mem) == original


def test_a_store_without_the_no_record_marker_loads_with_none(tmp_path):
    store_path(tmp_path).parent.mkdir(parents=True)
    store_path(tmp_path).write_text('{"version": 1, "notes": {"a.md": {"inferred_key": "k"}}}')
    record = load_store(tmp_path).notes["a.md"]
    assert (record.no_record, record.no_record_key) == ([], None)


def test_a_note_with_no_marker_writes_none(tmp_path):
    save_store(tmp_path, Store({"a.md": NoteRecord(inferred_key="k")}))
    assert "no_record" not in store_path(tmp_path).read_text()


def test_round_trip_keeps_why_a_citation_has_no_fingerprint(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    original = Store(
        {
            "a.md": NoteRecord(
                {
                    "x.py#nope": CitationRecord(
                        None, None, "missing:'nope' is not defined in x.py"
                    ),
                    "y.py": CitationRecord(None, None, "changed"),
                    "z.py": CitationRecord(None, None),
                }
            )
        }
    )
    save_store(mem, original)
    assert load_store(mem) == original
    text = store_path(mem).read_text()
    assert text.count('"problem"') == 2  # a citation never read has no reason to record


def test_round_trip_keeps_line_range_start_and_uniqueness(tmp_path):
    original = Store(
        {
            "a.md": NoteRecord(
                {
                    "y.py#L5-L7": CitationRecord(
                        Fingerprint("lines", "e", ("1", "2", "3"), start=5, unique=True),
                        "2026-10-01",
                    ),
                }
            )
        }
    )
    save_store(tmp_path, original)
    loaded = load_store(tmp_path)
    assert loaded == original
    kept = loaded.notes["a.md"].citations["y.py#L5-L7"].fingerprint
    assert kept is not None
    assert (kept.start, kept.unique) == (5, True)


def test_an_inferred_record_without_a_time_loads(tmp_path):
    store_path(tmp_path).parent.mkdir(parents=True)
    store_path(tmp_path).write_text(
        '{"version": 1, "notes": {"a.md": {"inferred_key": "k", "inferred": '
        '{"x.py": {"kind": "file", "method": "git", "baseline": "b"}}}}}'
    )
    loaded = load_store(tmp_path)
    assert loaded.notes["a.md"].inferred == {"x.py": InferredRecord("file", "git", "b", None)}


def test_missing_store_is_empty(tmp_path):
    assert load_store(tmp_path) == Store()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unreadable_store_is_flagged_not_taken_for_a_missing_one(tmp_path, pathlib_flavour):
    save_store(tmp_path, Store({"a.md": NoteRecord()}))
    store_path(tmp_path).parent.chmod(0)
    try:
        assert load_store(tmp_path) == Store(corrupt=True)
    finally:
        store_path(tmp_path).parent.chmod(0o700)


def test_corrupt_or_unknown_version_store_is_flagged(tmp_path):
    store_path(tmp_path).parent.mkdir(parents=True)
    store_path(tmp_path).write_text("{nope")
    assert load_store(tmp_path) == Store(corrupt=True)
    store_path(tmp_path).write_text('{"version": 99, "notes": {}}')
    assert load_store(tmp_path).corrupt


def test_a_store_that_is_not_text_is_flagged_not_a_crash(tmp_path):
    store_path(tmp_path).parent.mkdir(parents=True)
    store_path(tmp_path).write_bytes(b"\xff\xfe\x00garbage")
    assert load_store(tmp_path) == Store(corrupt=True)


def test_receipts_append_under_dated_headings_and_parse(tmp_path):
    append_receipts(tmp_path, dt.date(2026, 10, 1), [("a.md", "src/x.py#f", "3f2a9c1b77")])
    append_receipts(tmp_path, dt.date(2026, 10, 1), [("b.md", "y.toml#k", "abcdef0123")])
    append_receipts(tmp_path, dt.date(2026, 10, 2), [("a.md", "src/x.py#f", "zzzzzzzz99")])
    text = receipts_path(tmp_path).read_text()
    assert text.count("## 2026-10-01") == 1
    assert "- verified a.md#evidence — re-read src/x.py#f (session 3f2a9c1b)" in text
    receipts, problems, dated = parse_receipt_log(text)
    assert problems == []
    assert [(r.date.isoformat(), r.path) for r in receipts] == [
        ("2026-10-01", "a.md"),
        ("2026-10-01", "b.md"),
        ("2026-10-02", "a.md"),
    ]
    assert check_order(dated, "ascending") == []


def test_appending_nothing_creates_nothing(tmp_path):
    append_receipts(tmp_path, dt.date(2026, 10, 1), [])
    assert not receipts_path(tmp_path).exists()


def test_the_lock_is_exclusive_between_holders(tmp_path):
    fcntl = pytest.importorskip("fcntl")
    lock = state_dir(tmp_path) / "lock.lock"
    with locked(tmp_path), lock.open("a") as other, pytest.raises(BlockingIOError):
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with lock.open("a") as other:  # released on exit
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_a_lock_that_cannot_be_taken_in_time_fails_open(tmp_path):
    fcntl = pytest.importorskip("fcntl")
    state_dir(tmp_path).mkdir(parents=True)
    with (state_dir(tmp_path) / "lock.lock").open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        started = time.monotonic()
        with locked(tmp_path, timeout=0.1):
            ran = True
        assert ran and 0.1 <= time.monotonic() - started < 2


def test_without_fcntl_or_write_access_the_lock_fails_open(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "fcntl", None)  # as on Windows: the import fails
    with locked(tmp_path):
        ran = True
    assert ran
    monkeypatch.undo()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with locked(blocker):  # the state directory cannot be created below a file
        ran = True
    assert ran
