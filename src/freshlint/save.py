"""What happens when an agent saves a memory note: push back, fingerprint, renew.

The vouching rule: a citation is fingerprinted or renewed only if the agent read, edited or
wrote the cited file in this session and the file still matches the hash recorded at that
read. Evidence that still compares fresh is never touched by a re-save, and everything else
keeps its old fingerprint, so stale evidence stays flagged.

A save drops the inferred record of a mention only when a citation that now has a fingerprint
covers that file; it keeps every other record, so neither a citation for something else nor an
unread one clears a baseline.
The uncited-claim check runs per sentence, with or without citations (see ``freshlint.coverage``).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from freshlint import coverage
from freshlint.checks import Context
from freshlint.claims import applies_to
from freshlint.evidence import (
    Citation,
    EvidenceBlock,
    misplaced_evidence_lines,
    parse_evidence,
    resolve,
)
from freshlint.fingerprint import (
    CHANGED,
    FRESH,
    GONE,
    UNREAD,
    Fingerprint,
    TargetMissing,
    cites_directory,
    compare,
    directory_changes,
    directory_files,
    directory_problem,
    is_missing,
    kind_change,
    level_note,
    take,
    unread_summary,
)
from freshlint.ledger import raw_digest
from freshlint.memory import Note
from freshlint.store import CitationRecord, NoteRecord, Store

MAX_FEEDBACK = 5


@dataclass
class SaveOutcome:
    feedback: list[str] = field(default_factory=list)
    renewed: list[str] = field(default_factory=list)


def _keep(old: CitationRecord | None, problem: str) -> CitationRecord:
    """The record of a citation read but not fingerprinted: a fingerprint it already has stays."""
    if old is not None and old.fingerprint is not None:
        return old
    return CitationRecord(None, None, problem)


def _drop_covered(record: NoteRecord, block: EvidenceBlock, ctx: Context) -> None:
    """Drop the inferred records of the mentions a vouched citation covers; keep all the others.

    A citation is vouched when it has a fingerprint (the agent read it). The key that says "every
    mention has its baseline" goes with a dropped record, so a mention uncovered again later is
    baselined again by the next scan.
    """
    if not (block.citations and record.inferred):
        return
    cited = coverage.cited_files(block, ctx.project_root, ctx.home, only=coverage.vouched(record))
    covered = [m for m in record.inferred if coverage.covers(cited, m, ctx.project_root, ctx.home)]
    if covered:
        record.inferred = {m: r for m, r in record.inferred.items() if m not in covered}
        record.inferred_key = None


def _directory_citation(cit: Citation, path: Path) -> bool:
    """Whether ``cit`` cites a directory that is fingerprinted as one; see ``cites_directory``."""
    return cites_directory(cit) and path.is_dir()


def _slash_hint(cit: Citation) -> str:
    return "" if cit.path.endswith("/") else f" Write it as `{cit.path}/` to cite the directory."


def _save_directory(
    note: Note,
    cit: Citation,
    path: Path,
    old: CitationRecord | None,
    state: str | None,
    reads: dict[str, str],
    outcome: SaveOutcome,
    today: dt.date,
) -> CitationRecord:
    """The record of a directory citation: its manifest, once every file in it was read.

    A bare ``dir`` citation is taken as ``dir/`` here, at a save, and only here: the note is
    never rewritten and session start never fingerprints. The read gate compares each file's
    raw byte digest with the read ledger's; the manifest holds normalised file digests, and the
    two are never compared with each other.
    """
    try:
        files = directory_files(path, cit.path)
    except TargetMissing as exc:
        outcome.feedback.append(f"{note.name} cites {cit.raw}, but {exc}.")
        return _keep(old, f"missing:{exc}")
    seen = [reads.get(str(target.resolve())) for _, target in files]
    unread = [rel for (rel, _), digest in zip(files, seen, strict=True) if digest is None]
    if unread:
        if old is None or old.fingerprint is None:
            outcome.feedback.append(
                f"{note.name} cites {cit.raw}, a directory, but you have not read every file "
                f"in it in this session ({unread_summary(unread)}). Read each one with the "
                f"Read tool before citing it, or cite the files you read.{_slash_hint(cit)}"
            )
            return CitationRecord(None, None, UNREAD + "\n".join(sorted(unread)))
        if state == CHANGED:
            todo = (  # a cited file became this directory: nothing left to re-read
                f"Cite a file inside it, or `{cit.path.rstrip('/')}/`."
                if kind_change(cit, old.fingerprint, path) is not None
                else "Re-read it before relying on or updating this memory."
            )
            outcome.feedback.append(
                f"{note.name} rests on {cit.raw}, which changed after this memory was written"
                f"{_named(cit, old.fingerprint, path)}. {todo}"
            )
        return old
    if any(raw_digest(target) != digest for (_, target), digest in zip(files, seen, strict=True)):
        outcome.feedback.append(
            f"{note.name} cites {cit.raw}, and a file in it changed after you read it. "
            "Read it again before citing it."
        )
        return _keep(old, "changed")
    try:
        fingerprint = take(cit, path)
    except TargetMissing as exc:
        outcome.feedback.append(f"{note.name} cites {cit.raw}, but {exc}.")
        return _keep(old, f"missing:{exc}")
    if old is not None and old.fingerprint == fingerprint:
        return old
    if old is not None and old.fingerprint is not None:
        outcome.renewed.append(cit.raw)
    return CitationRecord(fingerprint, today.isoformat())


def _named(cit: Citation, stored: Fingerprint, path: Path) -> str:
    """``: src/x is now a directory`` or `` (a.py edited, b.py added)``, else nothing."""
    reason = kind_change(cit, stored, path)
    if reason is not None:
        return f": {reason}"
    if stored.kind != "dir":
        return ""
    changes = directory_changes(stored, path)
    return f" ({', '.join(changes)})" if changes else ""


def on_save(
    note: Note, store: Store, reads: dict[str, str], ctx: Context, today: dt.date
) -> SaveOutcome:
    record = store.notes.setdefault(note.name, NoteRecord())
    block = parse_evidence(note.text)
    outcome = SaveOutcome([f"{note.name}: {problem}." for _, problem in block.problems])
    kept: dict[str, CitationRecord] = {}
    for cit in block.citations:
        path = resolve(cit.path, ctx.project_root, ctx.home)
        old = record.citations.get(cit.raw)
        seen = reads.get(str(path.resolve()))
        state = None
        if old is not None and old.fingerprint is not None:
            state = compare(cit, old.fingerprint, path)
            if state == FRESH:  # a re-save never moves evidence onto different code
                kept[cit.raw] = old
                continue
        if is_missing(path):  # there is nothing to read: say so instead of asking for a read
            if state == GONE and old is not None:
                kept[cit.raw] = old
                outcome.feedback.append(
                    f"{note.name} rests on {cit.raw}, which no longer exists. "
                    "Rewrite the memory, or remove the citation, before relying on it."
                )
            else:
                kept[cit.raw] = old or CitationRecord(None, None)
                outcome.feedback.append(
                    f"{note.name} cites {cit.raw}, but that file does not exist. "
                    "Cite a file that exists, or remove the citation."
                )
            continue
        if state == CHANGED and not _directory_citation(cit, path):
            reason = kind_change(cit, old.fingerprint, path)
            if reason is not None:  # nothing to re-fingerprint: report it as session start does
                kept[cit.raw] = old
                folder = cit.path.rstrip("/")
                todo = (  # nothing left to re-read: say what to cite instead
                    f"Cite a file inside it, or `{folder}/`."
                    if path.is_dir()
                    else "Re-read it before relying on or updating this memory."
                )
                outcome.feedback.append(
                    f"{note.name} rests on {cit.raw}, which changed after this memory was "
                    f"written: {reason}. {todo}"
                )
                continue
        if _directory_citation(cit, path):
            kept[cit.raw] = _save_directory(note, cit, path, old, state, reads, outcome, today)
            continue
        if path.is_dir():  # a bare `dir`, or a symbol, key or range of one: nothing to fingerprint
            problem = directory_problem(cit)
            outcome.feedback.append(f"{note.name} cites {cit.raw}, but {problem}.")
            kept[cit.raw] = _keep(old, f"missing:{problem}")
            continue
        if seen is None:
            if old is None or old.fingerprint is None:
                outcome.feedback.append(
                    f"{note.name} cites {cit.raw}, which you have not read in this session. "
                    "Read it with the Read tool before citing it, or remove the citation."
                )
                kept[cit.raw] = CitationRecord(None, None)
            else:
                kept[cit.raw] = old
                what = (
                    "no longer exists"
                    if state == GONE
                    else (
                        "changed after this memory was written"
                        f"{level_note(cit, old.fingerprint, path)}"
                    )
                )
                outcome.feedback.append(
                    f"{note.name} rests on {cit.raw}, which {what}. "
                    "Re-read it before relying on or updating this memory."
                )
            continue
        if raw_digest(path) != seen:
            outcome.feedback.append(
                f"{note.name} cites {cit.raw}, which changed after you read it. "
                "Read it again before citing it."
            )
            kept[cit.raw] = _keep(old, "changed")
            continue
        try:
            fingerprint = take(cit, path)
        except TargetMissing as exc:
            outcome.feedback.append(f"{note.name} cites {cit.raw}, but {exc}.")
            kept[cit.raw] = _keep(old, f"missing:{exc}")
            continue
        if old is not None and old.fingerprint == fingerprint:
            kept[cit.raw] = old
            continue
        if old is not None and old.fingerprint is not None:
            outcome.renewed.append(cit.raw)
        kept[cit.raw] = CitationRecord(fingerprint, today.isoformat())
    record.citations = kept
    _drop_covered(record, block, ctx)
    if applies_to(note.type):
        if not block.citations:
            misplaced = misplaced_evidence_lines(note.text)
            if misplaced:
                outcome.feedback.append(
                    f"{note.name}: citations must be on their own line that starts with "
                    "**Evidence:** (for example `**Evidence:** `src/limits.py#retry_after``); "
                    f"line {misplaced[0][0]} was not read as evidence."
                )
        hits = coverage.uncovered_claims(
            note.text, block, ctx.project_root, ctx.home, ctx.phrases, ctx.hedges
        )
        if hits:
            how = (
                'or say when and how it was checked (for example "checked 2026-10-01 by '
                'running `pytest`").'
            )
            if block.citations:
                outcome.feedback.append(
                    f'{note.name} says "{hits[0].phrase}" but cites no evidence for it: the '
                    "sentence names no cited file and no Evidence line sits beside it. Put an "
                    "**Evidence:** line with backticked citations right after its paragraph, "
                    f"name the cited file in the sentence, {how}"
                )
            else:
                outcome.feedback.append(
                    f'{note.name} says "{hits[0].phrase}" but cites no evidence. Add a line '
                    "that starts with **Evidence:** followed by backticked citations (for "
                    "example `**Evidence:** `path/to/file.py#function_name``), " + how
                )
    if len(outcome.feedback) > MAX_FEEDBACK:
        extra = len(outcome.feedback) - MAX_FEEDBACK
        cap = f"(+{extra} more: run /freshlint:memory-check)"
        outcome.feedback = outcome.feedback[:MAX_FEEDBACK] + [cap]
    return outcome
