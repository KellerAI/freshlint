"""Certainty claims: wording that presents something about code as checked.

A sentence is a claim when it uses a certainty phrase ("verified", "root cause") and names
code: an inline code span or a file path. It is not a claim when it hedges ("probably", "may",
"will"), credits a person ("confirmed by Sam", "Sam confirmed"), or says when and how the check
was done ("checked 2026-10-01 by running `pytest`"). A named check in the sentence itself also
credits it: "verified", "tested" or "checked" followed by "with", "by" or "on", or a test, script
or command in backticks with a check word next to it ("ran", "passes", "with", "via", "using").
A source file is never a check unless its name is test-shaped.

P1: a pin (an ISO date, a commit, a version number, a
prerelease such as ``0.3.0b2`` or ``1.2.0-rc.1`` included) credits a claim only when the same
sentence also has a verification verb (verified, tested, checked, confirmed, reproduced,
measured, ran, observed) or a provenance marker ("as of", "at commit", "in v...", "pinned",
"since v...", "per <source>"). "The deadline is 2026-10-09" or "supports version 4.0" credits
nothing. The claim's own certainty phrase is not such a verb outside its own clause: in
"`route()` is verified; the deadline is 2026-10-09." and in "`route()` is fixed and verified;
the deadline is 2026-10-09." the date is in another clause (a verb that is not the claim, such as
"ran" or "checked", still binds a pin anywhere in the sentence).

P3: "per <named doc or policy>", "by convention
(<source>)" and "Source: <name>" in the same sentence credit the claim. A source is only a
document: a doc-like code span (a name with a document extension, or a path with no extension
inside a document directory such as `docs/decisions`; never code, config, data or a code location
such as `a.py:42`, and never a slash token such as `owner/repo`), a URL, a document path in
prose (a line or symbol suffix, ``docs/policy.md:42`` or ``docs/policy.md#retention``, makes it
code there too), a markdown link to one (its anchor is link syntax), a capitalized phrase that
ends in Policy, Guide, Standard, Runbook, README, Spec, Handbook, Playbook or Charter, or RFC, ADR
or PEP and a number. It is never the claim's own code, and the markers are read in prose, outside
code spans. "per second", "per API call", "Source: Sam" and "per the team" name nothing.

Only ``project`` and ``reference`` notes (and notes with no type) are checked; ``user`` and
``feedback`` notes hold preferences and rules. Claims are checked when the agent saves a
memory; the session report leaves them out.

A certainty phrase counts only where the sentence asserts it. A question ("Has `a.py` been
tested?"), the complement of a verification-type instruction ("Check that `a.py` is fixed",
"Record the root cause in `issue.md`"), a quotation (text in quotation marks, or a line that
starts with ``>``) and a negated or denied certainty ("never confirmed", "It is false that ...")
assert nothing. Each exemption covers only the phrase inside that question, complement, quote or
negation: the rest of the sentence still counts. An instruction complement ends at the first
clause break ("and", "or", "but", "because", "which", a relative "that", a parenthesis, ";", ":",
a dash). Any other imperative ("Avoid ...", "Use ...") exempts nothing. A verb that introduces a
statement ("Note that", "Remember:") is not an instruction.

A bracketed verification tag (``[verified]``, ``[verified: ...]``, ``(verified)``,
``[checked ...]``) is never a sentence of its own and never ends one. It stays with the sentence
before it, also when it stands alone on the next line. A tag holds a verification word and at
most a short source; a bracket that holds a statement ("(Tested only on Linux; `a.py` is
broken.)") is a sentence.

P2: a claim is credited when its own line is directly
followed by a line that starts with ``Verified:``, ``Checked:``, ``Tested:``, ``Source:`` or
``Evidence (manual):``, or by a standalone verification tag (after a full stop, or on a line of
its own). A line that continues the claim's bullet, or a bullet nested under it, counts; a marker
in a sibling bullet, after a blank line, in the next sentence of the same line, or above the claim
does not. Any standalone tag credits, a bare ``[verified]`` included, and so does a tag that
ends its sentence, line or bullet: the stricter option (a tag credits only when it names a source
or a check) is not implemented. A tag with text after it is part of the statement and is read as
the writer's own words. A marker line that serves a claim above (one the marker credits, or one a
pin or a source already did) is read without its label: "Verified: by hand in `a.py`" is
provenance, not a claim of its own. A marker line with no claim above is read like any line, and
whatever else a marker line says is always read.

This module decides which sentences are claims. Which of them the note's citations already
cover (a cited file named in the sentence, an Evidence line beside it) is decided by
``freshlint.coverage``, which wraps ``find_claims``.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import NamedTuple

from freshlint.markdown import HEADING_RE, prose_lines

DEFAULT_PHRASES = (
    "verified",
    "confirmed",
    "root cause",
    "is fixed",
    "is broken",
    "does not support",
    "is not supported",
    "always fails",
    "never works",
    "tested",
    "proven",
)
DEFAULT_HEDGES = (
    "unverified",
    "not verified",
    "unconfirmed",
    "not confirmed",
    "probably",
    "likely",
    "i think",
    "guess",
    "maybe",
    "might",
    "suspect",
    "will",
    "would",
    "should",
    "must",
    "needs to",
    "need to",
    "not yet",
    "to be",
    "unless",
    "may",
    "could",
    "possibly",
    "perhaps",
    "seems",
    "appears",
    "not tested",
    "never tested",
)
CHECKED_TYPES = frozenset({"project", "reference"})
MAX_LINE = 4000  # longer lines are skipped: the path pattern backtracks badly on them
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_CODE_SPAN_RE = re.compile(r"`[^`]*`")
_BLOCKQUOTE_RE = re.compile(r"^\s*>")
# A bracketed verification tag: "[verified]", "[verified: main.py]", "(verified)", "[checked ...]",
# optionally in bold or italics. Tag-shaped means a verification word and at most a short source:
# no sentence punctuation (a full stop only inside a word, as in "main.py"), no verb that makes it
# a statement, and at most 60 characters. "(Confirmed in `a.py`: the cause is X.)" is a sentence.
_TAG_WORD = r"(?:verified|checked|tested|confirmed)\b"
_FINITE = r"\b(?:is|are|was|were|has|have|had|does|did|always|never|fails?|breaks?|works?)\b"
_TAG_BODY = r"(?:`[^`]*`|[^`\[\]()!?;.]|\.(?=\w)){0,60}"
_TAG_SHAPE = rf"\s*{_TAG_WORD}(?![^\]()]{{0,60}}?{_FINITE}){_TAG_BODY}"
_TAG = rf"(?:\*{{1,2}}|_{{1,2}})?(?:\[{_TAG_SHAPE}\]|\({_TAG_SHAPE}\))(?:\*{{1,2}}|_{{1,2}})?"
_TAG_RE = re.compile(_TAG, re.IGNORECASE)
_LEADING_TAG_RE = re.compile(rf"\s*{_TAG}", re.IGNORECASE)
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_LIST_ITEM_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+")
# P2: a line that starts with one of these labels (after
# a list marker and emphasis) and says something after it. "Verified results ..." has no colon,
# "Sources:" is not the label, and "Verified:" alone says nothing.
_MARKER_LINE_RE = re.compile(
    r"^\s*(?:(?:[-*+]|\d+[.)])\s+)?(?:\*{1,2}|_{1,2})?"
    r"(?P<label>verified|checked|tested|source|evidence\s*\(manual\))"
    r"(?:\*{1,2}|_{1,2})?\s*:(?:\*{1,2}|_{1,2})?\s*(?=[^\s*_])",
    re.IGNORECASE,
)
_EXTENSIONS = r"(?:py|pyi|ts|tsx|js|jsx|mjs|json|toml|ya?ml|sh|go|rs|java|kt|cs|rb|sql|md)"
_PATH_RE = re.compile(
    rf"(?<![\w/])(?:~/|\./|/)?(?:[\w.-]+/)+[\w-]+\.{_EXTENSIONS}\b"
    rf"|(?<![\w/])(?:~/|\./)(?:[\w.-]+/?)+"
    rf"|\b[a-z0-9_-]+\.{_EXTENSIONS}\b"
)
# "confirmed by Sam", or a name before the verb ("Sam confirmed ..."). Words that cannot be a
# person's name, such as "We" or "The", leave the sentence a claim by the agent itself.
_NOT_NAMES = (
    "The|This|That|These|Those|We|It|They|Claude|Agent|All|Both|Each|Once|Then|When"
    "|If|And|But|So|Not|Never|Always|As|After|Before|Also|Later|Now"
)
_ATTRIBUTED_RE = re.compile(
    r"\b(?i:confirmed|verified|tested)\s+(?i:by|with)\s+[A-Z]"
    rf"|\b(?!(?:{_NOT_NAMES})\b)[A-Z][a-z]+\s+confirmed\b"
)

# A pin or a named check inside the claim sentence itself. A pin is an ISO date, a commit (7 to 40
# hex characters with a digit and a letter) or a version number. P1: a pin credits only when the
# sentence also binds it to verification (``_bound``), so
# an unrelated deadline or version credits nothing. "verified", "tested" or "checked" followed by
# "with", "by" or "on" is a named check and needs no pin. A test, script or command in backticks
# counts only with a check word right next to it (``_names_a_check``): "`pytest` passes" does,
# "`npm install` always fails" does not. A prerelease tail written without a separator
# (``0.3.0b2``, ``1.0.0a1``) belongs to the version; one with a separator (``1.2.0-rc.1``,
# ``v2.0.0-beta``) already ends at a word boundary.
_PRERELEASE = r"(?:[-.]?(?:alpha|beta|preview|pre|rc|dev|post|a|b|c)\.?\d+)?"
_PIN_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"
    rf"|\bv\d+(?:\.\d+)+\b|\b\d+\.\d+\.\d+{_PRERELEASE}\b|\bversion\s+\d+(?:\.\d+)*\b",
    re.IGNORECASE,
)
_NAMED_CHECK_RE = re.compile(r"\b(?:verified|tested|checked)\s+(?:with|by|on)\b", re.IGNORECASE)
# What binds a pin (P1): a verification verb, or a provenance marker. "in v..." and "since v..."
# take a v-prefixed version or the word "version" ("in v2.3.1", "since version 4.0"); a bare
# number does not ("in 4f9c2ab", "since 0.4.1"). The sixth marker, "per <source>", credits the
# claim by itself under P3.
_VERB_WORDS = frozenset(
    {"verified", "tested", "checked", "confirmed", "reproduced", "measured", "ran", "observed"}
)
_VERB_RE = re.compile(
    r"(?<![\w-])(?:verified|tested|checked|confirmed|reproduced|measured|ran|observed)(?![\w-])",
    re.IGNORECASE,
)
_MARKER_RE = re.compile(
    r"\bas\s+of\b|\bat\s+commit\b|\b(?:in|since)\s+v(?:ersion\b|\d)|(?<![\w-])pinned(?![\w-])",
    re.IGNORECASE,
)
# P3: a claim is credited when its own sentence says
# "per <named doc or policy>", "by convention (<source>)" or "Source: <name>". The markers are read
# in prose, with code spans blanked (as the P1 verbs are). A source is only a document:
# - a doc-like code span (``_doc_like``): a name with a document extension (.md, .rst, .txt,
#   .adoc), or a path with no extension that has a document directory as one of its segments
#   (`docs/decisions`, `adr/0007`); never code, config or data (`src/db.py`, `config/app.yaml`,
#   `Makefile`), never a code location (`src/db.py:42`, `docs/a.md#name`), and never a plain word
#   or a slash token (`tenant`, `user.id`, `owner/repo`, `and/or`);
# - a URL, or a document path in prose (CONTRIBUTING.md, docs/decisions/0007.md; with no line or
#   symbol suffix, as for a code span), or a markdown link to a URL or to a doc-like path
#   ([the policy](docs/policy.md#retention): the anchor is link syntax);
# - a capitalized phrase that ends in a document noun (the Security Policy, the README);
# - RFC, ADR or PEP and a number.
# It is never the claim's own code (``_is_own``): a source equal to a code span before its marker,
# or to the first code span of the sentence, is the claim citing itself. In a sentence that starts
# with its marker the claim follows it, so a mention of the same document in the claim's clause (up
# to the next semicolon, parenthesis, bracket or dash) is the claim citing itself too ("Per
# `docs/a.md`, the root cause is `retry()` in `docs/a.md`"). A later mention elsewhere is a second
# mention of the source ("per `docs/a.md` (see `docs/a.md`)"). After "by convention" the source
# sits in a bracket or parenthesis: a source,
# or a verification tag that gives one ("[verified: convention]"). A bare "[verified]" there names
# no source, and credits only as a tag that ends its sentence (P2), as it does anywhere else.
_DOC_EXTENSIONS = frozenset({"md", "rst", "txt", "adoc"})
# A path with no extension names a document only inside one of these directories: `owner/repo`,
# `and/or` and `TCP/IP` have a "/" and name nothing.
_DOC_DIRECTORIES = frozenset(
    {
        "docs",
        "doc",
        "adr",
        "adrs",
        "rfc",
        "rfcs",
        "policies",
        "policy",
        "runbooks",
        "handbook",
        "wiki",
        "guides",
    }
)
_URL_START_RE = re.compile(r"https?://", re.IGNORECASE)
# Files that are code or configuration although their name has no extension (or, for
# CMakeLists.txt, a document one).
_CODE_FILE_NAMES = frozenset(
    {
        "makefile",
        "gnumakefile",
        "dockerfile",
        "containerfile",
        "jenkinsfile",
        "vagrantfile",
        "gemfile",
        "rakefile",
        "procfile",
        "justfile",
        "brewfile",
        "cmakelists.txt",
    }
)
# Where the clause of a claim that follows its marker ends: a semicolon, a parenthesis, a bracket
# or a dash. What comes after is an aside ("(see `docs/a.md`)"), not the claim.
_ASIDE_RE = re.compile(r"[;(\[\u2014\u2013]|\s--?\s")
_LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?(?::\d+)?$")  # "docs/a.md:42", ":10-20"
# What follows a path in prose when it names a place in code: ":42", "::name", "#L10", "#name".
# A colon or a full stop before a space, a comma or a bracket is just punctuation.
_LOCATOR_RE = re.compile(r"(?:::?|#)\w")
_DOC_NOUNS = "Policy|Guide|Standard|Runbook|README|Spec|Handbook|Playbook|Charter"
_DETERMINER_RE = re.compile(r"(?:the|our|this|that|its|their|a|an)\s+", re.IGNORECASE)
_SOURCE_NAME_RE = re.compile(
    r"\[[^\]\n]*\]\((?P<target>[^)\s]+)\)"  # a markdown link; its target must be a document
    r"|https?://\S+"
    r"|[\w.~/-]*\.(?:md|rst|txt|adoc)\b"
    rf"|(?-i:(?:[A-Z][\w'-]*[ \t]+){{0,4}}(?:{_DOC_NOUNS})\b)"
    r"|(?-i:(?:RFC|ADR|PEP)[ \t-]*\d+\b)",
    re.IGNORECASE,
)
_PER_RE = re.compile(r"(?<![\w-])per(?=\s)", re.IGNORECASE)
_SOURCE_LABEL_RE = re.compile(
    r"(?<![\w-])source(?:\*{1,2}|_{1,2})?\s*:(?:\*{1,2}|_{1,2})?", re.IGNORECASE
)
_CONVENTION_RE = re.compile(  # the text after the opening bracket, up to the next bracket
    r"(?<![\w-])by\s+convention\b[\s*_]*[\[(]([^\[\]()\n]*)", re.IGNORECASE
)
_SOURCED_TAG_RE = re.compile(r"\s*(?:verified|checked|tested|confirmed)\b\s*:?\s*(.*)", re.I | re.S)
_WORDS_RE_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_TEST_WORDS = frozenset({"test", "tests", "pytest", "unittest", "jest", "vitest", "mocha", "spec"})
_COMMANDS = frozenset(
    {
        "python",
        "python3",
        "node",
        "npm",
        "npx",
        "pnpm",
        "yarn",
        "cargo",
        "go",
        "make",
        "git",
        "ruff",
        "eslint",
        "tsc",
        "dotnet",
        "doit",
        "uv",
        "pip",
        "curl",
        "gh",
        "docker",
        "bash",
        "sh",
        "jq",
        "rg",
        "grep",
        "just",
        "mvn",
        "gradle",
        "pytest",
        "jest",
        "vitest",
        "tox",
    }
)
_SOURCE_EXTENSIONS = frozenset(
    {
        "py",
        "pyi",
        "ts",
        "tsx",
        "js",
        "jsx",
        "mjs",
        "cjs",
        "rs",
        "go",
        "java",
        "kt",
        "kts",
        "cs",
        "rb",
        "php",
        "c",
        "cc",
        "cpp",
        "cxx",
        "h",
        "hpp",
        "swift",
        "scala",
        "dart",
        "lua",
        "ex",
        "exs",
        "clj",
        "hs",
        "ml",
        "r",
        "jl",
        "vue",
        "svelte",
    }
)
_SCRIPT_EXTENSIONS = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "ps1",
        "bat",
        "cmd",
    }
)
_TEST_NAME_RE = re.compile(
    r"(?:^|/)(?:test_[^/]*|[^/]*_test\.\w+|[^/]*\.(?:spec|test)\.\w+)$", re.I
)
_CHECK_PREFIX_RE = re.compile(
    r"(?:^|/)(?:diag|probe|check|verify|smoke|demo|repro)(?:[_.-][\w.-]*)?\.\w+$", re.IGNORECASE
)
_CHECK_WORDS = frozenset({"ran", "run", "running", "passes", "passed", "with", "via", "using"})
_ADVERBS = frozenset({"now", "still", "then", "again", "also", "all", "both", "only"})
_WORD_RE = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class ClaimHit:
    line: int
    phrase: str
    sentence: str


def applies_to(note_type: str | None) -> bool:
    return note_type is None or note_type in CHECKED_TYPES


def _word(text: str) -> re.Pattern[str]:
    # Hyphens count as part of a word, so "auto-verified" is not "verified".
    return re.compile(rf"(?<![\w-]){re.escape(text)}(?![\w-])", re.IGNORECASE)


def _names_code(sentence: str, prose: str) -> bool:
    return bool(_CODE_SPAN_RE.search(sentence) or _PATH_RE.search(prose))


def _is_check(body: str) -> bool:
    """Whether a code span reads as a command, a script or a test (never a plain source file)."""
    words = body.split()
    if not words:
        return False
    if words[0].lower() in _COMMANDS:
        return True
    if _TEST_NAME_RE.search(body):
        return True
    base = body.rsplit("/", 1)[-1]
    extension = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    if extension in _SOURCE_EXTENSIONS:
        return False
    if extension in _SCRIPT_EXTENSIONS or _CHECK_PREFIX_RE.search(body):
        return True
    return bool(_TEST_WORDS & {word.lower() for word in _WORDS_RE_CAMEL.findall(body)})


def _names_a_check(sentence: str) -> bool:
    """Whether a command, script or test in backticks has a check word next to it."""
    for span in _CODE_SPAN_RE.finditer(sentence):
        if not _is_check(span.group()[1:-1].strip()):
            continue
        before = _WORD_RE.findall(sentence[: span.start()])[-1:]
        after = _WORD_RE.findall(sentence[span.end() :])[:2]
        if after and after[0].lower() in _ADVERBS:
            after = after[1:]
        if (before and before[0].lower() in _CHECK_WORDS) or (
            after and after[0].lower() in _CHECK_WORDS
        ):
            return True
    return False


def _credited(sentence: str, claim_phrases: list[str]) -> bool:
    """Whether the sentence itself says how or against what the claim was checked.

    ``claim_phrases`` are the certainty phrases the sentence asserts. A named check reads through
    a code span ("Verified `retry()` on 2026-10-01"): the thing verified sits between the verb and
    "on", "by" or "with".
    """
    prose = _CODE_SPAN_RE.sub(" ", sentence)
    if _NAMED_CHECK_RE.search(prose) or _names_a_check(sentence) or _cites_a_source(sentence):
        return True
    return bool(_PIN_RE.search(sentence)) and _bound(sentence, claim_phrases)


_Bodies = list[tuple[int, str]]  # the code spans of a sentence, as (start, body)


def _doc_like(body: str) -> bool:
    """Whether the body of a code span reads as a document, never as code or a code location.

    A URL is a document. Otherwise the last path segment decides: a path with a document
    extension counts (`docs/policy.md`, `policy.md`), and a "/" path with no extension counts only
    when one of its segments is a document directory (`docs/decisions`, `adr/0007`; not
    `owner/repo`, `and/or`, `src/components`). Any other extension (source, script, config, data:
    `src/x.py`, `config/app.yaml`, `app.json`), a build file without one (`Makefile`), and any
    suffix that locates code (":42", ":42-50", "::test_it", ":Cache.evict", "()", "#L10",
    "#name") make it code.
    """
    name = body.strip()
    if not name or any(char.isspace() for char in name):
        return False
    if _URL_START_RE.match(name):
        return True
    base = name.rsplit("/", 1)[-1]
    if any(char in base for char in ":#("):
        return False
    if base.lower() in _CODE_FILE_NAMES:
        return False
    extension = base.lstrip(".").rpartition(".")[2].lower() if "." in base.lstrip(".") else ""
    if extension:
        return extension in _DOC_EXTENSIONS
    return "/" in name and any(segment.lower() in _DOC_DIRECTORIES for segment in name.split("/"))


def _is_path(name: str) -> bool:
    """Whether a source found in prose is a path with a document extension (not a phrase, a URL)."""
    return name.rpartition(".")[2].lower() in _DOC_EXTENSIONS and not _URL_START_RE.match(name)


def _normalized(name: str) -> str:
    """A cited name without a fragment, a line suffix, a leading ``./`` or a call's ``()``."""
    name = _LINE_SUFFIX_RE.sub("", name.strip().partition("#")[0])
    return name.removeprefix("./").removesuffix("()")


