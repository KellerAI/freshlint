"""R1: a note's citations cover only what they syntactically name.

An unrelated citation used to retire every inferred finding in its note and to switch the
uncited-claim check off. Coverage is now per path (for mentioned files) and per sentence (for
claims), and it is syntactic: it never asserts that the cited code supports the claim.
"""

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from freshlint import coverage
from freshlint.checks import Context, check_note, scan
from freshlint.claims import DEFAULT_HEDGES, DEFAULT_PHRASES
from freshlint.evidence import parse_evidence
from freshlint.ledger import raw_digest
from freshlint.memory import read_note
from freshlint.save import on_save
from freshlint.store import Store

TODAY = dt.date(2026, 10, 1)
ROOT = Path(__file__).resolve().parent.parent
CLAIM = "The root cause is `code/target.py`; use it for retries."
MIDDLE = "Retries back off."  # a paragraph that is neither a claim nor evidence

# The evidence forms of the 2026-10-03 validation's bypass table, each citing something
# unrelated to `code/target.py`.
FORMS = {
    "unrelated-source": "**Evidence:** `code/decoy.py`",
    "unrelated-text": "**Evidence:** `note.txt`",
    "list-bold": "- **Evidence:** `code/decoy.py`",
    "missing-file": "**Evidence:** `code/absent.py`",
    "missing-symbol": "**Evidence:** `code/decoy.py#nope`",
    "missing-key": "**Evidence:** `config.json#absent.key`",
}
TARGETS = {
    "claims": ("uncited-claim", "root cause"),
    "inferred-gone": ("gone", "code/target.py"),
    "inferred-changed": ("inferred-changed", "code/target.py"),
}


def ctx(project):
    return Context(project.path.resolve(), Path.home())


def make_world(project):
    project.write("code/target.py", "def retry():\n    return 3\n")
    project.write("code/decoy.py", "def decoy():\n    return 4\n")
    project.write("note.txt", "a note about unrelated behavior\n")
    project.write("config.json", '{"present": 4}\n')
    project.commit_at("2026-08-01T00:00:00Z", "start")


def read_all(project):
    files = ("code/decoy.py", "note.txt", "config.json")
    return {str((project.path / f).resolve()): raw_digest(project.path / f) for f in files}


def drift(project, scenario):
    if scenario == "inferred-gone":
        project.remove("code/target.py")
    elif scenario == "inferred-changed":
        project.write("code/target.py", "def retry():\n    return 9\n")


def with_evidence(form):
    return f"{CLAIM}\n\n{MIDDLE}\n\n{FORMS[form]}\n"


def found(findings):
    return {(f.state, f.target) for f in findings}


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("scenario", TARGETS)
def test_an_unrelated_citation_added_at_save_leaves_the_target_finding(
    claude_project, scenario, form
):
    make_world(claude_project)
    claude_project.note("case.md", CLAIM)
    store, c = Store(), ctx(claude_project)
    scan(claude_project.memory, c, store)  # the first session start takes the baselines
    drift(claude_project, scenario)
    path = claude_project.note("case.md", with_evidence(form), modified="2026-09-20T12:00:00Z")
    out = on_save(read_note(path), store, read_all(claude_project), c, TODAY)
    findings, _ = check_note(read_note(path), store.notes["case.md"], c)
    assert TARGETS[scenario] in found(findings)
    if scenario == "claims":
        assert any('says "root cause"' in line for line in out.feedback)


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("scenario", TARGETS)
def test_an_unrelated_citation_already_in_the_note_leaves_the_target_finding_at_scan(
    claude_project, scenario, form
):
    make_world(claude_project)
    drift(claude_project, scenario)
    claude_project.note("case.md", with_evidence(form))
    result = scan(claude_project.memory, ctx(claude_project), Store())
    assert TARGETS[scenario] in found(result.findings)


