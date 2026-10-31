"""Inferred citations: paths that a memory note mentions without an Evidence line citing them.

An older note has no Evidence line at all. A newer note can still mention a file that none of
its citations covers (``freshlint.coverage``); that mention is tracked here too.

A mention counts only if the path existed when the note was written (in git history at the
note's time) or exists now. That rule drops branch names, package names and placeholders.
For git-tracked files the baseline is the file's content at the note's time; otherwise
freshlint falls back to modification times, which is low confidence.
"""

from __future__ import annotations

import datetime as dt
import functools
import re
import stat
import time
from pathlib import Path

from freshlint import gitio
from freshlint.fingerprint import current_digest, file_digest
from freshlint.markdown import prose_lines
from freshlint.store import InferredRecord

FRESH, CHANGED, GONE = "fresh", "inferred-changed", "gone"
KNOWN_EXTENSIONS = frozenset(
    {
        "md",
        "py",
        "pyi",
        "ts",
        "tsx",
        "js",
        "jsx",
        "mjs",
        "cjs",
        "json",
        "toml",
        "yaml",
        "yml",
        "sh",
        "sql",
        "go",
        "rs",
        "java",
        "kt",
        "cs",
        "rb",
        "rego",
        "tf",
        "tfvars",
        "css",
        "html",
        "txt",
        "cfg",
        "ini",
        "lock",
        "env",
        "proto",
        "graphql",
    }
)
_TOKEN_RE = re.compile(r"[~\w./@+-]+")
_TRAILING = ".,:;)]}'\""
_NOT_PATHS = frozenset({".", "..", "~"})


def _candidate(token: str) -> str | None:
    token = token.rstrip(_TRAILING)
    if not token or token.startswith(("@", "-", "//")) or token.count("/") > 12:
        return None
    if "/" in token:
        token = token.rstrip("/")
        return token if token and token not in _NOT_PATHS else None
    extension = token.rsplit(".", 1)[-1].lower() if "." in token else ""
    return token if extension in KNOWN_EXTENSIONS else None


def mentions(text: str, skip_lines: frozenset[int] = frozenset()) -> list[str]:
    found: list[str] = []
    for number, line in prose_lines(text):
        if number in skip_lines:
            continue
        for token in _TOKEN_RE.findall(line):
            candidate = _candidate(token)
            if candidate and candidate not in found:
                found.append(candidate)
    return found


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_ABBREVIATION_END_RE = re.compile(r"(?:^|[\s(])(?:e\.g\.|i\.e\.|vs\.|etc\.)$", re.IGNORECASE)
MAX_BLOCK = 4000  # a longer paragraph is not read for deletions (mirrors claims.MAX_LINE)
_BLOCK_START_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s|^\s*#|^\s*\||^\s*>")
_PARTICIPLE = r"(?:deleted|removed|moved|renamed|retired)"
_PASSIVE_RE = re.compile(rf"\b(?:was|were|been|got)\s+(?:\w+\s+){{0,2}}?{_PARTICIPLE}\b")
_GONE_RE = re.compile(
    r"\b(?:is|are|was|were)\s+(?:now\s+|already\s+|long\s+)?gone\b|\bno longer exists?\b"
)
_ACTIVE_RE = re.compile(rf"\b{_PARTICIPLE}\b")
_NO_LONGER_RE = re.compile(r"\bno longer\b")
_CUE_RE = re.compile(
    r"\b(?:not|never|neither|nor|cannot|without|no|none|nobody|nothing|hardly|barely"
    r"|fail(?:s|ed)? to)\b|n['\u2019]t\b"  # negation
    r"|\b(?:if|when|whenever|unless|until|once|whether|in case|assuming|provided|suppose"
    r"|supposing|imagine|imagining|what if)\b"  # conditional
    r"|\b(?:will|would|shall|should|must|may|might|can|could|going to|about to|yet to|ought"
    r"|needs? to|plans? to|planned to|supposed to|expected to|to be|to get)\b"  # future, modal
    r"|\b(?:gets?|todo|proposals?|proposed)\b"  # habitual present, planning labels
    rf"|\bbe\s+(?:\w+\s+)?{_PARTICIPLE}\b"
)
_UNDONE_RE = re.compile(r"\b(?:restor\w*|revert\w*|re-?add\w*|reinstat\w*|un-?delet\w*)")
_QUESTION_START_RE = re.compile(
    r"^[\s\W_]*(?:q\s*:\s*)?(?:who|whom|whose|what|when|where|why|how|which|is|are|was|were"
    r"|has|have|had|did|do|does|can|could|will|would|should|shall|may|might|must)\b(?!\s*[:*])",
    re.IGNORECASE,
)
_CLAUSE_BREAK_RE = re.compile(
    r";|\s[\u2014\u2013]\s|\s--\s|\s(?:but|while|whereas|because|since|so|yet|although|though|however)\s"
)
_LIST_GAP_RE = re.compile(r"[\s`'\"*()\[\],/&+]*(?:(?:and|or|plus|both|also)[\s`'\"*()\[\],/&+]*)?")
_DESTINATION = frozenset(
    ["to", "into", "with", "by", "in", "at", "via", "use", "using", "see", "onto", "towards"]
)
_FILLER = frozenset(
    [
        "the",
        "a",
        "an",
        "old",
        "empty",
        "obsolete",
        "stale",
        "legacy",
        "former",
        "whole",
        "entire",
        "folder",
        "folders",
        "directory",
        "directories",
        "dir",
        "file",
        "files",
        "script",
        "scripts",
        "and",
        "both",
        "all",
        "these",
        "those",
        "of",
        "unused",
        "dead",
    ]
)
_SUBJECT_PRONOUNS = frozenset(
    ["we", "i", "they", "he", "she", "you", "it", "who", "someone", "claude", "agent"]
)
_WORD_RE = re.compile(r"[A-Za-z']+")


