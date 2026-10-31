import os

import pytest

from freshlint import symbols
from freshlint.evidence import parse_citation as cite
from freshlint.fingerprint import (
    CHANGED,
    FRESH,
    GONE,
    Fingerprint,
    TargetMissing,
    compare,
    current_digest,
    file_digest,
    level_note,
    normalize,
    take,
)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_normalize_strips_trailing_space_crlf_and_edge_blank_lines():
    assert normalize("\n\na  \r\nb\t\n\n") == ["a", "b"]


def test_file_fingerprint_ignores_whitespace_only_changes(tmp_path):
    path = write(tmp_path / "a.txt", "x\ny\n")
    fp = take(cite("a.txt"), path)
    write(path, "x  \r\ny\n\n")
    assert compare(cite("a.txt"), fp, path) == FRESH
    write(path, "x\nz\n")
    assert compare(cite("a.txt"), fp, path) == CHANGED
    path.unlink()
    assert compare(cite("a.txt"), fp, path) == GONE


def test_line_range_survives_moving(tmp_path):
    path = write(tmp_path / "a.py", "a\nb\nc\nd\ne\n")
    c = cite("a.py#L2-L4")
    fp = take(c, path)
    assert fp.start == 2 and fp.unique
    write(path, "new\nnew2\na\nb\nc\nd\ne\n")
    assert compare(c, fp, path) == FRESH
    write(path, "a\nb\nX\nd\ne\n")
    assert compare(c, fp, path) == CHANGED


def test_line_range_unchanged_in_place_is_fresh_however_short(tmp_path):
    path = write(tmp_path / "r.py", "x = 1\nreturn None\ny = 2\nreturn None\n")
    c = cite("r.py#L2")
    fp = take(c, path)
    assert not fp.unique
    write(path, "x = 1\nreturn None\ny = 3\nreturn None\n")
    assert compare(c, fp, path) == FRESH


def test_short_line_range_that_moves_is_changed(tmp_path):
    path = write(tmp_path / "r.py", "a\nreturn None\nb\n")
    c = cite("r.py#L2")
    fp = take(c, path)
    assert fp.unique
    write(path, "new\na\nreturn None\nb\n")
    assert compare(c, fp, path) == CHANGED
    two = cite("r.py#L2-L3")
    fp = take(two, write(path, "a\nreturn None\nb\nc\n"))
    write(path, "new\na\nreturn None\nb\nc\n")
    assert compare(two, fp, path) == CHANGED


def test_edited_line_does_not_relocate_to_a_look_alike_elsewhere(tmp_path):
    path = write(tmp_path / "r.py", "a\nreturn None\nb\nreturn None\n")
    c = cite("r.py#L2")
    fp = take(c, path)
    write(path, "a\nreturn 1\nb\nreturn None\n")
    assert compare(c, fp, path) == CHANGED


def test_solid_block_duplicated_when_taken_is_changed_once_the_original_is_edited(tmp_path):
    path = write(tmp_path / "r.py", "one\ntwo\nthree\nx\none\ntwo\nthree\n")
    c = cite("r.py#L1-L3")
    fp = take(c, path)
    assert not fp.unique
    write(path, "one\ntwo\nTHREE\nx\none\ntwo\nthree\n")
    assert compare(c, fp, path) == CHANGED


def test_solid_block_that_moved_and_was_copied_is_changed(tmp_path):
    path = write(tmp_path / "r.py", "one\ntwo\nthree\nx\n")
    c = cite("r.py#L1-L3")
    fp = take(c, path)
    write(path, "x\none\ntwo\nthree\ny\none\ntwo\nthree\n")
    assert compare(c, fp, path) == CHANGED


def test_blank_line_range_is_tracked_at_file_level(tmp_path):
    path = write(tmp_path / "a.txt", "a\n\n\nb\n")
    c = cite("a.txt#L2-L3")
    fp = take(c, path)
    assert fp.fallback and not fp.line_digests
    assert compare(c, fp, path) == FRESH
    write(path, "a\nx\ny\nb\n")
    assert compare(c, fp, path) == CHANGED


def test_a_different_kind_of_citation_never_matches_a_stored_fingerprint(tmp_path):
    path = write(tmp_path / "a.py", "a\nb\n")
    lines_fp = take(cite("a.py#L1-L2"), path)
    file_fp = take(cite("a.py"), path)
    assert compare(cite("a.py"), lines_fp, path) == CHANGED
    assert compare(cite("a.py#L1-L2"), file_fp, path) == CHANGED


def test_stored_line_range_without_digests_is_changed(tmp_path):
    path = write(tmp_path / "a.py", "a\nb\n")
    stored = Fingerprint.from_dict({"kind": "lines", "digest": "x"})
    assert compare(cite("a.py#L1-L2"), stored, path) == CHANGED