def test_a_baseline_survives_saves_that_add_renew_and_remove_other_citations(claude_project):
    make_world(claude_project)
    claude_project.note("case.md", "See `code/target.py`.")
    store, c = Store(), ctx(claude_project)
    scan(claude_project.memory, c, store)
    first = store.notes["case.md"].inferred["code/target.py"]

    def save(body, day):
        path = claude_project.note("case.md", body, modified=f"2026-09-{day}T12:00:00Z")
        on_save(read_note(path), store, read_all(claude_project), c, TODAY)
        assert store.notes["case.md"].inferred["code/target.py"] == first  # not re-baselined

    save("See `code/target.py`.\n\n**Evidence:** `code/decoy.py`", 10)  # add
    claude_project.write("code/decoy.py", "def decoy():\n    return 5\n")
    save("See `code/target.py`.\n\n**Evidence:** `code/decoy.py`", 11)  # renew
    save("See `code/target.py`.", 12)  # remove
    claude_project.write("code/target.py", "def retry():\n    return 9\n")
    result = scan(claude_project.memory, c, store)
    assert ("inferred-changed", "code/target.py") in found(result.findings)


def test_a_mention_of_the_cited_file_is_tracked_by_its_citation_only(claude_project):
    make_world(claude_project)
    claude_project.note("case.md", "See `code/target.py`.")
    store, c = Store(), ctx(claude_project)
    scan(claude_project.memory, c, store)
    assert "code/target.py" in store.notes["case.md"].inferred
    body = "See `code/target.py`.\n\n**Evidence:** `code/target.py#retry`"
    path = claude_project.note("case.md", body, modified="2026-09-20T12:00:00Z")
    target = claude_project.path / "code/target.py"
    seen = {str(target.resolve()): raw_digest(target)}
    on_save(read_note(path), store, seen, c, TODAY)
    assert store.notes["case.md"].inferred == {}  # dropped: the citation tracks it
    claude_project.write("code/target.py", "def retry():\n    return 9\n")
    assert found(scan(claude_project.memory, c, store).findings) == {
        ("changed", "code/target.py#retry")
    }
    claude_project.remove("code/target.py")
    result = scan(claude_project.memory, c, store)
    assert found(result.findings) == {("gone", "code/target.py#retry")}
    assert "mentions" not in result.findings[0].message


def test_a_scan_drops_the_inferred_record_of_a_mention_a_vouched_citation_covers(claude_project):
    make_world(claude_project)
    claude_project.note("case.md", "See `code/target.py` and `code/decoy.py`.")
    store, c = Store(), ctx(claude_project)
    scan(claude_project.memory, c, store)
    assert set(store.notes["case.md"].inferred) == {"code/target.py", "code/decoy.py"}
    body = "See `code/target.py` and `code/decoy.py`.\n\n**Evidence:** `code/decoy.py`"
    path = claude_project.note("case.md", body, modified="2026-09-20T12:00:00Z")
    decoy = claude_project.path / "code/decoy.py"
    seen = {str(decoy.resolve()): raw_digest(decoy)}
    on_save(read_note(path), store, seen, c, TODAY)  # the agent read it: the citation is vouched
    scan(claude_project.memory, c, store)
    assert set(store.notes["case.md"].inferred) == {"code/target.py"}  # decoy.py is covered


def test_an_unread_citation_does_not_take_over_a_mention_that_already_has_a_record(
    claude_project,
):
    make_world(claude_project)
    claude_project.note("case.md", "See `code/target.py`.")
    store, c = Store(), ctx(claude_project)
    scan(claude_project.memory, c, store)
    first = store.notes["case.md"].inferred["code/target.py"]
    claude_project.write("code/target.py", "def retry():\n    return 9\n")
    body = "See `code/target.py`.\n\n**Evidence:** `code/target.py`"
    path = claude_project.note("case.md", body, modified="2026-09-20T12:00:00Z")
    on_save(read_note(path), store, {}, c, TODAY)  # cited, but never read: nothing vouches
    assert store.notes["case.md"].inferred["code/target.py"] == first
    assert found(scan(claude_project.memory, c, store).findings) == {
        ("unread", "code/target.py"),
        ("inferred-changed", "code/target.py"),
    }


