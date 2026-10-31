"""The README and changelog say what the memory checks do, and the examples they quote hold.

The docs describe three things: what a citation covers, what the C and C++ declaration prefixes do,
and the uncited-claim rules (a contract part and a policy part). These tests pin
the claims that a reader relies on, and run every sentence the README quotes as an example through
the code, so the text cannot drift from the behaviour unnoticed.
"""

from pathlib import Path

import pytest

from freshlint.claims import find_claims
from freshlint.symbols import Unparseable, locate_braced

ROOT = Path(__file__).resolve().parent.parent


def flat(path: str) -> str:
    return " ".join((ROOT / path).read_text().split())


README = flat("README.md")
CHANGELOG = flat("CHANGELOG.md")


def warns(text: str) -> bool:
    return bool(find_claims(text + "\n"))


def test_readme_says_what_a_citation_covers_and_that_coverage_is_syntactic():
    for sentence in (
        "A citation covers the files it names and the claims written next to it.",
        "Coverage is bounded and syntactic, judged per path and per claim.",
        "A citation never asserts that the cited code supports the claim.",
        "an adjacent Evidence line that has nothing to do with the claim still covers the claims "
        "in its paragraph",
        "Adding, renewing or removing a citation of another file never deletes or re-baselines it.",
        "the last commit, on or before the note's time, that touched that path",
        "Each path is answered on its own, so a repository with merge commits gets the same answer",
        "Explicit citations are checked first, for every note, before any inferred baseline",
        "A mention is handed over to a citation only once a citation of that file has a "
        "fingerprint",
        "(in backticks or as a path-like token: one with a `/`, a `.` or a `:line` suffix, so a "
        "bare word such as `src` or `test` names nothing)",
    ):
        assert sentence in README, sentence


def test_readme_describes_the_declaration_prefixes_their_fallbacks_and_their_cost():
    for sentence in (
        "`template <...>` headers (one line or several, angle brackets balanced)",
        "`requires` clauses with their continuation lines",
        "`[[...]]`, `alignas(...)`, `__attribute__((...))` and `__declspec(...)`",
        "tracked at file level",
        "a conditional directive (`#if`, `#ifdef`, `#ifndef`, `#elif`, `#else`, `#endif`)",
        "a backslash line splice outside a preprocessor line",
        "a `#define` whose braces do not balance",
        "an `#include` header name that contains `/*` or `//`, and a NUL byte or U+180E anywhere "
        "in the file",
        'a C++20 header-unit `import <a/*b>;` or `import "a//b";` whose header name contains '
        "`/*` or `//`",
        "for the forms listed here, editing only such a line is not read as `fresh`",
        "A file-level citation reports any edit to the file, so it can report a change that is "
        "not in the symbol",
        "a bare identifier, keyword or `NAME(args)` alone on a line",
        "a line of the prefix that names an identifier the same file `#define`s",
        "One shape is deliberately not refused, and an edit to it is not seen",
        "a bare identifier (not a keyword) or `NAME(args)` line that this file does not "
        "`#define`, with a blank line between it and the declaration",
        "a line separator other than LF, CR or CRLF",
        "a digraph",
        "or a trigraph",
        "Free functions, `using` and `typedef` aliases, unions and concepts in C and C++ are not "
        "isolated at all, so they always fall back to the file.",
        "a macro that another file defines, and that expands to prefix text or opens or closes a "
        "body, is not seen",
        "reads `changed` once after upgrading",
        "a C or C++ symbol that the scan now refuses, for any shape listed above, and so tracks "
        'at file level (the message says "tracked at file level")',
        "a C# or Swift symbol with `#if` or `#region` lines among its attributes, whose span now "
        "takes in those lines and the attributes above them",
    ):
        assert sentence in README, sentence
    assert "a C++ `requires` clause above a struct" not in README  # the old limit is gone
    for overclaim in ("it cannot miss one", "is never `fresh`:"):
        assert overclaim not in README, overclaim


# What the README lists as falling back or isolating, run through the scanner (suffix .cpp).
def rows(*lines: str) -> str:
    return "\n".join(lines) + "\n"