def _is_own(
    candidate: str, bodies: _Bodies, within: tuple[int, int], marker: int, sentence: str
) -> bool:
    """Whether the claim's own code names the same thing as the candidate source.

    The claim's own code is the code spans before the marker (``marker`` is where its source
    starts) and the first code span of the sentence outside the candidate (``within``, the
    candidate's own text), for "Per `docs/a.md`, `docs/a.md` is broken". When no code span comes
    before the marker, the claim follows it, and its own code is every span in its clause too:
    "Per `docs/a.md`, the root cause is `retry()` in `docs/a.md`". That clause ends at the next
    semicolon, parenthesis, bracket or dash, so a later mention of the same document ("per
    `docs/policy.md`, `a.py` is fixed (see `docs/policy.md`)", or after the claim in a sentence
    that starts with the claim) is a second mention of the source, not the claim citing itself.
    """
    name = _normalized(candidate)
    others = [(start, body) for start, body in bodies if not within[0] <= start < within[1]]
    own = [body for start, body in others if start < marker] + [body for _, body in others[:1]]
    if all(start >= marker for start, _ in others):  # the marker comes first: a claim follows it
        prose = _CODE_SPAN_RE.sub(lambda m: " " * len(m.group()), sentence)
        aside = _ASIDE_RE.search(prose, within[1])
        end = aside.start() if aside else len(sentence)
        own += [body for start, body in others if start < end]
    return any(_normalized(body) == name for body in own)


