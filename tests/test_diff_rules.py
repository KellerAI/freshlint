"""Diff-mode rules: freshness-laundering, stale-refresh, receipts, receipt-append-only.
Each test builds a real git repository; most use ``--staged`` (index vs HEAD)."""

import pytest

OLD = "<!-- memory: verified=2026-08-15 review_by=2026-10-15 -->"
NEW = "<!-- memory: verified=2026-09-30 review_by=2026-12-30 -->"

SHIPPING = f"""# Shipping

Intro paragraph.

## Carriers
{OLD}

We ship with two regional carriers.

## Rates
{OLD}

Flat rate per crate.
"""


def only(result, rule):
    return [f for f in result.findings if f["rule"] == rule]


@pytest.fixture
def base(repo):
    repo.write("context/shipping.md", SHIPPING)
    repo.write(
        "VERIFICATIONS.md", "# Verifications\n\n## 2026-08-15\n- verified context/x.md — seed\n"
    )
    repo.commit("baseline")
    return repo


def bump_carriers(repo, new=NEW):
    text = repo.read("context/shipping.md")
    head, tail = text.split("## Rates", 1)
    repo.write("context/shipping.md", head.replace(OLD, new) + "## Rates" + tail)


class TestLaundering:
    def test_marker_only_bump_is_laundering(self, base):
        bump_carriers(base)
        base.stage()
        result = base.lint("--staged")
        found = only(result, "freshness-laundering")
        assert len(found) == 1
        assert result.code == 1
        f = found[0]
        assert f["path"] == "context/shipping.md"
        assert f["line"] == 6
        assert '"Carriers"' in f["message"]
        assert "2026-08-15 -> 2026-09-30" in f["message"]
        assert "- 2026-09-30 verified context/shipping.md#carriers — " in f["hint"]

    def test_receipt_with_same_date_and_section_covers_it(self, base):
        bump_carriers(base)
        base.append(
            "VERIFICATIONS.md",
            "\n## 2026-09-30\n- verified context/shipping.md#carriers — re-checked carrier list\n",
        )
        base.stage()
        result = base.lint("--staged")
        assert result.findings == []
        assert result.code == 0

    def test_receipt_with_wrong_date_does_not_cover(self, base):
        bump_carriers(base)
        base.append(
            "VERIFICATIONS.md",
            "\n## 2026-09-29\n- verified context/shipping.md#carriers — checked\n",
        )
        base.stage()
        found = only(base.lint("--staged"), "freshness-laundering")
        assert len(found) == 1
        assert "dated 2026-09-29" in found[0]["hint"]

    def test_receipt_for_wrong_section_does_not_cover(self, base):
        bump_carriers(base)
        base.append(
            "VERIFICATIONS.md", "\n## 2026-09-30\n- verified context/shipping.md#rates — checked\n"
        )
        base.stage()
        found = only(base.lint("--staged"), "freshness-laundering")
        assert len(found) == 1
        assert "#rates" in found[0]["hint"]

    def test_file_level_receipt_covers_every_section(self, base):
        text = base.read("context/shipping.md").replace(OLD, NEW)
        base.write("context/shipping.md", text)
        base.append(
            "VERIFICATIONS.md", "\n## 2026-09-30\n- verified context/shipping.md — full review\n"
        )
        base.stage()
        assert base.lint("--staged").findings == []

    def test_preexisting_receipt_does_not_cover_a_new_bump(self, repo):
        repo.write("context/shipping.md", SHIPPING)
        repo.write(
            "VERIFICATIONS.md",
            "## 2026-09-30\n- verified context/shipping.md#carriers — an older claim\n",
        )
        repo.commit("baseline")
        bump_carriers(repo)
        repo.stage()
        assert only(repo.lint("--staged"), "freshness-laundering")

    def test_old_receipt_does_not_count_when_the_log_also_changes(self, repo):
        repo.write("context/shipping.md", SHIPPING)
        repo.write(
            "VERIFICATIONS.md",
            "## 2026-09-30\n- verified context/shipping.md#carriers — an older claim\n",
        )
        repo.commit("baseline")
        bump_carriers(repo)
        repo.append("VERIFICATIONS.md", "- verified context/other.md — unrelated\n")
        repo.stage()
        assert only(repo.lint("--staged"), "freshness-laundering")

    def test_receipt_outside_receipt_paths_does_not_count(self, base):
        bump_carriers(base)
        base.write(
            "notes.txt", "## 2026-09-30\n- verified context/shipping.md#carriers — checked\n"
        )
        base.write("NOTES.md", "## 2026-09-30\n- verified context/shipping.md#carriers — checked\n")
        base.stage()
        assert only(base.lint("--staged"), "freshness-laundering")

    def test_review_by_extension_without_verification(self, base):
        bump_carriers(base, "<!-- memory: verified=2026-08-15 review_by=2027-03-01 -->")
        base.append(
            "VERIFICATIONS.md",
            "\n## 2026-08-15\n- verified context/shipping.md#carriers — backdated\n",
        )
        base.stage()
        found = only(base.lint("--staged"), "freshness-laundering")
        assert len(found) == 1
        assert "review date" in found[0]["message"]

    def test_switch_to_evergreen_without_change(self, base):
        bump_carriers(base, "<!-- memory: verified=2026-08-15 cadence=evergreen -->")
        base.stage()
        found = only(base.lint("--staged"), "freshness-laundering")
        assert "evergreen" in found[0]["message"]

    def test_whitespace_only_edit_is_still_laundering(self, base):
        text = base.read("context/shipping.md").replace(
            "We ship with two regional carriers.", "We ship with two regional carriers.   \n\n"
        )
        base.write("context/shipping.md", text)
        bump_carriers(base)
        base.stage()
        assert only(base.lint("--staged"), "freshness-laundering")

    def test_exact_whitespace_mode_counts_whitespace_as_change(self, base):
        base.write(".freshlint.toml", 'whitespace = "exact"\n')
        base.commit("config")
        text = base.read("context/shipping.md").replace(
            "We ship with two regional carriers.", "We ship with two regional carriers.  "
        )
        base.write("context/shipping.md", text)
        bump_carriers(base)
        base.stage()
        assert only(base.lint("--staged"), "freshness-laundering") == []

    def test_real_content_change_with_bump_is_honest(self, base):
        text = base.read("context/shipping.md").replace("two regional", "three regional")
        base.write("context/shipping.md", text)
        bump_carriers(base)
        base.stage()
        assert base.lint("--staged").findings == []

    def test_nested_child_bump_with_parent_unchanged(self, repo):
        child_old = "<!-- memory: verified=2026-08-15 cadence=evergreen -->"
        child_new = "<!-- memory: verified=2026-09-30 cadence=evergreen -->"
        repo.write(
            "a.md",
            f"## Parent\n{OLD}\n\nparent text\n\n### Child\n{child_old}\n\nchild text\n",
        )
        repo.commit("baseline")
        repo.replace("a.md", child_old, child_new)
        repo.stage()
        found = only(repo.lint("--staged"), "freshness-laundering")
        assert len(found) == 1
        assert '"Child"' in found[0]["message"]

    def test_parent_content_includes_unmarked_child(self, repo):
        repo.write("a.md", f"## Parent\n{OLD}\n\nparent text\n\n### Child\n\nchild text\n")
        repo.commit("baseline")
        repo.write("a.md", f"## Parent\n{NEW}\n\nparent text\n\n### Child\n\nchild text v2\n")
        repo.stage()
        assert repo.lint("--staged").findings == []


