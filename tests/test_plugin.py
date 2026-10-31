import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from freshlint import __version__, ledger, store
from freshlint.memory import project_slug

ROOT = Path(__file__).resolve().parent.parent
# Put this interpreter first on PATH so the runner finds a Python 3.11+ on any machine.
ENV = {
    **os.environ,
    "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
}


def test_manifests_are_valid_and_agree_on_the_version():
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert (plugin["name"], plugin["version"]) == ("freshlint", __version__)
    assert (market["plugins"][0]["name"], market["plugins"][0]["source"]) == ("freshlint", "./")


def test_hooks_call_the_bundled_runner():
    hooks = json.loads((ROOT / "hooks/hooks.json").read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "PostToolUse"}
    commands = [h["command"] for group in hooks.values() for entry in group for h in entry["hooks"]]
    assert commands and all(c == '"${CLAUDE_PLUGIN_ROOT}/bin/freshlint" hook' for c in commands)
    assert hooks["PostToolUse"][0]["matcher"] == "Read|Edit|MultiEdit|Write|NotebookEdit"


def test_runner_is_an_executable_posix_script():
    runner = ROOT / "bin/freshlint"
    assert os.access(runner, os.X_OK)
    subprocess.run(["sh", "-n", str(runner)], check=True)


def test_runner_runs_the_cli_and_the_hook():
    runner = str(ROOT / "bin/freshlint")
    proc = subprocess.run(
        [runner, "--version"], capture_output=True, text=True, check=False, env=ENV
    )
    assert proc.returncode == 0 and proc.stdout.startswith("freshlint ")
    proc = subprocess.run(
        [runner, "hook"],
        input='{"hook_event_name": "Stop"}',
        capture_output=True,
        text=True,
        check=False,
        env=ENV,
    )
    assert (proc.returncode, proc.stdout) == (0, "")


def test_skill_and_command_are_well_formed():
    skill = (ROOT / "skills/memory-evidence/SKILL.md").read_text()
    assert skill.startswith("---\nname: memory-evidence\ndescription: ")
    command = (ROOT / "commands/memory-check.md").read_text()
    assert command.startswith("---\ndescription: ")
    assert '"${CLAUDE_PLUGIN_ROOT}/bin/freshlint" memory scan' in command


def test_skill_and_command_say_how_a_mentioned_missing_file_is_cleared():
    # A citation covers a mention only once it has a fingerprint, and a missing file can never be
    # read, so citing one clears nothing: the citation is reported `unread` next to the inferred
    # `gone`. Dropping the mention clears it, and so does dropping a citation of the missing file
    # that was added. The earlier wording, "only turns the finding into `unread`", is reversed.
    for name in ("skills/memory-evidence/SKILL.md", "commands/memory-check.md"):
        flat = " ".join((ROOT / name).read_text().split())
        assert (
            "citing a file that no longer exists clears nothing (the citation is reported "
            "`unread` next to the `gone` mention); remove the mention, and that citation if you "
            "added it, to clear it"
        ) in flat
        assert "only turns the finding into" not in flat
        assert "only dropping the mention" not in flat


def test_skill_and_command_say_what_a_citation_covers():
    # A citation covers the files it names and the claims written next to it, and coverage is
    # syntactic. Both texts tell the agent that an Evidence line elsewhere in the note covers a
    # claim only if the sentence names what the line cites, and that freshlint does not check that
    # the cited code supports the claim.
    skill = " ".join((ROOT / "skills/memory-evidence/SKILL.md").read_text().split())
    command = " ".join((ROOT / "commands/memory-check.md").read_text().split())
    assert "A citation covers the files it names and the claims written next to it." in skill
    assert "Coverage is syntactic." in skill
    assert "freshlint never checks that the cited code supports the claim" in skill
    assert (
        "A citation covers the files it names and the claims written next to it, and coverage is "
        "syntactic"
    ) in command
    assert "freshlint does not check that the cited code supports the claim" in command
    # A bare word names nothing: the sentence needs a backticked or path-like name.
    assert "a bare word such as `src` names nothing" in skill
    assert "a bare word such as `src` names nothing" in command
    for text in (skill, command):
        assert (
            "An Evidence line elsewhere in the note covers a claim only if the sentence names what "
            "the line cites"
        ) in text
        # A mention that only a citation of another file, or an unread citation, names stays.
        assert "Citing other files" in text
        assert "without reading it" in text