def test_adding_and_removing_an_unread_citation_does_not_launder_a_stale_mention(claude_project):
    make_world(claude_project)
    body = "See `code/target.py` and `code/decoy.py`."
    claude_project.note("case.md", body)
    store, c = Store(), ctx(claude_project)
    scan(claude_project.memory, c, store)
    first = dict(store.notes["case.md"].inferred)
    claude_project.write("code/target.py", "def retry():\n    return 9\n")
    claude_project.remove("code/decoy.py")
    stale = {("inferred-changed", "code/target.py"), ("gone", "code/decoy.py")}
    assert found(scan(claude_project.memory, c, store).findings) == stale

    cited = f"{body}\n\n**Evidence:** `code/target.py` `code/decoy.py`"
    path = claude_project.note("case.md", cited, modified="2026-09-20T12:00:00Z")
    on_save(read_note(path), store, {}, c, TODAY)  # added, never read
    path = claude_project.note("case.md", body, modified="2026-09-21T12:00:00Z")
    on_save(read_note(path), store, {}, c, TODAY)  # removed again
    result = scan(claude_project.memory, c, store)
    assert found(result.findings) == stale
    assert store.notes["case.md"].inferred == first  # the same baselines, not new ones


def test_a_citation_that_was_read_and_vouched_takes_over_for_good(claude_project):
    make_world(claude_project)
    body = "See `code/target.py`."
    claude_project.note("case.md", body)
    store, c = Store(), ctx(claude_project)
    scan(claude_project.memory, c, store)
    target = claude_project.path / "code/target.py"
    cited = f"{body}\n\n**Evidence:** `code/target.py`"
    path = claude_project.note("case.md", cited, modified="2026-09-20T12:00:00Z")
    on_save(read_note(path), store, {str(target.resolve()): raw_digest(target)}, c, TODAY)
    assert store.notes["case.md"].inferred == {}  # a fingerprint now: the citation tracks it
    path = claude_project.note("case.md", body, modified="2026-09-21T12:00:00Z")
    on_save(read_note(path), store, {}, c, TODAY)  # the citation goes away again
    scan(claude_project.memory, c, store)
    # the mention is new again, so it is baselined at the later note time
    later = dt.datetime.fromisoformat("2026-09-21T12:00:00+00:00").timestamp()
    assert store.notes["case.md"].inferred["code/target.py"].when == later


SPELLINGS = ("dot-slash", "absolute", "home", "parent", "symlink")


def spell(project, variant):
    """A file in the project, written two ways: the plain way and the way ``variant`` names."""
    root = project.path.resolve()
    if variant == "home":
        project.home_file = Path.home() / "notes" / "n.py"
        project.home_file.parent.mkdir(parents=True, exist_ok=True)
        project.home_file.write_text("x = 1\n")
        return project.home_file, "~/notes/n.py", str(project.home_file)
    (project.path / "link").symlink_to("code")
    other = {
        "dot-slash": "./code/target.py",
        "absolute": str(root / "code/target.py"),
        "parent": "code/../code/target.py",
        "symlink": "link/target.py",
    }[variant]
    return project.path / "code/target.py", "code/target.py", other


@pytest.mark.parametrize("mention_is_plain", [True, False])
@pytest.mark.parametrize("variant", SPELLINGS)
def test_a_citation_covers_a_mention_whichever_way_each_spells_the_path(
    claude_project, variant, mention_is_plain
):
    make_world(claude_project)
    target, plain, other = spell(claude_project, variant)
    mention, cited = (plain, other) if mention_is_plain else (other, plain)
    claude_project.note("case.md", f"See `{mention}` and `code/decoy.py`.")
    store, c = Store(), ctx(claude_project)
    scan(claude_project.memory, c, store)
    assert set(store.notes["case.md"].inferred) == {mention, "code/decoy.py"}
    body = f"See `{mention}` and `code/decoy.py`.\n\n**Evidence:** `{cited}`"
    path = claude_project.note("case.md", body, modified="2026-09-20T12:00:00Z")
    seen = {str(target.resolve()): raw_digest(target)}
    on_save(read_note(path), store, seen, c, TODAY)
    # the read, vouched citation tracks the file now; the other mention is not its business
    assert set(store.notes["case.md"].inferred) == {"code/decoy.py"}
    target.write_text("def retry():\n    return 9\n")
    assert found(scan(claude_project.memory, c, store).findings) == {("changed", cited)}