def _blocks(text: str, skip_lines: frozenset[int]) -> list[str]:
    """Runs of consecutive prose lines (a paragraph, a list item, a table row) joined by spaces."""
    blocks: list[list[str]] = []
    previous = -2
    for number, line in prose_lines(text):
        if number in skip_lines:
            continue
        if not line.strip():
            previous = -2
            continue
        if number != previous + 1 or _BLOCK_START_RE.match(line) or not blocks:
            blocks.append([])
        blocks[-1].append(line.strip())
        previous = number
    return [" ".join(block) for block in blocks]


def _sentences(block: str) -> list[str]:
    """Split at sentence ends, but not after e.g., i.e., vs. or etc. (a cue stays with its verb)."""
    sentences: list[str] = []
    for piece in _SENTENCE_RE.split(block):
        if sentences and _ABBREVIATION_END_RE.search(sentences[-1]):
            sentences[-1] = f"{sentences[-1]} {piece}"
        else:
            sentences.append(piece)
    return sentences


def _occurrences(sentence: str) -> list[tuple[str, int, int]]:
    found = []
    for match in _TOKEN_RE.finditer(sentence):
        candidate = _candidate(match.group())
        if candidate:
            found.append((candidate, match.start(), match.end()))
    return found


def _clauses(sentence: str) -> list[tuple[int, int]]:
    """Spans split at ``;``, dashes and contrast words, never inside parentheses or backticks."""
    protected = [False] * len(sentence)
    depth, in_code = 0, False
    for index, char in enumerate(sentence):
        if char == "`":
            in_code = not in_code
        elif not in_code and char == "(":
            depth += 1
        elif not in_code and char == ")":
            depth = max(0, depth - 1)
        protected[index] = in_code or depth > 0
    spans, start = [], 0
    for match in _CLAUSE_BREAK_RE.finditer(sentence):
        if not protected[match.start()]:
            spans.append((start, match.start()))
            start = match.end()
    spans.append((start, len(sentence)))
    return spans


