"""Frontmatter splitting and a deliberately small YAML subset reader.

freshlint has no runtime dependencies, so it does not ship a YAML parser. It reads
only the shapes that freshness metadata uses in practice:

* ``key: scalar`` (plain, single- or double-quoted, with ``# comments``)
* ``key: { a: b, c: d }`` flow mappings
* ``key: [a, b]`` flow sequences (items may be flow mappings)
* ``key:`` followed by an indented block sequence (``- item``) whose items are
  scalars, flow mappings, or block mappings
* ``key:`` followed by an indented block mapping

Anything else is ignored rather than rejected: freshlint only needs a handful of
keys, and must never refuse a document because of unrelated frontmatter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Node:
    value: Any
    line: int


def split_frontmatter(lines: list[str]) -> tuple[list[tuple[int, str]], int]:
    """Return ``([(line_number, text), ...], body_start_index)``.

    Frontmatter must open with ``---`` on the first line and close with ``---`` or
    ``...``. An unclosed block is treated as body text.
    """
    if not lines or lines[0].rstrip() != "---":
        return [], 0
    for index in range(1, len(lines)):
        if lines[index].rstrip() in ("---", "..."):
            return [(n + 1, lines[n]) for n in range(1, index)], index + 1
    return [], 0


def _strip_comment(text: str) -> str:
    quote = None
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index].rstrip()
    return text.strip()


def _scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote = None
    current: list[str] = []
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if "".join(current).strip():
        parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _split_key(text: str) -> tuple[str, str] | None:
    """Split ``key: value`` on the first colon followed by whitespace or end."""
    quote = None
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == ":" and (index + 1 == len(text) or text[index + 1] in " \t"):
            return _scalar(text[:index]), text[index + 1 :].strip()
    return None


def _inline(text: str) -> Any:
    text = _strip_comment(text)
    if text.startswith("{") and text.endswith("}"):
        mapping: dict[str, Any] = {}
        for part in _split_top_level(text[1:-1]):
            pair = _split_key(part)
            if pair:
                mapping[pair[0]] = _inline(pair[1])
        return mapping
    if text.startswith("[") and text.endswith("]"):
        return [_inline(part) for part in _split_top_level(text[1:-1])]
    return _scalar(text)


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip(" "))


def _block(entries: list[tuple[int, str]]) -> Any:
    """Parse an indented block (already known to belong to one key)."""
    meaningful = [(n, t) for n, t in entries if t.strip() and not t.strip().startswith("#")]
    if not meaningful:
        return ""
    if meaningful[0][1].lstrip().startswith("-"):
        items: list[Any] = []
        base = _indent(meaningful[0][1])
        current: list[str] | None = None
        for _, text in meaningful:
            stripped = text.lstrip()
            if _indent(text) == base and stripped.startswith("-"):
                if current is not None:
                    items.append(_finish_item(current))
                current = [stripped[1:].strip()]
            elif current is not None:
                current.append(stripped)
        if current is not None:
            items.append(_finish_item(current))
        return items
    mapping: dict[str, Any] = {}
    for _, text in meaningful:
        pair = _split_key(text.strip())
        if pair:
            mapping[pair[0]] = _inline(pair[1])
    return mapping


def _finish_item(parts: list[str]) -> Any:
    first = parts[0]
    if len(parts) == 1 and (_split_key(first) is None or first.startswith(("{", "["))):
        return _inline(first)
    mapping: dict[str, Any] = {}
    for part in parts:
        pair = _split_key(part)
        if pair:
            mapping[pair[0]] = _inline(pair[1])
    return mapping


def parse_frontmatter(entries: list[tuple[int, str]]) -> dict[str, Node]:
    """Parse frontmatter lines into ``{key: Node(value, line)}``."""
    result: dict[str, Node] = {}
    index = 0
    while index < len(entries):
        line_number, text = entries[index]
        index += 1
        if not text.strip() or text.startswith((" ", "\t", "#", "-")):
            continue
        pair = _split_key(text)
        if pair is None:
            continue
        key, rest = pair
        if _strip_comment(rest):
            result[key] = Node(_inline(rest), line_number)
            continue
        block: list[tuple[int, str]] = []
        while index < len(entries) and (
            not entries[index][1].strip() or entries[index][1].startswith((" ", "\t", "-"))
        ):
            block.append(entries[index])
            index += 1
        result[key] = Node(_block(block), line_number)
    return result