def test_removing_the_covering_citation_baselines_the_mention_again(claude_project):
    make_world(claude_project)
    body = "See `code/target.py`."
    claude_project.note("case.md", body + "\n\n**Evidence:** `code/target.py`")
    store, c = Store(), ctx(claude_project)
    scan(claude_project.memory, c, store)
    assert store.notes["case.md"].inferred == {}
    path = claude_project.note("case.md", body, modified="2026-09-20T12:00:00Z")
    on_save(read_note(path), store, {}, c, TODAY)
    scan(claude_project.memory, c, store)
    assert set(store.notes["case.md"].inferred) == {"code/target.py"}  # new, so a baseline


def test_a_covered_record_is_dropped_even_when_the_key_still_matches(claude_project):
    # A save that drops a record and a later save that uncovers the mention must not leave a
    # mention with no record: the key that said "all baselined" is cleared with the drop.
    make_world(claude_project)
    body = "See `code/target.py`."
    claude_project.note("case.md", body)
    store, c = Store(), ctx(claude_project)
    scan(claude_project.memory, c, store)
    path = claude_project.note(
        "case.md", f"{body}\n\n**Evidence:** `code/target.py`", modified="2026-09-10T12:00:00Z"
    )
    target = claude_project.path / "code/target.py"
    on_save(read_note(path), store, {str(target.resolve()): raw_digest(target)}, c, TODAY)
    assert store.notes["case.md"].inferred == {} and store.notes["case.md"].inferred_key is None
    path = claude_project.note("case.md", body, modified="2026-09-11T12:00:00Z")
    on_save(read_note(path), store, {}, c, TODAY)
    claude_project.remove("code/target.py")
    assert ("gone", "code/target.py") in found(scan(claude_project.memory, c, store).findings)


def claim_findings(project, body, cited_reads=True):
    """Whether the scan and the save raise the uncited-claim finding for ``body``."""
    make_world(project)
    path = project.note("case.md", body)
    store, c = Store(), ctx(project)
    reads = read_all(project) if cited_reads else {}
    out = on_save(read_note(path), store, reads, c, TODAY)
    findings, _ = check_note(read_note(path), store.notes["case.md"], c)
    scanned = any(f.state == "uncited-claim" for f in findings)
    saved = any('says "' in line for line in out.feedback)
    assert scanned == saved, (scanned, saved)
    return scanned


EVIDENCE = "**Evidence:** `code/decoy.py`"
SYMBOL = "**Evidence:** `code/decoy.py#decoy`"
KEY = "**Evidence:** `config.json#present`"

