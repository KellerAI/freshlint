"""The evidence store: fingerprints and receipts kept next to a memory directory.

``lock.json`` holds, per note, the fingerprint of each citation and one inferred baseline per
mentioned path that no citation covers (with the time it was taken; it lives until the mention
leaves the note or a citation covers it). ``receipts.md`` is an append-only log, one line per
renewal, in the same format as freshlint's verification receipts. Neither file holds code.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from freshlint.fingerprint import Fingerprint

STORE_VERSION = 1
STATE_INSIDE = True  # spike S1: keep state in <memory dir>/.freshlint/
STATE_DIRNAME = ".freshlint"
LOCK_TIMEOUT_S = 2.0  # how long a hook waits for another hook's load, change and save to finish
RECEIPTS_HEADER = (
    "# freshlint memory receipts\n\n"
    "Append-only. freshlint adds a line each time an agent re-reads cited code and renews a\n"
    "memory's evidence.\n"
)


@dataclass
class CitationRecord:
    fingerprint: Fingerprint | None
    stamped: str | None
    # Why a citation the save hook saw has no fingerprint although the agent read the file:
    # "changed" (it changed after the read) or "missing:<why>" (the symbol, key or range is not
    # there). None for a citation that was never read.
    problem: str | None = None


@dataclass(frozen=True)
class InferredRecord:
    kind: str  # "file" or "dir"
    method: str  # "git" or "mtime"
    baseline: str | None  # file digest when the note was written (git method only)
    when: float | None = None  # the note time the baseline was taken for (epoch seconds)


@dataclass
class NoteRecord:
    citations: dict[str, CitationRecord] = field(default_factory=dict)
    inferred: dict[str, InferredRecord] = field(default_factory=dict)
    inferred_key: str | None = None
    # The mentions a cut baselining pass looked at that have no record (never existed, or deleted
    # at their last commit before the note), and the key of the mention set that pass was for.
    # Only a pass of that same set goes on from them; any other set looks at every mention again.
    no_record: list[str] = field(default_factory=list)
    no_record_key: str | None = None


@dataclass
class Store:
    notes: dict[str, NoteRecord] = field(default_factory=dict)
    corrupt: bool = False
    last_scan: float | None = None


def state_dir(memory_dir: Path) -> Path:
    if STATE_INSIDE:
        return memory_dir / STATE_DIRNAME
    return memory_dir.parent / f"{memory_dir.name}-freshlint"


def store_path(memory_dir: Path) -> Path:
    return state_dir(memory_dir) / "lock.json"


def receipts_path(memory_dir: Path) -> Path:
    return state_dir(memory_dir) / "receipts.md"


@contextlib.contextmanager
def locked(memory_dir: Path, timeout: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold an advisory lock while one process loads, changes and saves the store.

    Two hooks that save at once (parallel subagents, two windows on a project) would otherwise
    each load the same store and the later write would drop the other's record. The lock is
    best effort and fails open: without ``fcntl`` (Windows), without write access, or when it
    cannot be taken within ``timeout`` seconds, the caller carries on unlocked.
    """
    handle = None
    try:
        import fcntl

        path = state_dir(memory_dir) / "lock.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
    except (ImportError, OSError):
        pass
    try:
        yield
    finally:
        if handle is not None:
            handle.close()  # closing the file releases the lock


def _encode(store: Store) -> dict:
    notes = {}
    for name, note in sorted(store.notes.items()):
        notes[name] = {
            "citations": {
                raw: {
                    "fingerprint": rec.fingerprint.to_dict() if rec.fingerprint else None,
                    "stamped": rec.stamped,
                    **({"problem": rec.problem} if rec.problem is not None else {}),
                }
                for raw, rec in sorted(note.citations.items())
            },
            "inferred": {
                mention: {
                    "kind": rec.kind,
                    "method": rec.method,
                    "baseline": rec.baseline,
                    "when": rec.when,
                }
                for mention, rec in sorted(note.inferred.items())
            },
            "inferred_key": note.inferred_key,
            **(
                {"no_record": {"key": note.no_record_key, "mentions": sorted(note.no_record)}}
                if note.no_record and note.no_record_key is not None
                else {}
            ),
        }
    encoded = {"version": STORE_VERSION, "notes": notes}
    if store.last_scan is not None:
        encoded["last_scan"] = store.last_scan
    return encoded


def _decode(data: object) -> Store:
    if not isinstance(data, dict) or data.get("version") != STORE_VERSION:
        raise ValueError("unsupported store version")
    notes: dict[str, NoteRecord] = {}
    for name, raw in data["notes"].items():
        citations = {
            key: CitationRecord(
                Fingerprint.from_dict(value["fingerprint"]) if value.get("fingerprint") else None,
                value.get("stamped"),
                value.get("problem"),
            )
            for key, value in raw.get("citations", {}).items()
        }
        inferred = {
            key: InferredRecord(
                value["kind"],
                value["method"],
                value.get("baseline"),
                None if value.get("when") is None else float(value["when"]),
            )
            for key, value in raw.get("inferred", {}).items()
        }
        looked = raw.get("no_record") or {}
        notes[name] = NoteRecord(
            citations,
            inferred,
            raw.get("inferred_key"),
            [str(mention) for mention in looked.get("mentions", [])],
            looked.get("key"),
        )
    scanned = data.get("last_scan")
    return Store(notes, last_scan=None if scanned is None else float(scanned))


def load_store(memory_dir: Path) -> Store:
    path = store_path(memory_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return Store()  # only a cleanly missing store is a first run; a permission error is not
    except (OSError, ValueError):  # unreadable, or not UTF-8
        return Store(corrupt=True)
    try:
        return _decode(json.loads(text))
    except (ValueError, KeyError, TypeError, AttributeError):
        return Store(corrupt=True)


def save_store(memory_dir: Path, store: Store) -> None:
    target = store_path(memory_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".lock-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_encode(store), handle, indent=1, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def append_receipts(memory_dir: Path, day: dt.date, entries: list[tuple[str, str, str]]) -> None:
    if not entries:
        return
    path = receipts_path(memory_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    last = None
    for line in existing.splitlines():
        if line.startswith("## "):
            last = line[3:].strip()
    chunk = [] if existing else [RECEIPTS_HEADER]
    if last != day.isoformat():
        chunk.append(f"\n## {day.isoformat()}\n")
    for note, citation, session in entries:
        chunk.append(f"- verified {note}#evidence — re-read {citation} (session {session[:8]})\n")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("".join(chunk))
