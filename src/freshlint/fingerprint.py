"""Content fingerprints for citations: what a memory's evidence looked like.

Text is normalised first (CRLF to LF, trailing whitespace removed, leading and trailing
blank lines dropped), so whitespace-only edits do not count as changes. Only hashes are
kept, never code. Files larger than ``MAX_BYTES``, and files that are not valid UTF-8, are
hashed whole, byte for byte, and a narrower citation of them is tracked at file level.

Invariant: ``compare`` never answers FRESH when the cited content changed. Anything it cannot
establish (a stored entry that is incomplete, a different kind of citation, a line range that
may have moved to a look-alike place) is answered CHANGED, which can only over-report.
"""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path

from freshlint import gitio, symbols
from freshlint.evidence import Citation

MAX_BYTES = 2_000_000
DIR_MAX_FILES = 50  # a directory citation covers at most this many files
DIR_MAX_BYTES = 2_000_000  # and at most this many bytes in total
MIN_RELOCATE_LINES = 3  # a moved line range is trusted only with this many non-blank lines
FRESH, CHANGED, GONE = "fresh", "changed", "gone"


class TargetMissing(Exception):
    """The cited file, line range, symbol or key does not exist."""


class _Unreadable(TargetMissing):
    """The cited file exists but cannot be read, so it is never reported as gone."""


_UNREADABLE = "<unreadable>"  # ``_load`` gives this in place of a digest (never a hex digest)


@dataclass(frozen=True)
class Fingerprint:
    kind: str
    digest: str
    line_digests: tuple[str, ...] = ()
    fallback: bool = False  # True when a narrower citation is tracked at file level
    start: int | None = None  # a line range's first line when it was taken
    unique: bool = False  # the line range occurred exactly once in the file when it was taken
    # A directory's manifest: (path relative to the directory, file digest) per file, sorted.
    entries: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict:
        data = {
            "kind": self.kind,
            "digest": self.digest,
            "line_digests": list(self.line_digests),
            "fallback": self.fallback,
            "start": self.start,
            "unique": self.unique,
        }
        if self.entries:
            data["entries"] = [list(entry) for entry in self.entries]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Fingerprint:
        return cls(
            str(data["kind"]),
            str(data["digest"]),
            tuple(str(d) for d in data.get("line_digests", ())),
            bool(data.get("fallback", False)),
            None if data.get("start") is None else int(data["start"]),
            bool(data.get("unique", False)),
            tuple((str(rel), str(digest)) for rel, digest in data.get("entries", ())),
        )


_ISOLATED = {"symbol": "symbol", "key": "key", "lines": "line range"}


def level_note(cit: Citation, stored: Fingerprint, path: Path | None = None) -> str:
    """The words that tell the reader a narrow citation is tracked as a whole file.

    Empty unless ``stored`` fell back to file level for a symbol, key or line range: then a
    "changed" finding can be about another part of the file, and the message must say so. The
    same goes when ``path`` (the cited file) can no longer be isolated although the stored
    fingerprint was (a later version of the scan refuses a shape it used to read): the stored span
    cannot be compared, so the citation reads ``changed`` once and the file is what was compared.
    """
    if cit.kind == "file":
        return ""
    if not stored.fallback and (path is None or not _falls_back_now(cit, path)):
        return ""
    what = _ISOLATED.get(cit.kind, "symbol")
    return f" (tracked at file level: this {what} could not be isolated)"


def _falls_back_now(cit: Citation, path: Path) -> bool:
    """Whether the cited symbol, key or range of ``path`` is only tracked at file level now."""
    try:
        return take(cit, path).fallback
    except TargetMissing:  # absent, unreadable or deleted: nothing was compared as a file
        return False


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def normalize(text: str) -> list[str]:
    lines = [line.rstrip() for line in _split(text)]
    start, end = 0, len(lines)
    while start < end and not lines[start]:
        start += 1
    while end > start and not lines[end - 1]:
        end -= 1
    return lines[start:end]