COVERED = {
    # a path-like token needs a "/", a "." (an extension, or a dot file) or a ":line" suffix
    "names-a-cited-directory-as-a-path": (
        f"**Evidence:** `src/`\n\n{MIDDLE}\n\nThe root cause is in src/ and `retry()` fails.\n"
    ),
    "names-a-cited-dot-file": (
        f"**Evidence:** `.gitignore`\n\n{MIDDLE}\n\nThe root cause is in .gitignore and `retry()` fails.\n"
    ),
    "names-a-cited-root-file-with-an-extension": (
        f"**Evidence:** `note.txt`\n\n{MIDDLE}\n\nThe root cause is in note.txt and `retry()` fails.\n"
    ),
    "names-a-cited-extensionless-file-and-a-line": (
        f"**Evidence:** `Makefile`\n\n{MIDDLE}\n\nThe root cause is in Makefile:12 and `retry()` fails.\n"
    ),
    "names-a-cited-bare-word-in-backticks": (
        f"**Evidence:** `src`\n\n{MIDDLE}\n\nThe root cause is in `src` and `retry()` fails.\n"
    ),
    "names-a-cited-bare-word-in-a-command-in-backticks": (
        f"**Evidence:** `src`\n\n{MIDDLE}\n\nThe root cause is in `ls src` and `retry()` fails.\n"
    ),
    # the claim and the Evidence line are in one paragraph (the validation's b012 text)
    "adjacent": f"{CLAIM}\n{EVIDENCE}\n",
    "evidence-first-line-after-the-paragraph": f"{CLAIM}\n\n{EVIDENCE}\n",
    "same-list-item": f"- {CLAIM}\n  {EVIDENCE}\n",
    "sub-item-under-the-claim": f"- {CLAIM}\n  - {EVIDENCE}\n",
    "next-list-item": f"- {CLAIM}\n- {EVIDENCE}\n",
    "heading-claim": f"## Root cause verified in `code/target.py`\n\n{EVIDENCE}\n",
    "names-the-cited-path": f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is `code/decoy.py`.\n",
    "names-the-cited-path-bare": f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is code/decoy.py.\n",
    "names-the-cited-path-with-a-fragment": (
        f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is `code/decoy.py#decoy`.\n"
    ),
    "names-a-cited-symbol": f"{SYMBOL}\n\n{MIDDLE}\n\nThe root cause is `decoy`.\n",
    "names-a-cited-symbol-as-a-call": f"{SYMBOL}\n\n{MIDDLE}\n\nThe root cause is `decoy()`.\n",
    "names-a-cited-key": f"{KEY}\n\n{MIDDLE}\n\nThe root cause is `present`.\n",
    # a bare path-like token that starts with a dot is still the path it spells
    "names-a-dot-slash-path": f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is ./code/decoy.py.\n",
    "names-a-dot-directory-path": (
        f"**Evidence:** `.claude/settings.json`\n\n{MIDDLE}\n\n"
        "The root cause is .claude/settings.json in the hook.\n"
    ),
    "names-a-parent-path": (
        f"**Evidence:** `../shared.py`\n\n{MIDDLE}\n\nThe root cause is ../shared.py in the hook.\n"
    ),
    "names-a-bold-path": f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is **code/decoy.py**.\n",
    # a line or a line range after the path still names the path
    "names-a-path-and-a-line": f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is `code/decoy.py:10`.\n",
    "names-a-path-and-a-line-range": (
        f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is `code/decoy.py:10-20`.\n"
    ),
    "names-a-bare-path-and-a-line-range": (
        f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is code/decoy.py:10-20 in the loop.\n"
    ),
    "names-a-bare-path-line-range-and-column": (
        f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is code/decoy.py:10-20:5 in the loop.\n"
    ),
}
UNCOVERED = {
    # A bare word is not a path-like token, though it resolves to the cited path: a note that cites
    # a directory or a file with a word for a name must not cover every claim that uses the word.
    "a-bare-word-names-a-cited-directory": (
        f"**Evidence:** `src/`\n\n{MIDDLE}\n\nThe root cause is in src and `retry()` fails.\n"
    ),
    "a-bare-word-names-a-cited-directory-without-a-slash": (
        f"**Evidence:** `code`\n\n{MIDDLE}\n\nThe root cause is in code and `retry()` fails.\n"
    ),
    "a-bare-word-names-a-cited-file-called-test": (
        f"**Evidence:** `test`\n\n{MIDDLE}\n\nThe root cause is `retry()`, the test shows it.\n"
    ),
    "a-bare-word-names-a-cited-directory-docs": (
        f"**Evidence:** `docs/`\n\n{MIDDLE}\n\nThe root cause is `retry()`, see the docs.\n"
    ),
    "a-bare-word-names-a-cited-extensionless-file": (
        f"**Evidence:** `Makefile`\n\n{MIDDLE}\n\nThe root cause is `retry()`, see Makefile.\n"
    ),
    "a-bare-word-in-bold-names-a-cited-directory": (
        f"**Evidence:** `src/`\n\n{MIDDLE}\n\nThe root cause is in **src** and `retry()`.\n"
    ),
    "a-paragraph-between": f"{CLAIM}\n\n{MIDDLE}\n\n{EVIDENCE}\n",
    "evidence-above-the-claim": f"{EVIDENCE}\n\n{CLAIM}\n",
    "a-heading-ends-the-paragraph": f"{CLAIM}\n## Sources\n{EVIDENCE}\n",
    "list-items-are-paragraphs": f"- {CLAIM}\n- {MIDDLE}\n- {EVIDENCE}\n",
    "a-code-fence-between": f"{CLAIM}\n```\nx = 1\n```\n{EVIDENCE}\n",
    "names-another-file": f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is `code/other.py`.\n",
    "names-a-name-that-is-not-cited": f"{SYMBOL}\n\n{MIDDLE}\n\nThe root cause is `other`.\n",
    "names-only-the-file-name": f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is `decoy.py`.\n",
    "names-another-dot-slash-path": (
        f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is ./code/other.py in the loop.\n"
    ),
    "names-the-parent-of-the-cited-path": (
        f"{EVIDENCE}\n\n{MIDDLE}\n\nThe root cause is ../decoy.py in the loop.\n"
    ),
}