class TestFileLevel:
    FM_OLD = "---\ntype: context\nlast_verified: 2026-08-15\nreview_by: 2026-10-15\n---\n"
    FM_NEW = "---\ntype: context\nlast_verified: 2026-09-30\nreview_by: 2026-12-30\n---\n"

    def test_last_verified_bump_without_body_change(self, repo):
        repo.write("a.md", self.FM_OLD + "# A\n\nfact\n")
        repo.commit("baseline")
        repo.write("a.md", self.FM_NEW + "# A\n\nfact\n")
        repo.stage()
        found = only(repo.lint("--staged"), "freshness-laundering")
        assert len(found) == 1
        assert found[0]["line"] == 3
        assert found[0]["message"].startswith("File")
        assert "- 2026-09-30 verified a.md — " in found[0]["hint"]

    def test_frontmatter_only_changes_are_not_content(self, repo):
        repo.write("a.md", self.FM_OLD + "# A\n\nfact\n")
        repo.commit("baseline")
        repo.write(
            "a.md",
            self.FM_NEW.replace("type: context", "type: context\ntags: [x]") + "# A\n\nfact\n",
        )
        repo.stage()
        assert only(repo.lint("--staged"), "freshness-laundering")

    def test_frontmatter_only_change_without_date_bump_is_clean(self, repo):
        repo.write("a.md", self.FM_OLD + "# A\n\nfact\n")
        repo.commit("baseline")
        repo.write("a.md", self.FM_OLD.replace("type: context", "type: note") + "# A\n\nfact\n")
        repo.stage()
        assert repo.lint("--staged").findings == []

    def test_bumping_file_and_section_markers_only_is_laundering(self, repo):
        # The reference implementation missed this: section markers live in the body.
        repo.write("a.md", self.FM_OLD + f"# A\n\n## S\n{OLD}\n\nfact\n")
        repo.commit("baseline")
        repo.write("a.md", self.FM_NEW + f"# A\n\n## S\n{NEW}\n\nfact\n")
        repo.stage()
        found = only(repo.lint("--staged"), "freshness-laundering")
        assert sorted(f["line"] for f in found) == [3, 9]

    def test_file_receipt_covers_file_claim(self, repo):
        repo.write("a.md", self.FM_OLD + "# A\n\nfact\n")
        repo.commit("baseline")
        repo.write("a.md", self.FM_NEW + "# A\n\nfact\n")
        repo.write("VERIFICATIONS.md", "## 2026-09-30\n- verified a.md — re-read against source\n")
        repo.stage()
        assert repo.lint("--staged").findings == []

    def test_section_receipt_does_not_cover_file_claim(self, repo):
        repo.write("a.md", self.FM_OLD + f"# A\n\n## S\n{OLD}\n\nfact\n")
        repo.commit("baseline")
        repo.write("a.md", self.FM_NEW + f"# A\n\n## S\n{NEW}\n\nfact\n")
        repo.write("VERIFICATIONS.md", "## 2026-09-30\n- verified a.md#s — checked\n")
        repo.stage()
        found = only(repo.lint("--staged"), "freshness-laundering")
        assert [f["line"] for f in found] == [3]

    def test_okf_appended_verification_without_change(self, repo):
        old = "---\ntype: Metric\nverified:\n  - { by: human:ana, at: 2026-08-15T09:00:00Z }\n---\n"
        new = old.replace(
            "  - { by: human:ana, at: 2026-08-15T09:00:00Z }\n",
            "  - { by: human:ana, at: 2026-08-15T09:00:00Z }\n"
            "  - { by: agent/2, at: 2026-09-30T08:00:00Z }\n",
        )
        repo.write("m.md", old + "# Definition\n\nRevenue minus refunds.\n")
        repo.commit("baseline")
        repo.write("m.md", new + "# Definition\n\nRevenue minus refunds.\n")
        repo.stage()
        found = only(repo.lint("--staged"), "freshness-laundering")
        assert len(found) == 1
        assert "2026-08-15 -> 2026-09-30" in found[0]["message"]


