"""Claude Code memory: where a project's memory directory is, and what its notes say.

Claude Code keeps auto-memory in ``~/.claude/projects/<slug>/memory/``. The slug is the
project path with every character that is not a letter or digit replaced by ``-``; a slug
longer than ``SLUG_LIMIT`` characters is cut and gets a hash suffix. Git worktrees of one
repository share the main worktree's memory. A project can choose another directory with
the ``autoMemoryDirectory`` setting; only absolute or ``~/`` paths are honored, a relative
value is ignored.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from freshlint import gitio
from freshlint.frontmatter import Node, parse_frontmatter, split_frontmatter

INDEX_NAME = "MEMORY.md"
NOTE_TYPES = frozenset({"user", "feedback", "project", "reference"})
SETTINGS_FILES = (".claude/settings.local.json", ".claude/settings.json")
SLUG_LIMIT = 200
_SLUG_RE = re.compile(r"[^A-Za-z0-9]")
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


@dataclass(frozen=True)
class Note:
    path: Path
    name: str
    type: str | None
    modified: dt.datetime | None
    written_at: float
    text: str


def _path_hash(text: str) -> str:
    """Claude Code's slug suffix: base 36 of abs(signed 32-bit ``h * 31 + c``) over UTF-16 units."""
    units = text.encode("utf-16-le")
    h = 0
    for index in range(0, len(units), 2):
        h = (h * 31 + int.from_bytes(units[index : index + 2], "little")) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    n = abs(h)
    digits = ""
    while n:
        n, rest = divmod(n, 36)
        digits = _BASE36[rest] + digits
    return digits or "0"


def project_slug(project_root: Path) -> str:
    text = str(project_root)
    slug = _SLUG_RE.sub("-", text)
    if len(slug) <= SLUG_LIMIT:
        return slug
    return f"{slug[:SLUG_LIMIT]}-{_path_hash(text)}"


def project_root_for(cwd: Path) -> Path:
    """The git top level that contains ``cwd``, or ``cwd`` itself outside git."""
    return gitio.toplevel(cwd) or cwd.resolve()


def memory_root(project_root: Path) -> Path:
    """The path Claude Code keys memory by: the main worktree for git repositories."""
    top = gitio.toplevel(project_root)
    if top is None:
        return project_root.resolve()
    common = gitio.common_dir(top)
    if common is not None and common.name == ".git":
        return common.parent
    return top


def _override(settings_root: Path, home: Path) -> str | None:
    candidates = [settings_root / rel for rel in SETTINGS_FILES]
    candidates.append(home / ".claude" / "settings.json")
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        value = data.get("autoMemoryDirectory") if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def memory_dir_for(project_root: Path, home: Path | None = None) -> Path:
    home = Path.home() if home is None else home
    settings_root = project_root_for(project_root)
    override = _override(settings_root, home)
    if override:
        if override.startswith("~/"):
            return (home / override[2:]).resolve()
        if Path(override).is_absolute():
            return Path(override).resolve()
    return home / ".claude" / "projects" / project_slug(memory_root(project_root)) / "memory"


def _value(front: dict[str, Node], key: str) -> Any:
    meta = front.get("metadata")
    if meta is not None and isinstance(meta.value, dict) and key in meta.value:
        return meta.value[key]
    node = front.get(key)
    return None if node is None else node.value


def _note_type(value: Any) -> str | None:
    if isinstance(value, str) and value.strip().lower() in NOTE_TYPES:
        return value.strip().lower()
    return None


def _modified(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _git_time(path: Path, git_root: Path | None) -> float | None:
    if git_root is None:
        return None
    try:
        rel = path.resolve().relative_to(git_root).as_posix()
    except ValueError:
        return None
    if gitio.is_modified(git_root, rel):
        return None
    return gitio.last_commit_time(git_root, rel)


def read_note(path: Path, git_root: Path | None = None) -> Note:
    text = path.read_text(encoding="utf-8", errors="replace")
    entries, _ = split_frontmatter(text.splitlines())
    front = parse_frontmatter(entries)
    modified = _modified(_value(front, "modified"))
    if modified is not None:
        written = modified.timestamp()
    else:
        written = _git_time(path, git_root) or path.stat().st_mtime
    return Note(path, path.name, _note_type(_value(front, "type")), modified, written, text)


def _is_other_than_file(path: Path) -> bool:
    """True if ``path`` is cleanly missing or is not a regular file (a folder, say).

    A path that cannot be examined stays in the list: dropping it would silently prune its
    fingerprints, while keeping it lets the scan report that the note could not be checked.
    """
    try:
        return not stat.S_ISREG(path.stat().st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False


def note_paths(memory_dir: Path, patterns: tuple[str, ...] = ("*.md",)) -> list[Path]:
    """The note files directly in ``memory_dir`` that match ``patterns``, sorted."""
    if not memory_dir.is_dir():
        return []
    paths = {
        path
        for pattern in patterns
        for path in memory_dir.glob(pattern)
        if path.name != INDEX_NAME and not _is_other_than_file(path)
    }
    return sorted(paths)


def list_notes(memory_dir: Path, patterns: tuple[str, ...] = ("*.md",)) -> list[Note]:
    paths = note_paths(memory_dir, patterns)
    git_root = gitio.toplevel(memory_dir) if paths else None
    return [read_note(path, git_root) for path in paths]


def is_note(path: Path, memory_dir: Path) -> bool:
    try:
        rel = path.resolve().relative_to(memory_dir.resolve())
    except ValueError:
        return False
    return len(rel.parts) == 1 and rel.suffix == ".md" and rel.name != INDEX_NAME
