"""Find symbols and keys inside files, with the standard library only.

Python files are parsed with ``ast``. Other brace languages use a conservative scan: a
declaration line, then the matching closing brace.

Invariant: a returned span is never shorter than the real declaration. Whenever the scan is
not sure (a comment, string or template that spans lines, a regex literal beside braces,
several declarations that match, a declaration that may continue on the next line) it raises
``Unparseable``, and the caller falls back to fingerprinting the whole file, which can only
over-report. ``NotFound`` is raised only when the name is absent from the text as written, so a
file the scan misread can never make an existing symbol look deleted.

Every span the scan proposes is then checked against indentation, which catches shapes the
scan does not know about: a multi-line span must end on a closing line at the declaration's own
indentation, and the next line must be neither deeper nor a continuation.

C and C++ are read as the compiler reads them after translation phase 2: a line that ends in a
backslash is joined with the next one (spans are given in physical lines, every joined line
whole), and a preprocessor line is not code. That is the joined line that starts with ``#``, and
the lines after it while a block comment it opened is open. A backtick is an ordinary character,
a ``'`` inside a number (``1'000``) is a digit separator, and a raw string ends at its delimiter.

The lines directly above a declaration that belong to it join its span: decorators, annotations
and attributes (one that spans lines joins when its brackets balance and it holds nothing but
attributes) and, in C and C++, ``template<...>`` headers, ``requires`` clauses and the qualifier
lines ``export``, ``inline`` and ``constexpr``. The aim is that editing only such a line is never
fresh. The scan holds that for the forms it reads, and otherwise raises ``Unparseable`` when the
code line above the prefix may be part of the declaration: an unknown macro or qualifier, a
header or clause that does not balance, the end of a ``requires`` expression. The limits:
- a bare macro that is not a keyword (``EXPORT_API``, ``DECL(a, b)``) with a blank line between
  it and the prefix, and a statement end, a block boundary or the start of the file above it,
  is taken for an unrelated line (a comment line does not separate); with anything else above
  it, or directly above the prefix, it raises, and so it does when the same file ``#define``s
  that name (it may expand to a template header); a macro another file defines is not claimed;
- a prefix line that names an identifier the same file ``#define``s (``requires Small<T> &&
  LIMIT`` with ``#define LIMIT ...`` anywhere in the file, a ``template<typename T = DEFAULT_T>``
  parameter) makes the declaration fall back: the macro may expand to part of the prefix, and its
  ``#define`` lies outside the span, so an edit to it would read fresh. The scan does not expand
  macros anywhere else either: one used in the body, or defined in another file, is not claimed;
- a ``requires`` clause in parentheses after a declarator is taken for a requires expression,
  and the declaration below such a function falls back;
- in C and C++, a conditional directive (``#if``, ``#else``, ``#endif``, also one a splice or a
  comment spells over lines: ``#/* c`` / ``*/ if 0``) between the code line above a declaration
  and the declaration makes it fall back: the preprocessor is not evaluated, so that line may be
  dead code (this includes an ``#ifdef`` directly above a declaration, and conditional lines
  inside its prefix, unless the file starts there);
- in C and C++, a conditional directive between a declaration's first line and its closing
  brace makes it, and every member of it, fall back (a dead branch can hold a brace the compiler
  never sees), and so does a preprocessor line between a declarator and the body that follows
  it;
- a C or C++ file where a backslash-newline joins lines outside a preprocessor line (in code or
  in a comment) is never isolated, and neither is one with a ``#define`` whose braces do not
  balance (the scan does not expand macros, so it cannot see where such a macro opens or closes
  a block; a macro defined in another file that does so is not claimed);
- a C or C++ file that holds a line separator other than LF, CR or CRLF (a form feed, a vertical
  tab, 0x1c to 0x1e, 0x85, U+2028 or U+2029) is never isolated: the scan splits lines there and
  the compiler does not, so a comment could hide code from it. Nor is one with a NUL byte or
  U+180E (a space to the compiler, not to the scan: ``#<NUL>if 0`` is a directive), or with a
  header name that holds ``/*`` or ``//`` (``#include <a/*b>`` and a C++20 header-unit
  ``import <a/*b>;`` or ``import "a//b";`` name a file, they open no comment; the import is read
  on any line that holds the word ``import``, so a comment, string or macro that spells one
  refuses too), a digraph outside comments and strings (``<%``, ``%>``, ``%:``), or a trigraph
  for a brace, a bracket, a ``#`` or a backslash (``??<``, ``??=``, ``??/``; a C compiler in a
  strict mode reads them);
- a C or C++ file with a preprocessor line that opens a quote and does not close it (``#define X
  '/*``, ``#error don't``) is never isolated: the compiler's token runs to the end of the line
  and the scan's lexer may start a comment inside it. The same goes for any line (a skipped
  branch included) with an unclosed quote or a digit separator followed by ``/*``, for a raw
  string literal that runs over several lines, and for a backslash followed by a line feed and a
  lone carriage return (one newline to the compiler, two lines to the scan);
- free functions, aliases, unions and concepts in C and C++ are never isolated, so they always
  fall back to the file.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
import warnings
from dataclasses import dataclass, replace
from typing import Any

PYTHON_SUFFIXES = (".py", ".pyi")
BRACED_SUFFIXES = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".cs",
    ".swift",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
)
CXX_SUFFIXES = (".c", ".h", ".cc", ".cpp", ".hpp")  # the prefix grammar of C and C++ applies
DIRECTIVE_SUFFIXES = (*CXX_SUFFIXES, ".cs", ".swift")  # languages with `#if` lines
_MODIFIERS = (
    r"(?:(?:export|default|declare|public|private|protected|internal|static|final|abstract|"
    r"override|async|virtual|sealed|readonly|unsafe|pub(?:\([^)]*\))?|data|open|inner|value|enum|"
    r"const|suspend|inline|operator|infix|tailrec|external|lateinit|companion|"
    r'extern(?:\s+"")?)\s+)*'
)
_BEFORE = r"(?<![\w$])"  # identifier boundaries: `\b` does not treat `$` as part of a name
_AFTER = r"(?![\w$])"
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')
# A C or C++ identifier, or a number (a pp-number: `1'000`, `0x1'F`, `1e+5`, `.5`), read whole so a
# `'` inside a number is a digit separator, not the start of a character literal.
_CXX_WORD_RE = re.compile(r"[A-Za-z_$][\w$]*|\.?\d(?:[eEpP][+-]|'[0-9A-Za-z_]|[\w.])*")
# A raw string literal: one of these prefixes, then `"delimiter(`; it ends at `)delimiter"`.
_RAW_PREFIXES = frozenset({"R", "u8R", "uR", "UR", "LR"})
_RAW_OPENING_RE = re.compile(r'"([^()\\ \t\v\f]{0,16})\(')
# What closes a block comment or a multi-line template on a later line.
_TAILS = {"block": re.compile(r".*?\*/"), "template": re.compile(r"(?:\\.|[^`\\])*`")}
# A line that ends with one of these continues on the next line.
_CONTINUES = (
    "=",
    "(",
    ",",
    "=>",
    "<",
    ":",
    "[",
    "->",
    "||",
    "&&",
    "??",
    "+",
    "-",
    "*",
    "/",
    "|",
    "&",
    "?",
    ".",
    "%",
    "^",
)
# ... and so does one that ends with a keyword operator, as a whole word.
_ENDS_WITH_KEYWORD_RE = re.compile(r"(?<![\w$])(?:typeof|await|new|void|in|instanceof|delete)\s*$")
# A next line that starts with one of these continues a declaration that looked complete.
_NEXT_LINE_LEADS = (".", "?", ":", "|", "&", "+", "-", "*", "/", "=", "`")
_AFTER_BRACE_RE = re.compile(r"[.?:|&=`]|(?:as|satisfies)" + _AFTER)
# The next line starts like the middle of a declaration, not like a new one.
_CONTINUATION_RE = re.compile(
    r"[{.?:|&+\-*/=>,`(\[]|(?:extends|implements|where|throws|as|satisfies|in|instanceof|else|catch"
    r"|finally)" + _AFTER
)
# A line that begins an attribute: decorator, annotation, Rust or C# attribute, C++ template.
_ATTRIBUTE_RE = re.compile(r"@|#\[|\[|template\s*<")
# A trailing clause of a C++ function declarator may start on the line after the declarator.
_TRAILING_CLAUSE_RE = re.compile(
    r"(?:requires|noexcept|const|volatile|override|final|throw)" + _AFTER
)
# A preprocessor line (`#[` is a Rust attribute, not one) and the ones that open or close a branch.
_DIRECTIVE_RE = re.compile(
    r"#\s*(?:if|ifdef|ifndef|elif|elifdef|elifndef|else|elseif|endif|region|endregion|pragma"
    r"|define|undef|include|import|line|error|warning|nullable)" + _AFTER
)
_CONDITIONAL_RE = re.compile(
    r"#\s*(?:if|ifdef|ifndef|elif|elifdef|elifndef|else|elseif|endif)" + _AFTER
)
_DEFINE_RE = re.compile(r"#\s*define" + _AFTER)
_DEFINE_NAME_RE = re.compile(r"#\s*define\s+([A-Za-z_$][\w$]*)")  # `#define NAME` and `NAME(a)`
_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][\w$]*")
# The items a C++ declaration can start with, one per line or several to a line.
_CXX_QUALIFIER_RE = re.compile(r"(?:export|inline|constexpr)" + _AFTER)
_CXX_ATTRIBUTE_RE = re.compile(r"(?:alignas|__attribute__|__declspec)\s*\(")
_CXX_TEMPLATE_RE = re.compile(r"template\s*<")
_CXX_REQUIRES_RE = re.compile(_BEFORE + r"requires" + _AFTER)
_CXX_REQUIRES_END_RE = re.compile(_BEFORE + r"requires$")
# Keywords and qualifiers: a line of one of these is part of a declaration, never a bare macro.
_CXX_KEYWORD_RE = re.compile(
    r"(?:alignas|alignof|auto|bool|char|char8_t|char16_t|char32_t|class|concept|const|consteval"
    r"|constexpr|constinit|decltype|default|delete|double|enum|explicit|export|extern|false|final"
    r"|float|friend|inline|int|long|mutable|namespace|new|noexcept|nullptr|operator|override"
    r"|private|protected|public|register|requires|return|short|signed|sizeof|static|struct"
    r"|template|this|thread_local|throw|true|typedef|typeid|typename|union|unsigned|using"
    r"|virtual|void|volatile|wchar_t|__attribute__|__declspec|_Alignas|_Noreturn)$"
)
_CXX_OPERATOR_WORDS = frozenset({"and", "or", "not", "xor", "bitand", "bitor", "compl"})
# `str.splitlines` ends a line at these too; a C or C++ compiler does not (only \n, \r, \r\n).
_FOREIGN_SEPARATOR_RE = re.compile("[\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")
# A C or C++ compiler reads these as a space, Python's `str.isspace` and `\s` do not.
_FOREIGN_SPACE_RE = re.compile("[\x00\u180e]")
# Trigraphs for `#`, `\`, `[`, `]`, `{` and `}` (a C compiler in a strict mode reads them before
# anything else, in strings and comments too), and the digraphs for `{`, `}` and `#`.
_TRIGRAPH_RE = re.compile(r"\?\?[=/()<>]")
_DIGRAPHS = ("<%", "%>", "%:")
# The words a header name follows (`#include <a/*b>` names the file `a/*b`: one token to the
# compiler, where the scan's lexer would see a comment opener).
_HEADER_RE = re.compile(
    _BEFORE + r"(?:include_next|include|import|__has_include_next|__has_include)" + _AFTER
)
_HEADER_LIMIT = 32  # header names read on one preprocessor line
# A C++20 header-unit import (`import <x/*y>;`) names its header the same way, and it is code, not a
# preprocessor line.
_IMPORT_RE = re.compile(_BEFORE + r"import" + _AFTER)
_IMPORT_LINES = 32  # lines read after an `import` that a block comment carries over lines
_PREFIX_LIMIT = 40  # code lines above a declaration that are read as one prefix
_GROUP_LIMIT = 60  # lines in one multi-line attribute
_PARAMETER_WINDOW = 200  # lines searched above a block for the parameters in front of it
_SCAN_LIMIT = 2000
_BODY_SEARCH = 20


class NotFound(Exception):
    """The symbol or key does not exist."""


class Unparseable(Exception):
    """The file could not be read well enough to find the symbol or key."""


def _targets(node: ast.expr) -> list[str]:
    """Names bound by an assignment target, including tuple, list and starred unpacking."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple | ast.List):
        return [name for element in node.elts for name in _targets(element)]
    if isinstance(node, ast.Starred):
        return _targets(node.value)
    return []