def test_skill_and_command_say_how_a_claim_is_credited():
    # Pins and markers: a bare date credits nothing, a pin needs a verification
    # word or a marker, and a marker line directly below the claim credits it.
    skill = " ".join((ROOT / "skills/memory-evidence/SKILL.md").read_text().split())
    command = " ".join((ROOT / "commands/memory-check.md").read_text().split())
    assert "counts only together with a verification word or a marker in the same sentence" in skill
    assert "next to a claim counts for nothing" in skill
    assert "`Verified:`, `Checked:`, `Tested:`, `Source:` or `Evidence (manual):`" in skill
    assert "`Verified:` or `Source:` line directly below it" in command
    assert "a bare date such as a deadline credits nothing" in command
    # The earlier advice, a date, a commit or a version in the sentence, no longer holds.
    assert "(a date, a commit, a version, or" not in command
    # Both texts point at the README for the coverage and credit rules.
    for text in (skill, command):
        assert 'see the README, section "Uncited claims"' in text
    assert "state how the claim was checked, when that is true" in command


def test_skill_and_command_say_what_a_file_level_symbol_finding_means():
    # An uncertain C or C++ prefix falls back to the file, which can over-report.
    skill = " ".join((ROOT / "skills/memory-evidence/SKILL.md").read_text().split())
    command = " ".join((ROOT / "commands/memory-check.md").read_text().split())
    assert '"tracked at file level"' in skill and '"tracked at file level"' in command
    assert "can report a change elsewhere in the file" in skill
    assert "may report a change that is not in the symbol" in command


def test_skill_and_command_do_not_tell_the_agent_to_read_a_missing_file():
    skill = " ".join((ROOT / "skills/memory-evidence/SKILL.md").read_text().split())
    command = " ".join((ROOT / "commands/memory-check.md").read_text().split())
    # An `unread` citation of a file that no longer exists has nothing to read.
    assert "If the cited file no longer exists" in skill
    assert "If the cited file no longer exists" in command


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def test_runner_finds_freshlint_without_an_install(tmp_path):
    # Deployment mode: a plugin cache has no editable install. The shim runs this interpreter
    # without site-packages, so only the runner's own PYTHONPATH can supply freshlint.
    for name in (
        "python3.14",
        "python3.13",
        "python3.12",
        "python3.11",
        "python3",
    ):  # shadow system Pythons
        shim = _script(tmp_path / name, f'exec "{sys.executable}" -S "$@"')
    env = {"PATH": f"{tmp_path}{os.pathsep}/usr/bin{os.pathsep}/bin"}
    bare = subprocess.run(
        [str(shim), "-c", "import freshlint"], capture_output=True, check=False, env=env
    )
    assert bare.returncode != 0  # guards the test: freshlint is not importable without the runner
    proc = subprocess.run(
        [str(ROOT / "bin/freshlint"), "--version"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0 and proc.stdout.startswith("freshlint ")


def _runner_without_python(tmp_path, payload):
    for name in ("python3", "python3.14", "python3.13", "python3.12", "python3.11"):
        _script(tmp_path / name, "exit 1")
    env = {"PATH": f"{tmp_path}{os.pathsep}/usr/bin{os.pathsep}/bin"}
    return subprocess.run(
        [str(ROOT / "bin/freshlint"), "hook"],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_runner_without_python_says_so_at_session_start(tmp_path):
    for payload in ('{"hook_event_name":"SessionStart"}', '{"hook_event_name": "SessionStart"}'):
        proc = _runner_without_python(tmp_path, payload)
        assert proc.returncode == 0
        output = json.loads(proc.stdout)["hookSpecificOutput"]
        assert output["hookEventName"] == "SessionStart"
        assert "needs Python 3.11" in output["additionalContext"]


def test_runner_without_python_ignores_other_events_even_when_they_mention_session_start(tmp_path):
    payload = json.dumps(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"SessionStart": [{"hooks": []}]},
            "tool_response": {
                "text": 'the "SessionStart" hook, {"hook_event_name": "SessionStart"}'
            },
        }
    )
    proc = _runner_without_python(tmp_path, payload)
    assert (proc.returncode, proc.stdout) == (0, "")


def test_module_hook_exits_zero_even_when_freshlint_cannot_be_imported(tmp_path):
    package = tmp_path / "freshlint"
    shutil.copytree(ROOT / "src/freshlint", package, ignore=shutil.ignore_patterns("__pycache__"))
    (package / "hooks.py").write_text("raise RuntimeError('broken install')\n")
    env = {**ENV, "PYTHONPATH": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, "-m", "freshlint", "hook"],
        input='{"hook_event_name": "SessionStart"}',
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


def test_runner_ignores_stdlib_named_files_in_the_launch_directory(tmp_path):
    # A project can hold a types.py or json.py; python -m would put the cwd first on sys.path.
    project = tmp_path / "proj"
    project.mkdir()
    (project / "types.py").write_text("raise RuntimeError('shadowed types')\n")
    (project / "json.py").write_text("raise RuntimeError('shadowed json')\n")
    home = tmp_path / "home"
    memory = home / ".claude" / "projects" / project_slug(project.resolve()) / "memory"
    memory.mkdir(parents=True)
    (memory / "a.md").write_text("The root cause is the cache in `src/cache.py`.\n")
    env = {**ENV, "HOME": str(home), "CLAUDE_PROJECT_DIR": str(project)}
    runner = str(ROOT / "bin/freshlint")
    payload = '{"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"}'
    proc = subprocess.run(
        [runner, "hook"],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        cwd=project,
        env=env,
    )
    assert proc.returncode == 0 and proc.stderr == ""
    output = json.loads(proc.stdout)["hookSpecificOutput"]  # not silently inert
    assert output["additionalContext"].startswith("freshlint: 1 memory.")
    version = subprocess.run(
        [runner, "--version"], capture_output=True, text=True, check=False, cwd=project, env=env
    )
    assert version.returncode == 0 and version.stdout.startswith("freshlint ")


def _save_payload(project, note):
    return json.dumps(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": str(project.path),
            "tool_name": "Write",
            "tool_input": {"file_path": str(note)},
            "tool_response": {},
        }
    )


