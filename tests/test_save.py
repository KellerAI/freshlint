import datetime as dt
from pathlib import Path

from freshlint.checks import Context, check_note
from freshlint.ledger import raw_digest
from freshlint.memory import read_note
from freshlint.save import on_save
from freshlint.store import InferredRecord, NoteRecord, Store

TODAY = dt.date(2026, 10, 1)


def ctx(project):
    return Context(project.path.resolve(), Path.home())


def read(*paths):
    return {str(p.resolve()): raw_digest(p) for p in paths}


def test_first_save_after_reading_stamps_silently(claude_project):
    code = claude_project.write("src/a.py", "def f():\n    return 1\n")
    note = read_note(claude_project.note("a.md", "**Evidence:** `src/a.py#f`"))
    store = Store()
    out = on_save(note, store, read(code), ctx(claude_project), TODAY)
    assert (out.feedback, out.renewed) == ([], [])
    record = store.notes["a.md"].citations["src/a.py#f"]
    assert record.fingerprint is not None and record.stamped == "2026-10-01"


def test_citing_unread_code_pushes_back(claude_project):
    claude_project.write("src/a.py", "x\n")
    note = read_note(claude_project.note("a.md", "**Evidence:** `src/a.py`"))
    store = Store()
    out = on_save(note, store, {}, ctx(claude_project), TODAY)
    assert out.feedback == [
        "a.md cites src/a.py, which you have not read in this session. "
        "Read it with the Read tool before citing it, or remove the citation."
    ]
    assert store.notes["a.md"].citations["src/a.py"].fingerprint is None


def test_citing_a_file_that_does_not_exist_says_so_instead_of_asking_to_read_it(claude_project):
    note = read_note(claude_project.note("a.md", "**Evidence:** `src/nope.py`"))
    store = Store()
    out = on_save(note, store, {}, ctx(claude_project), TODAY)
    assert out.feedback == [
        "a.md cites src/nope.py, but that file does not exist. "
        "Cite a file that exists, or remove the citation."
    ]
    assert store.notes["a.md"].citations["src/nope.py"].fingerprint is None


def test_a_file_read_and_then_deleted_is_reported_as_missing_not_as_changed(claude_project):
    code = claude_project.write("src/a.py", "x\n")
    seen = read(code)
    claude_project.remove("src/a.py")
    note = read_note(claude_project.note("a.md", "**Evidence:** `src/a.py`"))
    out = on_save(note, Store(), seen, ctx(claude_project), TODAY)
    assert out.feedback == [
        "a.md cites src/a.py, but that file does not exist. "
        "Cite a file that exists, or remove the citation."
    ]


def test_code_changed_after_the_read_is_not_stamped(claude_project):
    code = claude_project.write("src/a.py", "x\n")
    seen = read(code)
    claude_project.write("src/a.py", "y\n")
    note = read_note(claude_project.note("a.md", "**Evidence:** `src/a.py`"))
    out = on_save(note, Store(), seen, ctx(claude_project), TODAY)
    assert out.feedback == [
        "a.md cites src/a.py, which changed after you read it. Read it again before citing it."
    ]


def test_renewal_needs_a_read_and_is_reported(claude_project):
    code = claude_project.write("src/a.py", "def f():\n    return 1\n")
    path = claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    store = Store()
    on_save(read_note(path), store, read(code), ctx(claude_project), dt.date(2026, 9, 1))
    claude_project.write("src/a.py", "def f():\n    return 2\n")
    out = on_save(read_note(path), store, {}, ctx(claude_project), TODAY)
    assert out.feedback == [
        "a.md rests on src/a.py#f, which changed after this memory was written. "
        "Re-read it before relying on or updating this memory."
    ]
    assert store.notes["a.md"].citations["src/a.py#f"].stamped == "2026-09-01"
    out = on_save(read_note(path), store, read(code), ctx(claude_project), TODAY)
    assert (out.feedback, out.renewed) == ([], ["src/a.py#f"])
    assert store.notes["a.md"].citations["src/a.py#f"].stamped == "2026-10-01"