def file_digest(text: str) -> str:
    return _sha("\n".join(normalize(text)))


UNREAD = "unread:"  # a record problem: the files of a directory left unread, one per line
UNREAD_NAMES_SHOWN = 5  # unread files a message names before "…and N more"


def unread_summary(names: list[str]) -> str:
    """``3 files unread: a.py, b.py, c.py``, naming the first few in sorted order."""
    names = sorted(names)
    shown = ", ".join(names[:UNREAD_NAMES_SHOWN])
    more = len(names) - UNREAD_NAMES_SHOWN
    count = f"{len(names)} file{'' if len(names) == 1 else 's'} unread"
    return f"{count}: {shown}" + (f" …and {more} more" if more > 0 else "")


def cites_directory(cit: Citation) -> bool:
    """Whether a citation of a directory is fingerprinted as one (a manifest of its files).

    ``dir/`` always is. A bare ``dir`` is too: it is taken as ``dir/`` at the next save, with
    the note left as written. Any ``#`` part (symbol, key, line range) never is.
    """
    return cit.kind == "file"


def directory_problem(cit: Citation) -> str:
    """What is wrong with citing a directory without the trailing ``/`` of a directory citation."""
    if cit.kind == "file":
        return f"{cit.path} is a directory; write it as `{cit.path}/` or cite a file inside it"
    return f"{cit.path} is a directory; cite a file inside it"


def directory_files(path: Path, cited: str) -> list[tuple[str, Path]]:
    """The files a directory citation covers: ``(path relative to it, path)``, sorted.

    Only files git would track count (committed, staged or untracked but not ignored), so
    build output and other ignored files never enter a manifest. A directory outside a git
    repository, one with no such files, and one over ``DIR_MAX_FILES`` files or
    ``DIR_MAX_BYTES`` bytes are refused with ``TargetMissing``: never a weaker check.
    """
    shown = cited if cited.endswith("/") else cited + "/"
    try:
        if gitio.toplevel(path) is None:
            raise TargetMissing(
                f"{shown} is not in a git repo: directory citations need a git repo"
            )
        names = gitio.ls_files(path)
    except gitio.GitError:
        raise TargetMissing(
            f"{shown} could not be listed: directory citations need a git repo"
        ) from None
    files: list[tuple[str, Path]] = []
    total = 0
    for rel in names:
        target = path / rel
        try:
            info = target.stat()
        except OSError:
            continue  # deleted from the work tree but still in the index: not there to cite
        if not stat.S_ISREG(info.st_mode):
            continue
        files.append((rel, target))
        total += info.st_size
        if len(files) > DIR_MAX_FILES or total > DIR_MAX_BYTES:
            raise TargetMissing(
                f"{shown} has more than {DIR_MAX_FILES} files or "
                f"{DIR_MAX_BYTES // 1_000_000} MB: too large; cite a narrower directory or files"
            )
    if not files:
        raise TargetMissing(f"{shown} has no tracked files")
    return files


def _manifest(path: Path, cited: str) -> Fingerprint:
    entries = []
    for rel, target in directory_files(path, cited):
        digest = current_digest(target)
        if digest is None:
            raise _Unreadable(f"{cited}{'' if cited.endswith('/') else '/'}{rel} cannot be read")
        entries.append((rel, digest))
    digest = _sha("\n".join(f"{rel}\t{d}" for rel, d in sorted(entries)))
    return Fingerprint("dir", digest, entries=tuple(sorted(entries)))


def directory_changes(stored: Fingerprint, path: Path) -> list[str]:
    """What differs between a stored directory manifest and the directory now, file by file."""
    try:
        now = dict(_manifest(path, str(path)).entries)
    except TargetMissing:
        return []
    then = dict(stored.entries)
    changes = [f"{rel} edited" for rel in sorted(then) if rel in now and now[rel] != then[rel]]
    changes += [f"{rel} added" for rel in sorted(now) if rel not in then]
    changes += [f"{rel} removed" for rel in sorted(then) if rel not in now]
    return changes


