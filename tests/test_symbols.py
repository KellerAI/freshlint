import warnings

import pytest

from freshlint import symbols

PY = """import os

RATE_LIMIT = 10


@cached
def refresh(token):
    return token


class Auth:
    timeout: int = 5

    def logout(self):
        return None
"""


@pytest.mark.parametrize(
    "name,span",
    [
        ("refresh", (6, 8)),
        ("RATE_LIMIT", (3, 3)),
        ("Auth", (11, 15)),
        ("Auth.logout", (14, 15)),
        ("Auth.timeout", (12, 12)),
    ],
)
def test_python_symbols(name, span):
    assert symbols.locate_python(PY, name) == span


@pytest.mark.parametrize("name", ["missing", "Auth.missing", "RATE_LIMIT.x"])
def test_python_missing(name):
    with pytest.raises(symbols.NotFound):
        symbols.locate_python(PY, name)


def test_python_syntax_error_is_unparseable():
    with pytest.raises(symbols.Unparseable):
        symbols.locate_python("def broken(:\n", "broken")


TS = """import { x } from "./x";

export async function refreshToken(token: string): Promise<string> {
  if (token) {
    return token;
  }
  return "";
}

export const LIMIT = 10

export const handler = (req: Request) =>
  {
    return req;
  };

export class Session {
  private timer = 0;

  start(): void {
    this.timer = 1;
  }

  stop() {
    start();
  }
}
"""


@pytest.mark.parametrize(
    "name,span",
    [
        ("refreshToken", (3, 8)),
        ("LIMIT", (10, 10)),
        ("Session", (17, 27)),
        ("Session.start", (20, 22)),
        ("Session.stop", (24, 26)),
    ],
)
def test_typescript_symbols(name, span):
    assert symbols.locate_braced(TS, name) == span


def test_arrow_body_on_the_next_line_is_unparseable():
    # The closing line `  };` is indented differently from the declaration, so the scan refuses.
    with pytest.raises(symbols.Unparseable):
        symbols.locate_braced(TS, "handler")


def test_typescript_missing_and_unsure():
    with pytest.raises(symbols.NotFound):
        symbols.locate_braced(TS, "missingThing")
    with pytest.raises(symbols.Unparseable):
        symbols.locate_braced(TS, "Session.timer")


def test_go_method_with_receiver_and_wrapped_parameters():
    go = "package auth\n\nfunc (s *Store) Refresh(ctx context.Context,\n\tid string) error {\n\treturn nil\n}\n"
    assert symbols.locate_braced(go, "Refresh") == (3, 6)


def test_rust_function_with_modifiers():
    rs = "pub(crate) async fn load(path: &str) -> Result<String> {\n    Ok(String::new())\n}\n"
    assert symbols.locate_braced(rs, "load") == (1, 3)


def test_declaration_without_a_clear_end_is_unparseable():
    stuck = "export const items = compute(\n  1,\n  2\n)\n"
    with pytest.raises(symbols.Unparseable):
        symbols.locate_braced(stuck, "items")


def test_json_keys_and_list_indexes():
    text = '{"scripts": {"test": "pytest", "test:e2e": "playwright"}, "files": ["a", "b"]}'
    assert symbols.locate_key(text, ".json", "scripts.test:e2e") == "playwright"
    assert symbols.locate_key(text, ".json", "files.1") == "b"
    with pytest.raises(symbols.NotFound):
        symbols.locate_key(text, ".json", "scripts.lint")


def test_toml_keys_and_bad_files():
    assert (
        symbols.locate_key("[tool.ruff]\nline-length = 100\n", ".toml", "tool.ruff.line-length")
        == 100
    )
    with pytest.raises(symbols.Unparseable):
        symbols.locate_key("{", ".json", "a")
    with pytest.raises(symbols.Unparseable):
        symbols.locate_key("[broken", ".toml", "a")


def test_block_comment_inside_a_span_is_unparseable():
    inside = "function f() {\n  /* } */\n  return 1;\n}\n"
    with pytest.raises(symbols.Unparseable):
        symbols.locate_braced(inside, "f")


def test_block_comment_above_the_declaration_is_outside_the_span():
    above = "/* note: } */\n/**\n * Docs for f.\n */\nfunction f() {\n  return 1;\n}\n"
    assert symbols.locate_braced(above, "f") == (5, 7)


