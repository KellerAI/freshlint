from freshlint import claims
from freshlint.claims import applies_to, find_claims


def hits(text, **kwargs):
    return [(h.line, h.phrase) for h in find_claims(text, **kwargs)]


def test_a_certainty_phrase_about_code_is_a_claim():
    assert hits("The root cause is the retry loop in `retry()`.\n") == [(1, "root cause")]
    assert hits("The root cause is in src/retry.py.\n") == [(1, "root cause")]


def test_claims_that_name_no_code_are_ignored():
    assert hits("The root cause is the retry loop.\n") == []
    assert hits("Sam confirmed this reframe is valuable.\n") == []
    assert hits("The Next.js site was confirmed moved.\n") == []
    assert hits("The Acme/Bolt demo is confirmed.\n") == []


def test_hedged_future_attributed_and_dated_sentences_are_not_claims():
    assert hits("The root cause is probably `retry()`.\n") == []
    assert hits("`deploy.sh` will be tested next week.\n") == []
    assert hits("This is unverified: `src/a.py`.\n") == []
    assert hits("`src/a.py` confirmed by Sam on Monday.\n") == []
    assert hits("Retries verified in `retry()`, checked 2026-10-01 by running `pytest`.\n") == []
    assert hits("Verified `retry()` on 2026-10-01.\n") == []  # a date in the sentence credits it


def test_compound_words_do_not_count():
    assert hits("`StageProgress` renders an auto-verified lane.\n") == []


def test_one_hit_per_sentence_and_every_sentence_counts():
    text = "Verified `retry()` in staging. The bug in `src/a.py` is fixed now.\n"
    assert hits(text) == [(1, "verified"), (1, "is fixed")]


def test_frontmatter_code_and_skipped_lines_are_ignored():
    text = "---\ndescription: verified `x.py`\n---\n~~~\nconfirmed `x.py`\n~~~\nEvidence: confirmed `x.py`\n"
    assert hits(text, skip_lines=frozenset({7})) == []


def test_custom_phrases():
    assert hits("`api.py` works.\n", phrases=("works",)) == [(1, "works")]


def test_applies_to():
    assert applies_to("project") and applies_to("reference") and applies_to(None)
    assert not applies_to("feedback") and not applies_to("user")


