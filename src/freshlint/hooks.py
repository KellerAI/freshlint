"""Claude Code hooks: ``freshlint hook`` reads one hook payload on stdin.

SessionStart reports memories that need re-checking. PostToolUse records which files the
agent read or edited, and checks a memory note when the agent saves it. Every failure is
logged and swallowed: freshlint must never break a session.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import IO

from freshlint import ledger, memreport, save, store
from freshlint.checks import Context, scan
from freshlint.memory import is_note, memory_dir_for, project_root_for, read_note

FEEDBACK_MODE = "context"  # spike S2: "context" (additionalContext) or "block" (decision)
SESSION_DEADLINE_S = 3.0
FILE_TOOLS = frozenset({"Read", "Edit", "MultiEdit", "Write", "NotebookEdit"})
EVENTS_ENV = "FRESHLINT_EVENTS"
PROJECT_DIR_ENV = "CLAUDE_PROJECT_DIR"


def _text(value: object) -> str | None:
    """``value`` if it is a non-empty string, else None."""
    return value if isinstance(value, str) and value else None


def _cwd(payload: dict) -> Path:
    return Path(_text(payload.get("cwd")) or os.getcwd())


def _project_dir(payload: dict) -> Path:
    """Where the session was launched: Claude Code's stable ``CLAUDE_PROJECT_DIR``, else ``cwd``.

    The payload ``cwd`` follows the agent as it changes directory, so it can leave the project.
    """
    return Path(_text(os.environ.get(PROJECT_DIR_ENV)) or _cwd(payload))


def _context(payload: dict) -> Context:
    return Context(project_root_for(_project_dir(payload)), Path.home())


def _event(kind: str, **fields: object) -> None:
    if os.environ.get(EVENTS_ENV) != "1":
        return
    with contextlib.suppress(OSError):  # a debug log must never cost the agent its report
        target = ledger.cache_dir() / "events.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": time.time(), "kind": kind, **fields}) + "\n")


def _context_output(event: str, text: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def _feedback(text: str) -> str:
    if FEEDBACK_MODE == "block":
        return json.dumps({"decision": "block", "reason": text})
    return _context_output("PostToolUse", text)


def _session_start(payload: dict) -> str:
    ledger.prune()
    ctx = _context(payload)
    memory_dir = memory_dir_for(ctx.project_root)
    if not memory_dir.is_dir():
        return ""
    with store.locked(memory_dir):
        evidence = store.load_store(memory_dir)
        first_run = evidence.last_scan is None and not evidence.corrupt
        result = scan(memory_dir, ctx, evidence, deadline=time.monotonic() + SESSION_DEADLINE_S)
        evidence.last_scan = time.time()
        store.save_store(memory_dir, evidence)
    text = memreport.session_report(result, first_run=first_run, corrupt=evidence.corrupt)
    session = _text(payload.get("session_id"))
    shown = bool(text)
    if text and session is not None:  # without a session there is nothing to deduplicate against
        if payload.get("source") in ("resume", "compact") and ledger.same_as_last_report(
            session, text
        ):
            shown = False  # the agent already has this report
        else:
            with contextlib.suppress(OSError):  # bookkeeping must not drop the report
                ledger.remember_report(session, text)
    _event(
        "session_report",
        session=session,
        notes=result.notes,
        reported=sum(f.reported for f in result.findings),
        lines=0 if not text else text.count("\n") + 1,
        first_run=first_run,
        shown=shown,
    )
    return _context_output("SessionStart", text) if shown else ""


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
    except ValueError:
        return False
    return True


def _post_tool_use(payload: dict, today: dt.date) -> str:
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool, str) or tool not in FILE_TOOLS or not isinstance(tool_input, dict):
        return ""
    raw = _text(tool_input.get("file_path")) or _text(tool_input.get("notebook_path"))
    if raw is None:
        return ""
    path = Path(raw)
    path = (path if path.is_absolute() else _cwd(payload) / path).resolve()
    ctx = _context(payload)
    memory_dir = memory_dir_for(ctx.project_root).resolve()
    session = _text(payload.get("session_id"))
    agent = _text(payload.get("agent_id"))  # None is the main agent, never ""
    if _inside(path, store.state_dir(memory_dir)):
        if tool == "Read":
            return ""
        _event("store_edit", session=session, tool=tool)
        return _feedback(
            "freshlint: files in the memory evidence store are maintained by freshlint. "
            "Do not edit them; change the memory note instead."
        )
    if is_note(path, memory_dir):
        if tool == "Read" or not path.exists():
            return ""
        note = read_note(path)
        reads = ledger.reads(session, agent) if session is not None else {}
        with store.locked(memory_dir):  # load, change and save without losing a parallel save
            evidence = store.load_store(memory_dir)
            outcome = save.on_save(note, evidence, reads, ctx, today)
            store.save_store(memory_dir, evidence)
            with contextlib.suppress(OSError):  # bookkeeping must not drop the feedback
                receipts = [(note.name, cited, session or "") for cited in outcome.renewed]
                store.append_receipts(memory_dir, today, receipts)
        _event(
            "save",
            session=session,
            note=note.name,
            feedback=len(outcome.feedback),
            renewed=len(outcome.renewed),
        )
        if not outcome.feedback:
            return ""
        return _feedback(memreport.save_feedback(outcome.feedback))
    if session is not None:
        ledger.record(session, agent, path)
    return ""


def run(stdin_text: str, today: dt.date | None = None) -> str:
    payload = json.loads(stdin_text)
    if not isinstance(payload, dict):
        return ""
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        return _session_start(payload)
    if event == "PostToolUse":
        return _post_tool_use(payload, today or dt.date.today())
    return ""


def _log(text: str) -> None:
    with contextlib.suppress(Exception):  # logging runs inside the fail-open handler
        target = ledger.cache_dir() / "hook.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", errors="backslashreplace") as handle:
            handle.write(f"{dt.datetime.now().isoformat()} {text}\n")


def _discard_stdout() -> None:
    """Point the real stdout at the null device, after its reader has gone away.

    The text that failed to flush stays in Python's buffer, and the flush at interpreter shutdown
    would fail again: "Exception ignored", and exit status 120.
    """
    with contextlib.suppress(Exception):
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, sys.stdout.fileno())
        os.close(null)


def main(stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        output = run(stdin.read())
        if output:
            stdout.write(output + "\n")
            stdout.flush()  # a closed pipe must fail here, inside the handler, not at shutdown
    except Exception as exc:  # fail open by design
        _log(traceback.format_exc())
        if isinstance(exc, BrokenPipeError) and stdout is sys.stdout:
            _discard_stdout()  # the reader went away: shutdown must not fail on the same pipe
    return 0
