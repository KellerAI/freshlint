"""Full-scan rules: review-expired, missing-marker, invalid-marker, temporal-language,
suppression and receipt-ordering. ``--today`` is fixed at 2026-09-30 unless stated."""

import pytest


def marker(verified="2026-09-01", review_by="2026-12-01"):
    return f"<!-- memory: verified={verified} review_by={review_by} -->"


def section_file(*sections, frontmatter=""):
    head = f"---\n{frontmatter}---\n" if frontmatter else ""
    body = "# Memory\n\n" + "\n".join(f"## {title}\n{m}\n\n{text}\n" for title, m, text in sections)
    return head + body


def only(result, rule):
    return [f for f in result.findings if f["rule"] == rule]


class TestCleanAndExitCodes:
    def test_clean_project_exits_zero(self, project):
        project.write("context/a.md", section_file(("A", marker(), "Fact.")))
        result = project.lint()
        assert result.code == 0
        assert result.findings == []
        assert result.payload["summary"] == {"errors": 0, "warnings": 0, "files": 1}

    def test_warnings_do_not_fail_unless_strict(self, project):
        project.write("context/a.md", "# No metadata\n\ntext\n")
        assert project.lint().code == 0
        assert project.lint("--strict").code == 1


class TestReviewExpired:
    @pytest.mark.parametrize(
        ("review_by", "severity", "fragment"),
        [
            ("2026-09-29", "error", "1 day ago"),
            ("2026-09-01", "error", "29 days ago"),
            ("2026-09-30", "warning", "due today"),
            ("2026-10-01", "warning", "due in 1 day"),
            ("2026-10-13", "warning", "due in 13 days"),
        ],
    )
    def test_section_review_boundaries(self, project, review_by, severity, fragment):
        project.write("a.md", section_file(("Carriers", marker("2026-08-01", review_by), "x")))
        result = project.lint()
        found = only(result, "review-expired")
        assert len(found) == 1
        assert found[0]["severity"] == severity
        assert fragment in found[0]["message"]
        assert '"Carriers"' in found[0]["message"]
        assert found[0]["line"] == 4  # the marker line
        assert result.code == (1 if severity == "error" else 0)

    def test_outside_warning_window_is_clean(self, project):
        project.write("a.md", section_file(("A", marker("2026-08-01", "2026-10-14"), "x")))
        assert only(project.lint(), "review-expired") == []

    def test_warn_window_is_configurable(self, project):
        project.write(".freshlint.toml", "review_warn_days = 0\n")
        project.write("a.md", section_file(("A", marker("2026-08-01", "2026-09-30"), "x")))
        assert only(project.lint(), "review-expired") == []

    def test_evergreen_never_expires(self, project):
        project.write(
            "a.md",
            section_file(("A", "<!-- memory: verified=2020-01-01 cadence=evergreen -->", "x")),
        )
        assert project.lint().findings == []

    def test_file_level_review_by(self, project):
        project.write("a.md", "---\nlast_verified: 2026-08-01\nreview_by: 2026-09-15\n---\n# A\n")
        found = only(project.lint(), "review-expired")
        assert found[0]["severity"] == "error"
        assert found[0]["line"] == 2
        assert found[0]["message"].startswith("File")

    def test_okf_stale_after_boundary(self, project):
        project.write("a.md", "---\ntype: Metric\nstale_after: 2026-09-30T00:00:00Z\n---\n# A\n")
        found = only(project.lint(), "review-expired")
        assert found[0]["severity"] == "error"
        assert "stale_after" in found[0]["message"]
        project.write("a.md", "---\ntype: Metric\nstale_after: 2026-10-01T00:00:00Z\n---\n# A\n")
        found = only(project.lint(), "review-expired")
        assert found[0]["severity"] == "warning"

    def test_okf_deprecated_is_not_reviewed(self, project):
        project.write(
            "a.md", "---\nstatus: deprecated\nstale_after: 2026-01-01T00:00:00Z\n---\n# A\n"
        )
        assert only(project.lint(), "review-expired") == []

    def test_max_age_days(self, project):
        project.write(".freshlint.toml", "max_age_days = 30\n")
        project.write("a.md", "---\nlast_verified: 2026-08-30\n---\n# A\n")
        assert only(project.lint(), "review-expired")[0]["severity"] == "error"
        project.write("a.md", "---\nlast_verified: 2026-08-31\n---\n# A\n")
        assert only(project.lint(), "review-expired")[0]["severity"] == "warning"

    def test_today_override_changes_outcome(self, project):
        project.write("a.md", section_file(("A", marker("2026-08-01", "2026-10-31"), "x")))
        assert project.lint(today="2026-09-01").findings == []
        assert project.lint(today="2026-11-01").code == 1

    def test_rule_can_be_turned_off_or_downgraded(self, project):
        project.write("a.md", section_file(("A", marker("2026-08-01", "2026-09-01"), "x")))
        project.write(".freshlint.toml", '[rules]\nreview-expired = "warn"\n')
        result = project.lint()
        assert result.code == 0
        assert result.rules("warning") == ["review-expired"]
        project.write(".freshlint.toml", '[rules]\nreview-expired = "off"\n')
        assert project.lint().findings == []


