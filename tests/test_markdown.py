import datetime as dt

from freshlint.markdown import parse_document, prose_lines, slugify

MARK = "<!-- memory: verified=2026-09-01 review_by=2026-12-01 -->"
MARK2 = "<!-- memory: verified=2026-09-02 cadence=evergreen -->"


def doc(text, **kwargs):
    return parse_document(text, **kwargs)


class TestSlug:
    def test_github_style(self):
        assert slugify("Shipping Carriers") == "shipping-carriers"
        assert slugify("Q3: Launch plan (draft)!") == "q3-launch-plan-draft"
        assert slugify("  Already-hyphenated_name ") == "already-hyphenated_name"
        assert slugify("`code` and **bold**") == "code-and-bold"
        assert slugify("[Link text](http://example.invalid)") == "link-text"


class TestSections:
    def test_nested_sections_and_extents(self):
        d = doc("# Top\n\nintro\n\n## A\n\na body\n\n### A1\n\na1 body\n\n## B\n\nb body\n")
        assert [(s.level, s.title, s.slug, s.line) for s in d.sections] == [
            (1, "Top", "top", 1),
            (2, "A", "a", 5),
            (3, "A1", "a1", 9),
            (2, "B", "b", 13),
        ]
        a, a1, b = d.sections[1], d.sections[2], d.sections[3]
        assert a.end_line == 12  # A contains A1, ends before B
        assert a1.end_line == 12
        assert b.end_line == 15
        assert a1.parent == 1
        assert d.sections[0].end_line == 15

    def test_headings_in_fenced_code_are_ignored(self):
        d = doc("## Real\n\n```md\n## Not a heading\n" + MARK + "\n```\n\n~~~\n# also not\n~~~\n")
        assert [s.title for s in d.sections] == ["Real"]
        assert d.sections[0].marker is None
        assert d.problems == []

    def test_closing_hashes_and_hashtags(self):
        d = doc("## Title ##\n\n#hashtag is text\n")
        assert [s.title for s in d.sections] == ["Title"]

    def test_setext_headings_are_not_sections(self):
        d = doc("Title\n=====\n\ntext\n")
        assert d.sections == []

    def test_duplicate_slugs_get_suffixes(self):
        d = doc("## Notes\n\nx\n\n## Notes\n\ny\n\n## Notes\n")
        assert [s.slug for s in d.sections] == ["notes", "notes-1", "notes-2"]

    def test_frontmatter_offsets_line_numbers(self):
        d = doc("---\nlast_verified: 2026-09-01\n---\n# T\n\n## S\n" + MARK + "\n")
        assert [(s.title, s.line) for s in d.sections] == [("T", 4), ("S", 6)]
        assert d.sections[1].marker.line == 7
        assert d.file_claim.verified == dt.date(2026, 9, 1)


class TestMarkers:
    def test_marker_after_blank_lines_attaches(self):
        d = doc("## S\n\n\n" + MARK + "\nbody\n")
        assert d.sections[0].marker.verified == dt.date(2026, 9, 1)
        assert d.problems == []

    def test_marker_not_first_line_is_a_problem(self):
        d = doc("## S\n\nbody\n" + MARK + "\n")
        assert d.sections[0].marker is None
        assert [(line, "first non-blank line" in msg) for line, msg in d.problems] == [(4, True)]

    def test_second_marker_is_a_problem(self):
        d = doc("## S\n" + MARK + "\n" + MARK2 + "\nbody\n")
        assert d.sections[0].marker.verified == dt.date(2026, 9, 1)
        assert len(d.problems) == 1
        assert "more than one marker" in d.problems[0][1]

    def test_invalid_marker_is_a_problem_and_no_claim(self):
        d = doc("## S\n<!-- memory: verified=2026-09-01 -->\n")
        assert d.sections[0].marker is None
        assert d.sections[0].marker_invalid is True
        assert "review_by= or cadence=evergreen" in d.problems[0][1]

    def test_file_level_marker_before_first_heading(self):
        d = doc("\n" + MARK + "\n# Title\n\ntext\n")
        assert d.file_claim.source == "marker"
        assert d.file_claim.line == 2

    def test_file_marker_and_frontmatter_claim_conflict(self):
        d = doc("---\nlast_verified: 2026-09-01\n---\n" + MARK + "\n# T\n")
        assert any("two file-level" in msg for _, msg in d.problems)

    def test_invalid_frontmatter_claim_is_a_problem(self):
        d = doc("---\nlast_verified: someday\n---\n# T\n")
        assert d.file_claim is None
        assert d.file_claim_invalid is True
        assert d.problems and d.problems[0][0] == 2

    def test_claims_lists_file_and_section_claims(self):
        d = doc("---\nlast_verified: 2026-09-01\n---\n## A\n" + MARK + "\n## B\n")
        assert [(c.key, c.title) for c in d.claims] == [("", None), ("a", "A")]