def test_unchanged_code_without_a_read_is_silent(claude_project):
    code = claude_project.write("src/a.py", "x\n")
    path = claude_project.note("a.md", "**Evidence:** `src/a.py`")
    store = Store()
    on_save(read_note(path), store, read(code), ctx(claude_project), TODAY)
    out = on_save(read_note(path), store, {}, ctx(claude_project), TODAY)
    assert (out.feedback, out.renewed) == ([], [])


def test_missing_symbol_is_reported(claude_project):
    code = claude_project.write("src/a.py", "def f():\n    return 1\n")
    note = read_note(claude_project.note("a.md", "**Evidence:** `src/a.py#nope`"))
    store = Store()
    out = on_save(note, store, read(code), ctx(claude_project), TODAY)
    assert out.feedback == ["a.md cites src/a.py#nope, but 'nope' is not defined in src/a.py."]
    record = store.notes["a.md"].citations["src/a.py#nope"]
    assert (record.fingerprint, record.problem) == (
        None,
        "missing:'nope' is not defined in src/a.py",
    )


def test_a_citation_read_but_changed_before_the_save_records_why(claude_project):
    code = claude_project.write("src/a.py", "x\n")
    seen = read(code)
    claude_project.write("src/a.py", "y\n")
    note = read_note(claude_project.note("a.md", "**Evidence:** `src/a.py`"))
    store = Store()
    on_save(note, store, seen, ctx(claude_project), TODAY)
    record = store.notes["a.md"].citations["src/a.py"]
    assert (record.fingerprint, record.problem) == (None, "changed")


def test_a_citation_never_read_records_no_reason(claude_project):
    claude_project.write("src/a.py", "x\n")
    note = read_note(claude_project.note("a.md", "**Evidence:** `src/a.py`"))
    store = Store()
    on_save(note, store, {}, ctx(claude_project), TODAY)
    assert store.notes["a.md"].citations["src/a.py"].problem is None


def test_reading_and_saving_again_clears_the_reason(claude_project):
    code = claude_project.write("src/a.py", "x\n")
    seen = read(code)
    claude_project.write("src/a.py", "y\n")
    note = read_note(claude_project.note("a.md", "**Evidence:** `src/a.py`"))
    store = Store()
    on_save(note, store, seen, ctx(claude_project), TODAY)
    on_save(note, store, read(code), ctx(claude_project), TODAY)
    record = store.notes["a.md"].citations["src/a.py"]
    assert record.fingerprint is not None and record.problem is None


def test_uncited_claims_push_back_and_feedback_notes_are_exempt(claude_project):
    note = read_note(
        claude_project.note("a.md", "Verified: the cache in `src/cache.py` is the root cause.")
    )
    out = on_save(note, Store(), {}, ctx(claude_project), TODAY)
    assert out.feedback == [
        'a.md says "verified" but cites no evidence. Add a line that starts with **Evidence:** '
        "followed by backticked citations (for example `**Evidence:** `path/to/file.py#function_name``), "
        'or say when and how it was checked (for example "checked 2026-10-01 by running `pytest`").'
    ]
    rule = read_note(
        claude_project.note("b.md", "Always say what you verified in `x.py`.", note_type="feedback")
    )
    assert on_save(rule, Store(), {}, ctx(claude_project), TODAY).feedback == []


def test_a_save_does_not_push_back_on_a_question_instruction_quote_or_negation(claude_project):
    for index, text in enumerate(
        (
            "Has `src/cache.py` been tested?",
            "Record the root cause in `issue.md` after investigation.",
            'The old log said "`route()` is broken".',
            "It is false that `route()` always fails.",
            "The release `0.3.0b2` is verified.",
            "`src/cache.py` is fixed. [verified: tested by Sam]",
        )
    ):
        note = read_note(claude_project.note(f"n{index}.md", text))
        assert on_save(note, Store(), {}, ctx(claude_project), TODAY).feedback == [], text
    for index, text in enumerate(("Note that `x` is fixed.", "Remember: `retry()` always fails.")):
        note = read_note(claude_project.note(f"w{index}.md", text))
        assert len(on_save(note, Store(), {}, ctx(claude_project), TODAY).feedback) == 1, text