def _binds(node: ast.stmt, name: str) -> bool:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name == name
    if isinstance(node, ast.Assign):
        return any(name in _targets(target) for target in node.targets)
    if isinstance(node, ast.AnnAssign | ast.AugAssign):
        return name in _targets(node.target)
    if isinstance(node, ast.Import):
        return any((a.asname or a.name.split(".")[0]) == name for a in node.names)
    if isinstance(node, ast.ImportFrom):
        return any((a.asname or a.name) == name for a in node.names)
    return False


def _sub_bodies(node: ast.stmt) -> list[list[ast.stmt]]:
    """Statement blocks nested in a compound statement, other than function and class bodies."""
    if isinstance(node, ast.Match):
        return [case.body for case in node.cases]
    if isinstance(
        node,
        ast.If
        | ast.For
        | ast.AsyncFor
        | ast.While
        | ast.With
        | ast.AsyncWith
        | ast.Try
        | ast.TryStar,
    ):
        blocks = [node.body, node.orelse] if hasattr(node, "orelse") else [node.body]
        blocks += [node.finalbody] if hasattr(node, "finalbody") else []
        blocks += [handler.body for handler in getattr(node, "handlers", [])]
        return blocks
    return []


def _binding_nodes(body: list[ast.stmt], name: str) -> list[ast.stmt]:
    """Every statement in ``body`` that binds ``name``, also inside if/try/with/for/while."""
    found: list[ast.stmt] = []
    for node in body:
        if _binds(node, name):
            found.append(node)
        for block in _sub_bodies(node):
            found.extend(_binding_nodes(block, name))
    return found


