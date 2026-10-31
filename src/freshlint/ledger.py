"""The read ledger: which files an agent read in a session, and their bytes' hash then.

One JSON line per read or edit, appended (so parallel hooks do not clobber each other).
The ledger holds paths and hashes, never contents. Files older than ``RETENTION_DAYS``
are deleted at session start.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

CACHE_ENV = "FRESHLINT_CACHE_DIR"
RETENTION_DAYS = 7
_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def cache_dir() -> Path:
    if os.environ.get(CACHE_ENV):
        return Path(os.environ[CACHE_ENV])
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "freshlint"


def _session_file(session_id: str, suffix: str) -> Path:
    safe = _SAFE_RE.sub("_", session_id)[:128] or "unknown"
    return cache_dir() / "sessions" / f"{safe}{suffix}"


def raw_digest(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def record(session_id: str, agent_id: str | None, path: Path) -> None:
    resolved = path.resolve()
    digest = raw_digest(resolved)
    if digest is None:
        return
    target = _session_file(session_id, ".jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"path": str(resolved), "sha256": digest, "agent": agent_id}) + "\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line)


def reads(session_id: str, agent_id: str | None) -> dict[str, str]:
    try:
        text = _session_file(session_id, ".jsonl").read_text(encoding="utf-8")
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(entry, dict)
            and entry.get("agent") == agent_id
            and isinstance(entry.get("path"), str)
            and isinstance(entry.get("sha256"), str)
        ):
            result[entry["path"]] = entry["sha256"]
    return result


def prune(now: float | None = None) -> None:
    cutoff = (time.time() if now is None else now) - RETENTION_DAYS * 86400
    try:
        entries = list((cache_dir() / "sessions").iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


def _report_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def same_as_last_report(session_id: str, text: str) -> bool:
    try:
        stored = _session_file(session_id, ".report").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return stored == _report_digest(text)


def remember_report(session_id: str, text: str) -> None:
    target = _session_file(session_id, ".report")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_report_digest(text), encoding="utf-8")
