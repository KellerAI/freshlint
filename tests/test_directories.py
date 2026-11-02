"""Directory citations (`dir/`): a manifest of the files git would track under the directory."""

import datetime as dt
import json
from pathlib import Path

import pytest

from conftest import ClaudeProject
from freshlint import coverage, hooks
from freshlint.checks import Context, scan
from freshlint.evidence import parse_citation as cite
from freshlint.fingerprint import (
    CHANGED,
    DIR_MAX_BYTES,
    DIR_MAX_FILES,
    FRESH,
    GONE,
    TargetMissing,
    compare,
    directory_changes,
    file_digest,
    take,
)
from freshlint.ledger import raw_digest
from freshlint.memory import read_note
from freshlint.save import on_save
from freshlint.store import Store, store_path

TODAY = dt.date(2026, 10, 1)


def ctx(project):
    return Context(project.path.resolve(), Path.home())


def read(*paths):
    return {str(p.resolve()): raw_digest(p) for p in paths}


@pytest.fixture
def tree(repo):
    repo.write("src/x/a.py", "a = 1\n")
    repo.write("src/x/b.py", "b = 2\n")
    return repo


def fp(tree):
    return take(cite("src/x/"), tree.path / "src/x")


def test_a_trailing_slash_directory_takes_a_manifest_of_its_files(tree):
    got = fp(tree)
    assert got.kind == "dir"
    assert got.entries == (("a.py", file_digest("a = 1\n")), ("b.py", file_digest("b = 2\n")))


def test_editing_a_file_inside_is_changed_and_names_it(tree):
    stored = fp(tree)
    tree.write("src/x/a.py", "a = 3\n")
    assert compare(cite("src/x/"), stored, tree.path / "src/x") == CHANGED
    assert directory_changes(stored, tree.path / "src/x") == ["a.py edited"]


def test_adding_or_removing_a_file_is_changed_and_named(tree):
    stored = fp(tree)
    tree.write("src/x/c.py", "c = 1\n")
    assert compare(cite("src/x/"), stored, tree.path / "src/x") == CHANGED
    assert directory_changes(stored, tree.path / "src/x") == ["c.py added"]
    tree.remove("src/x/c.py")
    tree.remove("src/x/b.py")
    assert compare(cite("src/x/"), stored, tree.path / "src/x") == CHANGED
    assert directory_changes(stored, tree.path / "src/x") == ["b.py removed"]


def test_trailing_whitespace_line_endings_and_edge_blank_lines_stay_fresh(tree):
    stored = fp(tree)
    (tree.path / "src/x/a.py").write_bytes(b"\n\na = 1   \r\n\n\n")
    assert compare(cite("src/x/"), stored, tree.path / "src/x") == FRESH


def test_re_indenting_a_file_inside_is_changed(tree):
    stored = fp(tree)
    tree.write("src/x/a.py", "  a = 1\n")
    assert compare(cite("src/x/"), stored, tree.path / "src/x") == CHANGED


def test_editing_a_git_ignored_file_stays_fresh(tree):
    tree.write(".gitignore", "*.log\n")
    tree.write("src/x/run.log", "one\n")
    stored = fp(tree)
    assert [rel for rel, _ in stored.entries] == ["a.py", "b.py"]
    tree.write("src/x/run.log", "two\n")
    assert compare(cite("src/x/"), stored, tree.path / "src/x") == FRESH


def test_deleting_the_directory_is_gone(tree):
    stored = fp(tree)
    for name in ("a.py", "b.py"):
        tree.remove(f"src/x/{name}")
    (tree.path / "src/x").rmdir()
    assert compare(cite("src/x/"), stored, tree.path / "src/x") == GONE


TOO_LARGE = "src/x/ has more than 50 files or 2 MB: too large; cite a narrower directory or files"


def test_more_files_than_the_cap_is_refused(repo):
    for i in range(DIR_MAX_FILES + 1):
        repo.write(f"src/x/f{i:02}.py", "x\n")
    with pytest.raises(TargetMissing) as exc:
        take(cite("src/x/"), repo.path / "src/x")
    assert str(exc.value) == TOO_LARGE


def test_exactly_the_file_cap_is_accepted(repo):
    for i in range(DIR_MAX_FILES):
        repo.write(f"src/x/f{i:02}.py", "x\n")
    assert len(take(cite("src/x/"), repo.path / "src/x").entries) == DIR_MAX_FILES


