import json

from freshlint import memreport
from freshlint.checks import NoteFinding, ScanResult


def f(note, state, target="t", message="m", confidence="high"):
    return NoteFinding(note, state, target, message, confidence)


def test_a_clean_scan_is_silent():
    assert memreport.session_report(ScanResult(3, 1, []), first_run=False) is None


def test_findings_are_listed_most_serious_first():
    result = ScanResult(
        5,
        3,
        [
            f("b.md", "uncited-claim", message='says "verified" but cites no evidence.'),
            f("a.md", "changed", message="src/a.py#f changed after this memory was written."),
            f("c.md", "gone", message="src/c.py no longer exists."),
            f("d.md", "inferred-changed", confidence="low"),
        ],
    )
    assert memreport.session_report(result, first_run=False) == "\n".join(
        [
            "freshlint: 2 memories need re-checking before you rely on them.",
            "- c.md: src/c.py no longer exists.",
            "- a.md: src/a.py#f changed after this memory was written.",
            memreport.FOOTER,
        ]
    )


def test_single_memory_wording():
    text = memreport.session_report(ScanResult(1, 1, [f("a.md", "gone")]), first_run=False)
    assert text.splitlines()[0] == "freshlint: 1 memory needs re-checking before you rely on it."


def test_first_run_summary_counts_memories_not_findings():
    result = ScanResult(
        107,
        36,
        [
            f("a.md", "gone"),
            f("a.md", "gone", target="u"),
            f("b.md", "inferred-changed", confidence="low"),
            f("c.md", "uncited-claim"),
        ],
    )
    lines = memreport.session_report(result, first_run=True).splitlines()
    assert lines[0] == (
        "freshlint: 107 memories. 36 cite code: 1 cites files that no longer exist. "
        "1 claims certainty without evidence. Run /freshlint:memory-check to review."
    )


def test_the_first_run_summary_does_not_count_inferred_changed_findings():
    result = ScanResult(
        4,
        3,
        [
            f("a.md", "inferred-changed", confidence="low"),
            f("b.md", "inferred-changed", target="u", confidence="low"),
            f("c.md", "gone"),
        ],
    )
    summary = memreport.session_report(result, first_run=True).splitlines()[0]
    assert summary == (
        "freshlint: 4 memories. 3 cite code: 1 cites files that no longer exist. "
        "0 claim certainty without evidence. Run /freshlint:memory-check to review."
    )
    full = memreport.full_report(result)  # the full scan still lists them
    assert "[inferred-changed, low confidence]" in full


def test_first_run_with_nothing_counted_is_silent():
    assert memreport.session_report(ScanResult(2, 0, []), first_run=True) is None
    low = [f("a.md", "inferred-changed", confidence="low")]
    assert memreport.session_report(ScanResult(2, 1, low), first_run=True) is None
    assert memreport.summary_line(ScanResult(2, 1, low)) is None


def test_first_run_with_only_uncited_claims_still_gives_the_summary():
    text = memreport.session_report(ScanResult(2, 0, [f("a.md", "uncited-claim")]), first_run=True)
    assert text.startswith("freshlint: 2 memories. 0 cite code: 0 cite files that no longer exist.")


def test_first_run_lists_reported_items_without_a_summary_when_nothing_is_counted():
    text = memreport.session_report(ScanResult(2, 1, [f("a.md", "changed")]), first_run=True)
    assert text.splitlines()[0] == "freshlint: 1 memory needs re-checking before you rely on it."
    assert "Run /freshlint:memory-check to review" not in text


def test_the_report_never_exceeds_ten_lines():
    many = [f(f"n{i:02}.md", "changed") for i in range(30)]
    for first_run in (False, True):
        for corrupt in (False, True):
            for partial in (False, True):
                text = memreport.session_report(
                    ScanResult(30, 30, many, partial), first_run=first_run, corrupt=corrupt
                )
                lines = text.splitlines()
                assert len(lines) <= memreport.MAX_LINES
                assert sum(line.startswith("- ") for line in lines) <= memreport.MAX_ITEMS
                assert any(line.endswith("more: run /freshlint:memory-check.") for line in lines)


def test_misplaced_evidence_sorts_after_invalid_citations_and_stays_out_of_the_session_report():
    result = ScanResult(
        2,
        0,
        [
            f("a.md", "misplaced-evidence", confidence="low", message="line 9 mentions evidence."),
            f("b.md", "invalid-citation", confidence="low", message="`x` is a URL; cite a file"),
        ],
    )
    lines = memreport.full_report(result).splitlines()
    assert lines[1:] == [
        "b.md: [invalid-citation, low confidence] `x` is a URL; cite a file",
        "a.md: [misplaced-evidence, low confidence] line 9 mentions evidence.",
    ]
    assert memreport.session_report(result, first_run=False) is None


def test_every_command_name_in_the_reports_is_namespaced():
    many = [f("g.md", "gone")] + [f(f"n{i:02}.md", "changed") for i in range(30)]
    text = memreport.session_report(ScanResult(31, 31, many, True), first_run=True)
    assert text.count("/freshlint:memory-check") == 3
    assert "/memory-check" not in text.replace("/freshlint:memory-check", "")


def test_full_report_includes_low_confidence_findings():
    result = ScanResult(
        2, 1, [f("a.md", "inferred-changed", message="mentions x.", confidence="low")]
    )
    assert "a.md: [inferred-changed, low confidence] mentions x." in memreport.full_report(result)
    assert "No findings." in memreport.full_report(ScanResult(1, 0, []))


def test_a_check_error_is_full_report_only_and_marked_low_confidence():
    error = f(
        "bad.md",
        "check-error",
        target="",
        message="could not be checked (OSError).",
        confidence="low",
    )
    result = ScanResult(2, 0, [error])
    assert "bad.md: [check-error, low confidence] could not be checked (OSError)." in (
        memreport.full_report(result)
    )
    assert memreport.session_report(result, first_run=False) is None
    assert memreport.session_report(result, first_run=True) is None


def test_json_report_marks_reported_findings():
    data = json.loads(memreport.json_report(ScanResult(1, 1, [f("a.md", "gone")])))
    assert data["schema"] == 1 and data["notes"] == 1
    assert data["findings"][0]["reported"] is True


def test_save_feedback_prefixes_each_line():
    assert memreport.save_feedback(["a", "b"]) == "freshlint: a\nfreshlint: b"


def test_a_partial_scan_is_reported_even_when_there_is_nothing_else_to_say():
    text = memreport.session_report(ScanResult(3, 1, [], partial=True), first_run=False)
    assert (
        text
        == "(The scan stopped early to keep startup fast; run /freshlint:memory-check for the rest.)"
    )
    low = f("a.md", "check-error", confidence="low")
    assert memreport.session_report(ScanResult(3, 1, [low], True), first_run=False) == text
    assert memreport.session_report(ScanResult(3, 1, [], False), first_run=False) is None


def test_whitespace_in_an_item_collapses_so_the_line_cap_holds():
    messy = "src/a.py\nchanged\r\n\n  after\tthis memory\x85was written."
    result = ScanResult(1, 1, [f("a.md", "changed", message=messy)])
    text = memreport.session_report(result, first_run=False)
    assert "- a.md: src/a.py changed after this memory was written." in text.splitlines()
    many = [f(f"n{i:02}.md", "changed", message="a\nb\nc") for i in range(30)]
    capped = memreport.session_report(ScanResult(30, 30, many, True), first_run=True, corrupt=True)
    assert len(capped.splitlines()) <= memreport.MAX_LINES
