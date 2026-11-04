"""freshlint.team_summary (FT-10): the pure renderer behind the job summary of a Team upload.

The rendered markdown is parsed back with a CommonMark + GFM-tables parser (markdown-it-py), so
the escaping tests check what GitHub would build from it, not only the text.
"""

import ast
import copy
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

from freshlint import team_summary
from freshlint.team_summary import MAX_ROWS, SummaryError, code, parse, render, sparkline

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = json.loads((ROOT / "tests" / "fixtures" / "team" / "response-201-sample.json").read_text())
MD = MarkdownIt("commonmark", {"html": True}).enable("table")

HOSTILE = [
    "<script>alert(1)</script>.md",
    "<img src=x onerror=alert(1)>.md",
    "](javascript:alert(1))",
    "[click](https://evil.example)",
    "![x](https://evil.example/x.png)",
    "a|b|c.md",
    "a\\|b.md",
    "ends-with-backslash\\",
    "`code` and ``more``.md",
    "```fence.md",
    "line\nbreak # heading.md",
    "cr\rreturn.md",
    "<!-- comment -->.md",
    "**bold** _em_ ~~strike~~.md",
    "&lt;entity&gt; &amp; &#124;.md",
    "@someone #123 https://evil.example",
    "bidi‮evil‬.md",
    "zero​width sep .md",
    "nul\x00and\x1bescape.md",
    "lone\ud800surrogate.md",
    "   leading and trailing spaces   ",
    "`",
    "| --- | --- |",
]


def comparison(**changes):
    data = copy.deepcopy(SAMPLE)
    data["comparison"].update(changes)
    return data


def items(paths, severity="error"):
    return {
        "count": len(paths),
        "items": [
            {"path": p, "rule": "review-expired", "severity": severity, "count": 1} for p in paths
        ],
    }


def run_module(stdin: bytes, *args):
    return subprocess.run(
        [sys.executable, "-I", "-m", "freshlint.team_summary", *args],
        input=stdin,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )


def tokens(markdown):
    """Every token, block and inline, of the parsed markdown."""
    out = []
    for token in MD.parse(markdown):
        out.append(token)
        out.extend(token.children or [])
    return out


def table_rows(markdown):
    """The body rows of every table, as lists of cells; each cell is its inline tokens."""
    rows, row, cell, in_body = [], None, None, False
    for token in MD.parse(markdown):
        if token.type == "tbody_open":
            in_body = True
        elif token.type == "tbody_close":
            in_body = False
        elif in_body and token.type == "tr_open":
            row = []
        elif in_body and token.type == "tr_close":
            rows.append(row)
        elif in_body and token.type == "inline":
            cell = list(token.children or [])
            row.append(cell)
    return rows


def visible(text):
    return "".join(
        "�" if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp", "Cs", "Co") else c for c in text
    )


# ---------------------------------------------------------------------- rendering


def test_sample_renders_the_comparison():
    markdown = render(parse(SAMPLE))
    assert markdown.startswith("### Freshlint Team\n")
    assert "**2 new in this run, 1 fixed** vs ` main ` at ` fedcba9 `." in markdown
    assert "This run (diff, 2 files): 3 errors, 1 warning." in markdown
    assert "only findings in files this run reported" in markdown
    assert "` main ` now: 2 errors, 2 warnings in 3 files." in markdown
    assert "#### New (2)" in markdown and "#### Fixed (1)" in markdown
    rows = table_rows(markdown)
    # trend (errors, warnings), then new (2), then fixed (1)
    assert len(rows) == 5
    assert [[t.content for t in cell] for cell in rows[2]] == [
        ["context/company.md"],
        ["freshness-laundering"],
        ["error"],
        ["1"],
    ]
    links = [t for t in tokens(markdown) if t.type == "link_open"]
    assert [link.attrs["href"] for link in links] == [SAMPLE["comparison"]["url"]]


def test_full_run_headline_has_no_partial_scan_note():
    markdown = render(parse(comparison(mode="full")))
    assert "**2 new, 1 fixed** vs ` main ` at ` fedcba9 `." in markdown
    assert "only findings in files this run reported" not in markdown


def test_full_run_limited_to_paths_reads_like_a_partial_run():
    markdown = render(parse(comparison(mode="full", scope="paths")))
    assert "**2 new in this run, 1 fixed** vs ` main ` at ` fedcba9 `." in markdown
    assert "This run (full, limited to paths, 2 files)" in markdown
    assert "A run limited to some paths checks only some files" in markdown
    assert "` main ` now: 2 errors, 2 warnings in 3 files." in markdown