def test_removed_citations_are_dropped(claude_project):
    a = claude_project.write("src/a.py", "x\n")
    b = claude_project.write("src/b.py", "y\n")
    path = claude_project.note("a.md", "**Evidence:** `src/a.py` `src/b.py`")
    store = Store()
    on_save(read_note(path), store, read(a, b), ctx(claude_project), TODAY)
    path = claude_project.note("a.md", "**Evidence:** `src/a.py`")
    on_save(read_note(path), store, read(a), ctx(claude_project), TODAY)
    assert list(store.notes["a.md"].citations) == ["src/a.py"]


def test_a_save_drops_the_inferred_record_of_a_cited_file_and_keeps_the_others(claude_project):
    a = claude_project.write("src/a.py", "x\n")
    claude_project.write("src/b.py", "y\n")
    first = InferredRecord("file", "mtime", None, 1.0)
    second = InferredRecord("file", "mtime", None, 2.0)
    path = claude_project.note("a.md", "See `src/a.py` and `src/b.py`.\n\n**Evidence:** `src/a.py`")
    store = Store(
        {
            "a.md": NoteRecord(
                inferred={"src/a.py": first, "src/b.py": second, "old.py": second},
                inferred_key="k",
            )
        }
    )
    on_save(read_note(path), store, read(a), ctx(claude_project), TODAY)
    record = store.notes["a.md"]
    # src/a.py is tracked by its citation now. The other records are not a citation's business
    # (old.py left the note, which the next scan notices); the key is reset so that scan looks.
    assert record.inferred == {"src/b.py": second, "old.py": second}
    assert record.inferred_key is None


def test_a_save_that_covers_nothing_leaves_the_inferred_records_and_their_key_alone(claude_project):
    a = claude_project.write("src/a.py", "x\n")
    claude_project.write("src/b.py", "y\n")
    path = claude_project.note("a.md", "See `src/b.py`.\n\n**Evidence:** `src/a.py`")
    record = InferredRecord("file", "mtime", None, 1.0)
    store = Store({"a.md": NoteRecord(inferred={"src/b.py": record}, inferred_key="k")})
    on_save(read_note(path), store, read(a), ctx(claude_project), TODAY)
    assert store.notes["a.md"].inferred == {"src/b.py": record}
    assert store.notes["a.md"].inferred_key == "k"


def test_bad_citations_and_the_feedback_cap(claude_project):
    cites = " ".join(f"`src/m{i}.py`" for i in range(7))
    note = read_note(claude_project.note("a.md", f"**Evidence:** `https://x.dev/a` {cites}"))
    out = on_save(note, Store(), {}, ctx(claude_project), TODAY)
    assert out.feedback[0] == "a.md: `https://x.dev/a` is a URL; cite a file."
    assert len(out.feedback) == 6
    assert out.feedback[-1] == "(+3 more: run /freshlint:memory-check)"


def test_a_resave_never_moves_a_line_range_onto_different_code(claude_project):
    block = "alpha\nbeta\ngamma\n"
    code = claude_project.write("src/a.py", f"h1\nh2\nh3\n{block}")
    path = claude_project.note("a.md", "**Evidence:** `src/a.py#L4-L6`")
    store = Store()
    on_save(read_note(path), store, read(code), ctx(claude_project), dt.date(2026, 9, 1))
    original = store.notes["a.md"].citations["src/a.py#L4-L6"]
    claude_project.write("src/a.py", f"n1\nn2\nh1\nh2\nh3\n{block}")
    out = on_save(read_note(path), store, read(code), ctx(claude_project), TODAY)
    assert (out.feedback, out.renewed) == ([], [])
    assert store.notes["a.md"].citations["src/a.py#L4-L6"] is original
    assert original.stamped == "2026-09-01"
    findings, _ = check_note(read_note(path), store.notes["a.md"], ctx(claude_project))
    assert findings == []