GO_STORE = "type Store struct {\n\tn int\n}\n\nfunc (s *Store) Refresh() error {\n\treturn nil\n}\n"
RUST_IMPL = "impl Foo {\n    fn bar(&self) {\n        run();\n    }\n}\n"
RUST_STRUCT_AND_IMPL = "struct Foo {\n    a: u8,\n}\n\nimpl Foo {\n    fn bar(&self) {}\n}\n"
TS_CLASS_OVERLOADS = "class A {\n  run(a: string): void;\n  run(a: number): void;\n  run(a: any) {\n    return a;\n  }\n}\n"

# Each shape is either located in full or refused. Never a span shorter than the declaration.
BRACED_SHAPES = [
    pytest.param("function f()\n{\n  return 1;\n}\n", "f", (1, 4), id="allman-function"),
    pytest.param("public class Foo\n{\n    int x;\n}\n", "Foo", (1, 4), id="allman-csharp-class"),
    pytest.param(
        "export function Card({ title }: Props) {\n  return title;\n}\n",
        "Card",
        (1, 3),
        id="tsx-destructured-parameter",
    ),
    pytest.param(
        "export function Foo({\n  a,\n  b,\n}: Props) {\n  return a;\n}\n",
        "Foo",
        (1, 6),
        id="multi-line-destructured-parameters",
    ),
    pytest.param(
        'export function Card({ title }: Props) {\n  return <div className="c">{title}</div>;\n}\n',
        "Card",
        (1, 3),
        id="tsx-closing-tag",
    ),
    pytest.param(
        "export function Card({ x }: Props) {\n  return <Foo bar={x} />;\n}\n",
        "Card",
        (1, 3),
        id="tsx-self-closing-tag",
    ),
    pytest.param(
        "function f(o = {}) {\n  return o;\n}\n", "f", (1, 3), id="default-parameter-object"
    ),
    pytest.param(
        "func (s *S) Handle(v interface{}) error {\n\treturn nil\n}\n",
        "Handle",
        (1, 3),
        id="go-interface-parameter",
    ),
    pytest.param("export const x: { a: number } = {\n  a: 1,\n};\n", "x", (1, 3), id="typed-const"),
    pytest.param(
        "export const x = a ||\n  b;\n", "x", symbols.Unparseable, id="operator-at-end-of-line"
    ),
    pytest.param(
        "export const A = { a: 1 } as const\nexport const B = 2;\n",
        "A",
        (1, 1),
        id="as-const-on-one-line",
    ),
    pytest.param(
        "export const h = wrap(async (req) => {\n  run();\n});\n",
        "h",
        (1, 3),
        id="callback-argument",
    ),
    pytest.param(
        "export function f<T extends { a: number }>(x: T) {\n  return x;\n}\n",
        "f",
        (1, 3),
        id="braces-in-generic-constraint",
    ),
    pytest.param(
        "/*\nfunction f() {\n}\n*/\nfunction f() {\n  return 1;\n}\n",
        "f",
        (5, 7),
        id="commented-out-copy-above",
    ),
    pytest.param(
        "const doc = `\nfunction f() {\n}\n`;\nfunction f() {\n  return 1;\n}\n",
        "f",
        (5, 7),
        id="declaration-inside-template-ignored",
    ),
    pytest.param(
        "class C {\n  void a() {\n    foo(new Object() {\n    });\n  }\n  void foo() {\n    return;\n  }\n}\n",
        "C.foo",
        (6, 8),
        id="member-after-call-with-brace",
    ),
    pytest.param(GO_STORE, "Store.Refresh", (5, 7), id="go-method-by-receiver"),
    pytest.param(RUST_IMPL, "Foo.bar", (2, 4), id="rust-impl-method"),
    pytest.param(
        'export const r = express\n  .Router()\n  .get("/", h);\n',
        "r",
        symbols.Unparseable,
        id="method-chain",
    ),
    pytest.param(
        "export const Q = gql`\n  query {\n    a\n  }\n`;\n",
        "Q",
        symbols.Unparseable,
        id="template-literal-const",
    ),
    pytest.param(
        "function f() {\n  return `\n    }\n  `;\n}\n",
        "f",
        symbols.Unparseable,
        id="multi-line-template-with-brace",
    ),
    pytest.param('val doc = """\n}\n"""\n', "doc", symbols.Unparseable, id="multi-line-text-block"),
    pytest.param(
        'function f(s) {\n  return s.replace(/}/g, "");\n}\n',
        "f",
        symbols.Unparseable,
        id="regex-literal-with-brace",
    ),
    pytest.param("export const y = cond\n  ? 1\n  : 2;\n", "y", symbols.Unparseable, id="ternary"),
    pytest.param(
        "type A =\n  | { a: 1 }\n  | { b: 2 };\n",
        "A",
        symbols.Unparseable,
        id="union-of-object-types",
    ),
    pytest.param(
        "export const x = {\n  a: 1,\n}\n  .foo();\n",
        "x",
        symbols.Unparseable,
        id="chain-after-closing-brace",
    ),
    pytest.param(
        "export const x = {\n  a: 1,\n}\n  as const;\n",
        "x",
        symbols.Unparseable,
        id="as-const-on-the-next-line",
    ),
    pytest.param(
        "function a() {\n  const cfg = {\n    k: 1,\n  };\n}\nexport const cfg = {\n  z: 2,\n};\n",
        "cfg",
        (6, 8),
        id="top-level-wins-over-a-nested-local",
    ),
    pytest.param(
        "export function f(a: string): string;\nexport function f(a: number): number;\nexport function f(a: any) {\n  return a;\n}\n",
        "f",
        symbols.Unparseable,
        id="typescript-overloads",
    ),
    pytest.param(TS_CLASS_OVERLOADS, "A.run", symbols.Unparseable, id="overloaded-class-member"),
    pytest.param(RUST_STRUCT_AND_IMPL, "Foo", symbols.Unparseable, id="rust-struct-and-impl"),
    # The name still appears in the raw text, so the scan cannot rule out that it misread the file.
    pytest.param(
        "/*\nfunction f() {\n  return 1;\n}\n*/\n",
        "f",
        symbols.Unparseable,
        id="declaration-only-in-block-comment",
    ),
    pytest.param(
        "// function f() {}\n", "f", symbols.Unparseable, id="declaration-only-in-line-comment"
    ),
    pytest.param(
        "function a() {\n  return 1;\n}\n", "f", symbols.NotFound, id="name-absent-from-the-text"
    ),
    # Wrapped heritage and constraint clauses, and braces in multi-line type lists.
    pytest.param(
        "export interface BadgeProps\n  extends Base<Props>,\n    Other {\n  a: string;\n}\n",
        "BadgeProps",
        symbols.Unparseable,
        id="typescript-interface-extends-on-the-next-line",
    ),
    pytest.param(
        "class A\n  extends B {\n  m() {\n  }\n}\n",
        "A",
        symbols.Unparseable,
        id="class-extends-on-the-next-line",
    ),
    pytest.param(
        "class C {\n  public void run()\n      throws IOException {\n    go();\n  }\n}\n",
        "C.run",
        symbols.Unparseable,
        id="java-throws-on-the-next-line",
    ),
    pytest.param(
        "fn foo<T>()\nwhere\n    T: Debug,\n{\n    run();\n}\n",
        "foo",
        symbols.Unparseable,
        id="rust-where-on-the-next-line",
    ),
    pytest.param(
        "public class Foo<T>\n    where T : class\n{\n    int x;\n}\n",
        "Foo",
        symbols.Unparseable,
        id="csharp-where-on-the-next-line",
    ),
    pytest.param(
        "export const L: Record<\n  K,\n  { label: string },\n> = {\n  a: 1,\n};\n",
        "L",
        symbols.Unparseable,
        id="brace-inside-a-multi-line-type-list",
    ),
    pytest.param(
        "export function f<\n  T extends { id: string },\n>(x: T) {\n  return x;\n}\n",
        "f",
        symbols.Unparseable,
        id="brace-inside-multi-line-generic-parameters",
    ),
    pytest.param(
        "const m = new Map<string, { a: 1 }\n>();\nfunction g() {\n}\n",
        "m",
        symbols.Unparseable,
        id="brace-inside-a-type-argument-list",
    ),
    pytest.param(
        "function f(): { a: number }\n{\n  return { a: 1 };\n}\n",
        "f",
        symbols.Unparseable,
        id="return-type-then-brace-on-the-next-line",
    ),
    # Top-level lookups ignore nested locals, and more declaration keywords are known.
    pytest.param("fun run() {\n    val run = 1\n}\n", "run", (1, 3), id="kotlin-fun-and-local-val"),
    pytest.param(
        "data class User(val id: Int) {\n    fun x() {}\n}\n",
        "User",
        (1, 3),
        id="kotlin-data-class",
    ),
    pytest.param("enum class Color {\n    RED,\n}\n", "Color", (1, 3), id="kotlin-enum-class"),
    pytest.param("fun String.shout() {\n}\n", "shout", (1, 2), id="kotlin-extension-fun"),
    pytest.param(
        "pub const fn max(a: u32) -> u32 {\n    a\n}\n", "max", (1, 3), id="rust-const-fn"
    ),
    pytest.param('pub extern "C" fn cb(x: i32) {\n}\n', "cb", (1, 2), id="rust-extern-c-fn"),
    pytest.param("unsafe fn danger() {\n}\n", "danger", (1, 2), id="rust-unsafe-fn"),
    pytest.param(
        "function a() {\n  const run = 1;\n}\n",
        "run",
        symbols.Unparseable,
        id="only-a-nested-local",
    ),
    # Names that end in `$`.
    pytest.param("function foo$() {\n  return 1;\n}\n", "foo$", (1, 3), id="name-ending-in-dollar"),
    pytest.param(
        "function foo$bar() {\n}\n", "foo", symbols.NotFound, id="dollar-continues-a-name"
    ),
    # Decorators belong to the declaration.
    pytest.param(
        "@Injectable()\nexport class A {\n  a = 1;\n}\n", "A", (1, 4), id="single-line-decorator"
    ),
    pytest.param("@A()\n@B\nexport class C {\n}\n", "C", (1, 4), id="two-decorators"),
    pytest.param("@A()\n\nclass B {\n}\n", "B", (1, 4), id="blank-line-after-decorator"),
    pytest.param(
        "class C {\n  @Override\n  public void run() {\n  }\n}\n",
        "C.run",
        (2, 4),
        id="annotated-member",
    ),
    pytest.param(
        '@Component({\n  selector: "x",\n})\nexport class A {\n}\n',
        "A",
        (1, 5),
        id="multi-line-decorator",
    ),
]