class TestMissingMarker:
    def test_file_without_metadata_warns(self, project):
        project.write("a.md", "# Title\n\nSome fact.\n")
        found = only(project.lint(), "missing-marker")
        assert found == [
            {
                "rule": "missing-marker",
                "severity": "warning",
                "path": "a.md",
                "line": 1,
                "message": "File has no freshness metadata (frontmatter last_verified or "
                "section markers)",
                "hint": found[0]["hint"],
            }
        ]

    def test_frontmatter_claim_is_enough_by_default(self, project):
        project.write("a.md", "---\nlast_verified: 2026-09-01\n---\n# T\n## A\nx\n")
        assert project.lint().findings == []

    def test_section_level_requires_markers_on_that_level(self, project):
        project.write(".freshlint.toml", "section_level = 2\n")
        project.write("a.md", "# T\n\n## A\n" + marker() + "\nx\n### A1\nnot required\n## B\n\ny\n")
        found = only(project.lint(), "missing-marker")
        assert [(f["line"], '"B"' in f["message"]) for f in found] == [(8, True)]

    def test_section_level_on_file_without_such_headings(self, project):
        project.write(".freshlint.toml", "section_level = 2\n")
        project.write("a.md", "# T\n\nx\n")
        found = only(project.lint(), "missing-marker")
        assert [f["line"] for f in found] == [1]

    def test_severity_levels(self, project):
        project.write("a.md", "# T\n")
        project.write(".freshlint.toml", '[rules]\nmissing-marker = "error"\n')
        assert project.lint().code == 1
        project.write(".freshlint.toml", '[rules]\nmissing-marker = "off"\n')
        assert project.lint().findings == []

    def test_globs_decide_what_is_governed(self, project):
        project.write(".freshlint.toml", 'include = ["context/*.md"]\n')
        project.write("context/a.md", "# T\n")
        project.write("README.md", "# readme\n")
        project.write("context/deep/b.md", "# T\n")
        result = project.lint()
        assert [f["path"] for f in result.findings] == ["context/a.md"]
        assert result.payload["summary"]["files"] == 1


class TestInvalidMarker:
    def test_malformed_marker(self, project):
        project.write("a.md", "## A\n<!-- memory: verified=2026-09-01 -->\nx\n")
        found = only(project.lint(), "invalid-marker")
        assert found[0]["line"] == 2
        assert project.lint().code == 1

    def test_misplaced_and_duplicate_markers(self, project):
        project.write("a.md", "## A\n" + marker() + "\n" + marker() + "\nx\n" + marker() + "\n")
        found = only(project.lint(), "invalid-marker")
        assert [f["line"] for f in found] == [3, 5]

    def test_future_verified_date(self, project):
        project.write("a.md", "## A\n" + marker("2026-10-01", "2026-12-01") + "\nx\n")
        found = only(project.lint(), "invalid-marker")
        assert "in the future" in found[0]["message"]

    def test_review_by_before_verified(self, project):
        project.write("a.md", "## A\n" + marker("2026-09-10", "2026-09-01") + "\nx\n")
        found = only(project.lint(), "invalid-marker")
        assert "before" in found[0]["message"]

    def test_bad_frontmatter_date(self, project):
        project.write("a.md", "---\nlast_verified: last week\n---\n# A\n")
        found = only(project.lint(), "invalid-marker")
        assert found[0]["line"] == 2

    def test_invalid_marker_cannot_be_suppressed(self, project):
        project.write(
            "a.md",
            '<!-- freshlint: allow=invalid-marker reason="x" expires=2026-10-01 -->\n'
            "## A\n<!-- memory: nonsense -->\n",
        )
        rules = project.lint().rules()
        assert "invalid-marker" in rules
        assert "suppression" in rules


