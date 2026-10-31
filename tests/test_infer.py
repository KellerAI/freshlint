import datetime as dt
import os
import random
import subprocess
import types

import pytest

from conftest import Project
from freshlint import gitio, infer
from freshlint.fingerprint import file_digest
from freshlint.store import InferredRecord


def ts(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def test_mentions_find_paths_and_skip_noise():
    text = (
        "---\nname: x\n---\n"
        "Deploy with `infra/deploy.sh` (see docs/runbook.md). Config in ~/acme/settings.toml and pyproject.toml.\n"
        "https://example.com/a/b and v1.2.3 and e.g. and a@b.com\n"
        "Evidence: `src/explicit.py`\n"
    )
    assert infer.mentions(text, frozenset({6})) == [
        "infra/deploy.sh",
        "docs/runbook.md",
        "~/acme/settings.toml",
        "pyproject.toml",
    ]


def test_git_baseline_detects_a_change_after_the_note(repo):
    repo.write("src/a.py", "one\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    repo.write("src/a.py", "two\n")
    repo.commit_at("2026-09-10T10:00:00Z")
    path = repo.path / "src/a.py"
    early = ts("2026-09-05T00:00:00Z")
    record = infer.baseline(path, early)
    assert (record.kind, record.method) == ("file", "git")
    assert infer.state(record, path, early) == infer.CHANGED
    late = ts("2026-09-11T00:00:00Z")
    assert infer.state(infer.baseline(path, late), path, late) == infer.FRESH


def test_a_file_deleted_after_the_note_is_gone(repo):
    repo.write("old/x.py", "x\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    repo.remove("old/x.py")
    repo.commit_at("2026-09-10T10:00:00Z")
    when = ts("2026-09-05T00:00:00Z")
    record = infer.baseline(repo.path / "old/x.py", when)
    assert record is not None and record.method == "git"
    assert infer.state(record, repo.path / "old/x.py", when) == infer.GONE


def test_a_directory_that_moved_is_gone(repo):
    repo.write("next-app/src/page.tsx", "export default 1\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    repo.git("mv", "next-app", "web")
    repo.commit_at("2026-09-10T10:00:00Z")
    when = ts("2026-09-05T00:00:00Z")
    record = infer.baseline(repo.path / "next-app", when)
    assert record == InferredRecord("dir", "git", None, when)
    assert infer.state(record, repo.path / "next-app", when) == infer.GONE


def test_paths_that_never_existed_are_ignored(repo):
    repo.write("a.txt", "a\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    assert infer.baseline(repo.path / "feature/branch-name", ts("2026-09-05T00:00:00Z")) is None


def test_untracked_files_use_modification_times(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("x")
    os.utime(path, (1000, 1000))
    record = infer.baseline(path, 2000.0)
    assert record == InferredRecord("file", "mtime", None, 2000.0)
    assert infer.state(record, path, 2000.0) == infer.FRESH
    os.utime(path, (3000, 3000))
    assert infer.state(record, path, 2000.0) == infer.CHANGED


def test_a_non_utf8_file_is_reported_changed_even_when_untouched(repo):
    target = repo.path / "blob.bin"
    target.write_bytes(b"\xff\xfe\x00binary\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    when = ts("2026-09-05T00:00:00Z")
    record = infer.baseline(target, when)
    assert (record.kind, record.method) == ("file", "git")
    assert infer.state(record, target, when) == infer.CHANGED


def test_bare_dot_and_home_tokens_are_not_paths():
    assert infer.mentions("see ./ and ../ and ~/") == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unreadable_directory_is_not_checked_and_never_gone(tmp_path, pathlib_flavour):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "x.py").write_text("x")
    locked.chmod(0)
    try:
        path = locked / "x.py"
        assert infer.baseline(path, 2000.0) is None
        for record in (
            InferredRecord("file", "git", "abc"),
            InferredRecord("file", "mtime", None),
            InferredRecord("dir", "git", None),
        ):
            assert infer.state(record, path, 2000.0) == infer.FRESH
    finally:
        locked.chmod(0o700)


def test_a_symlink_loop_is_not_checked(repo):
    repo.write("a.txt", "a\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    (repo.path / "loop-a").symlink_to("loop-b")
    (repo.path / "loop-b").symlink_to("loop-a")
    assert infer.baseline(repo.path / "loop-a", ts("2026-09-05T00:00:00Z")) is None


def test_a_git_record_without_a_baseline_is_never_fresh(tmp_path):
    folder = tmp_path / "now-a-folder"
    folder.mkdir()
    assert infer.state(InferredRecord("file", "git", None), folder, 2000.0) == infer.CHANGED


def test_the_repository_root_is_looked_up_once_per_directory(repo, monkeypatch):
    repo.write("src/a.py", "a\n")
    repo.write("src/b.py", "b\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    infer._toplevel.cache_clear()
    calls = []
    real = gitio.toplevel
    monkeypatch.setattr(gitio, "toplevel", lambda path: calls.append(path) or real(path))
    when = ts("2026-09-05T00:00:00Z")
    assert infer.baseline(repo.path / "src/a.py", when) is not None
    assert infer.baseline(repo.path / "src/b.py", when) is not None
    assert len(calls) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_an_unreadable_file_is_never_reported_changed(tmp_path):
    path = tmp_path / "secret.py"
    path.write_text("x")
    path.chmod(0)
    try:
        assert infer.state(InferredRecord("file", "git", "abc"), path, 2000.0) == infer.FRESH
    finally:
        path.chmod(0o600)


BEYOND_THE_CALENDAR = [253402300800.0, 253402318799.0, 1e18, 1e30, float("nan"), float("inf")]


@pytest.mark.parametrize("when", BEYOND_THE_CALENDAR)
def test_a_note_time_beyond_the_calendar_is_not_checked(repo, when):
    repo.write("src/a.py", "a\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    assert infer.baseline(repo.path / "src/a.py", when) is None


@pytest.mark.parametrize("when", BEYOND_THE_CALENDAR)
def test_a_note_time_git_cannot_take_leaves_the_pass_complete(repo, when):
    repo.write("src/a.py", "a\n")
    repo.write("src/b.py", "b\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    items = [("src/a.py", repo.path / "src/a.py"), ("src/b.py", repo.path / "src/b.py")]
    unrecorded: set[str] = set()
    records, complete = infer.baselines(items, when, unrecorded=unrecorded)
    assert records == {}  # untrackable: nothing to compare against
    assert complete  # and nothing left to try, so a later scan has nothing to take up
    assert unrecorded == {"src/a.py", "src/b.py"}  # looked at, done


def test_a_failure_of_git_while_baselining_is_incomplete_so_the_next_scan_tries_again(
    repo, monkeypatch
):
    repo.write("src/a.py", "a\n")
    repo.commit_at("2026-09-01T10:00:00Z")

    def broken(*_args):
        raise gitio.GitError("simulated")

    monkeypatch.setattr(gitio, "objects_at", broken)
    items = [("src/a.py", repo.path / "src/a.py")]
    unrecorded: set[str] = set()
    assert infer.baselines(items, ts("2026-09-05T00:00:00Z"), unrecorded=unrecorded) == ({}, False)
    assert unrecorded == set()  # not looked at: nothing may say "done" for it


def test_a_record_keeps_the_time_its_baseline_was_taken(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("x")
    os.utime(path, (3000, 3000))
    record = infer.baseline(path, 2000.0)
    # The note is re-saved later, but the baseline is still the earlier time.
    assert infer.state(record, path, 9000.0) == infer.CHANGED
    assert infer.state(InferredRecord("file", "mtime", None), path, 9000.0) == infer.FRESH


def test_check_ignore_reports_ignored_paths_in_one_call(repo):
    repo.write(".gitignore", "dist/\n*.cfg\n")
    repo.write("dist/app.js", "x\n")
    repo.write("src/a.py", "a\n")
    repo.write("tracked.cfg", "t\n")
    repo.git("add", "-f", "tracked.cfg")
    repo.commit_at("2026-09-01T10:00:00Z")
    paths = ["dist/app.js", "src/a.py", "tracked.cfg", "other.cfg", "never/there.py"]
    assert gitio.check_ignore(repo.path, paths) == {"dist/app.js", "other.cfg"}
    assert gitio.check_ignore(repo.path, []) == set()


def test_check_ignore_answers_only_the_paths_git_can_answer(repo, tmp_path):
    repo.write(".gitignore", "dist/\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    assert gitio.check_ignore(repo.path, ["../outside.js"]) == set()  # fatal in git
    assert gitio.check_ignore(repo.path, ["dist/a.js", "../outside.js"]) == {"dist/a.js"}
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gitio.check_ignore(plain, ["dist/a.js"]) == set()


def test_check_ignore_answers_for_the_other_paths_when_one_cannot_be_answered(repo):
    repo.write(".gitignore", "dist/\n*.cfg\n")
    repo.write("real/f.py", "f\n")
    repo.write("src/a.py", "a\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    (repo.path / "linked").symlink_to("real", target_is_directory=True)  # "beyond a symbolic link"
    for paths in (["dist/a.js", "linked/f.py", "src/a.py", "x.cfg"], ["linked/f.py", "dist/a.js"]):
        assert gitio.check_ignore(repo.path, paths) == set(paths) & {"dist/a.js", "x.cfg"}
    assert gitio.check_ignore(repo.path, ["linked/f.py"]) == set()


def _count_check_ignore_calls(monkeypatch):
    calls = []
    real = gitio._run
    monkeypatch.setattr(gitio, "_run", lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    return calls


def _ignore_fixture(repo):
    repo.write(".gitignore", "dist/\n")
    repo.write("real/f.py", "f\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    (repo.path / "linked").symlink_to("real", target_is_directory=True)


def test_check_ignore_costs_one_call_when_git_can_answer_every_path(repo, monkeypatch):
    _ignore_fixture(repo)
    calls = _count_check_ignore_calls(monkeypatch)
    paths = [f"dist/{n}.js" for n in range(64)]
    assert gitio.check_ignore(repo.path, paths) == set(paths)
    assert calls.count("check-ignore") == 1


def test_check_ignore_outside_a_repository_costs_two_calls_however_many_paths(
    tmp_path, monkeypatch
):
    calls = _count_check_ignore_calls(monkeypatch)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gitio.check_ignore(plain, [f"dist/{n}.js" for n in range(64)]) == set()
    assert calls.count("check-ignore") == 2  # the batch, then one probe for a repository


def test_check_ignore_one_bad_path_among_many(repo, monkeypatch):
    _ignore_fixture(repo)
    paths = [f"dist/{n}.js" for n in range(63)]
    paths.insert(40, "linked/f.py")
    calls = _count_check_ignore_calls(monkeypatch)
    assert gitio.check_ignore(repo.path, paths) == set(paths) - {"linked/f.py"}
    assert calls.count("check-ignore") == 14  # batch, probe, then two halves per level


def test_check_ignore_with_every_path_unanswerable_stops_at_the_cap(repo, monkeypatch):
    _ignore_fixture(repo)
    paths = [f"linked/{n}.py" for n in range(140)]
    calls = _count_check_ignore_calls(monkeypatch)
    assert gitio.check_ignore(repo.path, paths) == set()
    assert calls.count("check-ignore") == 17  # the batch, plus exactly 16 extra calls


def test_check_ignore_spends_its_budget_on_both_halves_before_splitting_either(repo, monkeypatch):
    _ignore_fixture(repo)
    paths = [f"dist/{n}.js" for n in range(1000)]
    paths[0] = "linked/f.py"  # the bad path sits in the first half at every level
    calls = _count_check_ignore_calls(monkeypatch)
    found = gitio.check_ignore(repo.path, paths)
    assert calls.count("check-ignore") == 17
    assert found <= set(paths) - {"linked/f.py"}  # never a wrong yes
    assert len(found) >= 990  # the good half of every level was asked before the bad one split


def test_looking_at_a_repository_never_rewrites_its_index(repo):
    repo.write("a.txt", "a\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    os.utime(repo.path / "a.txt", (1_000_000_000, 1_000_000_000))  # stale stat data in the index
    index = repo.path / ".git" / "index"
    before = index.read_bytes()
    assert gitio.is_modified(repo.path, "a.txt") is False
    assert index.read_bytes() == before  # a refresh would take .git/index.lock and rewrite it


def test_git_is_told_not_to_take_optional_locks(repo, monkeypatch):
    seen = []
    real = gitio.subprocess.run
    monkeypatch.setattr(
        gitio.subprocess, "run", lambda *a, **k: seen.append(k["env"]) or real(*a, **k)
    )
    gitio.toplevel(repo.path)
    assert [env["GIT_OPTIONAL_LOCKS"] for env in seen] == ["0"]


def test_is_modified_compares_the_working_file_with_head(repo):
    repo.write("a.txt", "a\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    assert gitio.is_modified(repo.path, "a.txt") is False
    os.utime(repo.path / "a.txt", (1_000_000_000, 1_000_000_000))  # touched, not edited
    assert gitio.is_modified(repo.path, "a.txt") is False
    repo.write("a.txt", "b\n")
    assert gitio.is_modified(repo.path, "a.txt") is True
    repo.stage("a.txt")  # staged is still different from HEAD
    assert gitio.is_modified(repo.path, "a.txt") is True
    repo.write("a.txt", "a\n")  # back to HEAD in the working tree, though the index differs
    assert gitio.is_modified(repo.path, "a.txt") is False
    repo.write("new.txt", "n\n")
    assert gitio.is_modified(repo.path, "new.txt") is True  # not in HEAD
    (repo.path / "a.txt").unlink()
    assert gitio.is_modified(repo.path, "a.txt") is True


def _historical(text, skip=frozenset()):
    return infer.historical_mentions(text, skip)


def test_a_sentence_that_says_two_paths_were_deleted_covers_both_across_a_line_break():
    text = (
        "Audited in August: `tools/old_alpha` and\n`tools/old_beta` were deleted (empty leftovers)."
    )
    assert _historical(text) == {"tools/old_alpha", "tools/old_beta"}


def test_a_list_of_paths_before_was_deleted_is_covered():
    assert _historical(
        "The old mascot (`images/mascot.svg`, `assets/mascot.png`) was deleted."
    ) == {
        "images/mascot.svg",
        "assets/mascot.png",
    }


def test_a_path_that_is_gone_is_covered():
    text = "The presets moved into the app folder. `presets_wip/` is gone. See the README."
    assert _historical(text) == {"presets_wip"}


def test_every_occurrence_in_a_deleting_sentence_is_covered_even_after_a_dash_and_an_if():
    text = (
        "The `presets_wip/README.md` that listed each preset was deleted with the folder"
        " — recover it from history (`git show abc1234~1:presets_wip/README.md`)"
        " if the per-preset notes are wanted."
    )
    assert _historical(text) == {"presets_wip/README.md"}


@pytest.mark.parametrize(
    "sentence",
    [
        "`docs/old-guide.md` no longer exists.",
        "`docs/old-guide.md` was renamed.",
        "`docs/old-guide.md` was moved last week.",
        "`docs/old-guide.md` was later retired.",
        "`docs/old-guide.md` has been removed.",
        "We deleted `docs/old-guide.md` in the cleanup.",
        "Removed the empty folder `docs/old-guide.md`.",
        "`docs/old-guide.md` and `docs/old-faq.md` were both removed.",
    ],
)
def test_past_tense_statements_cover_the_path(sentence):
    assert "docs/old-guide.md" in _historical(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "`old/a.py` was not deleted, it still builds the docs.",
        "`old/a.py` was never removed.",
        "`old/a.py` wasn't moved.",
        "If `old/a.py` was removed, update the index.",
        "When `old/a.py` was deleted the build broke.",
        "`old/a.py` will be removed next month.",
        "`old/a.py` should be deleted.",
        "`old/a.py` is to be removed.",
        "`old/a.py` can be deleted once the port lands.",
        "`old/a.py` is removed by the nightly cleanup.",
        "Delete `old/a.py` and remove its entry.",
        "`old/a.py` is the file to rename.",
        "See `old/a.py` for the details.",
    ],
)
def test_negated_conditional_modal_and_present_statements_do_not_cover_the_path(sentence):
    assert _historical(sentence) == set()


def test_a_sentence_that_deletes_one_path_does_not_cover_a_current_path_it_mentions():
    assert _historical("`old/a.py` was deleted; the guard now lives in `new/b.py`.") == {"old/a.py"}
    assert _historical("`old/a.py` was deleted and its tests moved to `new/b.py`.") == {"old/a.py"}
    assert _historical("`old/a.py` was moved to `new/b.py`.") == {"old/a.py"}
    assert _historical("`new/b.py` replaced `old/a.py`, which was removed.") == {"old/a.py"}


def test_one_pointer_to_the_path_keeps_it_reportable():
    text = "`old/a.py` was deleted.\n\nSee `old/a.py` for the history."
    assert _historical(text) == set()


def test_evidence_lines_frontmatter_and_code_fences_are_not_read():
    text = "---\nname: n\n---\n```\n`old/a.py` was deleted.\n```\n`old/b.py` was deleted.\n"
    assert _historical(text) == {"old/b.py"}
    assert _historical("`old/b.py` was deleted.\n", frozenset({1})) == set()


@pytest.mark.parametrize(
    "sentence",
    [
        "Has `old/x.py` been removed?",
        "Has `old/a.py` been deleted? Check before merging.",
        "Is it true that `old/a.py` was deleted?",
        "`old/a.py` was deleted, right?",
        "`old/a.py` got removed?",
        "Was `old/a.py` removed?",
        "Who removed `old/a.py`?",
        "Why was `old/a.py` deleted?",
        "How was `old/a.py` removed?",
        "Q: `old/a.py` was moved?",
    ],
)
def test_a_question_never_records_a_deletion(sentence):
    assert _historical(sentence) == set()


def test_a_label_that_looks_like_a_wh_word_is_not_a_question():
    assert _historical("**Why:** the legacy `old/a.py` was removed.") == {"old/a.py"}


@pytest.mark.parametrize(
    "sentence",
    [
        "None of `old/a.py`, `old/b.py` was removed.",
        "Nobody removed `old/a.py`.",
        "Nothing was deleted from `old/a.py`.",
        "`old/a.py` was hardly removed.",
        "`old/a.py` was barely deleted.",
        "`old/a.py` failed to be removed by the job.",
    ],
)
def test_more_negations_do_not_cover_the_path(sentence):
    assert _historical(sentence) == set()


@pytest.mark.parametrize(
    "sentence",
    [
        "Assuming `old/a.py` was removed, the index is stale.",
        "Provided `old/a.py` was deleted, the build skips it.",
        "Suppose `old/a.py` was removed.",
        "Imagine `old/a.py` was deleted.",
    ],
)
def test_more_conditionals_do_not_cover_the_path(sentence):
    assert _historical(sentence) == set()


@pytest.mark.parametrize(
    "sentence",
    [
        "`old/a.py` gets renamed in the next release.",
        "`old/a.py` gets removed by the nightly job.",
        "TODO: `old/a.py` moved to the new layout.",
        "Proposal: `old/a.py` moved to the new layout.",
    ],
)
def test_habitual_and_planning_labels_do_not_cover_the_path(sentence):
    assert _historical(sentence) == set()


@pytest.mark.parametrize(
    "sentence",
    [
        "`TODO.md` was deleted in the cleanup.",
        "`docs/get-started.md` was removed.",
        "`src/get.py` was removed.",
        "`docs/proposals/old.md` was removed.",
        "`scripts/restore.sh` was deleted.",
        "`scripts/revert_migration.py` was deleted.",
        "`lib/readd.py` was deleted.",
        "`src/none.py` was removed.",
        "`if/when.py` was removed.",
        "`how-to.md` was removed.",
        "`what-is.md` was deleted.",
        "TODO.md was deleted in the cleanup.",
        "docs/restore.md was removed.",
    ],
)
def test_a_cue_word_inside_a_path_is_not_a_cue(sentence):
    assert len(_historical(sentence)) == 1


def test_a_cue_word_in_a_code_span_is_not_a_cue():
    assert _historical("`old/a.py` was removed (`git restore` brings it back).") == {"old/a.py"}
    assert _historical("`old/a.py` was removed (`` `git restore` `` brings it back).") == {
        "old/a.py"
    }
    assert _historical("Removed `TODO.md` and `docs/guide.md` in the cleanup.") == {
        "TODO.md",
        "docs/guide.md",
    }


@pytest.mark.parametrize(
    "sentence",
    [
        "`old/a.py` was removed if/when the migration finished.",
        "`old/a.py` was removed when/if needed.",
        "Update the docs if/when `old/a.py` was removed.",
        "TODO/FIXME: `old/a.py` was removed.",
    ],
)
def test_two_words_joined_by_a_slash_are_not_a_path_and_keep_their_cue(sentence):
    assert _historical(sentence) == set()


def test_a_bare_path_with_a_cue_word_between_its_slashes_is_still_read_as_a_path():
    assert _historical("docs/when/old.md was removed.") == {"docs/when/old.md"}
    assert _historical("`docs/when/old.md` was removed.") == {"docs/when/old.md"}


@pytest.mark.parametrize(
    "sentence",
    [
        "TODO: `old/a.py` moved to the new layout.",
        "TODO: `TODO.md` was removed.",
        "Proposal: `docs/proposals/old.md` was removed.",
    ],
)
def test_a_cue_word_in_prose_still_voids_the_statement_beside_a_path_with_a_cue_word(sentence):
    assert _historical(sentence) == set()


@pytest.mark.parametrize(
    "sentence",
    [
        "`old/a.py` was removed and the cache gets rebuilt.",
        "TODO: `old/a.py` was removed.",
        "Proposal: `old/a.py` was removed.",
        "Proposed: `old/a.py` was removed.",
        "Proposals: `old/a.py` was removed.",
    ],
)
def test_each_planning_and_habitual_cue_voids_a_past_tense_statement(sentence):
    assert _historical(sentence) == set()


@pytest.mark.parametrize(
    "sentence",
    [
        "`old/a.py` was removed and then restored.",
        "`old/a.py` was deleted but later restored from git.",
        "`old/a.py` was removed, then reverted.",
        "`old/a.py` was deleted and re-added.",
        "`old/a.py` was removed and reinstated.",
        "`old/a.py` was undeleted.",
    ],
)
def test_a_deletion_that_was_undone_does_not_cover_the_path(sentence):
    assert _historical(sentence) == set()


@pytest.mark.parametrize(
    "sentence",
    [
        "`src/h.py` calls the legacy shim, which was removed in 2.0.",
        "`src/load.py` uses the new schema and the old flag was removed.",
        "`config/app.yml` no longer has the `timeout` key, which was removed in March.",
        "`old/a.py` and `old/b.py` use the same helper, which was deleted.",
        "`src/h.py` that calls the legacy shim, which was removed in 2.0.",
    ],
)
def test_a_passive_verb_is_not_about_a_path_that_only_sits_nearby(sentence):
    assert _historical(sentence) == set()


def test_a_passive_verb_reaches_back_a_few_words_but_not_twenty():
    assert _historical("`old/a.py` that listed each preset was deleted with the folder.") == {
        "old/a.py"
    }
    assert _historical("`old/a.py` stays in the repo for now and one flag was removed.") == set()
    assert _historical(
        "The `presets/README.md` that listed the set and what each live run proved was deleted."
    ) == {"presets/README.md"}


@pytest.mark.parametrize(
    "sentence",
    [
        "`scripts/sync.sh` moved the files to `data/`.",
        "`tools/build.py` renamed the output to `dist/x.js`.",
        "`legacy/a.py` retired the old parser.",
    ],
)
def test_a_path_that_does_the_moving_is_not_covered(sentence):
    assert _historical(sentence) == set()


def test_an_active_moved_verb_covers_its_object_only():
    assert _historical("We moved `docs/old.md` to `docs/new.md`.") == {"docs/old.md"}
    assert _historical("We renamed the old folder `docs/old` and kept `docs/new`.") == {"docs/old"}


@pytest.mark.parametrize(
    "sentence",
    [
        "`old/a.py` was deleted, but `old/a.py` is still referenced by CI.",
        "`old/a.py` was deleted; `old/a.py` is still the entry point.",
        "`old/a.py` was deleted so `old/a.py` no longer builds.",
    ],
)
def test_a_second_mention_in_the_same_sentence_that_is_not_narrated_stays_reportable(sentence):
    assert _historical(sentence) == set()


def test_a_revision_reference_beside_the_deleted_path_does_not_count_as_a_pointer():
    text = "`old/a.py` was deleted; recover it with `git show abc1234~1:old/a.py` if needed."
    assert _historical(text) == {"old/a.py"}
    text = "The `old/a.py` was deleted (`git show abc1234~1:old/a.py` has it) with the folder."
    assert _historical(text) == {"old/a.py"}
    text = "`old/a.py` was deleted; recover it from `backup/old/a.py` if needed."  # a live pointer
    assert _historical(text) == {"old/a.py"} and "backup/old/a.py" not in _historical(text)
    assert _historical("`old/a.py` was deleted; see `old/a.py` for the history.") == set()


@pytest.mark.parametrize(
    "sentence",
    [
        "If, e.g. `old/a.py` was removed, the build breaks.",
        "If, i.e. `old/a.py` was removed, the build breaks.",
        "Unless the cleanup (e.g. `old/a.py` was removed) ran, the index is stale.",
        "If caches, logs etc. `old/a.py` was removed, the build breaks.",
        "When `old/a.py` vs. `old/b.py` was deleted, the build broke.",
    ],
)
def test_an_abbreviation_does_not_cut_a_condition_off_from_its_deletion(sentence):
    assert _historical(sentence) == set()


def test_a_real_sentence_end_still_splits():
    assert _historical("We cleaned up the logs. `old/a.py` was removed.") == {"old/a.py"}


def test_a_block_over_the_size_limit_is_not_read_for_deletions():
    padding = " ".join(f"word{n}" for n in range(1500))
    long_block = f"`old/a.py` was deleted. {padding}"
    assert len(long_block) > infer.MAX_BLOCK
    assert _historical(long_block) == set()
    assert _historical(f"`old/a.py` was deleted. {padding[:200]}") == {"old/a.py"}
    # the mention in the oversized block also keeps another, narrated mention reportable
    assert _historical(f"`old/a.py` was deleted.\n\n{long_block}") == set()


def test_a_huge_unpunctuated_paragraph():
    text = "\n".join(f"removed `d/f{n}.py`" for n in range(4000))
    assert _historical(text) == set()


def _history(repo):
    """Files in every state a mention can be in when the note was written (2026-09-05)."""
    repo.write("src/kept.py", "one\n")
    repo.write("src/edited.py", "before\n")
    repo.write("old/gone.py", "x\n")
    repo.write("next-app/page.tsx", "p\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    repo.write("src/edited.py", "after\n")
    repo.remove("old/gone.py")
    repo.git("mv", "next-app", "web")
    repo.write("src/later.py", "new\n")  # added after the note
    repo.commit_at("2026-09-10T10:00:00Z")
    repo.write("src/untracked.py", "u\n")
    return ts("2026-09-05T00:00:00Z")


def test_baselines_give_each_mention_the_record_it_would_get_alone(repo, tmp_path):
    when = _history(repo)
    outside = tmp_path / "outside.txt"
    outside.write_text("o\n")
    names = (
        "src/kept.py",
        "src/edited.py",
        "old/gone.py",
        "next-app",
        "src/later.py",
        "src/untracked.py",
        "no/such/file.py",
    )
    items = [(name, repo.path / name) for name in names] + [("outside", outside)]
    records, complete = infer.baselines(items, when)
    assert complete
    digest = file_digest
    assert records == {
        "src/kept.py": InferredRecord("file", "git", digest("one\n"), when),
        "src/edited.py": InferredRecord("file", "git", digest("before\n"), when),  # as of the note
        "old/gone.py": InferredRecord("file", "git", digest("x\n"), when),
        "next-app": InferredRecord("dir", "git", None, when),
        "src/later.py": InferredRecord("file", "mtime", None, when),  # not in git at the time
        "src/untracked.py": InferredRecord("file", "mtime", None, when),
        "outside": InferredRecord("file", "mtime", None, when),
    }
    assert list(records) == [name for name in [*names, "outside"] if name in records]  # in order


def test_baselines_need_a_few_git_calls_however_many_mentions(repo, monkeypatch):
    for n in range(6):
        repo.write(f"d{n}/a.py", "a\n")
        repo.write(f"d{n}/b.py", "b\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    infer._toplevel.cache_clear()
    infer._KNOWN_TOPS.clear()
    calls = []
    real = gitio._run
    monkeypatch.setattr(
        gitio,
        "_run",
        lambda cwd, *a, **k: (
            calls.append(next(x for x in a if x != "--literal-pathspecs")) or real(cwd, *a, **k)
        ),
    )
    items = [(f"d{n}/{f}.py", repo.path / f"d{n}/{f}.py") for n in range(6) for f in "ab"]
    records, complete = infer.baselines(items, ts("2026-09-05T00:00:00Z"))
    assert complete and len(records) == 12
    # the repository root, whether it has merges, the last commit to touch each path (one log
    # for all of them, as the history is linear), and every blob at once
    assert calls == ["rev-parse", "rev-list", "log", "cat-file"]


def test_a_nested_repository_is_its_own_root_for_the_mentions_inside_it(repo):
    repo.write("src/a.py", "a\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    inner = repo.path / "vendor" / "lib"
    inner.mkdir(parents=True)
    (inner / "x.py").write_text("inner\n")
    dates = {
        "GIT_AUTHOR_DATE": "2026-09-01T10:00:00Z",
        "GIT_COMMITTER_DATE": "2026-09-01T10:00:00Z",
    }
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "inner"]):
        subprocess.run(
            ["git", *args], cwd=inner, check=True, capture_output=True, env={**os.environ, **dates}
        )
    infer._toplevel.cache_clear()
    infer._KNOWN_TOPS.clear()
    when = ts("2026-09-05T00:00:00Z")
    items = [("src/a.py", repo.path / "src/a.py"), ("vendor/lib/x.py", inner / "x.py")]
    records, _ = infer.baselines(items, when)
    assert records["src/a.py"] == InferredRecord("file", "git", file_digest("a\n"), when)
    assert records["vendor/lib/x.py"] == InferredRecord("file", "git", file_digest("inner\n"), when)


def test_baselines_check_the_deadline_before_every_mention(repo, monkeypatch):
    for n in range(6):
        repo.write(f"src/f{n}.py", f"{n}\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    calls = []

    def clock():
        calls.append(1)
        return 0.0 if len(calls) <= 3 else 100.0

    monkeypatch.setattr(infer, "time", types.SimpleNamespace(monotonic=clock))
    items = [(f"src/f{n}.py", repo.path / f"src/f{n}.py") for n in range(6)]
    records, complete = infer.baselines(items, ts("2026-09-05T00:00:00Z"), deadline=50.0)
    assert not complete
    assert list(records) == ["src/f0.py", "src/f1.py", "src/f2.py"]  # the ones reached in time


def test_baselines_without_a_deadline_never_read_the_clock(repo, monkeypatch):
    repo.write("src/a.py", "a\n")
    repo.commit_at("2026-09-01T10:00:00Z")
    monkeypatch.setattr(infer, "time", types.SimpleNamespace(monotonic=lambda: 1 / 0))
    items = [("src/a.py", repo.path / "src/a.py")]
    assert infer.baselines(items, ts("2026-09-05T00:00:00Z"))[1] is True


def _merge(repo, branch, when):
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(
        ["git", "merge", "--no-ff", "-q", "-m", "merge", branch],
        cwd=repo.path,
        check=True,
        capture_output=True,
        env=env,
    )


def _two_branches(repo, side_at, main_at):
    """Main changes X, adds A and deletes Z; a side branch changes Y; the merge comes later."""
    repo.write("X", "x1\n")
    repo.write("Y", "y1\n")
    repo.write("Z", "z1\n")
    repo.commit_at("2026-09-05T01:00:00Z", "base")
    repo.git("switch", "-q", "-c", "side")
    repo.write("Y", "y2\n")
    repo.commit_at(side_at, "side")
    repo.git("switch", "-q", "main")
    repo.write("X", "x2\n")
    repo.write("A", "a1\n")
    repo.remove("Z")
    repo.commit_at(main_at, "main")
    _merge(repo, "side", "2026-09-05T12:00:00Z")  # after the note, which was written at 07:00
    return ts("2026-09-05T07:00:00Z")


@pytest.mark.parametrize(
    ("side_at", "main_at"),
    [
        ("2026-09-05T06:00:00Z", "2026-09-05T05:00:00Z"),  # the side commit is the newer one
        ("2026-09-05T05:00:00Z", "2026-09-05T06:00:00Z"),  # the main commit is the newer one
    ],
    ids=["side-newer", "main-newer"],
)
def test_a_merge_does_not_make_one_branchs_commit_the_baseline_of_the_other_branchs_files(
    repo, side_at, main_at
):
    when = _two_branches(repo, side_at, main_at)
    items = [(name, repo.path / name) for name in "XAYZ"]
    records, complete = infer.baselines(items, when)
    assert complete
    # Each file is baselined at the last commit before the note that touched that file.
    assert records["X"] == InferredRecord("file", "git", file_digest("x2\n"), when)
    assert records["A"] == InferredRecord("file", "git", file_digest("a1\n"), when)
    assert records["Y"] == InferredRecord("file", "git", file_digest("y2\n"), when)
    assert "Z" not in records  # deleted before the note: nothing was stale, and it is no "gone"
    for name in "XAY":
        assert infer.state(records[name], repo.path / name, when) == infer.FRESH


def _oracle(repo, path, iso, when):
    """The record a mention would get from one ``git log -1`` and two more calls of its own."""
    commit = repo.git("log", "-1", f"--before={iso}", "--format=%H", "--", path)
    if commit:
        kind = subprocess.run(
            ["git", "cat-file", "-t", f"{commit}:./{path}"], cwd=repo.path, capture_output=True
        )
        if kind.stdout.strip() == b"blob":
            content = repo.git("show", f"{commit}:./{path}")
            return InferredRecord("file", "git", file_digest(content), when)
        if kind.stdout.strip() == b"tree":
            return InferredRecord("dir", "git", None, when)
    target = repo.path / path
    if target.is_file():
        return InferredRecord("file", "mtime", None, when)
    return InferredRecord("dir", "mtime", None, when) if target.is_dir() else None


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_batched_baselines_equal_the_per_path_rule_on_random_linear_histories(repo, seed):
    rng = random.Random(seed)
    names = ["a.py", "b.py", "d/c.py", "d/e.py", "f.py"]
    moment = ts("2026-09-01T00:00:00Z")
    commit_times = []
    for step in range(16):
        for _ in range(rng.randint(1, 2)):
            name = rng.choice(names)
            if (repo.path / name).exists() and rng.random() < 0.35:
                repo.remove(name)
            else:
                repo.write(name, f"{name} version {step} {rng.random()}\n")
        moment += rng.choice([0, 0, 1, 60, 3600])  # ties are common
        iso = dt.datetime.fromtimestamp(moment, tz=dt.UTC).isoformat()
        repo.commit_at(iso, f"step {step}")
        commit_times.append(moment)
    probes = [*names, "d", "never.py"]
    for note_time in (
        commit_times[0] - 1,
        commit_times[5],
        commit_times[9] + 1,
        rng.choice(commit_times),
        commit_times[-1] + 1,
    ):
        iso = dt.datetime.fromtimestamp(note_time, tz=dt.UTC).isoformat()
        records, complete = infer.baselines([(p, repo.path / p) for p in probes], note_time)
        assert complete
        expected = {p: _oracle(repo, p, iso, note_time) for p in probes}
        assert records == {p: r for p, r in expected.items() if r is not None}, (seed, note_time)


def test_a_path_touched_long_ago_is_still_found_in_a_longer_read_of_the_log(repo, monkeypatch):
    repo.write("old.py", "once\n")
    repo.commit_at("2026-09-01T00:00:00Z", "old")
    for n in range(9):  # many newer commits that touch other paths
        repo.write("busy.py", f"{n}\n")
        repo.commit_at(f"2026-09-02T0{n}:00:00Z", f"busy {n}")
    monkeypatch.setattr(gitio, "LOG_SLICE", 2)  # the first read stops after two commits
    monkeypatch.setattr(gitio, "LOG_GROWTH", 2)
    iso = "2026-09-05T00:00:00+00:00"
    found = gitio.last_touches(repo.path, iso, ["busy.py", "old.py", "never.py"])
    assert found == {
        "busy.py": repo.git("log", "-1", f"--before={iso}", "--format=%H", "--", "busy.py"),
        "old.py": repo.git("log", "-1", f"--before={iso}", "--format=%H", "--", "old.py"),
    }  # never.py was touched by no commit, which the end of the history proves


def test_baselines_check_the_deadline_between_the_repositories_of_one_note(tmp_path, monkeypatch):
    items = []
    for n in range(3):
        project = Project(tmp_path / f"r{n}")
        project.path.mkdir()
        project.init()
        project.write("f.py", f"{n}\n")
        project.commit_at("2026-09-01T10:00:00Z")
        items.append((f"r{n}", project.path / "f.py"))
    calls = []

    def clock():  # the three mentions are reached in time; the second repository is not
        calls.append(1)
        return 0.0 if len(calls) <= 3 else 100.0

    monkeypatch.setattr(infer, "time", types.SimpleNamespace(monotonic=clock))
    records, complete = infer.baselines(items, ts("2026-09-05T00:00:00Z"), deadline=50.0)
    assert not complete
    assert list(records) == ["r0"]


def _git(repo, *args, when=None, check=True):
    env = dict(os.environ)
    if when is not None:
        iso = dt.datetime.fromtimestamp(when, tz=dt.UTC).isoformat()
        env.update(GIT_AUTHOR_DATE=iso, GIT_COMMITTER_DATE=iso)
    return subprocess.run(
        ["git", *args], cwd=repo.path, env=env, capture_output=True, text=True, check=check
    )


def _history_with_merges(repo, rng, steps):
    """Branches, clean merges onto main, edits of one line of 12-line files, and date ties.

    Returns the commit times and the number of merge commits.
    """
    names = ["a.py", "b.py", "c.py", "d/e.py"]
    moment = ts("2026-09-01T00:00:00Z")

    def edit(name):
        target = repo.path / name
        if target.exists() and rng.random() < 0.12:
            target.unlink()
            return
        lines = target.read_text().splitlines() if target.exists() else [f"{n}" for n in range(12)]
        lines[rng.randrange(len(lines))] = f"v{rng.random()}"
        repo.write(name, "\n".join(lines) + "\n")

    def commit(message):
        nonlocal moment
        moment += rng.choice([0, 0, 1, 3600, 7200])
        for name in rng.sample(names, rng.randint(1, 2)):
            edit(name)
        repo.commit_at(dt.datetime.fromtimestamp(moment, tz=dt.UTC).isoformat(), message)
        times.append(moment)

    times: list[float] = []
    for name in names:
        repo.write(name, "\n".join(str(n) for n in range(12)) + "\n")
    repo.commit_at(dt.datetime.fromtimestamp(moment, tz=dt.UTC).isoformat(), "start")
    times.append(moment)
    branches, merged = ["main"], 0
    for step in range(steps):
        roll = rng.random()
        current = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if roll < 0.45:
            commit(f"step {step}")
        elif roll < 0.65 and len(branches) < 4:
            branches.append(f"b{len(branches)}")
            _git(repo, "switch", "-q", "-c", branches[-1])
            commit(f"step {step}")
        elif roll < 0.8 and len(branches) > 1:
            _git(repo, "switch", "-q", rng.choice([b for b in branches if b != current]))
        elif len(branches) > 1:
            _git(repo, "switch", "-q", "main")
            moment += rng.choice([0, 1, 3600])
            other = rng.choice(branches[1:])
            done = _git(repo, "merge", "--no-ff", "-q", "-m", "m", other, when=moment, check=False)
            if done.returncode:
                _git(repo, "merge", "--abort", check=False)
            else:
                merged += 1
                times.append(moment)
    _git(repo, "switch", "-q", "main")
    return times, merged


def test_baselines_equal_the_per_path_rule_on_random_histories_with_merges(tmp_path):
    probes = ["a.py", "b.py", "c.py", "d/e.py", "d", "never.py"]
    total_merges = 0
    # 17, 19 and 30 are seeds whose histories the union log got wrong; the others are company
    for seed in (2, 5, 8, 17, 19, 30):
        rng = random.Random(seed)
        repo = Project(tmp_path / f"seed{seed}")
        repo.path.mkdir()
        repo.init()
        times, merged = _history_with_merges(repo, rng, steps=14)
        total_merges += merged
        for note_time in (
            times[0] - 1,
            times[len(times) // 3],
            rng.choice(times) + 1,
            times[-1] + 1,
        ):
            iso = dt.datetime.fromtimestamp(note_time, tz=dt.UTC).isoformat()
            records, complete = infer.baselines([(p, repo.path / p) for p in probes], note_time)
            assert complete
            expected = {p: _oracle(repo, p, iso, note_time) for p in probes}
            assert records == {p: r for p, r in expected.items() if r is not None}, (
                seed,
                note_time,
            )
    assert total_merges >= 5  # the histories really do contain merges


HEAD_ENTRIES = [None, "untracked-file", "tracked-file", "directory"]


def _put_head_entry(repo, kind):
    """A root entry named ``HEAD``: git calls ``rev-list HEAD`` ambiguous while it exists."""
    if kind == "directory":
        (repo.path / "HEAD").mkdir()
    elif kind is not None:
        repo.write("HEAD", "not a revision\n")
        if kind == "tracked-file":
            repo.commit_at("2026-09-06T00:00:00Z", "a file named HEAD")


def _same_file_merge(repo):
    """Two edits to different hunks of one file, merged cleanly: the merge is its last touch."""
    repo.write("big.py", "".join(f"line {n}\n" for n in range(40)))
    repo.write("other.py", "o\n")
    repo.commit_at("2026-09-05T01:00:00Z", "base")
    repo.git("switch", "-q", "-c", "side")
    repo.write("big.py", "".join("SIDE\n" if n == 2 else f"line {n}\n" for n in range(40)))
    repo.commit_at("2026-09-05T02:00:00Z", "side")
    repo.git("switch", "-q", "main")
    repo.write("big.py", "".join("MAIN\n" if n == 37 else f"line {n}\n" for n in range(40)))
    repo.commit_at("2026-09-05T03:00:00Z", "main")
    _merge(repo, "side", "2026-09-05T04:00:00Z")
    return ts("2026-09-05T05:00:00Z")


@pytest.mark.parametrize("head", HEAD_ENTRIES)
def test_a_file_only_a_clean_merge_changed_is_fresh_after_it(repo, head):
    when = _same_file_merge(repo)
    _put_head_entry(repo, head)
    records, _ = infer.baselines([("big.py", repo.path / "big.py")], when)
    assert infer.state(records["big.py"], repo.path / "big.py", when) == infer.FRESH


@pytest.mark.parametrize("head", HEAD_ENTRIES)
def test_a_deletion_the_merge_kept_leaves_no_record_beside_other_mentioned_paths(repo, head):
    repo.write("X", "x1\n")
    repo.write("Y", "y1\n")
    repo.commit_at("2026-09-05T01:00:00Z", "base")
    repo.git("switch", "-q", "-c", "side")
    repo.write("X", "x2\n")
    repo.write("Y", "y2\n")
    repo.commit_at("2026-09-05T06:00:00Z", "side edits X and Y")
    repo.git("switch", "-q", "main")
    repo.remove("X")
    repo.commit_at("2026-09-05T05:00:00Z", "main deletes X")
    _git(
        repo,
        "merge",
        "--no-ff",
        "-q",
        "-m",
        "m",
        "side",
        when=ts("2026-09-05T07:00:00Z"),
        check=False,
    )
    repo.git("rm", "-q", "X")  # the modify/delete conflict is resolved by keeping the deletion
    _git(repo, "commit", "-q", "-m", "merge", when=ts("2026-09-05T07:00:00Z"))
    when = ts("2026-09-05T08:00:00Z")
    _put_head_entry(repo, head)
    records, _ = infer.baselines([("X", repo.path / "X"), ("Y", repo.path / "Y")], when)
    assert "X" not in records  # a deletion is not "gone"
    assert infer.state(records["Y"], repo.path / "Y", when) == infer.FRESH


@pytest.mark.parametrize("head", HEAD_ENTRIES)
def test_a_deletion_dated_like_its_fork_point_leaves_no_record(repo, head):
    repo.write("f.py", "f\n")
    repo.write("g.py", "g1\n")
    repo.write("h.py", "h1\n")
    repo.write("k.py", "k1\n")
    repo.commit_at("2026-09-05T01:00:00Z", "base")
    repo.git("switch", "-q", "-c", "side")
    repo.remove("f.py")
    repo.commit_at("2026-09-05T01:00:00Z", "side deletes f.py in the second of the fork point")
    repo.write("k.py", "k2\n")
    repo.commit_at("2026-09-05T01:30:00Z", "side")
    repo.git("switch", "-q", "main")
    repo.write("g.py", "g2\n")
    repo.commit_at("2026-09-05T02:00:00Z", "main g")
    repo.write("h.py", "h2\n")
    repo.commit_at("2026-09-05T03:00:00Z", "main h")
    _merge(repo, "side", "2026-09-05T04:00:00Z")
    when = ts("2026-09-05T05:00:00Z")
    _put_head_entry(repo, head)
    items = [(name, repo.path / name) for name in ("f.py", "g.py", "h.py")]
    records, _ = infer.baselines(items, when)
    assert "f.py" not in records
    assert all(
        infer.state(records[n], repo.path / n, when) == infer.FRESH for n in ("g.py", "h.py")
    )


def test_a_conflict_resolved_with_ours_keeps_the_per_path_baselines(repo):
    repo.write("X", "".join(f"x{n}\n" for n in range(10)))
    repo.write("Y", "y1\n")
    repo.commit_at("2026-09-05T01:00:00Z", "base")
    repo.git("switch", "-q", "-c", "side")
    repo.write("X", "".join("SIDE\n" if n == 5 else f"x{n}\n" for n in range(10)))
    repo.write("Y", "y2\n")
    repo.commit_at("2026-09-05T02:00:00Z", "side")
    repo.git("switch", "-q", "main")
    repo.write("X", "".join("MAIN\n" if n == 5 else f"x{n}\n" for n in range(10)))
    repo.commit_at("2026-09-05T03:00:00Z", "main")
    _git(
        repo,
        "merge",
        "--no-ff",
        "-q",
        "-m",
        "m",
        "-X",
        "ours",
        "side",
        when=ts("2026-09-05T04:00:00Z"),
    )
    when = ts("2026-09-05T05:00:00Z")
    records, _ = infer.baselines([(n, repo.path / n) for n in "XY"], when)
    assert all(infer.state(records[n], repo.path / n, when) == infer.FRESH for n in "XY")


def test_a_name_that_looks_like_pathspec_magic_is_a_literal_path(repo):
    repo.write(":(exclude)x.py", "x\n")
    first = repo.commit_at("2026-09-01T00:00:00Z", "the odd name")
    repo.write("plain.py", "p\n")
    second = repo.commit_at("2026-09-02T00:00:00Z", "a plain name")
    iso = "2026-09-05T00:00:00+00:00"
    # read as magic, ":(exclude)x.py" would mean "everything but x.py", and name the second commit
    assert gitio.last_touch(repo.path, iso, ":(exclude)x.py") == first
    found = gitio.last_touches(repo.path, iso, ["plain.py", ":(exclude)x.py"])
    assert found == {"plain.py": second, ":(exclude)x.py": first}


def test_a_repository_with_merges_is_read_path_by_path_and_one_without_is_read_together(
    repo, monkeypatch
):
    _two_branches(repo, "2026-09-05T06:00:00Z", "2026-09-05T05:00:00Z")
    infer._toplevel.cache_clear()
    infer._KNOWN_TOPS.clear()
    calls = []
    real = gitio._run
    monkeypatch.setattr(
        gitio,
        "_run",
        lambda cwd, *a, **k: (
            calls.append(next(x for x in a if x != "--literal-pathspecs")) or real(cwd, *a, **k)
        ),
    )
    items = [(name, repo.path / name) for name in "XAY"]
    infer.baselines(items, ts("2026-09-05T07:00:00Z"))
    # the root, the merge check, one exact log per mention, and every blob at once
    assert calls == ["rev-parse", "rev-list", "log", "log", "log", "cat-file"]


def test_whether_a_repository_has_merges_is_remembered_in_the_table_a_scan_passes(
    repo, monkeypatch
):
    _two_branches(repo, "2026-09-05T06:00:00Z", "2026-09-05T05:00:00Z")
    asked = []
    real = gitio.has_merges
    monkeypatch.setattr(gitio, "has_merges", lambda cwd: asked.append(cwd) or real(cwd))
    known = {}
    when = ts("2026-09-05T07:00:00Z")
    infer.baselines([("X", repo.path / "X")], when, merges=known)
    infer.baselines([("A", repo.path / "A")], when, merges=known)  # another note, same scan
    assert len(asked) == 1 and list(known.values()) == [True]
    infer.baselines([("A", repo.path / "A")], when)  # without a table, it is asked again
    assert len(asked) == 2


def test_a_repository_with_merges_still_honours_the_deadline_path_by_path(repo, monkeypatch):
    for n in range(6):
        repo.write(f"f{n}.py", f"{n}\n")
    _two_branches(repo, "2026-09-05T06:00:00Z", "2026-09-05T05:00:00Z")
    calls = []

    def clock():  # six mentions, then the first path (never checked) and two more are in time
        calls.append(1)
        return 0.0 if len(calls) <= 8 else 100.0

    monkeypatch.setattr(infer, "time", types.SimpleNamespace(monotonic=clock))
    items = [(f"f{n}.py", repo.path / f"f{n}.py") for n in range(6)]
    records, complete = infer.baselines(items, ts("2026-09-05T07:00:00Z"), deadline=50.0)
    assert not complete
    assert list(records) == ["f0.py", "f1.py", "f2.py"]


@pytest.mark.parametrize("head", HEAD_ENTRIES)
def test_the_merge_check_sees_a_merge_whatever_the_root_entries_are_called(repo, head):
    _two_branches(repo, "2026-09-05T06:00:00Z", "2026-09-05T05:00:00Z")
    _put_head_entry(repo, head)
    assert gitio.has_merges(repo.path)


@pytest.mark.parametrize("head", HEAD_ENTRIES)
def test_the_merge_check_finds_none_in_a_linear_history_with_a_root_entry_named_head(repo, head):
    repo.write("a.py", "a\n")
    repo.commit_at("2026-09-01T00:00:00Z")
    _put_head_entry(repo, head)
    assert not gitio.has_merges(repo.path)  # an honest "no", not a failure read as one


def _git_that_fails_the_merge_check(monkeypatch, how):
    real = gitio._run

    def run(cwd, *args, **kwargs):
        if "--min-parents=2" in args:
            if how == "raises":
                raise gitio.GitError("simulated")
            return subprocess.CompletedProcess(args, 128, b"", b"fatal: simulated")
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(gitio, "_run", run)


@pytest.mark.parametrize("how", ["exit-128", "raises"])
def test_when_the_merge_check_fails_the_history_is_read_path_by_path(repo, monkeypatch, how):
    repo.write("f.py", "f\n")
    repo.write("g.py", "g1\n")
    repo.write("h.py", "h1\n")
    repo.write("k.py", "k1\n")
    repo.commit_at("2026-09-05T01:00:00Z", "base")
    repo.git("switch", "-q", "-c", "side")
    repo.remove("f.py")
    repo.commit_at("2026-09-05T01:00:00Z", "side deletes f.py in the second of the fork point")
    repo.write("k.py", "k2\n")
    repo.commit_at("2026-09-05T01:30:00Z", "side")
    repo.git("switch", "-q", "main")
    repo.write("g.py", "g2\n")
    repo.commit_at("2026-09-05T02:00:00Z", "main g")
    repo.write("h.py", "h2\n")
    repo.commit_at("2026-09-05T03:00:00Z", "main h")
    _merge(repo, "side", "2026-09-05T04:00:00Z")
    when = ts("2026-09-05T05:00:00Z")
    _git_that_fails_the_merge_check(monkeypatch, how)
    assert gitio.has_merges(repo.path)  # an unanswered question is a "yes": the exact rule runs
    items = [(name, repo.path / name) for name in ("f.py", "g.py", "h.py")]
    records, _ = infer.baselines(items, when)
    assert "f.py" not in records  # the union log would have said it was deleted after the note
    assert all(
        infer.state(records[n], repo.path / n, when) == infer.FRESH for n in ("g.py", "h.py")
    )


def test_baselines_name_the_mentions_they_reached_that_have_no_record(repo, monkeypatch):
    repo.write("real0.py", "0\n")
    repo.write("real1.py", "1\n")
    _two_branches(repo, "2026-09-05T06:00:00Z", "2026-09-05T05:00:00Z")  # a merge: path by path
    calls = []

    def clock():  # six mentions, then the first path (never checked) and two more are in time
        calls.append(1)
        return 0.0 if len(calls) <= 8 else 100.0

    monkeypatch.setattr(infer, "time", types.SimpleNamespace(monotonic=clock))
    names = ["real0.py", "gone1.py", "real1.py", "gone2.py", "gone3.py", "gone4.py"]
    unrecorded: set[str] = set()
    records, complete = infer.baselines(
        [(n, repo.path / n) for n in names],
        ts("2026-09-05T07:00:00Z"),
        deadline=50.0,
        unrecorded=unrecorded,
    )
    assert not complete
    assert set(records) == {"real0.py", "real1.py"}
    assert unrecorded == {"gone1.py"}  # reached and nothing to track; gone2 to gone4 not reached


def test_a_mention_deleted_at_its_last_commit_before_the_note_is_named_too(repo):
    _two_branches(repo, "2026-09-05T06:00:00Z", "2026-09-05T05:00:00Z")  # main deleted Z
    unrecorded: set[str] = set()
    items = [("X", repo.path / "X"), ("Z", repo.path / "Z")]
    records, complete = infer.baselines(items, ts("2026-09-05T07:00:00Z"), unrecorded=unrecorded)
    assert complete and set(records) == {"X"}
    assert unrecorded == {"Z"}