@pytest.mark.parametrize("text,name,expected", BRACED_SHAPES)
def test_braced_shapes(text, name, expected):
    if isinstance(expected, tuple):
        assert symbols.locate_braced(text, name) == expected
    else:
        with pytest.raises(expected):
            symbols.locate_braced(text, name)


def test_rust_lifetime_struct_is_never_a_shorter_span():
    rs = "struct S<'a> { x: &'a str }\nfn g() {\n    let a = 1;\n}\n"
    try:
        span = symbols.locate_braced(rs, "S")
    except symbols.Unparseable:
        return
    assert span[0] <= 1 and span[1] >= 1


def test_python_several_definitions_give_the_union_span():
    overloads = (
        "from typing import overload\n@overload\ndef f(a: int) -> int: ...\n@overload\n"
        "def f(a: str) -> str: ...\ndef f(a):\n    return a\n"
    )
    assert symbols.locate_python(overloads, "f") == (2, 7)
    prop = "class A:\n    @property\n    def x(self):\n        return 1\n    @x.setter\n    def x(self, v):\n        pass\n"
    assert symbols.locate_python(prop, "A.x") == (2, 7)
    assert symbols.locate_python("def f():\n    return 1\n\ndef f():\n    return 2\n", "f") == (
        1,
        5,
    )