def test_missing_scope_means_all():
    data = comparison(mode="full")
    del data["comparison"]["scope"]
    assert render(parse(data)) == render(parse(comparison(mode="full", scope="all")))


def test_first_run_without_a_baseline_has_no_tables():
    markdown = render(parse(comparison(baseline=None, new=items([]), fixed=items([]), trend=[])))
    assert "**No full run on ` main ` to compare with yet.**" in markdown
    assert "####" not in markdown
    assert table_rows(markdown) == []


def test_unknown_default_branch_is_named_in_words():
    markdown = render(parse(comparison(default_branch=None)))
    assert "vs the default branch at ` fedcba9 `" in markdown


def test_trend_sparkline():
    assert sparkline([]) == ""
    assert sparkline([0, 0, 0]) == "▁▁▁"
    assert sparkline([0, 1, 2, 3, 4, 5, 6, 7]) == "▁▂▃▄▅▆▇█"
    assert sparkline([5]) == "█"
    markdown = render(parse(comparison(trend=[{"errors": e, "warnings": 0} for e in range(40)])))
    assert "| Last 30 full runs on ` main ` | Trend | Latest |" in markdown
    # The last 30 points (10 to 39), scaled from zero.
    assert f"| Errors | {code(sparkline(list(range(10, 40))))} | 39 |" in markdown
    assert sparkline(list(range(10, 40))).startswith("▃") and sparkline([1, 2]).endswith("█")


def test_tables_are_capped_and_count_the_rest():
    paths = [f"docs/{i:03}.md" for i in range(MAX_ROWS + 7)]
    new = items(paths)
    new["count"] += 3  # the server also cut its list
    markdown = render(parse(comparison(new=new)))
    assert len(table_rows(markdown)) == 2 + MAX_ROWS + 1
    assert "…and 10 more findings." in markdown


def test_long_paths_are_cut():
    markdown = render(parse(comparison(new=items(["d/" + "x" * 5000]))))
    cell = table_rows(markdown)[2][0][0].content
    assert len(cell) == team_summary.MAX_SHOWN_CHARS and cell.endswith("…")


# --------------------------------------------------------------- hostile strings


@pytest.mark.parametrize("path", HOSTILE, ids=range(len(HOSTILE)))
def test_hostile_paths_render_as_literal_text(path):
    markdown = render(parse(comparison(new=items([path]), fixed=items([path], "warning"))))
    for token in tokens(markdown):
        assert token.type not in ("html_inline", "html_block", "image"), token
        if token.type == "link_open":
            assert token.attrs["href"] == SAMPLE["comparison"]["url"]
    headings = [t for t in MD.parse(markdown) if t.type == "heading_open"]
    assert len(headings) == 3  # Freshlint Team, New, Fixed
    rows = table_rows(markdown)
    assert len(rows) == 4  # two trend rows, one new, one fixed
    for row in rows[2:]:
        assert len(row) == 4, row
        path_cell = row[0]
        assert [t.type for t in path_cell] == ["code_inline"], path_cell
        assert path_cell[0].content == visible(path)
        assert [t.content for t in row[2]] in (["error"], ["warning"])


@pytest.mark.parametrize("path", HOSTILE, ids=range(len(HOSTILE)))
def test_hostile_paths_never_reach_the_output_raw(path):
    markdown = render(parse(comparison(new=items([path]))))
    for char in markdown:
        assert char == "\n" or unicodedata.category(char) not in ("Cc", "Cf", "Zl", "Zp", "Cs")
    markdown.encode("utf-8")  # no lone surrogate survives


def test_hostile_rule_and_branch_are_code_spans():
    rule = "x|y<b>`"
    new = {"count": 1, "items": [{"path": "a.md", "rule": rule, "severity": "error", "count": 1}]}
    markdown = render(parse(comparison(new=new, default_branch="<b>main</b>|`x`")))
    for token in tokens(markdown):
        assert token.type not in ("html_inline", "html_block")
    row = table_rows(markdown)[2]
    assert row[1][0].type == "code_inline" and row[1][0].content == rule
    codes = [t.content for t in tokens(markdown) if t.type == "code_inline"]
    assert "<b>main</b>|`x`" in codes


