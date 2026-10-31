"""R2: a declaration's prefix lines belong to its tracked span, or the citation falls back.

A C++ ``struct`` below ``template<typename T>`` and ``requires Small<T>`` used to be tracked from
the ``struct`` line only, so changing just the constraint answered ``fresh``. The prefix lines
(``template``, ``requires``, attributes, qualifiers) now join the span when the scan can read
them, and anything it cannot read falls back to the file, with the usual disclosure.

What these tests check, for the prefix forms they list: editing only a prefix line is never
``fresh``, a span is never shorter than its declaration, and where the span is isolated an edit
outside it stays fresh. Forms they do not list are not claimed (see the module docstring of
``freshlint.symbols`` for the limits).
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from freshlint import symbols
from freshlint.evidence import parse_citation as cite
from freshlint.fingerprint import CHANGED, FRESH, TargetMissing, compare, level_note, take

ROOT = Path(__file__).resolve().parent.parent


def rows(*lines):
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Shape:
    """A source file, the symbol cited in it, and what the scan must make of it.

    ``span`` is the exact 1-based span the scan must give, or None when it must refuse (the
    citation then falls back to the whole file). ``prefix`` lists lines whose edit must never
    be fresh; ``control`` lists lines outside the span whose edit must stay fresh when the span
    is isolated.
    """

    id: str
    suffix: str
    text: str
    name: str
    span: tuple[int, int] | None
    prefix: tuple[int, ...] = ()
    control: tuple[int, ...] = ()


CPP = [
    Shape(
        "s075-requires-above-a-struct",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires Small<T>",
            "struct Router {",
            "  T value;",
            "};",
            "int after = 2;",
        ),
        "Router",
        (2, 6),
        prefix=(2, 3),
        control=(1, 7),
    ),
    Shape(
        "template-and-requires-on-one-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T> requires Small<T>",
            "struct Router {",
            "};",
            "int after = 2;",
        ),
        "Router",
        (2, 4),
        prefix=(2,),
        control=(1, 5),
    ),
    Shape(
        "requires-continued-after-an-and",
        ".hpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires Small<T> &&",
            "         Other<T>",
            "struct S {",
            "};",
            "int after = 2;",
        ),
        "S",
        (2, 6),
        prefix=(2, 3, 4),
        control=(1, 7),
    ),
    Shape(
        "requires-continued-by-leading-operators",
        ".hpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires Small<T>",
            "    && Other<T>",
            "    || Third<T>",
            "struct S {",
            "};",
            "int after = 2;",
        ),
        "S",
        (2, 7),
        prefix=(2, 3, 4, 5),
        control=(1, 8),
    ),
    Shape(
        "requires-with-a-parenthesis-across-lines",
        ".cc",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires (sizeof(T) > 4 &&",
            "          alignof(T) < 16)",
            "struct S {",
            "};",
            "int after = 2;",
        ),
        "S",
        (2, 6),
        prefix=(2, 3, 4),
        control=(1, 7),
    ),
    Shape(
        "requires-alone-on-its-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires",
            "    (sizeof(T) > 4)",
            "struct S {",
            "};",
            "int after = 2;",
        ),
        "S",
        (2, 6),
        prefix=(2, 3, 4),
        control=(1, 7),
    ),
    Shape(
        "multi-line-template-header",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T,",
            "         typename U = int>",
            "struct S {",
            "};",
            "int after = 2;",
        ),
        "S",
        (2, 5),
        prefix=(2, 3),
        control=(1, 6),
    ),
    Shape(
        "nested-template-header",
        ".cpp",
        rows(
            "int before = 1;",
            "template<template<typename> class C, int N = (1 > 2)>",
            "struct S {",
            "};",
            "int after = 2;",
        ),
        "S",
        (2, 4),
        prefix=(2,),
        control=(1, 5),
    ),
    Shape(
        "nodiscard-above-a-member-function",
        ".cpp",
        rows(
            "class R {",
            "public:",
            "  [[nodiscard]]",
            "  int route() const {",
            "    return 1;",
            "  }",
            "};",
        ),
        "R.route",
        (3, 6),
        prefix=(3,),
        control=(1, 2, 7),
    ),
    Shape(
        "alignas-above-a-struct",
        ".cpp",
        rows("int before = 1;", "alignas(16)", "struct S {", "};", "int after = 2;"),
        "S",
        (2, 4),
        prefix=(2,),
        control=(1, 5),
    ),
    Shape(
        "gnu-attribute-above-a-struct",
        ".h",
        rows("int before = 1;", "__attribute__((packed))", "struct S {", "};", "int after = 2;"),
        "S",
        (2, 4),
        prefix=(2,),
        control=(1, 5),
    ),
    Shape(
        "declspec-above-a-struct",
        ".h",
        rows("int before = 1;", "__declspec(dllexport)", "struct S {", "};", "int after = 2;"),
        "S",
        (2, 4),
        prefix=(2,),
        control=(1, 5),
    ),
    Shape(
        "stacked-attributes",
        ".cpp",
        rows(
            "int before = 1;",
            "[[nodiscard]]",
            'alignas(8) [[deprecated("x")]]',
            "struct S {",
            "};",
            "int after = 2;",
        ),
        "S",
        (2, 5),
        prefix=(2, 3),
        control=(1, 6),
    ),
    Shape(
        "explicit-specialization",
        ".cpp",
        rows("int before = 1;", "template<>", "struct S<int> {", "};", "int after = 2;"),
        "S",
        (2, 4),
        prefix=(2,),
        control=(1, 5),
    ),
    Shape(
        "export-on-its-own-line",
        ".cpp",
        rows("int before = 1;", "export", "struct S {", "};", "int after = 2;"),
        "S",
        (2, 4),
        prefix=(2,),
        control=(1, 5),
    ),
    Shape(
        "inline-and-constexpr-on-their-own-lines",
        ".cpp",
        rows(
            "struct S {",
            "  inline",
            "  constexpr",
            "  int f() const {",
            "    return 1;",
            "  }",
            "};",
        ),
        "S.f",
        (2, 6),
        prefix=(2, 3),
        control=(1, 7),
    ),
    Shape(
        "trailing-requires-clause-at-the-declarators-indent",
        ".cpp",
        rows(
            "template<typename T>",
            "struct Box {",
            "  int get() const",
            "  requires Small<T>",
            "  {",
            "    return 1;",
            "  }",
            "};",
        ),
        "Box.get",
        (3, 7),
        prefix=(4,),
        control=(1, 2, 8),
    ),
    Shape(
        "trailing-requires-clause-indented-deeper",
        ".cpp",
        rows(
            "struct Box {",
            "  int get() const",
            "      requires Small<int> &&",
            "               Other<int>",
            "  {",
            "    return 1;",
            "  }",
            "};",
        ),
        "Box.get",
        (2, 7),
        prefix=(3, 4),
        control=(1, 8),
    ),
    Shape(
        "trailing-noexcept-at-the-declarators-indent",
        ".cpp",
        rows(
            "struct Box {",
            "  int get() const",
            "  noexcept",
            "  {",
            "    return 1;",
            "  }",
            "};",
        ),
        "Box.get",
        (2, 6),
        prefix=(3,),
        control=(1, 7),
    ),
    Shape(
        "trailing-const-at-the-declarators-indent",
        ".cpp",
        rows(
            "struct B {",
            "  int get()",
            "  const",
            "  {",
            "    return 1;",
            "  }",
            "};",
        ),
        "B.get",
        (2, 6),
        prefix=(3,),
        control=(1, 7),
    ),
    Shape(
        "trailing-volatile-at-the-declarators-indent",
        ".cpp",
        rows(
            "struct B {",
            "  int get()",
            "  volatile",
            "  {",
            "    return 1;",
            "  }",
            "};",
        ),
        "B.get",
        (2, 6),
        prefix=(3,),
        control=(1, 7),
    ),
    Shape(
        "trailing-override-at-the-declarators-indent",
        ".cpp",
        rows(
            "struct B {",
            "  int get()",
            "  override",
            "  {",
            "    return 1;",
            "  }",
            "};",
        ),
        "B.get",
        (2, 6),
        prefix=(3,),
        control=(1, 7),
    ),
    Shape(
        "trailing-final-at-the-declarators-indent",
        ".cpp",
        rows(
            "struct B {",
            "  int get()",
            "  final",
            "  {",
            "    return 1;",
            "  }",
            "};",
        ),
        "B.get",
        (2, 6),
        prefix=(3,),
        control=(1, 7),
    ),
    Shape(
        "trailing-throw-at-the-declarators-indent",
        ".cpp",
        rows(
            "struct B {",
            "  int get()",
            "  throw()",
            "  {",
            "    return 1;",
            "  }",
            "};",
        ),
        "B.get",
        (2, 6),
        prefix=(3,),
        control=(1, 7),
    ),
    Shape(
        "macro-after-a-requires-clause-is-read-as-part-of-the-clause",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires Small<T>",
            "DECL_THING(a, b)",
            "struct S {",
            "};",
            "int after = 2;",
        ),
        "S",
        (2, 6),
        prefix=(2, 3, 4),
        control=(1, 7),
    ),
    Shape(
        "template-and-requires-above-a-member-function",
        ".cpp",
        rows(
            "class R {",
            "  template<typename T>",
            "  requires Small<T>",
            "  int f(T x) {",
            "    return 1;",
            "  }",
            "};",
        ),
        "R.f",
        (2, 6),
        prefix=(2, 3),
        control=(1, 7),
    ),
    Shape(
        "doc-comment-between-the-prefix-and-the-declaration",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires Small<T>",
            "/// Router docs.",
            "struct Router {",
            "};",
            "int after = 2;",
        ),
        "Router",
        (2, 6),
        prefix=(2, 3),
        control=(1, 7),
    ),
    Shape(
        "blank-line-inside-the-prefix",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "",
            "requires Small<T>",
            "",
            "struct Router {",
            "};",
            "int after = 2;",
        ),
        "Router",
        (2, 7),
        prefix=(2, 4),
        control=(1, 8),
    ),
    Shape(
        "conditional-compilation-inside-the-prefix-below-a-boundary",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#ifdef A",
            "requires Small<T>",
            "#else",
            "requires Large<T>",
            "#endif",
            "struct S {",
            "};",
            "int after = 2;",
        ),
        "S",
        None,
        prefix=(2, 3, 4, 5, 6, 7),
    ),
    Shape(
        "conditional-compilation-around-an-attribute-below-a-boundary",
        ".cpp",
        rows(
            "int before = 1;",
            "#ifdef A",
            "[[nodiscard]]",
            "struct S {",
            "};",
            "#endif",
            "int after = 2;",
        ),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "macro-detached-by-a-blank-line",
        ".cpp",
        rows("int before = 1;", "EXPORT_API", "", "struct S {", "};", "int after = 2;"),
        "S",
        (4, 5),
        control=(1, 2, 6),
    ),
    Shape(
        "macro-detached-by-a-blank-line-and-a-comment",
        ".cpp",
        rows("EXPORT_API", "", "// Docs.", "struct S {", "};"),
        "S",
        (4, 5),
        control=(1, 3),
    ),
    Shape(
        "trailing-block-comment-on-the-code-line-above",
        ".cpp",
        rows("int before = 1; /* note */", "struct S {", "};", "int after = 2;"),
        "S",
        (2, 3),
        control=(1, 4),
    ),
    Shape(
        "block-comment-opened-on-the-code-line-above",
        ".cpp",
        rows("int before = 1; /* a note", "   that goes on */", "struct S {", "};"),
        "S",
        (3, 4),
        control=(1, 2),
    ),
    Shape(
        "trailing-block-comment-on-a-prefix-line",
        ".cpp",
        rows("int before = 1;", "alignas(16) /* wide */", "struct S {", "};"),
        "S",
        (2, 4),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "block-comment-before-code-on-the-line-above",
        ".cpp",
        rows("/* x */ int before = 1;", "struct S {", "};"),
        "S",
        (2, 3),
        control=(1,),
    ),
    Shape(
        "bare-macro-after-a-statement-detached-by-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "MACRO0",
            "",
            "struct S {",
            "};",
            "int after = 2;",
        ),
        "S",
        (4, 5),
        control=(1, 2, 6),
    ),
    Shape(
        "bare-macro-after-a-block-end-detached-by-a-blank-line",
        ".cpp",
        rows(
            "void g() {",
            "}",
            "MACRO0",
            "",
            "struct S {",
            "};",
        ),
        "S",
        (5, 6),
        control=(1, 2, 3),
    ),
    Shape(
        "bare-macro-after-a-comment-at-the-start-of-the-file-detached-by-a-blank-line",
        ".cpp",
        rows(
            "// Docs.",
            "MACRO0",
            "",
            "struct S {",
            "};",
        ),
        "S",
        (4, 5),
        control=(1, 2),
    ),
    Shape(
        "bare-macro-after-an-include-detached-by-a-blank-line",
        ".cpp",
        rows(
            "#include <vector>",
            "MACRO0",
            "",
            "struct S {",
            "};",
        ),
        "S",
        (4, 5),
        control=(1, 2),
    ),
    Shape(
        "bare-macro-after-an-opening-brace-detached-by-a-blank-line",
        ".cpp",
        rows(
            "namespace ns {",
            "MACRO0",
            "",
            "struct S {",
            "};",
            "}",
        ),
        "ns.S",
        (4, 5),
        control=(1, 2, 6),
    ),
    Shape(
        "conditional-compilation-inside-the-prefix-at-the-start-of-the-file",
        ".cpp",
        rows(
            "#ifdef A",
            "template<typename T>",
            "#ifdef B",
            "requires Small<T>",
            "#else",
            "requires Large<T>",
            "#endif",
            "struct S {",
            "};",
            "#endif",
        ),
        "S",
        (1, 9),
        prefix=(1, 2, 3, 4, 5, 6, 7),
        control=(10,),
    ),
    Shape(
        "include-guard-at-the-start-of-the-file",
        ".cpp",
        rows(
            "#ifndef FOO_H",
            "#define FOO_H",
            "struct S {",
            "};",
            "#endif",
        ),
        "S",
        (3, 4),
        control=(1, 2, 5),
    ),
    Shape(
        "non-conditional-directives-between-a-boundary-and-a-declaration",
        ".cpp",
        rows(
            "int before = 1;",
            "#include <vector>",
            "#define FOO 1",
            "#pragma once",
            "struct S {",
            "};",
        ),
        "S",
        (5, 6),
        control=(1, 2, 3, 4),
    ),
    Shape(
        "conditional-directives-above-the-boundary",
        ".cpp",
        rows(
            "#if A",
            "int x;",
            "struct T {};",
            "#endif",
            "",
            "int y;",
            "struct S {",
            "};",
        ),
        "S",
        (7, 8),
        control=(1, 2, 3, 4, 6),
    ),
    Shape(
        "balanced-macro-call-detached-by-a-blank-line",
        ".cpp",
        rows("int before = 1;", 'FMT_PRAGMA(diagnostic ignored "-Wx")', "", "struct S {", "};"),
        "S",
        (4, 5),
        control=(1, 2),
    ),
    Shape(
        "constrained-function-with-a-body-directly-above",
        ".cpp",
        rows(
            "template<typename T>",
            "requires Small<T>",
            "void f(T) {",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        (5, 6),
        control=(1, 2, 3, 4),
    ),
    Shape(
        "constrained-function-with-a-body-then-a-blank-line",
        ".cpp",
        rows(
            "template<typename T>",
            "requires Small<T>",
            "void f(T) {",
            "}",
            "",
            "struct S {",
            "};",
        ),
        "S",
        (6, 7),
        control=(1, 2, 3, 4),
    ),
    Shape(
        "function-with-wrapped-parameters-and-a-trailing-requires-above",
        ".cpp",
        rows(
            "template<typename T>",
            "void f(T a,",
            "       T b)",
            "  requires Small<T>",
            "{",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        (7, 8),
        control=(1, 2, 3, 4, 5, 6),
    ),
    Shape(
        "member-after-a-member-with-a-trailing-requires-and-a-body",
        ".cpp",
        rows(
            "struct B {",
            "  int a() requires Small<int> {",
            "    return 1;",
            "  }",
            "  int b() {",
            "    return 2;",
            "  }",
            "};",
        ),
        "B.b",
        (5, 7),
        control=(1, 2, 3, 4, 8),
    ),
    Shape(
        "function-body-with-conditional-lines-directly-above",
        ".cpp",
        rows(
            "void f() {",
            "#if 1",
            "  g();",
            "#endif",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        (6, 7),
        control=(1, 2, 3, 4, 5),
    ),
    Shape(
        "directive-that-continues-over-two-lines-directly-above-a-boundary",
        ".cpp",
        rows(
            "int before = 1;",
            "#if defined(A) && \\",
            "    defined(B)",
            "struct S {",
            "};",
            "#endif",
        ),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "macro-definition-with-a-body-directly-above",
        ".cpp",
        rows(
            "#define DECLARE(T) \\",
            "  struct T { \\",
            "    int x; \\",
            "  }",
            "struct S {",
            "};",
        ),
        "S",
        (5, 6),
        control=(4,),  # an appended comment after a backslash would end the macro early
    ),
    Shape(
        "previous-declaration-directly-above",
        ".cpp",
        rows("struct A {", "};", "struct B {", "};"),
        "B",
        (3, 4),
        control=(1, 2),
    ),
    Shape(
        "access-label-directly-above",
        ".cpp",
        rows("class C {", "public:", "  struct In {", "  };", "};"),
        "C.In",
        (3, 4),
        control=(1, 2),
    ),
    Shape(
        "include-directly-above",
        ".cpp",
        rows("#include <vector>", "struct S {", "};"),
        "S",
        (2, 3),
        control=(1,),
    ),
    Shape(
        "namespace-opening-directly-above",
        ".cpp",
        rows("namespace ns {", "struct S {", "};", "}"),
        "ns.S",
        (2, 3),
        control=(1, 4),
    ),
    Shape(
        "a-closed-function-body-directly-above",
        ".cpp",
        rows("void f() {", "  g();", "}", "struct S {", "};"),
        "S",
        (4, 5),
        control=(1, 2, 3),
    ),
]

# Prefixes the scan cannot read with certainty: the citation falls back to the whole file.
CPP_UNCERTAIN = [
    Shape(
        "template-header-that-does-not-balance",
        ".cpp",
        rows("int before = 1;", "template<typename T,", "struct S {", "};"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "requires-clause-that-ends-with-an-and",
        ".cpp",
        rows("int before = 1;", "template<typename T>", "requires Small<T> &&", "struct S {", "};"),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "requires-clause-that-ends-with-an-or",
        ".cpp",
        rows("int before = 1;", "template<typename T>", "requires Small<T> ||", "struct S {", "};"),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "requires-clause-that-ends-with-an-open-parenthesis",
        ".cpp",
        rows("int before = 1;", "template<typename T>", "requires (", "struct S {", "};"),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "requires-clause-that-does-not-balance",
        ".cpp",
        rows("int before = 1;", "template<typename T>", "requires (A<T>", "struct S {", "};"),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "line-above-ends-with-a-comma",
        ".cpp",
        rows("int before = 1;", "template<typename T>", "requires Small<T>,", "struct S {", "};"),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "line-above-ends-with-an-equals-sign",
        ".cpp",
        rows("int before = 1;", "template<typename T>", "requires Small<T> =", "struct S {", "};"),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "requires-expression-with-a-body",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires(T t) {",
            "  t.f();",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4, 5),
    ),
    Shape(
        "requires-expression-on-its-own-lines",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires(T t)",
            "{",
            "  t.f();",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4, 5, 6),
    ),
    Shape(
        "macro-on-its-own-line-above",
        ".cpp",
        rows("int before = 1;", "EXPORT_API", "struct S {", "};"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "requires-with-a-braced-expression-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires (T{} == T{})",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "template-header-with-a-braced-default-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T, T V = T{}>",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "multi-line-template-header-with-a-braced-default-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T,",
            "         T V = T{}>",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "member-function-after-a-braced-template-header-and-a-blank-line",
        ".cpp",
        rows(
            "struct O {",
            "  template<typename T, T V = T{}>",
            "",
            "  int f() const {",
            "    return 1;",
            "  }",
            "};",
        ),
        "O.f",
        None,
        prefix=(2,),
    ),
    Shape(
        "lambda-default-above-a-requires-clause-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<auto F = [](int x) { return x > 1; }>",
            "",
            "requires true",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 4),
    ),
    Shape(
        "macro-call-with-angle-brackets-and-a-blank-line",
        ".cpp",
        rows("int before = 1;", "DECLARE(std::vector<int>)", "", "struct S {", "};"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "macro-call-with-a-logical-operator-and-a-blank-line",
        ".cpp",
        rows("int before = 1;", "CHECK(a && b)", "", "struct S {", "};"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "macro-followed-by-more-text-and-a-blank-line",
        ".cpp",
        rows("int before = 1;", "EXPORT_API int", "", "struct S {", "};"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "requires-expression-with-parameters-wrapped-over-three-lines",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T, typename U, typename V>",
            "requires requires(T t,",
            "                  U u,",
            "                  V v) {",
            "    t.foo(u, v);",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4, 5, 6, 7),
    ),
    Shape(
        "requires-expression-with-two-wrapped-parameters-and-the-brace-on-its-own-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T, typename U>",
            "requires requires(T t,",
            "                  U u)",
            "{",
            "    t.foo(u);",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4, 5, 6, 7),
    ),
    Shape(
        "wrapped-requires-expression-then-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T, typename U>",
            "requires requires(T t,",
            "                  U u)",
            "{",
            "    t.foo(u);",
            "}",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4, 5, 6, 7),
    ),
    Shape(
        "requires-expression-without-parameters-and-the-brace-on-its-own-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires",
            "{",
            "    typename T::x;",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4, 5, 6),
    ),
    Shape(
        "requires-expression-with-a-compound-requirement",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires(T t) {",
            "    { t.f() } -> std::same_as<int>;",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4, 5),
    ),
    Shape(
        "limit-function-with-a-parenthesised-trailing-clause-above-falls-back",
        ".cpp",
        rows(
            "template<typename T>",
            "void f() requires (sizeof(T) > 4) {",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "constraint-tail-on-its-own-line-after-an-unreadable-clause-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires (T{} == T{}) &&",
            "         kFlag",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4),
    ),
    Shape(
        "constraint-or-true-after-an-unreadable-clause-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires (T{} == T{}) ||",
            "         true",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4),
    ),
    Shape(
        "constraint-tail-after-a-requires-expression-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires(T t) { t.f(); } &&",
            "         kFlag",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4),
    ),
    Shape(
        "requires-expression-with-a-body-then-or-true-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires { typename T::x; } ||",
            "         true",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4),
    ),
    Shape(
        "bare-macro-between-the-template-header-and-the-declaration-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "MACRO0",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "bare-macro-between-an-attribute-and-a-member-and-a-blank-line",
        ".cpp",
        rows(
            "struct O {",
            "  [[nodiscard]]",
            "  MACRO0",
            "",
            "  int f() const {",
            "    return 1;",
            "  }",
            "};",
        ),
        "O.f",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "bare-macro-after-a-conditional-above-a-template-header-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#ifdef A",
            "MACRO0",
            "#endif",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 4),
    ),
    Shape(
        "static-on-its-own-line-and-a-blank-line",
        ".cpp",
        rows(
            "struct O {",
            "  static",
            "",
            "  int f() {",
            "    return 1;",
            "  }",
            "};",
        ),
        "O.f",
        None,
        prefix=(2,),
    ),
    Shape(
        "virtual-on-its-own-line-and-a-blank-line",
        ".cpp",
        rows(
            "struct O {",
            "  virtual",
            "",
            "  int f() {",
            "    return 1;",
            "  }",
            "};",
        ),
        "O.f",
        None,
        prefix=(2,),
    ),
    Shape(
        "thread-local-on-its-own-line-and-a-blank-line",
        ".cpp",
        rows(
            "struct O {",
            "  thread_local",
            "",
            "  int f() {",
            "    return 1;",
            "  }",
            "};",
        ),
        "O.f",
        None,
        prefix=(2,),
    ),
    Shape(
        "extern-on-its-own-line-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "extern",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "keyword-after-a-braced-template-header-and-a-blank-line",
        ".cpp",
        rows(
            "struct O {",
            "  template<typename T, T V = T{}>",
            "  static",
            "",
            "  int f() {",
            "    return 1;",
            "  }",
            "};",
        ),
        "O.f",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "two-bare-macros-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "FMT_BEGIN_NAMESPACE",
            "FMT_BEGIN_EXPORT",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "bare-macro-after-an-open-statement-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "int total =",
            "  MACRO0",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "directive-between-the-requires-parameters-and-the-brace",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires(T t)",
            "#if 1",
            "#endif",
            "{",
            "  t.f();",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 6, 7, 8),
    ),
    Shape(
        "directive-between-requires-and-its-parameters",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires",
            "#if 1",
            "#endif",
            "(T t) {",
            "  t.f();",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 6, 7, 8),
    ),
    Shape(
        "continued-directive-between-the-requires-parameters-and-the-brace",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires(T t)",
            "#if defined(A) && \\",
            "    defined(B)",
            "{",
            "  t.f();",
            "}",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 6, 7, 8),
    ),
    Shape(
        "inactive-if-0-branch-with-a-statement-above-a-template-header",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#if 0",
            "int dead;",
            "#endif",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "inactive-ifdef-branch-with-a-statement-and-a-macro-else",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#ifdef NOPE",
            "int dead;",
            "#else",
            "MACRO0",
            "#endif",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "inactive-ifdef-branch-with-a-function-and-a-macro-else",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#ifdef NOPE",
            "void g() {}",
            "#else",
            "MACRO0",
            "#endif",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "else-branch-with-a-different-template",
        ".cpp",
        rows(
            "int before = 1;",
            "#if 1",
            "template<typename T>",
            "#else",
            "template<typename T, typename U>",
            "#endif",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4, 5),
    ),
    Shape(
        "comment-ending-in-a-backslash-splices-a-statement-above-a-template-header",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "// note \\",
            "int dead;",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "requires-expression-with-alternative-bodies",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires(T t)",
            "#if 1",
            "{ t.f(); }",
            "#else",
            "{ t.g(); }",
            "#endif",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 5, 7),
    ),
    Shape(
        "conditional-directive-directly-above-a-prefix-less-declaration",
        ".cpp",
        rows(
            "int before = 1;",
            "#ifdef A",
            "struct S {",
            "};",
            "#endif",
        ),
        "S",
        None,
    ),
    Shape(
        "endif-directly-above-a-prefix-less-declaration",
        ".cpp",
        rows(
            "int before = 1;",
            "#endif",
            "struct S {",
            "};",
        ),
        "S",
        None,
    ),
    Shape(
        "continued-code-line-above-a-declaration",
        ".cpp",
        rows(
            "int before = 1;",
            "int a = \\",
            "  2;",
            "struct S {",
            "};",
        ),
        "S",
        None,  # a backslash on a line above the boundary may join prefix text into it
    ),
    Shape(
        "continued-define-between-a-boundary-and-a-declaration",
        ".cpp",
        rows(
            "int before = 1;",
            "#define FOO \\",
            "  1",
            "struct S {",
            "};",
        ),
        "S",
        (4, 5),  # a `#define` continuation holds a macro body, never code
        control=(1, 3),
    ),
    Shape(
        "bare-macro-below-a-spliced-statement-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "// note \\",
            "int dead;",
            "MACRO0",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "bare-macro-below-an-inactive-branch-and-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#if 0",
            "int dead;",
            "#endif",
            "EXPORT_API",
            "",
            "requires Small<T>",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 8),
    ),
    Shape(
        "macro-above-a-comment-above-the-declaration",
        ".cpp",
        rows("int before = 1;", "EXPORT_API", "// Docs.", "struct S {", "};"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "requires-expression-detached-by-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires(T t) {",
            "  t.f();",
            "}",
            "",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 3, 4, 5),
    ),
    Shape(
        "string-that-does-not-close-on-the-line-above",
        ".cpp",
        rows("int before = 1;", 'const char* s = "a \\', 'b";', "struct S {", "};"),
        "S",
        None,
        prefix=(3,),
    ),
    Shape(
        "block-comment-after-an-unknown-macro-above",
        ".cpp",
        rows("int before = 1;", "EXPORT_API /* shared */", "struct S {", "};"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "macro-call-without-a-semicolon-above",
        ".cpp",
        rows("int before = 1;", "DECLARE_THING(a, b)", "struct S {", "};"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "qualifier-the-scan-does-not-know-above-a-member",
        ".cpp",
        rows("struct S {", "  static", "  int f() {", "    return 1;", "  }", "};"),
        "S.f",
        None,
        prefix=(2,),
    ),
    Shape(
        "unknown-attribute-keyword-above",
        ".cpp",
        rows("int before = 1;", "__forceinline", "struct S {", "};"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "requires-expression-braces-inside-a-conditional",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#ifdef A",
            "requires requires { T::x; }",
            "#endif",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2, 4),
    ),
]

# The other declaration kinds R2 names are not isolated by the scan, so they always fall back.
CPP_NOT_ISOLATED = [
    Shape(
        "free-function",
        ".cpp",
        rows("int before = 1;", "[[nodiscard]]", "int foo(int x) {", "  return x;", "}"),
        "foo",
        None,
        prefix=(2,),
    ),
    Shape(
        "using-alias",
        ".hpp",
        rows("int before = 1;", "template<typename T>", "using Vec = std::vector<T>;"),
        "Vec",
        None,
        prefix=(2,),
    ),
    Shape(
        "typedef",
        ".h",
        rows("int before = 1;", "typedef struct {", "  int x;", "} Point;"),
        "Point",
        None,
        prefix=(2,),
    ),
    Shape(
        "union",
        ".cpp",
        rows("int before = 1;", "alignas(8)", "union U {", "  int x;", "};"),
        "U",
        None,
        prefix=(2,),
    ),
    Shape(
        "concept",
        ".hpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "concept C = requires(T t) {",
            "  t.f();",
            "};",
        ),
        "C",
        None,
        prefix=(2,),
    ),
]

# A conditional directive inside a declaration's braces: the branch the compiler skips can hold a
# brace the scan counts, so the end it finds is not the end of the declaration. The scan does not
# evaluate the preprocessor, so these fall back to the whole file.
CPP_BRACE_RANGE = [
    Shape(
        "dead-branch-brace-pair-inside-a-struct-body",
        ".cpp",
        rows(
            "int before = 1;",
            "struct Zq1 {",
            "  int a;",
            "#ifdef NOPE",
            "};",
            "struct Dead {",
            "#endif",
            "  int b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(8,),
    ),
    Shape(
        "dead-branch-brace-pair-inside-a-member-function-body",
        ".cpp",
        rows(
            "struct S {",
            "  int f() {",
            "#if 0",
            "  }",
            "  struct D {",
            "#endif",
            "    return 1;",
            "  }",
            "  int g() { return 2; }",
            "};",
        ),
        "S.f",
        None,
        prefix=(7,),
    ),
    Shape(
        "member-below-a-dead-branch-brace-pair",
        ".cpp",
        rows(
            "struct Oq1 {",
            "int x;",
            "#if 0",
            "}",
            "struct D {",
            "#endif",
            "int fq1() { return 1; }",
            "};",
        ),
        "Oq1.fq1",
        None,
        prefix=(7,),
    ),
    Shape(
        "conditional-directive-inside-a-struct-body",
        ".cpp",
        rows("struct S {", "#if 1", "  int a;", "#endif", "  int b;", "};"),
        "S",
        None,
        prefix=(5,),
    ),
    Shape(
        "conditional-directive-inside-the-container-of-a-member",
        ".cpp",
        rows("struct S {", "#if 1", "  int a;", "#endif", "  int f() { return 1; }", "};"),
        "S.f",
        None,
        prefix=(3, 5),
    ),
    Shape(
        "conditional-directive-between-the-parameters",
        ".cpp",
        rows(
            "struct S {",
            "  int f(int a,",
            "#ifdef A",
            "        int b,",
            "#endif",
            "        int c) {",
            "    return a;",
            "  }",
            "};",
        ),
        "S.f",
        None,
        prefix=(7,),
    ),
    Shape(
        "directive-between-the-declarator-and-the-body",
        ".cpp",
        rows(
            "struct Base {};",
            "struct Zq1",
            "#ifdef NOPE",
            "  : Missing",
            "#endif",
            "{",
            "  int a;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(7,),
    ),
    Shape(
        "pragma-between-the-declarator-and-the-body",
        ".cpp",
        rows("struct Zq1", "#pragma foo", "{", "  int a;", "};"),
        "Zq1",
        None,
        prefix=(4,),
    ),
    Shape(
        "conditional-directive-after-the-declaration",
        ".cpp",
        rows("struct S {", "};", "#if A", "int z;", "#endif"),
        "S",
        (1, 2),
        control=(3, 4, 5),
    ),
    Shape(
        "pragma-inside-a-struct-body",
        ".cpp",
        rows(
            "struct S {",
            "#pragma pack(push, 1)",
            "  int a;",
            "#pragma pack(pop)",
            "};",
            "int after = 2;",
        ),
        "S",
        (1, 5),
        control=(6,),
    ),
    Shape(
        "comment-that-reads-like-a-directive-inside-a-struct-body",
        ".cpp",
        rows("struct S {", "  int a;  // #if 0", "// #endif", "};", "int after = 2;"),
        "S",
        (1, 4),
        control=(5,),
    ),
]

OTHER = [
    Shape(
        "rust-multi-line-attribute",
        ".rs",
        rows(
            "const BEFORE: u8 = 1;",
            "#[cfg(any(",
            '    feature = "a",',
            '    feature = "b"',
            "))]",
            "struct S {",
            "    a: u8,",
            "}",
            "const AFTER: u8 = 2;",
        ),
        "S",
        (2, 8),
        prefix=(2, 3, 4, 5),
        control=(1, 9),
    ),
    Shape(
        "rust-single-line-attributes-with-a-doc-comment",
        ".rs",
        rows(
            "const BEFORE: u8 = 1;",
            "#[derive(Debug)]",
            "/// Docs.",
            "#[repr(C)]",
            "struct S {",
            "}",
            "const AFTER: u8 = 2;",
        ),
        "S",
        (2, 6),
        prefix=(2, 4),
        control=(1, 7),
    ),
    Shape(
        "rust-attribute-that-does-not-balance",
        ".rs",
        rows("const BEFORE: u8 = 1;", "#[derive(Debug,", "struct S {", "}"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "java-multi-line-annotation-above-a-member",
        ".java",
        rows(
            "class C {",
            "  @Test",
            '  @DisplayName("a" +',
            '          "b")',
            "  void run() {",
            "  }",
            "}",
        ),
        "C.run",
        (2, 6),
        prefix=(2, 3, 4),
        control=(1, 7),
    ),
    Shape(
        "java-annotation-with-a-named-argument-on-a-second-line",
        ".java",
        rows(
            "class R {",
            '  @Query(value = "x",',
            "      nativeQuery = true)",
            "  void find() {",
            "  }",
            "}",
        ),
        "R.find",
        (2, 5),
        prefix=(2, 3),
        control=(1, 6),
    ),
    Shape(
        "java-annotation-with-a-block-comment-inside",
        ".java",
        rows("class C {", "  @Foo(/* why */", "      1)", "  void run() {", "  }", "}"),
        "C.run",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "kotlin-multi-line-annotation-with-a-trailing-comma",
        ".kt",
        rows("val before = 1", "@Foo(", "    a = 1,", ")", "class X {", "}", "val after = 2"),
        "X",
        (2, 6),
        prefix=(2, 3, 4),
        control=(1, 7),
    ),
    Shape(
        "typescript-multi-line-decorator",
        ".ts",
        rows(
            "export const before = 1;",
            "@Component({",
            "  selector: 'x',",
            "})",
            "export class Foo {",
            "  bar() {",
            "    return 1;",
            "  }",
            "}",
            "export const after = 2;",
        ),
        "Foo",
        (2, 9),
        prefix=(2, 3, 4),
        control=(1, 10),
    ),
    Shape(
        "typescript-multi-line-decorator-on-a-method",
        ".ts",
        rows(
            "export class Foo {",
            "  @HostListener('window:resize', [",
            "    '$event',",
            "  ])",
            "  onResize() {",
            "  }",
            "  other() {",
            "  }",
            "}",
        ),
        "Foo.onResize",
        (2, 6),
        prefix=(2, 3, 4),
        control=(1, 7, 9),
    ),
    Shape(
        "typescript-decorated-class-with-an-undecorated-member",
        ".ts",
        rows(
            "@Component({",
            "  selector: 'x',",
            "})",
            "export class Foo {",
            "  bar() {",
            "    return 1;",
            "  }",
            "}",
        ),
        "Foo.bar",
        (5, 7),
        control=(1, 2, 3, 4, 8),
    ),
    Shape(
        "swift-multi-line-attribute",
        ".swift",
        rows(
            "let before = 1",
            "@available(iOS 13,",
            "           macOS 10.15, *)",
            "class X {",
            "}",
            "let after = 2",
        ),
        "X",
        (2, 5),
        prefix=(2, 3),
        control=(1, 6),
    ),
    Shape(
        "csharp-single-line-attribute",
        ".cs",
        rows("using System;", "[Serializable]", "public class Foo", "{", "    int x;", "}"),
        "Foo",
        (2, 6),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "csharp-multi-line-attribute",
        ".cs",
        rows("using System;", "[Foo(1,", "     2)]", "public class X", "{", "    int x;", "}"),
        "X",
        (2, 7),
        prefix=(2, 3),
        control=(1,),
    ),
    Shape(
        "csharp-attribute-inside-conditional-compilation",
        ".cs",
        rows("using System;", "#if DEBUG", "[Foo]", "#endif", "public class X", "{", "}"),
        "X",
        (2, 7),
        prefix=(2, 3, 4),
        control=(1,),
    ),
    Shape(
        "swift-attribute-inside-conditional-compilation",
        ".swift",
        rows(
            "let before = 1",
            "#if os(macOS)",
            "@available(macOS 11, *)",
            "#endif",
            "class X {",
            "}",
        ),
        "X",
        (2, 6),
        prefix=(2, 3, 4),
        control=(1,),
    ),
    Shape(
        "typescript-private-field-named-like-a-directive",
        ".ts",
        rows(
            "class A {",
            "  @Input()",
            "  #line = 1;",
            "  run() {",
            "  }",
            "}",
        ),
        "A.run",
        (4, 5),
        control=(1, 2, 3, 6),
    ),
    Shape(
        "javascript-private-field-named-if",
        ".js",
        rows(
            "class A {",
            "  @Input()",
            "  #if = 1;",
            "  run() {",
            "  }",
            "}",
        ),
        "A.run",
        (4, 5),
        control=(1, 2, 3, 6),
    ),
    Shape(
        "typescript-wrapped-call-on-an-array-above-a-class",
        ".ts",
        rows("[1, 2].forEach((x) =>", "  x)", "class A {", "}"),
        "A",
        None,
        prefix=(1, 2),
    ),
    Shape(
        "kotlin-declaration-that-starts-like-an-annotation-above",
        ".kt",
        rows("@Foo(a,", "     b) val x = foo(", "    1)", "class A {", "}"),
        "A",
        None,
        prefix=(1, 2, 3),
    ),
    Shape(
        "java-two-annotations-on-one-line-the-second-wrapped",
        ".java",
        rows("@Bar @Baz(", "    1)", "class A {", "}"),
        "A",
        (1, 4),
        prefix=(1, 2),
    ),
    Shape(
        "typescript-dotted-decorator-wrapped",
        ".ts",
        rows("export const before = 1;", "@Foo.bar(", "  1,", ")", "class A {", "}"),
        "A",
        (2, 6),
        prefix=(2, 3, 4),
        control=(1,),
    ),
    Shape(
        "kotlin-use-site-annotation-wrapped-above-a-property",
        ".kt",
        rows("val before = 1", "@get:JvmName(", '    "x")', "val y = 1", "val after = 2"),
        "y",
        (2, 4),
        prefix=(2, 3),
        control=(1, 5),
    ),
    Shape(
        "typescript-decorator-with-type-arguments-wrapped",
        ".ts",
        rows(
            "export const before = 1;",
            "@Foo<Bar>({",
            "  a: 1,",
            "})",
            "class A {",
            "}",
        ),
        "A",
        (2, 6),
        prefix=(2, 3, 4),
        control=(1,),
    ),
    Shape(
        "typescript-decorator-with-nested-type-arguments-wrapped",
        ".ts",
        rows(
            "export const before = 1;",
            "@Foo<Map<string, number>>(",
            "  1,",
            ")",
            "class A {",
            "}",
        ),
        "A",
        (2, 6),
        prefix=(2, 3, 4),
        control=(1,),
    ),
    Shape(
        "typescript-decorator-with-type-arguments-then-more-code-above",
        ".ts",
        rows(
            "@Foo<Bar>(a,",
            "          b) x(",
            "  1)",
            "class A {",
            "}",
        ),
        "A",
        None,
        prefix=(1, 2, 3),
    ),
    Shape(
        "csharp-attribute-after-a-wrapped-call",
        ".cs",
        rows("Setup(", "    1)", "public class X", "{", "}"),
        "X",
        None,
        prefix=(2,),
    ),
]


# A quote the directive never closes: the compiler's token runs to the end of the line, and a `/*`
# after it is no comment. The scan's lexer opens a block comment there and hides the lines below.
CPP_DIRECTIVE_QUOTES = [
    Shape(
        "unterminated-apostrophe-then-a-comment-opener-in-a-define",
        ".cpp",
        rows(
            "int before = 1;",
            "#define X '/*",
            "template<typename T>",
            "#define Y */",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        None,
        prefix=(3,),
    ),
    Shape(
        "unterminated-double-quote-then-a-comment-opener-in-a-define",
        ".cpp",
        rows(
            "int before = 1;",
            '#define X "/*',
            "template<typename T>",
            "#define Y */",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        None,
        prefix=(3,),
    ),
    Shape(
        "unterminated-apostrophe-in-a-warning-directive",
        ".h",
        rows(
            "int before = 1;",
            "#warning it's /* broken",
            "template<typename T>",
            "#warning done */",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        None,
        prefix=(3,),
    ),
    Shape(
        "unterminated-quote-on-the-continuation-of-a-define",
        ".cpp",
        rows(
            "int before = 1;",
            "#define X \\",
            "  '/*",
            "template<typename T>",
            "#define Y */",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        None,
        prefix=(4,),
    ),
    Shape(
        "unterminated-apostrophe-in-a-directive-far-above",
        ".cpp",
        rows("#warning don't do that", "int before = 1;", "struct S {", "};"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "quotes-that-close-in-directives",
        ".cpp",
        rows(
            "#include <vector>",
            '#define STR "a /* b"',
            "#define CH '/'",
            '#warning "no /* good"',
            "#define BIG 1'000'000",
            "struct S {",
            "};",
        ),
        "S",
        (6, 7),
        control=(1, 2, 3, 4, 5),
    ),
    Shape(
        "apostrophe-in-a-comment-after-a-directive",
        ".cpp",
        rows(
            "int before = 1;",
            "#define X 1 // don't",
            "#undef X // it's /* done",
            "template<typename T>",
            "struct S {",
            "};",
        ),
        "S",
        (4, 6),
        prefix=(4,),
        control=(1, 2, 3),
    ),
    Shape(
        "apostrophe-in-a-line-comment-above-a-declaration",
        ".cpp",
        rows("int before = 1;", "// don't /* do this", "template<typename T>", "struct S {", "};"),
        "S",
        (3, 5),
        prefix=(3,),
        control=(1, 2),
    ),
    Shape(
        "directive-inside-a-block-comment-with-a-lone-quote",
        ".cpp",
        rows("int before = 1;", "/*", "#define X '", "*/", "struct S {", "};"),
        "S",
        (5, 6),
        control=(1,),
    ),
]

# Backslashes that hide nothing: a `#define` continuation (its lines are one preprocessor line).
# Any other backslash between the boundary and a declaration still refuses: it may splice a
# comment, or join prefix text into the line the scan took for the boundary.
CPP_CONTINUATIONS = [
    Shape(
        "define-continued-over-several-lines-below-a-boundary",
        ".cpp",
        rows(
            "int before = 1;",
            "#define DECLARE(T) \\",
            "  struct T { \\",
            "    int x; \\",
            "  }",
            "struct S {",
            "};",
        ),
        "S",
        (6, 7),
        control=(1, 5),
    ),
    Shape(
        "define-continued-below-a-template-header",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#define FOO \\",
            "  1",
            "requires Small<T>",
            "struct S {",
            "};",
        ),
        "S",
        (2, 7),
        prefix=(2, 5),
        control=(1,),
    ),
    Shape(
        "define-continued-with-a-blank-line-ending-it",
        ".cpp",
        rows("int before = 1;", "#define X \\", "  1 \\", "", "struct S {", "};"),
        "S",
        (5, 6),
        control=(1, 3, 4),
    ),
    Shape(
        "define-continued-with-a-space-after-the-backslash",
        ".cpp",
        rows("int before = 1;", "#define FOO \\ ", "  1", "struct S {", "};"),
        "S",
        (4, 5),
        control=(1, 3),
    ),
    Shape(
        "typedef-continued-over-two-lines-above-a-declaration",
        ".c",
        rows("int before = 1;", "typedef struct x \\", "  *x_t;", "struct S {", "};"),
        "S",
        None,  # a backslash on a line above the boundary may join prefix text into it
    ),
    Shape(
        "requires-expression-whose-body-follows-a-splice",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires requires (T t) \\",
            "{ t; }",
            "struct Zq1 {",
            "  T t;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "requires-keyword-split-by-a-splice",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "requires req\\",
            "uires { T{}; }",
            "struct Zq1 {",
            "  T t;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "define-whose-last-line-continues-into-the-declaration",
        ".cpp",
        rows(
            "int before = 1;",
            "#define DECLARE \\",
            "struct S { \\",
            "  int a; \\",
            "}",
        ),
        "S",
        None,
    ),
    Shape(
        "define-with-a-comment-that-ends-in-a-backslash",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#define X 1 // note \\",
            "int dead;",
            "struct S {",
            "};",
        ),
        "S",
        (2, 6),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "define-with-a-slash-in-front-of-the-backslash",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#define X 1 /\\",
            "/ note",
            "struct S {",
            "};",
        ),
        "S",
        (2, 6),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "define-with-a-block-comment-that-holds-the-backslash",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#define X 1 /* note \\",
            "int dead; */",
            "struct S {",
            "};",
        ),
        "S",
        (2, 6),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "define-continuation-that-looks-like-a-conditional",
        ".cpp",
        rows("int before = 1;", "#define X \\", "#if 0", "struct S {", "};"),
        "S",
        (4, 5),  # `#if 0` is the text of the macro, not a directive
        control=(1,),
    ),
    Shape(
        "pragma-continued-over-two-lines-above-a-declaration",
        ".cpp",
        rows("int before = 1;", "#pragma pack(push, \\", "  1)", "struct S {", "};"),
        "S",
        (4, 5),
        control=(1,),
    ),
    Shape(
        "template-header-spliced-onto-the-declaration",
        ".cpp",
        rows("int before = 1;", "template<typename T> \\", "struct S {", "};"),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "continued-statement-whose-first-line-ends-in-a-comment",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "int a = 1; // note \\",
            "  int b;",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "continued-statement-with-a-slash-before-the-backslash",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "int a = 8 /\\",
            "  + 2;",
            "struct S {",
            "};",
        ),
        "S",
        None,
        prefix=(2,),
    ),
    Shape(
        "continued-statement-with-a-string-that-holds-the-backslash",
        ".cpp",
        rows("int before = 1;", 'const char* s = "a \\', 'b";', "struct S {", "};"),
        "S",
        None,
        prefix=(3,),
    ),
]

# A block comment that a directive opens ends the directive on the line where it closes, so what
# follows the `*/` there is part of the directive (a macro body), and a splice can make a comment
# delimiter out of `/` and `*` on two lines. When the scan read physical lines it took that text
# for code and the line for the boundary, and cut a live `template<...>` header out of the span.
# It now reads the lines as the compiler does: the directive is not code, the header belongs to
# the declaration.
CPP_COMMENT_TAILS = [
    Shape(
        "define-whose-comment-closes-on-a-later-line-with-code-after-it",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#define X 1 /* c",
            "*/ int dead;",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        (2, 6),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "pragma-whose-comment-closes-on-a-later-line-with-code-after-it",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#pragma pack(push, 1) /* c",
            "*/ int dead;",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        (2, 6),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "define-with-a-continued-line-whose-comment-closes-with-code-after-it",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#define X \\",
            "  1 /* c",
            "*/ int dead;",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        (2, 7),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "define-whose-comment-opener-is-split-by-a-backslash",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#define X 1 /\\",
            "* c",
            "*/ int dead;",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        (2, 7),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "define-whose-comment-opener-is-split-over-three-lines",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#define X 1 /\\",
            "\\",
            "* c",
            "*/ int dead;",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        (2, 8),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "line-comment-opener-split-by-a-backslash",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#define X 1 /\\",
            "/ note",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        (2, 6),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "comment-closer-split-by-a-backslash",
        ".cpp",
        rows(
            "int before = 1;",
            "/* c *\\",
            "/ template<typename T>",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        None,
        prefix=(3,),
    ),
    Shape(
        "define-whose-comment-closes-on-a-later-line-with-nothing-after-it",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#define X 1 /* c",
            "*/",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        (2, 6),
        prefix=(2,),
        control=(1,),
    ),
    Shape(
        "define-with-a-comment-on-one-line",
        ".cpp",
        rows("int before = 1;", "#define X 1 /* c */", "struct Zq1 {", "};"),
        "Zq1",
        (3, 4),
        control=(1, 2),
    ),
    Shape(
        "comment-on-a-code-line-closes-with-code-after-it",
        ".cpp",
        rows("int before = 1;", "/* c", "*/ int x;", "struct Zq1 {", "};"),
        "Zq1",
        (4, 5),
        control=(1, 3),
    ),
    Shape(
        "backslash-after-a-slash-before-a-line-that-starts-with-a-space",
        ".cpp",
        rows("int before = 1;", "#define X 1 /\\", "  + 2", "struct Zq1 {", "};"),
        "Zq1",
        (4, 5),
        control=(1, 3),
    ),
]

# A `//` comment that ends in a backslash swallows the next line, so a brace or the `{` of a body
# can hide in it. The scan read that line as code: it ended the declaration at the hidden brace, or
# took the declaration for complete before a `{` that follows the comment.
CPP_SPLICED_COMMENTS = [
    Shape(
        "comment-ending-in-a-backslash-between-a-declarator-and-its-brace",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "struct Zq1",
            "// note \\",
            "int dead;",
            "{",
            "  int a0;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(2, 7),
    ),
    Shape(
        "comment-ending-in-a-backslash-hides-a-requires-clause-below-a-declarator",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "struct Zq1",
            "/// note \\",
            "requires Small<T>",
            "{",
            "  int a0;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(2, 7),
    ),
    Shape(
        "comment-ending-in-a-backslash-hides-a-closing-brace-in-a-body",
        ".cpp",
        rows(
            "struct Zq1 {",
            "  int a;  // note \\",
            "};",
            "int b;",
            "// note \\",
            "struct Dead {",
            "};",
        ),
        "Zq1",
        None,
        prefix=(4,),
    ),
    Shape(
        "comment-ending-in-a-backslash-after-a-declaration",
        ".cpp",
        rows("struct S {", "};", "// note \\", "int dead;"),
        "S",
        None,  # a splice outside a preprocessor line refuses the whole file
    ),
    Shape(
        "define-continued-inside-a-struct-body",
        ".cpp",
        rows("struct S {", "#define X \\", "  1", "};"),
        "S",
        (1, 4),
    ),
    Shape(
        "brace-on-its-own-line-after-a-plain-comment",
        ".cpp",
        rows("template<typename T>", "struct Zq1", "// note", "{", "  int a0;", "};"),
        "Zq1",
        (1, 6),
        prefix=(1, 5),
    ),
]

# Text that the compiler lexes as a token but the scan lexes as a comment opener: an unterminated
# quote followed by `/*` on its line (the token runs to the end of the line, in a skipped branch
# or a directive), and the lines of a raw string. The scan's block comment then hides the lines
# below it, a `template<...>` header included, until some later `*/`.
CPP_PHANTOM_COMMENTS = [
    Shape(
        "apostrophe-and-a-comment-opener-in-a-skipped-branch-before-a-define-tail",
        ".cpp",
        rows(
            "using Int = int;",
            "template<typename T>",
            "#if 0",
            "say it's /* x",
            "#endif",
            "requires Small<T>",
            "#define XX 1 /* c",
            "*/ int dead;",
            "struct Zq1 final {",
            "  int a0;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(2, 6),
    ),
    Shape(
        "double-quote-and-a-comment-opener-in-a-skipped-branch-before-a-define-tail",
        ".cpp",
        rows(
            "using Int = int;",
            "template<typename T>",
            "#if 0",
            'say "hi /* x',
            "#endif",
            "requires Small<T>",
            "#define XX 1 /* c",
            "*/ int dead;",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        None,
        prefix=(2, 6),
    ),
    Shape(
        "raw-string-over-several-lines-with-a-comment-opener-before-a-define-tail",
        ".cpp",
        rows(
            "int before = 1;",
            'const char* r = R"(',
            "/* never closed",
            ')";',
            "template<typename T>",
            "#define XX 1 /* c",
            "*/ int dead;",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        None,
        prefix=(5,),
    ),
    Shape(
        "raw-string-with-a-delimiter-over-several-lines",
        ".cpp",
        rows(
            "int before = 1;",
            'const char* r = R"x(',
            "int dead;",
            ')x";',
            "template<typename T>",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        None,
        prefix=(5,),
    ),
    Shape(
        "raw-string-on-one-line-holding-a-comment-opener",
        ".cpp",
        rows(
            "int before = 1;",
            'const char* r = R"(a /* b)";',
            "template<typename T>",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        (3, 5),
        prefix=(3,),
        control=(1, 2),
    ),
    Shape(
        "apostrophe-in-a-skipped-branch-above-the-boundary",
        ".cpp",
        rows("#if 0", "it's dead", "#endif", "int before = 1;", "struct S {", "};"),
        "S",
        (5, 6),
        control=(1, 2, 3, 4),
    ),
    Shape(
        "string-that-closes-before-a-comment-opener",
        ".cpp",
        rows("int before = 1;", 'const char* s = "a"; /* c */', "struct S {", "};"),
        "S",
        (3, 4),
        control=(1, 2),
    ),
]

# The compiler joins a line that ends in a backslash with the next before it reads a token, and a
# preprocessor line is the whole joined line. The scan reads C and C++ that way too: a directive
# spelled over a splice is a directive, and the lines a splice joins to a directive are never code.
CPP_LOGICAL_LINES = [
    Shape(
        "conditional-directive-spelled-across-a-splice-inside-a-struct-body",
        ".cpp",
        rows(
            "int before = 1;",
            "struct Zq1 {",
            "  int a;",
            "#\\",
            "if 0",
            "};",
            "struct Dead {",
            "#\\",
            "endif",
            "  int b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(10,),
    ),
    Shape(
        "conditional-keyword-split-by-a-splice-inside-a-struct-body",
        ".cpp",
        rows(
            "int before = 1;",
            "struct Zq1 {",
            "  int a;",
            "#i\\",
            "f 0",
            "};",
            "struct Dead {",
            "#end\\",
            "if",
            "  int b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(10,),
    ),
    Shape(
        "conditional-after-a-comment-and-a-splice-inside-a-struct-body",
        ".cpp",
        rows(
            "int before = 1;",
            "struct Zq1 {",
            "  int a;",
            "/* c */\\",
            " #if 0",
            "};",
            "struct Dead {",
            "#endif",
            "  int b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(9,),
    ),
    Shape(
        "conditional-directive-spelled-across-a-splice-above-a-template-header",
        ".cpp",
        rows(
            "int before = 1;",
            "#\\",
            "if 0",
            "int dead;",
            "#\\",
            "endif",
            "template<typename T>",
            "struct Zq1 {",
            "};",
        ),
        "Zq1",
        None,
        prefix=(7,),
    ),
    Shape(
        "define-continuation-that-starts-with-a-closing-brace-in-a-flush-left-body",
        ".cpp",
        rows(
            "int before = 1;",
            "struct Zq1 {",
            "int a;",
            "#define CLOSE \\",
            "};",
            "int b;",
            "#define OPEN {",
            "};",
        ),
        "Zq1",
        None,  # read as macro text, but the macros' braces do not balance
        prefix=(6,),
    ),
    Shape(
        "define-whose-comment-carries-it-onto-a-closing-brace-in-a-flush-left-body",
        ".cpp",
        rows(
            "int before = 1;",
            "struct Zq1 {",
            "int a;",
            "#define CLOSE /* c",
            "*/ };",
            "int b;",
            "#define OPEN {",
            "};",
        ),
        "Zq1",
        None,  # a block comment over lines inside a declaration always refuses
        prefix=(6,),
    ),
    Shape(
        "define-continued-over-a-member-declaration",
        ".cpp",
        rows(
            "struct Oq1 {",
            "#define M \\",
            "  int fq1() { return 1; }",
            "  int fq1() const;",
            "};",
        ),
        "Oq1.fq1",
        (4, 4),
        prefix=(4,),
        control=(3,),
    ),
    Shape(
        "byte-order-mark-before-a-directive-on-the-first-line",
        ".cpp",
        rows("\ufeff#define X 1", "struct S {", "};"),
        "S",
        (2, 3),
        control=(1,),
    ),
]

# A splice outside a preprocessor line refuses the whole file. The scan reads such a line as the
# compiler does, but the rest of what it checks (indentation, blank lines, the shape of a span) is
# about physical lines, and splices in code are rare: a backslash alone between a declarator and
# its brace, a keyword or a name split over lines, a char literal spliced onto a brace, a comment
# that ends in a backslash.
CPP_SPLICED_CODE = [
    Shape(
        "lone-backslash-line-between-a-declarator-and-its-brace",
        ".cpp",
        rows("struct Zq1", "\\", "{", "  int a;", "};"),
        "Zq1",
        None,
        prefix=(4,),
    ),
    Shape(
        "lone-backslash-line-between-a-container-and-its-brace",
        ".cpp",
        rows("struct Oq1", "\\", "{", "  int fq1() { return 1; }", "};"),
        "Oq1.fq1",
        None,
        prefix=(4,),
    ),
    Shape(
        "trailing-clause-keyword-split-by-a-splice",
        ".cpp",
        rows("struct Oq1 {", "  int fq1() con\\", "st", "  { return 1; }", "};"),
        "Oq1.fq1",
        None,
        prefix=(4,),
    ),
    Shape(
        "char-literals-spliced-onto-braces-in-a-flush-left-body",
        ".cpp",
        rows(
            "struct Zq1 {",
            "char c = '\\",
            "}';",
            "int a;",
            "char d = '\\",
            "{';",
            "int b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(7,),
    ),
    Shape(
        "member-name-split-by-two-splices",
        ".cpp",
        rows("struct Oq1 {", "  class Z\\", "\\", "q1", "  {", "    int a;", "  };", "};"),
        "Oq1.Zq1",
        None,
        prefix=(6,),
    ),
    Shape(
        "block-comment-line-that-ends-in-a-backslash",
        ".cpp",
        rows("struct S {", "};", "/* a path C:\\dir\\", "   more */", "int after = 2;"),
        "S",
        None,
    ),
    Shape(
        "continued-statement-far-above-the-declaration",
        ".cpp",
        rows("int a = \\", "  1;", "int b = 2;", "struct S {", "};"),
        "S",
        None,
    ),
]

# A macro whose replacement text has an unbalanced `{` or `}` can open or close a declaration
# where it is used, and the scan does not expand macros, so a C or C++ file that defines one never
# isolates. Braces that balance, or that sit in a string or a comment, do not count.
CPP_MACRO_BRACES = [
    Shape(
        "macros-that-expand-to-the-braces-of-a-struct-body",
        ".cpp",
        rows(
            "#define BEGIN {",
            "#define END }",
            "struct Zq1",
            "BEGIN",
            "int a;",
            "int b;",
            "END;",
        ),
        "Zq1",
        None,
        prefix=(5, 6),
    ),
    Shape(
        "macro-that-closes-a-struct-and-opens-another",
        ".cpp",
        rows(
            "int before = 1;",
            "#define X }; struct D {",
            "struct Zq1 {",
            "  int a;",
            "  X",
            "  int b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(4,),
    ),
    Shape(
        "macro-continued-onto-an-opening-brace",
        ".cpp",
        rows("struct S {", "#define OPEN \\", "  {", "  int a;", "};"),
        "S",
        None,
        prefix=(4,),
    ),
    Shape(
        "macro-whose-comment-carries-it-onto-a-closing-brace",
        ".cpp",
        rows("int before = 1;", "#define CLOSE /* c", "*/ }", "struct S {", "};"),
        "S",
        None,
    ),
    Shape(
        "macros-whose-braces-balance-or-are-not-code",
        ".cpp",
        rows(
            "int before = 1;",
            "#define EMPTY {}",
            '#define STR "{"',
            "#define CMT 1 /* { */",
            "struct S {",
            "  int a;",
            "};",
        ),
        "S",
        (5, 7),
        prefix=(6,),
        control=(1, 2, 3),
    ),
    Shape(
        "unbalanced-brace-in-a-directive-that-is-not-a-macro",
        ".cpp",
        rows("#if 0", "#error unbalanced {", "#endif", "int before = 1;", "struct S {", "};"),
        "S",
        (5, 6),
        control=(2, 4),
    ),
]

# Text the compiler reads as one token or as a space, and the scan's lexer would not: a header name
# with `/*` or `//` in it (`<a/*b>` names the file `a/*b`, it opens no comment), a NUL byte, and
# U+180E (a space to the compiler, not to Python). A C or C++ file with any of them never isolates,
# and neither does one with a digraph (`<%`, `%>`, `%:`) or a trigraph (`??=`, `??<`, `??/`, ...)
# that spells a brace, a `#` or a backslash.
CPP_FOREIGN_TOKENS = [
    Shape(
        "header-name-with-a-comment-opener-above-a-template-header",
        ".cpp",
        rows(
            "int before = 1;",
            "#include <a/*b>",
            "template<typename T>",
            "/* c */",
            "struct Zq1 {",
            "  T t;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(3,),
    ),
    Shape(
        "has-include-with-a-comment-opener-above-a-template-header",
        ".cpp",
        rows(
            "#if __has_include(<a/*b>)",
            "int x = 1;",
            "#endif",
            "template<typename T>",
            "/* c */",
            "struct Zq1 {",
            "  T t;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(4,),
    ),
    Shape(
        "header-name-with-a-line-comment-opener",
        ".cpp",
        rows("int before = 1;", "#include <a//b>", "struct S {", "};"),
        "S",
        None,
    ),
    Shape(
        "quoted-header-name-with-a-comment-opener",
        ".cpp",
        rows("int before = 1;", '#include "a/*b"', "struct S {", "};"),
        "S",
        None,
    ),
    Shape(
        "header-names-with-comments-after-them",
        ".cpp",
        rows(
            "#include <vector> // the list",
            '#include "a.h" /* local */',
            "#include /* the system one */ <map>",
            "struct S {",
            "};",
        ),
        "S",
        (4, 5),
        control=(1, 2, 3),
    ),
    Shape(
        "conditional-directive-with-a-nul-inside-a-struct-body",
        ".cpp",
        rows(
            "int before = 1;",
            "struct Zq1 {",
            "  int a;",
            "#\x00if 0",
            "};",
            "struct Dead {",
            "#\x00endif",
            "  int b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(8,),
    ),
    Shape(
        "nul-byte-anywhere-in-the-file",
        ".h",
        rows("struct S {", "};", "int after = 2; /* \x00 */"),
        "S",
        None,
    ),
    Shape(
        "mongolian-vowel-separator-before-a-conditional-directive",
        ".cpp",
        rows(
            "int before = 1;",
            "struct Zq1 {",
            "  int a;",
            "\u180e#if 0",
            "};",
            "struct Dead {",
            "#endif",
            "  int b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(8,),
    ),
    Shape(
        "mongolian-vowel-separator-inside-a-conditional-directive",
        ".cpp",
        rows(
            "int before = 1;",
            "struct Zq1 {",
            "  int a;",
            "#\u180eif 0",
            "};",
            "struct Dead {",
            "#endif",
            "  int b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(8,),
    ),
    Shape(
        "digraph-conditional-inside-a-struct-body",
        ".cpp",
        rows(
            "struct Zq1 {", "  int a;", "%:if 0", "};", "struct Dead {", "%:endif", "  int b;", "};"
        ),
        "Zq1",
        None,
        prefix=(7,),
    ),
    Shape(
        "digraph-braces-around-a-struct-body",
        ".cpp",
        rows("struct Zq1", "<%", "  int a;", "%>;", "int after = 1;"),
        "Zq1",
        None,
        prefix=(3,),
    ),
    Shape(
        "trigraph-conditional-inside-a-struct-body",
        ".c",
        rows(
            "struct Zq1 {",
            "  int a;",
            "??=if 0",
            "};",
            "struct Dead {",
            "??=endif",
            "  int b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(7,),
    ),
    Shape(
        "question-marks-that-are-no-trigraph",
        ".cpp",
        rows('const char* q = "what??? ok?";', "struct S {", "};"),
        "S",
        (2, 3),
        control=(1,),
    ),
]

# Tokens the C and C++ lexer must read as the compiler does. A backtick is an ordinary character
# there (no template string), so it never hides the lines below it. A `'` inside a number is a
# digit separator, not a quote: read as a quote, `1'0; struct D { int b = 2'0;` hid a brace. A raw
# string ends at `)delimiter"`, not at the first quote: `R"(a";/*")"` opens no comment.
CPP_TOKENS = [
    Shape(
        "backtick-in-a-macro-above-a-template-header",
        ".cpp",
        rows(
            "int before = 1;",
            "#define Q `",
            "template<typename T>",
            "// `",
            "struct Zq1 {",
            "  T t;",
            "};",
        ),
        "Zq1",
        (3, 7),
        prefix=(3,),
        control=(1, 2),
    ),
    Shape(
        "backtick-in-a-skipped-branch-and-in-a-comment-above-a-template-header",
        ".cpp",
        rows(
            "#if 0",
            "`",
            "#endif",
            "int before = 1;",
            "template<typename T>",
            "// a`b",
            "struct Zq1 {",
            "  T t;",
            "};",
        ),
        "Zq1",
        (5, 9),
        prefix=(5,),
        control=(4,),
    ),
    Shape(
        "digit-separators-around-a-nested-struct-in-a-flush-left-body",
        ".cpp",
        rows(
            "struct Zq1 {",
            "int a = 1'0; struct D { int b = 2'0;",
            "int c;",
            "};",
            "#if 0",
            "{",
            "#endif",
            "int d;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(8,),
    ),
    Shape(
        "digit-separators-in-several-kinds-of-number",
        ".cpp",
        rows(
            "int before = 1;",
            "long x = 0x1'F'0 + 1'0e1'0 + .5'0;",
            "template<typename T>",
            "struct Zq1 {",
            "  T t; char c = '}';",
            "};",
        ),
        "Zq1",
        (3, 6),
        prefix=(3,),
        control=(1, 2),
    ),
    Shape(
        "digit-separator-in-a-macro",
        ".cpp",
        rows("int before = 1;", "#define N 1'000", "struct S {", "};"),
        "S",
        (3, 4),
        control=(1, 2),
    ),
    Shape(
        "digit-separator-before-a-block-comment-on-its-line",
        ".cpp",
        rows("int before = 1;", "int d = 1'0; /* { */", "struct S {", "};"),
        "S",
        None,  # a C compiler without digit separators reads `'0; /* { */` as a character literal
    ),
    Shape(
        "raw-string-holding-a-quote-and-a-comment-opener-above-a-template-header",
        ".cpp",
        rows(
            "int before = 1;",
            'const char* r = R"(a";/*")";',
            "template<typename T>",
            "// */",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        (3, 5),
        prefix=(3,),
        control=(1, 2),
    ),
    Shape(
        "raw-string-with-a-delimiter-holding-quotes-and-a-brace-in-a-flush-left-body",
        ".cpp",
        rows(
            "struct Zq1 {",
            'const char* r = R"x(a"}")x"; int q;',
            "int b;",
            "};",
            "int after = 1;",
        ),
        "Zq1",
        (1, 4),
        prefix=(3,),
        control=(5,),
    ),
    Shape(
        "raw-strings-with-encoding-prefixes",
        ".cpp",
        rows(
            "int before = 1;",
            'const char8_t* a = u8R"(})"; const wchar_t* b = LR"y(a)"{)y";',
            "template<typename T>",
            "struct Zq1 {",
            "  T t;",
            "};",
        ),
        "Zq1",
        (3, 6),
        prefix=(3,),
        control=(1, 2),
    ),
    Shape(
        "raw-string-on-a-macro-line-joined-by-a-splice",
        ".cpp",
        rows("int before = 1;", '#define RS R"(a\\', ')b)"', "struct S {", "};"),
        "S",
        None,  # the compiler undoes the splice inside the raw string
    ),
]

# A comment can carry a preprocessor line over several lines, and the compiler reads the joined
# text: `#/* c` / `*/ if 0` is `# if 0`. The conditional and header-name checks read that joined
# text, not one physical line at a time.
CPP_DIRECTIVE_COMMENTS = [
    Shape(
        "conditional-split-from-its-hash-by-a-comment-above-a-template-header",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "#/* c",
            "*/ if 0",
            "int dead;",
            "#/* c",
            "*/ endif",
            "struct Zq1 {",
            "  T b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(2,),
    ),
    Shape(
        "ifdef-split-from-its-hash-by-a-comment-over-three-lines",
        ".cpp",
        rows(
            "int before = 1;",
            "template<typename T>",
            "# /* c",
            "still a comment */ /* and another",
            "*/ ifdef NEVER",
            "int dead;",
            "#  /* c",
            "*/ endif",
            "struct Zq1 {",
            "  T b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(2,),
    ),
    Shape(
        "conditional-split-by-a-comment-above-a-member-struct-template-header",
        ".cpp",
        rows(
            "struct Oq1 {",
            "  int a;",
            "  template<typename T>",
            "#/* c",
            "*/ if 0",
            "  int dead;",
            "#/* c",
            "*/ endif",
            "  struct Zq1 { T b; };",
            "};",
        ),
        "Oq1.Zq1",
        None,
        prefix=(3,),
    ),
    Shape(
        "comment-that-carries-a-define-over-lines-above-a-declaration-is-not-a-conditional",
        ".cpp",
        rows(
            "int before = 1;",
            "#/* c",
            "*/ define N 1",
            "template<typename T>",
            "struct Zq1 {",
            "  T b;",
            "};",
        ),
        "Zq1",
        (4, 7),
        prefix=(4,),
        control=(1, 2, 3),
    ),
    Shape(
        "header-name-with-a-comment-opener-after-a-comment-inside-the-include-line",
        ".cpp",
        rows(
            "int before = 1;",
            "#include /* c",
            "*/ <a/*b>",
            "template<typename T>",
            "/* x */",
            "struct Zq1 {",
            "  T b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(4,),
    ),
    Shape(
        "quoted-header-name-with-a-line-comment-opener-after-a-comment-inside-the-import-line",
        ".cpp",
        rows(
            "int before = 1;",
            "#import /* c",
            "more */ /* d",
            '*/ "a//b"',
            "template<typename T>",
            "// x */",
            "struct Zq1 {",
            "  T b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(5,),
    ),
    Shape(
        "has-include-header-name-with-a-comment-opener-after-a-comment-inside-the-directive",
        ".cpp",
        rows(
            "#if __has_include(/* c",
            "*/ <a/*b>)",
            "#endif",
            "int before = 1;",
            "template<typename T>",
            "/* x */",
            "struct Zq1 {",
            "  T b;",
            "};",
        ),
        "Zq1",
        None,
        prefix=(5,),
    ),
    Shape(
        "ordinary-header-name-after-a-comment-inside-the-include-line",
        ".cpp",
        rows(
            "int before = 1;",
            "#include /* c",
            "*/ <a/b>",
            "template<typename T>",
            "struct Zq1 {",
            "  T b;",
            "};",
        ),
        "Zq1",
        (4, 7),
        prefix=(4,),
        control=(1, 2, 3),
    ),
]

# A bare macro line with a blank line below it is taken for an unrelated line, unless the same file
# defines it: `#define TPL template<typename T>` makes `TPL` the declaration's own template header.
CPP_DEFINED_PREFIX_MACROS = [
    Shape(
        "template-header-macro-defined-in-the-file-above-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "#define TPL template<typename T>",
            "TPL",
            "",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "function-like-prefix-macro-defined-in-the-file-above-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "#define DECL(a, b) template<a b>",
            "DECL(typename, T)",
            "",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "prefix-macro-defined-through-a-comment-and-spaces-above-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "#/* c",
            "*/ define\tTPL  template<typename T>",
            "TPL",
            "",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        None,
        prefix=(3, 4),
    ),
    Shape(
        "prefix-macro-defined-in-the-file-at-the-start-of-the-file",
        ".cpp",
        rows(
            "#define TPL template<typename T>",
            "TPL",
            "",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        None,
        prefix=(1, 2),
    ),
    Shape(
        "bare-macro-that-the-file-does-not-define-above-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "#define OTHER 1",
            "TPL",
            "",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        (5, 5),
        control=(1, 2, 3),
    ),
    Shape(
        "bare-macro-whose-name-only-starts-a-defined-one-above-a-blank-line",
        ".cpp",
        rows(
            "int before = 1;",
            "#define TPLX template<typename T>",
            "TPL",
            "",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        (5, 5),
        control=(1, 2, 3),
    ),
]

# A prefix line in the accepted run that names an identifier the same file `#define`s falls back to
# the file: the macro may expand to a constraint, a template parameter or an attribute, and editing
# its `#define` (which lies outside the span) would read fresh. The scan does not expand macros, so
# it refuses where it can see the name in the prefix, wherever the `#define` stands.
CPP_PREFIX_USES_DEFINED_MACRO = [
    Shape(
        "constraint-tail-macro-defined-above-the-template-header",
        ".cpp",
        rows(
            "int before = 1;",
            "#define CONSTR Dq1<T>",
            "template<typename T>",
            "requires Cq1<T> &&",
            "CONSTR",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        None,
        prefix=(2, 3, 4, 5),
    ),
    Shape(
        "constraint-macro-on-the-requires-line",
        ".cpp",
        rows(
            "int before = 1;",
            "#define CONSTR Dq1<T>",
            "template<typename T>",
            "requires Cq1<T> && CONSTR",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        None,
        prefix=(2, 3, 4),
    ),
    Shape(
        "function-like-constraint-macro",
        ".cpp",
        rows(
            "int before = 1;",
            "#define CONSTR(x) Dq1<x>",
            "template<typename T>",
            "requires CONSTR(T)",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        None,
        prefix=(2, 3, 4),
    ),
    Shape(
        "constraint-macro-defined-below-the-declaration",
        ".cpp",
        rows(
            "template<typename T>",
            "requires Small<T> && LIMIT",
            "struct Zq1 { T t; };",
            "#define LIMIT Dq1<T>",
        ),
        "Zq1",
        None,
        prefix=(1, 2, 4),
    ),
    Shape(
        "template-parameter-default-is-a-defined-macro",
        ".cpp",
        rows(
            "int before = 1;",
            "#define DEFAULT_T int",
            "template<typename T = DEFAULT_T>",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        None,
        prefix=(2, 3),
    ),
    Shape(
        "constraint-macro-defined-through-a-comment-split-directive",
        ".cpp",
        rows(
            "int before = 1;",
            "#/* c",
            "*/ define CONSTR Dq1<T>",
            "template<typename T>",
            "requires Cq1<T> && CONSTR",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        None,
        prefix=(2, 3, 4, 5),
    ),
    Shape(
        "constraint-macro-of-a-member-declaration",
        ".cpp",
        rows(
            "#define CONSTR Dq1<T>",
            "struct Oq1 {",
            "  template<typename T>",
            "  requires Cq1<T> && CONSTR",
            "  struct Zq1 { T t; };",
            "};",
        ),
        "Oq1.Zq1",
        None,
        prefix=(1, 3, 4),
    ),
    Shape(
        "constraint-names-a-macro-that-the-file-does-not-define",
        ".cpp",
        rows(
            "int before = 1;",
            "#define OTHER 1",
            "template<typename T>",
            "requires Cq1<T> && CONSTR",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        (3, 5),
        prefix=(3, 4),
        control=(1, 2),
    ),
    Shape(
        "constraint-names-a-macro-that-a-defined-name-only-starts",
        ".cpp",
        rows(
            "int before = 1;",
            "#define CONSTRX Dq1<T>",
            "template<typename T>",
            "requires Cq1<T> && CONSTR",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        (3, 5),
        prefix=(3, 4),
        control=(1, 2),
    ),
    Shape(
        "a-comment-on-a-prefix-line-names-a-defined-macro",
        ".cpp",
        rows(
            "int before = 1;",
            "#define CONSTR Dq1<T>",
            "template<typename T>",
            "requires Cq1<T> // CONSTR",
            "struct Zq1 { T t; };",
        ),
        "Zq1",
        (3, 5),
        prefix=(3, 4),
        control=(1, 2),
    ),
    Shape(
        "a-defined-macro-used-only-in-the-body",
        ".cpp",
        rows(
            "#define LIMIT 4",
            "template<typename T>",
            "struct Zq1 { int a[LIMIT]; };",
        ),
        "Zq1",
        (2, 3),
        prefix=(2,),
    ),
]


# A C++20 header-unit import names a header like an `#include` does: `import <x/*y>;` names the
# file `x/*y` and opens no comment (with header units compiled in, clang reads it so), where the
# scan's lexer would see a block comment that hides the template header and the constraint below
# it. A header name that holds a comment opener refuses the file, wherever the import stands.
def _import_shape(shape_id, *import_lines, span=None, control=()):
    """An import above a template header, a constraint and a comment that closes the phantom one."""
    top = len(import_lines) + 1
    return Shape(
        shape_id,
        ".cpp",
        rows(
            "int before = 1;",
            *import_lines,
            "template<typename T>",
            "requires C<T>",
            "// */ int b;",
            "struct R { T v; };",
        ),
        "R",
        None if span is None else (top + 1, top + 4),
        prefix=(top + 1, top + 2),
        control=control,
    )


CPP_IMPORT_HEADER_NAMES = [
    _import_shape("import-header-name-with-a-block-comment-opener", "import <x/*y>;"),
    _import_shape("import-quoted-header-name-with-a-block-comment-opener", 'import "x/*y";'),
    _import_shape("import-header-name-with-a-line-comment-opener", "import <x//y>;"),
    _import_shape("import-quoted-header-name-with-a-line-comment-opener", 'import "x//y";'),
    _import_shape("import-after-export", "export import <x/*y>;"),
    _import_shape("import-with-a-comment-before-the-header-name", "import /* c */ <x/*y>;"),
    _import_shape("import-with-a-comment-after-the-header-name", "import <x/*y>; /* c */"),
    _import_shape("import-after-a-comment-on-its-line", "/* c */ import <x/*y>;"),
    _import_shape("import-after-a-comment-that-closes-on-its-line", "/* c", "*/ import <x/*y>;"),
    _import_shape(
        "import-whose-header-name-follows-a-comment-over-lines", "import /* c", "*/ <x/*y>;"
    ),
    _import_shape(
        "import-whose-header-name-follows-a-comment-over-three-lines",
        "import /* c",
        "d",
        "*/ <x/*y>;",
    ),
    _import_shape("import-with-tabs", "\timport\t<x/*y>;"),
    _import_shape(
        "import-of-a-header-name-with-a-slash-only", "import <x/y>;", span=True, control=(1, 2)
    ),
    _import_shape("import-of-a-standard-header", "import <vector>;", span=True, control=(1, 2)),
    _import_shape("import-of-a-named-module", "import std.core;", span=True, control=(1, 2)),
    _import_shape(
        "import-with-a-comment-between-the-keyword-and-an-ordinary-name",
        "import /* c */ <x/y>;",
        span=True,
        control=(1, 2),
    ),
    _import_shape(
        "import-of-a-quoted-name-after-a-closed-comment-over-lines",
        "import /* c",
        '*/ "x/y";',
        span=True,
        control=(1,),
    ),
]

SHAPES = (
    CPP
    + CPP_UNCERTAIN
    + CPP_NOT_ISOLATED
    + CPP_BRACE_RANGE
    + CPP_DIRECTIVE_QUOTES
    + CPP_CONTINUATIONS
    + CPP_COMMENT_TAILS
    + CPP_SPLICED_COMMENTS
    + CPP_PHANTOM_COMMENTS
    + CPP_LOGICAL_LINES
    + CPP_SPLICED_CODE
    + CPP_MACRO_BRACES
    + CPP_FOREIGN_TOKENS
    + CPP_TOKENS
    + CPP_DIRECTIVE_COMMENTS
    + CPP_DEFINED_PREFIX_MACROS
    + CPP_PREFIX_USES_DEFINED_MACRO
    + CPP_IMPORT_HEADER_NAMES
    + OTHER
)


def edited(text, number):
    """``text`` with a comment appended to the 1-based line ``number``."""
    lines = text.split("\n")
    lines[number - 1] += " // edited"
    return "\n".join(lines)


def locate(shape):
    return symbols.locate_braced(shape.text, shape.name, shape.suffix)


@pytest.mark.parametrize("shape", SHAPES, ids=lambda shape: shape.id)
def test_the_span_includes_the_prefix_or_the_scan_refuses(shape):
    if shape.span is None:
        with pytest.raises(symbols.Unparseable):
            locate(shape)
    else:
        assert locate(shape) == shape.span


@pytest.mark.parametrize("shape", SHAPES, ids=lambda shape: shape.id)
def test_editing_only_a_prefix_line_is_never_fresh(shape, tmp_path):
    path = tmp_path / f"a{shape.suffix}"
    path.write_text(shape.text)
    citation = cite(f"a{shape.suffix}#{shape.name}")
    stored = take(citation, path)
    assert stored.fallback == (shape.span is None)
    assert compare(citation, stored, path) == FRESH
    for number in shape.prefix:
        path.write_text(edited(shape.text, number))
        assert compare(citation, stored, path) == CHANGED, f"line {number}"


@pytest.mark.parametrize(
    "shape", [s for s in SHAPES if s.span is not None], ids=lambda shape: shape.id
)
def test_an_edit_outside_an_isolated_span_stays_fresh(shape, tmp_path):
    path = tmp_path / f"a{shape.suffix}"
    path.write_text(shape.text)
    citation = cite(f"a{shape.suffix}#{shape.name}")
    stored = take(citation, path)
    assert not stored.fallback
    for number in shape.control:
        path.write_text(edited(shape.text, number))
        assert compare(citation, stored, path) == FRESH, f"line {number}"


@pytest.mark.parametrize("shape", [s for s in SHAPES if s.span is None], ids=lambda shape: shape.id)
def test_an_uncertain_prefix_says_the_citation_is_tracked_at_file_level(shape, tmp_path):
    path = tmp_path / f"a{shape.suffix}"
    path.write_text(shape.text)
    citation = cite(f"a{shape.suffix}#{shape.name}")
    stored = take(citation, path)
    assert stored.fallback
    assert level_note(citation, stored) == (
        " (tracked at file level: this symbol could not be isolated)"
    )


# The validation's s075: only the constraint above the struct changes.
S075_BEFORE = "template<typename T>\nrequires Small<T>\nstruct Router {\n  T value;\n};\n"
S075_AFTER = S075_BEFORE.replace("Small", "Large")


def test_changing_only_the_constraint_above_a_struct_is_changed(tmp_path):
    path = tmp_path / "a.cpp"
    path.write_text(S075_BEFORE)
    citation = cite("a.cpp#Router")
    stored = take(citation, path)
    assert not stored.fallback  # isolated: the finding is about this struct
    path.write_text(S075_AFTER)
    assert compare(citation, stored, path) == CHANGED


def test_changing_only_the_template_header_above_a_struct_is_changed(tmp_path):
    path = tmp_path / "a.cpp"
    path.write_text(S075_BEFORE)
    citation = cite("a.cpp#Router")
    stored = take(citation, path)
    path.write_text(S075_BEFORE.replace("typename T", "class T"))
    assert compare(citation, stored, path) == CHANGED


# Prefix words in a language that does not use them are plain code, and nothing there widens.
def test_a_template_word_in_another_language_is_just_code():
    text = "const template = 1;\nfunction f() {\n}\n"
    assert symbols.locate_braced(text, "f", ".ts") == (2, 3)


def test_a_directive_like_line_in_a_javascript_class_is_not_a_prefix():
    text = "class A {\n  #count = 1;\n  run() {\n  }\n}\n"
    assert symbols.locate_braced(text, "A.run", ".js") == (3, 4)


def test_a_requires_call_after_a_javascript_declaration_is_not_a_trailing_clause():
    text = "const x = foo()\nrequires(bar)\nfunction f() {\n}\n"
    assert symbols.locate_braced(text, "x", ".js") == (1, 1)


def test_a_very_long_run_of_prefix_lines_is_refused_not_cut():
    short = "[[nodiscard]]\n" * 5 + "struct S {\n};\n"
    assert symbols.locate_braced(short, "S", ".cpp") == (1, 7)
    long = "[[nodiscard]]\n" * 45 + "struct S {\n};\n"
    with pytest.raises(symbols.Unparseable):
        symbols.locate_braced(long, "S", ".cpp")


def test_a_very_long_multi_line_attribute_is_refused_not_cut():
    body = "".join(f"    {n},\n" for n in range(70))
    text = f"@Foo(\n{body})\nclass X {{\n}}\n"
    with pytest.raises(symbols.Unparseable):
        symbols.locate_braced(text, "X", ".java")
    assert symbols.locate_braced(text.replace(body, "    1,\n"), "X", ".java") == (1, 5)


def test_the_c_family_rules_follow_an_upper_case_suffix(tmp_path):
    path = tmp_path / "a.HPP"
    path.write_text(S075_BEFORE)
    citation = cite("a.HPP#Router")
    stored = take(citation, path)
    assert not stored.fallback
    path.write_text(S075_AFTER)
    assert compare(citation, stored, path) == CHANGED


def test_a_block_longer_than_the_scan_limit_above_a_declaration_does_not_refuse_it():
    text = "void f() {\n" + "  g();\n" * 2100 + "}\nstruct S {\n};\n"
    assert symbols.locate_braced(text, "S", ".cpp") == (2103, 2104)


# What sits above a declaration but is not a prefix must not widen its span.
def test_a_c_function_prototype_above_a_struct_is_not_a_prefix():
    text = "int f(int x);\nstruct S {\n};\n"
    assert symbols.locate_braced(text, "S", ".c") == (2, 3)


def test_a_brace_style_with_the_opening_brace_on_its_own_line():
    text = "template<typename T>\nrequires Small<T>\nstruct S\n{\n};\n"
    assert symbols.locate_braced(text, "S", ".cpp") == (1, 5)


# The real hook, as the validation's s075 ran: a temporary HOME and cache, a new session.


def _hook(project, env, event, session, path=None, tool=None):
    payload = {"hook_event_name": event, "session_id": session, "cwd": str(project.path)}
    if path is None:
        payload["source"] = "startup"
    else:
        payload.update(tool_name=tool, tool_input={"file_path": str(path)}, tool_response={})
    proc = subprocess.run(
        [str(ROOT / "bin/freshlint"), "hook"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        cwd=project.path,
        env=env,
        timeout=60,
    )
    assert (proc.returncode, proc.stderr) == (0, "")
    if not proc.stdout.strip():
        return ""
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def test_the_hook_reports_a_changed_constraint_above_a_cited_struct(
    claude_project, tmp_path, monkeypatch
):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(claude_project.path))
    monkeypatch.setenv("FRESHLINT_CACHE_DIR", str(tmp_path / "cache"))
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
    }
    code = claude_project.write("code/a.cpp", S075_BEFORE + "int after = 2;\n")
    note = claude_project.note("router.md", "**Evidence:** `code/a.cpp#Router`")
    _hook(claude_project, env, "SessionStart", "s1")
    _hook(claude_project, env, "PostToolUse", "s1", code, "Read")
    assert _hook(claude_project, env, "PostToolUse", "s1", note, "Write") == ""
    lock = json.loads((claude_project.memory / ".freshlint" / "lock.json").read_text())
    stored = lock["notes"]["router.md"]["citations"]["code/a.cpp#Router"]["fingerprint"]
    assert stored["kind"] == "symbol" and stored["fallback"] is False

    claude_project.write("code/a.cpp", S075_BEFORE + "int after = 3;\n")  # an unrelated edit
    assert "changed" not in _hook(claude_project, env, "SessionStart", "s2")

    claude_project.write("code/a.cpp", S075_AFTER + "int after = 3;\n")  # only the constraint
    report = _hook(claude_project, env, "SessionStart", "s3")
    assert "router.md: code/a.cpp#Router changed after this memory was written." in report


# A constraint that ends on a bare identifier line, then a blank line, then the struct.
BARE_TAIL = (
    "constexpr bool kFlag = true;\n"
    "constexpr bool kOther = false;\n"
    "template<typename T>\n"
    "requires (T{} == T{}) &&\n"
    "         kFlag\n"
    "\n"
    "struct S {\n"
    "};\n"
)


def test_the_hook_reports_a_changed_constraint_tail_above_a_blank_line_at_file_level(
    claude_project, tmp_path, monkeypatch
):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(claude_project.path))
    monkeypatch.setenv("FRESHLINT_CACHE_DIR", str(tmp_path / "cache"))
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
    }
    code = claude_project.write("code/a.cpp", BARE_TAIL)
    note = claude_project.note("s.md", "**Evidence:** `code/a.cpp#S`")
    _hook(claude_project, env, "SessionStart", "s1")
    _hook(claude_project, env, "PostToolUse", "s1", code, "Read")
    assert _hook(claude_project, env, "PostToolUse", "s1", note, "Write") == ""
    lock = json.loads((claude_project.memory / ".freshlint" / "lock.json").read_text())
    stored = lock["notes"]["s.md"]["citations"]["code/a.cpp#S"]["fingerprint"]
    assert stored["kind"] == "symbol" and stored["fallback"] is True  # refused, not cut short

    claude_project.write("code/a.cpp", BARE_TAIL.replace("         kFlag", "         kOther"))
    report = _hook(claude_project, env, "SessionStart", "s2")
    assert "s.md: code/a.cpp#S changed after this memory was written" in report
    assert "tracked at file level" in report


# A constraint that ends on a macro that the same file defines: the `#define` lies outside the span.
DEFINED_CONSTRAINT = (
    "int before = 1;\n"
    "#define CONSTR Dq1<T>\n"
    "template<typename T>\n"
    "requires Cq1<T> &&\n"
    "CONSTR\n"
    "struct Zq1 { T t; };\n"
    "int after = 2;\n"
)


def test_the_hook_reports_a_changed_define_that_a_cited_constraint_uses_at_file_level(
    claude_project, tmp_path, monkeypatch
):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(claude_project.path))
    monkeypatch.setenv("FRESHLINT_CACHE_DIR", str(tmp_path / "cache"))
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
    }
    code = claude_project.write("code/a.cpp", DEFINED_CONSTRAINT)
    note = claude_project.note("z.md", "**Evidence:** `code/a.cpp#Zq1`")
    _hook(claude_project, env, "SessionStart", "s1")
    _hook(claude_project, env, "PostToolUse", "s1", code, "Read")
    assert _hook(claude_project, env, "PostToolUse", "s1", note, "Write") == ""
    lock = json.loads((claude_project.memory / ".freshlint" / "lock.json").read_text())
    stored = lock["notes"]["z.md"]["citations"]["code/a.cpp#Zq1"]["fingerprint"]
    assert stored["kind"] == "symbol" and stored["fallback"] is True  # refused, not cut short

    claude_project.write(
        "code/a.cpp", DEFINED_CONSTRAINT.replace("Dq1<T>", "Eq1<T>")
    )  # the #define
    report = _hook(claude_project, env, "SessionStart", "s2")
    assert "z.md: code/a.cpp#Zq1 changed after this memory was written" in report
    assert "tracked at file level" in report


# A header-unit import whose header name holds a comment opener: the scan reads a comment there.
IMPORTED_CONSTRAINT = (
    "int before = 1;\n"
    "import <x/*y>;\n"
    "template<typename T>\n"
    "requires C<T>\n"
    "// */ int b;\n"
    "struct R { T v; };\n"
    "int after = 2;\n"
)


def test_the_hook_reports_a_changed_constraint_below_a_header_unit_import_at_file_level(
    claude_project, tmp_path, monkeypatch
):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(claude_project.path))
    monkeypatch.setenv("FRESHLINT_CACHE_DIR", str(tmp_path / "cache"))
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
    }
    code = claude_project.write("code/a.cpp", IMPORTED_CONSTRAINT)
    note = claude_project.note("r.md", "**Evidence:** `code/a.cpp#R`")
    _hook(claude_project, env, "SessionStart", "s1")
    _hook(claude_project, env, "PostToolUse", "s1", code, "Read")
    assert _hook(claude_project, env, "PostToolUse", "s1", note, "Write") == ""
    lock = json.loads((claude_project.memory / ".freshlint" / "lock.json").read_text())
    stored = lock["notes"]["r.md"]["citations"]["code/a.cpp#R"]["fingerprint"]
    assert stored["kind"] == "symbol" and stored["fallback"] is True  # refused, not cut short

    claude_project.write(
        "code/a.cpp", IMPORTED_CONSTRAINT.replace("C<T>", "D<T>")
    )  # the constraint
    report = _hook(claude_project, env, "SessionStart", "s2")
    assert "r.md: code/a.cpp#R changed after this memory was written" in report
    assert "tracked at file level" in report


# A member that exists is never reported gone because a dead branch hides the end of its container.
def test_a_member_behind_a_dead_branch_brace_is_not_reported_gone(tmp_path):
    text = rows(
        "struct Oq1 {",
        "int x;",
        "#if 0",
        "}",
        "struct D {",
        "#endif",
        "int fq1() { return 1; }",
        "};",
    )
    with pytest.raises(symbols.Unparseable):  # not NotFound: the compiler sees `fq1`
        symbols.locate_braced(text, "Oq1.fq1", ".cpp")
    path = tmp_path / "a.cpp"
    path.write_text(text)
    citation = cite("a.cpp#Oq1.fq1")
    stored = take(citation, path)
    assert stored.fallback
    path.write_text(text.replace("return 1", "return 2"))
    assert compare(citation, stored, path) == CHANGED


# A line separator that Python knows and a C++ compiler does not. `str.splitlines` splits there,
# so a comment (to the compiler: one line) would show its tail as a code line.
SEPARATORS = {
    "form-feed": "\x0c",
    "vertical-tab": "\x0b",
    "file-separator": "\x1c",
    "group-separator": "\x1d",
    "record-separator": "\x1e",
    "next-line": "\x85",
    "line-separator": " ",
    "paragraph-separator": " ",
}


def hidden_by_a_separator(separator):
    return rows(
        "int before = 1;",
        "template<typename T>",
        f"// see note{separator}int dead;",
        "struct Zq1 {",
        "};",
    )


@pytest.mark.parametrize("separator", SEPARATORS.values(), ids=list(SEPARATORS))
@pytest.mark.parametrize("suffix", [".cpp", ".h", ".C"])
def test_a_c_or_cpp_file_with_a_foreign_line_separator_falls_back(separator, suffix, tmp_path):
    text = hidden_by_a_separator(separator)
    with pytest.raises(symbols.Unparseable):
        symbols.locate_braced(text, "Zq1", suffix)
    path = tmp_path / f"a{suffix}"
    path.write_text(text)
    citation = cite(f"a{suffix}#Zq1")
    stored = take(citation, path)
    assert stored.fallback
    path.write_text(text.replace("template<typename T>", "template<typename T = int>"))
    assert compare(citation, stored, path) == CHANGED  # was fresh: the header was cut off


def test_a_form_feed_on_a_line_of_its_own_falls_back_too():
    text = rows("int before = 1;", "\x0c", "struct S {", "};")
    with pytest.raises(symbols.Unparseable):
        symbols.locate_braced(text, "S", ".c")


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_the_line_ends_every_compiler_knows_still_isolate(newline):
    text = newline.join(["int before = 1;", "template<typename T>", "struct S {", "};", ""])
    assert symbols.locate_braced(text, "S", ".cpp") == (2, 4)


@pytest.mark.parametrize("separator", SEPARATORS.values(), ids=list(SEPARATORS))
def test_other_languages_keep_their_line_numbering(separator):
    text = f"const a = 1;{separator}\nfunction f() {{\n}}\n"
    assert symbols.locate_braced(text, "f", ".ts") == (3, 4)


# What a refusal says: the first thing that makes the scan unsure. A splice in code refuses the file
# before anything else. The nearest code line may be part of the declaration (the reason before the
# gap check existed), and only a line that ends a statement has its gap checked for a directive.
@pytest.mark.parametrize(
    ("text", "reason"),
    [
        pytest.param(
            rows("FOO", "#ifdef A", "struct S {", "};", "#endif"),
            "a line above the declaration may be part of it",
            id="open-line-above-a-conditional",
        ),
        pytest.param(
            rows("int before = 1;", "#if 0", "EXPORT_API", "#endif", "struct S {", "};"),
            "a line above the declaration may be part of it",
            id="open-line-inside-a-conditional",
        ),
        pytest.param(
            rows("int before = 1;", "#ifdef A", "struct S {", "};", "#endif"),
            "a conditional directive is above the declaration",
            id="statement-above-a-conditional",
        ),
        pytest.param(
            rows(
                "int before = 1;",
                "// note \\",
                "int dead;",
                "template<typename T>",
                "struct S {",
                "};",
            ),
            "a backslash-newline joins lines outside a preprocessor line",
            id="statement-spliced-into-a-comment",
        ),
        pytest.param(
            rows("int a;", "#if 0", "int b;", "#endif", "MACRO0", "", "struct S {", "};"),
            "a conditional directive is above the declaration",
            id="detached-macro-below-a-conditional",
        ),
    ],
)
def test_a_refusal_names_the_first_thing_that_makes_the_scan_unsure(text, reason):
    with pytest.raises(symbols.Unparseable) as raised:
        symbols.locate_braced(text, "S", ".cpp")
    assert str(raised.value) == reason


# A name that a backslash-newline splits over two lines (`Zq\` then `1 {`) is the name to the compiler.
# It must never read as "not defined", and an edit to the declaration must never read fresh.
def assert_never_short(text, name, suffix, first, last):
    """The scan refuses, or gives a span that holds lines ``first`` to ``last``."""
    try:
        span = symbols.locate_braced(text, name, suffix)
    except symbols.Unparseable:
        return
    assert span[0] <= first and span[1] >= last, span


def test_an_identifier_split_by_a_line_splice_is_not_reported_gone(tmp_path):
    text = rows("int before = 1;", "struct Zq\\", "1 {", "  int a0;", "};")
    assert_never_short(text, "Zq1", ".cpp", 2, 5)
    path = tmp_path / "a.cpp"
    path.write_text(text)
    citation = cite("a.cpp#Zq1")
    stored = take(citation, path)
    path.write_text(text.replace("int a0;", "long a0;"))
    assert compare(citation, stored, path) == CHANGED


def test_a_member_split_by_a_line_splice_is_not_reported_gone():
    text = rows("struct Outer {", "  int fq\\", "1(int x) {", "    return x;", "  }", "};")
    assert_never_short(text, "Outer.fq1", ".cpp", 2, 5)


def test_a_name_split_by_two_splices_in_a_macro_is_not_reported_gone(tmp_path):
    text = rows("#define DECL struct Z\\", "\\", "q1 { int a; };", "DECL")
    with pytest.raises(symbols.Unparseable):  # not NotFound: the compiler declares `Zq1`
        symbols.locate_braced(text, "Zq1", ".cpp")
    path = tmp_path / "a.cpp"
    path.write_text(text)
    assert take(cite("a.cpp#Zq1"), path).fallback


def test_a_splice_whose_newline_is_a_line_feed_and_a_lone_carriage_return_is_refused():
    # The compiler takes LF then CR after a backslash as one newline, so `};` is the macro's text.
    text = "struct S {\nint a;\n#define W \\\n\r};\nint b;\n#define V {\n};\n"
    with pytest.raises(symbols.Unparseable):
        symbols.locate_braced(text, "S", ".cpp")


def test_a_file_with_a_line_feed_and_carriage_return_splice_falls_back(tmp_path):
    # Read with universal newlines, the LF and CR are two line ends and `int dead;` a statement;
    # to the compiler it is the macro's text and the template header belongs to Zq1.
    data = b"int before = 1;\ntemplate<typename T>\n#define X 1 \\\n\rint dead;\nstruct Zq1 { T t; };\n"
    path = tmp_path / "a.cpp"
    path.write_bytes(data)
    citation = cite("a.cpp#Zq1")
    stored = take(citation, path)
    assert stored.fallback
    path.write_bytes(data.replace(b"typename T>", b"typename T = int>"))
    assert compare(citation, stored, path) == CHANGED


def test_line_ends_as_written_give_the_same_span_as_universal_newlines(tmp_path):
    data = b"int before = 1;\r\ntemplate<typename T>\rstruct Zq1 {\r\n  T t;\n};\r\n"
    path = tmp_path / "a.cpp"
    path.write_bytes(data)
    stored = take(cite("a.cpp#Zq1"), path)
    assert not stored.fallback
    path.write_bytes(data.replace(b"before = 1", b"before = 2"))
    assert compare(cite("a.cpp#Zq1"), stored, path) == FRESH
    path.write_bytes(data.replace(b"T t;", b"T* t;"))
    assert compare(cite("a.cpp#Zq1"), stored, path) == CHANGED


def test_a_member_that_is_missing_from_its_container_reads_gone_in_a_file_with_macros(tmp_path):
    text = rows(
        "#define FOO \\",
        "  1",
        "struct A {",
        "  int f() { return 1; }",
        "};",
        "struct B {",
        "};",
    )
    with pytest.raises(symbols.NotFound):  # `f` is in A, not in B
        symbols.locate_braced(text, "B.f", ".cpp")
    path = tmp_path / "a.cpp"
    path.write_text(text)
    with pytest.raises(TargetMissing):
        take(cite("a.cpp#B.f"), path)


def test_a_symbol_that_is_not_in_a_file_with_macros_still_reads_gone(tmp_path):
    text = rows("#define FOO \\", "  1", "struct Other {", "};")
    with pytest.raises(symbols.NotFound):
        symbols.locate_braced(text, "Gone", ".cpp")
    path = tmp_path / "a.cpp"
    path.write_text(text)
    with pytest.raises(TargetMissing):
        take(cite("a.cpp#Gone"), path)


# A member that exists is never reported gone because a spliced comment hides the end of its container.
def test_a_member_behind_a_brace_hidden_by_a_spliced_comment_is_not_reported_gone(tmp_path):
    text = rows(
        "struct Outer {",
        "  int a;  // note \\",
        "};",
        "int fq1() { return 1; }",
        "// note \\",
        "struct Dead {",
        "};",
    )
    assert_never_short(text, "Outer.fq1", ".cpp", 4, 4)
    path = tmp_path / "a.cpp"
    path.write_text(text)
    citation = cite("a.cpp#Outer.fq1")
    stored = take(citation, path)
    path.write_text(text.replace("return 1", "return 2"))
    assert compare(citation, stored, path) == CHANGED