def _names_a_source(sentence: str, position: int, bodies: _Bodies) -> bool:
    """Whether a document (or other P3 source) starts at ``position`` and is not the claim's own."""
    start = position
    while start < len(sentence) and sentence[start] in " \t*_":
        start += 1
    determiner = _DETERMINER_RE.match(sentence, start)
    for at in {start, determiner.end() if determiner else start}:
        code = _CODE_SPAN_RE.match(sentence, at)
        if code:
            body = code.group()[1:-1]
            if _doc_like(body) and not _is_own(body, bodies, code.span(), position, sentence):
                return True
            continue
        found = _SOURCE_NAME_RE.match(sentence, at)
        if not found:
            continue
        target = found.group("target")
        name = found.group() if target is None else target
        # a link may point at an anchor ("docs/policy.md#retention"): that is link syntax
        if target is not None and not _doc_like(target.partition("#")[0]):
            continue
        # a path in prose follows the rule for a code span: a suffix makes it a place in code
        if target is None and _is_path(name) and _LOCATOR_RE.match(sentence, found.end()):
            continue
        if not _is_own(name, bodies, found.span(), position, sentence):
            return True
    return False


def _convention_source(sentence: str, position: int, end: int, bodies: _Bodies) -> bool:
    """Whether the bracket after "by convention" holds a source: a name, or a tag with one."""
    tag = _SOURCED_TAG_RE.match(sentence, position, end)
    if tag:
        return bool(tag.group(1).strip())
    return _names_a_source(sentence, position, bodies)


