import pytest

from freshlint.catalog import RULES
from freshlint.config import ConfigError, load_config


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestDiscovery:
    def test_defaults_without_config(self, tmp_path):
        cfg = load_config(tmp_path, stop_at=tmp_path)
        assert cfg.root == tmp_path
        assert cfg.source is None
        assert cfg.include == ["**/*.md"]
        assert cfg.severity("missing-marker") == "warning"
        assert cfg.severity("freshness-laundering") == "error"
        assert cfg.receipt_paths == ["VERIFICATIONS.md"]
        assert cfg.receipt_order == "ascending"
        assert cfg.review_warn_days == 14
        assert cfg.max_suppression_days == 90

    def test_dotfile_config_sets_root(self, tmp_path):
        write(tmp_path / ".freshlint.toml", 'include = ["context/*.md"]\n')
        sub = tmp_path / "context"
        sub.mkdir()
        cfg = load_config(sub, stop_at=tmp_path)
        assert cfg.root == tmp_path
        assert cfg.include == ["context/*.md"]
        assert cfg.source == tmp_path / ".freshlint.toml"

    def test_pyproject_tool_table(self, tmp_path):
        write(tmp_path / "pyproject.toml", "[tool.freshlint]\nsection_level = 2\n")
        cfg = load_config(tmp_path, stop_at=tmp_path)
        assert cfg.section_level == 2

    def test_pyproject_without_table_is_skipped(self, tmp_path):
        write(tmp_path / ".freshlint.toml", "section_level = 3\n")
        write(tmp_path / "pkg" / "pyproject.toml", '[project]\nname = "x"\n')
        cfg = load_config(tmp_path / "pkg", stop_at=tmp_path)
        assert cfg.section_level == 3

    def test_dotfile_wins_over_pyproject(self, tmp_path):
        write(tmp_path / ".freshlint.toml", "section_level = 1\n")
        write(tmp_path / "pyproject.toml", "[tool.freshlint]\nsection_level = 2\n")
        assert load_config(tmp_path, stop_at=tmp_path).section_level == 1

    def test_search_stops_at_boundary(self, tmp_path):
        write(tmp_path / ".freshlint.toml", "section_level = 1\n")
        inner = tmp_path / "repo"
        inner.mkdir()
        cfg = load_config(inner, stop_at=inner)
        assert cfg.section_level == 0
        assert cfg.root == inner

    def test_explicit_config_path(self, tmp_path):
        path = write(tmp_path / "custom" / "lint.toml", "review_warn_days = 3\n")
        cfg = load_config(tmp_path, explicit=path, stop_at=tmp_path)
        assert cfg.review_warn_days == 3
        assert cfg.root == tmp_path / "custom"

    def test_explicit_pyproject_requires_table(self, tmp_path):
        path = write(tmp_path / "pyproject.toml", '[project]\nname = "x"\n')
        with pytest.raises(ConfigError, match=r"\[tool.freshlint\]"):
            load_config(tmp_path, explicit=path, stop_at=tmp_path)

    def test_missing_explicit_config(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path, explicit=tmp_path / "nope.toml", stop_at=tmp_path)


class TestValidation:
    @pytest.mark.parametrize(
        ("text", "fragment"),
        [
            ("includes = []\n", "unknown key 'includes'"),
            ('include = "a.md"\n', "include"),
            ('[rules]\nmissing-marker = "loud"\n', "missing-marker"),
            ('[rules]\nno-such-rule = "off"\n', "unknown rule 'no-such-rule'"),
            ('whitespace = "squash"\n', "whitespace"),
            ("section_level = 7\n", "section_level"),
            ("review_warn_days = -1\n", "review_warn_days"),
            ('[receipts]\norder = "random"\n', "order"),
            ("[receipts]\nfiles = []\n", "unknown key 'receipts.files'"),
            ("not toml = = \n", "invalid TOML"),
        ],
    )
    def test_bad_config_is_rejected(self, tmp_path, text, fragment):
        write(tmp_path / ".freshlint.toml", text)
        with pytest.raises(ConfigError, match=fragment):
            load_config(tmp_path, stop_at=tmp_path)

    def test_rule_overrides_and_warn_alias(self, tmp_path):
        write(
            tmp_path / ".freshlint.toml",
            '[rules]\nmissing-marker = "error"\n'
            'temporal-language = "off"\nstale-refresh = "warn"\n',
        )
        cfg = load_config(tmp_path, stop_at=tmp_path)
        assert cfg.severity("missing-marker") == "error"
        assert cfg.severity("temporal-language") == "off"
        assert cfg.severity("stale-refresh") == "warning"

    def test_every_catalog_rule_has_a_default(self, tmp_path):
        cfg = load_config(tmp_path, stop_at=tmp_path)
        for rule_id in RULES:
            assert cfg.severity(rule_id) in {"error", "warning", "off"}


class TestGovernance:
    def test_include_exclude_and_receipts(self, tmp_path):
        write(
            tmp_path / ".freshlint.toml",
            'include = ["**/*.md"]\nextend_exclude = ["drafts/**"]\n'
            '[receipts]\npaths = ["VERIFICATIONS.md", "evidence/*.md"]\n',
        )
        cfg = load_config(tmp_path, stop_at=tmp_path)
        assert cfg.is_governed("context/a.md")
        assert not cfg.is_governed("drafts/a.md")
        assert not cfg.is_governed("node_modules/pkg/README.md")
        assert not cfg.is_governed("notes.txt")
        assert not cfg.is_governed("VERIFICATIONS.md")
        assert not cfg.is_governed("evidence/x.md")
        assert cfg.is_receipt_file("evidence/x.md")
        assert cfg.is_receipt_file("VERIFICATIONS.md")
        assert not cfg.is_receipt_file("context/a.md")

    def test_exclude_replaces_defaults(self, tmp_path):
        write(tmp_path / ".freshlint.toml", "exclude = []\n")
        cfg = load_config(tmp_path, stop_at=tmp_path)
        assert cfg.is_governed("node_modules/pkg/README.md")