_BODY = ("struct S {", "  int t;", "};")
README_ISOLATES = {
    "template header": (rows("int a;", "template <typename T>", *_BODY), (2, 5)),
    "multi-line template header": (
        rows("int a;", "template <typename T,", "          typename U>", *_BODY),
        (2, 6),
    ),
    "requires clause with a continuation": (
        rows("int a;", "template <typename T>", "requires Small<T> &&", "         Big<T>", *_BODY),
        (2, 7),
    ),
    "attributes": (rows("int a;", "[[nodiscard]]", "alignas(8)", *_BODY), (2, 6)),
    "__attribute__": (rows("int a;", "__attribute__((packed))", *_BODY), (2, 5)),
    "__declspec": (rows("int a;", "__declspec(dllexport)", *_BODY), (2, 5)),
    "export": (rows("int a;", "export", *_BODY), (2, 5)),
    "inline": (rows("int a;", "inline", *_BODY), (2, 5)),
    "constexpr": (rows("int a;", "constexpr", *_BODY), (2, 5)),
}
# The same claim for the other languages and for a trailing qualifier: (suffix, name, text, span).
README_OTHER_ISOLATES = {
    "a multi-line Rust attribute": (
        ".rs",
        "S",
        rows("fn a() {}", "#[derive(", "  Debug,", "  Clone)]", "struct S {", "  x: i32,", "}"),
        (2, 7),
    ),
    "a multi-line Java annotation": (
        ".java",
        "S",
        rows("class Q {}", '@Query(value = "x",', "  nativeQuery = true)", "class S {", "}"),
        (2, 5),
    ),
    "a multi-line C# attribute": (
        ".cs",
        "S",
        rows("class Q {}", "[Foo(1,", " 2)]", "class S {", "}"),
        (2, 5),
    ),
    "a trailing requires after a member declarator": (
        ".cpp",
        "Outer.f",
        rows(
            "int a;",
            "struct Outer {",
            "  void f()",
            "    const",
            "    requires Small<int>",
            "  {",
            "  }",
            "};",
        ),
        (3, 7),
    ),
}
README_FALLS_BACK = {
    "a clause that ends on an operator": rows(
        "int a;", "template <typename T>", "requires Small<T> &&", *_BODY
    ),
    "an unknown macro alone on a line": rows("int a;", "EXPORT_API", *_BODY),
    "a qualifier alone on a line": rows("int a;", "static", *_BODY),
    "a requires expression with a body": rows(
        "int a;", "template <typename T>", "requires requires(T t) {", "  t.x();", "}", *_BODY
    ),
    "braces in a prefix": rows("int a;", "template <typename T, T V = T{}>", *_BODY),
    "a prefix line that names a macro the file defines": rows(
        "#define LIMIT 4", "template <typename T>", "requires Small<T> && LIMIT", *_BODY
    ),
    "a template parameter default that is a defined macro": rows(
        "#define DEFAULT_T int", "template <typename T = DEFAULT_T>", *_BODY
    ),
    "a conditional above the declaration": rows("int a;", "#ifdef X", *_BODY, "#endif"),
    "a conditional inside the declaration": rows(
        "int a;", "struct S {", "#if X", "  int t;", "#endif", "};"
    ),
    "a splice outside a directive": rows("int a = \\", "  1;", *_BODY),
    "an unbalanced define": rows("#define OPEN {", *_BODY),
    "a header name with a comment opener": rows("#include <a/*b>", *_BODY),
    "a header-unit import with a comment opener": rows("import <a/*b>;", *_BODY),
    "a quoted header-unit import with a comment opener": rows('import "a//b";', *_BODY),
    "a NUL byte": rows("int a;", "\x00", *_BODY),
    "a NUL byte inside a comment": rows("int a; // note\x00", *_BODY),
    "U+180E": rows("int a;", chr(0x180E), *_BODY),
    "a form feed": "int a;\n\f\nstruct S {\n  int t;\n};\n",
    "a digraph": rows("int a;", "struct S <%", "  int t;", "%>;"),
    "a trigraph": rows("int a;", "??=define X 1", *_BODY),
    "a free function": rows("int a;", "int S() {", "  return 1;", "}"),
    "an alias": rows("using S = int;"),
    "a union": rows("union S {", "  int a;", "};"),
    "a concept": rows("template <typename T>", "concept S = true;"),
}


@pytest.mark.parametrize("shape", sorted(README_ISOLATES))
def test_a_prefix_the_readme_lists_joins_the_span(shape):
    text, span = README_ISOLATES[shape]
    assert locate_braced(text, "S", ".cpp") == span


# A bare identifier or NAME(args) line that the file does not #define, a blank line from the
# declaration, with a statement end above it: outside the span, as the README says. An edit to it
# is not seen.
README_OUTSIDE_THE_SPAN = {
    "a bare macro between two blank lines": (
        rows("int a;", "", "EXPORT_API", "", *_BODY),
        (5, 7),
    ),
    "a bare macro above a blank line": (rows("int a;", "EXPORT_API", "", *_BODY), (4, 6)),
    "a macro call above a blank line": (rows("int a;", "DECL(a, b)", "", *_BODY), (4, 6)),
}


@pytest.mark.parametrize("shape", sorted(README_OUTSIDE_THE_SPAN))
def test_a_detached_bare_line_stays_outside_the_span_as_the_readme_says(shape):
    text, span = README_OUTSIDE_THE_SPAN[shape]
    assert locate_braced(text, "S", ".cpp") == span


