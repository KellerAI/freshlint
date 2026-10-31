"""Markdown structure: sections, markers, and which claim governs which line.

Section model
-------------
* A section is an ATX heading (``#`` to ``######``) plus every line up to the next
  heading of the same or a higher level, so subsections sit inside their parents.
  Headings inside fenced code blocks are ignored. Setext headings are not sections.
* A section marker is the first non-blank line after its heading. A marker anywhere
  else is reported as misplaced. A marker on the first non-blank body line before any
  heading is a file-level claim (for files that cannot carry frontmatter).
* Every content line is governed by its nearest enclosing section that carries a
  marker, or by the file-level claim when there is none. A marked subsection is
  governed only by its own marker; unmarked subsections belong to their parent.
* Sections are identified by GitHub-style slugs; duplicate titles get ``-1``, ``-2``.

Content used for comparisons excludes frontmatter, marker lines and freshlint
suppression comments. With ``whitespace="ignore"`` (the default) trailing whitespace
and blank lines are ignored too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property

from freshlint.frontmatter import Node, parse_frontmatter, split_frontmatter
from freshlint.markers import Freshness, freshness_from_frontmatter, is_marker_line, parse_marker

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
SUPPRESSION_COMMENT_RE = re.compile(r"[ \t]*<!--\s*freshlint:.*?-->")
FILE_KEY = ""


@dataclass
class Section:
    level: int
    title: str
    slug: str
    line: int
    end_line: int
    parent: int | None
    marker: Freshness | None = None
    marker_invalid: bool = False


@dataclass(frozen=True)
class Claim:
    key: str  # "" for the file-level claim, otherwise the section slug
    title: str | None
    freshness: Freshness
    heading_line: int | None


@dataclass
class Document:
    lines: list[str]
    body_start: int
    frontmatter: dict[str, Node]
    sections: list[Section]
    file_claim: Freshness | None
    file_claim_invalid: bool
    problems: list[tuple[int, str]]
    whitespace: str
    code_lines: set[int] = field(default_factory=set)
    metadata_lines: set[int] = field(default_factory=set)
    owners: dict[int, str] = field(default_factory=dict)

    @property
    def claims(self) -> list[Claim]:
        result = []
        if self.file_claim is not None:
            result.append(Claim(FILE_KEY, None, self.file_claim, None))
        for section in self.sections:
            if section.marker is not None:
                result.append(Claim(section.slug, section.title, section.marker, section.line))
        return result

    @property
    def marked_sections(self) -> dict[str, Section]:
        return {s.slug: s for s in self.sections if s.marker is not None}

    @cached_property
    def _content_lines(self) -> list[tuple[int, str]]:
        return self._compute_content_lines()

    def content_lines(self) -> list[tuple[int, str]]:
        """Body lines that are content (not frontmatter, markers, or pure suppressions)."""
        return list(self._content_lines)

    def _compute_content_lines(self) -> list[tuple[int, str]]:
        result = []
        for index in range(self.body_start, len(self.lines)):
            number = index + 1
            if number in self.metadata_lines:
                continue
            text = _strip_suppressions(self.lines[index])
            if not text.strip() and self.lines[index].strip():
                continue  # the line held only suppression comments
            result.append((number, text))
        return result

    def _normalize(self, items: list[tuple[int, str]]) -> str:
        if self.whitespace == "exact":
            return "\n".join(text for _, text in items)
        return "\n".join(text.rstrip() for _, text in items if text.strip())

    def unit_content(self, key: str) -> str:
        """Normalized content governed by ``key`` ("" = file-level, else a slug)."""
        return self._normalize(
            [(n, t) for n, t in self._content_lines if self.owners.get(n, FILE_KEY) == key]
        )

    @property
    def body_content(self) -> str:
        """Normalized content of the whole body, ignoring all freshness metadata."""
        return self._normalize(self._content_lines)

    def section_for_line(self, line: int) -> Section | None:
        """Innermost section whose extent contains ``line``."""
        containing = [s for s in self.sections if s.line <= line <= s.end_line]
        return max(containing, key=lambda s: s.line) if containing else None


_INLINE_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_CODE_SPAN_RE = re.compile(r"(`[^`]*`)")


def _strip_suppressions(line: str) -> str:
    """Remove suppression comments, leaving inline code spans (examples) intact."""
    parts = _CODE_SPAN_RE.split(line)
    return "".join(
        part if index % 2 else SUPPRESSION_COMMENT_RE.sub("", part)
        for index, part in enumerate(parts)
    )


def slugify(title: str) -> str:
    """GitHub-style anchor slug (without duplicate suffixes)."""
    text = _INLINE_LINK_RE.sub(r"\1", title.strip().lower())
    text = text.replace("`", "").replace("*", "")
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _fence_open(line: str) -> tuple[str, int] | None:
    """``(char, length)`` when ``line`` opens a fenced code block, else ``None``.

    A backtick fence whose info string contains a backtick is inline code, not a fence.
    """
    opened = FENCE_OPEN_RE.match(line)
    if opened is None or (opened.group(1)[0] == "`" and "`" in opened.group(2)):
        return None
    return opened.group(1)[0], len(opened.group(1))


def _fence_closes(line: str, fence: tuple[str, int]) -> bool:
    """Whether ``line`` closes the open ``fence`` (same character, at least as long)."""
    stripped = line.strip()
    return bool(stripped) and set(stripped) == {fence[0]} and len(stripped) >= fence[1]


def parse_document(text: str, whitespace: str = "ignore") -> Document:
    lines = [line.rstrip("\r") for line in text.splitlines()]
    fm_entries, body_start = split_frontmatter(lines)
    frontmatter = parse_frontmatter(fm_entries)
    problems: list[tuple[int, str]] = []
    fm_claim, fm_errors = freshness_from_frontmatter(frontmatter)
    problems.extend(fm_errors)
    file_claim_invalid = bool(fm_errors)

    sections: list[Section] = []
    slug_counts: dict[str, int] = {}
    code_lines: set[int] = set()
    metadata_lines: set[int] = set(range(1, body_start + 1))
    fence: tuple[str, int] | None = None
    awaiting: int | None = -1  # -1 = file-level slot, int = index of section, None = closed
    last_marker_owner: int | None = None  # section index (or -1) whose marker was just seen
    file_marker: Freshness | None = None
    open_stack: list[int] = []

    for index in range(body_start, len(lines)):
        number = index + 1
        line = lines[index]
        if fence is not None:
            code_lines.add(number)
            if _fence_closes(line, fence):
                fence = None
            continue
        opened = _fence_open(line)
        if opened is not None:
            fence = opened
            code_lines.add(number)
            awaiting = None
            last_marker_owner = None
            continue

        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            title = re.sub(r"[ \t]+#+$", "", heading.group(2) or "").strip()
            if set(title) == {"#"}:
                title = ""
            while open_stack and sections[open_stack[-1]].level >= level:
                sections[open_stack.pop()].end_line = number - 1
            base = slugify(title)
            count = slug_counts.get(base, 0)
            slug_counts[base] = count + 1
            slug = base if count == 0 else f"{base}-{count}"
            sections.append(
                Section(
                    level=level,
                    title=title,
                    slug=slug,
                    line=number,
                    end_line=len(lines),
                    parent=open_stack[-1] if open_stack else None,
                )
            )
            open_stack.append(len(sections) - 1)
            awaiting = len(sections) - 1
            last_marker_owner = None
            continue

        if not line.strip():
            continue

        if is_marker_line(line):
            metadata_lines.add(number)
            if awaiting is not None:
                fresh, error = parse_marker(line, number)
                if error:
                    problems.append((number, error))
                    if awaiting == -1:
                        file_claim_invalid = True
                    else:
                        sections[awaiting].marker_invalid = True
                elif awaiting == -1:
                    file_marker = fresh
                else:
                    sections[awaiting].marker = fresh
                last_marker_owner = awaiting
            elif last_marker_owner is not None:
                problems.append((number, "more than one marker for the same section"))
            else:
                problems.append(
                    (
                        number,
                        "misplaced marker: a marker must be the first non-blank line after "
                        "its heading (or of the file body, for a file-level marker)",
                    )
                )
            awaiting = None
            continue

        awaiting = None
        last_marker_owner = None

    for section_index in open_stack:
        sections[section_index].end_line = len(lines)

    file_claim = fm_claim
    if file_marker is not None:
        if fm_claim is not None or fm_errors:
            problems.append(
                (
                    file_marker.line,
                    "two file-level claims (frontmatter and a file-level marker); keep one",
                )
            )
        else:
            file_claim = file_marker

    owners: dict[int, str] = {}

    def governor(section_index: int | None) -> str:
        while section_index is not None:
            if sections[section_index].marker is not None:
                return sections[section_index].slug
            section_index = sections[section_index].parent
        return FILE_KEY

    # Assign each body line to the innermost section containing it, then to its governor.
    stack: list[int] = []
    next_section = 0
    for index in range(body_start, len(lines)):
        number = index + 1
        while next_section < len(sections) and sections[next_section].line == number:
            while stack and sections[stack[-1]].end_line < number:
                stack.pop()
            stack.append(next_section)
            next_section += 1
        while stack and sections[stack[-1]].end_line < number:
            stack.pop()
        owners[number] = governor(stack[-1] if stack else None)

    problems.sort()
    return Document(
        lines=lines,
        body_start=body_start,
        frontmatter=frontmatter,
        sections=sections,
        file_claim=file_claim,
        file_claim_invalid=file_claim_invalid,
        problems=problems,
        whitespace=whitespace,
        code_lines=code_lines,
        metadata_lines=metadata_lines,
        owners=owners,
    )


def prose_lines(text: str) -> list[tuple[int, str]]:
    """``(line_number, text)`` for body lines outside frontmatter and fenced code blocks."""
    lines = text.splitlines()
    _, start = split_frontmatter(lines)
    result: list[tuple[int, str]] = []
    fence: tuple[str, int] | None = None
    for index in range(start, len(lines)):
        line = lines[index]
        if fence is not None:
            if _fence_closes(line, fence):
                fence = None
            continue
        opened = _fence_open(line)
        if opened is not None:
            fence = opened
            continue
        result.append((index + 1, line))
    return result
