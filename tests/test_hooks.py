import datetime as dt
import io
import json
import subprocess

import pytest

from freshlint import hooks, ledger, store

TODAY = dt.date(2026, 10, 1)
MISSING = object()
CLAIM = "The root cause is the cache in `src/cache.py`."  # counted in the first-session summary


def send(project, event, session="s1", **fields):
    payload = {"hook_event_name": event, "session_id": session, "cwd": str(project.path), **fields}
    if session is MISSING:
        del payload["session_id"]
    out = hooks.run(json.dumps(payload), today=TODAY)
    return json.loads(out) if out else None


def tool(project, name, path, session="s1", **extra):
    return send(
        project,
        "PostToolUse",
        session,
        tool_name=name,
        tool_input={"file_path": str(path)},
        tool_response={},
        **extra,
    )


def context(out):
    return out["hookSpecificOutput"]["additionalContext"]


def test_a_first_session_with_nothing_to_count_is_silent_but_still_creates_the_store(
    claude_project,
):
    claude_project.note("a.md", "A plain note about the team.")
    assert send(claude_project, "SessionStart", source="startup") is None
    assert store.store_path(claude_project.memory).exists()


def test_session_start_without_memory_is_silent(claude_project):
    assert send(claude_project, "SessionStart", source="startup") is None


def test_first_session_start_summarises_and_creates_the_store(claude_project):
    claude_project.note("a.md", CLAIM)
    out = send(claude_project, "SessionStart", source="startup")
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert context(out).startswith("freshlint: 1 memory. 0 cite code:")
    assert store.store_path(claude_project.memory).exists()


def test_a_save_before_any_session_start_still_shows_the_first_run_summary(claude_project):
    note = claude_project.note("a.md", CLAIM)
    tool(claude_project, "Write", note)
    assert store.store_path(claude_project.memory).exists()
    out = send(claude_project, "SessionStart", source="startup")
    assert out is not None
    assert context(out).startswith("freshlint: 1 memory. 0 cite code:")


def test_saves_from_a_session_already_running_at_install_still_get_the_summary(claude_project):
    running = "already-running"
    first = claude_project.note("a.md", CLAIM)
    second = claude_project.note("b.md", "The root cause is the retry loop in `src/retry.py`.")
    tool(claude_project, "Write", first, session=running)
    tool(claude_project, "Edit", second, session=running)
    assert store.store_path(claude_project.memory).exists()
    out = send(claude_project, "SessionStart", "next", source="startup")
    assert out is not None
    assert context(out).startswith("freshlint: 2 memories. 0 cite code:")


def test_session_start_then_save_keeps_last_scan(claude_project):
    note = claude_project.note("a.md", CLAIM)
    first = send(claude_project, "SessionStart", "s1", source="startup")
    assert first is not None
    assert context(first).startswith("freshlint: 1 memory. 0 cite code:")
    path = store.store_path(claude_project.memory)
    before = json.loads(path.read_text())
    tool(claude_project, "Write", note, session="s1")
    saved = json.loads(path.read_text())
    assert saved["version"] == 1
    assert "last_scan" in saved
    assert saved["last_scan"] == before["last_scan"]
    second = send(claude_project, "SessionStart", "s2", source="startup")
    text = "" if second is None else context(second)
    assert "cite code" not in text
    assert "without evidence" not in text


def test_reads_are_recorded_and_notes_are_checked_on_save(claude_project):
    code = claude_project.write("src/a.py", "def f():\n    return 1\n")
    note = claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    assert "which you have not read in this session" in context(tool(claude_project, "Write", note))
    assert tool(claude_project, "Read", code) is None
    assert str(code.resolve()) in ledger.reads("s1", None)
    assert tool(claude_project, "Write", note) is None
    citations = store.load_store(claude_project.memory).notes["a.md"].citations
    assert citations["src/a.py#f"].fingerprint is not None


def test_renewal_writes_a_receipt(claude_project):
    code = claude_project.write("src/a.py", "def f():\n    return 1\n")
    note = claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    tool(claude_project, "Read", code)
    tool(claude_project, "Write", note)
    claude_project.write("src/a.py", "def f():\n    return 2\n")
    tool(claude_project, "Read", code, session="s2")
    assert tool(claude_project, "Write", note, session="s2") is None
    receipts = store.receipts_path(claude_project.memory).read_text()
    assert "- verified a.md#evidence — re-read src/a.py#f (session s2)" in receipts