def test_unchanged_symbol_is_not_renewed_by_a_resave_after_a_read(claude_project):
    code = claude_project.write("src/a.py", "def f():\n    return 1\n")
    path = claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    store = Store()
    on_save(read_note(path), store, read(code), ctx(claude_project), dt.date(2026, 9, 1))
    claude_project.append("src/a.py", "\ndef g():\n    return 2\n")
    out = on_save(read_note(path), store, read(code), ctx(claude_project), TODAY)
    assert (out.feedback, out.renewed) == ([], [])
    assert store.notes["a.md"].citations["src/a.py#f"].stamped == "2026-09-01"


def test_deleted_code_without_a_read_says_it_no_longer_exists(claude_project):
    code = claude_project.write("src/a.py", "def f():\n    return 1\n")
    path = claude_project.note("a.md", "**Evidence:** `src/a.py#f`")
    store = Store()
    on_save(read_note(path), store, read(code), ctx(claude_project), dt.date(2026, 9, 1))
    original = store.notes["a.md"].citations["src/a.py#f"]
    claude_project.remove("src/a.py")
    out = on_save(read_note(path), store, {}, ctx(claude_project), TODAY)
    assert out.feedback == [
        "a.md rests on src/a.py#f, which no longer exists. "
        "Rewrite the memory, or remove the citation, before relying on it."
    ]
    assert store.notes["a.md"].citations["src/a.py#f"] is original


MISPLACED = (
    "a.md: citations must be on their own line that starts with **Evidence:** "
    "(for example `**Evidence:** `src/limits.py#retry_after``); line {n} was not read as evidence."
)


def line_of(note, needle):
    return next(i for i, line in enumerate(note.text.splitlines(), 1) if needle in line)


def test_evidence_inside_a_sentence_is_pushed_back_even_when_the_claim_is_hedged(claude_project):
    code = claude_project.write("src/limits.py", "def retry_after():\n    return 30\n")
    body = (
        "Retries wait 30 seconds after a 429.\n\n"
        "Unverified guess. Evidence: `src/limits.py#retry_after` returns 30.\n"
    )
    note = read_note(claude_project.note("a.md", body))
    store = Store()
    out = on_save(note, store, read(code), ctx(claude_project), TODAY)
    assert out.feedback == [MISPLACED.format(n=line_of(note, "Unverified guess"))]
    assert store.notes["a.md"].citations == {}


def test_misplaced_feedback_names_the_first_such_line_and_comes_before_the_claim_check(
    claude_project,
):
    text = (
        "Intro.\nSo Evidence: `src/a.py` shows it.\nVerified: the cache in `src/cache.py` is it.\n"
    )
    note = read_note(claude_project.note("a.md", text))
    out = on_save(note, Store(), {}, ctx(claude_project), TODAY)
    assert out.feedback[0] == MISPLACED.format(n=line_of(note, "So Evidence"))
    assert out.feedback[1].startswith('a.md says "verified" but cites no evidence.')
    assert len(out.feedback) == 2


def test_a_proper_evidence_line_draws_no_misplaced_feedback(claude_project):
    code = claude_project.write("src/a.py", "def f():\n    return 1\n")
    text = "As Evidence: `src/a.py` says.\n\n**Evidence:** `src/a.py#f`\n"
    note = read_note(claude_project.note("a.md", text))
    out = on_save(note, Store(), read(code), ctx(claude_project), TODAY)
    assert out.feedback == []


def test_prose_about_evidence_without_code_is_not_pushed_back(claude_project):
    note = read_note(claude_project.note("a.md", "There is evidence: see the logs.\n"))
    assert on_save(note, Store(), {}, ctx(claude_project), TODAY).feedback == []


