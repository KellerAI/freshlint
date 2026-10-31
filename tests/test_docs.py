"""Every rule in the catalog is documented, and the docs agree with the catalog."""

from pathlib import Path

import pytest

from freshlint.catalog import RULES, docs_path

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_rule_has_a_docs_page(rule_id):
    page = ROOT / docs_path(rule_id)
    assert page.is_file(), f"missing {page}"
    text = page.read_text()
    assert text.startswith(f"# {rule_id}\n")
    rule = RULES[rule_id]
    assert f"**Default severity:** {rule.default}" in text
    assert f"**Mode:** {rule.mode}" in text
    assert f"**Inline suppression:** {'allowed' if rule.suppressible else 'not allowed'}" in text
    assert "## Example" in text


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_readme_rule_catalog_lists_rule(rule_id):
    readme = (ROOT / "README.md").read_text()
    assert f"[`{rule_id}`]({docs_path(rule_id)})" in readme


def test_no_orphan_rule_pages():
    pages = {p.stem for p in (ROOT / "docs" / "rules").glob("*.md")}
    assert pages == set(RULES)