class TestStaleRefresh:
    def test_content_change_without_bump(self, base):
        base.replace("context/shipping.md", "Flat rate per crate.", "Flat rate per pallet.")
        base.stage()
        result = base.lint("--staged")
        found = only(result, "stale-refresh")
        assert len(found) == 1
        assert '"Rates"' in found[0]["message"]
        assert "verified=2026-08-15" in found[0]["message"]
        assert found[0]["line"] == 11
        assert result.code == 1

    def test_content_change_with_bump_is_fine(self, base):
        text = base.read("context/shipping.md").replace("Flat rate per crate.", "Per pallet.")
        head, tail = text.split("## Rates", 1)
        base.write("context/shipping.md", head + "## Rates" + tail.replace(OLD, NEW))
        base.stage()
        assert base.lint("--staged").findings == []

    def test_same_day_edit_is_fine(self, base):
        bump_carriers(base, "<!-- memory: verified=2026-09-30 review_by=2026-12-30 -->")
        base.replace("context/shipping.md", "two regional", "three regional")
        base.commit("first edit today")
        base.replace("context/shipping.md", "three regional", "four regional")
        base.stage()
        assert base.lint("--staged").findings == []

    def test_edited_receipt_covers_non_substantive_edit(self, base):
        base.replace("context/shipping.md", "Flat rate per crate.", "Flat rate per crate (CAD).")
        base.append(
            "VERIFICATIONS.md",
            "\n## 2026-09-30\n- edited context/shipping.md#rates — currency label only\n",
        )
        base.stage()
        assert base.lint("--staged").findings == []

    def test_file_level_forward_check_ignores_marked_sections(self, repo):
        fm = "---\nlast_verified: 2026-08-15\n---\n"
        repo.write("a.md", fm + f"# A\n\nintro\n\n## S\n{OLD}\n\nfact\n")
        repo.commit("baseline")
        repo.write("a.md", fm + f"# A\n\nintro\n\n## S\n{NEW}\n\nnew fact\n")
        repo.stage()
        assert repo.lint("--staged").findings == []
        repo.commit("section edit")
        repo.write("a.md", fm + f"# A\n\nintro v2\n\n## S\n{NEW}\n\nnew fact\n")
        repo.stage()
        found = only(repo.lint("--staged"), "stale-refresh")
        assert [f["line"] for f in found] == [2]
        assert found[0]["message"].startswith("File")

    def test_can_be_downgraded(self, base):
        base.write(".freshlint.toml", '[rules]\nstale-refresh = "warn"\n')
        base.commit("config")
        base.replace("context/shipping.md", "Flat rate per crate.", "Per pallet.")
        base.stage()
        result = base.lint("--staged")
        assert result.code == 0
        assert result.rules("warning") == ["stale-refresh"]