Occurrence = tuple[str, int, int]  # a mentioned path, and where it starts and ends in the sentence
_RELATIVE_BEFORE_RE = re.compile(r"\b(?:which|that|who)\s*$", re.IGNORECASE)
_RELATIVE_START_RE = re.compile(r"[\s`'\"*)\]]*(?:that|which|who)\b", re.IGNORECASE)
_REVISION_BEFORE_RE = re.compile(
    r"(?:(?:[0-9a-f]{4,40}|HEAD|\w+)(?:[~^]\d*)+|[0-9a-f]{4,40}|HEAD):$"
)
MAX_SUBJECT_WORDS = 6  # a passive verb reaches back this far for its subject path
MAX_RELATIVE_WORDS = 12  # ... or this far when a "that ..." clause hangs off the path directly


def _subjects_before(sentence: str, occ: list[Occurrence], limit: int) -> list[Occurrence]:
    """The paths a passive verb at ``limit`` is about: its subject and the subject's list mates.

    The subject is the nearest path before the verb, at most ``MAX_SUBJECT_WORDS`` words back
    (more when a "that ..." clause hangs directly off the path, with no comma in between). After
    "which" or "that" the subject is the word before it, so the path must stand right there:
    "``a.py`` calls the shim, which was removed" is not about ``a.py``.
    """
    before = [o for o in occ if o[2] <= limit]
    relative = _RELATIVE_BEFORE_RE.search(sentence[:limit])
    anchor = relative.start() if relative else limit
    before = [o for o in before if o[2] <= anchor]
    if not before:
        return []
    gap = sentence[before[-1][2] : anchor]
    if relative:
        if re.search(r"\w", gap):
            return []
    else:
        words = gap.split()
        hanging = (
            _RELATIVE_START_RE.match(gap)
            and "," not in gap
            and ";" not in gap
            and len(words) <= MAX_RELATIVE_WORDS
        )
        if len(words) > MAX_SUBJECT_WORDS and not hanging:
            return []
    found: list[Occurrence] = []
    following = anchor
    for name, start, end in reversed(before):
        if found and not _LIST_GAP_RE.fullmatch(sentence[end:following]):
            break
        previous = _WORD_RE.findall(sentence[:start])[-1:]
        if not (previous and previous[0].lower() in _DESTINATION):
            found.append((name, start, end))
        following = start
    return found


def _objects_after(sentence: str, occ: list[Occurrence], limit: int) -> list[Occurrence]:
    """The object list of an active verb: paths right after it, past only filler words."""
    found: list[Occurrence] = []
    previous = limit
    for name, start, end in (o for o in occ if o[1] >= limit):
        gap = sentence[previous:start]
        if found:
            if not _LIST_GAP_RE.fullmatch(gap):
                break
        else:
            gap_words = [w.lower() for w in _WORD_RE.findall(gap)]
            if len(gap_words) > 3 or any(w not in _FILLER for w in gap_words):
                break
        found.append((name, start, end))
        previous = end
    return found


_CODE_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]+$")
_PLACEHOLDER = "x"  # a letter, so a hidden path at the start is not skipped like punctuation


def _prose(sentence: str, occ: list[Occurrence]) -> str:
    """``sentence`` with code spans and file names hidden, so cue words match prose only.

    Hidden text becomes a run of ``x``, which keeps every offset: a clause span in the sentence is
    the same span here. A cue word inside a code span or a file name ("``TODO.md``",
    "``docs/get-started.md``", "``how-to.md``") is part of a name, not of what the sentence says.
    A bare token with a slash but no extension stays visible: "if/when" and "TODO/FIXME" are
    prose, and a bare directory name cannot be told from them.
    """
    hidden = [False] * len(sentence)
    for match in _CODE_SPAN_RE.finditer(sentence):
        hidden[match.start() : match.end()] = [True] * (match.end() - match.start())
    for name, start, end in occ:
        if _EXTENSION_RE.search(name):  # "if/when" and "TODO/FIXME" are words, not paths
            hidden[start:end] = [True] * (end - start)
    return "".join(
        _PLACEHOLDER if cover else char for char, cover in zip(sentence, hidden, strict=True)
    )