def _cites_a_source(sentence: str) -> bool:
    """P3: "per <doc>", "by convention (<source>)" or "Source: <name>" in the sentence.

    The markers are found in the sentence with its code spans blanked (to equal length, so a
    position in one is a position in the other); the source that follows may be a code span.
    """
    bodies = [(m.start(), m.group()[1:-1]) for m in _CODE_SPAN_RE.finditer(sentence)]
    prose = _CODE_SPAN_RE.sub(lambda m: " " * len(m.group()), sentence)
    if any(_names_a_source(sentence, m.end(), bodies) for m in _PER_RE.finditer(prose)):
        return True
    if any(_names_a_source(sentence, m.end(), bodies) for m in _SOURCE_LABEL_RE.finditer(prose)):
        return True
    return any(
        _convention_source(sentence, m.start(1), m.end(1), bodies)
        for m in _CONVENTION_RE.finditer(prose)
    )


def _bound(sentence: str, claim_phrases: list[str]) -> bool:
    """Whether a pin in ``sentence`` is tied to verification (policy P1).

    A verification verb counts where the sentence asserts it: "never confirmed" and "was not
    checked" bind nothing. The claim's own certainty phrase is not a free-floating verb. Each
    phrase the sentence asserts that is itself a verification verb is part of the claim, and it
    binds only a pin in its own clause (the text between semicolons), also when the claim has
    another phrase too: "`route()` confirmed on 2026-09-20." and "`a.py` is fixed (verified
    2026-10-01)" are dated verifications, but in "`route()` is fixed and verified; the deadline
    is 2026-10-09." the date is about something else. A verb that is not a claim phrase binds
    anywhere in the sentence ("`route()` is verified; we ran the batch on 2026-10-09." stays
    quiet: a known bypass).
    """
    phrases = {phrase.lower() for phrase in claim_phrases}
    own = phrases & _VERB_WORDS
    # Code spans are blanked to equal length, so a position in one text is a position in the other.
    prose = _CODE_SPAN_RE.sub(lambda m: " " * len(m.group()), sentence)
    breaks = [m.start() for m in re.finditer(";", prose)]
    pin_clauses = {bisect.bisect(breaks, m.start()) for m in _PIN_RE.finditer(sentence)}
    for verb in _VERB_RE.finditer(prose):
        before = prose[max(0, verb.start() - _NEGATION_WINDOW) : verb.start()]
        if _NEGATED_BEFORE_RE.search(before):
            continue
        if verb.group().lower() in own and bisect.bisect(breaks, verb.start()) not in pin_clauses:
            continue
        return True
    return bool(_MARKER_RE.search(sentence))


