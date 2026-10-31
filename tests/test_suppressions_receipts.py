import datetime as dt

import pytest

from freshlint.markdown import parse_document
from freshlint.receipts import check_order, parse_receipt_log
from freshlint.suppressions import is_suppressed, parse_suppressions

TODAY = dt.date(2026, 9, 30)


def sup(rule="temporal-language", reason="known exception", expires="2026-10-30"):
    return f'<!-- freshlint: allow={rule} reason="{reason}" expires={expires} -->'


def parse(text, today=TODAY, max_days=90):
    return parse_suppressions(parse_document(text), today, max_days)


class TestSuppressions:
    def test_valid_suppression_scopes_to_enclosing_section(self):
        text = "# T\n\n## A\n" + sup() + "\ncurrently x\n### A1\ny\n## B\nz\n"
        valid, problems = parse(text)
        assert problems == []
        assert len(valid) == 1
        assert valid[0].scope == (3, 7)  # A plus its subsection, not B
        assert is_suppressed(valid, "temporal-language", 5)
        assert is_suppressed(valid, "temporal-language", 7)
        assert not is_suppressed(valid, "temporal-language", 9)
        assert not is_suppressed(valid, "missing-marker", 5)

    def test_preamble_suppression_scopes_to_file(self):
        text = sup(rule="missing-marker") + "\n# T\n## A\nx\n"
        valid, _ = parse(text)
        assert valid[0].scope == (1, 4)

    def test_trailing_suppression_scopes_to_its_line(self):
        text = "## A\nline one " + sup() + "\nline two\n"
        valid, _ = parse(text)
        assert valid[0].scope == (2, 2)

    def test_multiple_rules(self):
        valid, problems = parse("## A\n" + sup(rule="temporal-language,review-expired") + "\n")
        assert problems == []
        assert valid[0].rules == ("temporal-language", "review-expired")

    @pytest.mark.parametrize(
        ("comment", "fragment"),
        [
            (sup(reason=""), "reason"),
            (sup(reason="   "), "reason"),
            ("<!-- freshlint: allow=temporal-language expires=2026-10-01 -->", "malformed"),
            ('<!-- freshlint: allow=temporal-language reason="x" expires=2026-10-01', "malformed"),
            (sup(rule="no-such-rule"), "unknown rule"),
            (sup(rule="freshness-laundering"), "cannot be suppressed"),
            (sup(rule="suppression"), "cannot be suppressed"),
            (sup(expires="2026-13-01"), "ISO"),
            (sup(expires="2026-09-29"), "expired on 2026-09-29"),
            (sup(expires="2026-12-30"), "more than 90 days"),
        ],
    )
    def test_problems(self, comment, fragment):
        valid, problems = parse("## A\n" + comment + "\n")
        assert valid == []
        assert len(problems) == 1
        assert fragment in problems[0][1]
        assert problems[0][0] == 2

    def test_expiry_boundaries(self):
        # expires today: still valid; exactly max days away: valid; one more: invalid
        assert parse("## A\n" + sup(expires="2026-09-30") + "\n")[1] == []
        assert parse("## A\n" + sup(expires="2026-12-29") + "\n")[1] == []
        assert parse("## A\n" + sup(expires="2026-12-30") + "\n")[1] != []
        assert parse("## A\n" + sup(expires="2026-10-10") + "\n", max_days=10)[1] == []
        assert parse("## A\n" + sup(expires="2026-10-11") + "\n", max_days=10)[1] != []

    def test_suppressions_in_inline_code_are_ignored(self):
        text = '## A\nUse `<!-- freshlint: allow=RULE reason="why" expires=YYYY-MM-DD -->` here.\n'
        valid, problems = parse(text)
        assert valid == []
        assert problems == []

    def test_suppressions_in_code_fences_are_ignored(self):
        valid, problems = parse("## A\n```\n" + sup(reason="") + "\n```\n")
        assert valid == []
        assert problems == []