def _not_a_record(sentence: str, prose: str) -> bool:
    """A question, or a deletion the sentence says was undone: neither records a deletion."""
    if sentence.strip().rstrip(" \t\"'`*_)]").endswith("?") or _QUESTION_START_RE.match(
        prose.strip()
    ):
        return True
    return bool(_UNDONE_RE.search(prose.lower()))


def _covered(sentence: str, occ: list[Occurrence]) -> set[int]:
    """The start offsets of the paths that ``sentence`` says, in the past tense, were deleted.

    Only a passive verb ("was deleted", "is gone", "no longer exists") covers its subject, and only
    an active one ("deleted X", "moved X") covers its object; a path that does the moving or
    retiring is not covered.
    """
    covered: set[int] = set()
    prose = _prose(sentence, occ)
    if _not_a_record(sentence, prose):
        return covered
    for start, end in _clauses(sentence):
        clause = sentence[start:end].lower()
        if _CUE_RE.search(_NO_LONGER_RE.sub(" ", prose[start:end].lower())):
            continue
        here = [o for o in occ if o[1] >= start and o[2] <= end]
        taken: list[tuple[int, int]] = []
        for pattern in (_PASSIVE_RE, _GONE_RE):
            for match in pattern.finditer(clause):
                taken.append(match.span())
                covered.update(
                    o[1] for o in _subjects_before(sentence, here, start + match.start())
                )
        for match in _ACTIVE_RE.finditer(clause):
            if any(a <= match.start() < b for a, b in taken):
                continue
            word_before = _WORD_RE.findall(clause[: match.start()])[-1:]
            if word_before and word_before[0] in ("is", "are", "be", "being", "to"):
                continue
            covered.update(o[1] for o in _objects_after(sentence, here, start + match.end()))
    return covered


def historical_mentions(text: str, skip_lines: frozenset[int] = frozenset()) -> frozenset[str]:
    """Mentions that the note itself says, in the past tense, were deleted, moved or retired.

    A mention counts only if every sentence that names it states that this path was deleted,
    removed, moved, renamed or retired, or that it is gone or no longer exists. A statement that
    is negated, conditional, future or modal ("not deleted", "if removed", "will be removed")
    does not count, and neither does a path the sentence only mentions beside the deleted one
    ("... was deleted; use ``new/b.py``"). One plain pointer to the path keeps it reportable.
    """
    every: dict[str, bool] = {}
    for block in _blocks(text, skip_lines):
        if len(block) > MAX_BLOCK:  # too long to read cheaply: its mentions stay reportable
            for name, _, _ in _occurrences(block):
                every[name] = False
            continue
        for sentence in _sentences(block):
            occ = _occurrences(sentence)
            if not occ:
                continue
            covered = _covered(sentence, occ)
            named = {name for name, start, _ in occ if start in covered}
            for name, start, _ in occ:
                # A revision reference ("git show abc123~1:path") beside the deleted path is part of
                # recovering it, not a pointer to a live file.
                history = bool(_REVISION_BEFORE_RE.search(sentence[:start])) and name in named
                every[name] = every.get(name, True) and (start in covered or history)
    return frozenset(name for name, ok in every.items() if ok)


def _nearest_existing_dir(path: Path) -> Path | None:
    """The directory that holds ``path``, or the nearest ancestor that exists.

    Only a cleanly missing path is skipped. Any other access error propagates, so ``baseline``
    leaves the mention unchecked (``Path.is_dir`` would read a permission error as "missing" on
    Python 3.14).
    """
    for candidate in (path, *path.parents):
        try:
            info = candidate.stat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        return candidate if stat.S_ISDIR(info.st_mode) else candidate.parent
    return None


def _iso(when: float) -> str:
    return dt.datetime.fromtimestamp(when, tz=dt.UTC).isoformat()


_KNOWN_TOPS: set[Path] = set()  # repository roots git has already named in this process