class TestGovernance:
    def test_parent_marker_governs_unmarked_child(self):
        d = doc("## A\n" + MARK + "\npa\n### A1\nchild\n## B\n" + MARK2 + "\nb\n")
        assert d.unit_content("a") == "## A\npa\n### A1\nchild"
        assert d.unit_content("b") == "## B\nb"

    def test_marked_child_is_excluded_from_parent(self):
        d = doc("## A\n" + MARK + "\npa\n### A1\n" + MARK2 + "\nchild\n\nmore\n")
        assert d.unit_content("a") == "## A\npa"
        assert d.unit_content("a1") == "### A1\nchild\nmore"

    def test_unmarked_content_belongs_to_file(self):
        d = doc("---\nlast_verified: 2026-09-01\n---\n# T\n\nintro\n## A\n" + MARK + "\npa\n")
        assert d.unit_content("") == "# T\nintro"
        assert d.unit_content("a") == "## A\npa"

    def test_whitespace_ignore_vs_exact(self):
        text = "## A\n" + MARK + "\nline  \n\n\nnext\n"
        assert doc(text).unit_content("a") == "## A\nline\nnext"
        exact = doc(text, whitespace="exact").unit_content("a")
        assert exact == "## A\nline  \n\n\nnext"

    def test_body_content_excludes_metadata(self):
        a = doc("---\nlast_verified: 2026-09-01\n---\n## A\n" + MARK + "\npa\n")
        b = doc(
            "---\nlast_verified: 2026-09-30\ntags: [x]\n---\n## A\n"
            "<!-- memory: verified=2026-09-30 review_by=2027-01-01 -->\npa\n"
        )
        assert a.body_content == b.body_content

    def test_suppression_comments_are_not_content(self):
        a = doc("## A\n" + MARK + "\npa\n")
        b = doc(
            "## A\n" + MARK + "\n"
            '<!-- freshlint: allow=temporal-language reason="x" expires=2026-10-01 -->\n'
            'pa <!-- freshlint: allow=temporal-language reason="y" expires=2026-10-01 -->\n'
        )
        assert a.unit_content("a") == b.unit_content("a")

    def test_unknown_unit_is_empty(self):
        assert doc("text\n").unit_content("nope") == ""

    def test_crlf_line_endings(self):
        d = doc("## A\r\n" + MARK + "\r\npa\r\n")
        assert d.sections[0].marker is not None
        assert d.unit_content("a") == "## A\npa"


class TestFenceDetection:
    def test_plain_fence_opens_and_closes(self):
        text = "```python\ncode\n```\nafter\n"
        assert prose_lines(text) == [(4, "after")]

    def test_longer_fence_needs_an_equally_long_close(self):
        text = "````\ncode\n```\nstill code\n````\nafter\n"
        assert prose_lines(text) == [(6, "after")]

    def test_inline_code_with_triple_backticks_is_not_a_fence(self):
        assert prose_lines("```foo``` text\nnext\n") == [(1, "```foo``` text"), (2, "next")]

    def test_tilde_fence_with_a_backtick_in_its_info_string_still_opens(self):
        text = "~~~ info with ` backtick\nhidden\n~~~\nafter\n"
        assert prose_lines(text) == [(4, "after")]

    def test_prose_lines_and_parse_document_agree_on_what_is_code(self):
        texts = [
            "```python\ncode\n```\nafter\n",
            "```foo``` text\n## H\nnext\n",
            "~~~ info with ` backtick\nhidden\n~~~\nafter\n",
            "````\na\n```\nb\n````\nc\n",
            "```\nnever closed\n",
        ]
        for text in texts:
            d = doc(text)
            body = set(range(d.body_start + 1, len(d.lines) + 1))
            assert {n for n, _ in prose_lines(text)} == body - d.code_lines, text
