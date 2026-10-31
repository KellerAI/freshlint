"""Check memory notes against their evidence.

The scan only writes to the in-memory ``Store`` (inference caches and pruning); callers
decide whether to save it. Findings in ``REPORTED`` with high confidence go into the
session report; everything else appears only in the full report. A note that cannot be
read or checked at all becomes a low-confidence ``check-error`` finding and the scan goes on.

Citations cover only what they name (see ``freshlint.coverage``). A note that cites some file
is still checked for the rest: a mentioned file no citation covers keeps its inferred
baseline and findings, and a claim no citation covers is still an uncited claim.
"""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from freshlint import coverage, gitio, infer
from freshlint.claims import DEFAULT_HEDGES, DEFAULT_PHRASES, applies_to
from freshlint.evidence import (
    EvidenceBlock,
    misplaced_evidence_lines,
    parse_evidence,
    resolve,
)
from freshlint.fingerprint import CHANGED, GONE, compare, file_digest, is_missing, level_note
from freshlint.memory import Note, note_paths, read_note
from freshlint.store import InferredRecord, NoteRecord, Store

REPORTED = ("gone", "changed", "unread")  # uncited claims are raised when a note is saved
MAX_MENTIONS = 50


@dataclass(frozen=True)
class Context:
    project_root: Path
    home: Path
    phrases: tuple[str, ...] = DEFAULT_PHRASES
    hedges: tuple[str, ...] = DEFAULT_HEDGES


@dataclass(frozen=True)
class NoteFinding:
    note: str
    state: str
    target: str
    message: str
    confidence: str = "high"

    @property
    def reported(self) -> bool:
        return self.state in REPORTED and self.confidence == "high"