def test_subagent_reads_do_not_count_for_the_main_agent(claude_project):
    code = claude_project.write("src/a.py", "x\n")
    note = claude_project.note("a.md", "**Evidence:** `src/a.py`")
    tool(claude_project, "Read", code, agent_id="sub-1")
    assert "not read in this session" in context(tool(claude_project, "Write", note))
    assert str(code.resolve()) in ledger.reads("s1", "sub-1")
    assert ledger.reads("s1", None) == {}


@pytest.mark.parametrize("agent_id", ["", 7, None, ["sub-1"]])
def test_a_missing_or_malformed_agent_id_is_the_main_agent(claude_project, agent_id):
    code = claude_project.write("src/a.py", "x\n")
    tool(claude_project, "Read", code, agent_id=agent_id)
    assert str(code.resolve()) in ledger.reads("s1", None)
    assert ledger.reads("s1", "") == {}
    assert '"agent": ""' not in (ledger.cache_dir() / "sessions" / "s1.jsonl").read_text()


def test_editing_the_store_gets_feedback_but_reading_it_does_not(claude_project):
    claude_project.note("a.md", "x")
    send(claude_project, "SessionStart", source="startup")
    lock = store.store_path(claude_project.memory)
    assert "maintained by freshlint" in context(tool(claude_project, "Edit", lock))
    assert tool(claude_project, "Read", lock) is None


def test_resume_does_not_repeat_an_identical_report(claude_project):
    claude_project.write("src/a.py", "x\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py`")
    send(claude_project, "SessionStart", source="startup")
    assert send(claude_project, "SessionStart", source="startup") is not None
    assert send(claude_project, "SessionStart", source="resume") is None
    assert send(claude_project, "SessionStart", source="startup") is not None


@pytest.mark.parametrize("session", [MISSING, "", 7])
def test_a_payload_without_a_session_id_never_touches_the_ledger(claude_project, session):
    code = claude_project.write("src/a.py", "def f():\n    return 1\n")
    note = claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    assert tool(claude_project, "Read", code, session=session) is None
    claude_project.write("src/b.py", "y\n")
    assert tool(claude_project, "Edit", claude_project.path / "src/b.py", session=session) is None
    assert not (ledger.cache_dir() / "sessions").exists()
    assert ledger.reads("unknown", None) == {}
    # the read above vouches for nothing: an unidentified session has no reads
    feedback = context(tool(claude_project, "Write", note, session=session))
    assert "which you have not read in this session" in feedback
    assert not (ledger.cache_dir() / "sessions").exists()


def test_a_session_id_less_save_cannot_borrow_another_sessions_reads(claude_project):
    code = claude_project.write("src/a.py", "def f():\n    return 1\n")
    note = claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    tool(claude_project, "Read", code, session="unknown")
    assert "not read in this session" in context(tool(claude_project, "Write", note, session=""))


@pytest.mark.parametrize("session", [MISSING, ""])
def test_session_start_without_a_session_id_does_not_deduplicate_resumes(claude_project, session):
    claude_project.write("src/a.py", "x\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py`")
    assert send(claude_project, "SessionStart", session, source="startup") is not None
    assert send(claude_project, "SessionStart", session, source="resume") is not None
    assert send(claude_project, "SessionStart", session, source="resume") is not None
    assert not (ledger.cache_dir() / "sessions").exists()


def test_block_mode_uses_decision_and_reason(claude_project, monkeypatch):
    monkeypatch.setattr(hooks, "FEEDBACK_MODE", "block")
    note = claude_project.note("a.md", "The root cause is the cache in `src/cache.py`.")
    out = tool(claude_project, "Write", note)
    assert out["decision"] == "block"
    assert out["reason"].startswith('freshlint: a.md says "root cause"')


def test_context_mode_is_the_default(claude_project):
    assert hooks.FEEDBACK_MODE == "context"
    note = claude_project.note("a.md", "The root cause is the cache in `src/cache.py`.")
    out = tool(claude_project, "Write", note)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "decision" not in out


def test_main_swallows_errors_and_logs_them():
    out = io.StringIO()
    assert hooks.main(io.StringIO("not json"), out) == 0
    assert out.getvalue() == ""
    assert "Traceback" in (ledger.cache_dir() / "hook.log").read_text()


