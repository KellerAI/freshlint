from pathlib import Path

import pytest

from freshlint.evidence import (
    Citation,
    CitationError,
    misplaced_evidence_lines,
    parse_citation,
    parse_evidence,
    resolve,
)
from freshlint.markdown import prose_lines


def test_prose_lines_skip_frontmatter_and_fences():
    text = "---\na: b\n---\none\n~~~\ncode\n~~~\ntwo\n"
    assert prose_lines(text) == [(4, "one"), (8, "two")]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("src/a.py", Citation("src/a.py", "src/a.py", "file")),
        ("./src/a.py", Citation("./src/a.py", "src/a.py", "file")),
        ("src/a.py#L10", Citation("src/a.py#L10", "src/a.py", "lines", 10, 10)),
        ("src/a.py#L10-L24", Citation("src/a.py#L10-L24", "src/a.py", "lines", 10, 24)),
        ("src/a.py#L10-24", Citation("src/a.py#L10-24", "src/a.py", "lines", 10, 24)),
        (
            "src/a.py#Auth.logout",
            Citation("src/a.py#Auth.logout", "src/a.py", "symbol", name="Auth.logout"),
        ),
        (
            "package.json#scripts.test:e2e",
            Citation(
                "package.json#scripts.test:e2e", "package.json", "key", name="scripts.test:e2e"
            ),
        ),
        (
            "pyproject.toml#tool.ruff.line-length",
            Citation(
                "pyproject.toml#tool.ruff.line-length",
                "pyproject.toml",
                "key",
                name="tool.ruff.line-length",
            ),
        ),
        ("~/notes/x.md", Citation("~/notes/x.md", "~/notes/x.md", "file")),
    ],
)
def test_parse_citation_forms(raw, expected):
    assert parse_citation(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "https://x.dev/a.py",
        "#L1",
        "a.py#L0",
        "a.py#L9-L3",
        "a.py#not a name",
        "a.py#L1234567890",
        "a.py#L1-L1234567890",
        pytest.param("a.py#L" + "9" * 5000, id="5000-digit-start"),
        pytest.param("a.py#L1-L" + "9" * 5000, id="5000-digit-end"),
    ],
)
def test_parse_citation_rejects(raw):
    with pytest.raises(CitationError):
        parse_citation(raw)


def test_a_huge_line_number_is_a_citation_error_with_a_short_message():
    with pytest.raises(CitationError, match="invalid line range") as caught:
        parse_citation("a.py#L" + "9" * 5000)
    assert len(str(caught.value)) < 200


def test_nine_digit_line_numbers_are_still_accepted():
    cit = parse_citation("a.py#L1-L999999999")
    assert (cit.start, cit.end) == (1, 999999999)


NOTE = """---
name: x
metadata:
  type: project
---

Deploys go through the script.

**Evidence:** `infra/deploy.sh#deploy`, `package.json#scripts.deploy`
Evidence: `docs/a.md#L1-L3` `infra/deploy.sh#deploy`
- __Evidence__: `src/b.py`

~~~text
**Evidence:** `ignored/in/code.py`
~~~
"""


def test_parse_evidence_collects_lines_and_dedupes():
    block = parse_evidence(NOTE)
    assert [c.raw for c in block.citations] == [
        "infra/deploy.sh#deploy",
        "package.json#scripts.deploy",
        "docs/a.md#L1-L3",
        "src/b.py",
    ]
    assert block.lines == frozenset({9, 10, 11})
    assert block.problems == ()


def test_parse_evidence_reports_bad_citations():
    block = parse_evidence("Evidence: `https://x.dev/a` `a.py#L5-L1` `ok.py`\n")
    assert [c.raw for c in block.citations] == ["ok.py"]
    assert [line for line, _ in block.problems] == [1, 1]
    assert block.problems[0][1] == "`https://x.dev/a` is a URL; cite a file"


def test_prose_mentioning_evidence_is_not_an_evidence_line():
    block = parse_evidence("We found evidence: `a.py` was slow.\n")
    assert block.citations == ()
    assert block.lines == frozenset()


def test_resolve(tmp_path):
    home, root = tmp_path / "home", tmp_path / "proj"
    assert resolve("src/a.py", root, home) == root / "src/a.py"
    assert resolve("~/x/y.md", root, home) == home / "x/y.md"
    assert resolve("/abs/z.py", root, home) == Path("/abs/z.py")


def test_evidence_after_inline_triple_backticks_is_still_parsed():
    block = parse_evidence("```foo``` text\n**Evidence:** `a.py`\n")
    assert [c.raw for c in block.citations] == ["a.py"]
    assert block.lines == frozenset({2})


@pytest.mark.parametrize(
    "line",
    [
        "Retries wait 30s. Evidence: `src/limits.py#retry_after` returns 30.",
        "Retries wait 30s. **Evidence:** `src/limits.py#retry_after`",
        "Retries wait 30s. __Evidence__: `src/limits.py#retry_after`",
        "We found evidence: `a.py` was slow.",
        "> **Evidence:** `a.py`",
        "`x.py` is slow. EVIDENCE: `a.py`",
    ],
)
def test_misplaced_evidence_is_found_with_its_line_number(line):
    assert misplaced_evidence_lines(f"intro\n{line}\n") == [(2, line)]


@pytest.mark.parametrize(
    "text",
    [
        "**Evidence:** `a.py`\n",
        "- Evidence: `a.py`\n",
        "  __Evidence__: `a.py`\n",
        "There is evidence: see the code.\n",
        "No evidence yet, but `a.py` exists.\n",
        "`evidence`: the cited file is `a.py`.\n",
        "Evidence-based: `a.py`\n",
        "~~~text\nsee Evidence: `a.py`\n~~~\n",
        "---\nnote: Evidence: `a.py`\n---\nbody\n",
        "",
    ],
)
def test_lines_that_are_not_misplaced_evidence_are_not_returned(text):
    assert misplaced_evidence_lines(text) == []


def test_a_note_can_have_both_a_proper_line_and_a_misplaced_one():
    text = "**Evidence:** `a.py`\nAs Evidence: `b.py` shows.\n"
    assert misplaced_evidence_lines(text) == [(2, "As Evidence: `b.py` shows.")]
    assert [c.raw for c in parse_evidence(text).citations] == ["a.py"]