# What a sentence says versus what it only mentions. A certainty phrase counts only where the
# sentence asserts it, so the checks below blank out the parts that do not: a question, a quote,
# an instructed clause and a denied clause. A negated phrase is skipped where the phrase is found.
_AUXILIARIES = "is|are|was|were|do|does|did|has|have|had|can|could|will|would|should|may|might|must"
_QUESTION_END_RE = re.compile(r"\?[\s*_\"'\u201d\u2019]*$")
# A question without its question mark: an interrogative word and an auxiliary ("How was `a.py`
# confirmed"), or an inverted auxiliary followed by a subject ("Is `a.py` tested"). Telegraphic
# notes drop the subject ("Was verified in `a.py`"), which is a statement and does not match.
_INVERTED_RE = re.compile(
    rf"^[\W_]*(?:(?:what|why|how|when|where|which|who|whom|whose)\s+(?:{_AUXILIARIES})\b"
    r"|(?:is|are|was|were|do|does|did|has|have|had|can|shall)\s+"
    r"(?:`|(?:the|a|an|this|that|these|those|it|there|he|she|they|we|you|i|any|all|each|every"
    r"|my|our|their|its|his|her)\b|(?-i:[A-Z])))",
    re.IGNORECASE,
)
# Where a clause ends. A reason ("because", "since", "as it"), a relative clause ("which", "who",
# "that"), "knowing", "so", and every conjunction ("and", "or", "but", "while", ...) start a clause
# that carries its own claim. No subject or verb is required after a conjunction: guessing whether
# "and `b`" starts a clause kept re-hiding real claims, so it always does (when unsure, warn).
_CLAUSE_BREAK_RE = re.compile(
    r";|:|[\u2014\u2013]|\s--?\s|,\s+(?:but|yet|however|although|though|while|whereas)\s"
    r"|,\s+(?:it|this|that|these|those|they|we|there)\s"
    r"|\b(?:note|notice|remember|recall|know|keep in mind|bear in mind|be aware|beware)\s+that\b"
    r"|\b(?:because|since|which|who|whom|whose|knowing|so|that)\b"
    r"|\bas\s+(?=(?:it|they|this|that|these|those|we|there|he|she|you)\b)"
    r"|\b(?:and|or|but|while|whereas|although|though|however)\b"
    # "yet" is also an adverb ("is not yet the root cause"): it breaks only before a pronoun or code
    r"|\byet\s+(?=(?:X|it|they|this|these|those|we|there|he|she|you)\b)",
    re.IGNORECASE,
)
_QUOTED_RE = re.compile(r'"[^"]*"|\u201c[^\u201d]*\u201d')
_CURLY_QUOTED_RE = re.compile(r"\u201c[^\u201d]*\u201d")
# An instruction exempts a certainty phrase only inside the complement of a verification-type
# imperative: check, verify, confirm, ensure, make sure, test, record, document, note whether,
# determine whether. The imperative must open a clause (after "and", "then", a comma, a colon, a
# bracket or a dash, and after adverbs such as "please" and "do not") and take an object ("Check
# that `a.py` is fixed", "Record the root cause in `issue.md`"). The complement ends at the first
# clause break or opening parenthesis; every other certainty phrase in the sentence is read as an
# assertion, even when it follows an instruction ("Avoid `a.py` because it always fails").
_VERIFICATION_RE = re.compile(
    r"\b(?:(?:(?:re-?)?(?:check|verify|confirm|test)|ensure|record|document)\b"
    r"|(?:make|be)\s+(?:sure|certain)\b|(?:note|determine)\s+whether\b)",
    re.IGNORECASE,
)
_WHETHER_VERB_RE = re.compile(r"(?:make|be)\s+(?:sure|certain)|(?:note|determine)\s+whether", re.I)
_ADVERBS_BEFORE_VERB = (
    "please|kindly|always|never|only|just|also|still|then|first|next|finally|now"
    "|do not|don['\u2019]t|do|never ever"
)
_IMPERATIVE_POSITION_RE = re.compile(
    r"(?:^[\W_]*(?:\d+[.)]\s+)?"
    r"|(?:[;:,(\u2014\u2013]|\s--?\s|\b(?:and|then|but|or|so)\b)[\W_]*)"
    rf"(?:(?:{_ADVERBS_BEFORE_VERB})\s+)*$",
    re.IGNORECASE,
)
# The verb takes an object: a code span, a determiner, a pronoun, "that" or "whether". A noun that
# looks like the verb does not ("Test results confirmed `a.py`", "Record shows `a.py` is broken").
_OBJECT_RE = re.compile(
    r"[\s*_]+(?:X|the|a|an|this|that|these|those|your|our|all|any|each|every|it|them|whether|if"
    r"|how|again|first|before|after|once|manually|locally|carefully|with|in|on|at|for|only|and|or"
    r"|not|no|never|always)\b|[\s*_]*$",
    re.IGNORECASE,
)
_COMPLEMENT_START_RE = re.compile(r"[\s*_]*(?:that|whether|if)\b", re.IGNORECASE)
_COMPLEMENT_END_RE = re.compile(rf"{_CLAUSE_BREAK_RE.pattern}|\(", re.IGNORECASE)
# "that" after one of these words completes it ("It is false that `a.py` is fixed"); after anything
# else it opens a relative clause ("Avoid `a.py` that fails").
_COMPLEMENT_RE = re.compile(
    r"\b(?:false|untrue|incorrect|true|case|evidence|proof)[\s*_]+$", re.IGNORECASE
)
_DENIAL_RE = re.compile(
    r"\b(?:false|untrue|incorrect|not true|not the case|no evidence|no proof)\s+that\b",
    re.IGNORECASE,
)
# A negator right before a certainty phrase, with at most two fillers between ("never been
# confirmed", "not always verified", "not the root cause", "wasn't tested"). The phrase's own
# words are never part of this: "does not support" is a claim, not a negated one.
_NEGATION_WINDOW = 60  # a negator, two fillers and the blanks between them fit in 60 characters
_NEGATED_BEFORE_RE = re.compile(
    r"(?:\b(?:not|never|no longer|cannot|hardly|barely|neither|nor)|n['\u2019]t)"
    r"(?:\s+(?:been|be|yet|ever|always|really|actually|the|a|an|at all|even|quite|fully|properly"
    r"|entirely|completely|formally|independently|manually)){0,2}\s*$",
    re.IGNORECASE,
)