def is_missing(path: Path) -> bool:
    """Whether ``path`` is cleanly absent; an access error is not "missing"."""
    try:
        path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
    return False


def _load(path: Path) -> tuple[str | None, str | None]:
    """Read ``path`` as ``(text, None)``, ``(None, raw_digest)`` or ``(None, None)`` if absent.

    A large or non-UTF-8 file gives its raw byte digest instead of text: invalid bytes are never
    decoded lossily, because two different ones would become the same replacement character.
    A path that exists but cannot be read (permissions, I/O errors) gives ``(None, _UNREADABLE)``,
    which callers must not treat as absent: only a cleanly missing path is ever gone.

    The probe is ``stat``, not ``Path.is_file``: Python 3.14 makes ``is_file`` answer False on a
    permission error, which would read as a missing file.
    """
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            return None, None
        if info.st_size > MAX_BYTES:
            return None, hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            return path.read_text(encoding="utf-8"), None
        except UnicodeDecodeError:
            return None, hashlib.sha256(path.read_bytes()).hexdigest()
    except (FileNotFoundError, NotADirectoryError):
        return None, None
    except OSError:
        return None, _UNREADABLE


def current_digest(path: Path) -> str | None:
    """File-level digest of ``path`` now, or None if it is not a readable file."""
    text, large = _load(path)
    if large == _UNREADABLE:
        return None
    if large is not None:
        return large
    return None if text is None else file_digest(text)


def _hashes(lines: list[str]) -> list[str]:
    return [_sha(line.rstrip()) for line in lines]


def _count(haystack: list[str], needle: tuple[str, ...]) -> int:
    """How many times ``needle`` occurs in ``haystack``, overlaps included; never for empty."""
    if not needle:
        return 0
    width = len(needle)
    return sum(
        1
        for i in range(len(haystack) - width + 1)
        if haystack[i] == needle[0] and tuple(haystack[i : i + width]) == needle
    )


def _tagged(value: object) -> dict[str, str]:
    """Encode values JSON has no type for (TOML dates, times) so they differ from strings."""
    return {"__type__": type(value).__name__, "value": str(value)}


def _as_written(path: Path, text: str) -> str:
    """``text`` with the line ends ``path`` has, if the file still reads as ``text``.

    ``_load`` reads with universal newlines, which turns a backslash, a line feed and a lone
    carriage return into a backslash before an empty line; a C or C++ compiler takes them as one
    line splice, and the C and C++ scan refuses them when it can see them. Line numbers are the
    same either way. A file that changed since ``text`` was read gives ``text`` back.
    """
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            raw = handle.read()
    except (OSError, UnicodeDecodeError):
        return text
    return raw if raw.replace("\r\n", "\n").replace("\r", "\n") == text else text


def _symbol_span(cit: Citation, text: str, path: Path) -> tuple[int, int] | None:
    """Span of the cited symbol, or None when it cannot be located reliably.

    A locator that gives up (``Unparseable``) or fails on pathological input
    (``RecursionError``, ``ValueError``) means the citation is tracked at file level. C and C++
    are scanned with the line ends as written (``_as_written``).
    """
    suffix = Path(cit.path).suffix.lower()
    try:
        if suffix in symbols.PYTHON_SUFFIXES:
            return symbols.locate_python(text, cit.name or "")
        if suffix in symbols.CXX_SUFFIXES:
            return symbols.locate_braced(_as_written(path, text), cit.name or "", suffix)
        if suffix in symbols.BRACED_SUFFIXES:
            return symbols.locate_braced(text, cit.name or "", suffix)
    except symbols.NotFound:
        raise TargetMissing(f"{cit.name!r} is not defined in {cit.path}") from None
    except (symbols.Unparseable, RecursionError, ValueError):
        return None
    return None