@pytest.mark.parametrize("shape", sorted(README_OTHER_ISOLATES))
def test_a_prefix_in_another_language_or_a_trailing_qualifier_joins_the_span(shape):
    suffix, name, text, span = README_OTHER_ISOLATES[shape]
    assert locate_braced(text, name, suffix) == span


@pytest.mark.parametrize("shape", sorted(README_FALLS_BACK))
def test_a_shape_the_readme_lists_falls_back_to_the_file(shape):
    with pytest.raises(Unparseable):
        locate_braced(README_FALLS_BACK[shape], "S", ".cpp")


def test_readme_labels_the_contract_and_the_policy():
    assert "#### Contract" in README
    assert "#### Policy" in README
    assert "#### Documented bypasses and limits" in README
    # Items 1, 2, 3 and 5 are headline inference; items 4 and 6 are directly documented.
    assert README.count(": contract (headline inference).") == 4
    assert README.count(": contract (directly documented).") == 2
    for item in (
        "1. **Questions**: contract (headline inference).",
        "2. **Instructions to verify**: contract (headline inference).",
        "3. **Quotes and blockquotes**: contract (headline inference).",
        "4. **Negation**: contract (directly documented).",
        "5. **Tags attach to their sentence**: contract (headline inference).",
        "6. **Prerelease versions**: contract (directly documented).",
    ):
        assert item in README, item
    for label in (
        "**P1. A pin credits only when it is bound to verification.**",
        "**P2. Adjacent or structured provenance credits.**",
        "**P3. A convention or a source credits.**",
    ):
        assert label in README, label
    assert "from the plan" not in README and "recorded in the plan" not in README


# (sentence as the README quotes it, text to scan, whether it warns)
README_CLAIM_EXAMPLES = [
    # Contract
    ("Has `a.py` been tested?", "Has `a.py` been tested?", False),
    ("Check that `a.py` is fixed.", "Check that `a.py` is fixed.", False),
    ("Make sure `retry()` always succeeds.", "Make sure `retry()` always succeeds.", False),
    (
        "Record the root cause in `issue.md`.",
        "Record the root cause in `issue.md`.",
        False,
    ),
    (
        "It is false that `a.py` is fixed.",
        "It is false that `a.py` is fixed.",
        False,
    ),
    (
        "(Tested only on Linux; `a.py` is broken.)",
        "(Tested only on Linux; `a.py` is broken.)",
        True,
    ),
    # Policy P1
    (
        "`route()` is verified; the deadline is 2026-10-09.",
        "`route()` is verified; the deadline is 2026-10-09.",
        True,
    ),
    (
        "`route()` confirmed on 2026-09-20.",
        "`route()` confirmed on 2026-09-20.",
        False,
    ),
    (
        '("the deadline is 2026-10-09")',
        "`route()` is broken and the deadline is 2026-10-09.",
        True,
    ),
    # Policy P2
    (
        "Verified: by hand in `a.py`",
        "`a.py` is fixed.\nVerified: by hand in `a.py`",
        False,
    ),
    # Policy P3
    (
        '"per second", "per the team" and "Source: Sam" name nothing',
        "`a.py` is fixed per second.",
        True,
    ),
    ('"per the team"', "`a.py` is fixed per the team.", True),
    ('"Source: Sam"', "`a.py` is fixed (Source: Sam).", True),
    (
        "`docs/policy.md:42`",
        "`a.py` is fixed per `docs/policy.md:42`.",
        True,
    ),
    # Controls that keep warning
    ('"Note that"', "Note that `x` is fixed.", True),
    ('"Remember:"', "Remember: `retry()` always fails.", True),
    # Documented bypasses
    (
        "`route()` is broken; the deployment was checked 2026-10-09.",
        "`route()` is broken; the deployment was checked 2026-10-09.",
        False,
    ),
    (
        "`route()` is verified, the deadline is 2026-10-09.",
        "`route()` is verified, the deadline is 2026-10-09.",
        False,
    ),
    (
        "Check `x`, the root cause is `y`.",
        "Check `x`, the root cause is `y`.",
        False,
    ),
    (
        "per https://github.com/o/r/blob/main/src/a.py#L10",
        "`a.py` is fixed per https://github.com/o/r/blob/main/src/a.py#L10.",
        False,
    ),
    ("`.txt` counts as a document", "`a.py` is fixed per `requirements.txt`.", False),
    ("(`src/policy/engine`)", "`a.py` is fixed per `src/policy/engine`.", False),
    (
        "Verified: yes",
        "`a.py` is fixed.\nVerified: yes",
        False,
    ),
    ("bare `[verified]` tag credits", "`a.py` is fixed.\n[verified]", False),
    # Recall-direction bypasses the README lists: what the question and quote exemptions hide
    ("`x` is fixed, right?", "`x` is fixed, right?", False),
    ("`x` is fixed?", "`x` is fixed?", False),
    ("Did you know `x` is broken?", "Did you know `x` is broken?", False),
    (
        "What was verified in `src/a.py` is the cache path.",
        "What was verified in `src/a.py` is the cache path.",
        False,
    ),
    ('The "root cause" is `retry()`.', 'The "root cause" is `retry()`.', False),
    (
        'Resize to 5" and `x` is fixed in the 3" mode.',
        'Resize to 5" and `x` is fixed in the 3" mode.',
        False,
    ),
    (">= 3 callers: `x` is fixed.", ">= 3 callers: `x` is fixed.", False),
    # A marker binds an unrelated pin
    (
        "`a.py` is fixed as of 2026-10-09 in version control.",
        "`a.py` is fixed as of 2026-10-09 in version control.",
        False,
    ),
    (
        "`a.py` is fixed; the pinned issue says 2026-10-09.",
        "`a.py` is fixed; the pinned issue says 2026-10-09.",
        False,
    ),
    # Empty provenance values credit
    ('"Verified: no"', "`a.py` is fixed.\nVerified: no", False),
    ('"Tested: failed"', "`a.py` is fixed.\nTested: failed", False),
    ('"Checked: pending"', "`a.py` is fixed.\nChecked: pending", False),
    ('"Source: unknown"', "`a.py` is fixed.\nSource: unknown", False),
    ('"[verified: no]"', "`a.py` is fixed. [verified: no]", False),
    ('"[verified: guess]"', "`a.py` is fixed. [verified: guess]", False),
    (
        '"by convention [verified: trust me]"',
        "`a.py` is fixed by convention [verified: trust me].",
        False,
    ),
    # A marker chain stays quiet, and its last claim with nothing below it warns
    (
        'then "Verified: `b.py` is broken." then',
        "`a.py` is fixed.\nVerified: `b.py` is broken.\nSource: `docs/x.md`.",
        False,
    ),
    (
        "A last line that states a claim with nothing below it still warns.",
        "`a.py` is fixed.\nVerified: `a.py` is fixed.",
        True,
    ),
    # Honest sentences that still warn
    (
        "Ensure `a` is tested and `b` is fixed.",
        "Ensure `a` is tested and `b` is fixed.",
        True,
    ),
    (
        "Suppose `route()` is broken",
        "Suppose `route()` is broken; investigate the logs.",
        True,
    ),
    (
        '"Checked by running `pytest`" without a colon',
        "`a.py` is fixed.\nChecked by running `pytest`.",
        True,
    ),
]