def test_python_null_byte_is_unparseable():
    with pytest.raises(symbols.Unparseable):
        symbols.locate_python("x = 1\x00\n", "x")


@pytest.mark.parametrize("index", ["\u00b2", "\u0661"])
def test_key_list_index_must_be_ascii_digits(index):
    with pytest.raises(symbols.NotFound):
        symbols.locate_key('{"files": ["a", "b"]}', ".json", f"files.{index}")


@pytest.mark.parametrize(
    "text,name,expected",
    [
        pytest.param(
            "try:\n    import json\nexcept ImportError:\n    json = None\n",
            "json",
            (2, 4),
            id="try-import-and-fallback",
        ),
        pytest.param(
            "try:\n    def f():\n        return 1\nexcept Exception:\n    def f():\n        return 2\n",
            "f",
            (2, 6),
            id="def-in-try-and-except",
        ),
        pytest.param("if x:\n    y = 1\nelse:\n    y = 2\n", "y", (2, 4), id="if-else-bindings"),
        pytest.param("total = 1\ntotal += 2\n", "total", (1, 2), id="augmented-assignment"),
        pytest.param("a, b = 1, 2\n", "b", (1, 1), id="tuple-target"),
        pytest.param("[a, *rest] = items\n", "rest", (1, 1), id="starred-list-target"),
        pytest.param("from m import name as other\n", "other", (1, 1), id="import-from-as"),
        pytest.param("import os.path as p\n", "p", (1, 1), id="import-as"),
        pytest.param(
            "class A:\n    if X:\n        def m(self): pass\n    else:\n        def m(self): pass\n",
            "A.m",
            (3, 5),
            id="method-in-if-else-inside-a-class",
        ),
        pytest.param(
            "def f():\n    z = 1\n",
            "z",
            symbols.NotFound,
            id="function-locals-are-not-module-names",
        ),
    ],
)
def test_python_bindings(text, name, expected):
    if isinstance(expected, tuple):
        assert symbols.locate_python(text, name) == expected
    else:
        with pytest.raises(expected):
            symbols.locate_python(text, name)