@pytest.mark.parametrize("body", COVERED.values(), ids=COVERED.keys())
def test_a_claim_the_citations_cover_is_not_flagged(claude_project, body):
    assert claim_findings(claude_project, body) is False


@pytest.mark.parametrize("body", UNCOVERED.values(), ids=UNCOVERED.keys())
def test_a_claim_no_citation_covers_is_still_flagged(claude_project, body):
    assert claim_findings(claude_project, body) is True


@pytest.mark.parametrize(
    "evidence",
    [
        "**Evidence:** `https://example.invalid/a`",
        "**Evidence:** code/decoy.py",
        "**Evidence:**",
    ],
)
def test_an_evidence_line_that_parsed_no_citation_covers_nothing(claude_project, evidence):
    assert claim_findings(claude_project, f"{CLAIM}\n{evidence}\n") is True


def test_coverage_is_syntactic_so_an_unrelated_citation_next_to_a_claim_covers_it(claude_project):
    # The documented limit: freshlint never checks that the cited code supports the claim.
    assert claim_findings(claude_project, f"{CLAIM}\n{EVIDENCE}\n") is False


def test_only_the_uncovered_claims_of_a_note_are_flagged(claude_project):
    text = f"The root cause is `a.py`.\n\n{MIDDLE}\n\nThe root cause is `b.py`.\n{EVIDENCE}\n"
    block = parse_evidence(text)
    hits = coverage.uncovered_claims(
        text, block, claude_project.path.resolve(), Path.home(), DEFAULT_PHRASES, DEFAULT_HEDGES
    )
    assert [(h.line, h.sentence) for h in hits] == [(1, "The root cause is `a.py`.")]


def test_a_note_without_a_parsed_citation_flags_every_claim_as_before(claude_project):
    text = f"The root cause is `a.py`.\n\n{MIDDLE}\n\nThe root cause is `b.py`.\n"
    block = parse_evidence(text)
    hits = coverage.uncovered_claims(
        text, block, claude_project.path.resolve(), Path.home(), DEFAULT_PHRASES, DEFAULT_HEDGES
    )
    assert [h.line for h in hits] == [1, 5]


def test_paragraphs_end_at_blank_lines_headings_fences_and_list_items():
    text = (
        "---\ntype: project\n---\n"  # 1-3: frontmatter is not a paragraph
        "intro line\ncontinues\n\n"  # 4-5, blank 6
        "# Heading\ntext\n"  # 7 heading, 8
        "- item one\n  more\n- item two\n"  # 9-10, 11
        "```\ncode\n```\nafter fence\n"  # fence 12-14, 15
    )
    assert coverage.paragraph_spans(text) == [(4, 5), (7, 7), (8, 8), (9, 10), (11, 11), (15, 15)]