def test_misplaced_evidence_is_only_flagged_in_notes_that_make_claims(claude_project):
    text = "Always cite as you go. Evidence: `src/a.py` shows why.\n"
    rule = read_note(claude_project.note("rule.md", text, note_type="feedback"))
    assert on_save(rule, Store(), {}, ctx(claude_project), TODAY).feedback == []
    person = read_note(claude_project.note("who.md", text, note_type="user"))
    assert on_save(person, Store(), {}, ctx(claude_project), TODAY).feedback == []
    for note_type in ("project", "reference", None):
        note = read_note(claude_project.note("p.md", text, note_type=note_type))
        out = on_save(note, Store(), {}, ctx(claude_project), TODAY)
        assert [line.split(";")[-1] for line in out.feedback] == [
            f" line {line_of(note, 'Always cite')} was not read as evidence."
        ], note_type


def test_a_file_level_fallback_says_so_when_a_save_rests_on_stale_evidence(claude_project):
    source = (
        "export function f(a: string): string;\nexport function f(a: number): number;\n"
        "export function f(a: any) {\n  return a;\n}\n\nfunction other() {\n  return 1;\n}\n"
    )
    code = claude_project.write("src/a.ts", source)
    path = claude_project.note("a.md", "**Evidence:** `src/a.ts#f`")
    store = Store()
    on_save(read_note(path), store, read(code), ctx(claude_project), dt.date(2026, 9, 1))
    assert store.notes["a.md"].citations["src/a.ts#f"].fingerprint.fallback
    claude_project.write("src/a.ts", source.replace("return 1", "return 2"))  # not f
    out = on_save(read_note(path), store, {}, ctx(claude_project), TODAY)
    assert out.feedback == [
        "a.md rests on src/a.ts#f, which changed after this memory was written "
        "(tracked at file level: this symbol could not be isolated). "
        "Re-read it before relying on or updating this memory."
    ]


def test_a_symbol_that_can_no_longer_be_isolated_says_so_when_a_save_rests_on_stale_evidence(
    claude_project,
):
    source = "int a;\nstruct S {\n  int t;\n};\n"
    code = claude_project.write("src/a.cpp", source)
    path = claude_project.note("a.md", "**Evidence:** `src/a.cpp#S`")
    store = Store()
    on_save(read_note(path), store, read(code), ctx(claude_project), dt.date(2026, 9, 1))
    assert not store.notes["a.md"].citations["src/a.cpp#S"].fingerprint.fallback
    claude_project.write("src/a.cpp", "int a;\n#ifdef X\nstruct S {\n  int t;\n};\n#endif\n")
    out = on_save(read_note(path), store, {}, ctx(claude_project), TODAY)
    assert out.feedback == [
        "a.md rests on src/a.cpp#S, which changed after this memory was written "
        "(tracked at file level: this symbol could not be isolated). "
        "Re-read it before relying on or updating this memory."
    ]


def test_a_save_still_pushes_back_on_a_claim_beside_an_unrelated_date(claude_project):
    # P1: a date credits only when bound to verification.
    note = read_note(
        claude_project.note("n.md", "`route()` is broken and the deadline is 2026-10-09.")
    )
    assert len(on_save(note, Store(), {}, ctx(claude_project), TODAY).feedback) == 1


def test_a_save_accepts_a_claim_with_a_marker_line_or_a_standalone_tag_and_not_other_lines(
    claude_project,
):
    # P2.
    for index, text in enumerate(
        (
            "`route()` is fixed.\nVerified: ran the suite by hand",
            "- `route()` is fixed.\n  - Checked: by hand",
            "`route()` is fixed. [verified]",
            "- `route()` is fixed.\n  **[verified: code comments]**",
        )
    ):
        note = read_note(claude_project.note(f"p{index}.md", text))
        assert on_save(note, Store(), {}, ctx(claude_project), TODAY).feedback == [], text
    for index, text in enumerate(
        (
            "`route()` is fixed.\nSome other note.",
            "- `route()` is fixed.\n- Verified: ran the suite by hand",
            "`route()` is fixed.\n\n[verified]",
        )
    ):
        note = read_note(claude_project.note(f"w{index}.md", text))
        assert len(on_save(note, Store(), {}, ctx(claude_project), TODAY).feedback) == 1, text