def test_more_hedges_are_not_claims():
    for sentence in (
        "The root cause may be the cache in `src/cache.py`.",
        "The root cause could be the cache in `src/cache.py`.",
        "Possibly the root cause is the cache in `src/cache.py`.",
        "Perhaps the root cause is the cache in `src/cache.py`.",
        "The root cause seems to be the cache in `src/cache.py`.",
        "The root cause appears to be the cache in `src/cache.py`.",
        "The retry path in `src/retry.py` is not tested.",
        "`src/retry.py` was never tested.",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_a_named_person_confirming_is_an_attribution_without_by():
    assert hits("Sam confirmed `src/a.py#f` is fixed.\n") == []
    assert hits("Priya Rao confirmed the cache in src/cache.py is broken.\n") == []
    assert hits("She confirmed `src/a.py#f` is fixed.\n") == []


def test_an_unhedged_certainty_claim_is_still_flagged():
    assert hits("The root cause is the cache in `src/cache.py`.\n") == [(1, "root cause")]
    assert hits("`src/a.py#f` is fixed.\n") == [(1, "is fixed")]
    assert hits("`src/retry.py` was tested.\n") == [(1, "tested")]


def test_the_agent_confirming_something_itself_is_still_a_claim():
    assert hits("We confirmed `src/a.py#f` is fixed.\n") == [(1, "confirmed")]
    assert hits("It confirmed `src/a.py#f` is fixed.\n") == [(1, "confirmed")]
    assert hits("The confirmed root cause is in `src/a.py`.\n") == [(1, "confirmed")]


def test_a_pathologically_long_line_is_skipped():
    line = "a.b/" * 20_000 + " verified\n"  # 80k characters that look like the start of a path
    assert hits(line) == []


def test_an_ordinary_long_line_is_still_checked():
    assert hits("The root cause is in `src/a.py`. " + "word " * 500 + "\n") == [(1, "root cause")]


def test_a_pin_or_a_named_check_in_the_same_sentence_credits_the_claim():
    for sentence in (
        "The root cause is `Backoff.next`, reproduced on 2026-08-21.",  # ISO date, bound by a verb
        "The root cause is in `src/retry.py`, introduced at commit 4f9c2ab.",  # commit, 7 hex
        "The root cause is in `src/retry.py` as of b67ced8a1c2f90d3e4a5b6c7d8e9f001a2b3c4d5.",  # 40
        "`src/retry.py` is fixed in v2.3.1.",  # version, bound by "in v"
        "`src/retry.py` is fixed since v0.4.1.",  # version, bound by "since v"
        "`src/retry.py` is fixed in version 3.12.",  # the word version
        "`src/retry.py` is fixed: `pytest tests/test_retry.py` passes.",  # command, check word after
        "`src/retry.py` is fixed: `dotnet build` passed.",
        "`src/retry.py` is fixed: `tests/test_retry.py` passes.",  # test-shaped file
        "`src/retry.py` is fixed: `api.spec.ts` now passes.",  # a spec file, an adverb between
        "`src/retry.py` is fixed, ran `RetryBackoffTests`.",  # a test class, check word before
        "`src/retry.py` is fixed, tested by `RetryBackoffTests`.",  # "tested by"
        "`src/retry.py` is fixed (by running `make test`).",  # "by running"
        "`src/retry.py` is fixed, using `cargo test`.",
        "`src/retry.py` is fixed: ran `tools/probe.sh`, which no longer reproduces it.",  # script
        "`src/retry.py` is fixed, verified with the debugger.",  # verified with
        "`src/retry.py` is fixed, tested by hand.",
        "`src/retry.py` is fixed, checked on the staging box.",
        "Checked by hand: the root cause is in `src/retry.py`.",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_a_bare_claim_with_look_alike_pins_is_still_flagged():
    for sentence in (
        "The root cause is `agent/tools.py` splitting on commas.",  # a source file is not a check
        "The `decade` module is verified.",  # hex letters only: not a commit
        "`src/a.py` is fixed for 12345678 users.",  # digits only: not a commit
        "`src/a.py` is fixed and runs in 3.5 seconds.",  # not a version
        "`src/a.py` is fixed; verified.",  # no "with", "by" or "on"
        "`src/a.py` is fixed, the checked flag is set.",
        "The root cause is `Cache.evict` ignoring the lock.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_source_file_is_never_a_check_unless_its_name_is_test_shaped():
    for name in (
        "src/checkout.py",  # "check" is not a word here
        "src/checksum.py",
        "src/diagram.py",
        "src/demographics.py",
        "examples/demo.py",
        "agent/tools/registry.py",  # a directory called tools
        "src/bin/main.rs",
        "./src/retry.py",
        "tests/helpers.py",  # under tests/, but not a test
        "jest.config.js",
        "scripts/deploy.py",
        "src/check_output.py",
    ):
        sentence = f"`{name}` is fixed, and `{name}` passes."  # even with a check word next to it
        assert len(hits(sentence + "\n")) == 1, name
    for name in ("tests/test_retry.py", "src/retry_test.go", "src/retry.spec.ts", "a/b.test.js"):
        assert hits(f"`src/retry.py` is fixed: `{name}` passes.\n") == [], name


def test_a_command_script_or_test_counts_only_with_a_check_word_next_to_it():
    for sentence in (
        "`npm install` always fails behind the proxy.",
        "`git push origin` is broken.",
        "`make build` never works on Windows.",
        "The root cause is `python build.py` failing under 3.11.",
        "`deploy.sh` is broken on mac.",
        "`docker compose up` does not support profiles.",
        "`curl -x` does not support socks5h.",
        "`api.spec.ts` is fixed.",
        "`TestClient` is fixed.",
        "`src/retry.py` is fixed: see `tests/test_retry.py` and `make test`.",
        "`src/retry.py` is fixed; `tools/probe.sh` shows the loop.",
        "The root cause is two `python build.py` runs in the same directory at once.",
        "The root cause is that `make build` runs twice.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_fails_is_not_a_check_word_but_passes_and_ran_are():
    # A command that is the subject of "fails" is the claim, not the check of one.
    for sentence in (
        "The root cause is that `npm install` fails behind the proxy.",
        "`npm install` fails behind the proxy, and that is verified.",
        "`npm install` always fails behind the proxy.",
        "`pytest tests/test_x.py` failed, and it is the root cause.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence
    for sentence in (
        "`src/retry.py` is fixed: ran `pytest tests/test_x.py`; it passes.",
        "`src/retry.py` is fixed: `pytest tests/test_x.py` passes.",
        "`src/retry.py` is fixed: `pytest tests/test_x.py` passed.",
    ):
        assert hits(sentence + "\n") == [], sentence


# R3-contract: a prerelease version is a version for the pin credit (README: "a version
# number").


def test_a_prerelease_version_is_a_version_pin():
    for version in ("0.3.0b2", "1.2.0-rc.1", "v2.0.0-beta", "1.0.0a1", "1.0.0rc1", "2.1.0.dev3"):
        assert hits(f"`src/a.py` is fixed, tested in {version}.\n") == [], version
        assert hits(f"The release `{version}` is verified.\n") == [], version
        assert hits(f"`{version}` is verified in `src/a.py`.\n") == [], version


def test_a_prerelease_look_alike_is_not_a_version_pin():
    for text in (
        "`src/a.py` is fixed in 1.2.3abc.",
        "`src/a.py` is fixed in 1.2.3x.",
        "`src/a.py` is fixed in 12.3b2.",  # two parts and no leading v: not a version
        "`src/a.py` is fixed and takes 0.3 seconds.",
    ):
        assert len(hits(text + "\n")) == 1, text


# R3-contract: a bracketed verification tag stays with the sentence before it.


def sentences(text):
    """The sentences of one line, tags attached (the segmentation, whatever it credits)."""
    return [sentence.text for sentence in claims._sentences(text.strip())]


def test_a_denial_covers_its_own_clause_and_a_second_claim_after_a_conjunction_still_warns():
    for sentence in (
        "There is no evidence that `a` is slow, and `x` is broken.",
        "It is false that `a` is slow so `x` is fixed.",
        "It is false that `a` is slow but `x` is broken.",
        "It is not true that `a` is slow, yet `x` is broken.",
        "There is no proof that `a` is slow or that `x` is broken.",
        "It is false that `a` is slow, and the root cause is in `x`.",
        "It is not the case that `a` is slow and `x` is fixed.",
        "There is no evidence that `a` is slow yet `x` always fails.",
        "It is false that `a` is slow because `x` is broken.",
        "It is untrue that `a` is slow while `x` is broken.",
        "It is false that `a` is slow whereas `x` is fixed.",
        "It is incorrect that `a` is slow although `x` is broken.",
        "It is false that `a` is slow, however `x` is broken.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence
    # The denied clause itself stays quiet, with or without a trailing conjunction clause.
    for sentence in (
        "It is false that `x` is broken.",
        "It is false that `x` is broken, and `a` is slow.",
        "There is no evidence that `x` is broken or that `a` is slow.",
        "It is not true that `x` is fixed but `a` is slow.",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_a_bracketed_verification_tag_never_forms_or_splits_a_sentence():
    # The tag is the tail of the sentence before it: one sentence, one hit.
    line = "The pitfall in `db.py` breaks writes silently. **[verified]**\n"
    assert sentences(line) == ["The pitfall in `db.py` breaks writes silently. **[verified]**"]
    for tag in (
        "[verified]",
        "**[verified]**",
        "[verified: config.py + user rule]",
        "(verified)",
        "[Verified: see notes]",
        "[checked]",
        "[tested]",
        "[confirmed]",
    ):
        text = f"`db.py` is fixed. {tag}\n"
        assert sentences(text) == [text.strip()], tag
    # A full stop in a file name inside the tag does not end a sentence either.
    text = "`db.py` is broken. [verified: see main.py] `a.py` is fine.\n"
    assert sentences(text) == ["`db.py` is broken. [verified: see main.py]", "`a.py` is fine."]


def test_a_parenthetical_statement_is_not_a_tag():
    # A tag is a bracket or paren that holds a verification word and, at most, a short source.
    # A statement that starts with the same word keeps its own claim and its own credit.
    for text in (
        "(Confirmed in `src/a.py`: the root cause is the cache.)",
        "(Confirmed in `src/a.py`: the root cause is the cache)",
        "(Tested only on Linux; `src/a.py` is broken on Windows.)",
        "(Tested `src/a.py`: it is fixed.)",
        "(Verified: `src/a.py` is broken.)",
        "[Confirmed: `src/a.py` always fails on Windows.]",
        "`b.py` is slow. (Checked twice; the root cause is `src/a.py`.)",
    ):
        assert len(hits(text + "\n")) == 1, text
    # Glued under a dated line, the statement is not credited by that date.
    text = "`x.py` is fixed (verified 2026-10-01).\n(Tested only on Linux; `src/a.py` is broken.)\n"
    assert [(h.line, h.phrase) for h in find_claims(text)] == [(2, "is broken")]


def test_a_short_source_in_a_bracket_or_paren_is_still_a_tag():
    for tag in (
        "(verified)",
        "[verified: config.py + user rule]",
        "[verified: ci.yml + branch protection API]",
        "[verified: comment + code]",
        "**[verified: code comments]**",
    ):  # one sentence with the tag at its end
        text = f"`db.py` is slow. {tag}\n"
        assert sentences(text) == [text.strip()], tag
    for tag in (
        "(verified 2026-10-01)",
        "(tested by Sam)",
        "(verified: ran `pytest tests/test_a.py`)",
        "[verified with the debugger]",
        "(verified by reading main.py)",
    ):  # the tag carries a pin or a named check: the sentence rules credit it
        assert hits(f"`db.py` is slow. {tag}\n") == [], tag


def test_a_tag_with_more_text_after_it_is_read_as_the_writers_own_words():
    # P2 credits a standalone tag (after a full stop, on a line of its own) and a tag that ends
    # the sentence. A tag in the middle of the sentence has text after it: its wording is part of
    # the statement it sits in.
    assert hits("The retry loop in `db.py` [verified] is slow and still failing.\n") == [
        (1, "verified")
    ]
    assert hits("The `db.py` loop [verified: main.py] is slow, and `b.py` is fixed.\n") == [
        (1, "verified")
    ]


def test_a_tag_alone_on_a_line_belongs_to_the_line_above():
    text = "- The retry loop in `db.py` is slow.\n  **[verified]**\n- `a.py` is fine.\n"
    assert hits(text) == []  # P2: the standalone tag credits the line above
    # With nothing directly above it, a tag on its own is no sentence and no claim.
    assert hits("[verified: `a.py`]\n") == []
    assert hits("The loop in `db.py` is slow.\n\n[verified: `a.py`]\n") == []
    assert hits("- [verified]\n") == []


def test_a_tag_inside_the_sentence_that_carries_a_pin_or_a_named_check_credits_by_that_rule():
    # Not P2: the same pin and named-check rules, for a tag that is part of the sentence.
    assert hits("`src/a.py` is fixed [checked 2026-10-01].\n") == []
    assert hits("`src/a.py` is fixed [verified: ran `pytest tests/test_a.py`].\n") == []
    assert hits("`src/a.py` is fixed [verified: tested by Sam].\n") == []
    assert hits("`src/a.py` is fixed [verified: main.py] on Linux.\n") == [(1, "verified")]


def test_a_bracket_that_is_not_a_verification_tag_still_splits_nothing_and_hides_nothing():
    assert hits("`src/a.py` is fixed. [see notes]\n") == [(1, "is fixed")]
    assert hits("`src/a.py` is fixed. [1]\n") == [(1, "is fixed")]


# R3-contract. A sentence is an assertion only when it claims certainty (README, "Limits": a
# certainty phrase that names code). A question, an instruction, a quotation and a negated
# certainty claim nothing. Each exemption is narrow: it covers the phrase inside the question,
# instructed clause, quote or negation, never the rest of the note.


def test_a_question_is_not_an_assertion():
    for sentence in (
        "Has `code/a.py` been tested?",
        "What is the root cause of `route()` failing?",
        "Is `route()` really fixed?",
        "Is `src/a.py` verified",  # inverted auxiliary, no question mark
        "Was `src/a.py` confirmed by anyone",
        "How was `src/a.py` confirmed",
        "Why is `route()` broken",
        "- Does `src/a.py` always fail?",
        "`src/a.py` is fixed?",  # a trailing question mark is a question
        "**Is `src/a.py` tested?**",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_a_question_exempts_only_its_own_sentence():
    assert hits("Is `src/a.py` tested? `src/a.py` is fixed.\n") == [(1, "is fixed")]
    assert hits("Has `src/a.py` been tested? Yes: the root cause is in `src/a.py`.\n") == [
        (1, "root cause")
    ]


def test_an_opener_that_only_looks_like_a_question_is_still_a_claim():
    for sentence in (
        "Was verified in `src/a.py`.",  # telegraphic: no subject follows the verb
        "Is fixed in `src/a.py`.",
        "Has been confirmed in `src/a.py`.",
        "When `src/a.py` fails, the root cause is the cache.",  # a clause, not a question
        "What `src/a.py` returns is verified.",
        "What is verified: `src/a.py` always fails.",  # a label, then the statement
        "Why `src/a.py` is broken: the root cause is the cache.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


# An instruction exempts a certainty phrase only inside the complement of a verification-type
# imperative (check, verify, confirm, ensure, make sure, test, record, document, note whether,
# determine whether). The complement ends at the first clause boundary: "and", "or", "but",
# "because", "since", "which", a relative "that", a parenthesis, ";", ":" or a dash. Every other
# certainty phrase in the sentence is read as an assertion. Ruling: when unsure, warn.
def test_a_verification_instruction_is_not_an_assertion():
    for sentence in (
        "Ensure `code/a.py` is tested before changing it.",
        "Record the root cause in `issue.md` after investigation.",
        "Make sure `src/a.py` is tested.",
        "Make sure `retry()` always succeeds before merging.",
        "Check that `src/a.py` is fixed before merging.",
        "Check that `x` is fixed.",
        "Verify `src/a.py` is fixed.",
        "Verify that `src/a.py` is fixed.",
        "Confirm `src/a.py` is fixed before release.",
        "Test that `src/a.py` always fails on Windows.",
        "Document that `src/a.py` is broken.",
        "Record whether `a` is verified.",
        "Note whether `src/a.py` is fixed.",
        "Determine whether `src/a.py` is broken.",
        "Re-run the suite and confirm `src/a.py` is fixed.",
        "Run it again and make sure `src/a.py` is verified.",
        "Please verify `src/a.py` is fixed.",
        "Always double-check, then verify `src/a.py` is fixed.",
        "1. Be sure `src/a.py` is tested.",
        "**Record** the root cause in `issue.md`.",
        "| Record the root cause in `issue.md` | later |",
        "Do not verify `src/a.py` is fixed without the logs.",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_an_assertion_that_follows_an_instruction_is_still_a_claim():
    for sentence in (
        "Open `src/b.py`; `src/a.py` is fixed.",
        "Record the root cause: the root cause is in `src/a.py`.",
        "Check `src/b.py` \u2014 `src/a.py` is fixed.",
        "Open `src/b.py`, but `src/a.py` is fixed.",
        "Open `src/b.py` and note that `src/a.py` is fixed.",
        "Open `src/b.py`. `src/a.py` is fixed.",
        "Use `src/b.py`, it is the root cause.",  # a new subject starts a new clause
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_reason_or_relative_clause_after_an_instruction_carries_its_own_claim():
    # The instruction exempts what it instructs. A reason or a relative clause is the writer's own
    # statement, so it is read on its own, whatever connector introduces it.
    for sentence in (
        "Avoid `retry()` because it always fails.",
        "Avoid `retry()`, which always fails.",
        "Avoid `retry()` since it always fails on Windows.",
        "Don't call `retry()` as it always fails.",
        "Use `fetch()` instead of `xhr`, which is broken.",
        "Use `src/new.py`, which is fixed.",
        "Do not use `src/old.py` because the root cause is in it.",
        "Call `retry()` knowing it is broken.",
        "Avoid `retry()` that always fails.",
        "Avoid `retry()` so `src/a.py` is fixed.",
        "Ensure `src/a.py` is tested and `src/b.py` is fixed.",
        "Use `src/a.py` or `src/b.py` is broken.",
        "Use `src/a.py` but it always fails.",
        "Avoid `retry()`, who is broken.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_verification_instruction_that_has_a_connector_stays_quiet_up_to_it():
    for sentence in (
        "Check that `src/a.py` is fixed before merging.",
        "Make sure that `src/a.py` is tested.",
        "Verify that `src/a.py` is fixed.",
        "Re-run the suite and confirm `src/a.py` is fixed.",
        "Run it again and make sure `src/a.py` is verified.",
        "Check that `a.py` is fixed. Verify that `b.py` is tested.",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_a_noun_that_looks_like_an_instruction_verb_is_still_a_claim():
    for sentence in (
        "Test results confirmed `src/a.py` is fixed.",
        "Review of `src/a.py` confirmed the root cause is the cache.",
        "Report says `src/a.py` is fixed.",
        "Log shows `src/a.py` always fails.",
        "Check passed for `src/a.py`; the root cause is the cache.",
        "Use of `src/a.py` is verified in staging.",
        "Run 12 of the suite confirmed `src/a.py` is fixed.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_gerund_noun_after_a_verb_looking_word_is_a_subject_not_an_object():
    # "Update handling" is a compound noun, not a verb with an object.
    for sentence in (
        "Update handling is broken in `src/a.py`.",
        "Log shipping is verified in `src/a.py`.",
        "Call routing is broken in `src/a.py`.",
        "Test parsing always fails in `src/a.py`.",
        "Report handling is broken in `src/a.py`.",
        "Save handling is broken in `src/a.py`.",
        "Check pointing is broken in `src/a.py`.",
        "Open sourcing is verified in `src/a.py`.",
        "Find indexing is broken in `src/a.py`.",
        "Use caching is verified in `src/a.py`.",
        "Test running on CI confirmed `src/a.py` is fixed.",
        "Store handling is verified in `src/a.py`.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_an_instruction_that_is_not_verification_type_exempts_nothing():
    # Ruling: a closed list of "instruction" verbs kept re-hiding claims, so any other verb leaves
    # every certainty phrase in the sentence checked. The cost is these false alarms.
    for sentence in (
        "Only call `route()` after its behavior is confirmed.",
        "Do not mark `src/a.py` as verified.",
        "Don't say `src/a.py` is fixed until it is.",
        "Never claim `route()` always fails.",
        "Avoid saying `src/a.py` is fixed.",
        "Always re-run the build until `src/a.py` is verified.",
        "- Remember to mark `src/a.py` as tested.",
        "Label `src/a.py` as the root cause.",
        "Mark `src/a.py` and `src/b.py` as verified.",
        "Record `startedAt` and `endedAt` independently as verified UTC timestamps with evidence.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_compound_verification_instruction_warns_on_its_second_part():
    # Pinned as accepted under the ruling: the complement ends at "and".
    assert hits("Ensure `a` is tested and `b` is fixed.\n") == [(1, "is fixed")]
    assert hits("Check that `a` is fixed and `b` is broken.\n") == [(1, "is broken")]
    assert hits("Verify `a` is fixed, or `b` is broken.\n") == [(1, "is broken")]


def test_a_claim_after_and_or_but_following_an_instruction_still_warns():
    for sentence in (
        "Use `x` and the root cause lies in `y`.",
        "Ensure `a` is tested and the root cause remains `b`.",
        "Avoid `retry()` and the root cause sits in `cache.py`.",
        "Use `x` but the root cause lies in `y`.",
        "Use `x`, and the root cause lies in `y`.",
        "Use `x` and `y` remains the root cause.",
        "Use `x` and `y` stays verified.",
        "Ensure `a` is tested and `b` can be confirmed.",
        "Ensure `a` is tested and `b` gets confirmed.",
        "Use `x` or `y` becomes the root cause.",
        "Use `x` and the whole legacy retry subsystem for the old API is broken.",
        "Avoid `retry()` and Windows always fails.",
        "Use `fetch()` but Safari never works.",
        "Use `x` but production is broken.",
        "Use `x` and CI is broken.",
        "Check `x` and Windows always fails.",
        "Verify `x`, but production is broken.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_relative_that_after_a_noun_is_not_the_complement_of_an_instruction():
    for sentence in (
        "Avoid the test that always fails in `src/a.py`.",
        "Avoid the call that is broken in `src/a.py`.",
        "Don't use the tag that is broken in `src/a.py`.",
        "Use the link that is verified in `src/a.py`.",
        "Delete the flag that always fails in `src/a.py`.",
        "Avoid the update that is broken in `src/a.py`.",
        "Use the set that is verified in `src/a.py`.",
        "Avoid the report that is broken in `src/a.py`.",
        "Avoid the run that never works in `src/a.py`.",
        "Don't read the log that is broken in `src/a.py`.",
        "Replace the mark that is broken in `src/a.py`.",
        "Use the store that is verified in `src/a.py`.",
        "Check the test that always fails in `src/a.py`.",
        "Record the run that is verified in `src/a.py`.",
        "Skip the `x` check that always fails.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_parenthetical_aside_after_an_instruction_is_its_own_claim():
    for sentence in (
        "Avoid `x` (it always fails).",
        "Avoid `x` (the root cause).",
        "Check `x` (it always fails).",
        "Ensure `a` is tested (`b` is broken).",
        "Record `x` (the root cause is in `y`).",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_verification_word_that_is_not_an_imperative_exempts_nothing():
    for sentence in (
        "The test confirmed `src/a.py` is fixed.",
        "Tests record that `src/a.py` is broken.",
        "We verify `src/a.py` is fixed.",
        "I check that `src/a.py` is fixed.",
        "A check that `src/a.py` is fixed passed.",
        "Our record shows `src/a.py` is broken.",
        "Test results confirmed `src/a.py` is fixed.",
        "Record shows `src/a.py` is broken.",
        "Document handling is broken in `src/a.py`.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_verb_that_introduces_an_assertion_is_not_an_instruction():
    # Must keep warning: the certainty phrase is what the sentence says, not what it asks for.
    for sentence in (
        "Note that `retry()` is fixed.",
        "Remember: `retry()` always fails.",
        "Remember that `src/a.py` is broken.",
        "Keep in mind `src/a.py` is broken.",
        "Be aware that `src/a.py` does not support sockets.",
        "Assume `src/a.py` is fixed.",  # a premise carried into memory, not an instruction
        "Treat `src/a.py` as verified.",  # likewise: it sets a premise
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_quoted_certainty_phrase_is_not_an_assertion():
    for sentence in (
        '> The old log said "`route()` is broken"; this quotes the incident.',
        'The old log said "`route()` is broken".',
        "The old log said “`route()` is broken”.",
        'The label "verified" is set in `src/a.py`.',
        '"The root cause is `retry()`", said the old ticket.',
        "> `src/a.py` is fixed.",
        ">> `src/a.py` is fixed.",
        "  > `src/a.py` is fixed.",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_a_certainty_phrase_outside_a_quote_is_still_a_claim():
    for sentence in (
        'The log says "ok" and `src/a.py` is fixed.',
        'The log says "ok", and the root cause is in `src/a.py`.',
        'It said "ok and `src/a.py` is fixed.',  # an unbalanced quote hides nothing
        "He said “ok”; `src/a.py` is fixed.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence
    # Only a line that starts with ">" is a blockquote.
    assert hits("Result > `src/a.py` is fixed.\n") == [(1, "is fixed")]


def test_a_negated_certainty_is_not_an_assertion():
    for sentence in (
        "It is false that `route()` always fails.",
        "`route()` was never confirmed to fail.",
        "It is not true that `src/a.py` is broken.",
        "It is not the case that `src/a.py` does not support sockets.",
        "`src/a.py` is not always verified.",
        "`src/a.py` was not fully tested.",
        "`src/a.py` has not yet been verified.",
        "`src/a.py` wasn't verified.",
        "`src/a.py` hasn't been confirmed.",
        "`src/a.py` is no longer verified.",
        "`src/a.py` was neither verified nor confirmed.",
        "`src/a.py` is not the root cause.",
        "`src/a.py` is not yet the root cause.",  # "yet" here is an adverb, not a connector
        "`src/a.py` isn't fixed.",  # no certainty phrase to begin with
        "`src/a.py` does not always fail.",
        "There is no evidence that `src/a.py` is broken.",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_a_negation_does_not_hide_a_negative_claim_or_a_second_claim():
    for sentence in (
        "`src/a.py` does not support sockets.",  # the claim itself is negative
        "`src/a.py` is not supported on Windows.",
        "`src/a.py` never works behind a proxy.",
        "`src/a.py` always fails.",
        "`src/a.py` was never confirmed and `src/b.py` is fixed.",
        "`src/a.py` is not only tested but also fixed.",  # "only" is no filler: not negated
        "`src/a.py` was not fully tested, and the root cause is in `src/b.py`.",
        "It is false that `src/a.py` is fixed, but `src/b.py` is fixed.",
        "It is false that `src/a.py` is fixed; `src/b.py` is broken.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_long_lines_of_unclosed_tags_quotes_and_negators():
    for line in (
        "`a.py` verified " + "[verified " * 380,
        "`a.py` verified " + "(verified " * 380,
        "`a.py` verified " + "\u201c " * 1900,
        "`a.py` verified " + '" ' * 1901,
        "`a.py` is fixed " + "not the " * 450,
    ):
        assert len(hits(line + "\n")) <= 1


def test_a_line_of_adjacent_emphasised_tags():
    # An emphasis run between two tags can be cut in several ways. A regex that tries every cut
    # grows with the number of cuts; a line may hold about 300 tags, and the save hook has a short budget.
    for unit in ("[verified]***", "[verified]___", "**[verified]**", "_[checked]_", "([tested])**"):
        line = unit * (3900 // len(unit)) + "x"
        assert hits(line + "\n") == []  # no code is named: nothing to claim, and no hang
    line = "`a.py` is fixed. " + "[verified]***" * 290 + "x"
    assert hits(line + "\n") == []  # the tags after the full stop are standalone: P2 credits it


def test_a_claim_followed_by_thousands_of_tag_lines():
    # The first tag line credits the line above; the rest are not joined to anything, so a long
    # run costs a line each and the statement never grows.
    tag = "[verified: " + "by " * 18 + "]\n"
    text = "`a.py` is fixed.\n" + tag * 4000
    assert len(text) > 260_000
    assert find_claims(text) == []
    # The same run under a line that no tag line credits: sibling bullets, then plain lines.
    text = "- `a.py` is fixed.\n" + "- [verified: foo `pytest` bar]\n" * 4000
    assert [(h.line, h.phrase) for h in find_claims(text)] == [(1, "is fixed")]


def test_a_line_of_tags_alone_is_still_a_tag_line_and_a_line_with_more_is_not():
    text = "`db.py` is slow.\n" + "**[verified]** _[checked]_ [tested]***\n"
    assert find_claims(text) == []  # one tag line, directly below: P2 credits the line above
    # A tag line with other words on it is a sentence of its own.
    assert hits("`db.py` is slow.\n[verified] and `a.py` is broken\n") == [(2, "verified")]


# Controls: an unsupported certainty claim keeps warning after every R3-contract change.


def test_an_unsupported_present_tense_certainty_claim_still_warns():
    assert hits("`retry()` is fixed.\n") == [(1, "is fixed")]
    assert hits("The root cause is the cache in `src/cache.py`.\n") == [(1, "root cause")]
    assert hits("`src/a.py` does not support streaming.\n") == [(1, "does not support")]


def test_note_that_and_remember_still_warn():
    assert hits("Note that `x` is fixed.\n") == [(1, "is fixed")]
    assert hits("Remember: `retry()` always fails.\n") == [(1, "always fails")]


# R3-policy P1: a date, version or commit credits a
# claim only when the same sentence also has a verification verb or a provenance marker. An
# unrelated deadline or version no longer credits. (The sixth marker, "per <source>", credits the
# claim by itself under P3 and is tested there.)

_PINS = ("2026-10-01", "4f9c2ab", "v2.3.1", "1.4.2", "version 4.0")
_VERBS = ("verified", "tested", "checked", "confirmed", "reproduced", "measured", "ran", "observed")


def test_a_pin_bound_to_a_verification_verb_credits_the_claim():
    for verb in _VERBS:
        for pin in _PINS:
            sentence = f"`src/a.py` is fixed ({verb} {pin})."
            assert hits(sentence + "\n") == [], sentence
    assert hits("Verified `retry()` on 2026-10-01.\n") == []
    assert hits("`src/a.py` is fixed; Observed with 2026-10-01 data.\n") == []


def test_a_pin_bound_to_a_provenance_marker_credits_the_claim():
    for sentence in (
        "`src/a.py` is fixed as of 2026-10-01.",
        "`src/a.py` is fixed as of 4f9c2ab.",
        "`src/a.py` is fixed at commit 4f9c2ab.",
        "`src/a.py` is fixed at Commit 4f9c2ab.",
        "`src/a.py` is fixed in v2.3.1.",
        "`src/a.py` is fixed in version 4.0.",
        "`src/a.py` is fixed since v0.4.1.",
        "`src/a.py` is fixed since version 4.0.",
        "`src/a.py` is fixed; dependencies are pinned to 1.4.2.",
        "`src/a.py` is fixed with the lockfile pinned at 4f9c2ab.",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_an_unrelated_date_version_or_commit_no_longer_credits_a_claim():
    for sentence in (
        "`route()` is broken and the deadline is 2026-10-09.",
        "`route()` root cause is the cache; upgrade the unrelated CLI to version 4.0.",
        "`src/a.py` is fixed; the release is v2.0.0.",
        "`src/a.py` is fixed, introduced in 4f9c2ab.",  # "in", but not "in v...": no marker
        "`src/a.py` is fixed since 0.4.1.",  # "since", but not "since v...": no marker
        "`src/a.py` is fixed since b67ced8a1c2f90d3e4a5b6c7d8e9f001a2b3c4d5.",
        "The root cause is `Backoff.next`, found on 2026-08-21.",
        "`src/a.py` is fixed, and the deadline is 2026-10-09 by the platform team.",
        "`src/a.py` is fixed, the deadline is 2026-10-09 with no extension.",
        "`src/a.py` is fixed in 1.2.0-rc.1.",
        "The root cause is in `src/a.py`; support ends 2026-12-31 (version 3.1.4).",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_negated_verification_verb_or_a_look_alike_does_not_bind_a_pin():
    for sentence in (
        "`src/a.py` is fixed; the deadline is 2026-10-09, never confirmed.",
        "`src/a.py` is fixed; the deadline is 2026-10-09 and was not checked.",
        "`src/a.py` is fixed; the deadline is 2026-10-09 (auto-verified).",  # a compound word
        "`src/a.py` is fixed; the deadline is 2026-10-09 (`verified`).",  # a code span, not prose
        "`src/a.py` is fixed; the test-ran tool pinned-down 2026-10-09.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_named_check_without_a_pin_still_credits_a_claim():
    # P1 gates dates, versions and commits only; a verb with "with", "by" or "on" and a command
    # beside a check word credit as before.
    assert hits("`src/a.py` is fixed, verified with the debugger.\n") == []
    assert hits("`src/a.py` is fixed, tested by hand.\n") == []
    assert hits("`src/a.py` is fixed: `pytest tests/test_a.py` passes.\n") == []
    assert len(hits("`src/a.py` is fixed; verified.\n")) == 1  # still no named check


# R3-policy P3: "per <named doc or policy>", "by
# convention (<source>)" and "Source: <name>" in the same sentence credit the claim.


def test_per_a_named_doc_or_policy_credits_the_claim():
    for sentence in (
        "`src/a.py` is fixed per `docs/policy.md`.",
        "`src/a.py` is fixed per CONTRIBUTING.md.",
        "`src/a.py` is fixed per the Security Policy.",
        "`src/a.py` is fixed per RFC 7231.",
        "`src/a.py` is fixed per docs/decisions/0007.md.",
        "`src/a.py` is fixed per policy.md.",
        "`src/a.py` is fixed (per the README).",
        "`src/a.py` is fixed per https://example.com/policy.",
        "Per `docs/policy.md`, `src/a.py` is fixed.",
        "`src/a.py` is fixed in 2026-10-01 per `docs/policy.md`.",  # no verb or marker: per alone
    ):
        assert hits(sentence + "\n") == [], sentence


def test_per_something_that_is_not_a_named_source_credits_nothing():
    for sentence in (
        "`src/a.py` is fixed and handles 100 requests per second.",
        "`src/a.py` is fixed and takes 3 ms per call.",
        "`src/a.py` is fixed, one retry per request and one per user.",
        "`src/a.py` is fixed for the limit per `tenant`.",  # a plain identifier is not a document
        "`src/a.py` is fixed per the team.",
        "`src/a.py` is fixed per policy.",  # a policy that is not named
        "`src/a.py` is fixed per it.",
        "`src/a.py` is fixed per The team.",
        "`src/a.py` is fixed per",
        "`src/per_tenant.py` is fixed.",  # "per" inside a name
        "`src/a.py` is fixed (per-tenant).",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_by_convention_with_a_source_credits_the_claim():
    for sentence in (
        "`src/a.py` is fixed by convention (CONTRIBUTING.md).",
        "`src/a.py` is fixed by convention (docs/style.md).",
        "`src/a.py` is fixed by convention [`docs/style.md`].",
        "`src/a.py` is fixed by convention [verified: docs/style.md].",
        "`src/a.py` is fixed by convention **[verified: convention]**.",
        "`src/a.py` is fixed by convention _(Style Guide)_.",
        # The rows of a real policy table: the tag after "by convention" is the source.
        "| `src/migrations/*` | applied in deployed DBs; off-limits by convention "
        "**[verified: convention]** (some devs also enforce it locally) | write a NEW migration |",
        "| `.github/workflows/**` | runs with credentials; off-limits by convention "
        "**[verified: convention]** | propose a diff |",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_by_convention_without_a_source_credits_nothing():
    for sentence in (
        "`src/a.py` is fixed by convention.",
        "`src/a.py` is fixed by convention [verified] and `b.py` is slow.",  # a bare tag: no source
        "`src/a.py` is fixed by convention (we think so).",
        "`src/a.py` is fixed by convention () and `b.py` is broken.",
        "`src/a.py` is fixed (by convention), the root cause is in `b.py`.",
        "`src/a.py` is fixed by convention, see CONTRIBUTING.md.",  # not in the bracket
        "`src/a.py` is fixed; convention (CONTRIBUTING.md) says so.",  # not "by convention"
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_source_label_with_a_name_in_the_sentence_credits_the_claim():
    for sentence in (
        "`src/a.py` is fixed (Source: docs/policy.md).",
        "`src/a.py` is fixed - Source: `docs/policy.md`.",
        "`src/a.py` is fixed **Source:** CONTRIBUTING.md",
        "`src/a.py` is fixed (source: the Security Policy).",
        "Source: RFC 7231 - `src/a.py` is fixed.",
        "`src/a.py` is fixed (Source: https://example.com/policy).",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_source_label_without_a_name_credits_nothing():
    for sentence in (
        "`src/a.py` is fixed (Source: trust me).",
        "`src/a.py` is fixed (Source: the team).",
        "`src/a.py` is fixed (Source: unknown).",
        "`src/a.py` is fixed (Source: TBD).",
        "`src/a.py` is fixed (Source: ).",
        "`src/a.py` is fixed, source code in `src/b.py`.",  # "source" is not the label
        "`src/a.py` is fixed (Resource: docs/policy.md).",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_source_in_another_sentence_does_not_credit_a_claim():
    # P3 is sentence-local; a line below is P2.
    assert len(hits("`src/a.py` is fixed. Per `docs/policy.md`.\n")) == 1
    assert len(hits("`src/a.py` is fixed. Source: docs/policy.md\n")) == 1


def test_a_line_full_of_source_phrases():
    for unit in (
        "per ",
        "per a/b/c/d/ ",
        "Source: ",
        "by convention (",
        "by convention [verified: ",
    ):
        line = "`a.py` is fixed " + unit * (3900 // len(unit))
        assert len(hits(line + "\n")) <= 1
    line = "`a.py` is fixed by convention" + " " * 3900 + "(x)"
    assert len(hits(line + "\n")) <= 1


# R3-policy P2: a claim is credited when its own line
# is directly followed by a line that starts with "Verified:", "Checked:", "Tested:", "Source:" or
# "Evidence (manual):", or by a standalone verification tag. A bullet sub-item directly under the
# claim's bullet that starts with one of those markers also counts. The frozen plan credits any
# standalone tag, a bare "[verified]" included; the stricter option (a tag credits only when it
# names a source or a check) is not implemented.

_MARKERS = ("Verified:", "Checked:", "Tested:", "Source:", "Evidence (manual):")


def test_a_marker_line_directly_below_a_claim_credits_it():
    for marker in _MARKERS:
        for decorated in (
            marker,
            marker.lower(),
            f"**{marker}**",
            f"**{marker[:-1]}**:",
            f"_{marker}_",
            f"  {marker}",
        ):
            text = f"`route()` is fixed.\n{decorated} ran the suite by hand\n"
            assert hits(text) == [], decorated
    # A line that holds two claims is credited as a whole.
    assert hits("`a.py` is fixed. `b.py` is broken.\nVerified: by hand\n") == []
    # A marker below a claim with a pin that binds nothing still credits it.
    assert hits("`a.py` is fixed, deadline 2026-10-09.\nChecked: by hand\n") == []


def test_a_marker_in_the_claims_own_bullet_or_a_sub_item_credits_it():
    for text in (
        "- `route()` is fixed.\n  Verified: ran pytest by hand\n",  # indented continuation
        "- `route()` is fixed.\nVerified: ran pytest by hand\n",  # lazy continuation
        "- `route()` is fixed.\n  - Verified: ran pytest by hand\n",  # sub-item, 2 deeper
        "- `route()` is fixed.\n    * Checked: by hand\n",  # sub-item, 4 deeper
        "1. `route()` is fixed.\n   - Tested: by hand\n",
        "* `route()` is fixed.\n  + Source: the issue tracker\n",
        "  - `route()` is fixed.\n    - Evidence (manual): read the code\n",
        "- The loop is slow.\n  `route()` is fixed.\n  Verified: by hand\n",  # 2nd line of the bullet
        "- Notes\n  - `route()` is fixed.\n    Verified: by hand\n",
    ):
        assert hits(text) == [], text


def test_a_marker_that_belongs_to_another_bullet_does_not_credit_a_claim():
    for text in (
        "- `route()` is fixed.\n- Verified: ran pytest by hand\n",  # a sibling bullet
        "- `route()` is fixed.\n* Verified: ran pytest by hand\n",
        "1. `route()` is fixed.\n2. Checked: by hand\n",
        "  - `route()` is fixed.\n- Verified: by hand\n",  # the parent's sibling
        "  - `route()` is fixed.\n  - Verified: by hand\n",
        "`route()` is fixed.\n- Verified: ran pytest by hand\n",  # the claim is not in a bullet
        "- `a.py` is broken.\n- `b.py` is fine.\n  Verified: by hand\n",  # belongs to `b.py`
    ):
        assert len(hits(text)) == 1, text
    # The bullet that owns the marker is credited: only the claim it follows.
    text = "- `a.py` is broken.\n- `route()` is fixed.\n  Verified: by hand\n"
    assert [(h.line, h.phrase) for h in find_claims(text)] == [(1, "is broken")]


def test_a_line_that_is_not_provenance_does_not_credit_the_claim_above():
    for text in (
        "`route()` is fixed.\nSome other note about it.\n",
        "`route()` is fixed.\nVerified results are in the log.\n",  # no colon: not the label
        "`route()` is fixed.\nSource code is in src/a.py.\n",
        "`route()` is fixed.\nSources: the tracker\n",
        "`route()` is fixed.\nVerified:\n",  # no content
        "`route()` is fixed.\n**Verified:**\n",
        "`route()` is fixed.\n## Verified: by hand\n",  # a heading
        "`route()` is fixed.\n> Verified: by hand\n",  # a quotation
        "`route()` is fixed.\n\nVerified: by hand\n",  # a blank line between
        "`route()` is fixed.\nanother line\nVerified: by hand\n",  # not directly below
        "`route()` is fixed. Verified: by hand\n",  # the same line, not a line of its own
        "`route()` is fixed.\n```\nVerified: by hand\n```\n",  # inside a code fence
        "`route()` is fixed.\n| Verified: by hand |\n",
    ):
        assert len(hits(text)) == 1, text


def test_a_marker_line_above_the_claim_does_not_credit_it():
    # The plan says "directly followed by": a provenance line before the claim is not P2.
    assert (
        len(hits("**Checked:** `pytest tests/test_route.py` passed.\n`route()` is fixed.\n")) == 1
    )
    assert len(hits("Verified: by hand\n`route()` is fixed.\n")) == 1


def test_a_marker_line_that_serves_a_claim_above_is_not_a_claim_for_its_label():
    # "Verified: manually in `src/routes.py`" is provenance: its label asserts nothing when a claim
    # is above it, whatever credits that claim (the marker itself, a pin, a source).
    for text in (
        "`route()` is fixed.\nVerified: manually in `src/routes.py`.\n",  # the line credits it
        "`route()` is fixed.\nTested: by hand against `src/routes.py`.\n",
        "`route()` is fixed in v2.3.1.\nVerified: manually in `src/routes.py`.\n",  # a pin does
        "`route()` is fixed per `docs/policy.md`.\nVerified: manually in `src/routes.py`.\n",
        "- `route()` is fixed in v2.3.1.\n  - Tested: by hand in `src/routes.py`\n",
        "`route()` is fixed.\nVerified: by hand in `a.py`\nChecked: by hand in `b.py`\n",
    ):
        assert hits(text) == [], text
    # Anything else a marker line says is still read: the label is not a pass for the line.
    text = "`route()` is fixed.\nVerified: the root cause is `Cache.evict`.\n"
    assert [(h.line, h.phrase) for h in find_claims(text)] == [(2, "root cause")]
    text = "`route()` is fixed in v2.3.1.\nVerified: the root cause is `Cache.evict`.\n"
    assert [(h.line, h.phrase) for h in find_claims(text)] == [(2, "root cause")]


def test_a_marker_line_with_no_claim_above_is_read_like_any_line():
    # Ruled partially: a label with nothing to serve is read as before, so "Verified: <assertion
    # about code>" still reports, as the save tests rely on. Only a claim above frees the label.
    assert hits("Verified: manually in `src/routes.py`.\n") == [(1, "verified")]
    assert hits("We changed it.\nVerified: manually in `src/routes.py`.\n") == [(2, "verified")]
    assert hits("- Tested: by hand in `src/routes.py`\n") == [(1, "tested")]
    assert hits("**Verified:** by hand in `src/routes.py`\n") == [(1, "verified")]
    assert hits("Verified: `src/a.py` is fixed.\n") == [(1, "verified")]
    # A line that is no marker keeps its words ("Verified `x`" has no colon).
    assert hits("Verified `src/routes.py` manually.\n") == [(1, "verified")]
    # A marker is no certainty phrase unless it is configured as one.
    assert hits("Evidence (manual): read `src/routes.py`\n") == []
    assert hits("Source: the issue tracker, `src/routes.py` section\n") == []


def test_a_standalone_tag_credits_the_claim_it_follows():
    for tag in (
        "[verified]",
        "**[verified]**",
        "_[checked]_",
        "(verified)",
        "[tested]",
        "[confirmed]",
        "[verified: config.py + user rule]",
        "**[verified: code comments]**",
        "[Verified: see notes]",
    ):
        assert hits(f"`db.py` is slow and `a.py` is fixed. {tag}\n") == [], tag  # after a full stop
        assert hits(f"- `a.py` is fixed.\n  {tag}\n") == [], tag  # on the next line
        assert hits(f"- `a.py` is fixed.\n  - {tag}\n") == [], tag  # as a sub-item
        assert hits(f"`a.py` is fixed.\n{tag}\n") == [], tag
    assert hits("`a.py` is fixed. [verified] [checked 2026-10-01]\n") == []


def test_a_tag_that_is_not_standalone_or_not_directly_below_credits_nothing():
    for text in (
        "- `a.py` is fixed.\n- [verified]\n",  # the tag is another bullet
        "`a.py` is fixed.\n\n[verified]\n",  # a blank line between
        "`a.py` is fixed.\nsome other note\n[verified]\n",  # not directly below
        "`a.py` is fixed [verified: main.py] and `b.py` is slow.\n",  # text after the tag
        "`a.py` is fixed. [see notes]\n",  # not a verification tag
        "`a.py` is fixed. (Verified: `a.py` is broken.)\n",  # a statement, not a tag
        "`a.py` is fixed.\n[verified] and `b.py` is broken\n",  # a tag line with more on it
    ):
        assert hits(text), text


def test_a_tag_credits_only_the_sentence_before_it():
    text = "`a.py` is fixed. [verified] `b.py` is broken.\n"
    assert [(h.line, h.phrase) for h in find_claims(text)] == [(1, "is broken")]


def test_a_marker_or_a_tag_does_not_credit_a_claim_in_another_note_section():
    text = "## A\n`route()` is fixed.\n## B\nVerified: by hand\n"
    assert len(hits(text)) == 1


def test_a_hundred_long_lines_with_an_unclosed_code_span_after_a_source_marker():
    for marker in ("per `", "Source: `", "by convention (`", "per the `", "(Source: **`"):
        line = f"`a.py` is fixed {marker}" + "A" * (3990 - len(marker) - 16)
        assert len(line) < 4000
        assert len(find_claims((line + "\n") * 100)) == 100


# P3 name test, narrowed after review. A source is only
# a document: a doc-like code span (a "/" or a document extension, never a source file), a URL, a
# document path, a capitalized phrase that ends in a document noun, or RFC, ADR or PEP and a
# number. It is never the claim's own cited code, and the markers are read in prose only.


def test_per_a_unit_a_proper_noun_or_a_source_file_is_not_a_named_source():
    for sentence in (
        "`retry()` always fails after 3 attempts per API call.",
        "`retry()` always fails after 3 attempts per HTTP request.",
        "`retry()` always fails after 3 attempts per CPU core.",
        "`retry()` always fails after 3 attempts per GPU.",
        "`retry()` always fails after 3 attempts per SQL statement.",
        "`retry()` always fails after 3 attempts per JSON line.",
        "`retry()` always fails after 3 attempts per Docker container.",
        "`retry()` always fails after 3 attempts per Kubernetes pod.",
        "`retry()` always fails after 3 attempts per Tenant.",
        "`retry()` always fails after 3 attempts per Customer.",
        "`retry()` always fails after 3 attempts per `Tenant`.",
        "`retry()` always fails after 3 attempts per `user.id`.",
        "`retry()` always fails after 3 attempts per Priya.",
        "`retry()` always fails after 3 attempts per Claude.",
        "`retry()` always fails after 3 attempts per Slack.",
        "`retry()` always fails after 3 attempts per Google.",
        "`retry()` always fails after 3 attempts per Second.",
        "`retry()` always fails after 3 attempts per Mr. smith.",
        "`retry()` always fails after 3 attempts per Python semantics.",
        "`retry()` always fails after 3 attempts per OWASP guidance.",
        "`retry()` always fails after 3 attempts per SLA.",
        "`retry()` always fails after 3 attempts per TypeScript.",
        "`retry()` always fails after 3 attempts per the style guide.",  # lowercase: not named
        "`retry()` always fails after 3 attempts per the runbook.",
        "`retry()` always fails after 3 attempts per Policies.",  # not one of the nouns
        "`retry()` always fails after 3 attempts per `config.py`.",  # source code is no document
        "`retry()` always fails after 3 attempts per `src/db.py`.",
        "`retry()` always fails after 3 attempts per db.py.",
        "`retry()` always fails after 3 attempts per src/db.py.",
        "`retry()` always fails after 3 attempts per other.py.",
        "`retry()` always fails after 3 attempts per `run.sh`.",
        "`retry()` always fails after 3 attempts per `rm -rf /`.",  # a command, not a path
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_source_label_naming_a_person_a_tool_or_code_credits_nothing():
    for sentence in (
        "`retry()` is fixed (Source: Me).",
        "`retry()` is fixed (Source: Sam).",
        "`retry()` is fixed (Source: Slack).",
        "`retry()` is fixed (Source: Nowhere).",
        "`retry()` is fixed (Source: Tom).",
        "`retry()` is fixed (Source: Rust).",
        "`retry()` is fixed (Source: me).",
        "`retry()` is fixed (Source: the wiki).",
        "`retry()` is fixed (data source: `thing.x`).",
        "`retry()` is fixed (Source: `src/a.py`).",
        "`retry()` is fixed (Source: `a.py`).",
        "`retry()` is fixed (Source: src/a.py).",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_claim_cannot_cite_its_own_code_as_its_source():
    for sentence in (
        "`src/a.py` is fixed (Source: `src/a.py`).",
        "`src/a.py` is fixed (Source: `a.py`).",
        "`Cache.evict` is broken (Source: `Cache.evict`).",
        "`src/a.py` is fixed (source: src/a.py).",
        "`retry()` is fixed - Source: `retry.py`.",
        "`docs/a.md` is broken (Source: `docs/a.md`).",
        "`docs/a.md` is broken per `docs/a.md`.",
        "`docs/a.md` is broken per docs/a.md.",
        "`docs/a.md#L10` is broken per `docs/a.md`.",  # a fragment does not make it another file
        "`./docs/a.md` is broken per docs/a.md.",
        "The root cause is `docs/a.md`, per docs/a.md.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence
    # Another document is a source.
    assert hits("`docs/a.md` is broken per `docs/b.md`.\n") == []
    assert hits("`docs/a.md` is broken (Source: docs/b.md).\n") == []


def test_a_document_is_a_named_source():
    for source in (
        "the Security Policy",
        "our Coding Standard",
        "the Ops Runbook",
        "the API Spec",
        "the Team Handbook",
        "the Incident Playbook",
        "the Project Charter",
        "the Style Guide",
        "the README",
        "README",
        "RFC 7231",
        "ADR-0007",
        "PEP 8",
        "`docs/policy`",  # a "/" in a code span
        "`policy.md`",
        "`docs/guide.rst`",
        "docs/policy.md",
        "./docs/guide.adoc",
        "notes.txt",
        "https://example.com/policy",
    ):
        assert hits(f"`src/a.py` is fixed per {source}.\n") == [], source
        assert hits(f"`src/a.py` is fixed (Source: {source}).\n") == [], source


def test_a_marker_inside_a_code_span_is_not_a_marker():
    for sentence in (
        "`calls per the Security Policy` is broken.",
        "`Source: docs/x.md` is fixed.",
        "`by convention (CONTRIBUTING.md)` is fixed.",
        "`per docs/x.md` always fails.",
        "`src/a.py` is fixed, see `Source: docs/policy.md`.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence
    # The marker is read outside the span; its name may be inside one.
    assert hits("`src/a.py` is fixed, per `docs/policy.md`.\n") == []


def test_a_markdown_link_to_a_document_or_a_url_is_a_named_source():
    # P3 name test (review Minor).
    for source in (
        "[RFC 7231](https://www.rfc-editor.org/rfc/rfc7231)",
        "[the policy](docs/policy.md)",
        "[policy](./docs/policy.md#section)",
        "[the style guide](docs/style/guide)",  # a "/" path, as for a code span
        "[`policy.md`](policy.md)",
        "[wiki](https://example.com/wiki)",
    ):
        assert hits(f"`src/a.py` is fixed per {source}.\n") == [], source
        assert hits(f"`src/a.py` is fixed (Source: {source}).\n") == [], source
        assert hits(f"`src/a.py` is fixed by convention ({source}).\n") == [], source


def test_a_markdown_link_to_code_or_to_the_claims_own_file_is_not_a_source():
    for sentence in (
        "`src/a.py` is fixed per [main](src/main.py).",  # a source file is no document
        "`src/a.py` is fixed per [the api](api).",  # neither a path nor a document
        "`src/a.py` is fixed per [it]().",
        "`src/a.py` is fixed per [policy](docs/policy.md",  # never closed
        "`docs/a.md` is broken per [a](docs/a.md).",  # the claim citing itself
        "`docs/a.md` is broken (Source: [the doc](./docs/a.md#top)).",
        "`src/a.py` is fixed (Source: [Sam](mailto:sam)).",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


# P1, narrowed after review: the claim's own certainty
# phrase never counts as the verification verb that binds a pin.


def test_a_claims_own_certainty_phrase_does_not_bind_a_pin_in_another_clause():
    for sentence in (
        "`route()` is verified; the deadline is 2026-10-09.",
        "`route()` is confirmed fixed; supports version 4.0.",
        "`route()` is tested; the release is v2.0.0.",
        "Verified `route()`; the deadline is 2026-10-09.",
        "`route()` is confirmed; the vendor release 1.4.2 is out.",
        "`route()` is verified; see commit 4f9c2ab for the unrelated change.",
        "The deadline is 2026-10-09; `route()` is verified.",  # the pin may come first
    ):
        assert len(hits(sentence + "\n")) == 1, sentence
    # The same words beside a claim that is not itself a verification: the verb is provenance for
    # the pin in its own clause.
    for sentence in (
        "`src/a.py` is fixed (verified 2026-10-01).",
        "`src/a.py` is fixed, confirmed on 2026-10-01.",
        "`src/a.py` is fixed; tested v2.3.1.",
        "`src/a.py` is fixed, reproduced on 2026-08-21.",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_a_claims_own_certainty_phrase_binds_a_pin_in_its_own_clause():
    # Dated verifications written the usual way keep crediting (README: "checked 2026-10-01 by
    # running `pytest`"; the original corpus case c028): the phrase and the pin share a clause.
    for sentence in (
        "`route()` confirmed on 2026-09-20.",
        "Verified `retry()` on 2026-10-01.",
        "Last verified: 2026-07-23 against `src/a.py`.",
        "(historical; the section below was verified against commits through `3bc3bc96`)",
        "The release `0.3.0b2` is implemented and verified.",
        "The pin `0f72628ce989b00d81f6f9928b320864e00b4c68`, verified against live main.",
        "| correct hash verified, token issued | `236242b` |",
        "Retries verified in `retry()`, checked 2026-10-01 by running `pytest`.",
    ):
        assert hits(sentence + "\n") == [], sentence
    # With custom phrases the claim's phrase is whatever was configured.
    assert hits("`api.py` works, tested 2026-10-01.\n", phrases=("works",)) == []
    assert hits("`api.py` is tested; deadline 2026-10-09.\n", phrases=("tested",)) == [
        (1, "tested")
    ]


def test_an_unrelated_verb_or_a_pin_in_the_same_clause_is_a_known_bypass():
    # Documented limits, accepted by ruling. P1 asks that the sentence holds a verification verb
    # that is not the claim itself, and that the claim's own words reach the pin within a clause.
    # A verb about something else with a pin about something else, or the claim's own words with an
    # unrelated pin in the same clause, still credit. Closing them needs a reading of what each
    # date and verb is about, which would cost "`x` is fixed; verified on 2026-10-01." and
    # "Last verified: 2026-07-23".
    for sentence in (
        "`route()` is broken; the deployment was checked 2026-10-09.",
        "`route()` is verified; we ran the payroll batch on 2026-10-09.",
        "`route()` always fails, though the vendor observed version 4.0.",
        "`route()` is verified, the deadline is 2026-10-09.",  # a comma is not a clause break
        "`route()` is verified and the unrelated CLI moves to version 4.0.",
        "`route()` is verified for the deadline of 2026-10-09.",
    ):
        assert hits(sentence + "\n") == [], sentence


# P2, widened after review: a tag that ends the line, the
# bullet or the sentence credits like a standalone tag. "- `route()` is fixed **[verified: main.py]**"
# is a tag directly after the claim; only a tag with text after it is part of the statement.


def test_a_tag_at_the_end_of_a_line_a_bullet_or_a_sentence_credits_the_claim():
    for text in (
        "- `route()` is fixed **[verified: main.py]**",
        "- `route()` is fixed [verified]",
        "`route()` is fixed (verified)",
        "`route()` is fixed, **[verified]**",
        "`route()` is fixed; [verified: code comments]",
        "`route()` is fixed.[verified]",
        "`route()` is fixed [verified].",
        "`route()` is fixed [verified]!",
        "`route()` is fixed _[checked]_.",
        "`route()` is fixed [tested] [checked 2026-10-01]",
        "1. The root cause is `Cache.evict` [confirmed]",
    ):
        assert hits(text + "\n") == [], text
    # The tag credits the sentence it ends, not the next one.
    assert [
        (h.line, h.phrase) for h in find_claims("`a.py` is fixed [verified]. `b.py` is broken.\n")
    ] == [(1, "is broken")]


def test_a_tag_with_text_after_it_or_a_statement_in_brackets_does_not_credit_the_claim():
    for text in (
        "`route()` is fixed [verified] and `b.py` is slow.",
        "`route()` is fixed [verified] on Linux.",
        "`route()` is fixed (Verified: `route()` is broken.)",
        "`route()` is fixed [see notes]",
        "`route()` is fixed [1]",
        "`route()` is fixed [verified] (see notes)",
    ):
        assert hits(text + "\n"), text


# P3 name test, second round. A code span with a "/"
# is a document only when its last path segment has no extension or a document extension; any
# other extension (source, config, data), a build file such as Makefile, and any line or symbol
# suffix (":42", ":42-50", "::test_it", ":Cache.evict", "()", "#L10", "#name") make it code.


def test_a_code_span_with_a_suffix_or_a_code_config_or_data_extension_is_not_a_document():
    for source in (
        # a line, range or symbol suffix on a source file
        "`src/x.py:42`",
        "`src/x.py:42-50`",
        "`src/x.py::test_it`",
        "`src/x.py:Cache.evict`",
        "`src/x.py()`",
        # source, config, data and web files outside the two extension sets
        "`src/x.mts`",
        "`src/x.cts`",
        "`src/x.zig`",
        "`src/x.sql`",
        "`db/schema.sql`",
        "`config/app.yaml`",
        "`app.json`",
        "`app.toml`",
        "`infra/main.tf`",
        "`web/index.html`",
        "`web/app.css`",
        "`api/schema.proto`",
        "`src/Makefile`",
        "`src/Dockerfile`",
        # a suffix on a document, and on any other file
        "`docs/policy.md:42`",
        "`docs/policy.md#retention`",
        "`docs/policy.md#L10-L20`",
        "`config/app.yaml:12`",
    ):
        assert len(hits(f"`a.py` is fixed per {source}.\n")) == 1, source
        assert len(hits(f"`a.py` is fixed (Source: {source}).\n")) == 1, source
        assert len(hits(f"`a.py` is fixed by convention ({source}).\n")) == 1, source
        assert len(hits(f"`a.py` is fixed by convention [{source}].\n")) == 1, source


def test_a_code_span_path_to_a_document_or_a_directory_is_still_a_document():
    for source in (
        "`docs/policy.md`",
        "`docs/guide.rst`",
        "`docs/notes.txt`",
        "`docs/guide.adoc`",
        "`docs/POLICY.MD`",
        "`docs/decisions/0007-retries.md`",
        "`docs/`",  # a directory
        "`docs/decisions`",  # no extension
        "`./docs/policy`",
        "`.github/policies`",
        "`policy.md`",  # no "/", a document extension
        "`https://example.com/policy.html`",  # a URL, whatever its page ends in
        "`http://wiki.example.com/handbook`",
    ):
        assert hits(f"`a.py` is fixed per {source}.\n") == [], source
        assert hits(f"`a.py` is fixed (Source: {source}).\n") == [], source
        assert hits(f"`a.py` is fixed by convention ({source}).\n") == [], source
    # A link to a document may point at an anchor: that is link syntax, not a symbol suffix.
    assert hits("`a.py` is fixed per [the policy](docs/policy.md#retention).\n") == []
    assert hits("`a.py` is fixed per [the policy](docs/policy.md:42).\n") != []


# P3 name test, third round. A path without an
# extension is a document only when one of its segments is a document directory (docs, doc, adr,
# adrs, rfc, rfcs, policies, policy, runbooks, handbook, wiki, guides). `owner/repo`, `and/or` and
# `TCP/IP` have a "/" and name nothing.


def test_a_slash_token_without_a_document_directory_is_not_a_document():
    for source in (
        "`owner/repo`",
        "`and/or`",
        "`TCP/IP`",
        "`tenant/user`",
        "`host/port`",
        "`read/write`",
        "`IPv4/IPv6`",
        "`16/9`",
        "`src/components`",
        "`scripts/deploy`",
        "`cmd/server`",
        "`src/`",
        "`./`",
        "`/etc/hosts`",
        "`build/output`",
        "`config/env`",
        "`documentation/guide`",  # not one of the listed directories
        "`my-docs/guide`",  # a segment must be the whole name
        "`docs-old/guide`",
    ):
        assert len(hits(f"`a.py` is fixed per {source}.\n")) == 1, source
        assert len(hits(f"`a.py` is fixed (Source: {source}).\n")) == 1, source
        assert len(hits(f"`a.py` is fixed by convention ({source}).\n")) == 1, source
        assert len(hits(f"`a.py` is fixed by convention [{source}].\n")) == 1, source
    assert len(hits("`a.py` is fixed per [the repo](owner/repo).\n")) == 1
    assert len(hits("`a.py` is fixed per `docs`.\n")) == 1  # a word, not a path


def test_a_path_with_a_document_directory_segment_is_a_document():
    for source in (
        "`docs/decisions`",
        "`doc/api`",
        "`adr/0007-retries`",
        "`adrs/0003`",
        "`rfc/7231`",
        "`rfcs/0012`",
        "`policy/retention`",
        "`policies/security`",
        "`runbooks/deploy`",
        "`handbook/onboarding`",
        "`wiki/home`",
        "`guides/setup`",
        "`./docs/policy`",
        "`~/docs/policy`",
        "`/repo/docs/policy`",
        "`src/docs/guide`",  # the segment may be anywhere in the path
        "`.github/policies`",
        "`docs/`",
        "`Docs/Policy`",
    ):
        assert hits(f"`a.py` is fixed per {source}.\n") == [], source
        assert hits(f"`a.py` is fixed (Source: {source}).\n") == [], source
        assert hits(f"`a.py` is fixed by convention ({source}).\n") == [], source
    assert hits("`a.py` is fixed per [the guide](guides/setup).\n") == []


# P3 name test, prose paths. A path in prose follows the
# rule for a code span: a line or symbol suffix (":42", "#L10", "#retention", "::name") makes it a
# location in code, not a document. A markdown link keeps its anchor.


def test_a_prose_path_with_a_line_or_symbol_suffix_is_not_a_document():
    for source in (
        "docs/policy.md:42",
        "docs/policy.md:42-50",
        "docs/policy.md#retention",
        "docs/policy.md#L10-L20",
        "docs/policy.md::section",
        "policy.md#L10",
        "CONTRIBUTING.md:12",
        "./docs/guide.adoc:7",
        "notes.txt:3",
    ):
        assert len(hits(f"`a.py` is fixed per {source}.\n")) == 1, source
        assert len(hits(f"`a.py` is fixed (Source: {source}).\n")) == 1, source
        assert len(hits(f"`a.py` is fixed by convention ({source}).\n")) == 1, source


def test_a_prose_path_that_punctuation_or_words_follow_is_still_a_document():
    for text in (
        "`a.py` is fixed per docs/policy.md.",
        "`a.py` is fixed per docs/policy.md: retention is 30 days.",
        "`a.py` is fixed per docs/policy.md, line 40.",
        "`a.py` is fixed per docs/policy.md line 40.",
        "`a.py` is fixed per docs/policy.md (line 40).",
        "`a.py` is fixed (Source: docs/policy.md).",
        "`a.py` is fixed by convention (docs/policy.md).",
        "`a.py` is fixed per CONTRIBUTING.md; see also `b.py`.",
        # a markdown link keeps its anchor, as for a code span
        "`a.py` is fixed per [the policy](docs/policy.md#retention).",
    ):
        assert hits(text + "\n") == [], text


# P3 self-citation and a second mention of the cited document. The claim's own code is what comes before the marker, or the first code span of the
# sentence. A later mention of the cited document is a second mention of the source, not the claim
# citing itself; a claim about the document that cites it stays uncredited however often it is
# mentioned.


def test_a_second_mention_of_the_cited_document_does_not_make_it_the_claims_own_code():
    for sentence in (
        "`src/a.py` is fixed per `docs/policy.md` (see `docs/policy.md`).",
        "`src/a.py` is fixed per `docs/policy.md`; `docs/policy.md` also lists `retry()`.",
        "`src/a.py` is fixed per `docs/policy.md` and `docs/policy.md#retry`.",
        "`src/a.py` is fixed per docs/policy.md (see `docs/policy.md`).",
        "`src/a.py` is fixed per [the policy](docs/policy.md) (see `docs/policy.md`).",
        "`src/a.py` is fixed (Source: `docs/policy.md`, see also `docs/policy.md`).",
        "`src/a.py` is fixed by convention (`docs/style.md`; see `docs/style.md`).",
        "Per `docs/policy.md`, `src/a.py` is fixed (see `docs/policy.md`).",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_a_claim_about_a_document_that_cites_it_stays_uncredited_when_it_is_mentioned_again():
    for sentence in (
        "`docs/a.md` is broken per `docs/a.md`.",
        "`docs/a.md` is broken per `docs/a.md` (see `docs/a.md`).",
        "`docs/a.md` is broken per `docs/a.md`; `docs/a.md` also lists `retry()`.",
        "`docs/a.md` is broken (Source: `docs/a.md`, see also `docs/a.md`).",
        "The root cause is in `docs/a.md` and `a.py`, per `docs/a.md`.",
        "Per `docs/a.md`, `docs/a.md` is broken.",
        "`docs/a.md` is broken per docs/a.md (see `docs/a.md`).",
        "`docs/a.md` is broken by convention (`docs/a.md`; see `docs/a.md`).",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


# P3, a sentence that starts with its marker. After a
# leading marker the claim follows it, so a later mention of the cited document is either the
# claim's own code ("the root cause is `retry()` in `docs/a.md`") or a second mention of the source
# ("(see `docs/a.md`)"). The claim's own code is what lies in the claim's clause: up to the next
# semicolon, parenthesis, bracket or dash.


def test_a_claim_that_starts_with_its_marker_and_names_the_cited_document_cites_itself():
    for sentence in (
        "Per `docs/a.md`, the root cause is `retry()` in `docs/a.md`.",
        "Per `docs/a.md`, `retry()` in `docs/a.md` is broken.",
        "Per `docs/a.md`: the root cause is in `b.py`, `c.py` and `docs/a.md`.",
        "(Source: `docs/a.md`) The root cause is `retry()` in `docs/a.md`.",
        "By convention (`docs/a.md`), the root cause is `retry()` in `docs/a.md`.",
        "Source: docs/a.md, the root cause is `retry()` in `docs/a.md`.",
        "As noted, per `docs/a.md`, the root cause is `retry()` in `docs/a.md`.",
        # the call's own parenthesis is not a break: it sits in a code span
        "Per `docs/a.md`, the root cause is `retry()` in `docs/a.md`, not `b.py`.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_claim_that_starts_with_its_marker_may_mention_the_cited_document_again_aside():
    for sentence in (
        "Per `docs/policy.md`, `src/a.py` is fixed (see `docs/policy.md`).",
        "Per `docs/policy.md`, `src/a.py` is fixed; `docs/policy.md` also lists `retry()`.",
        "Per `docs/policy.md`, `src/a.py` is fixed [see `docs/policy.md`].",
        "Per `docs/policy.md`, the root cause is in `src/a.py` (`docs/policy.md` lists it).",
        "Per `docs/policy.md`, the root cause is in `src/a.py`.",
        "Per docs/policy.md, `src/a.py` is fixed (see `docs/policy.md`).",
        "Source: `docs/policy.md`, `src/a.py` is fixed (see `docs/policy.md`).",
    ):
        assert hits(sentence + "\n") == [], sentence


def test_the_code_before_the_marker_is_the_claims_own_code_wherever_it_stands():
    # not the sentence's first code span: the cited document is the second of two before "per"
    for sentence in (
        "The root cause is in `a.py` and `docs/a.md`, per `docs/a.md`.",
        "The root cause is in `a.py` and `docs/a.md`, per docs/a.md.",
        "The root cause is in `a.py` and `docs/a.md` (Source: `docs/a.md`).",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


# P1, the two-phrase claim. Each of the claim's own
# certainty phrases that is a verification verb binds a pin only in its own clause, also when the
# claim holds another phrase ("is fixed") as well.


def test_a_claim_with_two_phrases_does_not_bind_a_pin_in_another_clause():
    for sentence in (
        "`route()` is fixed and verified; the deadline is 2026-10-09.",
        "`route()` is broken and confirmed; supports version 4.0.",
        "`route()` is fixed (tested); the release is v2.0.0.",
        "The root cause is in `a.py` (verified); the deadline is 2026-10-09.",
        "`route()` is verified and is fixed; the deadline is 2026-10-09.",
        "The deadline is 2026-10-09; `route()` is fixed and verified.",
    ):
        assert len(hits(sentence + "\n")) == 1, sentence


def test_a_claim_with_two_phrases_binds_a_pin_in_its_own_clause():
    for sentence in (
        "`a.py` is fixed (verified 2026-10-01).",
        "`route()` is fixed and verified (2026-10-01).",
        "`a.py` is fixed; verified 2026-10-01.",
        "`route()` is fixed, confirmed on 2026-09-20.",
        "The root cause is in `a.py`, verified against 4f9c2ab.",
    ):
        assert hits(sentence + "\n") == [], sentence