def test_cited_files_and_names_cover_every_citation_kind(claude_project):
    text = (
        "**Evidence:** `a.py` `b.py#L2-L3` `c.py#Cls.method` `d.json#x.y` `https://bad.example`\n"
    )
    block = parse_evidence(text)
    root = claude_project.path.resolve()
    assert coverage.cited_files(block, root, Path.home()) == {
        str(root / name) for name in ("a.py", "b.py", "c.py", "d.json")
    }
    assert coverage.cited_names(block) == {"Cls.method", "x.y"}
    assert block.cited_lines == frozenset({1})


def test_an_evidence_line_with_no_good_citation_is_not_a_cited_line():
    block = parse_evidence("a\n**Evidence:** `https://bad.example`\n**Evidence:**\n")
    assert block.lines == frozenset({2, 3}) and block.cited_lines == frozenset()


# The real hook: Read and Write payloads, a temporary HOME, a new session, the bundled runner.


def _hook(project, env, event, session, path=None, tool=None):
    payload = {"hook_event_name": event, "session_id": session, "cwd": str(project.path)}
    if path is None:
        payload["source"] = "startup"
    else:
        payload.update(tool_name=tool, tool_input={"file_path": str(path)}, tool_response={})
    proc = subprocess.run(
        [str(ROOT / "bin/freshlint"), "hook"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        cwd=project.path,
        env=env,
        timeout=60,
    )
    assert (proc.returncode, proc.stderr) == (0, "")
    if not proc.stdout.strip():
        return ""
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def _full_scan(project, env):
    proc = subprocess.run(
        [str(ROOT / "bin/freshlint"), "memory", "scan", "--project", str(project.path)]
        + ["--format", "json"],
        capture_output=True,
        text=True,
        check=True,
        cwd=project.path,
        env=env,
        timeout=60,
    )
    return {(f["state"], f["target"]) for f in json.loads(proc.stdout)["findings"]}


@pytest.mark.parametrize("scenario", TARGETS)
def test_the_hook_still_reports_the_target_after_an_unrelated_evidence_line(
    claude_project, tmp_path, monkeypatch, scenario
):
    """The validation's b012, b013 and b014, with real hook payloads and a new session.

    The claim and its decoy sit a paragraph apart: with the decoy directly under the claim,
    the Evidence line is in the claim's paragraph, which covers it by design.
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(claude_project.path))
    monkeypatch.setenv("FRESHLINT_CACHE_DIR", str(tmp_path / "cache"))
    path_dirs = f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"
    env = {**os.environ, "PATH": path_dirs}
    make_world(claude_project)
    claude_project.note("case.md", CLAIM)
    _hook(claude_project, env, "SessionStart", "s1")  # the baselines are taken here
    drift(claude_project, scenario)
    assert TARGETS[scenario] in _full_scan(claude_project, env)  # reported before the decoy
    decoy = claude_project.path / "code/decoy.py"
    _hook(claude_project, env, "PostToolUse", "s2", decoy, "Read")
    note = claude_project.note(
        "case.md", with_evidence("unrelated-source"), modified="2026-09-20T12:00:00Z"
    )
    saved = _hook(claude_project, env, "PostToolUse", "s2", note, "Write")
    next_session = _hook(claude_project, env, "SessionStart", "s3")
    assert TARGETS[scenario] in _full_scan(claude_project, env)
    if scenario == "claims":
        assert 'case.md says "root cause"' in saved
    if scenario == "inferred-gone":
        assert "case.md: mentions code/target.py, which no longer exists." in next_session
    lock = json.loads((claude_project.memory / ".freshlint" / "lock.json").read_text())
    record = lock["notes"]["case.md"]
    assert record["citations"]["code/decoy.py"]["fingerprint"] is not None
    if scenario != "claims":
        assert "code/target.py" in record["inferred"]