class TestTemporalLanguage:
    def test_relative_phrase_warns(self, project):
        project.write("a.md", section_file(("A", marker(), "We currently ship to 3 regions.")))
        found = only(project.lint(), "temporal-language")
        assert found[0]["severity"] == "warning"
        assert '"currently"' in found[0]["message"]
        assert found[0]["line"] == 6

    def test_expired_deadline_phrase(self, project):
        project.write("a.md", section_file(("A", marker(), "Target launch by June 2026.")))
        found = only(project.lint(), "temporal-language")
        assert "June 2026" in found[0]["message"]
        assert "2026-06-30" in found[0]["message"]

    def test_future_deadline_is_fine(self, project):
        project.write("a.md", section_file(("A", marker(), "Target launch by Q4 2026.")))
        assert only(project.lint(), "temporal-language") == []

    def test_code_is_ignored(self, project):
        project.write(
            "a.md",
            section_file(("A", marker(), "Run `currently` flag.\n\n```\ncurrently\n```")),
        )
        assert only(project.lint(), "temporal-language") == []

    def test_custom_phrases(self, project):
        project.write(".freshlint.toml", 'temporal_phrases = ["for now"]\n')
        project.write("a.md", section_file(("A", marker(), "Currently fine. Pinned for now.")))
        found = only(project.lint(), "temporal-language")
        assert len(found) == 1
        assert '"for now"' in found[0]["message"]

    def test_suppression_with_reason_silences_it(self, project):
        project.write(
            "a.md",
            section_file(
                (
                    "A",
                    marker(),
                    '<!-- freshlint: allow=temporal-language reason="quoting a customer" '
                    'expires=2026-10-30 -->\n"We currently love it."',
                )
            ),
        )
        assert project.lint().findings == []

    def test_can_be_disabled(self, project):
        project.write(".freshlint.toml", '[rules]\ntemporal-language = "off"\n')
        project.write("a.md", section_file(("A", marker(), "currently")))
        assert project.lint().findings == []


class TestSuppressionRule:
    def test_expired_suppression_is_an_error_and_does_not_apply(self, project):
        project.write(
            "a.md",
            section_file(
                (
                    "A",
                    marker(),
                    '<!-- freshlint: allow=temporal-language reason="r" expires=2026-09-29 -->\n'
                    "currently",
                )
            ),
        )
        result = project.lint()
        assert sorted(result.rules()) == ["suppression", "temporal-language"]
        assert result.code == 1

    def test_reasonless_suppression(self, project):
        project.write(
            "a.md",
            '<!-- freshlint: allow=missing-marker reason="" expires=2026-10-01 -->\n# T\n',
        )
        assert sorted(project.lint().rules()) == ["missing-marker", "suppression"]

    def test_file_scope_suppression_of_missing_marker(self, project):
        project.write(
            "a.md",
            '<!-- freshlint: allow=missing-marker reason="index page" expires=2026-10-01 -->\n'
            "# T\n",
        )
        assert project.lint().findings == []

    def test_max_suppression_days_configurable(self, project):
        project.write(".freshlint.toml", "max_suppression_days = 7\n")
        project.write(
            "a.md",
            '<!-- freshlint: allow=missing-marker reason="r" expires=2026-10-08 -->\n# T\n',
        )
        assert "suppression" in project.lint().rules()


class TestReceiptOrdering:
    def test_clean_log(self, project):
        project.write(
            "VERIFICATIONS.md",
            "# Verifications\n\n## 2026-09-01\n- verified a.md — checked\n\n"
            "## 2026-09-20\n- verified a.md#x — checked\n",
        )
        assert project.lint().findings == []

    def test_non_monotonic_log(self, project):
        project.write(
            "VERIFICATIONS.md",
            "## 2026-09-20\n- verified a.md — checked\n## 2026-09-01\n- verified b.md — y\n",
        )
        result = project.lint()
        found = only(result, "receipt-ordering")
        assert [f["line"] for f in found] == [3]
        assert result.code == 1

    def test_future_dated_receipt(self, project):
        project.write("VERIFICATIONS.md", "## 2026-10-01\n- verified a.md — checked\n")
        found = only(project.lint(), "receipt-ordering")
        assert "future" in found[0]["message"]

    def test_malformed_receipt(self, project):
        project.write("VERIFICATIONS.md", "## 2026-09-01\n- verified a.md\n")
        assert only(project.lint(), "receipt-ordering")

    def test_descending_order_option(self, project):
        project.write(".freshlint.toml", '[receipts]\norder = "descending"\npaths = ["log.md"]\n')
        project.write("log.md", "## 2026-09-20\n- verified a.md — x\n## 2026-09-01\n")
        assert project.lint().findings == []

    def test_evidence_logs_via_receipt_globs(self, project):
        project.write(".freshlint.toml", '[receipts]\npaths = ["evidence/*.md"]\n')
        project.write(
            "evidence/a.md", "# Evidence\n\n## 2026-09-02\n- Observation.\n## 2026-09-01\n"
        )
        found = only(project.lint(), "receipt-ordering")
        assert [f["path"] for f in found] == ["evidence/a.md"]