def _known_top(anchor: Path) -> Path | None:
    """A repository root seen before that holds ``anchor``, with no other repository between.

    A directory with a ``.git`` entry is a repository root, so when the walk up from ``anchor``
    reaches a known root first, git would name that root and need not be asked again.
    """
    try:
        here = anchor.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    for ancestor in (here, *here.parents):
        has_git = (ancestor / ".git").exists()
        if ancestor in _KNOWN_TOPS:
            return ancestor if has_git else None
        if has_git:
            return None  # a repository root not seen yet (or a nested one): let git say
    return None


@functools.lru_cache(maxsize=256)
def _toplevel(anchor: str) -> Path | None:
    """The repository root above ``anchor``; memoised, and asked of git once per repository."""
    start = Path(anchor)
    top = _known_top(start)
    if top is None:
        top = gitio.toplevel(start)
        if top is not None:
            _KNOWN_TOPS.add(top)
    return top


def _locate(path: Path) -> tuple[Path, str] | None:
    """``(repository root, path relative to it)`` when git can be asked about ``path``."""
    anchor = _nearest_existing_dir(path)
    top = _toplevel(str(anchor)) if anchor is not None else None
    if top is None:
        return None
    try:
        rel = path.resolve().relative_to(top).as_posix()
    except ValueError:
        return None
    return (top, rel) if rel and rel != "." and "\n" not in rel else None


def _mtime_record(path: Path, when: float) -> InferredRecord | None:
    if path.is_file():
        return InferredRecord("file", "mtime", None, when)
    if path.is_dir():
        return InferredRecord("dir", "mtime", None, when)
    return None


_UNCHECKABLE = (OSError, RuntimeError, ValueError, OverflowError, gitio.GitError)


def _has_merges(top: Path, known: dict[Path, bool] | None) -> bool:
    if known is None:
        return gitio.has_merges(top)
    if top not in known:
        known[top] = gitio.has_merges(top)
    return known[top]


def _git_records(
    top: Path,
    entries: list[tuple[str, Path, str]],
    when: float,
    deadline: float | None,
    merges: dict[Path, bool] | None,
) -> tuple[dict[str, InferredRecord | None], bool]:
    """The records of the mentions in one repository, and whether all of them were reached.

    A mention is in the result when it was looked at, with None when there is nothing to track;
    one that was not reached, or whose lookup failed, is not.

    A path's baseline is what it held at the last commit at or before ``when`` that touched it;
    the blobs and trees are read together, at their own commits. A path no such commit touched, or
    that the commit deleted, has nothing in git and falls back to modification times, which find
    nothing for a file that is gone.

    Where no merge commit is reachable, one ``git log`` finds that commit for every path
    (``gitio.last_touches``). Where one is, it is found path by path (``gitio.last_touch``), the
    only way to get the per-path answer on a history with merges; the deadline is then checked
    before each path after the first.
    """
    reached = len(entries)
    try:
        iso = _iso(when)
    except _UNCHECKABLE:
        # A note time the calendar cannot hold gives git nothing to look up, now or later: the
        # mentions are untrackable, and a pass that is done must not be reported as cut short.
        return {mention: None for mention, _, _ in entries}, True
    try:
        rels = [rel for _, _, rel in entries]
        if _has_merges(top, merges):
            touches: dict[str, str] = {}
            looked: set[str] = set()
            for index, rel in enumerate(rels):
                if index and deadline is not None and time.monotonic() > deadline:
                    reached = index
                    break
                if rel not in looked:
                    looked.add(rel)
                    commit = gitio.last_touch(top, iso, rel)
                    if commit:
                        touches[rel] = commit
            entries = entries[:reached]
        else:
            touches = gitio.last_touches(top, iso, rels)
        asked = [(touches[rel], rel) for _, _, rel in entries if rel in touches]
        objects = iter(gitio.objects_at(top, asked))
    except _UNCHECKABLE:
        return {}, False  # a failure of git: nothing is looked at, so the next scan tries again
    records: dict[str, InferredRecord | None] = {}
    for mention, path, rel in entries:
        try:
            obj = next(objects) if rel in touches else None
            if obj is not None and obj[0] == "blob":
                digest = file_digest(obj[1].decode("utf-8", "replace"))
                records[mention] = InferredRecord("file", "git", digest, when)
            elif obj is not None and obj[0] == "tree":
                records[mention] = InferredRecord("dir", "git", None, when)
            else:
                records[mention] = _mtime_record(path, when)
        except _UNCHECKABLE:
            records[mention] = None
    return records, reached == len(rels)


