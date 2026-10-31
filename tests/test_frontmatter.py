import datetime as dt

from freshlint.dates import parse_date, parse_instant_date
from freshlint.frontmatter import parse_frontmatter, split_frontmatter


def lines(text):
    return text.splitlines()


class TestDates:
    def test_iso_date(self):
        assert parse_date("2026-09-30") == dt.date(2026, 9, 30)

    def test_rejects_non_iso_shapes(self):
        for bad in ["2026-9-30", "20260930", "Sept 30 2026", "", None, "2026-02-30"]:
            assert parse_date(bad) is None

    def test_instant_with_z_converts_to_utc_date(self):
        assert parse_instant_date("2026-06-25T09:00:00Z") == dt.date(2026, 6, 25)

    def test_instant_with_offset_converts_to_utc_date(self):
        # 23:30 at -02:00 is 01:30 UTC the next day
        assert parse_instant_date("2026-06-25T23:30:00-02:00") == dt.date(2026, 6, 26)

    def test_instant_accepts_plain_date(self):
        assert parse_instant_date("2026-06-25") == dt.date(2026, 6, 25)

    def test_instant_rejects_garbage(self):
        assert parse_instant_date("soon") is None


class TestSplit:
    def test_no_frontmatter(self):
        fm, start = split_frontmatter(lines("# Title\n\ntext\n"))
        assert fm == []
        assert start == 0

    def test_frontmatter_block(self):
        fm, start = split_frontmatter(lines("---\na: 1\nb: 2\n---\n# Title\n"))
        assert fm == [(2, "a: 1"), (3, "b: 2")]
        assert start == 4

    def test_unclosed_frontmatter_is_not_frontmatter(self):
        fm, start = split_frontmatter(lines("---\na: 1\n# Title\n"))
        assert fm == []
        assert start == 0

    def test_dots_close_frontmatter(self):
        fm, start = split_frontmatter(lines("---\na: 1\n...\nbody\n"))
        assert fm == [(2, "a: 1")]
        assert start == 3


class TestParse:
    def parse(self, text):
        fm, _ = split_frontmatter(lines(text))
        return parse_frontmatter(fm)

    def test_scalars_quotes_and_comments(self):
        data = self.parse(
            '---\nlast_verified: 2026-09-01\ntitle: "Quoted: value"\n'
            "note: plain # trailing comment\nsingle: 'x'\n---\n"
        )
        assert data["last_verified"].value == "2026-09-01"
        assert data["last_verified"].line == 2
        assert data["title"].value == "Quoted: value"
        assert data["note"].value == "plain"
        assert data["single"].value == "x"

    def test_flow_mapping(self):
        data = self.parse("---\nverified: { by: human:alice, at: 2026-06-25T09:00:00Z }\n---\n")
        assert data["verified"].value == {"by": "human:alice", "at": "2026-06-25T09:00:00Z"}

    def test_block_list_of_flow_and_block_mappings(self):
        data = self.parse(
            "---\n"
            "verified:\n"
            "  - { by: human:alice, at: 2026-06-25T09:00:00Z }\n"
            "  - by: process:nightly\n"
            "    at: 2026-06-26T02:00:00Z\n"
            "stale_after: 2026-09-23T00:00:00Z   # stale on/after\n"
            "---\n"
        )
        assert data["verified"].value == [
            {"by": "human:alice", "at": "2026-06-25T09:00:00Z"},
            {"by": "process:nightly", "at": "2026-06-26T02:00:00Z"},
        ]
        assert data["verified"].line == 2
        assert data["stale_after"].value == "2026-09-23T00:00:00Z"

    def test_flow_list(self):
        data = self.parse("---\ntags: [a, b, 'c d']\n---\n")
        assert data["tags"].value == ["a", "b", "c d"]

    def test_flow_list_of_mappings(self):
        data = self.parse(
            "---\nverified: [{by: a, at: 2026-01-01}, {by: b, at: 2026-01-02}]\n---\n"
        )
        assert data["verified"].value == [
            {"by": "a", "at": "2026-01-01"},
            {"by": "b", "at": "2026-01-02"},
        ]

    def test_block_mapping(self):
        data = self.parse("---\ngenerated:\n  by: agent/1\n  at: 2026-01-01T00:00:00Z\n---\n")
        assert data["generated"].value == {"by": "agent/1", "at": "2026-01-01T00:00:00Z"}

    def test_unsupported_lines_are_ignored(self):
        data = self.parse("---\n  stray indented\nkey: v\n---\n")
        assert data["key"].value == "v"