def test_main_prints_the_report_and_swallows_ledger_failures(claude_project, monkeypatch):
    claude_project.note("a.md", CLAIM)
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "s1",
        "cwd": str(claude_project.path),
    }
    out = io.StringIO()
    assert hooks.main(io.StringIO(json.dumps(payload)), out) == 0
    assert json.loads(out.getvalue())["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "record", boom)
    code = claude_project.write("src/a.py", "x\n")
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "s1",
        "cwd": str(claude_project.path),
        "tool_name": "Read",
        "tool_input": {"file_path": str(code)},
    }
    out = io.StringIO()
    assert hooks.main(io.StringIO(json.dumps(payload)), out) == 0
    assert out.getvalue() == ""
    assert "disk full" in (ledger.cache_dir() / "hook.log").read_text()


def test_unrelated_events_and_tools_are_ignored(claude_project):
    assert send(claude_project, "Stop") is None
    assert (
        send(claude_project, "PostToolUse", tool_name="Bash", tool_input={"command": "ls"}) is None
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"tool_name": "Read"},
        {"tool_name": "Read", "tool_input": []},
        {"tool_name": "Read", "tool_input": {"file_path": 3}},
        {"tool_name": "Read", "tool_input": {"file_path": ""}},
        {"tool_name": ["Read"], "tool_input": {"file_path": "x"}},
    ],
)
def test_malformed_tool_payloads_are_ignored(claude_project, fields):
    assert send(claude_project, "PostToolUse", **fields) is None
    assert not (ledger.cache_dir() / "sessions").exists()


def test_a_non_object_payload_is_ignored():
    assert hooks.run("[1, 2]") == ""


def test_a_relative_path_resolves_against_the_payload_cwd(claude_project):
    claude_project.write("src/a.py", "x\n")
    send(
        claude_project,
        "PostToolUse",
        tool_name="Read",
        tool_input={"file_path": "src/a.py"},
    )
    assert str((claude_project.path / "src/a.py").resolve()) in ledger.reads("s1", None)


def test_the_event_log_is_opt_in(claude_project, monkeypatch):
    claude_project.note("a.md", "x")
    send(claude_project, "SessionStart", source="startup")
    assert not (ledger.cache_dir() / "events.jsonl").exists()
    monkeypatch.setenv(hooks.EVENTS_ENV, "1")
    send(claude_project, "SessionStart", source="startup")
    assert '"kind": "session_report"' in (ledger.cache_dir() / "events.jsonl").read_text()


def logged_reports():
    lines = (ledger.cache_dir() / "events.jsonl").read_text().splitlines()
    return [e for e in map(json.loads, lines) if e["kind"] == "session_report"]


def test_the_session_report_event_says_whether_the_agent_saw_the_report(
    claude_project, monkeypatch
):
    monkeypatch.setenv(hooks.EVENTS_ENV, "1")
    claude_project.write("src/a.py", "x\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py`")
    assert send(claude_project, "SessionStart", source="startup") is not None
    assert send(claude_project, "SessionStart", source="resume") is None  # the same report again
    assert send(claude_project, "SessionStart", source="compact") is None
    assert send(claude_project, "SessionStart", source="startup") is not None
    shown = [e["shown"] for e in logged_reports()]
    assert shown == [True, False, False, True]


def test_a_report_with_nothing_to_say_is_logged_as_not_shown(claude_project, monkeypatch):
    monkeypatch.setenv(hooks.EVENTS_ENV, "1")
    claude_project.note("a.md", "A plain note about the team.")
    assert send(claude_project, "SessionStart", source="startup") is None
    (event,) = logged_reports()
    assert (event["shown"], event["lines"], event["reported"]) == (False, 0, 0)


def test_a_resume_without_a_session_id_is_logged_as_shown(claude_project, monkeypatch):
    monkeypatch.setenv(hooks.EVENTS_ENV, "1")
    claude_project.write("src/a.py", "x\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py`")
    assert send(claude_project, "SessionStart", MISSING, source="startup") is not None
    assert send(claude_project, "SessionStart", MISSING, source="resume") is not None
    assert [e["shown"] for e in logged_reports()] == [True, True]


def test_an_unwritable_event_log_does_not_lose_the_report(claude_project, monkeypatch):
    claude_project.note("a.md", CLAIM)
    monkeypatch.setenv(hooks.EVENTS_ENV, "1")
    (ledger.cache_dir() / "events.jsonl").mkdir(parents=True)  # appending to a directory fails
    out = send(claude_project, "SessionStart", source="startup")
    assert context(out).startswith("freshlint: 1 memory.")