def locate_python(text: str, name: str) -> tuple[int, int]:
    """Span of a Python symbol. Several definitions of one name give the union of their spans."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # the cited file's warnings are not ours to print
            tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:  # ValueError: a NUL byte, before Python 3.12
        raise Unparseable(str(exc)) from None
    body: list[ast.stmt] = tree.body
    nodes: list[ast.stmt] = []
    for part in name.split("."):
        nodes = _binding_nodes(body, part)
        if not nodes:
            raise NotFound(name)
        body = [child for node in nodes for child in getattr(node, "body", [])]
    starts = [
        min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
        for node in nodes
    ]
    return min(starts), max(node.end_lineno or node.lineno for node in nodes)


@dataclass(frozen=True, slots=True)
class _Line:
    raw: str  # the source line as written (in C and C++ the logical line, splices joined)
    code: str  # comments removed, string contents blanked to ""
    plain: str  # comments removed, strings kept
    unsafe: bool  # a block comment, multi-line template or unmatched quote touches the line
    indent: int  # leading whitespace characters in the source line
    broken: bool = False  # a multi-line template or an unmatched quote touches the line, so
    # its code cannot be read (a block comment that opens or closes on it does not)
    open_quote: bool = False  # a `'` or `"` outside any comment has no partner on the line
    comment: bool = False  # the line ends in a `//` comment
    quote_comment: bool = False  # a `/*` follows a `'` or `"` that has no partner on the line
    # (in C and C++ also a digit separator, which a C compiler without them reads as a quote)
    raw_open: bool = False  # C and C++: a raw string literal (`R"(`) starts on the line and does
    # not end on it, or stands on a line joined by a splice
    first: int = 0  # the first and last physical line it was read from (0-based), which differ
    last: int = 0  # only for a C or C++ line joined by backslash-newlines
    directive: bool = False  # C and C++: part of a preprocessor line, so it has no code
    directive_plain: str = ""  # C and C++: on the first line of a preprocessor line that a
    # block comment carries over several lines, the `plain` text of all of them joined
    directive_raw: str = ""  # ... and the `raw` text of all of them, one per line


# A backslash that ends a line, spaces or tabs after it included: the compiler deletes it and the
# newline before it reads a token (translation phase 2), so the next line joins this one.
_SPLICE_RE = re.compile(r"\\[ \t]*$")
# A splice whose newline is LF then a lone CR: the compiler takes both as one newline, while
# `str.splitlines` ends a line at each.
_SPLICE_LF_CR_RE = re.compile(r"\\[ \t]*\n\r(?!\n)")


def _logical_lines(text: str) -> list[tuple[str, int, int]]:
    """C and C++ lines as the compiler reads them: each line ending in a splice joins the next.

    Every entry is the joined text, the backslashes removed, and its first and last physical line
    (0-based, ``str.splitlines`` numbering). A leading byte-order mark is dropped, as the
    compiler drops it. A backslash on the last line joins nothing and stays.
    """
    if _SPLICE_LF_CR_RE.search(text):
        raise Unparseable("a line splice ends in a line feed and a lone carriage return")
    physical = text.removeprefix("\ufeff").splitlines()
    logical: list[tuple[str, int, int]] = []
    parts: list[str] = []
    for number, raw in enumerate(physical):
        splice = _SPLICE_RE.search(raw)
        if splice and number + 1 < len(physical):
            parts.append(raw[: splice.start()])
            continue
        logical.append(("".join(parts) + raw, number - len(parts), number))
        parts = []
    return logical


def _lex(text: str, cxx: bool = False) -> list[_Line]:
    """The lines of ``text``, lexed. In C and C++ these are logical lines (``_logical_lines``).

    There a preprocessor line has no code: it is the logical line that starts with ``#``, and the
    lines after it while a block comment it opened stays open (the directive ends at the end of
    the line where that comment closes, so a ``*/ int x;`` there is part of it). A ``#define``
    whose braces do not balance raises (``_check_macro``).
    """
    if cxx:
        entries = _logical_lines(text)
    else:
        entries = [(raw, number, number) for number, raw in enumerate(text.splitlines())]
    lines: list[_Line] = []
    mode = ""  # "" in code, "block" inside /* */, "template" inside a multi-line backtick string
    directive = ""  # the code of a preprocessor line that a block comment carries on
    opened = 0  # the index of its first line
    for raw, first, last in entries:
        line, mode = _lex_line(raw, mode, first, last, cxx)
        if cxx and (directive or line.plain.lstrip().startswith("#")):
            if not directive:
                opened = len(lines)
            directive += line.code + " "
            lines.append(replace(line, code="", directive=True))
            if mode != "block":
                _check_macro(directive)
                directive = ""
                if opened < len(lines) - 1:  # a comment carried it over lines: join its text
                    lines[opened] = replace(
                        lines[opened],
                        directive_plain="".join(part.plain for part in lines[opened:]),
                        directive_raw="\n".join(part.raw for part in lines[opened:]),
                    )
        else:
            lines.append(line)
    return lines


def _check_macro(code: str) -> None:
    """Refuse a ``#define`` whose replacement text does not balance its braces.

    Where such a macro is used it opens or closes a block (``#define BEGIN {``, then ``struct S``
    / ``BEGIN`` / ... / ``END;``), and the scan does not expand macros. Braces in a string, a
    character literal or a comment do not count; a ``}`` before its ``{`` does not balance.
    """
    if not _DEFINE_RE.match(code.lstrip()):
        return
    depth = 0
    for char in code:
        depth += (char == "{") - (char == "}")
        if depth < 0:
            break
    if depth:
        raise Unparseable("a macro's braces do not balance, and the scan does not expand macros")


def _lex_line(
    raw: str, mode: str, first: int = 0, last: int = 0, cxx: bool = False
) -> tuple[_Line, str]:
    """Lex one line from ``mode`` (open at its start): the line and the mode open at its end.

    In C and C++ (``cxx``) a backtick is an ordinary character, not a template string, a ``'``
    inside a number is a digit separator, and a raw string ends at its delimiter. A C compiler
    without digit separators reads one as a quote, so a ``/*`` after it on the line counts as a
    comment opener after an open quote. A raw string that does not end on the line, or that
    stands on a line joined by a splice (the compiler undoes the splice inside it), is marked
    ``raw_open``. A digraph outside comments and strings raises: it spells a brace or a ``#``.
    """
    code: list[str] = []
    plain: list[str] = []
    unsafe = bool(mode)
    broken = mode == "template"
    open_quote = comment = quote_comment = raw_open = separator = False
    pos = 0
    while pos < len(raw):
        char = raw[pos]
        if mode:
            tail = _TAILS[mode].match(raw, pos)
            if tail is None:
                break
            mode, pos = "", tail.end()
            code.append(" ")
            plain.append(" ")
        elif raw.startswith("//", pos):
            comment = True
            break
        elif raw.startswith("/*", pos):
            quote_comment = quote_comment or open_quote or separator
            mode, unsafe, pos = "block", True, pos + 2
            code.append(" ")
            plain.append(" ")
        elif cxx and raw.startswith(_DIGRAPHS, pos):
            raise Unparseable("a digraph spells a brace or a '#'")
        elif cxx and (word := _CXX_WORD_RE.match(raw, pos)):
            pos = word.end()
            if word.group() in _RAW_PREFIXES and raw.startswith('"', pos):
                end = _raw_string_end(raw, pos)
                if end < 0 or first < last:
                    unsafe = broken = open_quote = raw_open = True
                    plain.append(raw[word.start() :])
                    break
                code.append(word.group() + '""')
                plain.append(raw[word.start() : end])
                pos = end
                continue
            separator = separator or "'" in word.group()
            code.append(word.group())
            plain.append(word.group())
        elif char in "\"'" or (char == "`" and not cxx):
            literal = _STRING_RE.match(raw, pos)
            if literal:
                code.append('""')
                plain.append(literal.group())
                pos = literal.end()
                continue
            if char == "`":
                mode, unsafe, broken = "template", True, True
            elif char == '"':
                unsafe = broken = open_quote = True
            else:
                open_quote = True
            plain.append(char)
            if mode != "template":
                code.append(char)
            pos += 1
        else:
            code.append(char)
            plain.append(char)
            pos += 1
    indent = len(raw) - len(raw.lstrip())
    line = _Line(
        raw,
        "".join(code),
        "".join(plain),
        unsafe,
        indent,
        broken,
        open_quote,
        comment,
        quote_comment=quote_comment,
        raw_open=raw_open,
        first=first,
        last=last,
    )
    return line, mode


def _raw_string_end(raw: str, pos: int) -> int:
    """The index after the raw string literal whose ``"`` is at ``pos``, or -1 if it goes on."""
    opening = _RAW_OPENING_RE.match(raw, pos)
    if opening is None:
        return -1
    close = raw.find(")" + opening.group(1) + '"', opening.end())
    return -1 if close < 0 else close + len(opening.group(1)) + 2


def _scan_code(line: _Line) -> str:
    """The code of a line inside a declaration, or Unparseable if it cannot be trusted."""
    if line.unsafe:
        raise Unparseable("a comment, string or template spans lines inside the declaration")
    slashes = line.code.replace("</", "").replace("/>", "")  # JSX tags are not regex literals
    if "/" in slashes and ("{" in line.code or "}" in line.code):
        raise Unparseable("a '/' beside braces may be a regex literal")
    return line.code


def _declarations(name: str) -> list[re.Pattern[str]]:
    n = re.escape(name) + _AFTER
    patterns = (
        rf"^\s*{_MODIFIERS}function\*?\s+{n}",
        rf"^\s*{_MODIFIERS}(?:class|interface|enum|type|struct|trait|object|record|namespace"
        rf"|module|mod)\s+{n}",
        rf"^\s*{_MODIFIERS}impl(?:<[^>]*>)?\s+(?:[\w:<>]+\s+for\s+)?{n}",
        rf"^\s*{_MODIFIERS}(?:const|let|var|val)\s+{n}",
        rf"^\s*{_MODIFIERS}fn\s+{n}",
        rf"^\s*{_MODIFIERS}fun\s+(?:<[^>]*>\s*)?(?:[\w<>?.,\s]*\.)?{n}",
        rf"^\s*func\s+(?:\([^)]*\)\s*)?{n}",
    )
    return [re.compile(p) for p in patterns]


def _members(name: str) -> list[re.Pattern[str]]:
    n = re.escape(name) + _AFTER
    patterns = (
        rf"^\s*{_MODIFIERS}(?:get\s+|set\s+|\*\s*)?{n}\s*(?:<[^>]*>)?\s*\(",
        rf"^\s*{_MODIFIERS}(?!return\b|new\b|await\b|throw\b|else\b|case\b|yield\b)"
        rf"[\w<>\[\],.?]+\s+{n}\s*\(",
    )
    return [re.compile(p) for p in patterns]


def _next_line(lines: list[_Line], index: int) -> _Line | None:
    """The next line after ``index`` that has code, or None at the end of the file."""
    return next((line for line in lines[index + 1 :] if line.plain.strip()), None)


def _next_head(lines: list[_Line], index: int) -> str:
    """The next line's text without its indentation, or "" at the end of the file."""
    line = _next_line(lines, index)
    return line.plain.strip() if line else ""


def _complete_here(lines: list[_Line], index: int, cxx: bool = False) -> bool:
    """True if the declaration ends on this line, False if a body starts on the next one.

    In C and C++ a ``requires``, ``noexcept``, ``const``, ``volatile``, ``override``, ``final`` or
    ``throw`` clause on the next line belongs to the declarator, so the scan goes on to the body.
    """
    following = _next_line(lines, index)
    if cxx and following and following.directive:  # it may sit between the declarator and body
        raise Unparseable("a preprocessor line follows the declarator")
    head = following.plain.strip() if following else ""
    if head.startswith("{") or (cxx and _TRAILING_CLAUSE_RE.match(head)):
        return False
    if head.startswith(_NEXT_LINE_LEADS):
        raise Unparseable("the declaration may continue on the next line")
    return True


def _continues(code: str) -> bool:
    """True if a line ends in an operator, so the declaration goes on to the next line."""
    return code.rstrip().endswith(_CONTINUES) or bool(_ENDS_WITH_KEYWORD_RE.search(code))


def _span(lines: list[_Line], start: int, cxx: bool = False) -> tuple[int, int]:
    """1-based inclusive span of the declaration that starts on line index ``start``.

    Braces inside ``()`` or ``[]`` (destructured or default parameters) must balance but never
    open or end the body.
    """
    brace = paren = 0
    opened = False
    for index in range(start, min(len(lines), start + _SCAN_LIMIT)):
        code = _scan_code(lines[index])
        for pos, char in enumerate(code):
            if char in "([":
                paren += 1
            elif char in ")]":
                paren -= 1
            elif char == "{":
                brace += 1
                opened = opened or paren == 0
            elif char == "}":
                brace -= 1
            if brace < 0 or paren < 0:
                raise Unparseable("unbalanced brackets in the declaration")
            if brace or paren:
                continue
            if char == ";":
                return start + 1, index + 1
            if char == "}" and opened and not code[pos + 1 :].strip(" \t;,)]"):
                if _AFTER_BRACE_RE.match(_next_head(lines, index)):
                    raise Unparseable("the declaration may continue after the closing brace")
                return start + 1, index + 1
        if brace or paren:
            continue
        if (index == start or opened) and not _continues(code):
            if _complete_here(lines, index, cxx):
                return start + 1, index + 1
        elif not opened and index - start >= _BODY_SEARCH:
            break
    raise Unparseable("could not find the end of the declaration")


def _unbalanced(code: str) -> bool:
    return any(code.count(a) != code.count(b) for a, b in ("()", "[]", "{}"))


def _ends_wrapped_call(code: str) -> bool:
    """True if the line ends with ``)`` or ``]`` that closes more than it opens.

    That is the tail of a wrapped decorator or annotation (``"b")``, ``])``, ``}, 2)``). A line
    that ends with ``;`` or ``}`` is an ordinary statement end, and braces are not counted.
    """
    code = code.rstrip()
    closers = code.count(")") + code.count("]")
    openers = code.count("(") + code.count("[")
    return code.endswith((")", "]")) and closers > openers


def _brackets(code: str) -> int:
    """Closing minus opening brackets of a line of code, of all three kinds."""
    return sum(code.count(c) for c in ")]}") - sum(code.count(c) for c in "([{")


_ATTRIBUTE_NAME_RE = re.compile(r"@[\w$.:]+")


def _attributes_only(text: str) -> bool:
    """True if ``text`` is nothing but attributes: ``@Name(...)``, ``#[...]`` and ``[...]``.

    A name may carry type arguments (``@Foo<Bar>(...)``). A call such as ``[1, 2].forEach(f)`` or
    a declaration that starts like an annotation has more after the first bracket group, and is
    not.
    """
    pos = _skip_space(text, 0)
    while pos < len(text):
        if text.startswith("#[", pos):
            end = _balanced(text, pos + 1, "[", "]")
        elif text[pos] == "[":
            end = _balanced(text, pos, "[", "]")
        elif name := _ATTRIBUTE_NAME_RE.match(text, pos):
            end = name.end()
            if text.startswith("<", end):  # type arguments: `@Foo<Bar>(...)`
                end = _angle_end(text, end)
            after = _skip_space(text, end) if end >= 0 else end
            if after >= 0 and text.startswith("(", after):
                end = _balanced(text, after, "(", ")")
        else:
            return False
        if end < 0:
            return False
        pos = _skip_space(text, end)
    return True


def _group_start(lines: list[_Line], last: int) -> int:
    """The first line of the multi-line attribute that ends on line ``last``.

    The brackets of ``last`` and of the lines above it are added up until they balance. The group
    must then begin on a line that starts an attribute, hold nothing but attributes, and contain
    no line the scan cannot trust. Anything else (a call that merely ends here, a declaration
    that starts like an annotation, a group that never balances) raises.
    """
    depth = 0
    codes: list[str] = []
    for probe in range(last, max(-1, last - _GROUP_LIMIT), -1):
        line = lines[probe]
        if not line.plain.strip():
            continue
        codes.append(_scan_code(line).strip())
        depth += _brackets(codes[-1])
        if depth > 0:
            continue
        if (
            probe != last
            and depth == 0
            and _ATTRIBUTE_RE.match(line.plain.lstrip())
            and _attributes_only(" ".join(reversed(codes)))
        ):
            return probe
        break
    raise Unparseable("the declaration follows the end of a multi-line attribute")


def _conditional(line: _Line) -> bool:
    """Whether the line is a conditional directive: ``#if``, ``#else``, ``#endif`` and the like.

    A comment can sit between the ``#`` and the keyword and carry the directive over lines
    (``#/* c`` / ``*/ if 0``): the compiler reads the joined text, so the check does too.
    """
    return any(_CONDITIONAL_RE.match(text.lstrip()) for text in (line.plain, line.directive_plain))


def _over_conditionals(lines: list[_Line], top: int) -> int:
    """Move up over the ``#if`` and ``#endif`` lines directly above ``top``.

    They may wrap the attribute lines that were just read, so they belong with them.
    """
    probe = top - 1
    while probe >= 0:
        if not lines[probe].plain.strip():
            probe -= 1
        elif _conditional(lines[probe]):
            top = probe
            probe -= 1
        else:
            break
    return top


def _with_attributes(lines: list[_Line], index: int, directives: bool = False) -> int:
    """Move up over the attribute lines above a declaration.

    Attributes are ``@x``, ``#[x]``, ``[X]`` and ``template<..>`` lines. One that spans lines is
    read when its brackets balance and its first line starts an attribute.

    Blank and comment-only lines between them are skipped, and so are preprocessor lines when
    ``directives`` says the language has them (``#line`` is a private field in JavaScript). A code
    line above that ends something the scan cannot match up (``"b")``, ``])`` closing a call)
    raises.
    """
    top = index
    probe = index - 1
    while probe >= 0:
        line = lines[probe]
        head = line.plain.lstrip()
        if not head or (directives and _DIRECTIVE_RE.match(head)):
            probe -= 1
        elif _ATTRIBUTE_RE.match(head):
            if _unbalanced(_scan_code(line)):
                raise Unparseable("a multi-line attribute is above the declaration")
            top = probe
            probe -= 1
        elif _ends_wrapped_call(line.code):
            top = _group_start(lines, probe)
            probe = top - 1
        else:
            break
    return _over_conditionals(lines, top) if directives and top != index else top


def _skip_space(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _balanced(text: str, start: int, opener: str, closer: str) -> int:
    """The index after the ``closer`` that matches the ``opener`` at ``start``, or -1."""
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] == opener:
            depth += 1
        elif text[pos] == closer:
            depth -= 1
            if depth == 0:
                return pos + 1
    return -1


def _angle_end(text: str, start: int) -> int:
    """The index after the ``>`` that closes the ``<`` at ``start``, or -1.

    An angle bracket inside parentheses, brackets or braces (a comparison in a default argument)
    does not count, and neither does the ``>`` of ``->``.
    """
    depth = nest = 0
    for pos in range(start, len(text)):
        char = text[pos]
        if char in "([{":
            nest += 1
        elif char in ")]}":
            nest -= 1
            if nest < 0:
                return -1
        elif nest == 0 and char == "<":
            depth += 1
        elif nest == 0 and char == ">" and text[pos - 1] != "-":
            depth -= 1
            if depth == 0:
                return pos + 1
    return -1


def _requires_clause(clause: str) -> bool:
    """True if ``clause`` (the text after ``requires``) is a whole constraint, not a part of one.

    Parentheses and brackets must balance and it must not end on an operator, so a clause that
    goes on to the next line is never taken for a complete one. A body in braces is refused.
    """
    clause = clause.strip()
    if not clause or clause[-1] in "&|,<(=!+-*/%^~:?.[":
        return False
    depth = 0
    for char in clause:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
            if depth < 0:
                return False
    last_word = re.search(r"[\w$]+$", clause)
    return depth == 0 and not (last_word and last_word.group() in _CXX_OPERATOR_WORDS)


def _cxx_prefix_text(text: str) -> bool:
    """True if ``text`` is nothing but C++ prefix items, all of them whole.

    The items are ``template<...>`` headers, ``requires`` clauses (which run to the end of the
    text), ``[[...]]``, ``alignas(...)``, ``__attribute__((...))`` and ``__declspec(...)``
    attributes, and the qualifiers ``export``, ``inline`` and ``constexpr``. Statements and
    braces are never prefixes.
    """
    if any(char in text for char in ";{}"):
        return False
    pos, found = _skip_space(text, 0), False
    while pos < len(text):
        if match := _CXX_QUALIFIER_RE.match(text, pos):
            pos = match.end()
        elif text.startswith("[[", pos):
            pos = _balanced(text, pos, "[", "]")
        elif match := _CXX_ATTRIBUTE_RE.match(text, pos):
            pos = _balanced(text, match.end() - 1, "(", ")")
        elif match := _CXX_TEMPLATE_RE.match(text, pos):
            pos = _angle_end(text, match.end() - 1)
        elif match := _CXX_REQUIRES_RE.match(text, pos):
            return _requires_clause(text[match.end() :])
        else:
            return False
        if pos < 0:
            return False
        pos, found = _skip_space(text, pos), True
    return found


def _opening_brace(lines: list[_Line], probe: int) -> tuple[int, int] | None:
    """Where the ``{`` that the ``}`` ending line ``probe`` closes stands, as (line, column).

    Preprocessor lines have no code (``_lex``), so their braces do not count.
    """
    depth = 0
    for index in range(probe, -1, -1):
        code = lines[index].code.rstrip() if index == probe else lines[index].code
        for column in range(len(code) - 1, -1, -1):
            if code[column] == "}":
                depth += 1
            elif code[column] == "{":
                depth -= 1
                if depth == 0:
                    return index, column
    return None


def _closes_requires_expression(lines: list[_Line], probe: int) -> bool:
    """True if the block that ends on line ``probe`` may be the body of a ``requires`` expression.

    That is a ``{`` that follows ``requires`` directly or follows one parenthesised group (the
    parameters, which may wrap over lines) that follows ``requires``. A block that cannot be
    matched up counts as one. Any other block (a function body, a constrained function with a
    trailing ``requires`` clause) does not.
    """
    opening = _opening_brace(lines, probe)
    if opening is None:
        return True
    index, column = opening
    window = [lines[before].code for before in range(max(0, index - _PARAMETER_WINDOW), index)]
    text = "\n".join([*window, lines[index].code[:column]]).rstrip()
    if text.endswith(")"):
        depth = 0
        for pos in range(len(text) - 1, -1, -1):
            if text[pos] == ")":
                depth += 1
            elif text[pos] == "(":
                depth -= 1
                if depth == 0:
                    text = text[:pos].rstrip()
                    break
        else:
            return True
    return _CXX_REQUIRES_END_RE.search(text) is not None


def _standalone_macro(code: str) -> bool:
    """True if ``code`` is a bare identifier or one balanced call that is not a C++ keyword.

    ``EXPORT_API`` and ``DECL(a, b)`` qualify. Anything with an angle bracket, a brace, ``&&`` or
    ``||`` in it, and any keyword or qualifier (``static``, ``virtual``, ``true``), does not.
    A bare line can still be prefix text (a constraint's last term, a macro between a template
    header and its declaration), so this alone never detaches a line.
    """
    if any(part in code for part in ("<", ">", "{", "}", "&&", "||")) or code.startswith("[["):
        return False
    if not (name := re.match(r"[A-Za-z_$][\w$]*", code)) or _CXX_KEYWORD_RE.match(name.group()):
        return False
    rest = code[name.end() :].strip()
    return not rest or (rest.startswith("(") and _balanced(rest, 0, "(", ")") == len(rest))


def _defined_names(lines: list[_Line]) -> set[str]:
    """The names that a ``#define`` anywhere in the file defines (a comment-split one included)."""
    names: set[str] = set()
    for line in lines:
        if line.directive:
            for text in (line.plain, line.directive_plain):
                if defined := _DEFINE_NAME_RE.match(text.lstrip()):
                    names.add(defined.group(1))
    return names


def _defined_here(lines: list[_Line], code: str) -> bool:
    """Whether a ``#define`` in the file defines the macro that the bare line ``code`` spells.

    ``code`` is a bare identifier or one call (``_standalone_macro``). A macro the file defines
    may expand to a template header or an attribute, and then the line is part of the declaration
    below it, blank line or not.
    """
    name = _IDENTIFIER_RE.match(code)
    return bool(name) and name.group() in _defined_names(lines)


def _check_defined_names(lines: list[_Line], prefix: list[tuple[int, str, bool]]) -> None:
    """Refuse when a line of the accepted prefix names a macro that the same file defines.

    A defined macro may expand to a constraint, a template parameter or an attribute. Its
    ``#define`` lies outside the span, so an edit to it would read fresh, and the scan does not
    expand macros to see what the prefix line really says. A macro another file defines is not
    claimed.
    """
    if not prefix:
        return
    defined = _defined_names(lines)
    for _, code, _ in prefix:
        if defined.intersection(_IDENTIFIER_RE.findall(code)):
            raise Unparseable("a line of the prefix names a macro that the file defines")


def _ends_statement(lines: list[_Line], probe: int, code: str) -> bool:
    """True if ``code`` ends a statement or a block, or opens one, or is an access label.

    A ``}`` that may end a ``requires`` expression does not count.
    """
    if code.endswith("}") and _closes_requires_expression(lines, probe):
        return False
    return code.endswith((";", "{", "}")) or (code.endswith(":") and not code.endswith("::"))


def _check_boundary(
    lines: list[_Line],
    above: list[tuple[int, str, bool]],
    best: int,
    exhausted: bool,
    index: int,
) -> None:
    """The nearest code line above a prefix must end a statement, or the prefix may go on.

    ``above[best]`` is that line. When a blank line lies between it and the prefix it is taken
    to be unrelated only if all hold: it is a bare macro that is not a keyword
    (``_standalone_macro``) that the file does not ``#define`` (``_defined_here``: it may expand
    to a template header), and the code line above it is itself a boundary, or there is none
    (the start of the file). A bare line in the middle of a constraint or between a template
    header and its declaration fails the last test.
    """
    probe, code, detached = above[best]
    if code.endswith("}") and _closes_requires_expression(lines, probe):
        raise Unparseable("a requires expression is above the declaration")
    if _ends_statement(lines, probe, code):
        return
    if detached and _standalone_macro(code) and not _defined_here(lines, code):
        if best + 1 < len(above):
            if _ends_statement(lines, above[best + 1][0], above[best + 1][1]):
                _check_gap(lines, above[best + 1][0], index)  # that line is the boundary now
                return
        elif exhausted:
            return
    raise Unparseable("a line above the declaration may be part of it")


def _check_gap(lines: list[_Line], boundary: int, index: int) -> None:
    """Refuse when the preprocessor may hide the real line above the declaration.

    The boundary line is the nearest code line, but a conditional directive between it and the
    declaration (``#if``, ``#else``, ``#endif``, also when a splice spells it over lines) can make
    it dead code, or code of another branch, and the preprocessor is not evaluated.
    """
    for probe in range(boundary, index):
        if _conditional(lines[probe]):
            raise Unparseable("a conditional directive is above the declaration")


def _cxx_prefix_top(lines: list[_Line], index: int) -> int:
    """Move up over the C++ prefix lines above a declaration, or raise if they are not certain.

    The code lines above are read as far as the first one that holds a statement end or a brace.
    The longest run of them next to the declaration that is nothing but whole prefix items is the
    prefix. The code line above that run must end a statement (``;``, ``{``, ``}`` that is not
    the end of a ``requires`` expression, or an access label), or the prefix may reach further
    than the scan can tell, and it raises. A blank line between that line and the prefix detaches
    it only when it is a bare macro (``_standalone_macro``) and the code line above it is itself a
    boundary or the file starts there: a macro that stands apart is not part of the declaration,
    while a line that may be prefix text still raises. A prefix line that names an identifier the
    same file ``#define``s raises too (``_check_defined_names``).

    Blank, comment-only and preprocessor lines are not code and are stepped over. But a
    conditional directive between the boundary line and the declaration raises (``_check_gap``):
    the boundary may be dead code. That check runs once the boundary has passed its own, so a
    refusal gives the first reason: the line above may be part of the declaration, and only then
    a directive.
    """
    # Each entry is (line index, code, whether a blank line lies between it and the line below it).
    above: list[tuple[int, str, bool]] = []
    exhausted = True
    blank = False
    probe = index - 1
    while probe >= 0:
        line = lines[probe]
        if line.directive or not line.plain.strip():
            blank = blank or not (line.directive or line.raw.strip())
            probe -= 1
            continue
        if line.broken:
            raise Unparseable("a string or template touches the line above")
        above.append((probe, line.code.strip(), blank))
        blank = False
        probe -= 1
        if len(above) >= _PREFIX_LIMIT or any(char in above[-1][1] for char in ";{}"):
            exhausted = False
            break
    best = 0
    for count in range(1, len(above) + 1):
        if any(char in above[count - 1][1] for char in ";{}"):
            break
        if _cxx_prefix_text(" ".join(code for _, code, _ in reversed(above[:count]))):
            best = count
    top = above[best - 1][0] if best else index
    if best < len(above):
        _check_boundary(lines, above, best, exhausted, index)
        _check_gap(lines, above[best][0], index)
    elif not exhausted:
        raise Unparseable("too many lines above the declaration to read as one prefix")
    _check_defined_names(lines, above[:best])
    return _over_conditionals(lines, top) if top != index else top


def _check_shape(lines: list[_Line], index: int, span: tuple[int, int]) -> None:
    """Reject a proposed span whose shape does not look like a whole declaration.

    A multi-line span must end on a closing line at the declaration's own indentation, and the
    next line must not be indented deeper or read like the middle of a declaration. This catches
    heritage clauses, ``where`` clauses and wrapped type lists that the scan does not model.
    """
    start, end = span
    indent = lines[index].indent
    if lines[end - 1].code.rstrip().endswith(","):
        raise Unparseable("the declaration ends with a comma, so it is part of a larger list")
    if end > start:
        last = lines[end - 1]
        if last.indent != indent or not last.plain.lstrip().startswith(("}", ")", "]", ">")):
            raise Unparseable("the declaration does not end on a closing line at its indentation")
    following = _next_line(lines, end - 1)
    if following and (
        following.indent > indent or _CONTINUATION_RE.match(following.plain.lstrip())
    ):
        raise Unparseable("the declaration may continue after its last line")


def _check_inside(lines: list[_Line], index: int, end: int) -> None:
    """Refuse when the preprocessor may hide braces in the declaration at ``index``.

    ``end`` is the 1-based last line of its span. The scan counts braces in every branch, and the
    compiler in one: a branch can hold a ``}`` and a ``struct Dead {`` that cancel out, so the
    scan ends the declaration early, and a member behind that dead brace reads as missing.
    """
    for probe in range(index, end):
        if _conditional(lines[probe]):
            raise Unparseable("a conditional directive is inside the declaration")


def _declaration_span(lines: list[_Line], index: int, suffix: str = "") -> tuple[int, int]:
    """Scan, check and extend over the prefix lines: the span of the declaration at ``index``.

    ``suffix`` (``.cpp``) names the language, which decides how the prefix lines are read.
    """
    cxx = suffix in CXX_SUFFIXES
    span = _span(lines, index, cxx)
    if cxx:
        _check_inside(lines, index, span[1])
    _check_shape(lines, index, span)
    if cxx:
        top = _cxx_prefix_top(lines, index)
    else:
        top = _with_attributes(lines, index, suffix in DIRECTIVE_SUFFIXES)
    return top + 1, span[1]


def _candidates(lines: list[_Line], part: str, low: int, high: int, member: bool) -> list[int]:
    """Lines in ``[low, high)`` that declare ``part``.

    A member of a container counts only on the lines that start directly inside the
    container's braces, so a call such as ``start();`` inside another method is not a rival.
    A top-level name counts only at brace depth 0 and indentation 0, so a local never wins. If
    the braces of the file do not balance (a regex or lifetime the lexer misread), the depth
    cannot be trusted and the lookup raises.
    """
    patterns = _declarations(part) + (_members(part) if member else [])
    found: list[int] = []
    depth = 0
    for index in range(low, high):
        code = lines[index].code
        top_level = depth == 0 and lines[index].indent == 0
        direct = index > low and depth == 1 if member else top_level
        if direct and any(p.match(code) for p in patterns):
            found.append(index)
        depth += code.count("{") - code.count("}")
        if not member and depth < 0:
            raise Unparseable("the braces of the file do not balance")
    if not member and depth != 0:
        raise Unparseable("the braces of the file do not balance")
    return found


def _find(
    lines: list[_Line], name: str, part: str, low: int, high: int, member: bool, first: int
) -> int:
    """The line that declares ``part`` in ``[low, high)``; ``first`` is where its container starts.

    ``first`` is the container's first line, its prefix lines included. A name that appears only
    there is still "found but not its declaration", never "missing".
    """
    found = _candidates(lines, part, low, high, member)
    if len(found) == 1:
        return found[0]
    if found:
        raise Unparseable(f"{part!r} is declared more than once")
    # Missing means absent from the text as written. The lexer can misread a regex literal as the
    # start of a template string or comment and then hide every later line, so a name that
    # appears anywhere (a comment included) is "could not be located", never "does not exist".
    word = re.compile(_BEFORE + re.escape(part) + _AFTER)
    if any(word.search(line.raw) for line in lines[first:high]):
        raise Unparseable(f"found {part!r} but not its declaration")
    raise NotFound(name)


def _go_method(lines: list[_Line], type_name: str, method: str) -> tuple[int, int] | None:
    """A Go method sits outside its type, so match its receiver: ``func (s *Type) Method``."""
    pattern = re.compile(
        rf"^\s*func\s+\([^)]*{_BEFORE}{re.escape(type_name)}{_AFTER}[^)]*\)\s*"
        rf"{re.escape(method)}{_AFTER}"
    )
    found = [i for i, line in enumerate(lines) if pattern.match(line.code)]
    if len(found) > 1:
        raise Unparseable(f"{type_name}.{method} is declared more than once")
    return _declaration_span(lines, found[0]) if found else None


def _check_splices(lines: list[_Line]) -> None:
    """Refuse a C or C++ file where a backslash-newline joins lines outside a preprocessor line.

    The lines are read as joined, but the scan's checks of a span (indentation, blank lines, where
    a line ends) are about physical lines, and such a splice is rare in code: a backslash alone
    between a declarator and its brace, a keyword or a name split over two lines, a char literal
    spliced onto a brace, a ``//`` or ``/* */`` comment whose line ends in a backslash.
    """
    for line in lines:
        if line.first < line.last and not line.directive:
            raise Unparseable("a backslash-newline joins lines outside a preprocessor line")


def _skip_comments(text: str, pos: int) -> int:
    """The position after the spaces and block comments at ``pos``."""
    while True:
        pos = _skip_space(text, pos)
        if not text.startswith("/*", pos):
            return pos
        end = text.find("*/", pos + 2)
        pos = len(text) if end < 0 else end + 2


def _check_header_names(lines: list[_Line]) -> None:
    """Refuse a C or C++ file with a header name that holds ``/*`` or ``//``.

    ``#include <a/*b>`` (also ``"a//b"``, ``#include_next``, ``#import``, ``__has_include(<a/*b>)``)
    names a file; the scan's lexer would open a comment there and take the lines below for comment
    text. A preprocessor line with more header names than the scan reads refuses too.
    """
    for line in lines:
        if line.directive:
            _check_names_in(line.raw)
            if line.directive_raw:  # a comment carried it over lines: the name may follow it
                _check_names_in(line.directive_raw)


def _check_names_in(text: str) -> None:
    """Refuse when a header name in the preprocessor text ``text`` holds ``/*`` or ``//``."""
    for count, word in enumerate(_HEADER_RE.finditer(text)):
        if count == _HEADER_LIMIT:
            raise Unparseable("too many header names on a preprocessor line")
        pos = _skip_comments(text, word.end())
        if text.startswith("(", pos):  # `__has_include(`
            pos = _skip_comments(text, pos + 1)
        if not text.startswith(("<", '"'), pos):
            continue
        close = text.find(">" if text[pos] == "<" else '"', pos + 1)
        name = text[pos : None if close < 0 else close]
        if "/*" in name or "//" in name:
            raise Unparseable("a header name holds a comment opener")


def _check_import_names(lines: list[_Line]) -> None:
    """Refuse a C++ file with an ``import`` whose header name holds ``/*`` or ``//``.

    A C++20 header-unit import (``import <x/*y>;``, ``import "x//y";``, ``export import ...``) names
    a header like an ``#include`` does, so its name is one token to the compiler and opens no
    comment, where the scan's lexer would open one and take the lines below it for comment text,
    ``template<...>`` headers included. An import is code, not a preprocessor line, so the check
    reads every other line that holds the word ``import``. A block comment that follows the word
    and goes on over lines carries the header name to the lines after it, which are read with it.
    A comment, string or macro that merely spells such an import refuses too.
    """
    for number, line in enumerate(lines):
        if line.directive or not _IMPORT_RE.search(line.raw):
            continue
        text = line.raw
        for later in lines[number + 1 : number + 1 + _IMPORT_LINES]:
            if not _comment_open_after_import(text):
                break
            text += "\n" + later.raw
        _check_names_in(text)


def _comment_open_after_import(text: str) -> bool:
    """Whether the last ``import`` in ``text`` is followed by a block comment that never closes."""
    *_, last = _IMPORT_RE.finditer(text)
    pos = last.end()
    while True:
        pos = _skip_space(text, pos)
        if not text.startswith("/*", pos):
            return False
        end = text.find("*/", pos + 2)
        if end < 0:
            return True
        pos = end + 2


def _check_open_quotes(lines: list[_Line]) -> None:
    """Refuse a file where a quote the line does not close may hide a comment opener.

    The compiler lexes such a token to the end of the line (``#define X '/*``, or ``it's /*`` in a
    skipped branch: the ``/*`` is inside it, no comment starts). The scan's lexer starts a block
    comment there and takes the lines below it for comment text, ``template<...>`` headers
    included. A raw string that goes on over several lines shows its content to the scan as code,
    a ``/*`` in it the same way. So these refuse: an unclosed quote on a preprocessor line, an
    unclosed quote or a digit separator (``1'0``, a quote to a C compiler without separators)
    followed by ``/*``, and a raw string that does not end on its line or stands on a joined one.
    """
    for line in lines:
        if line.raw_open or line.quote_comment or (line.open_quote and line.directive):
            raise Unparseable("a quote that is never closed may hide a comment opener")


def locate_braced(text: str, name: str, suffix: str = "") -> tuple[int, int]:
    """Span of a symbol in a braced language. ``suffix`` (``.cpp``) selects C and C++ prefixes."""
    suffix = suffix.lower()
    cxx = suffix in CXX_SUFFIXES
    if cxx and _FOREIGN_SEPARATOR_RE.search(text):
        raise Unparseable("a line separator that a C or C++ compiler does not know")
    if cxx and _FOREIGN_SPACE_RE.search(text):
        raise Unparseable("a NUL or U+180E, which a C or C++ compiler reads as a space")
    if cxx and _TRIGRAPH_RE.search(text):
        raise Unparseable("a trigraph may spell a brace, a bracket, a '#' or a backslash")
    lines = _lex(text, cxx)
    if cxx:
        _check_splices(lines)
        _check_header_names(lines)
        _check_import_names(lines)
        _check_open_quotes(lines)
    parts = name.split(".")
    if len(parts) == 2 and (method := _go_method(lines, *parts)):
        return _physical(lines, method)
    low, first, high = 0, 0, len(lines)
    span = (0, 0)
    for level, part in enumerate(parts):
        found = _find(lines, name, part, low, high, level > 0, first)
        span = _declaration_span(lines, found, suffix)
        low, first, high = found, span[0] - 1, span[1]
    return _physical(lines, span)


def _physical(lines: list[_Line], span: tuple[int, int]) -> tuple[int, int]:
    """A 1-based span of lexed lines as a span of physical lines, every joined line whole."""
    return lines[span[0] - 1].first + 1, lines[span[1] - 1].last + 1


def locate_key(text: str, suffix: str, dotted: str) -> Any:
    try:
        data = json.loads(text) if suffix == ".json" else tomllib.loads(text)
    except ValueError as exc:  # json.JSONDecodeError and tomllib.TOMLDecodeError
        raise Unparseable(str(exc)) from None
    node: Any = data
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif (
            isinstance(node, list)
            and part.isascii()
            and part.isdecimal()
            and len(part) <= 9
            and int(part) < len(node)
        ):
            node = node[int(part)]
        else:
            raise NotFound(dotted)
    return node
