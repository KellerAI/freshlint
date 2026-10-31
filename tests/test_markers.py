import datetime as dt

import pytest

from freshlint.frontmatter import parse_frontmatter, split_frontmatter
from freshlint.markers import Freshness, freshness_from_frontmatter, is_marker_line, parse_marker

D = dt.date


class TestParseMarker:
    def test_dynamic_marker(self):
        fresh, error = parse_marker("<!-- memory: verified=2026-09-01 review_by=2026-10-01 -->", 7)
        assert error is None
        assert fresh == Freshness(
            line=7, source="marker", verified=D(2026, 9, 1), review_by=D(2026, 10, 1)
        )

    def test_evergreen_marker(self):
        fresh, error = parse_marker("<!-- memory: verified=2026-09-01 cadence=evergreen -->", 3)
        assert error is None
        assert fresh.evergreen is True
        assert fresh.review_by is None

    def test_keys_in_any_order_and_extra_spaces(self):
        fresh, error = parse_marker(
            "  <!--memory:   review_by=2026-10-01  verified=2026-09-01-->", 1
        )
        assert error is None
        assert fresh.verified == D(2026, 9, 1)

    @pytest.mark.parametrize(
        ("line", "fragment"),
        [
            ("<!-- memory: review_by=2026-10-01 -->", "verified="),
            ("<!-- memory: verified=2026-09-01 -->", "review_by= or cadence=evergreen"),
            ("<!-- memory: verified=2026-9-1 review_by=2026-10-01 -->", "not an ISO date"),
            ("<!-- memory: verified=2026-09-01 reviewby=2026-10-01 -->", "unknown key 'reviewby'"),
            ("<!-- memory: verified=2026-09-01 cadence=weekly -->", "cadence"),
            (
                "<!-- memory: verified=2026-09-01 review_by=2026-10-01 cadence=evergreen -->",
                "both",
            ),
            ("<!-- memory: verified=2026-09-01 review_by=2026-10-01", "-->"),
            (
                "<!-- memory: verified=2026-09-01 verified=2026-09-02 review_by=2026-10-01 -->",
                "twice",
            ),
        ],
    )
    def test_invalid_markers_explain_themselves(self, line, fragment):
        fresh, error = parse_marker(line, 1)
        assert fresh is None
        assert fragment in error

    def test_marker_detection(self):
        assert is_marker_line("<!-- memory: verified=2026-09-01 cadence=evergreen -->")
        assert is_marker_line("<!--memory:")
        assert not is_marker_line("<!-- other comment -->")
        assert not is_marker_line("text <!-- memory: verified=2026-09-01 -->")


class TestDeadline:
    def test_review_by_expires_the_day_after(self):
        fresh = Freshness(line=1, source="marker", verified=D(2026, 9, 1), review_by=D(2026, 9, 30))
        assert fresh.expires_on() == D(2026, 10, 1)

    def test_stale_after_expires_on_its_date(self):
        fresh = Freshness(line=1, source="frontmatter", verified=None, stale_after=D(2026, 9, 30))
        assert fresh.expires_on() == D(2026, 9, 30)

    def test_earliest_deadline_wins(self):
        fresh = Freshness(
            line=1,
            source="frontmatter",
            verified=D(2026, 9, 1),
            review_by=D(2026, 12, 1),
            stale_after=D(2026, 10, 15),
        )
        assert fresh.expires_on() == D(2026, 10, 15)

    def test_max_age_applies_only_without_explicit_deadline(self):
        fresh = Freshness(line=1, source="frontmatter", verified=D(2026, 9, 1))
        assert fresh.expires_on() is None
        assert fresh.expires_on(max_age_days=30) == D(2026, 10, 2)

    def test_evergreen_never_expires(self):
        fresh = Freshness(line=1, source="marker", verified=D(2026, 1, 1), evergreen=True)
        assert fresh.expires_on(max_age_days=30) is None


def fm(text):
    entries, _ = split_frontmatter(text.splitlines())
    return parse_frontmatter(entries)


class TestFrontmatterFreshness:
    def test_absent(self):
        fresh, errors = freshness_from_frontmatter(fm("---\ntype: note\n---\n"))
        assert fresh is None
        assert errors == []

    def test_last_verified_and_review_by(self):
        fresh, errors = freshness_from_frontmatter(
            fm("---\ntype: note\nlast_verified: 2026-09-01\nreview_by: 2026-10-01\n---\n")
        )
        assert errors == []
        assert fresh.verified == D(2026, 9, 1)
        assert fresh.review_by == D(2026, 10, 1)
        assert fresh.line == 3
        assert fresh.source == "frontmatter"

    def test_hyphenated_spelling(self):
        fresh, _ = freshness_from_frontmatter(fm("---\nlast-verified: 2026-09-01\n---\n"))
        assert fresh.verified == D(2026, 9, 1)

    def test_okf_verified_list_uses_latest_at(self):
        fresh, errors = freshness_from_frontmatter(
            fm(
                "---\ntype: Metric\nverified:\n"
                "  - { by: human:alice, at: 2026-06-25T09:00:00Z }\n"
                "  - { by: process:nightly, at: 2026-06-26T02:00:00Z }\n"
                "stale_after: 2026-09-23T00:00:00Z\n---\n"
            )
        )
        assert errors == []
        assert fresh.verified == D(2026, 6, 26)
        assert fresh.stale_after == D(2026, 9, 23)
        assert fresh.events == (
            ("human:alice", "2026-06-25T09:00:00Z"),
            ("process:nightly", "2026-06-26T02:00:00Z"),
        )

    def test_okf_bare_mapping_is_one_element_list(self):
        fresh, errors = freshness_from_frontmatter(
            fm("---\nverified: { by: human:alice, at: 2026-06-25T09:00:00Z }\n---\n")
        )
        assert errors == []
        assert fresh.verified == D(2026, 6, 25)

    def test_okf_stale_after_without_verified_is_allowed(self):
        fresh, errors = freshness_from_frontmatter(
            fm("---\nstale_after: 2026-09-23T00:00:00Z\n---\n")
        )
        assert errors == []
        assert fresh.verified is None
        assert fresh.stale_after == D(2026, 9, 23)

    def test_okf_deprecated_status(self):
        fresh, _ = freshness_from_frontmatter(
            fm("---\nstatus: deprecated\nlast_verified: 2026-01-01\n---\n")
        )
        assert fresh.deprecated is True

    def test_evergreen_cadence(self):
        fresh, _ = freshness_from_frontmatter(
            fm("---\nlast_verified: 2026-01-01\ncadence: evergreen\n---\n")
        )
        assert fresh.evergreen is True

    @pytest.mark.parametrize(
        ("text", "fragment"),
        [
            ("---\nlast_verified: yesterday\n---\n", "last_verified"),
            ("---\nlast_verified: 2026-01-01\nreview_by: soon\n---\n", "review_by"),
            ("---\nverified: { by: human:alice }\n---\n", "verified"),
            ("---\nverified: [{ by: a, at: nope }]\n---\n", "verified"),
            ("---\nlast_verified: 2026-01-01\ncadence: monthly\n---\n", "cadence"),
            (
                "---\nlast_verified: 2026-01-01\ncadence: evergreen\nreview_by: 2026-02-01\n---\n",
                "evergreen",
            ),
            ("---\nreview_by: 2026-02-01\n---\n", "verified"),
        ],
    )
    def test_invalid_frontmatter_claims(self, text, fragment):
        _, errors = freshness_from_frontmatter(fm(text))
        assert errors
        assert any(fragment in message for _, message in errors)
