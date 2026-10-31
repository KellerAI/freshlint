"""Bounded syntactic coverage: what a note's citations account for.

A parsed citation used to retire every inferred finding of its note and to switch the
uncited-claim check off for the whole note, so one unrelated citation could silence the rest.
A citation now covers only what it names:

* A mentioned file is *covered* when it resolves to one of the note's *cited files*: the files
  of all its parsed citations, whatever their kind (file, lines, symbol or key). A mention whose
  covering citation has a fingerprint (the agent read it and vouched for it) is tracked by that
  citation alone, and its inferred record is dropped. Until then a mention that already has a
  record keeps it: adding an unread citation and removing it again must not launder a stale
  mention. Every other mention keeps its inferred tracking and its baseline, whatever else the
  note cites.
* A claim sentence is *covered* when it names a cited file or a cited name (the part after
  ``#``), as an inline code span or a path-like token; or when a parsed Evidence line is in its
  paragraph, or is the first non-blank line after that paragraph. A path-like token has a ``/``,
  a ``.`` (an extension, or a dot file) or a ``:line`` suffix: a bare word (``src``, ``docs``,
  ``test``) that happens to resolve to a cited path names nothing. A paragraph is a maximal run
  of non-blank lines outside code fences. A heading ends a paragraph and every list item is a
  paragraph of its own.

Coverage is syntactic. It never asserts that the cited code supports the claim; it only checks
that the note put a citation where a reader looks for one. An Evidence line without a parsed
citation (empty, a bare path, a URL) covers nothing.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from freshlint.claims import DEFAULT_HEDGES, DEFAULT_PHRASES, ClaimHit, find_claims
from freshlint.evidence import EvidenceBlock, resolve
from freshlint.markdown import HEADING_RE, prose_lines
from freshlint.store import NoteRecord

_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_TOKEN_RE = re.compile(r"[^\s`'\"()\[\]{}<>,;]+")
_LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?(?::\d+)?$")  # "src/a.py:42", ":10-20", ":10-20:5"
_TRAILING = ".:!?*"  # sentence punctuation and a closing bold marker after a token


def _path_like(word: str) -> bool:
    """Whether a prose token reads as a path: it has a ``/``, a ``.`` or a ``:line`` suffix."""
    return "/" in word or "." in word or _LINE_SUFFIX_RE.search(word) is not None


def _key(path: Path) -> str:
    try:
        return str(path.resolve())
    except (OSError, RuntimeError, ValueError):  # a symlink loop, an embedded NUL
        return os.path.normpath(path)


def cited_files(
    block: EvidenceBlock,
    project_root: Path,
    home: Path,
    only: frozenset[str] | None = None,
) -> frozenset[str]:
    """The resolved paths of every parsed citation, of any kind (of those in ``only``, if given)."""
    return frozenset(
        _key(resolve(c.path, project_root, home))
        for c in block.citations
        if only is None or c.raw in only
    )


def vouched(record: NoteRecord) -> frozenset[str]:
    """The citations of a note that have a fingerprint: read, and vouched for when saved."""
    return frozenset(raw for raw, cit in record.citations.items() if cit.fingerprint is not None)


def cited_names(block: EvidenceBlock) -> frozenset[str]:
    """The symbol and key names after ``#`` in the parsed citations."""
    return frozenset(c.name for c in block.citations if c.name)


def covers(cited: frozenset[str], path: str, project_root: Path, home: Path) -> bool:
    """Whether ``path`` (as the note writes it) resolves to one of the ``cited`` files."""
    return _key(resolve(path, project_root, home)) in cited


def tracked_mentions(
    mentions: list[str], block: EvidenceBlock, record: NoteRecord, project_root: Path, home: Path
) -> list[str]:
    """The mentions inference tracks: those no vouched citation covers.

    A mention only an unread citation names is still tracked if it has an inferred record, so
    that record outlives the citation's arrival and removal. One with no record is left to its
    citation: there is no stale state to keep, and the citation reports it.
    """
    if not block.citations:
        return list(mentions)
    cited = cited_files(block, project_root, home)
    sure = cited_files(block, project_root, home, only=vouched(record))
    tracked = []
    for mention in mentions:
        key = _key(resolve(mention, project_root, home))
        if key not in sure and (key not in cited or mention in record.inferred):
            tracked.append(mention)
    return tracked


def paragraph_spans(text: str) -> list[tuple[int, int]]:
    """The first and last line number of every paragraph outside frontmatter and code fences."""
    spans: list[list[int]] = []
    open_span, previous = False, 0
    for number, line in prose_lines(text):
        if not line.strip():
            open_span = False
        elif HEADING_RE.match(line):
            spans.append([number, number])  # a heading is a paragraph of its own
            open_span = False
        elif not open_span or number != previous + 1 or _LIST_ITEM_RE.match(line):
            spans.append([number, number])  # after a gap (a code fence) or at a list item
            open_span = True
        else:
            spans[-1][1] = number
        previous = number
    return [(first, last) for first, last in spans]


def _beside_evidence(
    line: int, spans: list[tuple[int, int]], lines: list[str], block: EvidenceBlock
) -> bool:
    """Whether a parsed Evidence line is in the paragraph of ``line`` or right after it."""
    span = next((s for s in spans if s[0] <= line <= s[1]), None)
    if span is None:
        return False
    if any(span[0] <= number <= span[1] for number in block.cited_lines):
        return True
    after = span[1]  # the index of the line below the paragraph
    while after < len(lines) and not lines[after].strip():
        after += 1
    return after < len(lines) and after + 1 in block.cited_lines


def _names_cited(
    sentence: str, cited: frozenset[str], names: frozenset[str], project_root: Path, home: Path
) -> bool:
    """Whether ``sentence`` names a cited file or a cited name."""
    spans = [m.group(1).strip() for m in _CODE_SPAN_RE.finditer(sentence)]
    if any(span.removesuffix("()") in names for span in spans):
        return True
    # Only a bold marker is stripped from the left: a leading dot is part of the path
    # (``./a.py``, ``../a.py``, ``.claude/settings.json``). A word in prose names a path only when
    # it looks like one: "src" or "test" next to a cited ``src/`` or ``test`` is just a word.
    prose = _TOKEN_RE.findall(_CODE_SPAN_RE.sub(" ", sentence))
    words = [word.lstrip("*").rstrip(_TRAILING) for word in prose]
    words = [word for word in words if _path_like(word)]
    for span in spans:
        words.append(span)
        words.extend(span.split())  # "pytest tests/test_a.py" names the file in it
    for word in words:
        path = _LINE_SUFFIX_RE.sub("", word.partition("#")[0])
        if path and covers(cited, path, project_root, home):
            return True
    return False


def uncovered_claims(
    text: str,
    block: EvidenceBlock,
    project_root: Path,
    home: Path,
    phrases: tuple[str, ...] = DEFAULT_PHRASES,
    hedges: tuple[str, ...] = DEFAULT_HEDGES,
) -> list[ClaimHit]:
    """The certainty claims of ``text`` that no citation covers, whatever else the note cites.

    This is the claim check's entry point. ``find_claims`` decides which sentences are claims;
    coverage decides which of them the note's citations already account for.
    """
    hits = find_claims(text, block.lines, phrases, hedges)
    if not hits or not block.citations:
        return hits
    cited = cited_files(block, project_root, home)
    names = cited_names(block)
    spans = paragraph_spans(text)
    lines = text.splitlines()
    return [
        hit
        for hit in hits
        if not _beside_evidence(hit.line, spans, lines, block)
        and not _names_cited(hit.sentence, cited, names, project_root, home)
    ]