def _is_question(sentence: str) -> bool:
    text = sentence.strip()
    if _QUESTION_END_RE.search(text):
        return True
    return not _CLAUSE_BREAK_RE.search(text) and bool(_INVERTED_RE.match(text))


def _mask_quotes(text: str) -> str:
    """Blank out quoted text. An odd count of plain quotation marks pairs nothing."""
    pattern = _QUOTED_RE if text.count('"') % 2 == 0 else _CURLY_QUOTED_RE
    return pattern.sub(lambda m: " " * len(m.group()), text)


def _clauses(text: str) -> list[str]:
    """``text`` cut at every clause break, except a "that" that completes its verb."""
    clauses, start = [], 0
    for match in _CLAUSE_BREAK_RE.finditer(text):
        if match.group().strip().lower() == "that":
            before = text[max(start, match.start() - 30) : match.start()]
            if _COMPLEMENT_RE.search(before):
                continue
        clauses.append(text[start : match.start()])
        start = match.end()
    clauses.append(text[start:])
    return clauses


def _blank_complements(text: str) -> str:
    """``text`` with the complement of every verification-type imperative blanked out."""
    pieces, position = [], 0
    for verb in _VERIFICATION_RE.finditer(text):
        if verb.start() < position:
            continue
        before = text[max(0, verb.start() - 60) : verb.start()]
        if not _IMPERATIVE_POSITION_RE.search(before):
            continue
        if not _WHETHER_VERB_RE.fullmatch(verb.group()) and not _OBJECT_RE.match(text, verb.end()):
            continue
        start = verb.end()
        opening = _COMPLEMENT_START_RE.match(text, start)
        if opening:
            start = opening.end()
        end = _COMPLEMENT_END_RE.search(text, start)
        stop = end.start() if end else len(text)
        pieces.append(text[position : verb.start()])
        pieces.append(" ")
        position = stop
    pieces.append(text[position:])
    return "".join(pieces)