def test_code_span_fence_is_longer_than_any_backtick_run():
    assert code("plain") == "` plain `"
    assert code("a`b") == "`` a`b ``"
    assert code("a```b`") == "```` a```b` ````"
    assert code("a|b") == "` a\\|b `"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://freshlint-team.example/o/x",
        "https://user:pass@evil.example/",
        "https://evil.example/a>b",
        "https://evil.example/a b",
        "https://evil.example/a\nb",
        "https://evil.example/<x>",
        'https://evil.example/"x"',
        "data:text/html,hi",
        "//evil.example/x",
        "https://" + "a" * 2100,
        42,
        None,
    ],
)
def test_unsafe_links_are_left_out(url):
    markdown = render(parse(comparison(url=url)))
    assert not [t for t in tokens(markdown) if t.type == "link_open"]
    assert "Open this repository" not in markdown


@pytest.mark.parametrize(
    "url", ["https://team.example/o/a%20b/repo/1?x=(1)#y", "http://127.0.0.1:8080/o/a/repo/1"]
)
def test_safe_links_are_kept(url):
    markdown = render(parse(comparison(url=url)))
    assert [t.attrs["href"] for t in tokens(markdown) if t.type == "link_open"] == [
        MD.normalizeLink(url)
    ]


# ------------------------------------------------------------------- validation


def _drop(key):
    def mutate(c):
        del c[key]

    return mutate


@pytest.mark.parametrize(
    "mutate",
    [
        _drop("mode"),
        _drop("new"),
        _drop("fixed"),
        _drop("totals"),
        _drop("trend"),
        lambda c: c.update(mode="partial"),
        lambda c: c.update(scope="some"),
        lambda c: c.update(scope=None),
        lambda c: c.update(default_branch=""),
        lambda c: c.update(default_branch="x" * 256),
        lambda c: c["baseline"].update(sha="not-a-sha"),
        lambda c: c["baseline"].update(errors=-1),
        lambda c: c["totals"].update(errors=True),
        lambda c: c["totals"].update(files="2"),
        lambda c: c["new"].update(count=0),
        lambda c: c["new"]["items"][0].update(severity="note"),
        lambda c: c["new"]["items"][0].update(path=""),
        lambda c: c["new"]["items"][0].update(rule=None),
        lambda c: c["new"]["items"][0].update(count=0),
        lambda c: c["new"].update(items={}),
        lambda c: c.update(trend=[{"errors": 1}]),
        lambda c: c.update(trend=[1, 2]),
    ],
)
def test_invalid_comparisons_are_refused(mutate):
    data = copy.deepcopy(SAMPLE)
    mutate(data["comparison"])
    with pytest.raises(SummaryError):
        parse(data)


@pytest.mark.parametrize("response", [None, [], {"run_id": "x"}, {"comparison": []}])
def test_responses_without_a_comparison_are_refused(response):
    with pytest.raises(SummaryError):
        parse(response)


def test_unknown_fields_are_ignored():
    data = comparison(future_field={"x": 1})
    data["comparison"]["new"]["items"][0]["future"] = True
    assert render(parse(data)) == render(parse(SAMPLE))


# --------------------------------------------------------------------- the module


def test_module_renders_stdin():
    proc = run_module(json.dumps(SAMPLE).encode())
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.decode("utf-8") == render(parse(SAMPLE))
    assert proc.stderr == b""


@pytest.mark.parametrize(
    "stdin",
    [
        b"",
        b"not json zqx-leak-4e1f",
        b"\xff\xfe",
        b"[" * 100_000,
        json.dumps({"comparison": {"mode": "zqx-leak-4e1f"}}).encode(),
        b" " * (team_summary.MAX_RESPONSE_BYTES + 1),
    ],
    ids=["empty", "text", "bad-utf8", "deep", "bad-mode", "too-large"],
)
def test_module_fails_closed_without_echoing_input(stdin):
    proc = run_module(stdin)
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert proc.stderr.decode().startswith("freshlint team_summary: ")
    assert b"zqx-leak-4e1f" not in proc.stderr


def test_module_takes_no_arguments():
    proc = run_module(json.dumps(SAMPLE).encode(), "--anything")
    assert proc.returncode == 2 and proc.stdout == b""


def test_module_is_stdlib_only_and_has_no_network_code():
    tree = ast.parse((ROOT / "src" / "freshlint" / "team_summary.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "dataclasses", "json", "re", "sys", "typing", "unicodedata"}