def test_key_list_index_longer_than_nine_digits_is_not_found():
    with pytest.raises(symbols.NotFound):
        symbols.locate_key('{"files": ["a"]}', ".json", "files." + "1" * 5000)


NB1_REPRO = """export function isUrl(s: string) {
  if (/^https?:\\/\\//.test(s)) {
    return true;
  }
  return false;
}

export function load() {
  const config = readFile();
  return config;
}

export const config = {
  retries: 3,
  timeout: 5000,
};
"""

DISPLAY_NAME = 'class C {\n  @Test\n  @DisplayName("a" +\n          "b")\n  void run() {\n  }\n}\n'

# Depth drift, attributes and the last guards. Each shape is located in full or refused. An
# attribute that spans lines joins the span when its brackets balance (tests/test_prefixes.py).
GUARD_SHAPES = [
    pytest.param(
        NB1_REPRO, "config", symbols.Unparseable, id="regex-with-slashes-drifts-the-depth"
    ),
    pytest.param(
        "const re = /}/;\nfunction a() {\n  const cfg = 1;\n}\nexport const cfg = {\n  z: 2,\n};\n",
        "cfg",
        symbols.Unparseable,
        id="regex-with-a-brace-before-a-nested-local",
    ),
    pytest.param(
        "struct S<'a> { x: &'a str }\nfn bar() {\n    let foo = 1;\n}\nfn foo() {\n}\n",
        "foo",
        symbols.Unparseable,
        id="rust-lifetime-before-a-nested-local",
    ),
    pytest.param(
        "function f() {\n}\nconst x = {\n", "f", symbols.Unparseable, id="braces-do-not-balance"
    ),
    pytest.param(
        "  function f() {\n  }\n", "f", symbols.Unparseable, id="indented-top-level-candidate"
    ),
    pytest.param(DISPLAY_NAME, "C.run", (2, 6), id="wrapped-annotation-above-member"),
    pytest.param(
        "@Foo([\n  A,\n])\nclass X {\n}\n",
        "X",
        (1, 5),
        id="decorator-ending-in-bracket",
    ),
    pytest.param(
        "@A()\n\n// note\nclass B {\n}\n", "B", (1, 5), id="blank-and-comment-after-decorator"
    ),
    pytest.param(
        "@A()\n/**\n * Docs.\n */\nclass B {\n}\n", "B", (1, 6), id="doc-comment-after-decorator"
    ),
    pytest.param("#[derive(Debug)]\nstruct S {\n    a: u8,\n}\n", "S", (1, 4), id="rust-derive"),
    pytest.param(
        "#[derive(Debug)]\n#[repr(C)]\nstruct S {\n}\n", "S", (1, 4), id="two-rust-attributes"
    ),
    pytest.param(
        "[Serializable]\npublic class Foo\n{\n    int x;\n}\n", "Foo", (1, 5), id="csharp-attribute"
    ),
    pytest.param(
        "template<typename T>\nstruct Box {\n};\n", "Box", (1, 3), id="cpp-template-header"
    ),
    pytest.param(
        "val x = if (a) {\n    1\n}\nelse {\n    2\n}\n",
        "x",
        symbols.Unparseable,
        id="else-on-the-next-line",
    ),
    pytest.param(
        "val r = try {\n    1\n}\ncatch (e: Exception) {\n}\n",
        "r",
        symbols.Unparseable,
        id="catch-on-the-next-line",
    ),
    pytest.param(
        "function f() {\n  return 1;\n}\n(function g() {\n})();\n",
        "f",
        symbols.Unparseable,
        id="parenthesis-on-the-next-line",
    ),
    pytest.param(
        "function f() {\n}\n[1].map(g);\n", "f", symbols.Unparseable, id="bracket-on-the-next-line"
    ),
    pytest.param(
        "function a() {\n  return 1;\n}\nfunction b() {\n  return 2;\n}\n",
        "b",
        (4, 6),
        id="function-after-a-closing-brace",
    ),
    pytest.param(
        'app.get("/", () => {\n  x();\n});\nfunction f() {\n}\n',
        "f",
        (4, 5),
        id="declaration-after-a-call-statement-with-a-callback",
    ),
    pytest.param(
        "foo(function () {\n}, 2)\nfunction g() {\n}\n",
        "g",
        symbols.Unparseable,
        id="declaration-after-a-wrapped-call-tail",
    ),
    pytest.param(
        'class R {\n  @Query(value = "x",\n      nativeQuery = true)\n  void find() {\n  }\n}\n',
        "R.find",
        (2, 5),
        id="annotation-ending-with-a-named-argument",
    ),
    pytest.param(
        "const o = {\n  foo() {\n    return 1;\n  },\n  bar() {\n  },\n};\n",
        "o.foo",
        symbols.Unparseable,
        id="last-line-ends-with-a-comma",
    ),
]


