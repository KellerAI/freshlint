# Design notes

These notes explain how freshlint decides what a freshness claim covers, when a claim
is honest, and why the escape hatches look the way they do. They describe v0.1.

## Goals and non-goals

freshlint checks that freshness metadata in markdown memory tells the truth about the
content next to it. It is deterministic, offline, read-only, and has no runtime
dependencies.

It does not judge whether a fact is true, and it cannot prove that anyone re-checked
anything. It can only check that dates, content changes and written verification
records are consistent with each other.

## Freshness claims

A claim is a verified date, optionally with a review deadline:

| Where | Syntax |
|---|---|
| Section marker | `<!-- memory: verified=YYYY-MM-DD review_by=YYYY-MM-DD -->` or `... cadence=evergreen -->`, as the first non-blank line after a heading |
| File-level marker | the same comment as the first non-blank body line, before any heading (for files that cannot carry frontmatter) |
| Frontmatter | `last_verified` (or `last-verified`), `review_by` (or `review-by`), `cadence: evergreen` |
| OKF v0.2 frontmatter | `verified` (a `{by, at}` mapping or a list of them; the latest `at` wins), `stale_after`, `status: deprecated` |

A file can have one file-level claim (frontmatter or a file-level marker, not both) and
any number of section claims.

## Section model

* A section is an ATX heading (`#` to `######`) and every line up to the next heading
  of the same or a higher level. Subsections are therefore inside their parent.
