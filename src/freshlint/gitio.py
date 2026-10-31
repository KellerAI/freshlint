"""Thin, read-only wrappers around the git CLI.

freshlint never writes to a repository. Every command here only reads objects, the index, or
refs, and git runs with ``GIT_OPTIONAL_LOCKS=0`` so it takes no optional lock. Paths are
relative to the directory passed as ``cwd`` (the project root), using ``--relative`` and
``REV:./path`` so a memory project can live in a subdirectory of a larger repository.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(Exception):
    """A git command failed (exit code 2)."""


@dataclass(frozen=True)
class Change:
    status: str  # A, M, D, R (renames), C treated as A, T treated as M
    old_path: str | None
    new_path: str | None


def _run(cwd: Path, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=cwd,
            input=stdin,
            capture_output=True,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},  # never refresh (write) the index
        )
    except FileNotFoundError:
        raise GitError("git is not installed or not on PATH") from None


def _checked(cwd: Path, *args: str, stdin: bytes | None = None) -> str:
    proc = _run(cwd, *args, stdin=stdin)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or f"git {' '.join(args)} failed"
        raise GitError(detail)
    return proc.stdout.decode("utf-8", "replace")


def toplevel(path: Path) -> Path | None:
    try:
        proc = _run(path, "rev-parse", "--show-toplevel")
    except GitError:
        return None
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.decode().strip()).resolve()


def ls_files(cwd: Path) -> list[str]:
    out = _checked(cwd, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({p for p in out.split("\0") if p})


def show(cwd: Path, rev: str, path: str) -> str | None:
    proc = _run(cwd, "show", f"{rev}:./{path}")
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def show_index(cwd: Path, path: str) -> str | None:
    proc = _run(cwd, "show", f":./{path}")
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def has_head(cwd: Path) -> bool:
    return _run(cwd, "rev-parse", "--verify", "-q", "HEAD").returncode == 0


def empty_tree(cwd: Path) -> str:
    return _checked(cwd, "hash-object", "-t", "tree", "--stdin", stdin=b"").strip()


def verify_rev(cwd: Path, rev: str) -> str:
    proc = _run(cwd, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}")
    if proc.returncode != 0:
        raise GitError(f"unknown revision {rev!r}")
    return proc.stdout.decode().strip()


def merge_base(cwd: Path, a: str, b: str) -> str:
    proc = _run(cwd, "merge-base", a, b)
    if proc.returncode != 0:
        raise GitError(f"no merge base between {a!r} and {b!r}")
    return proc.stdout.decode().strip()


def _parse_name_status(out: str) -> list[Change]:
    tokens = out.split("\0")
    changes: list[Change] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index]
        kind = status[0]
        if kind in ("R", "C"):
            old, new = tokens[index + 1], tokens[index + 2]
            index += 3
            changes.append(Change("R", old, new) if kind == "R" else Change("A", None, new))
            continue
        path = tokens[index + 1]
        index += 2
        if kind == "A":
            changes.append(Change("A", None, path))
        elif kind == "D":
            changes.append(Change("D", path, None))
        else:  # M, T, U
            changes.append(Change("M", path, path))
    return changes


def diff_changes(cwd: Path, *revs: str, cached: bool = False) -> list[Change]:
    args = ["diff", "--name-status", "-z", "-M", "--relative", "--no-color", "--no-ext-diff"]
    if cached:
        args.append("--cached")
    return _parse_name_status(_checked(cwd, *args, *revs))


def common_dir(cwd: Path) -> Path | None:
    """The shared ``.git`` directory (the main worktree's), or None outside git."""
    try:
        proc = _run(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except GitError:
        return None
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.decode().strip()).resolve()


def last_commit_time(cwd: Path, path: str) -> float | None:
    """Committer time of the last commit that touched ``path``, or None."""
    proc = _run(cwd, "log", "-1", "--format=%ct", "--", path)
    if proc.returncode != 0:
        return None
    out = proc.stdout.decode().strip()
    return float(out) if out.isdigit() else None


def is_modified(cwd: Path, path: str) -> bool:
    """True if the working-tree ``path`` differs from HEAD, or is not in HEAD.

    The file is hashed and compared with HEAD's blob. ``git diff`` answers the same question but
    refreshes the index as a side effect, taking ``.git/index.lock`` that a user's ``git add``
    may need; ``GIT_OPTIONAL_LOCKS=0`` does not stop that for ``git diff``.
    """
    committed = _run(cwd, "rev-parse", "--verify", "-q", f"HEAD:./{path}")
    if committed.returncode != 0:
        return True
    current = _run(cwd, "hash-object", "--", path)
    return current.returncode != 0 or current.stdout.strip() != committed.stdout.strip()


CHECK_IGNORE_EXTRA_CALLS = 16  # after a failed batch: one probe plus at most 15 split calls


def _check_ignore_batch(cwd: Path, paths: list[str]) -> set[str] | None:
    """One ``git check-ignore`` call: the ignored paths, or None if git failed outright."""
    try:
        stdin = b"".join(path.encode() + b"\0" for path in paths)
        proc = _run(cwd, "check-ignore", "-z", "--stdin", stdin=stdin)
    except GitError:
        return None
    if proc.returncode not in (0, 1):  # 0: some ignored, 1: none; anything else is a failure
        return None
    return {path for path in proc.stdout.decode("utf-8", "replace").split("\0") if path}


def check_ignore(cwd: Path, paths: list[str]) -> set[str]:
    """The ``paths`` that git ignores, usually found with one ``git check-ignore`` call.

    ``paths`` are relative to ``cwd``. A tracked file is never ignored, whatever the ignore files
    say. Git fails the whole call (exit 128) for a path it cannot answer for, such as one beyond a
    symlink or inside a submodule. The wrapper then probes once for a repository (none: nothing
    is ignored) and splits the paths in halves, asking each half, and the halves that fail again,
    until ``CHECK_IGNORE_EXTRA_CALLS`` extra calls are used. Paths still unanswered at that point
    count as not ignored, which is what happened before the split existed.
    """
    if not paths:
        return set()
    found = _check_ignore_batch(cwd, paths)
    if found is not None:
        return found
    if len(paths) == 1 or _check_ignore_batch(cwd, []) is None:  # one bad path, or no repository
        return set()
    budget = CHECK_IGNORE_EXTRA_CALLS - 1  # the probe was the first extra call

    def split(chunk: list[str]) -> set[str]:
        nonlocal budget
        middle = len(chunk) // 2
        answered: set[str] = set()
        failed: list[list[str]] = []
        for half in (chunk[:middle], chunk[middle:]):
            if budget <= 0:
                break
            budget -= 1
            result = _check_ignore_batch(cwd, half)
            if result is None:
                if len(half) > 1:
                    failed.append(half)  # a lone failing path is the bad one: it answers nothing
            else:
                answered |= result
        for half in failed:  # both siblings were asked first, so a good half is never starved
            answered |= split(half)
        return answered

    return split(paths)


LOG_SLICE = 128  # the first read of the history stops after this many commits that touch a path
LOG_GROWTH = 8  # a path still unfound after a slice is looked for again in a slice this much longer


def _prefixes(name: str) -> list[str]:
    """``a/b/c.py`` and every directory above it: the paths a change to that file touches."""
    parts = name.split("/")
    return ["/".join(parts[:count]) for count in range(len(parts), 0, -1)]


def _touched(out: bytes) -> list[tuple[str, list[str]]]:
    """Parse ``git log -z --name-only --format=%x01%H`` into ``(commit, touched files)`` pairs."""
    commits: list[tuple[str, list[str]]] = []
    after_header = False
    for token in out.split(b"\0"):
        if after_header and token.startswith(b"\n"):
            token = token[1:]  # git puts a newline between the header and the first file name
        after_header = False
        if token.startswith(b"\x01"):
            commits.append((token[1:].decode(), []))
            after_header = True
        elif token and commits:
            commits[-1][1].append(token.decode("utf-8", "replace"))
    return commits


def has_merges(cwd: Path) -> bool:
    """Whether a merge commit is reachable from HEAD; True whenever git cannot say.

    The answer picks how baselines are read: with no merge the cheap batched log is exact, with
    one only the per-path log is. A failure of git (an unborn HEAD, a damaged repository, git
    gone) must therefore read as "merges", the case that is always right. The ``--`` after
    ``HEAD`` keeps a root entry called ``HEAD`` from making the revision ambiguous, which would
    otherwise be such a failure.
    """
    try:
        proc = _run(cwd, "rev-list", "--min-parents=2", "--max-count=1", "HEAD", "--")
    except GitError:
        return True
    return proc.returncode != 0 or bool(proc.stdout.strip())


def last_touch(cwd: Path, when_iso: str, path: str) -> str | None:
    """The newest commit on HEAD at or before ``when_iso`` that touched ``path``, or None.

    This is the per-path answer, ``git log -1 --before=WHEN -- PATH``, on any history. A commit
    that deleted the path counts, so the caller finds the path missing there.
    """
    args = ["--literal-pathspecs", "log", "-1", f"--before={when_iso}", "--format=%H", "HEAD"]
    proc = _run(cwd, *args, "--", path)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode().strip() or None


def last_touches(cwd: Path, when_iso: str, paths: list[str]) -> dict[str, str]:
    """``last_touch`` for all the paths at once, exact only when no merge commit is reachable.

    One ``git log`` names the files each commit touched, newest first; the first commit to touch a
    path (or a file inside it, for a directory) is its answer. On a history without merge commits
    this equals ``last_touch`` for every path. With merges it can differ: ``git log`` lists no
    files for a merge, the union of the paths changes how history is simplified, and the walk is
    by date, not by ancestry. Use ``has_merges`` first, and ``last_touch`` per path when it is
    true.

    The log stops after ``LOG_SLICE`` commits and is read again, longer and for the paths still
    unfound only, when some are. A path no commit touched, or a failure of git, leaves the path
    out of the result.
    """
    found: dict[str, str] = {}
    remaining = list(dict.fromkeys(paths))
    limit = LOG_SLICE
    while remaining:
        proc = _run(
            cwd,
            "--literal-pathspecs",
            "log",
            f"--before={when_iso}",
            f"--max-count={limit}",
            "--no-renames",
            "--name-only",
            "-z",
            "--format=%x01%H",
            "HEAD",
            "--",
            *remaining,
        )
        if proc.returncode != 0:
            break
        commits = _touched(proc.stdout)
        wanted = set(remaining)
        for commit, names in commits:
            for name in names:
                for prefix in _prefixes(name):
                    if prefix in wanted:
                        found.setdefault(prefix, commit)
        remaining = [path for path in remaining if path not in found]
        if len(commits) < limit:
            break  # the history is used up: what is left was never touched
        limit *= LOG_GROWTH
    return found


def objects_at(cwd: Path, specs: list[tuple[str, str]]) -> list[tuple[str, bytes] | None]:
    """The type (``"blob"`` or ``"tree"``) and content of each ``(rev, path)``, in one git call.

    One ``git cat-file --batch`` answers for all of them, in order; a path that does not exist at
    its ``rev`` is None. A path with a newline in it cannot be asked about this way and counts as
    missing. A failure of git itself raises ``GitError``.
    """
    asked = [(rev, path) for rev, path in specs if "\n" not in path]
    if not asked:
        return [None] * len(specs)
    stdin = "".join(f"{rev}:./{path}\n" for rev, path in asked).encode()
    proc = _run(cwd, "cat-file", "--batch", stdin=stdin)
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip() or "git cat-file failed")
    out, position, found = proc.stdout, 0, []
    for _ in asked:
        end = out.find(b"\n", position)
        if end < 0:
            raise GitError("git cat-file --batch ended early")
        header = out[position:end]
        position = end + 1
        if header.endswith((b" missing", b" ambiguous")):
            found.append(None)
            continue
        _, kind, size = header.rsplit(b" ", 2)
        found.append((kind.decode(), out[position : position + int(size)]))
        position += int(size) + 1  # the content is followed by a newline
    answers = iter(found)
    return [None if "\n" in path else next(answers) for _, path in specs]