@pytest.mark.parametrize("text,name,expected", GUARD_SHAPES)
def test_braced_guards(text, name, expected):
    if isinstance(expected, tuple):
        assert symbols.locate_braced(text, name) == expected
    else:
        with pytest.raises(expected):
            symbols.locate_braced(text, name)


# Regex literals that the line lexer misreads as the start of a template string or a block comment.
# Every later line then looks like part of the literal, so the name must not be called missing.
DESYNC_BACKTICK = "const quotes = /[`'\"]/g;\n\nfunction g() {\n  return 1;\n}\n"
DESYNC_TRAILING_SLASHES = (
    'const trim = (s) => s.replace(/\\/*$/, "");\n\nfunction g() {\n  return 1;\n}\n'
)


@pytest.mark.parametrize(
    "source", [DESYNC_BACKTICK, DESYNC_TRAILING_SLASHES], ids=["backtick", "slashes"]
)
def test_a_name_in_a_misread_file_is_unparseable_never_not_found(source):
    with pytest.raises(symbols.Unparseable):
        symbols.locate_braced(source, "g")


def test_parsing_a_cited_python_file_never_leaks_a_syntax_warning():
    source = "import re\n\nPATTERN = re.compile('\\d+')\n\n\ndef f():\n    return PATTERN\n"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert symbols.locate_python(source, "f") == (6, 7)
    assert [str(w.message) for w in caught] == []


HAS_SYMBOL = (
    "".join(f"const a{i} = {i};\n" for i in range(1, 10))  # the declaration starts on line 10
    + 'var hasSymbol = typeof Symbol !== "undefined" && typeof\n'
    + "/*#__PURE__*/\n"
    + 'Symbol("x") === "symbol";\n'
    + "function other() {\n  return 1;\n}\n"
)


def test_a_declaration_ending_in_a_keyword_operator_is_not_cut_short():
    # Bundler output: an unindented continuation line after a trailing `typeof`.
    try:
        span = symbols.locate_braced(HAS_SYMBOL, "hasSymbol")
    except symbols.Unparseable:
        return
    assert span == (10, 12)


@pytest.mark.parametrize(
    "keyword", ["typeof", "await", "new", "void", "in", "instanceof", "delete"]
)
def test_every_trailing_keyword_operator_continues_the_line(keyword):
    source = f"var v = a {keyword}\nb;\n"
    try:
        span = symbols.locate_braced(source, "v")
    except symbols.Unparseable:
        return
    assert span == (1, 2)


def test_a_name_that_merely_ends_in_a_keyword_still_ends_the_line():
    assert symbols.locate_braced("var a = main;\nvar b = 2;\n", "a") == (1, 1)
    assert symbols.locate_braced("var a = renew;\nvar b = 2;\n", "a") == (1, 1)
