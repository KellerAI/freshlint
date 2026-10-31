"""CLI behaviour: modes, ranges, output formats, exit codes and paths."""

import json
import subprocess
import sys

import pytest

import freshlint
from conftest import run_cli

OLD = "<!-- memory: verified=2026-08-15 review_by=2026-10-15 -->"
NEW = "<!-- memory: verified=2026-09-30 review_by=2026-12-30 -->"
DOC = f"# A\n\n## S\n{OLD}\n\nfact\n"


def launder(repo, path="a.md"):
    repo.replace(path, OLD, NEW)


class TestModes:
    def test_full_scan_works_outside_git(self, project):
        project.write("a.md", DOC)
        result = project.lint()
        assert result.code == 0
        assert result.payload["mode"] == "full"

    def test_staged_requires_git(self, project):
        project.write("a.md", DOC)
        result = project.lint("--staged")
        assert result.code == 2
        assert "git repository" in result.err

    def test_staged_on_initial_commit_has_no_diff_findings(self, repo):
        repo.write("a.md", DOC.replace(OLD, NEW))
        repo.stage()
        result = repo.lint("--staged")
        assert result.code == 0
        assert result.payload["mode"] == "staged"

    def test_staged_reads_the_index_not_the_worktree(self, repo):
        repo.write("a.md", DOC)
        repo.commit("base")
        launder(repo)
        repo.stage()
        # an unstaged real edit must not hide the staged laundering
        repo.replace("a.md", "fact", "changed fact")
        assert "freshness-laundering" in repo.lint("--staged").rules()

    def test_diff_triple_dot_uses_merge_base(self, repo):
        repo.write("a.md", DOC)
        repo.commit("base")
        repo.git("checkout", "-q", "-b", "agent")
        launder(repo)
        repo.commit("agent refresh")
        repo.git("checkout", "-q", "main")
        repo.write("b.md", DOC)
        repo.commit("unrelated main work")
        result = repo.lint("--diff", "main...agent")
        assert result.rules() == ["freshness-laundering"]
        assert result.payload["range"] == "main...agent"

    def test_diff_two_dot_and_single_rev(self, repo):
        repo.write("a.md", DOC)
        repo.commit("base")
        launder(repo)
        repo.commit("refresh")
        assert repo.lint("--diff", "HEAD~1..HEAD").rules() == ["freshness-laundering"]
        assert repo.lint("--diff", "HEAD~1").rules() == ["freshness-laundering"]

    def test_diff_rejects_bad_revisions(self, repo):
        repo.write("a.md", DOC)
        repo.commit("base")
        result = repo.lint("--diff", "nope...HEAD")
        assert result.code == 2
        assert "nope" in result.err

    def test_staged_and_diff_are_exclusive(self, repo):
        result = repo.lint("--staged", "--diff", "HEAD")
        assert result.code == 2

    def test_diff_mode_only_lints_changed_files(self, repo):
        repo.write("a.md", DOC)
        repo.write(
            "expired.md", "## S\n<!-- memory: verified=2026-01-01 review_by=2026-02-01 -->\n"
        )
        repo.commit("base")
        repo.replace("a.md", "fact", "new fact")
        repo.replace("a.md", OLD, NEW)
        repo.stage()
        assert repo.lint("--staged").findings == []
        assert "review-expired" in repo.lint().rules()

    def test_state_rules_run_on_changed_files_in_diff_mode(self, repo):
        repo.write("a.md", DOC)
        repo.commit("base")
        repo.replace("a.md", "fact", "currently a fact")
        repo.replace("a.md", OLD, NEW)
        repo.stage()
        assert repo.lint("--staged").rules() == ["temporal-language"]

    def test_paths_filter(self, project):
        project.write("context/a.md", "# no meta\n")
        project.write("other/b.md", "# no meta\n")
        result = project.lint("context")
        assert [f["path"] for f in result.findings] == ["context/a.md"]
        result = project.lint("other/b.md")
        assert [f["path"] for f in result.findings] == ["other/b.md"]

    def test_respects_gitignore_in_full_scan(self, repo):
        repo.write(".gitignore", "scratch/\n")
        repo.write("scratch/notes.md", "# no meta\n")
        repo.write("a.md", DOC)
        assert repo.lint().findings == []

    def test_subproject_paths_display_relative_to_git_root(self, repo):
        repo.remove(".freshlint.toml")
        sub = repo.path / "docs" / "memory"
        repo.write("docs/memory/.freshlint.toml", "")
        repo.write("docs/memory/a.md", DOC)
        repo.write("docs/memory/VERIFICATIONS.md", "# Verifications\n")
        repo.write("README.md", "# outside the memory project\n")
        repo.commit("base")
        launder(repo, "docs/memory/a.md")
        repo.stage()
        result = repo.lint("--staged", cwd=sub)
        assert [f["path"] for f in result.findings] == ["docs/memory/a.md"]
        assert "- 2026-09-30 verified a.md#s — " in result.findings[0]["hint"]
        repo.append(
            "docs/memory/VERIFICATIONS.md", "\n## 2026-09-30\n- verified a.md#s — checked\n"
        )
        repo.stage()
        assert repo.lint("--staged", cwd=sub).findings == []


