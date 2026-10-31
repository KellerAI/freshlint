import pytest

from freshlint.globs import match_any, matches


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("**/*.md", "README.md", True),
        ("**/*.md", "docs/a/b.md", True),
        ("**/*.md", "docs/a/b.txt", False),
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        ("context/*.md", "context/company.md", True),
        ("context/*.md", "context/sub/company.md", False),
        ("context/**", "context/sub/company.md", True),
        ("context/**", "contexts/company.md", False),
        ("**/node_modules/**", "node_modules/x/y.md", True),
        ("**/node_modules/**", "a/node_modules/y.md", True),
        ("docs/**/*.md", "docs/x.md", True),
        ("docs/**/*.md", "docs/a/b/x.md", True),
        ("file?.md", "file1.md", True),
        ("file?.md", "file10.md", False),
        ("[ab].md", "a.md", True),
        ("[ab].md", "c.md", False),
        ("VERIFICATIONS.md", "VERIFICATIONS.md", True),
        ("VERIFICATIONS.md", "x/VERIFICATIONS.md", False),
        ("a.b.md", "aXb.md", False),
    ],
)
def test_glob_semantics(pattern, path, expected):
    assert matches(pattern, path) is expected


def test_match_any_uses_any_pattern():
    assert match_any(["*.txt", "**/*.md"], "a/b.md")
    assert not match_any(["*.txt"], "a/b.md")
    assert not match_any([], "a.md")


def test_leading_dot_slash_in_pattern_is_ignored():
    assert matches("./context/*.md", "context/a.md")