def test_main_swallows_a_failed_write_to_stdout(claude_project, monkeypatch):
    claude_project.note("a.md", CLAIM)
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "s1",
        "cwd": str(claude_project.path),
    }

    class Closed:
        def write(self, text):
            raise BrokenPipeError

    calls = []
    monkeypatch.setattr(hooks, "_discard_stdout", lambda: calls.append(1))
    assert hooks.main(io.StringIO(json.dumps(payload)), Closed()) == 0
    assert "BrokenPipeError" in (ledger.cache_dir() / "hook.log").read_text()
    assert calls == []  # only the process's own stdout is pointed at the null device


def test_main_returns_zero_when_even_the_log_cannot_be_written(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no home directory")

    monkeypatch.setattr(hooks, "run", boom)
    monkeypatch.setattr(ledger, "cache_dir", boom)
    out = io.StringIO()
    assert hooks.main(io.StringIO("{}"), out) == 0
    assert out.getvalue() == ""


def test_the_log_survives_text_that_utf8_cannot_encode(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("lone surrogate \ud800 in a path")

    monkeypatch.setattr(hooks, "run", boom)
    assert hooks.main(io.StringIO("{}"), io.StringIO()) == 0
    assert "lone surrogate \\ud800" in (ledger.cache_dir() / "hook.log").read_text()


class Pipe:
    def __init__(self, fail_on):
        self.fail_on, self.written, self.flushed = fail_on, [], False

    def write(self, text):
        if self.fail_on == "write":
            raise BrokenPipeError
        self.written.append(text)

    def flush(self):
        self.flushed = True
        if self.fail_on == "flush":
            raise BrokenPipeError


def hook_payload(project, **fields):
    return json.dumps(
        {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(project.path), **fields}
    )


def test_main_flushes_stdout(claude_project):
    claude_project.note("a.md", CLAIM)
    pipe = Pipe(fail_on=None)
    assert hooks.main(io.StringIO(hook_payload(claude_project)), pipe) == 0
    assert pipe.flushed and "SessionStart" in "".join(pipe.written)


def test_main_swallows_a_failing_flush(claude_project):
    claude_project.note("a.md", CLAIM)
    pipe = Pipe(fail_on="flush")
    assert hooks.main(io.StringIO(hook_payload(claude_project)), pipe) == 0
    assert pipe.flushed
    assert "BrokenPipeError" in (ledger.cache_dir() / "hook.log").read_text()


def test_a_failing_report_bookkeeping_write_still_returns_the_report(claude_project, monkeypatch):
    claude_project.note("a.md", CLAIM)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "remember_report", boom)
    out = send(claude_project, "SessionStart", source="startup")
    assert context(out).startswith("freshlint: 1 memory.")


def test_a_failing_receipt_write_still_returns_the_save_feedback(claude_project, monkeypatch):
    code = claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.write("src/b.py", "x\n")
    note = claude_project.note("a.md", "**Evidence:** `src/a.py#f` `src/b.py`")
    tool(claude_project, "Read", code)
    tool(claude_project, "Write", note)
    claude_project.write("src/a.py", "def f():\n    return 2\n")
    tool(claude_project, "Read", code, session="s2")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "append_receipts", boom)
    out = tool(claude_project, "Write", note, session="s2")
    assert "cites src/b.py, which you have not read in this session" in context(out)
    # the renewal itself was still stored
    renewed = store.load_store(claude_project.memory).notes["a.md"].citations["src/a.py#f"]
    assert renewed.fingerprint is not None


def test_the_project_dir_env_beats_a_payload_cwd_that_left_the_project(
    claude_project, tmp_path, monkeypatch
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=elsewhere, check=True)
    note = claude_project.note("a.md", "The root cause is the cache in `src/cache.py`.")
    assert tool(claude_project, "Write", note, cwd=str(elsewhere)) is None
    monkeypatch.setenv(hooks.PROJECT_DIR_ENV, str(claude_project.path))
    out = tool(claude_project, "Write", note, cwd=str(elsewhere))
    assert 'freshlint: a.md says "root cause"' in context(out)
    monkeypatch.setenv(hooks.PROJECT_DIR_ENV, "")  # an empty value falls back to the cwd
    assert tool(claude_project, "Write", note, cwd=str(elsewhere)) is None


