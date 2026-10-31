import datetime as dt
import os
import shutil
import time
import types
from pathlib import Path

import pytest

from conftest import ClaudeProject
from freshlint import checks, coverage, gitio, infer
from freshlint.checks import Context, scan
from freshlint.evidence import parse_citation, resolve
from freshlint.fingerprint import take
from freshlint.store import CitationRecord, NoteRecord, Store, load_store, save_store


def ctx_for(project):
    return Context(project.path.resolve(), Path.home())


def stamp(project, store, note, raw):
    cit = parse_citation(raw)
    fp = take(cit, resolve(cit.path, project.path.resolve(), Path.home()))
    store.notes.setdefault(note, NoteRecord()).citations[raw] = CitationRecord(fp, "2026-09-01")


def states(result):
    return sorted((f.note, f.state) for f in result.findings)


def test_fresh_citation_has_no_findings(claude_project):
    claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.note("a.md", "f returns 1.\n\n**Evidence:** `src/a.py#f`")
    store = Store()
    stamp(claude_project, store, "a.md", "src/a.py#f")
    result = scan(claude_project.memory, ctx_for(claude_project), store)
    assert (result.notes, result.citing, result.findings) == (1, 1, [])


def test_changed_gone_and_unread(claude_project):
    claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.write("src/b.py", "def g():\n    return 2\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    claude_project.note("b.md", "**Evidence:** `src/b.py#g`")
    claude_project.note("c.md", "**Evidence:** `src/a.py`")
    store = Store()
    stamp(claude_project, store, "a.md", "src/a.py#f")
    stamp(claude_project, store, "b.md", "src/b.py#g")
    claude_project.write("src/a.py", "def f():\n    return 9\n")
    claude_project.remove("src/b.py")
    result = scan(claude_project.memory, ctx_for(claude_project), store)
    assert states(result) == [("a.md", "changed"), ("b.md", "gone"), ("c.md", "unread")]
    assert all(f.reported for f in result.findings)
    messages = {f.note: f.message for f in result.findings}
    assert messages["a.md"] == "src/a.py#f changed after this memory was written."
    assert messages["b.md"] == "src/b.py#g no longer exists."
    assert messages["c.md"] == (
        "src/a.py has no saved fingerprint yet, so freshlint cannot tell whether it was read when "
        "the memory was saved. Read it and save the memory to fingerprint it."
    )


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_an_unreadable_cited_file_is_changed_never_gone(claude_project):
    target = claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    store = Store()
    stamp(claude_project, store, "a.md", "src/a.py#f")
    target.chmod(0)
    try:
        result = scan(claude_project.memory, ctx_for(claude_project), store)
    finally:
        target.chmod(0o600)
    assert states(result) == [("a.md", "changed")]


def test_uncited_claims_only_for_project_and_reference_notes(claude_project):
    claude_project.note("p.md", "The root cause is the cache in `src/cache.py`.")
    claude_project.note("f.md", "Never say the root cause is in `src/x.py`.", note_type="feedback")
    result = scan(claude_project.memory, ctx_for(claude_project), Store())
    assert states(result) == [("p.md", "uncited-claim")]
    assert result.findings[0].message == 'says "root cause" but cites no evidence.'
    assert not result.findings[0].reported


def test_a_citation_the_save_hook_recorded_without_a_read_keeps_the_not_read_message(
    claude_project,
):
    claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    store = Store()
    store.notes["a.md"] = NoteRecord(citations={"src/a.py#f": CitationRecord(None, None)})
    result = scan(claude_project.memory, ctx_for(claude_project), store)
    assert [(f.state, f.message) for f in result.findings] == [
        (
            "unread",
            "cites src/a.py#f, but it was not read when the memory was saved, "
            "so it was never fingerprinted.",
        )
    ]


def test_a_citation_with_no_store_record_says_there_is_no_saved_fingerprint(claude_project):
    claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    first = scan(claude_project.memory, ctx_for(claude_project), Store())
    other = Store()
    other.notes["a.md"] = NoteRecord(citations={"src/other.py": CitationRecord(None, None)})
    second = scan(claude_project.memory, ctx_for(claude_project), other)
    expected = (
        "src/a.py#f has no saved fingerprint yet, so freshlint cannot tell whether it was read "
        "when the memory was saved. Read it and save the memory to fingerprint it."
    )
    for result in (first, second):  # a first scan, and a note whose record lacks this citation
        assert [(f.state, f.message) for f in result.findings] == [("unread", expected)]
        assert result.findings[0].reported


def unread_message(claude_project, record):
    store = Store()
    if record is not None:
        store.notes["a.md"] = NoteRecord(citations={"src/a.py#f": record})
    result = scan(claude_project.memory, ctx_for(claude_project), store)
    (finding,) = result.findings
    assert finding.state == "unread" and finding.reported
    return finding.message


def test_unread_says_what_happened_to_a_citation_the_hook_read_but_could_not_fingerprint(
    claude_project,
):
    claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    changed = unread_message(claude_project, CitationRecord(None, None, "changed"))
    assert changed == (
        "cites src/a.py#f, which changed after it was read when the memory was saved, so it was "
        "never fingerprinted. Read it again and save the memory to fingerprint it."
    )
    missing = unread_message(
        claude_project, CitationRecord(None, None, "missing:'f' is not defined in src/a.py")
    )
    assert missing == (
        "cites src/a.py#f, but when the memory was saved 'f' is not defined in src/a.py, so it "
        "was never fingerprinted."
    )
    for message in (changed, missing):
        assert "was not read" not in message


def test_unread_for_a_file_that_does_not_exist_says_so_whatever_the_record(claude_project):
    claude_project.note("a.md", "**Evidence:** `src/a.py#f`")  # src/a.py was never created
    expected = (
        "cites src/a.py#f, but that file does not exist. "
        "Cite a file that exists, or remove the citation."
    )
    for record in (None, CitationRecord(None, None), CitationRecord(None, None, "changed")):
        assert unread_message(claude_project, record) == expected


def test_inferred_mentions(claude_project):
    claude_project.write("src/live.py", "a\n")
    claude_project.write("old/x.py", "x\n")
    claude_project.commit_at("2026-08-01T00:00:00Z", "start")
    claude_project.note("n.md", "See `src/live.py`, `old/x.py` and `feature/branch-name`.")
    claude_project.write("src/live.py", "b\n")
    claude_project.remove("old/x.py")
    claude_project.commit_at("2026-09-10T00:00:00Z", "later")
    result = scan(claude_project.memory, ctx_for(claude_project), Store())
    found = {(f.state, f.target, f.confidence, f.reported) for f in result.findings}
    assert found == {
        ("gone", "old/x.py", "high", True),
        ("inferred-changed", "src/live.py", "low", False),
    }
    assert result.citing == 1


def test_inferred_baselines_are_cached_per_mention(claude_project, monkeypatch):
    claude_project.write("src/a.py", "a\n")
    claude_project.write("src/b.py", "b\n")
    claude_project.commit_at("2026-08-01T00:00:00Z")
    claude_project.note("n.md", "See `src/a.py`.")
    store, ctx = Store(), ctx_for(claude_project)
    scan(claude_project.memory, ctx, store)
    first = dict(store.notes["n.md"].inferred)

    calls = []
    real = infer.baselines
    monkeypatch.setattr(infer, "baselines", lambda *a, **k: calls.append(a) or real(*a, **k))
    scan(claude_project.memory, ctx, store)
    assert calls == []
    claude_project.note("n.md", "See `src/a.py` again.", modified="2026-09-20T12:00:00Z")
    scan(claude_project.memory, ctx, store)
    assert calls == []  # the same mentions: nothing is baselined again
    claude_project.note("n.md", "See `src/a.py` and `src/b.py`.", modified="2026-09-21T12:00:00Z")
    scan(claude_project.memory, ctx, store)
    assert [item[1].name for call in calls for item in call[0]] == ["b.py"]  # only the new mention
    record = store.notes["n.md"].inferred
    assert record["src/a.py"] == first["src/a.py"]  # kept, with the time it was taken
    assert record["src/b.py"].when == ts("2026-09-21T12:00:00Z")


def ts(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def drift(claude_project):
    """A note written 2026-09-01 that mentions a file which changed and one that was removed."""
    claude_project.write("src/live.py", "a\n")
    claude_project.write("old/x.py", "x\n")
    claude_project.commit_at("2026-08-01T00:00:00Z", "start")
    body = "See `src/live.py` and `old/x.py`."
    claude_project.note("n.md", body)
    claude_project.write("src/live.py", "b\n")
    claude_project.remove("old/x.py")
    claude_project.commit_at("2026-09-10T00:00:00Z", "later")
    return body


def found(result):
    return {(f.state, f.target) for f in result.findings}


STILL = {("gone", "old/x.py"), ("inferred-changed", "src/live.py")}


def test_re_saving_a_note_without_a_read_keeps_its_inferred_findings(claude_project):
    body = drift(claude_project)
    store, ctx = Store(), ctx_for(claude_project)
    assert found(scan(claude_project.memory, ctx, store)) == STILL
    # Claude Code stamps a new `modified` on every write; the agent re-saves unchanged.
    claude_project.note("n.md", body, modified="2026-09-20T12:00:00Z")
    assert found(scan(claude_project.memory, ctx, store)) == STILL
    claude_project.note("n.md", body + "\n", modified="2026-09-25T12:00:00Z")
    assert found(scan(claude_project.memory, ctx, store)) == STILL


def test_inferred_findings_survive_a_re_save_across_sessions(claude_project):
    body = drift(claude_project)
    store, ctx = Store(), ctx_for(claude_project)
    scan(claude_project.memory, ctx, store)
    save_store(claude_project.memory, store)  # session one ends
    claude_project.note("n.md", body, modified="2026-09-20T12:00:00Z")  # the agent re-saves
    reloaded = load_store(claude_project.memory)  # session two starts
    assert found(scan(claude_project.memory, ctx, reloaded)) == STILL


def test_removing_the_mention_clears_its_inferred_finding(claude_project):
    drift(claude_project)
    store, ctx = Store(), ctx_for(claude_project)
    scan(claude_project.memory, ctx, store)
    claude_project.note("n.md", "See `src/live.py` only.", modified="2026-09-20T12:00:00Z")
    assert found(scan(claude_project.memory, ctx, store)) == {("inferred-changed", "src/live.py")}
    assert set(store.notes["n.md"].inferred) == {"src/live.py"}
    claude_project.note("n.md", "Nothing named here.", modified="2026-09-21T12:00:00Z")
    assert found(scan(claude_project.memory, ctx, store)) == set()
    assert store.notes["n.md"].inferred == {}


def test_an_unread_evidence_line_leaves_the_inferred_findings_until_a_fingerprint_takes_over(
    claude_project,
):
    drift(claude_project)
    store, ctx = Store(), ctx_for(claude_project)
    scan(claude_project.memory, ctx, store)
    claude_project.note(
        "n.md",
        "See `src/live.py` and `old/x.py`.\n\n**Evidence:** `src/live.py`",
        modified="2026-09-20T12:00:00Z",
    )
    # The citation of src/live.py was never read, so nothing vouches for it yet: its inferred
    # record stays beside the `unread` finding until a fingerprint takes over. old/x.py is not
    # covered by any citation, so its inferred finding stays whatever happens to src/live.py.
    assert found(scan(claude_project.memory, ctx, store)) == {
        ("unread", "src/live.py"),
        ("inferred-changed", "src/live.py"),
        ("gone", "old/x.py"),
    }
    assert set(store.notes["n.md"].inferred) == {"src/live.py", "old/x.py"}


def test_records_for_deleted_notes_are_pruned(claude_project):
    claude_project.note("a.md", "x")
    store = Store({"gone.md": NoteRecord()})
    scan(claude_project.memory, ctx_for(claude_project), store)
    assert set(store.notes) == {"a.md"}


def test_a_passed_deadline_marks_the_scan_partial(claude_project):
    claude_project.note("a.md", "The root cause is X.")
    result = scan(
        claude_project.memory, ctx_for(claude_project), Store(), deadline=time.monotonic() - 1
    )
    assert result.partial and result.findings == []


def test_invalid_citations_are_low_confidence(claude_project):
    claude_project.note("a.md", "**Evidence:** `https://example.com/x`")
    result = scan(claude_project.memory, ctx_for(claude_project), Store())
    assert [(f.state, f.reported) for f in result.findings] == [("invalid-citation", False)]


def test_evidence_inside_a_sentence_is_a_low_confidence_finding(claude_project):
    claude_project.write("src/a.py", "def f():\n    return 1\n")
    path = claude_project.note(
        "a.md", "f is quick.\nUnverified. Evidence: `src/a.py#f` returns 1.\n"
    )
    result = scan(claude_project.memory, ctx_for(claude_project), Store())
    found = [f for f in result.findings if f.state == "misplaced-evidence"]
    assert [(f.note, f.target, f.confidence, f.reported) for f in found] == [
        ("a.md", "", "low", False)
    ]
    lines = path.read_text().splitlines()
    line = next(i for i, text in enumerate(lines, 1) if "Unverified" in text)
    assert found[0].message == (
        f"line {line} mentions evidence but is not an Evidence line, so nothing was cited."
    )


def test_misplaced_evidence_is_only_a_finding_for_notes_that_make_claims(claude_project):
    text = "Always cite as you go. Evidence: `src/a.py` shows why.\n"
    claude_project.note("rule.md", text, note_type="feedback")
    claude_project.note("who.md", text, note_type="user")
    claude_project.note("p.md", text, note_type="project")
    claude_project.note("r.md", text, note_type="reference")
    result = scan(claude_project.memory, ctx_for(claude_project), Store())
    flagged = sorted(f.note for f in result.findings if f.state == "misplaced-evidence")
    assert flagged == ["p.md", "r.md"]


def test_a_proper_evidence_line_hides_the_misplaced_finding(claude_project):
    claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.note("a.md", "As Evidence: `src/a.py` says.\n\n**Evidence:** `src/a.py#f`\n")
    result = scan(claude_project.memory, ctx_for(claude_project), Store())
    assert [f.state for f in result.findings] == ["unread"]


def test_an_unreadable_cited_file_says_it_cannot_be_read(claude_project):
    if os.geteuid() == 0:
        pytest.skip("root ignores file permissions")
    target = claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    store = Store()
    stamp(claude_project, store, "a.md", "src/a.py#f")
    target.chmod(0)
    try:
        result = scan(claude_project.memory, ctx_for(claude_project), store)
    finally:
        target.chmod(0o600)
    assert [(f.state, f.message) for f in result.findings] == [
        ("changed", "src/a.py#f cannot be read, so it could not be checked.")
    ]


def test_a_changed_readable_file_still_says_it_changed(claude_project):
    claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    store = Store()
    stamp(claude_project, store, "a.md", "src/a.py#f")
    claude_project.write("src/a.py", "def f():\n    return 2\n")
    result = scan(claude_project.memory, ctx_for(claude_project), store)
    assert [f.message for f in result.findings] == [
        "src/a.py#f changed after this memory was written."
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_an_unreadable_note_does_not_stop_the_scan(claude_project):
    claude_project.write("src/b.py", "def g():\n    return 2\n")
    claude_project.note("a.md", "x")
    locked = claude_project.note("b.md", "**Evidence:** `src/b.py#g`")
    claude_project.note("c.md", "**Evidence:** `src/b.py`")
    locked.chmod(0)
    try:
        result = scan(claude_project.memory, ctx_for(claude_project), Store())
    finally:
        locked.chmod(0o600)
    assert result.notes == 3
    assert states(result) == [("b.md", "check-error"), ("c.md", "unread")]
    error = next(f for f in result.findings if f.state == "check-error")
    assert error.message == "could not be checked (PermissionError)."
    assert (error.confidence, error.reported) == ("low", False)
    assert not result.partial


def test_a_huge_line_number_is_an_invalid_citation_not_a_crash(claude_project):
    claude_project.note("a.md", "**Evidence:** `src/a.py#L" + "9" * 5000 + "`")
    claude_project.note("b.md", "The root cause is the cache in `src/cache.py`.")
    result = scan(claude_project.memory, ctx_for(claude_project), Store())
    assert states(result) == [("a.md", "invalid-citation"), ("b.md", "uncited-claim")]


def test_an_absurd_modified_date_does_not_stop_the_scan(claude_project):
    claude_project.write("src/a.py", "a\n")
    claude_project.commit_at("2026-08-01T00:00:00Z", "start")
    claude_project.note("n.md", "See `src/a.py`.", modified="9999-12-31T23:59:59-23:00")
    claude_project.note("m.md", "**Evidence:** `src/a.py`")
    result = scan(claude_project.memory, ctx_for(claude_project), Store())
    assert states(result) == [("m.md", "unread")]


def test_deeply_nested_frontmatter_is_a_check_error_and_the_scan_continues(claude_project):
    deep = (
        "---\nname: deep\nmetadata:\n  type: project\n  x: "
        + "[" * 20000
        + "]" * 20000
        + "\n---\n\nbody\n"
    )
    (claude_project.memory).mkdir(parents=True, exist_ok=True)
    (claude_project.memory / "deep.md").write_text(deep)
    claude_project.note("z.md", "**Evidence:** `src/a.py`")
    result = scan(claude_project.memory, ctx_for(claude_project), Store())
    assert result.notes == 2
    assert states(result) == [("deep.md", "check-error"), ("z.md", "unread")]
    error = next(f for f in result.findings if f.state == "check-error")
    assert error.message == "could not be checked (RecursionError)."
    assert not error.reported


def ignoring(claude_project, *patterns):
    claude_project.write(".gitignore", "\n".join(patterns) + "\n")
    claude_project.write("src/a.py", "a\n")
    claude_project.commit_at("2026-08-01T00:00:00Z", "start")


def test_a_gitignored_mention_is_never_baselined_or_reported_gone(claude_project):
    ignoring(claude_project, "dist/")
    claude_project.write("dist/app.js", "built\n")  # generated, untracked, ignored
    claude_project.note("n.md", "The bundle is `dist/app.js`; source is `src/a.py`.")
    store, ctx = Store(), ctx_for(claude_project)
    assert found(scan(claude_project.memory, ctx, store)) == set()
    assert set(store.notes["n.md"].inferred) == {"src/a.py"}
    shutil.rmtree(claude_project.path / "dist")  # the next build regenerates it
    assert found(scan(claude_project.memory, ctx, store)) == set()


def test_one_unanswerable_mention_does_not_switch_off_the_ignore_skip(claude_project):
    ignoring(claude_project, "dist/")
    claude_project.write("dist/app.js", "built\n")
    (claude_project.path / "linked").symlink_to("src", target_is_directory=True)
    claude_project.note("n.md", "Bundle `dist/app.js`, source `src/a.py`, link `linked/a.py`.")
    store, ctx = Store(), ctx_for(claude_project)
    assert found(scan(claude_project.memory, ctx, store)) == set()
    assert "dist/app.js" not in store.notes["n.md"].inferred
    assert "src/a.py" in store.notes["n.md"].inferred
    shutil.rmtree(claude_project.path / "dist")
    assert found(scan(claude_project.memory, ctx, store)) == set()


def test_a_tracked_file_that_matches_an_ignore_pattern_is_still_checked(claude_project):
    ignoring(claude_project, "*.cfg")
    claude_project.write("local.cfg", "one\n")
    claude_project.git("add", "-f", "local.cfg")
    claude_project.commit_at("2026-08-02T00:00:00Z", "force-add")
    claude_project.note("n.md", "See `local.cfg`.")
    claude_project.write("local.cfg", "two\n")
    claude_project.commit_at("2026-09-10T00:00:00Z", "later")
    store = Store()
    assert found(scan(claude_project.memory, ctx_for(claude_project), store)) == {
        ("inferred-changed", "local.cfg")
    }


def test_all_ignore_checks_of_a_scan_are_one_git_call(claude_project, monkeypatch):
    ignoring(claude_project, "dist/")
    claude_project.write("dist/app.js", "built\n")
    for name in ("a.md", "b.md", "c.md"):
        claude_project.note(name, f"See `dist/app.js`, `src/a.py` and `dist/{name}.js`.")
    calls = []
    real = gitio._run
    monkeypatch.setattr(gitio, "_run", lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    scan(claude_project.memory, ctx_for(claude_project), Store())
    assert calls.count("check-ignore") == 1


def test_a_project_outside_git_checks_every_mention(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src/a.py").write_text("a\n")
    plain = ClaudeProject(root)  # never git-initialised
    plain.note("n.md", "See `src/a.py`.")
    store = Store()
    scan(plain.memory, Context(root.resolve(), Path.home()), store)
    assert set(store.notes["n.md"].inferred) == {"src/a.py"}


OVERLOADED_TS = (
    "export function f(a: string): string;\nexport function f(a: number): number;\n"
    "export function f(a: any) {\n  return a;\n}\n\nfunction other() {\n  return 1;\n}\n"
)
LABEL = " (tracked at file level: this symbol could not be isolated)"


def test_a_file_level_fallback_says_so_in_the_changed_message(claude_project):
    claude_project.write("src/a.ts", OVERLOADED_TS)
    claude_project.note("a.md", "**Evidence:** `src/a.ts#f`, `src/a.ts`")
    store = Store()
    stamp(claude_project, store, "a.md", "src/a.ts#f")
    stamp(claude_project, store, "a.md", "src/a.ts")
    assert store.notes["a.md"].citations["src/a.ts#f"].fingerprint.fallback
    claude_project.write("src/a.ts", OVERLOADED_TS.replace("return 1", "return 2"))  # not f
    result = scan(claude_project.memory, ctx_for(claude_project), store)
    messages = {f.target: f.message for f in result.findings}
    assert messages["src/a.ts#f"] == f"src/a.ts#f changed after this memory was written{LABEL}."
    assert messages["src/a.ts"] == "src/a.ts changed after this memory was written."


CPP_STRUCT = "int a;\nstruct S {\n  int t;\n};\n"


def test_a_symbol_that_can_no_longer_be_isolated_says_so_in_the_changed_message(claude_project):
    # An upgrade: the stored fingerprint isolated the symbol, the scan now refuses it (a
    # conditional directive directly above), so the citation reads `changed` once. The message
    # must say that the whole file was compared, as it does for a fingerprint stored at file level.
    claude_project.write("src/a.cpp", CPP_STRUCT)
    claude_project.note("a.md", "**Evidence:** `src/a.cpp#S`")
    store = Store()
    stamp(claude_project, store, "a.md", "src/a.cpp#S")
    assert not store.notes["a.md"].citations["src/a.cpp#S"].fingerprint.fallback
    claude_project.write("src/a.cpp", "int a;\n#ifdef X\nstruct S {\n  int t;\n};\n#endif\n")
    result = scan(claude_project.memory, ctx_for(claude_project), store)
    assert [f.message for f in result.findings] == [
        f"src/a.cpp#S changed after this memory was written{LABEL}."
    ]


def test_an_edit_to_an_isolated_cpp_symbol_is_not_labelled_as_file_level(claude_project):
    claude_project.write("src/a.cpp", CPP_STRUCT)
    claude_project.note("a.md", "**Evidence:** `src/a.cpp#S`")
    store = Store()
    stamp(claude_project, store, "a.md", "src/a.cpp#S")
    claude_project.write("src/a.cpp", CPP_STRUCT.replace("int t", "long t"))
    result = scan(claude_project.memory, ctx_for(claude_project), store)
    assert [f.message for f in result.findings] == [
        "src/a.cpp#S changed after this memory was written."
    ]


def test_an_isolated_symbol_is_not_labelled_as_file_level(claude_project):
    claude_project.write("src/a.py", "def f():\n    return 1\n")
    claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    store = Store()
    stamp(claude_project, store, "a.md", "src/a.py#f")
    claude_project.write("src/a.py", "def f():\n    return 2\n")
    result = scan(claude_project.memory, ctx_for(claude_project), store)
    assert "file level" not in result.findings[0].message


def test_one_note_that_breaks_mention_finding_does_not_stop_the_scan(claude_project, monkeypatch):
    ignoring(claude_project, "dist/")
    claude_project.note("a.md", "See `src/a.py`.")
    claude_project.note("b.md", "boom `src/a.py`.")
    real = infer.mentions

    def mentions(text, skip=frozenset()):
        if "boom" in text:
            raise RuntimeError("odd note")
        return real(text, skip)

    monkeypatch.setattr(infer, "mentions", mentions)
    store = Store()
    result = scan(claude_project.memory, ctx_for(claude_project), store)
    assert [(f.note, f.state) for f in result.findings] == [("b.md", "check-error")]
    assert set(store.notes["a.md"].inferred) == {"src/a.py"}


def vanished(claude_project, body, *names):
    """A note written 2026-09-01 whose mentioned files existed then and were removed after."""
    for name in names:
        claude_project.write(name, "x\n")
    claude_project.commit_at("2026-08-01T00:00:00Z", "start")
    claude_project.note("n.md", body)
    for name in names:
        claude_project.remove(name)
    claude_project.commit_at("2026-09-10T00:00:00Z", "later")
    return scan(claude_project.memory, ctx_for(claude_project), Store())


def test_a_mention_that_the_note_says_was_deleted_is_not_reported_gone(claude_project):
    result = vanished(
        claude_project, "The `old/x.py` module was deleted in the cleanup.", "old/x.py"
    )
    assert found(result) == set()


def test_a_deleted_path_is_hidden_but_a_current_path_in_the_same_sentence_is_not(claude_project):
    body = "`old/x.py` was deleted; the guard now lives in `old/y.py`."
    result = vanished(claude_project, body, "old/x.py", "old/y.py")
    assert found(result) == {("gone", "old/y.py")}


@pytest.mark.parametrize(
    "name", ["TODO.md", "docs/get-started.md", "scripts/restore.sh", "how-to.md"]
)
def test_a_cue_word_in_the_file_name_does_not_hide_that_the_note_says_it_was_deleted(
    claude_project, name
):
    result = vanished(claude_project, f"`{name}` was deleted in the cleanup.", name)
    assert found(result) == set()


@pytest.mark.parametrize(
    "body",
    [
        "`old/x.py` was not deleted.",
        "If `old/x.py` is removed, update the index.",
        "`old/x.py` will be removed next month.",
        "`old/x.py` was deleted.\n\nSee `old/x.py` for the details.",
        "Has `old/x.py` been removed?",
        "`old/x.py` was deleted, but `old/x.py` is still referenced by CI.",
        "`old/x.py` calls the legacy shim, which was removed in 2.0.",
        "`old/x.py` moved the files to `data/`.",
        "`old/x.py` was removed and then restored.",
        "Nobody removed `old/x.py`.",
        "TODO: `old/x.py` moved to the new layout.",
        "`old/x.py` was removed if/when the migration finished.",
        "TODO/FIXME: `old/x.py` was removed.",
    ],
)
def test_a_deletion_statement_that_is_negated_conditional_future_or_partial_still_reports(
    claude_project, body
):
    assert found(vanished(claude_project, body, "old/x.py")) == {("gone", "old/x.py")}


def test_a_scan_stops_baselining_inside_a_note_at_the_deadline_and_the_next_scan_resumes(
    claude_project, monkeypatch
):
    for n in range(6):
        claude_project.write(f"src/f{n}.py", f"{n}\n")
    claude_project.write("src/cited.py", "c\n")
    claude_project.commit_at("2026-08-01T00:00:00Z")
    mentions = " ".join(f"`src/f{n}.py`" for n in range(6))
    claude_project.note("n.md", f"See {mentions}.\n\n**Evidence:** `src/cited.py`")
    store, ctx = Store(), ctx_for(claude_project)
    deadline = time.monotonic() + 1000.0
    calls = []

    def clock():  # three mentions are reached in time; the fourth finds the deadline passed
        calls.append(1)
        return deadline - 1 if len(calls) <= 3 else deadline + 1

    monkeypatch.setattr(infer, "time", types.SimpleNamespace(monotonic=clock))
    result = scan(claude_project.memory, ctx, store, deadline=deadline)
    record = store.notes["n.md"]
    assert result.partial
    assert set(record.inferred) == {"src/f0.py", "src/f1.py", "src/f2.py"}
    assert record.inferred_key is None  # not "done": the next scan picks up where this stopped
    assert ("unread", "src/cited.py") in found(result)  # the explicit checks still ran
    first = dict(record.inferred)

    monkeypatch.setattr(infer, "time", time)
    claude_project.write(
        "src/f0.py", "changed\n"
    )  # after the first baselines, before the second scan
    resumed = scan(claude_project.memory, ctx, store, deadline=time.monotonic() + 1000.0)
    assert not resumed.partial
    assert set(record.inferred) == {f"src/f{n}.py" for n in range(6)}
    assert {k: record.inferred[k] for k in first} == first  # the early baselines were kept
    assert record.inferred_key is not None
    assert ("inferred-changed", "src/f0.py") in found(resumed)  # judged against the first baseline


def test_the_mentions_a_note_tracks_are_worked_out_once_per_note_per_scan(
    claude_project, monkeypatch
):
    claude_project.write("src/a.py", "a\n")
    claude_project.commit_at("2026-08-01T00:00:00Z")
    for name in ("a.md", "b.md", "c.md"):
        claude_project.note(name, "See `src/a.py`.\n\n**Evidence:** `src/a.py`")
    calls = []
    real = coverage.tracked_mentions
    monkeypatch.setattr(coverage, "tracked_mentions", lambda *a: calls.append(1) or real(*a))
    scan(claude_project.memory, ctx_for(claude_project), Store())
    assert len(calls) == 3


class _VirtualClock:
    """A clock that moves only when git is asked something: one second per call."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


def test_explicit_citations_are_checked_before_inference_spends_the_budget(
    claude_project, monkeypatch
):
    for n in range(4):  # early notes: older memories with many mentions and no Evidence line
        for k in range(5):
            claude_project.write(f"src/m{n}_{k}.py", f"{n}{k}\n")
        mentions = " ".join(f"`src/m{n}_{k}.py`" for k in range(5))
        claude_project.note(f"a{n}.md", f"See {mentions}.")
    claude_project.write("src/late.py", "def f():\n    return 1\n")  # cited by the last note
    claude_project.commit_at("2026-08-01T00:00:00Z")
    claude_project.note("z.md", "f returns 1.\n\n**Evidence:** `src/late.py#f`")
    store, ctx = Store(), ctx_for(claude_project)
    stamp(claude_project, store, "z.md", "src/late.py#f")
    claude_project.write("src/late.py", "def f():\n    return 2\n")

    clock = _VirtualClock()
    real = gitio._run
    monkeypatch.setattr(
        gitio, "_run", lambda *a, **k: setattr(clock, "now", clock.now + 1) or real(*a, **k)
    )
    monkeypatch.setattr(checks, "time", clock)
    monkeypatch.setattr(infer, "time", clock)
    first = scan(claude_project.memory, ctx, store, deadline=9.5)  # a few git calls' worth

    assert first.partial
    assert ("changed", "src/late.py#f") in found(first)  # not starved by the notes before it
    notes = store.notes
    assert any(notes[f"a{n}.md"].inferred for n in range(4))  # what was left went to inference
    assert any(notes[f"a{n}.md"].inferred_key is None for n in range(4))  # but not all of it

    clock.now = 0.0  # a later session finishes the baselines
    second = scan(claude_project.memory, ctx, store, deadline=1000.0)
    assert not second.partial
    assert all(notes[f"a{n}.md"].inferred_key is not None for n in range(4))
    assert ("changed", "src/late.py#f") in found(second)


def test_a_cut_baselining_pass_does_not_leave_the_key_of_an_earlier_complete_one(
    claude_project, monkeypatch
):
    for name in ("a", "b", "n1", "n2"):
        claude_project.write(f"src/{name}.py", f"{name}\n")
    claude_project.commit_at("2026-08-01T00:00:00Z")
    store, ctx = Store(), ctx_for(claude_project)
    v1 = "See `src/a.py` and `src/b.py`."
    claude_project.note("n.md", v1)
    scan(claude_project.memory, ctx, store)  # complete: the key now stands for {a, b}
    assert store.notes["n.md"].inferred_key is not None

    v2 = "See `src/b.py`, `src/n1.py` and `src/n2.py`."
    claude_project.note("n.md", v2, modified="2026-09-20T12:00:00Z")
    deadline = time.monotonic() + 1000.0
    monkeypatch.setattr(infer, "time", types.SimpleNamespace(monotonic=lambda: deadline + 1))
    cut = scan(claude_project.memory, ctx, store, deadline=deadline)  # stops before n1
    assert cut.partial
    assert set(store.notes["n.md"].inferred) == {"src/b.py"}  # a's record went with its mention
    assert store.notes["n.md"].inferred_key is None  # and the scan did not finish, so no key

    monkeypatch.setattr(infer, "time", time)
    claude_project.note("n.md", v1, modified="2026-09-21T12:00:00Z")  # back to {a, b}
    assert not scan(claude_project.memory, ctx, store).partial
    claude_project.remove("src/a.py")
    assert ("gone", "src/a.py") in found(scan(claude_project.memory, ctx, store))


def test_a_scan_asks_once_per_repository_whether_it_has_merges(claude_project, monkeypatch):
    claude_project.write("src/a.py", "a\n")
    claude_project.commit_at("2026-08-01T00:00:00Z")
    for name in ("a.md", "b.md", "c.md"):
        claude_project.note(name, f"See `src/a.py` in {name}.")
    asked = []
    real = gitio.has_merges
    monkeypatch.setattr(gitio, "has_merges", lambda cwd: asked.append(cwd) or real(cwd))
    scan(claude_project.memory, ctx_for(claude_project), Store())
    assert len(asked) == 1  # three notes, one repository


def test_a_note_time_beyond_the_calendar_does_not_leave_the_scan_partial(claude_project):
    claude_project.write("src/a.py", "a\n")
    claude_project.commit_at("2026-08-01T00:00:00Z")
    # 9999-12-31T23:59:59-05:00 is already in the year 10000 in UTC: no commit can be found for it
    claude_project.note("n.md", "See `src/a.py`.", modified="9999-12-31T23:59:59-05:00")
    store, ctx = Store(), ctx_for(claude_project)
    for _ in range(2):  # the second scan must not try again and fall short again
        result = scan(claude_project.memory, ctx, store, deadline=time.monotonic() + 1000.0)
        assert not result.partial
        assert store.notes["n.md"].inferred_key is not None
        assert result.findings == []


def _git_clock(monkeypatch):
    """A virtual clock that moves one second for every git call, for the scan and for inference."""
    clock = _VirtualClock()
    real = gitio._run
    monkeypatch.setattr(
        gitio, "_run", lambda *a, **k: setattr(clock, "now", clock.now + 1) or real(*a, **k)
    )
    monkeypatch.setattr(checks, "time", clock)
    monkeypatch.setattr(infer, "time", clock)
    return clock


def _with_a_merge(project):
    """A history with one merge commit, from before the notes are written."""
    project.write("src/b.py", "b\n")
    project.write("src/c.py", "c\n")
    project.commit_at("2026-08-01T00:00:00Z", "base")
    project.git("switch", "-q", "-c", "side")
    project.write("src/c.py", "c2\n")
    project.commit_at("2026-08-02T00:00:00Z", "side")
    project.git("switch", "-q", "main")
    project.write("src/b.py", "b2\n")
    project.commit_at("2026-08-03T00:00:00Z", "main")
    project.git("merge", "--no-ff", "-q", "-m", "merge", "side")


def _sessions_to_converge(project, store, ctx, clock, deadline, limit=10):
    """How many sessions, each with its own ``deadline``, until a scan is no longer partial."""
    for session in range(1, limit + 1):
        clock.now = 0.0
        if not scan(project.memory, ctx, store, deadline=deadline).partial:
            return session
    return None


def test_a_note_of_mentions_that_resolve_to_no_record_converges_in_a_repository_with_merges(
    claude_project, monkeypatch
):
    _with_a_merge(claude_project)
    gone = " ".join(f"`src/old{n}.py`" for n in range(14))  # never existed: no record each
    claude_project.note("a.md", f"Once there were {gone}.")
    claude_project.note("b.md", "See `src/b.py`.")  # sorts after a.md, which must not starve it
    store, ctx = Store(), ctx_for(claude_project)
    clock = _git_clock(monkeypatch)
    sessions = _sessions_to_converge(claude_project, store, ctx, clock, deadline=9.5)
    assert sessions is not None and sessions <= 5  # a pass that restarted would never finish
    assert store.notes["a.md"].inferred_key is not None
    assert set(store.notes["b.md"].inferred) == {"src/b.py"}  # the note after it got its baseline
    assert store.notes["b.md"].inferred_key is not None


def test_a_cut_pass_remembers_the_mentions_that_have_no_record_for_its_own_mentions_only(
    claude_project, monkeypatch
):
    _with_a_merge(claude_project)
    gone = " ".join(f"`src/old{n}.py`" for n in range(14))
    claude_project.note("a.md", f"Once there were {gone}.")
    store, ctx = Store(), ctx_for(claude_project)
    clock = _git_clock(monkeypatch)
    clock.now = 0.0
    assert scan(claude_project.memory, ctx, store, deadline=9.5).partial
    record = store.notes["a.md"]
    assert record.inferred_key is None  # cut
    assert 0 < len(record.no_record) < 14
    assert record.no_record_key is not None
    first = list(record.no_record)

    clock.now = 0.0
    scan(claude_project.memory, ctx, store, deadline=9.5)
    assert set(first) < set(record.no_record)  # the next pass went on from there

    clock.now = 0.0
    assert not scan(claude_project.memory, ctx, store, deadline=1000.0).partial
    assert record.inferred_key is not None
    assert (record.no_record, record.no_record_key) == ([], None)  # done: the key says it all


def test_the_mentions_without_a_record_are_forgotten_when_the_mentions_of_the_note_change(
    claude_project, monkeypatch
):
    _with_a_merge(claude_project)
    gone = " ".join(f"`src/old{n}.py`" for n in range(14))
    claude_project.note("a.md", f"Once there were {gone}.")
    store, ctx = Store(), ctx_for(claude_project)
    clock = _git_clock(monkeypatch)
    clock.now = 0.0
    assert scan(claude_project.memory, ctx, store, deadline=9.5).partial
    record = store.notes["a.md"]
    assert "src/old0.py" in record.no_record  # looked at, nothing there

    claude_project.write("src/old0.py", "now it exists\n")  # a later commit's worth of change
    claude_project.note(
        "a.md", f"Once there were {gone} and `src/new.py`.", modified="2026-09-20T12:00:00Z"
    )
    clock.now = 0.0
    assert not scan(claude_project.memory, ctx, store, deadline=1000.0).partial
    # the note is another set of mentions: nothing it learned about the old one is believed
    assert "src/old0.py" in record.inferred
    assert (record.no_record, record.no_record_key) == ([], None)


def test_scans_cut_short_in_a_repository_with_merges_end_as_one_unlimited_scan_would(
    claude_project, monkeypatch
):
    _with_a_merge(claude_project)
    for n in range(10):  # each note: four files that exist, four names that never did
        for k in range(4):
            claude_project.write(f"src/t{n}_{k}.py", f"{n}{k}\n")
    claude_project.commit_at("2026-08-10T00:00:00Z", "the files the notes mention")
    for n in range(10):
        mentions = [f"src/t{n}_{k}.py" for k in range(4)] + [f"src/old{n}_{k}.py" for k in range(4)]
        claude_project.note(f"n{n:02d}.md", "See " + " ".join(f"`{m}`" for m in mentions) + ".")
    cut, ctx = Store(), ctx_for(claude_project)
    clock = _git_clock(monkeypatch)
    sessions = _sessions_to_converge(claude_project, cut, ctx, clock, deadline=25.5)
    assert sessions is not None and 1 < sessions <= 8

    whole = Store()
    clock.now = 0.0
    assert not scan(claude_project.memory, ctx, whole, deadline=10_000.0).partial
    for name, record in whole.notes.items():
        assert (
            cut.notes[name].inferred == record.inferred
        )  # the same baselines, none lost or doubled
        assert len(record.inferred) == 4
        assert cut.notes[name].inferred_key == record.inferred_key
