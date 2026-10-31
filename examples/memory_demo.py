"""A scripted walk-through of freshlint's Claude Code hooks, with no Claude Code needed.

It builds a throwaway project and memory directory, then sends the hook payloads Claude
Code would send. Run: python examples/memory_demo.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from freshlint import hooks, store  # noqa: E402
from freshlint.memory import project_slug  # noqa: E402

TODAY = dt.date(2026, 10, 1)
SESSION_1 = "4f1c9a2e-7b1d-4c2e-9a10-3e5d6f7a8b01"
SESSION_2 = "7d3b5e90-2c4a-4f8e-b6d1-9a0c1e2f3b45"
SESSION_3 = "c2a8f4d1-5e6b-4a7c-8d9e-0f1a2b3c4d56"
HEADER = "---\nname: api-limits\ndescription: API rate limits\nmetadata:\n  type: project\n---\n\n"
CODE_V1 = "RATE_LIMIT = 100\n\n\ndef retry_after(status):\n    return 30 if status == 429 else 0\n"
CODE_V2 = CODE_V1.replace("30 if", "60 if")


def say(text: str) -> None:
    print(f"\n== {text}")


def show(output: str) -> None:
    if not output:
        print("(silent)")
        return
    data = json.loads(output)
    print(data.get("reason") or data["hookSpecificOutput"]["additionalContext"])


def send(project: Path, event: str, session: str, **fields: object) -> str:
    payload = {"hook_event_name": event, "session_id": session, "cwd": str(project), **fields}
    return hooks.run(json.dumps(payload), today=TODAY)


def use(project: Path, session: str, tool: str, path: Path) -> str:
    return send(
        project,
        "PostToolUse",
        session,
        tool_name=tool,
        tool_input={"file_path": str(path)},
        tool_response={},
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp).resolve()
        os.environ["HOME"] = str(base / "home")
        os.environ["FRESHLINT_CACHE_DIR"] = str(base / "cache")
        # Make the demo deterministic even when it runs inside a Claude Code session.
        for name in (
            "CLAUDE_PROJECT_DIR",
            "XDG_CACHE_HOME",
            "FRESHLINT_EVENTS",
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
        ):
            os.environ.pop(name, None)
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        project = base / "shop"
        (project / "src").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(project)], check=True)
        code = project / "src" / "limits.py"
        code.write_text(CODE_V1)
        memory_dir = base / "home" / ".claude" / "projects" / project_slug(project) / "memory"
        memory_dir.mkdir(parents=True)
        note = memory_dir / "api_limits.md"

        say("1. The agent saves a memory that claims certainty, with no evidence")
        claim = "The root cause of the 429s is the retry loop in `src/limits.py`.\n"
        note.write_text(HEADER + claim)
        show(use(project, SESSION_1, "Write", note))

        say("2. It cites code it has not read")
        body = "Retries wait 30 seconds after a 429.\n\n**Evidence:** `src/limits.py#retry_after`\n"
        note.write_text(HEADER + body)
        show(use(project, SESSION_1, "Write", note))

        say("3. It reads the code and saves again: fingerprinted, no feedback")
        use(project, SESSION_1, "Read", code)
        show(use(project, SESSION_1, "Write", note))

        say("4. Someone changes the code. The next session starts")
        code.write_text(CODE_V2)
        show(send(project, "SessionStart", SESSION_2, source="startup"))

        say("5. Saving again without re-reading renews nothing")
        show(use(project, SESSION_2, "Write", note))

        say("6. The agent re-reads the code, fixes the memory and saves: renewed, with a receipt")
        use(project, SESSION_2, "Read", code)
        note.write_text(HEADER + body.replace("30 seconds", "60 seconds"))
        show(use(project, SESSION_2, "Write", note))
        receipts = store.receipts_path(memory_dir).read_text(encoding="utf-8").strip().splitlines()
        print("receipts.md: " + receipts[-1])

        say("7. The next session starts clean")
        show(send(project, "SessionStart", SESSION_3, source="startup"))


if __name__ == "__main__":
    main()