def test_a_relative_path_still_resolves_against_the_payload_cwd_with_a_project_dir(
    claude_project, tmp_path, monkeypatch
):
    monkeypatch.setenv(hooks.PROJECT_DIR_ENV, str(claude_project.path))
    claude_project.write("src/sub/a.py", "x\n")
    send(
        claude_project,
        "PostToolUse",
        tool_name="Read",
        tool_input={"file_path": "a.py"},
        cwd=str(claude_project.path / "src" / "sub"),
    )
    assert str((claude_project.path / "src/sub/a.py").resolve()) in ledger.reads("s1", None)


def test_reading_a_note_records_nothing_and_checks_nothing(claude_project):
    code = claude_project.write("src/a.py", "x\n")
    note = claude_project.note("a.md", "The root cause is the cache in `src/cache.py`.")
    tool(claude_project, "Read", code)
    assert tool(claude_project, "Read", note) is None
    reads = ledger.reads("s1", None)
    assert str(code.resolve()) in reads
    assert str(note.resolve()) not in reads
    assert not store.store_path(claude_project.memory).exists()


@pytest.mark.parametrize("name", ["Write", "Edit", "MultiEdit"])
def test_every_note_write_tool_runs_the_save_checks(claude_project, name):
    note = claude_project.note("a.md", "The root cause is the cache in `src/cache.py`.")
    out = tool(claude_project, name, note)
    assert 'freshlint: a.md says "root cause"' in context(out)
    assert "a.md" in store.load_store(claude_project.memory).notes


def test_re_saving_a_note_does_not_clear_a_gone_mention(claude_project):
    claude_project.write("old/x.py", "x\n")
    claude_project.commit_at("2026-08-01T00:00:00Z", "start")
    body = "See `old/x.py`."
    note = claude_project.note("a.md", body)
    claude_project.remove("old/x.py")
    claude_project.commit_at("2026-09-10T00:00:00Z", "later")
    first = send(claude_project, "SessionStart", "s1", source="startup")
    assert "a.md: mentions old/x.py, which no longer exists." in context(first)
    # The agent re-saves the note without reading anything; Claude Code stamps a new time.
    note = claude_project.note("a.md", body, modified="2026-09-20T12:00:00Z")
    assert tool(claude_project, "Write", note, session="s1") is None
    second = send(claude_project, "SessionStart", "s2", source="startup")
    assert "a.md: mentions old/x.py, which no longer exists." in context(second)


def test_citing_a_missing_file_does_not_clear_its_gone_mention(claude_project):
    claude_project.write("old/x.py", "x\n")
    claude_project.commit_at("2026-08-01T00:00:00Z", "start")
    claude_project.note("a.md", "See `old/x.py`.")
    claude_project.remove("old/x.py")
    claude_project.commit_at("2026-09-10T00:00:00Z", "later")
    first = send(claude_project, "SessionStart", "s1", source="startup")
    assert "a.md: mentions old/x.py, which no longer exists." in context(first)
    # A missing file cannot be read, so a citation of it never gets a fingerprint.
    note = claude_project.note(
        "a.md", "See `old/x.py`.\n\n**Evidence:** `old/x.py`", modified="2026-09-20T12:00:00Z"
    )
    saved = tool(claude_project, "Write", note, session="s1")
    assert "cites old/x.py, but that file does not exist" in context(saved)
    second = send(claude_project, "SessionStart", "s2", source="startup")
    assert "a.md: cites old/x.py, but that file does not exist" in context(second)
    # Dropping the mention clears it.
    note = claude_project.note("a.md", "Nothing to see here.", modified="2026-09-21T12:00:00Z")
    assert tool(claude_project, "Write", note, session="s2") is None
    assert send(claude_project, "SessionStart", "s3", source="startup") is None


def test_a_citation_read_but_not_fingerprintable_is_not_reported_as_unread_next_session(
    claude_project,
):
    code = claude_project.write("src/b.py", "def g():\n    return 1\n")
    note = claude_project.note("b.md", "**Evidence:** `src/b.py#nope`")
    tool(claude_project, "Read", code, session="s1")
    saved = tool(claude_project, "Write", note, session="s1")
    assert "'nope' is not defined" in context(saved)
    second = send(claude_project, "SessionStart", "s2", source="startup")
    text = context(second)
    assert "b.md: cites src/b.py#nope, but when the memory was saved 'nope' is not defined" in text
    assert "was not read" not in text