class TestStructuralChanges:
    def test_new_file_and_new_section_have_no_diff_findings(self, base):
        base.write("context/new.md", f"# New\n\n## Fresh\n{NEW}\n\ntext\n")
        base.append("context/shipping.md", f"\n## Brand new\n{NEW}\n\nnew text\n")
        base.stage()
        assert base.lint("--staged").findings == []

    def test_deleted_section_and_file(self, base):
        text = base.read("context/shipping.md")
        base.write("context/shipping.md", text.split("## Rates")[0])
        base.stage()
        assert base.lint("--staged").findings == []
        base.remove("context/shipping.md")
        base.stage()
        assert base.lint("--staged").findings == []

    def test_renamed_heading_is_a_new_section(self, base):
        base.replace("context/shipping.md", "## Carriers", "## Shipping carriers")
        bump_carriers(base)
        base.stage()
        assert base.lint("--staged").findings == []

    def test_renamed_file_is_compared_with_its_old_path(self, base):
        base.git("mv", "context/shipping.md", "context/logistics.md")
        base.commit("rename")
        base.git("mv", "context/logistics.md", "context/delivery.md")
        text = base.read("context/delivery.md")
        head, tail = text.split("## Rates", 1)
        base.write("context/delivery.md", head.replace(OLD, NEW) + "## Rates" + tail)
        base.stage()
        found = only(base.lint("--staged"), "freshness-laundering")
        assert [f["path"] for f in found] == ["context/delivery.md"]
        assert "context/delivery.md#carriers" in found[0]["hint"]

    def test_moved_section_between_files_is_new(self, base):
        base.write(
            "context/other.md",
            f"# Other\n\n## Carriers\n{NEW}\n\nWe ship with two regional carriers.\n",
        )
        text = base.read("context/shipping.md")
        head, tail = text.split("## Rates", 1)
        base.write("context/shipping.md", head.split("## Carriers")[0] + "## Rates" + tail)
        base.stage()
        assert base.lint("--staged").findings == []


class TestAppendOnly:
    def test_editing_an_existing_receipt(self, base):
        base.replace("VERIFICATIONS.md", "## 2026-08-15", "## 2026-09-30")
        base.stage()
        found = only(base.lint("--staged"), "receipt-append-only")
        assert len(found) == 1
        assert "2026-08-15 verified context/x.md" in found[0]["message"]

    def test_deleting_the_log(self, base):
        base.remove("VERIFICATIONS.md")
        base.stage()
        assert only(base.lint("--staged"), "receipt-append-only")

    def test_rewriting_the_log_is_reported_even_if_it_adds_a_receipt(self, base):
        bump_carriers(base)
        base.write(
            "VERIFICATIONS.md",
            "# Verifications\n\n## 2026-09-30\n- verified context/shipping.md#carriers — x\n",
        )
        base.stage()
        rules = base.lint("--staged").rules()
        assert "receipt-append-only" in rules
        # the added receipt does cover the bump; the rewrite is what is reported
        assert "freshness-laundering" not in rules

    def test_appending_is_fine(self, base):
        base.append("VERIFICATIONS.md", "- verified context/y.md — another\n")
        base.stage()
        assert base.lint("--staged").findings == []