def test_stored_line_range_without_start_only_relocates_a_unique_solid_block(tmp_path):
    path = write(tmp_path / "a.py", "one\ntwo\nthree\n")
    c = cite("a.py#L1-L3")
    stored = Fingerprint.from_dict(take(c, path).to_dict() | {"start": None})
    assert stored.start is None
    assert compare(c, stored, path) == FRESH
    short = take(cite("a.py#L1"), path)
    legacy = Fingerprint.from_dict(short.to_dict() | {"start": None})
    assert compare(cite("a.py#L1"), legacy, path) == CHANGED


def test_undecodable_bytes_are_not_collapsed(tmp_path):
    path = tmp_path / "latin.txt"
    path.write_bytes(b"price \xe9 100\n")
    for raw in ("latin.txt", "latin.txt#L1"):
        fp = take(cite(raw), path)
        assert fp.digest == current_digest(path)
        assert compare(cite(raw), fp, path) == FRESH
        path.write_bytes(b"price \xe8 100\n")
        assert compare(cite(raw), fp, path) == CHANGED
        path.write_bytes(b"price \xe9 100\n")
    assert take(cite("latin.txt#L1"), path).fallback


def test_toml_date_and_string_are_different_values(tmp_path):
    path = write(tmp_path / "x.toml", "d = 2024-01-01\n")
    c = cite("x.toml#d")
    fp = take(c, path)
    assert not fp.fallback
    assert compare(c, fp, path) == FRESH
    write(path, 'd = "2024-01-01"\n')
    assert compare(c, fp, path) == CHANGED


def test_line_range_beyond_the_end_is_missing(tmp_path):
    path = write(tmp_path / "a.py", "a\n")
    with pytest.raises(TargetMissing):
        take(cite("a.py#L2-L5"), path)