def take(cit: Citation, path: Path) -> Fingerprint:
    text, large = _load(path)
    if large == _UNREADABLE:
        raise _Unreadable(f"{cit.path} cannot be read")
    if text is None and large is None:
        if path.is_dir():
            if cites_directory(cit):
                return _manifest(path, cit.path)
            raise TargetMissing(directory_problem(cit))
        raise TargetMissing(f"{cit.path} does not exist")
    if large is not None:
        return Fingerprint(cit.kind, large, fallback=cit.kind != "file")
    assert text is not None
    if cit.kind == "file":
        return Fingerprint("file", file_digest(text))
    lines = _split(text)
    if cit.kind == "lines":
        start, end = cit.start or 1, cit.end or 1
        count = len(lines) - 1 if lines[-1] == "" else len(lines)  # no line after the last newline
        if end > count:
            raise TargetMissing(f"{cit.path} has only {count} lines")
        span = normalize("\n".join(lines[start - 1 : end]))
        if not span:  # a blank range says nothing that could be compared later
            return Fingerprint("lines", file_digest(text), fallback=True)
        digests = tuple(_sha(line) for line in span)
        unique = _count(_hashes(lines), digests) == 1
        return Fingerprint("lines", _sha("\n".join(span)), digests, start=start, unique=unique)
    if cit.kind == "key":
        try:
            value = symbols.locate_key(text, Path(cit.path).suffix.lower(), cit.name or "")
            canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=_tagged)
        except symbols.NotFound:
            raise TargetMissing(f"key {cit.name!r} is not in {cit.path}") from None
        except (symbols.Unparseable, RecursionError):  # RecursionError: absurdly nested data
            return Fingerprint("key", file_digest(text), fallback=True)
        return Fingerprint("key", _sha(canonical))
    span = _symbol_span(cit, text, path)
    if span is None:
        return Fingerprint("symbol", file_digest(text), fallback=True)
    if Path(cit.path).suffix.lower() in symbols.BRACED_SUFFIXES:
        lines = text.splitlines()  # the span counts lines the way the braced scan does
    body = normalize("\n".join(lines[span[0] - 1 : span[1]]))
    return Fingerprint("symbol", _sha("\n".join(body)))


def _lines_unchanged(cit: Citation, stored: Fingerprint, text: str) -> bool:
    """Whether a line range still holds what it held, in place or (for a solid block) moved."""
    needle = stored.line_digests
    if not needle:
        return False
    lines = _split(text)
    if stored.start is not None and stored.start >= 1:
        height = (cit.end or 1) - (cit.start or 1)
        here = normalize("\n".join(lines[stored.start - 1 : stored.start + height]))
        if tuple(_sha(line) for line in here) == needle:
            return True
    blank = _sha("")
    solid = sum(1 for digest in needle if digest != blank) >= MIN_RELOCATE_LINES
    return solid and stored.unique and _count(_hashes(lines), needle) == 1


def _compare_directory(stored: Fingerprint, path: Path) -> str:
    if is_missing(path):
        return GONE
    try:
        current = _manifest(path, str(path))
    except TargetMissing:  # unreadable, emptied, over the cap or out of git: not shown unchanged
        return CHANGED
    return FRESH if current.digest == stored.digest else CHANGED


def compare(cit: Citation, stored: Fingerprint, path: Path) -> str:
    if stored.kind == "dir":  # before the load below, which answers GONE for any directory
        return _compare_directory(stored, path)
    text, large = _load(path)
    if large == _UNREADABLE:
        return CHANGED  # present but unreadable: it cannot be shown unchanged, nor gone
    if text is None and large is None:
        return GONE
    if stored.kind != cit.kind:
        return CHANGED
    if stored.fallback or large is not None:
        now = large if large is not None else file_digest(text or "")
        return FRESH if now == stored.digest else CHANGED
    if cit.kind == "lines":
        return FRESH if _lines_unchanged(cit, stored, text or "") else CHANGED
    try:
        current = take(cit, path)
    except _Unreadable:
        return CHANGED  # became unreadable between the load above and the take
    except TargetMissing:
        return GONE
    if current.fallback:
        return CHANGED
    return FRESH if current.digest == stored.digest else CHANGED