* Headings and markers inside fenced code blocks (```` ``` ```` or `~~~`) are ignored.
  Setext headings (underlined with `===` or `---`) are not treated as sections.
* A section is identified by its GitHub-style anchor slug. Duplicate titles in a file
  get `-1`, `-2`, ... in order, as GitHub does.
* **Governance.** Every content line belongs to its nearest enclosing section that
  carries a marker. A marked subsection is governed by its own marker only. Unmarked
  subsections belong to their parent. Lines outside any marked section belong to the
  file-level claim, if there is one.

This differs from the reference implementation it grew out of, which only recognised
`##` headings and treated everything under them (including `###` subsections) as one
body. Nesting by level keeps that behaviour for unmarked subsections while letting a
subsection carry its own claim. Ignoring fenced code avoids false sections in examples.

## Comparing content

Content is compared after removing frontmatter, marker lines and freshlint suppression
comments. With the default `whitespace = "ignore"`, trailing whitespace and blank lines
are also ignored, so a reflowed blank line neither hides laundering nor forces a date
bump. `whitespace = "exact"` compares those too.

Any other difference, even one character, counts as a change. freshlint detects
date bumps with **zero** content change; it cannot tell a meaningful edit from a
cosmetic one. That is a known limitation (see below).

## The two directions

For each claim present in both versions of a changed file:

| Content | Claim | Receipt added in the same diff | Result |
|---|---|---|---|
| changed | verified moved forward | (any) | honest edit |
| changed | verified unchanged | none | `stale-refresh` |
| changed | verified unchanged | `edited` for that target | honest non-substantive edit |
| changed | verified unchanged, already equals today | (any) | honest same-day edit |
| unchanged | verified moved forward | none, or wrong date or target | `freshness-laundering` |
| unchanged | verified moved forward | `verified`, same date, same target | honest re-verification |
| unchanged | only review_by / stale_after moved later, or switched to evergreen | (any) | `freshness-laundering` |
| unchanged | unchanged | (any) | nothing to say |

File-level claims are compared slightly differently in each direction:

* **Laundering** compares the whole body (all sections), because a file-level date
  claims the whole file was checked. Section markers are ignored in that comparison,
  so bumping `last_verified` together with every section marker, and changing nothing
  else, is caught.
* **Stale refresh** only looks at content not governed by a section marker. Editing
  one marked section should move that section's marker, not force a re-claim of the
  whole file, which would itself overstate what was checked.

New sections, new files, deleted sections and deleted files produce no diff findings.
A renamed heading is a new section. A renamed file is compared with its old path
(git rename detection).

## Verification receipts

A receipt is a line in an append-only log (default `VERIFICATIONS.md`):

```markdown
## 2026-09-30
- verified context/shipping.md#carriers — both carrier contracts confirmed on their portals
- edited context/company.md#overview — fixed a typo; facts not re-checked
```

Grammar: `- [YYYY-MM-DD ]verified|edited PATH[#slug] (— | -- | -) NOTE`. A line without
its own date inherits the nearest `## YYYY-MM-DD` heading. Paths are relative to the
project root. A target without `#slug` means the whole file.

Why these rules:

* **Added in the same diff.** A receipt is evidence for one specific bump. An old
  receipt cannot vouch for a new date.
* **Same date as the new verified date.** The receipt says when the check happened;
  the marker repeats it. A mismatch means one of them is wrong.
* **A note is required.** "Verified" alone is just another date. The note says what
  the content was checked against.
* **Append-only.** Editing an old receipt (for example changing its date) would let it
  cover a new bump, so `receipt-append-only` reports edited or removed entries and
  `receipt-ordering` requires dates to be in order.
* **`edited` receipts** cover the opposite case: a wording fix that deliberately did
  not re-check the facts. Without them, the forward rule would push people to bump
  `verified=` on typo fixes, which is a small laundering of its own.
* **File-level receipts cover sections.** `- verified a.md — full review` covers the
  file-level claim and every section claim in `a.md` with the same date. A section
  receipt does not cover the file-level claim.

The receipt log can live anywhere and can be a glob (`evidence/*.md`). Logs ordered
newest first, like OKF `log.md`, are supported with `order = "descending"`.

## Two escape hatches, on purpose

`cadence=evergreen` is a permanent, typed claim about the content: this fact does not
decay. It still carries a verified date and is still checked for laundering and stale
refreshes. It is only exempt from expiry. Switching a dynamic section to evergreen
without changing it is reported as laundering, because it removes the review date.

An inline suppression is a temporary, reasoned waiver of one rule at one place, for a
condition that is expected to go away. It must have a reason and an expiry no more
than `max_suppression_days` (default 90) away, and it can only waive rules that
describe a persistent state (`review-expired`, `missing-marker`, `temporal-language`).
Diff rules are one-time events and are resolved with receipts instead.

Keeping these apart stops the permanent exemption from being used as a quick silencer,
and stops the quick silencer from becoming permanent.

## Modes

* `freshlint check` reads the working tree (respecting `.gitignore` inside git) and
  runs the state rules on every governed file and receipt log.
* `freshlint check --staged` compares the index with `HEAD` (or the empty tree before
  the first commit). This is the pre-commit mode.
* `freshlint check --diff BASE...HEAD` compares the merge base with `HEAD`;
  `BASE..HEAD` compares the two commits directly; a single `BASE` means `BASE...HEAD`.

In both diff modes, state rules run only on changed files, so a pull request is not
blocked by an unrelated section that expired last week. Expiry across the whole tree is
the job of a scheduled full scan; the example workflow runs both.

## Paths and the project root

The project root is the directory holding the config file (or the git top level, or
the current directory, if there is none). Globs and receipt targets are relative to
it, so a memory can live in a subdirectory of a larger repository. Reported paths are
relative to the git top level, which is what GitHub annotations need.

## OKF v0.2 support level

Supported: `verified` as a list or a bare mapping (latest `at` is the verified date),
`stale_after` (day granularity, UTC), `status: deprecated` (no expiry).

Not interpreted in v0.1: `generated`, `sources`, trust tiers, attested computations,
`log.md` structure beyond receipt ordering.

Open decision: OKF allows re-confirming a concept by appending a `verified` event
without changing the content. freshlint v0.1 does **not** treat a new `verified` event
as a receipt, because an event records who and when but not what was checked. A
project that trusts its OKF events can downgrade the rule with
`[rules] freshness-laundering = "warn"`, which in v0.1 applies to every file; a
per-format option is on the roadmap.

The frontmatter reader handles the YAML subset that freshness metadata uses (scalars,
quoted strings, flow mappings and lists, block lists of mappings). Anything else in
frontmatter is ignored, never rejected.

## Determinism

No network access, no writes, no clock reads except `datetime.date.today()` when
`--today` is not given. Output is sorted by path, line and rule.

## Known limitations

* A one-character edit counts as a content change, so laundering can be disguised as a
  trivial edit. Reviewers still need to read diffs.
* Receipts are claims too. freshlint checks that they exist, match, and are not
  rewritten; it cannot check that the work they describe happened.
* Section identity is the heading slug. Renaming a heading resets its history.
* Setext headings and headings inside HTML blocks are not recognised.
* `--today` is a calendar date; OKF instants are compared at day granularity in UTC.
* Merge commits are compared against their merge base like any other range.