def test_python_symbol_tracks_only_its_own_lines(tmp_path):
    path = write(tmp_path / "m.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    c = cite("m.py#a")
    fp = take(c, path)
    assert not fp.fallback
    write(path, "def a():\n    return 1\n\n\ndef b():\n    return 3\n")
    assert compare(c, fp, path) == FRESH
    write(path, "def a():\n    return 9\n\n\ndef b():\n    return 3\n")
    assert compare(c, fp, path) == CHANGED
    write(path, "def b():\n    return 3\n")
    assert compare(c, fp, path) == GONE


def test_missing_symbol_and_missing_file(tmp_path):
    path = write(tmp_path / "m.py", "def a():\n    pass\n")
    with pytest.raises(TargetMissing, match="'zzz' is not defined in m.py"):
        take(cite("m.py#zzz"), path)
    with pytest.raises(TargetMissing, match="nope.py does not exist"):
        take(cite("nope.py"), tmp_path / "nope.py")


def test_line_after_the_final_newline_is_missing(tmp_path):
    path = write(tmp_path / "a.txt", "one\n")
    assert take(cite("a.txt#L1"), path).line_digests
    with pytest.raises(TargetMissing, match="has only 1 lines"):
        take(cite("a.txt#L2"), path)


def test_directory_citation_is_rejected(tmp_path):
    (tmp_path / "d").mkdir()
    with pytest.raises(TargetMissing, match="is a directory"):
        take(cite("d"), tmp_path / "d")


def test_unknown_language_symbol_falls_back_to_the_file(tmp_path):
    path = write(tmp_path / "x.rb", "def go\nend\n")
    fp = take(cite("x.rb#go"), path)
    assert fp.fallback and fp.digest == file_digest("def go\nend\n")
    write(path, "def go\n  1\nend\n")
    assert compare(cite("x.rb#go"), fp, path) == CHANGED


@pytest.mark.parametrize("error", [RecursionError, ValueError])
def test_locator_failures_fall_back_to_the_file(tmp_path, monkeypatch, error):
    path = write(tmp_path / "m.py", "def a():\n    return 1\n")

    def boom(text, name):
        raise error("locator gave up")

    monkeypatch.setattr("freshlint.symbols.locate_python", boom)
    fp = take(cite("m.py#a"), path)
    assert fp.fallback is True
    assert fp.digest == file_digest("def a():\n    return 1\n")


@pytest.mark.parametrize("separator", ["\f", "\u2028", "\x85"])
def test_braced_symbol_uses_the_scans_line_numbers(tmp_path, separator):
    # str.splitlines() breaks on these characters and the braced scan numbers lines that way
    source = f"const x = 1;\n{separator}\nfunction f(a) {{\n  return 1;\n}}\nfunction g() {{\n  return 2;\n}}\n"
    path = write(tmp_path / "a.js", source)
    c = cite("a.js#f")
    fp = take(c, path)
    assert not fp.fallback
    assert compare(c, fp, path) == FRESH
    write(path, source.replace("function f(a)", "function f(a, b)"))
    assert compare(c, fp, path) == CHANGED
    write(path, source.replace("return 2", "return 3"))
    assert compare(c, fp, path) == FRESH


def test_deeply_nested_key_file_falls_back_to_the_file(tmp_path, monkeypatch):
    # Whether json.loads overflows depends on the interpreter, so make the locator do it.
    def too_deep(*_):
        raise RecursionError

    monkeypatch.setattr(symbols, "locate_key", too_deep)
    text = '{"a": [[[]]], "b": 1}'
    path = write(tmp_path / "deep.json", text)
    fp = take(cite("deep.json#b"), path)
    assert fp.fallback is True
    assert fp.digest == file_digest(text)


def test_key_fingerprint_ignores_other_keys_and_formatting(tmp_path):
    path = write(tmp_path / "package.json", '{"scripts": {"test": "pytest", "lint": "ruff"}}')
    c = cite("package.json#scripts.test")
    fp = take(c, path)
    write(path, '{\n  "scripts": {"lint": "ruff check", "test": "pytest"}\n}')
    assert compare(c, fp, path) == FRESH
    write(path, '{"scripts": {"test": "pytest -x"}}')
    assert compare(c, fp, path) == CHANGED
    write(path, '{"scripts": {}}')
    assert compare(c, fp, path) == GONE


def test_large_files_are_hashed_whole(tmp_path, monkeypatch):
    monkeypatch.setattr("freshlint.fingerprint.MAX_BYTES", 10)
    path = write(tmp_path / "big.py", "def a():\n    return 1\n")
    fp = take(cite("big.py#a"), path)
    assert fp.fallback
    assert compare(cite("big.py#a"), fp, path) == FRESH
    assert current_digest(path) == fp.digest


def test_round_trip_through_dict(tmp_path):
    path = write(tmp_path / "a.py", "a\nb\n")
    fp = take(cite("a.py#L1-L2"), path)
    assert Fingerprint.from_dict(fp.to_dict()) == fp
    assert fp.to_dict()["start"] == 1 and fp.to_dict()["unique"] is True


def test_older_stored_dict_without_start_or_unique_loads_with_defaults():
    fp = Fingerprint.from_dict({"kind": "lines", "digest": "d", "line_digests": ["h"]})
    assert fp.start is None and fp.unique is False and fp.line_digests == ("h",)


def test_current_digest_of_missing_file_is_none(tmp_path):
    assert current_digest(tmp_path / "nope") is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
@pytest.mark.parametrize("raw", ["a.py", "a.py#f", "a.py#L1-L2"])
def test_an_unreadable_file_is_changed_not_gone(tmp_path, raw):
    path = write(tmp_path / "a.py", "def f():\n    return 1\n")
    c = cite(raw)
    fp = take(c, path)
    path.chmod(0)
    try:
        assert compare(c, fp, path) == CHANGED
        assert current_digest(path) is None
    finally:
        path.chmod(0o600)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_taking_a_fingerprint_of_an_unreadable_file_says_it_cannot_be_read(tmp_path):
    path = write(tmp_path / "a.py", "x = 1\n")
    path.chmod(0)
    try:
        with pytest.raises(TargetMissing, match="a.py cannot be read"):
            take(cite("a.py"), path)
    finally:
        path.chmod(0o600)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_file_inside_an_unreadable_directory_is_changed_not_gone(tmp_path, pathlib_flavour):
    path = write(tmp_path / "d" / "a.py", "x = 1\n")
    c = cite("d/a.py")
    fp = take(c, path)
    path.parent.chmod(0)
    try:
        assert compare(c, fp, path) == CHANGED
    finally:
        path.parent.chmod(0o700)


def test_a_deleted_file_is_still_gone(tmp_path):
    path = write(tmp_path / "a.py", "def f():\n    return 1\n")
    c = cite("a.py#f")
    fp = take(c, path)
    path.unlink()
    assert compare(c, fp, path) == GONE


UTIL_JS = "function g() {\n  return 1;\n}\n"


@pytest.mark.parametrize(
    "above",
    ["const quotes = /[`'\"]/g;\n", 'const trim = (s) => s.replace(/\\/*$/, "");\n'],
    ids=["backtick-in-a-regex", "slash-star-in-a-regex"],
)
def test_a_lexer_misread_never_makes_an_untouched_symbol_gone(tmp_path, above):
    path = write(tmp_path / "util.js", UTIL_JS)
    c = cite("util.js#g")
    fp = take(c, path)
    assert not fp.fallback
    write(path, above + UTIL_JS)  # g itself is untouched
    assert compare(c, fp, path) in (FRESH, CHANGED)
    assert take(c, path).fallback  # an honest file-level fallback, not "g is not defined"


def test_a_symbol_that_is_really_deleted_is_still_gone(tmp_path):
    path = write(tmp_path / "util.js", UTIL_JS + "function h() {\n  return 2;\n}\n")
    c = cite("util.js#g")
    fp = take(c, path)
    write(path, "function h() {\n  return 2;\n}\n")
    assert compare(c, fp, path) == GONE
    with pytest.raises(TargetMissing, match="is not defined"):
        take(c, path)


@pytest.mark.parametrize(
    "raw,noun",
    [("a.ts#f", "symbol"), ("deep.json#b", "key"), ("a.py#L1-L2", "line range")],
)
def test_the_file_level_note_names_what_could_not_be_isolated(raw, noun):
    fallback = Fingerprint("symbol", "d", fallback=True)
    assert level_note(cite(raw), fallback) == (
        f" (tracked at file level: this {noun} could not be isolated)"
    )
    assert level_note(cite(raw), Fingerprint("symbol", "d")) == ""
    assert level_note(cite("a.ts"), Fingerprint("file", "d", fallback=True)) == ""


def test_the_file_level_note_also_names_a_symbol_that_can_no_longer_be_isolated(tmp_path):
    # A fingerprint stored when the scan isolated the symbol, and a scan that now refuses (an
    # `#ifdef` directly above it): the stored span cannot be compared, so the citation reads
    # `changed`, and the reader must be told that the whole file is what was compared.
    path = write(tmp_path / "a.cpp", "int a;\nstruct S {\n  int t;\n};\n")
    c = cite("a.cpp#S")
    stored = take(c, path)
    assert not stored.fallback
    assert level_note(c, stored, path) == ""  # still isolated
    write(path, "int a;\n#ifdef X\nstruct S {\n  int t;\n};\n#endif\n")
    assert compare(c, stored, path) == CHANGED
    assert level_note(c, stored, path) == (
        " (tracked at file level: this symbol could not be isolated)"
    )
    assert level_note(c, stored) == ""  # without the file only the stored fingerprint speaks
    write(path, "int a;\nstruct S {\n  long t;\n};\n")  # an edit to the isolated symbol
    assert compare(c, stored, path) == CHANGED
    assert level_note(c, stored, path) == ""


@pytest.mark.parametrize(
    "suffix,name,text,before",
    [
        (
            ".cs",
            "K.M",
            "class K {\n#if DEBUG\n[Obsolete]\n#endif\npublic void M() {\n}\n}\n",
            (5, 6),
        ),
        (".cs", "K.M", "class K {\n[A]\n#region r\n[B]\npublic void M() {\n}\n}\n", (4, 6)),
        (
            ".swift",
            "K.M",
            "class K {\n#if os(iOS)\n@available(iOS 13, *)\n#endif\nfunc M() {\n}\n}\n",
            (5, 6),
        ),
    ],
    ids=["csharp-if", "csharp-region", "swift-if"],
)
def test_a_span_stored_before_the_attribute_directives_joined_it_reads_changed_once(
    tmp_path, suffix, name, text, before
):
    # The upgrade note: the span used to start at the line below the `#if` lines, and now starts
    # above them. A fingerprint of the old span reads `changed` until the note is renewed.
    path = write(tmp_path / f"a{suffix}", text)
    c = cite(f"a{suffix}#{name}")
    lines = text.splitlines()
    stored_then = Fingerprint("symbol", file_digest("\n".join(lines[before[0] - 1 : before[1]])))
    assert compare(c, stored_then, path) == CHANGED  # not a false `fresh`
    renewed = take(c, path)
    assert not renewed.fallback and compare(c, renewed, path) == FRESH


def test_the_file_level_note_names_a_key_that_can_no_longer_be_isolated(tmp_path):
    path = write(tmp_path / "deep.json", '{"b": 1}\n')
    c = cite("deep.json#b")
    stored = take(c, path)
    assert not stored.fallback
    write(path, '{"b": 1,}\n')  # not JSON any more
    assert compare(c, stored, path) == CHANGED
    assert level_note(c, stored, path) == (
        " (tracked at file level: this key could not be isolated)"
    )


def test_the_file_level_note_ignores_a_cited_file_that_cannot_be_read(tmp_path):
    path = write(tmp_path / "a.cpp", "int a;\nstruct S {\n  int t;\n};\n")
    c = cite("a.cpp#S")
    stored = take(c, path)
    path.unlink()
    assert level_note(c, stored, path) == ""
