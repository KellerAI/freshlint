import io
import json
import subprocess
import sys
from pathlib import Path

from conftest import run_cli
from freshlint.cli import main

ROOT = Path(__file__).resolve().parent.parent


def test_memory_scan_text_and_json(claude_project):
    claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.note("a.md", "Calls f.\n\n**Evidence:** `src/a.py#f`")
    res = run_cli(claude_project.path, "memory", "scan")
    assert res.code == 0
    assert "a.md: [unread] src/a.py#f has no saved fingerprint yet" in res.out
    res = run_cli(claude_project.path, "memory", "scan", "--format", "json")
    data = json.loads(res.out)
    assert data["notes"] == 1
    assert (data["findings"][0]["state"], data["findings"][0]["reported"]) == ("unread", True)


def test_memory_check_exit_codes(claude_project):
    claude_project.note("ok.md", "Plain note.")
    assert run_cli(claude_project.path, "memory", "check").code == 0
    claude_project.note("claim.md", "The root cause is in `src/x.py`.")
    assert run_cli(claude_project.path, "memory", "check").code == 0
    claude_project.write("src/x.py", "x\n")
    claude_project.note("bad.md", "**Evidence:** `src/x.py`")
    assert run_cli(claude_project.path, "memory", "check").code == 1


def test_scan_does_not_write_the_store(claude_project):
    claude_project.write("src/a.py", "x\n")
    claude_project.note("a.md", "Plain note mentioning `src/a.py`.")
    run_cli(claude_project.path, "memory", "scan")
    assert not (claude_project.memory / ".freshlint").exists()


def test_missing_memory_dir_is_a_usage_error(claude_project):
    res = run_cli(claude_project.path, "memory", "scan")
    assert res.code == 2 and "no memory directory" in res.err


def test_memory_dir_and_include_scan_claude_md(repo):
    repo.write(
        "CLAUDE.md",
        "# Guide\nRun `scripts/old.sh`. The deploy step in `Deploy.run` is verified.\n",
    )
    repo.write("scripts/old.sh", "echo\n")
    repo.commit_at("2026-09-01T00:00:00Z", "add")
    repo.remove("scripts/old.sh")
    repo.commit_at("2026-09-10T00:00:00Z", "remove")
    res = run_cli(
        repo.path,
        "memory",
        "scan",
        "--memory-dir",
        ".",
        "--include",
        "CLAUDE.md",
        "--format",
        "json",
    )
    assert sorted(f["state"] for f in json.loads(res.out)["findings"]) == ["gone", "uncited-claim"]


def test_project_that_is_a_file_is_a_usage_error(claude_project):
    target = claude_project.write("notes.txt", "not a directory\n")
    for action in ("scan", "check"):
        res = run_cli(claude_project.path, "memory", action, "--project", str(target))
        assert res.code == 2
        assert f"--project {target} is not a directory" in res.err
        assert "Traceback" not in res.err


def test_missing_project_is_a_usage_error_even_with_a_memory_dir(claude_project):
    missing = claude_project.path / "no-such-project"
    claude_project.note("a.md", "Plain note.")
    res = run_cli(
        claude_project.path,
        "memory",
        "check",
        "--project",
        str(missing),
        "--memory-dir",
        str(claude_project.memory),
    )
    assert res.code == 2
    assert f"--project {missing} is not a directory" in res.err
    assert "Traceback" not in res.err


def _run_hook(payload, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "freshlint", "hook"],
        input=payload,
        capture_output=True,
        text=True,
        cwd=cwd,
        env={"PYTHONPATH": str(ROOT / "src"), "HOME": str(Path.home()), "PATH": "/usr/bin:/bin"},
        check=False,
    )


def test_hook_subcommand_reads_stdin():
    proc = _run_hook('{"hook_event_name": "Stop"}')
    assert (proc.returncode, proc.stdout) == (0, "")


def test_hook_subcommand_reports_at_session_start(claude_project):
    claude_project.note("a.md", "The root cause is the cache in `src/cache.py`.")
    payload = json.dumps(
        {
            "hook_event_name": "SessionStart",
            "session_id": "s1",
            "source": "startup",
            "cwd": str(claude_project.path),
        }
    )
    proc = _run_hook(payload, cwd=claude_project.path)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_hook_never_exits_2_whatever_the_arguments(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"hook_event_name": "Stop"}'))
    assert main(["hook", "--bogus"]) == 0
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")