def _asserting_text(core: str) -> str:
    """The part of a sentence that asserts: no question, quote, instruction complement or denial.

    Code spans become an ``X`` placeholder so that a verb and its object stay apart ("Ensure X is
    tested") without the code being read as words.
    """
    if _is_question(core):
        return ""
    text = _blank_complements(_mask_quotes(_CODE_SPAN_RE.sub(" X ", core)))
    kept = []
    for clause in _clauses(text):
        denial = _DENIAL_RE.search(clause)
        kept.append(clause[: denial.start()] if denial else clause)
    return " ; ".join(kept)


def _asserts(rx: re.Pattern[str], asserting: str) -> bool:
    """Whether ``rx`` matches somewhere that no negator directly governs."""
    return any(
        not _NEGATED_BEFORE_RE.search(
            asserting[max(0, match.start() - _NEGATION_WINDOW) : match.start()]
        )
        for match in rx.finditer(asserting)
    )


class _Sentence(NamedTuple):
    text: str
    tagged: bool = False  # a verification tag follows it, or ends it (P2)


class _Statement(NamedTuple):
    line: int
    text: str
    below: tuple[int, bool] | None = None  # (line, is a marker line) of the line that credits it


def _sentences(line: str) -> list[_Sentence]:
    """The sentences of one line. A verification tag stays with the sentence before it.

    A tag after a full stop is not a sentence of its own: it is attached to the sentence it
    follows, and that sentence is ``tagged``: P2 credits it. So is a sentence that a tag ends
    ("- `a.py` is fixed **[verified: main.py]**"); a tag with text after it is part of the
    statement.
    """
    spans, start = [], 0
    for match in _SENTENCE_RE.finditer(line):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(line)))
    sentences: list[_Sentence] = []
    for first, last in spans:
        piece = line[first:last]
        while sentences and (tag := _LEADING_TAG_RE.match(piece)):
            sentences[-1] = _Sentence(sentences[-1].text + " " + tag.group().strip(), True)
            piece = piece[tag.end() :].lstrip()
        if piece.strip():
            sentences.append(_Sentence(piece))
    return [
        _Sentence(sentence.text, sentence.tagged or _ends_with_tag(sentence.text))
        for sentence in sentences
    ]


def _ends_with_tag(sentence: str) -> bool:
    """Whether a verification tag ends ``sentence``: only its full stop and blanks come after it."""
    end = sentence.rstrip(" \t.!?")
    if end[-1:] not in ("]", ")", "*", "_"):
        return False
    tags = list(_TAG_RE.finditer(sentence))
    return bool(tags) and not sentence[tags[-1].end() :].strip(" \t.!?")


