"""The composite action's shell (FT-3): scripts/build_args.sh and scripts/team_upload.sh.

* The "Run freshlint" step, now built on build_args.sh, reproduces the v0.2.0 inline script
  (tests/fixtures/action/v0.2.0-run.sh) byte for byte: stdout, stderr and exit code.
* The check and the upload build identical arguments, differing only in --format.
* team_upload.sh makes exactly one POST, keeps the OIDC token off every command line and out
  of its output (apart from the ::add-mask:: command), and skips with one notice when no token
  is available.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from team_support import (
    AUDIENCE,
    MARKER,
    REQUEST_TOKEN,
    MockServer,
    build_marker_repo,
    fake_oidc_token,
)

ROOT = Path(__file__).resolve().parent.parent
ACTION = (ROOT / "action.yml").read_text()
LEGACY = ROOT / "tests" / "fixtures" / "action" / "v0.2.0-run.sh"
SCHEMA = json.loads((ROOT / "schemas" / "team-run-v1.schema.json").read_text())
BIN = Path(sys.executable).parent
GITHUB = {
    "GITHUB_REPOSITORY": "KellerAI/freshlint-sandbox",
    "GITHUB_REPOSITORY_ID": "123456789",
    "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
}
TOKEN = fake_oidc_token("pytest")

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
needs_curl = pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")


def step(name):
    """The parsed `- name: NAME` step of action.yml: its keys and its run script."""
    lines = ACTION.splitlines()
    start = lines.index(f"    - name: {name}")
    block = []
    for line in lines[start + 1 :]:
        if line.startswith("    - "):
            break
        block.append(line)
    keys = {}
    for line in block:
        match = re.match(r"^      ([a-z-]+):(?: (.*))?$", line)
        if match:
            keys[match.group(1)] = match.group(2)
    if keys.get("run") == "|":
        index = block.index("      run: |")
        script = []
        for line in block[index + 1 :]:
            if line and not line.startswith("        "):
                break
            script.append(line[8:])
        keys["run"] = "\n".join(script).strip("\n") + "\n"
    return keys


def env_for(**fl):
    base = {
        "FL_MODE": "full",
        "FL_BASE": "",
        "FL_HEAD": "",
        "FL_EVENT": "push",
        "FL_FORMAT": "github",
        "FL_STRICT": "false",
        "FL_TODAY": "2026-09-30",
        "FL_PATHS": "",
    }
    base.update(fl)
    return {
        **{k: v for k, v in os.environ.items() if not k.startswith(("FL_", "ACTIONS_ID_TOKEN"))},
        **base,
        "PATH": f"{BIN}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(ROOT / "src"),
        "GITHUB_ACTION_PATH": str(ROOT),
    }


def bash(script_or_file, cwd, env):
    args = ["bash", str(script_or_file)] if isinstance(script_or_file, Path) else None
    args = args or ["bash", "-e", "-c", script_or_file]
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True)


@pytest.fixture(scope="module")
def marker_repo(tmp_path_factory):
    return build_marker_repo(tmp_path_factory.mktemp("marker") / "repo")


# ------------------------------------------------------------------- action.yml


def test_action_declares_the_team_inputs():
    assert re.search(r'\n  team:\n(?:    .*\n)*?    default: "false"\n', ACTION)
    team_url = re.search(r"\n  team-url:\n((?:    .*\n)+)", ACTION).group(1)
    assert "default:" not in team_url
    assert re.search(r"\n  team-audience:\n(?:    .*\n)*?    default: freshlint-team\n", ACTION)


def test_check_step_uses_build_args():
    run = step("Run freshlint")
    assert run["run"] == (
        "set -euo pipefail\n"
        '. "$GITHUB_ACTION_PATH/scripts/build_args.sh" "$FL_FORMAT"\n'
        'freshlint "${FRESHLINT_ARGS[@]}"\n'
    )
    assert "continue-on-error" not in run and "if" not in run


def test_upload_step_is_opt_in_and_never_fails_the_job():
    upload = step("Upload run summary to freshlint Team")
    assert upload["if"] == "${{ !cancelled() && inputs.team == 'true' }}"
    assert upload["continue-on-error"] == "true"
    assert upload["run"] == 'bash "$GITHUB_ACTION_PATH/scripts/team_upload.sh"'
    assert upload["working-directory"] == "${{ inputs.working-directory }}"


def test_steps_share_the_same_inputs_except_format():
    def env_block(name):
        lines = ACTION.splitlines()
        start = lines.index(f"    - name: {name}")
        env = {}
        for line in lines[start + 1 :]:
            if line.startswith("    - "):
                break
            match = re.match(r"^        (FL_[A-Z_]+): (.*)$", line)
            if match:
                env[match.group(1)] = match.group(2)
        return env

    check, upload = env_block("Run freshlint"), env_block("Upload run summary to freshlint Team")
    assert check.pop("FL_FORMAT") == "${{ inputs.format }}"
    assert upload.pop("FL_TEAM_URL") == "${{ inputs.team-url }}"
    assert upload.pop("FL_TEAM_AUDIENCE") == "${{ inputs.team-audience }}"
    assert check == upload


def test_inputs_reach_scripts_only_through_the_environment():
    for name in ("Run freshlint", "Upload run summary to freshlint Team"):
        assert "${{" not in step(name)["run"], name
    for script in ("build_args.sh", "team_upload.sh"):
        assert "${{" not in (ROOT / "scripts" / script).read_text()


# ------------------------------------------- byte-identical to the v0.2.0 step

PARITY_CASES = {
    "full-github": {},
    "full-text": {"FL_FORMAT": "text"},
    "full-json": {"FL_FORMAT": "json"},
    "full-strict": {"FL_STRICT": "true", "FL_FORMAT": "text"},
    "no-today": {"FL_TODAY": "", "FL_FORMAT": "text"},
    "bad-today": {"FL_TODAY": "tomorrow"},
    "diff": {"FL_MODE": "diff", "FL_BASE": "HEAD~1", "FL_HEAD": "HEAD"},
    "diff-no-base": {"FL_MODE": "diff", "FL_HEAD": "HEAD"},
    "auto-pr": {"FL_MODE": "auto", "FL_EVENT": "pull_request", "FL_BASE": "HEAD~1"},
    "auto-pr-target": {
        "FL_MODE": "auto",
        "FL_EVENT": "pull_request_target",
        "FL_BASE": "HEAD~1",
        "FL_HEAD": "HEAD",
    },
    "auto-pr-no-base": {"FL_MODE": "auto", "FL_EVENT": "pull_request"},
    "auto-push": {"FL_MODE": "auto", "FL_EVENT": "push", "FL_BASE": "HEAD~1"},
    "staged": {"FL_MODE": "staged"},
    "unknown-mode": {"FL_MODE": f"{MARKER} 'quoted'"},
    "paths": {"FL_PATHS": "state.md  notes.md", "FL_FORMAT": "text"},
    "missing-path": {"FL_PATHS": "../outside.md"},
    "bad-format": {"FL_FORMAT": "xml"},
    "empty-format": {"FL_FORMAT": ""},
    "bad-diff-range": {"FL_MODE": "diff", "FL_BASE": "no-such-rev", "FL_HEAD": "HEAD"},
}


@needs_bash
@pytest.mark.parametrize("case", sorted(PARITY_CASES))
def test_check_step_is_byte_identical_to_v0_2_0(marker_repo, case):
    env = env_for(**PARITY_CASES[case])
    legacy = bash(LEGACY, marker_repo, env)
    current = bash(step("Run freshlint")["run"], marker_repo, env)
    assert current.stdout == legacy.stdout
    assert current.stderr == legacy.stderr
    assert current.returncode == legacy.returncode
    assert legacy.stdout or legacy.stderr  # the case exercised something


@needs_bash
def test_parity_cases_cover_every_exit_code(marker_repo):
    codes = {bash(LEGACY, marker_repo, env_for(**fl)).returncode for fl in PARITY_CASES.values()}
    assert codes == {0, 1, 2}  # staged: nothing staged; usage errors exit 2


@needs_bash
def test_check_step_is_byte_identical_on_a_clean_run(tmp_path):
    (tmp_path / ".freshlint.toml").write_text("")
    (tmp_path / "a.md").write_text("<!-- memory: verified=2026-09-01 review_by=2026-12-01 -->\n")
    env = env_for(FL_FORMAT="text")
    legacy = bash(LEGACY, tmp_path, env)
    current = bash(step("Run freshlint")["run"], tmp_path, env)
    assert (current.returncode, current.stdout, current.stderr) == (
        legacy.returncode,
        legacy.stdout,
        legacy.stderr,
    )
    assert legacy.returncode == 0


def build_args(fmt, **fl):
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "build_args.sh"), fmt],
        env=env_for(**fl),
        capture_output=True,
    )
    return proc.returncode, proc.stdout


@needs_bash
@pytest.mark.parametrize("case", sorted(PARITY_CASES))
def test_check_and_upload_build_identical_args_except_format(case):
    fl = {k: v for k, v in PARITY_CASES[case].items() if k != "FL_FORMAT"}
    check_code, check_out = build_args("github", **fl)
    upload_code, upload_out = build_args("json", **fl)
    assert check_code == upload_code
    if check_code:
        assert check_out == upload_out  # the same ::error:: line
        return
    check_args, upload_args = check_out.split(b"\0"), upload_out.split(b"\0")
    assert check_args[:3] == [b"check", b"--format", b"github"]
    assert upload_args[:3] == [b"check", b"--format", b"json"]
    assert check_args[3:] == upload_args[3:]


@needs_bash
def test_build_args_keeps_arguments_with_spaces_intact():
    code, out = build_args("json", FL_MODE="diff", FL_BASE="a b", FL_HEAD="c\nd", FL_TODAY="x y")
    assert code == 0
    assert out.split(b"\0")[:-1] == [
        b"check",
        b"--format",
        b"json",
        b"--diff",
        b"a b...c\nd",
        b"--today",
        b"x y",
    ]


# ------------------------------------------------------------------ team_upload.sh


@pytest.fixture
def server(tmp_path):
    srv = MockServer(tmp_path / "requests.jsonl", tmp_path / "bodies", TOKEN).start()
    yield srv
    srv.stop()


def curl_shim(tmp_path):
    """A `curl` on PATH that logs its argv and header-file modes, then runs the real curl."""
    real = shutil.which("curl")
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    log = tmp_path / "curl-argv.jsonl"
    shim = shim_dir / "curl"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import json, os, stat, sys\n"
        "argv = sys.argv[1:]\n"
        "headers = {}\n"
        "for flag, value in zip(argv, argv[1:]):\n"
        "    if flag == '-H' and value.startswith('@'):\n"
        "        headers[value[1:]] = oct(stat.S_IMODE(os.stat(value[1:]).st_mode))\n"
        f"with open({str(log)!r}, 'a') as handle:\n"
        "    handle.write(json.dumps({'argv': argv, 'header_files': headers}) + '\\n')\n"
        f"os.execv({real!r}, [{real!r}] + argv)\n"
    )
    shim.chmod(0o755)
    return shim_dir, log


def upload(tmp_path, cwd, server, **overrides):
    shim_dir, curl_log = curl_shim(tmp_path)
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir(exist_ok=True)
    env = env_for(FL_MODE="diff", FL_BASE="HEAD~1", FL_HEAD="HEAD")
    env.pop("FL_FORMAT")
    env.update(GITHUB)
    env.update(
        {
            "PATH": f"{shim_dir}{os.pathsep}{env['PATH']}",
            "RUNNER_TEMP": str(runner_temp),
            "FL_TEAM_URL": f"{server.base}/ingest",
            "FL_TEAM_AUDIENCE": AUDIENCE,
            "ACTIONS_ID_TOKEN_REQUEST_URL": f"{server.base}/token?api-version=2.0",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": REQUEST_TOKEN,
        }
    )
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "team_upload.sh")],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    calls = [json.loads(x) for x in curl_log.read_text().splitlines()] if curl_log.exists() else []
    return proc, calls, runner_temp


@needs_bash
@needs_curl
def test_upload_sends_exactly_one_post_without_leaking(tmp_path, marker_repo, server):
    proc, calls, runner_temp = upload(tmp_path, marker_repo, server)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    requests = server.requests()
    gets = [r for r in requests if r["method"] == "GET"]
    posts = [r for r in requests if r["method"] == "POST"]
    assert len(requests) == 2
    assert gets == [
        {
            "method": "GET",
            "path": "/token",
            "audience": AUDIENCE,
            "api_version": "2.0",
            "request_token_ok": True,
        }
    ]
    assert len(posts) == 1
    assert posts[0]["path"] == "/ingest"
    assert posts[0]["token_ok"] is True
    assert posts[0]["content_type"] == "application/json"

    body = (server.bodies / posts[0]["body_file"]).read_text()
    assert MARKER not in body
    payload = json.loads(body)
    Draft202012Validator(SCHEMA).validate(payload)
    assert payload["repo"] == GITHUB["GITHUB_REPOSITORY"]
    assert payload["mode"] == "diff"

    # The upload scanned exactly what the check scans: the same summary as the check step.
    check = bash(
        step("Run freshlint")["run"],
        marker_repo,
        env_for(FL_FORMAT="json", FL_MODE="diff", FL_BASE="HEAD~1", FL_HEAD="HEAD"),
    )
    report = json.loads(check.stdout)
    assert payload["summary"] == report["summary"]
    assert [(f["path"], f["line"], f["kind"]) for f in payload["findings"]] == [
        (f["path"], f["line"], f["rule"]) for f in report["findings"]
    ]

    # The token: masked first, then never printed again, never on a command line.
    assert proc.stdout.splitlines()[0] == f"::add-mask::{TOKEN}"
    assert proc.stdout.count(TOKEN) == 1
    assert TOKEN not in proc.stderr
    assert REQUEST_TOKEN not in proc.stdout + proc.stderr
    assert len(calls) == 2
    for call in calls:
        argv = " ".join(call["argv"])
        assert TOKEN not in argv and REQUEST_TOKEN not in argv
        assert "Authorization" not in argv
        assert call["header_files"], call["argv"]
        for path, mode in call["header_files"].items():
            assert Path(path).parent.parent == runner_temp
            assert mode == "0o600"
    assert [c["argv"].count("--data-binary") for c in calls] == [0, 1]
    assert "--retry" not in " ".join(calls[1]["argv"])

    # The temporary directory with the header files is gone.
    assert list(runner_temp.iterdir()) == []
    assert "freshlint team: uploaded the run summary (HTTP 202)" in proc.stdout


@needs_bash
@needs_curl
@pytest.mark.parametrize(
    "missing", ["ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
)
def test_upload_without_oidc_prints_one_notice_and_does_nothing(
    tmp_path, marker_repo, server, missing
):
    proc, calls, runner_temp = upload(tmp_path, marker_repo, server, **{missing: None})
    assert proc.returncode == 0
    assert proc.stderr == ""
    lines = proc.stdout.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("::notice title=freshlint team::upload skipped: no GitHub OIDC")
    assert "id-token: write" in lines[0]
    assert calls == []
    assert server.requests() == []
    assert list(runner_temp.iterdir()) == []


@needs_bash
@needs_curl
def test_rejected_upload_warns_fails_the_step_and_posts_once(tmp_path, marker_repo, server):
    server.status = 500
    proc, calls, runner_temp = upload(tmp_path, marker_repo, server)
    assert proc.returncode == 1
    assert "::warning title=freshlint team::upload failed (HTTP 500)" in proc.stdout
    assert [r["method"] for r in server.requests()] == ["GET", "POST"]
    assert list(runner_temp.iterdir()) == []


@needs_bash
@needs_curl
@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://example.com/ingest",
        "ftp://127.0.0.1/ingest",
        "http://localhost:@example.com/ingest",
        "https://user:pass@example.com/ingest",
        "file:///etc/passwd",
    ],
)
def test_upload_refuses_unsafe_team_urls_before_any_request(tmp_path, marker_repo, server, url):
    proc, calls, runner_temp = upload(tmp_path, marker_repo, server, FL_TEAM_URL=url)
    assert proc.returncode == 1
    assert "team-url must be an https:// URL" in proc.stdout
    assert calls == []
    assert server.requests() == []


@needs_bash
@needs_curl
def test_upload_skips_when_the_check_cannot_run(tmp_path, marker_repo, server):
    proc, calls, _ = upload(tmp_path, marker_repo, server, FL_MODE="bogus")
    assert proc.returncode == 1
    assert "::error" not in proc.stdout  # the check step already reported it
    assert "the check arguments are invalid" in proc.stdout
    assert calls == [] and server.requests() == []


@needs_bash
@needs_curl
def test_upload_refuses_a_token_that_is_not_a_bare_token(tmp_path, marker_repo, server):
    server.token = "evil\r\nX-Injected: 1"
    proc, calls, runner_temp = upload(tmp_path, marker_repo, server)
    assert proc.returncode == 1
    assert "the OIDC token response was not usable" in proc.stdout
    assert [r["method"] for r in server.requests()] == ["GET"]
    assert list(runner_temp.iterdir()) == []


ACME_GOLDENS = sorted((ROOT / "tests" / "fixtures" / "action").glob("acme-*.txt"))


@needs_bash
@pytest.mark.parametrize("golden", ACME_GOLDENS, ids=lambda p: p.stem)
def test_check_step_matches_the_v0_2_0_acme_goldens(golden):
    """The goldens were produced by the v0.2.0 step script; the team-off CI job reuses them."""
    _, fmt, today = golden.stem.split("-", 2)
    env = env_for(FL_FORMAT=fmt, FL_TODAY=today)
    proc = bash(step("Run freshlint")["run"], ROOT / "examples" / "acme-memory", env)
    assert proc.stdout == golden.read_bytes()
    assert proc.returncode == (0 if today == "2026-09-30" else 1)


def test_there_are_acme_goldens_for_both_outcomes():
    assert {p.stem for p in ACME_GOLDENS} == {
        f"acme-{fmt}-{day}" for fmt in ("github", "text") for day in ("2026-09-30", "2026-12-31")
    }
