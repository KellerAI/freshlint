import datetime as dt
import json
import re
from pathlib import Path

from freshlint import memory


def ts(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def test_project_slug_replaces_every_non_alphanumeric():
    assert memory.project_slug(Path("/Users/sam/Desktop/Acme AI")) == "-Users-sam-Desktop-Acme-AI"
    assert memory.project_slug(Path("/Users/sam/.acme-memory")) == "-Users-sam--acme-memory"
    assert memory.project_slug(Path("/Users/sam/Acme - Lab")) == "-Users-sam-Acme---Lab"
    assert memory.project_slug(Path("/w/my_repo")) == "-w-my-repo"


def test_long_slug_is_truncated_with_a_hash_suffix():
    path = Path("/private/tmp/" + "a" * 110 + "/" + "b" * 110 + "/proj")
    assert len(str(path)) == 239
    plain = re.sub(r"[^A-Za-z0-9]", "-", str(path))
    assert memory.project_slug(path) == plain[:200] + "-24va1c"


def test_slug_of_exactly_the_limit_is_unchanged():
    path = Path("/" + "a" * 199)
    assert len(str(path)) == memory.SLUG_LIMIT
    assert memory.project_slug(path) == "-" + "a" * 199


def test_memory_dir_defaults_under_home(claude_project):
    assert memory.memory_dir_for(claude_project.path) == claude_project.memory


def test_memory_dir_for_a_subdirectory_uses_the_repo_root(claude_project):
    sub = claude_project.path / "pkg"
    sub.mkdir()
    assert memory.memory_dir_for(sub) == claude_project.memory


def test_memory_dir_is_shared_across_worktrees(claude_project):
    claude_project.write("a.txt", "a")
    claude_project.commit()
    worktree = claude_project.path.parent / "wt"
    claude_project.git("worktree", "add", "-q", str(worktree))
    assert memory.memory_dir_for(worktree) == claude_project.memory


def test_memory_dir_outside_git_uses_the_directory(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    expected = (
        Path.home() / ".claude" / "projects" / memory.project_slug(plain.resolve()) / "memory"
    )
    assert memory.memory_dir_for(plain) == expected


def test_relative_override_is_ignored(claude_project):
    claude_project.write(".claude/settings.json", '{"autoMemoryDirectory": "notes/memory"}')
    assert memory.memory_dir_for(claude_project.path) == claude_project.memory


def test_absolute_override_from_project_settings(claude_project, tmp_path):
    target = tmp_path / "abs-mem"
    claude_project.write(".claude/settings.json", json.dumps({"autoMemoryDirectory": str(target)}))
    assert memory.memory_dir_for(claude_project.path) == target.resolve()


def test_home_override_from_local_settings_wins(claude_project):
    claude_project.write(".claude/settings.json", '{"autoMemoryDirectory": "notes/memory"}')
    claude_project.write(".claude/settings.local.json", '{"autoMemoryDirectory": "~/mem"}')
    assert memory.memory_dir_for(claude_project.path) == (Path.home() / "mem").resolve()


def test_home_override_uses_the_given_home(claude_project, tmp_path):
    claude_project.write(".claude/settings.json", '{"autoMemoryDirectory": "~/mem"}')
    other = tmp_path / "other-home"
    assert memory.memory_dir_for(claude_project.path, home=other) == (other / "mem").resolve()


def test_tilde_user_override_is_ignored(claude_project):
    claude_project.write(".claude/settings.json", '{"autoMemoryDirectory": "~nosuchuser/x"}')
    assert memory.memory_dir_for(claude_project.path) == claude_project.memory


def test_broken_settings_are_ignored(claude_project):
    claude_project.write(".claude/settings.json", "{not json")
    assert memory.memory_dir_for(claude_project.path) == claude_project.memory


def test_list_notes_skips_the_index_and_subdirectories(claude_project):
    claude_project.note("b.md", "Beta.")
    claude_project.note("a.md", "Alpha.")
    (claude_project.memory / "MEMORY.md").write_text("- [a](a.md)\n")
    (claude_project.memory / ".freshlint").mkdir()
    (claude_project.memory / ".freshlint" / "receipts.md").write_text("x\n")
    assert [n.name for n in memory.list_notes(claude_project.memory)] == ["a.md", "b.md"]


def test_note_paths_are_sorted_and_filtered_like_list_notes(claude_project):
    claude_project.note("b.md", "Beta.")
    claude_project.note("a.md", "Alpha.")
    (claude_project.memory / "MEMORY.md").write_text("- [a](a.md)\n")
    (claude_project.memory / ".freshlint").mkdir()
    paths = memory.note_paths(claude_project.memory)
    assert [p.name for p in paths] == ["a.md", "b.md"]
    assert [n.path for n in memory.list_notes(claude_project.memory)] == paths
    assert memory.note_paths(claude_project.memory / "nope") == []


def test_a_note_that_cannot_be_examined_is_kept_not_dropped(claude_project, monkeypatch):
    # Dropping it would also prune its fingerprints: scan reports a note it cannot check.
    claude_project.note("a.md", "Alpha.")
    claude_project.note("b.md", "Beta.")
    real = Path.stat

    def stat(self, **kwargs):
        if self.name == "b.md":
            raise PermissionError(13, "Permission denied")
        return real(self, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert [p.name for p in memory.note_paths(claude_project.memory)] == ["a.md", "b.md"]


def test_list_notes_with_patterns(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("Guide\n")
    (tmp_path / "README.md").write_text("Readme\n")
    names = [n.name for n in memory.list_notes(tmp_path, ("CLAUDE.md", "AGENTS.md"))]
    assert names == ["CLAUDE.md"]


def test_missing_memory_dir_has_no_notes(tmp_path):
    assert memory.list_notes(tmp_path / "nope") == []


def test_type_and_modified_come_from_the_metadata_block(claude_project):
    path = claude_project.note(
        "a.md", "Alpha.", note_type="reference", modified="2026-07-24T21:19:03.310Z"
    )
    note = memory.read_note(path)
    assert note.type == "reference"
    assert note.modified == dt.datetime(2026, 7, 24, 21, 19, 3, 310000, tzinfo=dt.UTC)
    assert note.written_at == ts("2026-07-24T21:19:03.310Z")


def test_top_level_type_and_mtime_fallback(tmp_path):
    path = tmp_path / "n.md"
    path.write_text("---\ntype: Project\n---\nBody\n")
    note = memory.read_note(path)
    assert note.type == "project"
    assert note.modified is None
    assert note.written_at == path.stat().st_mtime


def test_unknown_type_is_none(tmp_path):
    path = tmp_path / "n.md"
    path.write_text("---\nmetadata:\n  type: diary\n---\nBody\n")
    assert memory.read_note(path).type is None


def test_tracked_note_uses_its_commit_time_until_edited(repo):
    repo.write("mem/n.md", "Body\n")
    repo.commit_at("2026-09-01T00:00:00Z")
    assert memory.list_notes(repo.path / "mem")[0].written_at == ts("2026-09-01T00:00:00Z")
    repo.write("mem/n.md", "Edited\n")
    note = memory.list_notes(repo.path / "mem")[0]
    assert note.written_at == (repo.path / "mem/n.md").stat().st_mtime


def test_is_note(claude_project):
    mem = claude_project.memory
    mem.mkdir(parents=True)
    assert memory.is_note(mem / "a.md", mem)
    assert not memory.is_note(mem / "MEMORY.md", mem)
    assert not memory.is_note(mem / ".freshlint" / "receipts.md", mem)
    assert not memory.is_note(mem / "a.txt", mem)
    assert not memory.is_note(claude_project.path / "a.md", mem)