@dataclass
class Budget:
    """What a scan carries from note to note: its time limit and what it has learned.

    ``deadline`` is a ``time.monotonic`` instant, or None for no limit. The scan checks it between
    notes, and inference checks it before every mention it baselines, so one note with many new
    mentions cannot run past it. ``cut`` says inference stopped early. ``merges`` remembers, per
    repository, whether it has merge commits, which decides how baselines are read.
    """

    deadline: float | None = None
    cut: bool = False
    merges: dict[Path, bool] = field(default_factory=dict)

    def spent(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline


@dataclass
class ScanResult:
    notes: int
    citing: int
    findings: list[NoteFinding] = field(default_factory=list)
    partial: bool = False


def _cannot_open(path: Path) -> bool:
    """Whether ``path`` exists but cannot be opened (a permission or I/O error)."""
    try:
        with path.open("rb"):
            return False
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True


def _mentions_key(mentions: list[str]) -> str:
    return file_digest("\n".join(sorted(mentions)))


def _tracked_mentions(
    text: str, block: EvidenceBlock, record: NoteRecord, ctx: Context
) -> list[str]:
    """The mentions of a note that inference tracks: those no vouched citation covers.

    The cap comes first, so that adding or removing a citation never changes which mentions a
    long note tracks.
    """
    mentions = infer.mentions(text, block.lines)[:MAX_MENTIONS]
    return coverage.tracked_mentions(mentions, block, record, ctx.project_root, ctx.home)


def _inferred(
    note: Note,
    block: EvidenceBlock,
    record: NoteRecord,
    ctx: Context,
    ignored: frozenset[str],
    tracked: list[str] | None,
    budget: Budget | None,
) -> dict[str, InferredRecord]:
    """The inferred baselines of ``note``: one per tracked mention, kept until it leaves.

    A baseline is taken once, for a new mention, at the note's time then. Saving the note again
    (which stamps a newer time) keeps the baselines of the mentions that stay, so only removing
    the mention, or citing the file in an Evidence line that the agent read, clears a finding. A
    citation for another file changes nothing: a mention it does not cover keeps its baseline, and
    so does one covered only by a citation that has no fingerprint yet. A mention that git ignores
    (a build output, a cache) is not tracked: it is regenerated, not deleted.

    The new mentions are baselined together (``infer.baselines``). If the scan's time runs out
    first, the baselines taken so far are kept and the key is cleared, so the next scan takes up
    the rest instead of starting over. A mention with nothing to track (it never existed, or its
    last commit before the note deleted it) has no baseline to keep, so the cut pass notes it in
    ``no_record``, under the key of this set of mentions; the next pass of the same set skips it.
    """
    mentions = tracked if tracked is not None else _tracked_mentions(note.text, block, record, ctx)
    key = _mentions_key(mentions)
    if record.inferred_key != key:
        kept = {mention: rec for mention, rec in record.inferred.items() if mention in mentions}
        looked = set(record.no_record) if record.no_record_key == key else set()
        fresh = [
            (mention, resolve(mention, ctx.project_root, ctx.home))
            for mention in mentions
            if mention not in kept and mention not in ignored and mention not in looked
        ]
        unrecorded: set[str] = set()
        found, complete = infer.baselines(
            fresh,
            note.written_at,
            budget.deadline if budget is not None else None,
            budget.merges if budget is not None else None,
            unrecorded,
        )
        kept.update(found)
        record.inferred = kept
        # The key says "every tracked mention has its baseline". A cut pass has dropped records
        # of mentions that left, so an older key must not outlive it: a later edit back to that
        # set of mentions would match it and skip the baselines it lost. The same goes for the
        # mentions with no record: they are remembered only for the set they were found for.
        record.inferred_key = key if complete else None
        record.no_record = [] if complete else sorted(looked | unrecorded)
        record.no_record_key = None if complete else key
        if not complete and budget is not None:
            budget.cut = True
    return {mention: rec for mention, rec in record.inferred.items() if mention in mentions}


def _explicit_findings(
    note: Note, block: EvidenceBlock, record: NoteRecord, ctx: Context
) -> list[NoteFinding]:
    """The findings about what the note says outright: its Evidence citations and its claims.

    These need no git and no search, only a comparison with the files the note cites, so a scan
    runs them for every note before it spends anything on inference.
    """
    findings = [
        NoteFinding(note.name, "invalid-citation", "", problem, "low")
        for _, problem in block.problems
    ]
    for cit in block.citations:
        stored = record.citations.get(cit.raw)
        if stored is None or stored.fingerprint is None:
            target = resolve(cit.path, ctx.project_root, ctx.home)
            problem = stored.problem if stored is not None else None
            if is_missing(target):  # as in the save feedback: nothing to read
                message = (
                    f"cites {cit.raw}, but that file does not exist. "
                    "Cite a file that exists, or remove the citation."
                )
            elif stored is None:  # no record at all: written before install, or a first scan
                message = (
                    f"{cit.raw} has no saved fingerprint yet, so freshlint cannot tell whether "
                    "it was read when the memory was saved. Read it and save the memory to "
                    "fingerprint it."
                )
            elif problem == "changed":  # the agent read it, but it changed before the save
                message = (
                    f"cites {cit.raw}, which changed after it was read when the memory was "
                    "saved, so it was never fingerprinted. Read it again and save the memory "
                    "to fingerprint it."
                )
            elif problem is not None and problem.startswith("missing:"):
                message = (
                    f"cites {cit.raw}, but when the memory was saved {problem[8:]}, "
                    "so it was never fingerprinted."
                )
            else:  # the save hook saw the citation and recorded that it was not read
                message = (
                    f"cites {cit.raw}, but it was not read when the memory was saved, "
                    "so it was never fingerprinted."
                )
            findings.append(NoteFinding(note.name, "unread", cit.raw, message))
            continue
        target = resolve(cit.path, ctx.project_root, ctx.home)
        result = compare(cit, stored.fingerprint, target)
        if result == CHANGED:
            if _cannot_open(target):
                message = f"{cit.raw} cannot be read, so it could not be checked."
            else:
                where = level_note(cit, stored.fingerprint, target)
                message = f"{cit.raw} changed after this memory was written{where}."
            findings.append(NoteFinding(note.name, "changed", cit.raw, message))
        elif result == GONE:
            findings.append(NoteFinding(note.name, "gone", cit.raw, f"{cit.raw} no longer exists."))
    if applies_to(note.type):
        if not block.citations:
            misplaced = misplaced_evidence_lines(note.text)
            if misplaced:
                message = (
                    f"line {misplaced[0][0]} mentions evidence but is not an Evidence line, "
                    "so nothing was cited."
                )
                findings.append(NoteFinding(note.name, "misplaced-evidence", "", message, "low"))
        hits = coverage.uncovered_claims(
            note.text, block, ctx.project_root, ctx.home, ctx.phrases, ctx.hedges
        )
        if hits:
            phrase = hits[0].phrase
            message = f'says "{phrase}" but cites no evidence.'
            if block.citations:
                message = (
                    f'says "{phrase}" but cites no evidence for it: the sentence names no '
                    "cited file and no Evidence line sits beside it."
                )
            findings.append(NoteFinding(note.name, "uncited-claim", phrase, message))
    return findings


def _inferred_findings(
    note: Note,
    block: EvidenceBlock,
    record: NoteRecord,
    ctx: Context,
    ignored: frozenset[str],
    tracked: list[str] | None,
    budget: Budget | None,
) -> tuple[list[NoteFinding], dict[str, InferredRecord]]:
    """The findings about the files a note mentions, and the mentions that have a record.

    Taking a new baseline costs git calls and honours the scan's ``budget``; judging a mention
    that already has one is a stat, so it is done whether or not the time is spent.
    """
    findings: list[NoteFinding] = []
    inferred = _inferred(note, block, record, ctx, ignored, tracked, budget)
    history: frozenset[str] | None = None
    for mention, rec in sorted(inferred.items()):
        result = infer.state(rec, resolve(mention, ctx.project_root, ctx.home), note.written_at)
        if result == infer.GONE:
            if history is None:
                history = infer.historical_mentions(note.text, block.lines)
            if mention in history:  # the note itself says it was deleted: nothing stale to report
                continue
            message = f"mentions {mention}, which no longer exists."
            findings.append(NoteFinding(note.name, "gone", mention, message))
        elif result == infer.CHANGED:
            message = (
                f"mentions {mention}, which changed after this memory was written "
                "(file-level estimate)."
            )
            findings.append(NoteFinding(note.name, "inferred-changed", mention, message, "low"))
    return findings, inferred


def check_note(
    note: Note,
    record: NoteRecord,
    ctx: Context,
    ignored: frozenset[str] = frozenset(),
    tracked: list[str] | None = None,
    budget: Budget | None = None,
) -> tuple[list[NoteFinding], bool]:
    """Check one note against its evidence and its mentions.

    Called alone, the note works out the mentions inference tracks and has no time limit. ``scan``
    does the two halves separately instead (see there) and passes what it has already worked out.
    """
    block = parse_evidence(note.text)
    findings = _explicit_findings(note, block, record, ctx)
    more, inferred = _inferred_findings(note, block, record, ctx, ignored, tracked, budget)
    return findings + more, bool(block.citations) or bool(inferred)


@dataclass
class _Checked:
    """A note that has had its explicit checks, waiting for inference."""

    note: Note
    block: EvidenceBlock
    record: NoteRecord


def _tracked_by_note(checked: list[_Checked], ctx: Context, budget: Budget) -> dict[str, list[str]]:
    """The mentions inference tracks in each note, worked out once for the whole scan.

    Once the time is spent the remaining notes have no entry: ``_inferred`` works its own out.
    """
    tracked: dict[str, list[str]] = {}
    for item in checked:
        if budget.spent():
            break
        try:
            tracked[item.note.name] = _tracked_mentions(
                item.note.text, item.block, item.record, ctx
            )
        except Exception:  # one odd note must not stop the scan
            continue
    return tracked


def _ignored_mentions(tracked: dict[str, list[str]], ctx: Context, store: Store) -> frozenset[str]:
    """The new mentions of all notes that git ignores.

    One ``git check-ignore`` call when git can answer for every path; up to 17 when some path
    cannot be answered (see ``gitio.check_ignore``), and the rest then counts as not ignored.
    """
    pending: set[str] = set()
    for name, mentions in tracked.items():
        record = store.notes.get(name, NoteRecord())
        if record.inferred_key != _mentions_key(mentions):
            pending.update(m for m in mentions if m not in record.inferred)
    top = gitio.toplevel(ctx.project_root) if pending else None
    if top is None:
        return frozenset()
    inside: dict[str, str] = {}
    for mention in sorted(pending):
        target = os.path.normpath(resolve(mention, ctx.project_root, ctx.home))
        with contextlib.suppress(ValueError):  # outside the repository: git cannot say
            inside[mention] = Path(target).relative_to(top).as_posix()
    hits = gitio.check_ignore(top, list(inside.values()))
    return frozenset(mention for mention, rel in inside.items() if rel in hits)


def scan(
    memory_dir: Path,
    ctx: Context,
    store: Store,
    *,
    patterns: tuple[str, ...] = ("*.md",),
    deadline: float | None = None,
) -> ScanResult:
    """Check every note: first what each says outright, then what each only mentions.

    The first pass (Evidence citations and claims) needs no git and runs for every note before the
    second starts. The second pass (inferred baselines) spends what is left of the ``deadline``
    and stops, partial, when it runs out; the baselines it took are kept for the next scan.
    Judging a mention that already has a baseline costs a stat, so the second pass does that for
    every note regardless.
    """
    paths = note_paths(memory_dir, patterns)
    names = {path.name for path in paths}
    for name in [n for n in store.notes if n not in names]:
        del store.notes[name]
    git_root = gitio.toplevel(memory_dir) if paths else None
    budget = Budget(deadline)
    result = ScanResult(len(paths), 0)
    findings: dict[str, list[NoteFinding]] = {}
    checked: list[_Checked] = []
    for path in paths:
        if budget.spent():
            result.partial = True
            break
        try:  # one bad note (unreadable, absurd metadata) must not stop the scan
            note = read_note(path, git_root)
            record = store.notes.setdefault(path.name, NoteRecord())
            block = parse_evidence(note.text)
            findings[path.name] = _explicit_findings(note, block, record, ctx)
        except Exception as exc:
            findings[path.name] = [_check_error(path.name, exc)]
            continue
        checked.append(_Checked(note, block, record))
    tracked = _tracked_by_note(checked, ctx, budget)
    ignored = _ignored_mentions(tracked, ctx, store)
    for item in checked:
        name = item.note.name
        try:
            more, inferred = _inferred_findings(
                item.note, item.block, item.record, ctx, ignored, tracked.get(name), budget
            )
        except Exception as exc:
            findings[name].append(_check_error(name, exc))
            inferred = item.record.inferred
        else:
            findings[name].extend(more)
        result.citing += int(bool(item.block.citations) or bool(inferred))
        if budget.cut:  # inference ran out of time inside a note: say so
            result.partial = True
    for path in paths:
        result.findings.extend(findings.get(path.name, []))
    return result


def _check_error(name: str, exc: Exception) -> NoteFinding:
    message = f"could not be checked ({type(exc).__name__})."
    return NoteFinding(name, "check-error", "", message, "low")
