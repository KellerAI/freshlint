import os

from freshlint import ledger


def test_last_read_wins_and_agents_are_separate(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("1")
    ledger.record("s1", None, f)
    first = ledger.reads("s1", None)[str(f.resolve())]
    f.write_text("2")
    ledger.record("s1", None, f)
    assert ledger.reads("s1", None)[str(f.resolve())] != first
    assert ledger.reads("s1", "sub") == {}
    ledger.record("s1", "sub", f)
    assert str(f.resolve()) in ledger.reads("s1", "sub")
    assert ledger.reads("other", None) == {}


def test_missing_files_and_bad_lines_are_ignored(tmp_path):
    ledger.record("s1", None, tmp_path / "nope.py")
    assert ledger.reads("s1", None) == {}
    target = ledger.cache_dir() / "sessions" / "s1.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('not json\n[1]\n{"path": 3}\n')
    assert ledger.reads("s1", None) == {}


def test_session_ids_are_sanitised(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("1")
    ledger.record("../../etc", None, f)
    assert (ledger.cache_dir() / "sessions" / "______etc.jsonl").exists()


def test_prune_removes_old_files(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("1")
    ledger.record("old", None, f)
    ledger.record("new", None, f)
    old = ledger.cache_dir() / "sessions" / "old.jsonl"
    os.utime(old, (0, 0))
    ledger.prune()
    assert not old.exists()
    assert (ledger.cache_dir() / "sessions" / "new.jsonl").exists()


def test_report_memory():
    assert not ledger.same_as_last_report("s", "x")
    ledger.remember_report("s", "x")
    assert ledger.same_as_last_report("s", "x")
    assert not ledger.same_as_last_report("s", "y")


def test_cache_dir_sources(monkeypatch, tmp_path):
    monkeypatch.setenv(ledger.CACHE_ENV, str(tmp_path / "c"))
    assert ledger.cache_dir() == tmp_path / "c"
    monkeypatch.delenv(ledger.CACHE_ENV)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "x"))
    assert ledger.cache_dir() == tmp_path / "x" / "freshlint"