class TestArgs:
    def test_bad_today(self, project):
        result = project.lint(today="2026-13-01")
        assert result.code == 2
        assert "--today" in result.err

    def test_bad_config_exits_2(self, project):
        project.write(".freshlint.toml", "nope = 1\n")
        result = project.lint()
        assert result.code == 2
        assert "unknown key 'nope'" in result.err

    def test_explicit_config(self, project):
        project.write("cfg/strict.toml", '[rules]\nmissing-marker = "error"\n')
        project.write("cfg/a.md", "# no meta\n")
        result = project.lint("--config", "cfg/strict.toml")
        assert result.code == 1

    def test_no_command_is_usage_error(self, project):
        assert run_cli(project.path).code == 2

    def test_unknown_format(self, project):
        assert project.lint(fmt="xml").code == 2


class TestOutput:
    @pytest.fixture
    def laundered(self, repo):
        repo.write("a.md", DOC)
        repo.commit("base")
        launder(repo)
        repo.stage()
        return repo

    def test_text_format(self, laundered):
        result = laundered.lint("--staged", fmt="text")
        lines = result.out.splitlines()
        assert lines[0].startswith('a.md:4: error [freshness-laundering] Section "S"')
        assert lines[1].startswith("    hint: ")
        assert lines[-1] == (
            "freshlint: 1 error, 0 warnings in 1 file (staged changes, today 2026-09-30)"
        )

    def test_text_format_clean_summary(self, project):
        project.write("a.md", DOC)
        result = project.lint(fmt="text")
        assert result.out.splitlines() == [
            "freshlint: 0 errors, 0 warnings in 1 file (full scan, today 2026-09-30)"
        ]

    def test_json_schema(self, laundered):
        payload = laundered.lint("--staged").payload
        assert payload["tool"] == "freshlint"
        assert payload["version"] == freshlint.__version__
        assert payload["schema"] == 1
        assert payload["today"] == "2026-09-30"
        assert payload["mode"] == "staged"
        assert payload["summary"] == {"errors": 1, "warnings": 0, "files": 1}
        assert set(payload["findings"][0]) == {
            "rule",
            "severity",
            "path",
            "line",
            "message",
            "hint",
        }

    def test_github_format_escapes(self, laundered):
        result = laundered.lint("--staged", fmt="github")
        first = result.out.splitlines()[0]
        assert first.startswith(
            "::error file=a.md,line=4,title=freshlint freshness-laundering::Section"
        )
        assert "%0A" in first  # hint is appended on an escaped newline
        assert "\r" not in result.out

    def test_github_property_escaping(self, project):
        project.write("we,ird:name.md", "# no meta\n")
        out = project.lint(fmt="github").out
        assert "file=we%2Cird%3Aname.md" in out

    def test_findings_are_sorted(self, project):
        project.write("b.md", "# no meta\n")
        project.write("a.md", "# no meta\ncurrently\n")
        paths = [(f["path"], f["line"]) for f in project.lint().findings]
        assert paths == sorted(paths)


class TestRulesCommand:
    def test_lists_rules(self, project):
        result = run_cli(project.path, "rules")
        assert result.code == 0
        assert "freshness-laundering" in result.out
        data = json.loads(run_cli(project.path, "rules", "--format", "json").out)
        assert {r["id"] for r in data} >= {"freshness-laundering", "stale-refresh"}


def test_module_entry_point(project):
    proc = subprocess.run(
        [sys.executable, "-m", "freshlint", "--version"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(freshlint.__file__).rsplit("/freshlint/", 1)[0], "PATH": ""},
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == f"freshlint {freshlint.__version__}"