LOG = """# Verifications

## 2026-09-01
- verified context/a.md#pricing — re-checked the price list
- edited context/a.md — fixed a typo, facts not re-verified

## 2026-09-15
- 2026-09-15 verified context/b.md -- confirmed with the supplier
* verified context/c.md - checked
"""


class TestReceipts:
    def test_parse_receipts_with_inherited_and_explicit_dates(self):
        receipts, problems, dated = parse_receipt_log(LOG)
        assert problems == []
        assert [(r.date.isoformat(), r.verb, r.path, r.slug, r.line) for r in receipts] == [
            ("2026-09-01", "verified", "context/a.md", "pricing", 4),
            ("2026-09-01", "edited", "context/a.md", None, 5),
            ("2026-09-15", "verified", "context/b.md", None, 8),
            ("2026-09-15", "verified", "context/c.md", None, 9),
        ]
        assert receipts[0].note == "re-checked the price list"
        assert receipts[0].target == "context/a.md#pricing"
        assert [line for line, _ in dated] == [3, 7, 8]

    def test_prose_bullets_are_not_receipts(self):
        receipts, problems, _ = parse_receipt_log(
            "## 2026-09-01\n- verified with the supplier by phone\n- Observation.\n"
        )
        assert receipts == []
        assert problems == []

    @pytest.mark.parametrize(
        ("text", "fragment"),
        [
            ("## 2026-09-01\n- verified context/a.md\n", "note"),
            ("## 2026-09-01\n- verified context/a.md —\n", "note"),
            ("- verified context/a.md — undated\n", "no date"),
            ("## 2026-09-01\n- 2026-9-1 verified context/a.md — x\n", "not an ISO date"),
            ("## September 1\n- verified context/a.md — x\n", "ISO date"),
        ],
    )
    def test_malformed_receipts(self, text, fragment):
        _, problems, _ = parse_receipt_log(text)
        assert problems
        assert any(fragment in message for _, message in problems)

    def test_slug_is_lowercased_and_dot_slash_stripped(self):
        receipts, _, _ = parse_receipt_log("## 2026-09-01\n- verified ./context/a.md#Pricing — x\n")
        assert receipts[0].path == "context/a.md"
        assert receipts[0].slug == "pricing"

    def test_frontmatter_and_fences_are_skipped(self):
        text = "---\ntitle: log\n---\n```\n## not-a-date\n- verified a.md\n```\n"
        receipts, problems, _ = parse_receipt_log(text)
        assert receipts == []
        assert problems == []


class TestOrder:
    def test_ascending_ok(self):
        _, _, dated = parse_receipt_log(LOG)
        assert check_order(dated, "ascending") == []

    def test_non_monotonic_ascending(self):
        _, _, dated = parse_receipt_log(
            "## 2026-09-15\n- verified a.md — x\n## 2026-09-01\n- verified b.md — y\n"
        )
        problems = check_order(dated, "ascending")
        assert len(problems) == 1
        assert problems[0][0] == 3
        assert "2026-09-01" in problems[0][1] and "2026-09-15" in problems[0][1]

    def test_explicit_date_out_of_order_within_group(self):
        _, _, dated = parse_receipt_log(
            "## 2026-09-15\n- 2026-09-16 verified a.md — x\n- 2026-09-14 verified b.md — y\n"
        )
        problems = check_order(dated, "ascending")
        assert [line for line, _ in problems] == [3]

    def test_descending_logs(self):
        _, _, dated = parse_receipt_log("## 2026-09-15\n## 2026-09-01\n")
        assert check_order(dated, "descending") == []
        assert check_order(dated, "ascending") != []

    def test_equal_dates_are_fine(self):
        _, _, dated = parse_receipt_log("## 2026-09-15\n## 2026-09-15\n")
        assert check_order(dated, "ascending") == []
        assert check_order(dated, "descending") == []