def _is_tag_line(line: str) -> bool:
    """Whether ``line`` holds verification tags and nothing else (a list marker aside).

    The tags are cut out with one pass of ``_TAG_RE`` and the rest must be blanks and emphasis
    marks. A single regex that repeats the tag pattern can cut an emphasis run between two tags in
    many ways and backtracks exponentially on a line of them.
    """
    rest = _TAG_RE.sub("", _LIST_MARKER_RE.sub("", line, count=1))
    return not rest.strip(" \t*_") and _TAG_RE.search(line) is not None


def _item_indent(rows: dict[int, str], number: int) -> int | None:
    """The indent of the list item that line ``number`` belongs to, or None outside a list.

    A line belongs to the nearest list-item line at or above it in the same run of non-blank
    lines: a line that continues a bullet (indented or lazily) belongs to that bullet.
    """
    while (line := rows.get(number)) is not None and line.strip() and not HEADING_RE.match(line):
        item = _LIST_ITEM_RE.match(line)
        if item:
            return len(item.group(1).expandtabs())
        number -= 1
    return None


def _credit_below(
    rows: dict[int, str], number: int, skip_lines: frozenset[int]
) -> tuple[int, bool] | None:
    """The line directly below ``number`` if it credits the line (P2), as ``(line, is marker)``.

    It credits when it starts with a provenance marker or holds a standalone verification tag, and
    it belongs to the line's own item: a continuation, or a bullet nested deeper than the line's
    bullet. A bullet at the same level (or a bullet below a line that is no bullet) is another item.
    """
    below = rows.get(number + 1)
    if below is None or number + 1 in skip_lines or not below.strip() or len(below) > MAX_LINE:
        return None
    marker = _MARKER_LINE_RE.match(below) is not None
    if not marker and not _is_tag_line(below):
        return None
    bullet = _LIST_ITEM_RE.match(below)
    if bullet:
        parent = _item_indent(rows, number)
        if parent is None or len(bullet.group(1).expandtabs()) <= parent:
            return None
    return number + 1, marker


def _without_label(line: str) -> str:
    """``line`` without the label of a marker line ("Verified:", "Tested:", ...).

    The label is the name of provenance, not a claim: "Verified: by hand in `a.py`" says how
    something was checked. "Source:" stays, since "Source: <name>" is itself a P3 credit.
    """
    marker = _MARKER_LINE_RE.match(line)
    if marker is None or marker.group("label").lower() == "source":
        return line
    return line[marker.end() :]


def _statements(text: str, skip_lines: frozenset[int]) -> list[_Statement]:
    """The prose lines to read, each with the line below that credits it (P2), if any.

    A blockquote line quotes someone else and is left out. A line that holds only a verification
    tag is no statement of its own: it credits the line above, or, with nothing that it belongs
    to there, it is dropped.
    """
    rows = prose_lines(text)
    by_number = dict(rows)
    statements: list[_Statement] = []
    for number, line in rows:
        if not line.strip() or number in skip_lines or len(line) > MAX_LINE:
            continue
        if _BLOCKQUOTE_RE.match(line) or _is_tag_line(line):
            continue
        statements.append(_Statement(number, line, _credit_below(by_number, number, skip_lines)))
    return statements


def _claims_in(
    number: int,
    line: str,
    phrase_res: list[tuple[str, re.Pattern[str]]],
    hedge_res: list[re.Pattern[str]],
) -> tuple[list[ClaimHit], bool]:
    """The uncredited claims of one line, and whether the line holds a claim at all.

    A claim is a sentence that asserts a certainty phrase; it is credited, and so no hit, when the
    sentence says how it was checked.
    """
    hits: list[ClaimHit] = []
    claimed = False
    for sentence, tagged in _sentences(line):
        if tagged:  # P2: a standalone tag directly after the sentence credits it
            continue
        prose = _CODE_SPAN_RE.sub(" ", sentence)
        if not _names_code(sentence, prose):
            continue
        if _ATTRIBUTED_RE.search(prose):
            continue
        if any(rx.search(prose) for rx in hedge_res):
            continue
        # A tag inside the sentence is the writer's own word about it: read as written, whatever
        # the sentence itself is (a question, an instruction).
        tags = " ".join(m.group() for m in _TAG_RE.finditer(sentence))
        asserting = _asserting_text(_TAG_RE.sub(" ", sentence))
        asserting += " ; " + _CODE_SPAN_RE.sub(" X ", tags)
        asserted = [phrase for phrase, rx in phrase_res if _asserts(rx, asserting)]
        if not asserted:
            continue
        claimed = True
        if _credited(sentence, asserted):  # the sentence says when, with what or against what
            continue
        hits.append(ClaimHit(number, asserted[0], sentence.strip()))
    return hits, claimed


def find_claims(
    text: str,
    skip_lines: frozenset[int] = frozenset(),
    phrases: tuple[str, ...] = DEFAULT_PHRASES,
    hedges: tuple[str, ...] = DEFAULT_HEDGES,
) -> list[ClaimHit]:
    phrase_res = [(phrase, _word(phrase)) for phrase in phrases]
    hedge_res = [_word(hedge) for hedge in hedges]
    hits: list[ClaimHit] = []
    labelled: set[int] = set()  # marker lines that serve a claim above: their label asserts nothing
    for statement in _statements(text, skip_lines):
        line = statement.text
        if statement.line in labelled:
            line = _without_label(line)
        found, claimed = _claims_in(statement.line, line, phrase_res, hedge_res)
        if claimed and statement.below and statement.below[1]:
            labelled.add(statement.below[0])
        if not (found and statement.below):  # P2: a marker line or a tag directly below credits it
            hits.extend(found)
    return hits