def test_two_hooks_saving_different_notes_at_once_keep_both_records(
    claude_project, tmp_path, monkeypatch
):
    monkeypatch.setenv("FRESHLINT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(claude_project.path))
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    runner = str(ROOT / "bin/freshlint")
    notes = []
    for name, code in (("a.md", "src/a.py"), ("b.md", "src/b.py")):
        ledger.record("s1", None, claude_project.write(code, "x = 1\n"))  # the agent read it
        notes.append(claude_project.note(name, f"**Evidence:** `{code}`"))
    for attempt in range(8):  # one overlap is luck; eight make a lost record near certain
        shutil.rmtree(store.state_dir(claude_project.memory), ignore_errors=True)
        procs = [
            subprocess.Popen(
                [runner, "hook"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=claude_project.path,
                env=env,
            )
            for _ in notes
        ]
        for proc, note in zip(procs, notes, strict=True):  # both are up: release them together
            proc.stdin.write(_save_payload(claude_project, note))
            proc.stdin.close()
        for proc in procs:
            assert proc.wait(timeout=30) == 0
            proc.stdout.close()
            proc.stderr.close()
        saved = store.load_store(claude_project.memory)
        assert sorted(saved.notes) == ["a.md", "b.md"], f"round {attempt}"
        for name, record in saved.notes.items():
            assert all(c.fingerprint is not None for c in record.citations.values()), name


def test_a_reader_that_closes_the_pipe_early_does_not_fail_the_hook(claude_project, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(claude_project.path))
    claude_project.note("a.md", "The root cause is the cache in `src/cache.py`.")  # so it reports
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.Popen(
        [str(ROOT / "bin/freshlint"), "hook"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=claude_project.path,
        env=env,
    )
    proc.stdout.close()  # nobody reads the report: writing it hits a closed pipe
    payload = {"hook_event_name": "SessionStart", "session_id": "s1", "source": "startup"}
    _, stderr = proc.communicate(json.dumps(payload), timeout=30)
    assert (proc.returncode, stderr) == (0, "")