@pytest.mark.parametrize(
    ("quoted", "text", "expected"),
    README_CLAIM_EXAMPLES,
    ids=[example[0][:40] for example in README_CLAIM_EXAMPLES],
)
def test_the_sentences_the_readme_quotes_behave_as_it_says(quoted, text, expected):
    assert quoted in README, quoted
    assert warns(text) is expected, text


def test_readme_lists_the_documented_bypasses():
    for bypass in (
        "**Adjacent evidence.** An Evidence line next to a claim covers it, relevant or not.",
        "**An unrelated verb with an unrelated pin.**",
        "**A marker binds an unrelated pin.**",
        "**Weak provenance is accepted, and empty values credit.**",
        "**Marker chains.**",
        "**What the question and quote exemptions hide.**",
        "**Performance.**",
        "**A comma splice after a verification instruction.**",
        "**A bare `[verified]` tag credits.**",
        "A stricter option, where a tag credits only when it names a source or a check, has been "
        "considered and is not adopted; whether to adopt it is undecided.",
        "**URL permalinks to code credit.**",
        "**`.txt` counts as a document**",
    ):
        assert bypass in README, bypass


def test_changelog_records_the_reversal_the_policy_and_the_one_time_changed():
    for entry in (
        "**A citation covers the files it names and the claims written next to it (reverses the "
        '2026-10-02 ruling "any valid citation retires all inferred findings").**',
        "Uncited-claim rules, **policy**",
        "**contract**",
        "**After upgrading, a stored fingerprint of a C or C++ symbol whose tracked span grew",
        "reads `changed` once**",
        "a C or C++ symbol that the scan now refuses (any shape listed above, and so tracked at "
        'file level; the message says "tracked at file level")',
        "a C# or Swift symbol with `#if` or `#region` lines among its attributes, whose span now "
        "takes in those lines and the attributes above them",
        "One-time cost after upgrading",
    ):
        assert entry in CHANGELOG, entry