def baselines(
    items: list[tuple[str, Path]],
    when: float,
    deadline: float | None = None,
    merges: dict[Path, bool] | None = None,
    unrecorded: set[str] | None = None,
) -> tuple[dict[str, InferredRecord], bool]:
    """The records for the mentions of one note, and whether every mention was reached.

    ``items`` pairs each mention with its path.

    Every mention gets the note's time ``when``. In a repository without merge commits the
    mentions are read from git together (one log for all the paths, then one read of every blob),
    so a note with many mentions costs a few git calls, not a few per mention; in one with merges
    each mention costs a log call of its own (``_git_records``). A mention that cannot be checked
    (an access error, a symlink loop, a note time outside the calendar, a path that is nowhere,
    one deleted at its last commit before ``when``) has no record.

    ``deadline`` is a ``time.monotonic`` instant, checked before each mention, before each
    repository after the first, and (where a repository has merge commits) before each of its paths
    after the first. The second value is False when it passed before everything was reached: what
    was not reached has no record yet, and a later scan takes it up. ``merges`` remembers, per
    repository, whether it has merge commits, so a scan asks git once. ``unrecorded``, when given,
    receives the mentions that were looked at and have no record, so a caller that goes on after a
    cut pass need not look at them again; a mention whose lookup failed, or was not reached, is not
    among them.
    """
    results: dict[str, InferredRecord | None] = {}
    by_top: dict[Path, list[tuple[str, Path, str]]] = {}
    reached = 0
    for mention, path in items:
        if deadline is not None and time.monotonic() > deadline:
            break
        reached += 1
        try:
            located = _locate(path)
            if located is None:
                results[mention] = _mtime_record(path, when)
            else:
                by_top.setdefault(located[0], []).append((mention, path, located[1]))
        except _UNCHECKABLE:
            results[mention] = None
    skipped = False
    for index, (top, entries) in enumerate(by_top.items()):
        # The first repository is always read, so a scan that is nearly out of time still makes
        # progress; each later one waits for the deadline.
        if index and deadline is not None and time.monotonic() > deadline:
            skipped = True  # the repositories not reached have no records yet
            break
        found, whole = _git_records(top, entries, when, deadline, merges)
        results.update(found)
        skipped = skipped or not whole
    records = {m: r for m, _ in items if (r := results.get(m)) is not None}
    if unrecorded is not None:
        unrecorded.update(m for m, r in results.items() if r is None)
    return records, reached == len(items) and not skipped


def baseline(path: Path, when: float) -> InferredRecord | None:
    """The record to compare later scans against, or None if ``path`` cannot be checked.

    An access error (an unreadable directory), a symlink loop or a note time outside the
    calendar means the mention is not checked at all, so one odd path cannot abort a scan.
    """
    records, _ = baselines([(str(path), path)], when)
    return records.get(str(path))


def state(record: InferredRecord, path: Path, when: float) -> str:
    """``GONE`` only when ``path`` is cleanly absent; an access error is never a finding.

    ``when`` is the note's current time; a record keeps the time its own baseline was taken and
    uses that, so re-saving the note does not move the baseline forward.
    """
    when = when if record.when is None else record.when
    try:
        info = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return GONE
    except OSError:
        return FRESH
    if record.kind == "dir":
        return FRESH
    if record.method == "git":
        if record.baseline is None:
            return CHANGED
        current = current_digest(path)
        if current is None and path.is_file():
            return FRESH  # present but unreadable: cannot tell
        return FRESH if current == record.baseline else CHANGED
    return CHANGED if info.st_mtime > when else FRESH
