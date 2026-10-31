import contextlib
import io
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from freshlint.cli import main  # noqa: E402

TODAY = "2026-09-30"


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def payload(self):
        return json.loads(self.out)

    @property
    def findings(self):
        return self.payload["findings"]

    def rules(self, severity=None):
        return [f["rule"] for f in self.findings if severity is None or f["severity"] == severity]


def run_cli(cwd, *args):
    out, err = io.StringIO(), io.StringIO()
    previous = Path.cwd()
    os.chdir(cwd)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
    finally:
        os.chdir(previous)
    return Result(code, out.getvalue(), err.getvalue())


class Project:
    """A temporary memory project, optionally a git repository."""

    def __init__(self, path: Path):
        self.path = path

    def write(self, rel, text):
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def read(self, rel):
        return (self.path / rel).read_text()

    def replace(self, rel, old, new):
        text = self.read(rel)
        assert old in text, f"{old!r} not in {rel}"
        self.write(rel, text.replace(old, new, 1))

    def append(self, rel, text):
        target = self.path / rel
        existing = target.read_text() if target.exists() else ""
        self.write(rel, existing + text)

    def remove(self, rel):
        (self.path / rel).unlink()

    def git(self, *args):
        proc = subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, text=True, check=True
        )
        return proc.stdout.strip()

    def init(self):
        self.git("init", "-q", "-b", "main")
        return self

    def commit(self, message="change"):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def commit_at(self, when, message="change"):
        """Commit with fixed author and committer dates (ISO 8601)."""
        env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        subprocess.run(["git", "add", "-A"], cwd=self.path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", message],
            cwd=self.path,
            check=True,
            capture_output=True,
            env=env,
        )
        return self.git("rev-parse", "HEAD")

    def stage(self, *paths):
        self.git("add", "-A", *paths)

    def lint(self, *args, today=TODAY, fmt="json", cwd=None):
        extra = ["--format", fmt] if fmt else []
        if today:
            extra += ["--today", today]
        return run_cli(cwd or self.path, "check", *args, *extra)


@pytest.fixture(autouse=True)
def isolated_git(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    gitconfig = home / ".gitconfig"
    gitconfig.write_text("")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "Test Author")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "test@example.invalid")
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)
    for var in ("XDG_CACHE_HOME", "FRESHLINT_CACHE_DIR", "FRESHLINT_EVENTS", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "memory"
    root.mkdir()
    proj = Project(root)
    proj.write(".freshlint.toml", "")
    return proj


@pytest.fixture
def repo(project):
    return project.init()


class ClaudeProject(Project):
    """A git project plus its Claude Code memory directory under the test HOME."""

    @property
    def memory(self) -> Path:
        from freshlint.memory import project_slug

        return Path.home() / ".claude" / "projects" / project_slug(self.path.resolve()) / "memory"

    def note(self, name, body, note_type="project", modified="2026-09-01T12:00:00Z"):
        lines = ["---", f"name: {name}", "description: test note", "metadata:"]
        lines.append(f"  type: {note_type}")
        if modified:
            lines.append(f"  modified: {modified}")
        lines += ["---", "", body, ""]
        target = self.memory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines))
        return target


@pytest.fixture
def claude_project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return ClaudeProject(root).init()


@pytest.fixture(params=["native", "swallowing"])
def pathlib_flavour(request, monkeypatch):
    """Run a test under both ``pathlib`` behaviours for access errors.

    Python 3.14 makes ``Path.is_file``, ``is_dir`` and ``exists`` answer False on any OSError (a
    permission error included); earlier versions raise it. Code must never read that False as
    "gone", so permission tests run under both.
    """
    if request.param == "swallowing":
        monkeypatch.setattr(Path, "is_file", lambda self, **_: os.path.isfile(self))
        monkeypatch.setattr(Path, "is_dir", lambda self, **_: os.path.isdir(self))
        monkeypatch.setattr(Path, "exists", lambda self, **_: os.path.exists(self))
    return request.param