def test_more_bytes_than_the_cap_is_refused(repo):
    repo.write("src/x/big.txt", "x" * (DIR_MAX_BYTES // 2))
    repo.write("src/x/big2.txt", "x" * (DIR_MAX_BYTES // 2 + 1))
    with pytest.raises(TargetMissing) as exc:
        take(cite("src/x/"), repo.path / "src/x")
    assert str(exc.value) == TOO_LARGE


def test_a_directory_outside_a_git_repo_is_refused(project):
    project.write("src/x/a.py", "a = 1\n")
    with pytest.raises(TargetMissing) as exc:
        take(cite("src/x/"), project.path / "src/x")
    assert str(exc.value) == "src/x/ is not in a git repo: directory citations need a git repo"


def test_an_empty_or_all_ignored_directory_is_refused(repo):
    (repo.path / "src/x").mkdir(parents=True)
    with pytest.raises(TargetMissing) as exc:
        take(cite("src/x/"), repo.path / "src/x")
    assert str(exc.value) == "src/x/ has no tracked files"
    repo.write(".gitignore", "*.log\n")
    repo.write("src/x/run.log", "x\n")
    with pytest.raises(TargetMissing, match="^src/x/ has no tracked files$"):
        take(cite("src/x/"), repo.path / "src/x")


def test_a_directory_citation_covers_mentions_of_files_inside_it(tree):
    cited = frozenset({str((tree.path / "src/x").resolve())})
    root = tree.path.resolve()
    assert coverage.covers(cited, "src/x/a.py", root, Path.home())
    assert coverage.covers(cited, "src/x", root, Path.home())
    assert not coverage.covers(cited, "src/xy/a.py", root, Path.home())
    assert not coverage.covers(cited, "src/a.py", root, Path.home())


# ---------------------------------------------------------------- the save hook and session start
@pytest.fixture
def ctree(claude_project):
    a = claude_project.write("src/x/a.py", "a = 1\n")
    b = claude_project.write("src/x/b.py", "b = 2\n")
    return claude_project, a, b


def save(project, store, reads, body="**Evidence:** `src/x/`"):
    note = read_note(project.note("a.md", body))
    return on_save(note, store, reads, ctx(project), TODAY)


def test_a_directory_whose_files_were_all_read_is_stamped_silently(ctree):
    project, a, b = ctree
    store = Store()
    out = save(project, store, read(a, b))
    assert (out.feedback, out.renewed) == ([], [])
    record = store.notes["a.md"].citations["src/x/"]
    assert record.fingerprint.kind == "dir" and record.stamped == "2026-10-01"


def test_a_directory_with_an_unread_file_is_not_stamped(ctree):
    project, a, _ = ctree
    store = Store()
    out = save(project, store, read(a))
    assert len(out.feedback) == 1
    assert out.feedback[0].startswith(
        "a.md cites src/x/, a directory, but you have not read every file in it in this session."
    )
    assert store.notes["a.md"].citations["src/x/"].fingerprint is None


def test_the_read_gate_compares_raw_digests_and_the_manifest_normalised_ones(ctree):
    project, a, b = ctree
    a.write_bytes(b"a = 1   \r\n\r\n")  # raw bytes differ from the normalised text
    assert raw_digest(a) != file_digest(a.read_text())
    store = Store()
    out = save(project, store, read(a, b))
    assert out.feedback == []  # no false "changed after you read it"
    record = store.notes["a.md"].citations["src/x/"]
    assert dict(record.fingerprint.entries)["a.py"] == file_digest("a = 1\n")


def test_a_file_changed_after_its_read_is_reported_and_not_stamped(ctree):
    project, a, b = ctree
    seen = read(a, b)
    a.write_text("a = 9\n")
    store = Store()
    out = save(project, store, seen)
    assert out.feedback == [
        "a.md cites src/x/, and a file in it changed after you read it. "
        "Read it again before citing it."
    ]
    record = store.notes["a.md"].citations["src/x/"]
    assert (record.fingerprint, record.problem) == (None, "changed")


def test_a_refused_directory_says_why_on_save_and_at_session_start(ctree):
    project, *_ = ctree
    for i in range(DIR_MAX_FILES):
        project.write(f"src/x/f{i:02}.py", "x\n")
    store = Store()
    out = save(project, store, {})
    assert out.feedback == [f"a.md cites src/x/, but {TOO_LARGE}."]
    result = scan(project.memory, ctx(project), store)
    assert [(f.state, f.message) for f in result.findings] == [
        (
            "unread",
            f"cites src/x/, but when the memory was saved {TOO_LARGE}, so it was never "
            "fingerprinted.",
        )
    ]


def test_a_directory_left_unread_at_save_says_so_at_session_start(ctree):
    project, a, _ = ctree
    store = Store()
    save(project, store, read(a))
    result = scan(project.memory, ctx(project), store)
    assert [(f.state, f.message) for f in result.findings] == [
        (
            "unread",
            "cites src/x/, a directory, but not every file in it was read when the memory was "
            "saved, so it was never fingerprinted.",
        )
    ]


def test_session_start_names_the_files_that_changed(ctree):
    project, a, b = ctree
    store = Store()
    save(project, store, read(a, b))
    assert scan(project.memory, ctx(project), store).findings == []
    a.write_text("a = 5\n")
    project.write("src/x/c.py", "c\n")
    result = scan(project.memory, ctx(project), store)
    assert [(f.state, f.message) for f in result.findings] == [
        ("changed", "src/x/ changed after this memory was written (a.py edited, c.py added).")
    ]


# ---------------------------------------------------------------- the real hooks, end to end
def send(project, event, session, **fields):
    payload = {"hook_event_name": event, "session_id": session, "cwd": str(project.path), **fields}
    out = hooks.run(json.dumps(payload), today=TODAY)
    return json.loads(out) if out else None


def tool(project, name, path, session):
    return send(
        project,
        "PostToolUse",
        session,
        tool_name=name,
        tool_input={"file_path": str(path)},
        tool_response={},
    )


def test_hooks_end_to_end_quiet_until_a_file_changes_then_name_it(ctree):
    project, a, b = ctree
    project.commit("code")
    send(project, "SessionStart", "s1", source="startup")
    tool(project, "Read", a, "s1")
    tool(project, "Read", b, "s1")
    note = project.note("x.md", "The x package is small.\n\n**Evidence:** `src/x/`")
    assert tool(project, "Write", note, "s1") is None
    assert send(project, "SessionStart", "s2", source="startup") is None
    b.write_text("b = 3\n")
    out = send(project, "SessionStart", "s3", source="startup")
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "src/x/ changed after this memory was written (b.py edited)." in text


# ---------------------------------------------------------------- a bare `dir` is taken as `dir/`
def test_a_bare_directory_is_taken_as_a_directory_citation_at_save(ctree):
    project, a, b = ctree
    store = Store()
    note = project.note("a.md", "**Evidence:** `src/x`")
    out = on_save(read_note(note), store, read(a, b), ctx(project), TODAY)
    assert out.feedback == []
    assert store.notes["a.md"].citations["src/x"].fingerprint.kind == "dir"
    assert note.read_text().endswith("`src/x`\n")  # the note is never rewritten
    assert scan(project.memory, ctx(project), store).findings == []
    b.write_text("b = 0\n")
    assert [f.message for f in scan(project.memory, ctx(project), store).findings] == [
        "src/x changed after this memory was written (b.py edited)."
    ]


def test_a_bare_directory_over_the_cap_is_refused_like_a_slash_one(ctree):
    project, *_ = ctree
    for i in range(DIR_MAX_FILES):
        project.write(f"src/x/f{i:02}.py", "x\n")
    out = save(project, Store(), {}, body="**Evidence:** `src/x`")
    assert out.feedback == [f"a.md cites src/x, but {TOO_LARGE}."]


def test_a_bare_directory_outside_git_is_refused_like_a_slash_one(tmp_path):
    (tmp_path / "plain").mkdir()
    project = ClaudeProject(tmp_path / "plain")  # never `git init`
    project.write("src/x/a.py", "a = 1\n")
    note = read_note(project.note("a.md", "**Evidence:** `src/x`"))
    out = on_save(note, Store(), {}, ctx(project), TODAY)
    assert out.feedback == [
        "a.md cites src/x, but src/x/ is not in a git repo: directory citations need a git repo."
    ]


def test_hooks_an_old_directory_record_shows_until_re_saved_after_reading_every_file(ctree):
    project, a, b = ctree
    project.commit("code")
    note = project.note("x.md", "The x package.\n\n**Evidence:** `src/x`")
    path = store_path(project.memory)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = "missing:src/x is a directory; cite a file inside it"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "notes": {"x.md": {"citations": {"src/x": {"fingerprint": None, "problem": old}}}},
            }
        )
    )
    started = send(project, "SessionStart", "s1", source="startup")
    assert "src/x is a directory; cite a file inside it" in json.dumps(started)
    send(project, "SessionStart", "s2", source="startup")
    assert "src/x is a directory" in json.dumps(send(project, "SessionStart", "s2", source="x"))
    tool(project, "Read", a, "s3")
    tool(project, "Read", b, "s3")
    assert tool(project, "Write", note, "s3") is None
    assert send(project, "SessionStart", "s4", source="startup") is None
