"""Render a freshlint Team comparison as markdown for the GitHub job summary.

A pure renderer: it reads the freshlint Team ingest response (the body of a ``201``) on stdin
and writes GitHub-flavoured markdown on stdout. It has no network code and opens no files; the
composite action's upload step appends the output to ``$GITHUB_STEP_SUMMARY``.

The response comes from a server named by the workflow (``team-url``), so every value in it is
treated as hostile:

* the structure is checked field by field, and a response without a usable ``comparison``
  renders nothing (exit 2);
* every string from the response (file paths, rule ids, the branch name, the commit) is
  rendered inside a code span whose backtick fence is longer than any backtick run in it,
  with control, format and line-separator characters replaced and table pipes escaped, so
  it cannot open a link, an image, HTML, a heading, emphasis or another table cell;
* severities and counts are rendered only after they pass an exact check;
* the link to freshlint Team is used only if it is an https URL made of plain URL
  characters (http only for localhost).

Usage::

    python -I -m freshlint.team_summary < response.json >> "$GITHUB_STEP_SUMMARY"

Exit codes: 0 = markdown written, 2 = no usable comparison (nothing written to stdout).
Error messages never echo the input.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from typing import Any

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 50
"""Rows shown per table (new, fixed); the rest are counted in a closing line."""
MAX_SHOWN_CHARS = 300
"""Longest path or rule shown in a table cell; longer values are cut with an ellipsis."""
MAX_TREND = 30
SPARK = "▁▂▃▄▅▆▇█"
MODES = ("full", "diff", "staged")
SEVERITIES = ("error", "warning")
MAX_COUNT = 2**53 - 1

SHA_RE = re.compile(r"[0-9a-f]{40}")
# An http(s) URL with a plain host, no user info, and only RFC 3986 characters after it: no
# whitespace, quotes or angle brackets, which could end a markdown link destination.
URL_RE = re.compile(
    r"(?P<scheme>https?)://(?P<host>[A-Za-z0-9.-]{1,253}|\[::1\])(?::[0-9]{1,5})?"
    r"(?:[/?#][A-Za-z0-9\-._~%!$&'()*+,;=:@/?#]*)?"
)
MAX_URL_CHARS = 2048
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
# Characters that never reach the output: C0/C1 controls, format characters (bidi overrides,
# zero-width characters), line and paragraph separators, surrogates and private use.
_HIDDEN = frozenset(("Cc", "Cf", "Zl", "Zp", "Cs", "Co"))


class SummaryError(ValueError):
    """The response has no comparison that can be rendered."""


@dataclass(frozen=True)
class Item:
    path: str
    rule: str
    severity: str
    count: int


@dataclass(frozen=True)
class Changes:
    count: int
    items: tuple[Item, ...]


@dataclass(frozen=True)
class Counts:
    errors: int
    warnings: int
    files: int


@dataclass(frozen=True)
class Comparison:
    mode: str
    default_branch: str | None
    baseline_sha: str | None
    baseline: Counts | None
    new: Changes
    fixed: Changes
    totals: Counts
    trend: tuple[tuple[int, int], ...]
    url: str | None


# ------------------------------------------------------------------------ parsing


def _count(value: Any, where: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= MAX_COUNT:
        raise SummaryError(f"{where} is not a count")
    return value


def _object(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise SummaryError(f"{where} is not an object")
    return value


def _counts(value: Any, where: str) -> Counts:
    obj = _object(value, where)
    return Counts(
        errors=_count(obj.get("errors"), f"{where}.errors"),
        warnings=_count(obj.get("warnings"), f"{where}.warnings"),
        files=_count(obj.get("files"), f"{where}.files"),
    )


def _changes(value: Any, where: str) -> Changes:
    obj = _object(value, where)
    raw_items = obj.get("items")
    if not isinstance(raw_items, list):
        raise SummaryError(f"{where}.items is not a list")
    items = []
    for index, raw in enumerate(raw_items):
        item = _object(raw, f"{where}.items[{index}]")
        path, rule = item.get("path"), item.get("rule")
        if not isinstance(path, str) or not path or not isinstance(rule, str) or not rule:
            raise SummaryError(f"{where}.items[{index}] has no path or rule")
        severity = item.get("severity")
        if severity not in SEVERITIES:
            raise SummaryError(f"{where}.items[{index}] has no valid severity")
        items.append(Item(path, rule, severity, _count(item.get("count"), "item count", 1)))
    count = _count(obj.get("count"), f"{where}.count")
    if count < sum(item.count for item in items):
        raise SummaryError(f"{where}.count is less than its items")
    return Changes(count=count, items=tuple(items))


def _safe_url(value: Any) -> str | None:
    """The URL if it is https (http only for localhost) and made of plain URL characters."""
    if not isinstance(value, str) or len(value) > MAX_URL_CHARS:
        return None
    match = URL_RE.fullmatch(value)
    if not match:
        return None
    if match["scheme"] == "https" or match["host"].lower() in LOCAL_HOSTS:
        return value
    return None


def parse(response: Any) -> Comparison:
    """The comparison in an ingest response. Raises SummaryError if it is missing or invalid."""
    comparison = _object(_object(response, "response").get("comparison"), "comparison")
    mode = comparison.get("mode")
    if mode not in MODES:
        raise SummaryError("comparison.mode is not a run mode")
    branch = comparison.get("default_branch")
    if branch is not None and (not isinstance(branch, str) or not 0 < len(branch) <= 255):
        raise SummaryError("comparison.default_branch is not a branch name")
    baseline, baseline_sha = None, None
    if comparison.get("baseline") is not None:
        raw = _object(comparison["baseline"], "comparison.baseline")
        sha = raw.get("sha")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise SummaryError("comparison.baseline.sha is not a commit")
        baseline, baseline_sha = _counts(raw, "comparison.baseline"), sha
    trend_raw = comparison.get("trend")
    if not isinstance(trend_raw, list):
        raise SummaryError("comparison.trend is not a list")
    trend = []
    for raw_point in trend_raw[-MAX_TREND:]:
        point = _object(raw_point, "comparison.trend[]")
        trend.append(
            (
                _count(point.get("errors"), "comparison.trend[].errors"),
                _count(point.get("warnings"), "comparison.trend[].warnings"),
            )
        )
    return Comparison(
        mode=mode,
        default_branch=branch,
        baseline_sha=baseline_sha,
        baseline=baseline,
        new=_changes(comparison.get("new"), "comparison.new"),
        fixed=_changes(comparison.get("fixed"), "comparison.fixed"),
        totals=_counts(comparison.get("totals"), "comparison.totals"),
        trend=tuple(trend),
        url=_safe_url(comparison.get("url")),
    )


# ---------------------------------------------------------------------- rendering


def _visible(text: str) -> str:
    """Text with every hidden or line-breaking character replaced by U+FFFD."""
    return "".join("�" if unicodedata.category(char) in _HIDDEN else char for char in text)


def code(text: str, limit: int = MAX_SHOWN_CHARS) -> str:
    """A code span that shows text literally, safe inside a GFM table cell.

    Inside a code span nothing is markdown or HTML. The fence is one backtick longer than the
    longest backtick run in the text, and padded with spaces, so the text cannot close it. The
    text cannot hold a line break (replaced), and its pipes are escaped, which GFM tables
    require even inside code spans.
    """
    shown = _visible(text)
    if len(shown) > limit:
        shown = shown[: limit - 1] + "…"
    shown = shown.replace("|", "\\|")
    longest = max((len(run) for run in re.findall(r"`+", shown)), default=0)
    fence = "`" * (longest + 1)
    return f"{fence} {shown} {fence}"


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def sparkline(values: list[int]) -> str:
    """A text sparkline, scaled from zero to the largest value."""
    top = max(values, default=0)
    if top == 0:
        return SPARK[0] * len(values)
    return "".join(SPARK[round(v / top * (len(SPARK) - 1))] for v in values)


def _branch(c: Comparison) -> str:
    return code(c.default_branch, 255) if c.default_branch else "the default branch"


def _table(title: str, changes: Changes) -> list[str]:
    lines = [f"#### {title} ({changes.count:,})", ""]
    if not changes.items:
        return lines
    lines += ["| File | Rule | Severity | Count |", "| --- | --- | --- | ---: |"]
    shown = changes.items[:MAX_ROWS]
    for item in shown:
        lines.append(
            f"| {code(item.path)} | {code(item.rule)} | {item.severity} | {item.count:,} |"
        )
    rest = changes.count - sum(item.count for item in shown)
    if rest > 0:
        lines += ["", f"…and {_plural(rest, 'more finding')}."]
    lines.append("")
    return lines


def _headline(c: Comparison) -> list[str]:
    branch = _branch(c)
    totals = c.totals
    this_run = (
        f"This run ({c.mode}, {_plural(totals.files, 'file')}): "
        f"{_plural(totals.errors, 'error')}, {_plural(totals.warnings, 'warning')}."
    )
    if c.baseline is None:
        return [
            f"**No full run on {branch} to compare with yet.** {this_run}",
            "",
            f"New and fixed findings appear once a full run on {branch} is uploaded.",
        ]
    base = c.baseline
    against = f"{branch} at {code(c.baseline_sha[:7], 7)}"
    now = (
        f"{branch} now: {_plural(base.errors, 'error')}, {_plural(base.warnings, 'warning')} "
        f"in {_plural(base.files, 'file')}."
    )
    if c.mode == "full":
        return [
            f"**{c.new.count:,} new, {c.fixed.count:,} fixed** vs {against}. {this_run}",
        ]
    return [
        f"**{c.new.count:,} new in this run, {c.fixed.count:,} fixed** vs {against}. {this_run}",
        "",
        f"A {c.mode} run checks only some files, so fixed counts only findings in files this "
        f"run reported. {now}",
    ]


def _trend(c: Comparison) -> list[str]:
    if not c.trend:
        return []
    errors = [p[0] for p in c.trend]
    warnings = [p[1] for p in c.trend]
    runs = _plural(len(c.trend), "full run")
    return [
        f"| Last {runs} on {_branch(c)} | Trend | Latest |",
        "| --- | --- | ---: |",
        f"| Errors | {code(sparkline(errors))} | {errors[-1]:,} |",
        f"| Warnings | {code(sparkline(warnings))} | {warnings[-1]:,} |",
        "",
    ]


def render(c: Comparison) -> str:
    """The markdown for one comparison."""
    lines = ["### Freshlint Team", "", *_headline(c), ""]
    lines += _trend(c)
    if c.baseline is not None:
        lines += _table("New", c.new)
        lines += _table("Fixed", c.fixed)
    if c.url:
        lines += [f"[Open this repository in Freshlint Team](<{c.url}>)", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if argv:
        print(
            "freshlint team_summary: takes no arguments; reads the response on stdin",
            file=sys.stderr,
        )
        return 2
    raw = sys.stdin.buffer.read(MAX_RESPONSE_BYTES + 1)
    try:
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SummaryError("the response is too large")
        try:
            response = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise SummaryError("the response is not JSON") from None
        markdown = render(parse(response))
    except SummaryError as exc:
        print(f"freshlint team_summary: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(markdown.encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
