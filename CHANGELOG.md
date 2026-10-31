# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Rule IDs are part of the public interface:
renaming or removing one is a breaking change.

## [0.2.0] - 2026-10-30

### Added

- Claude Code plugin (`.claude-plugin/`, `hooks/`, `skills/memory-evidence`,
  `commands/memory-check.md`, `bin/freshlint`). Claude Code namespaces the command as
  `/freshlint:memory-check`, and the plugin's messages use that name.
- Evidence lines in memory notes, with file, line-range, symbol and key citations.
- Fingerprints for cited code (hashes only), relocation for moved line ranges, and
  standard-library symbol locators for Python and brace languages.
- PostToolUse checks when a memory note is saved: uncited certainty claims, unread citations,
  code changed since it was read, missing targets. Feedback only; nothing is blocked.
- Feedback when `Evidence:` sits inside a sentence instead of starting a line (nothing is
  cited then), and a low-confidence `misplaced-evidence` finding in `freshlint memory scan`.
- SessionStart report of memories whose evidence changed, is gone, or was never read.
- The vouching rule: evidence is renewed only after the agent read or edited the cited file in
  the same session, with an append-only receipt per renewal.
- Inferred, file-level checks for memories written before install, using git history.
- `freshlint memory scan`, `freshlint memory check` and `freshlint hook`.
- Opt-in local event log (`FRESHLINT_EVENTS=1`) for evaluating the plugin. A `session_report`
  event says whether the agent saw the report (`shown`: false when a resume or compact
  repeated it, or when there was nothing to say).
- Python 3.14 in the CI matrix and the package classifiers.
- An advisory lock (`.freshlint/lock.lock`, do not commit it) around the hooks' load, change
  and save of `lock.json`, so two parallel saves keep both records.

### Changed

- README and SECURITY.md say what freshlint writes and keeps (the `.freshlint/` files, the
  session ledger with the paths and byte hashes of files the agent read, `hook.log`), that
  fingerprints of short values can be guessed, that `freshlint memory check` in CI needs a
  committed `.freshlint/lock.json`, and that `CLAUDE_CONFIG_DIR` is not honored.
- A citation that cannot be fingerprinted (a missing symbol, key or line range, a directory) is
  documented as kept without a fingerprint, so it shows as unread at session start.
- **A citation covers the files it names and the claims written next to it (reverses the
  2026-10-02 ruling "any valid citation retires all inferred findings").** A note that cites code is no longer skipped by file-level
  tracking and by the uncited-claim check. A mentioned path that no citation covers keeps its
  baseline, and its `gone` and `inferred-changed` findings, whatever else the note cites; adding,
  renewing or removing a citation of another file never deletes or re-baselines it. A claim
  sentence is covered, and not flagged, only when it names a cited file, symbol or key (in
  backticks, or as a path with a `/`, a `.` or a `:line` suffix: a bare word such as `src` names
  nothing), or when a parsed Evidence line is in its paragraph or is the first non-blank line
  after it (a claim next to an Evidence line is covered whatever that line cites). Coverage is
  bounded and syntactic: it never asserts that the cited code supports the claim, so an
  adjacent but irrelevant Evidence line still covers the claims in its paragraph.
- A mention is handed over to a citation only once a citation of that file has a fingerprint, so
  adding an unread citation and removing it again cannot launder a stale mention. While a
  citation of the same file is unread or names a missing file, a note whose mention already had a
  baseline shows both the citation's `unread` and the mention's `gone` or `inferred-changed`.
- The baseline of a mentioned file is its content at the last commit, on or before the note's
  time, that touched that path, answered per path, so a repository with merge commits gets the
  same answer as one without. Explicit citations are checked before any inferred baseline.
- One-time cost after upgrading: the first session takes baselines for the paths that notes with
  citations mention but do not cite (they were skipped before). The startup budget is 3 seconds
  and a scan that stops early carries on in the next session.
- Uncited-claim rules, **contract** (the README's headline and sentence rule already promised
  these; items 1, 2, 3 and 5 are *contract (headline inference)*, items 4 and 6 are directly
  documented, item 4 only for "not tested" and "never tested"): (1) a question is not an assertion; (2) an instruction to verify exempts only the
  complement of its verb, up to the first clause break; (3) a quoted phrase and a `>` blockquote
  line are not assertions; (4) a negated or denied certainty is not an assertion; (5) a bracketed
  verification tag attaches to its sentence and never forms or splits one; (6) prerelease
  versions (`0.3.0b2`, `1.2.0-rc.1`) are versions.
- Uncited-claim rules, **policy**: This policy is a default; feedback welcome.
  (P1) a date, version or
  commit credits a claim only when the sentence also holds a verification verb or a provenance
  marker ("as of", "at commit", "pinned", "in v...", "since v...", "per <source>"), and the
  claim's own certainty phrase binds a pin only within its own `;` clause; (P2) a directly
  following `Verified:`, `Checked:`, `Tested:`, `Source:` or `Evidence (manual):` line, or a
  standalone verification tag, credits the claim; (P3) "per <doc>", "by convention (<source>)"
  and "Source: <name>" credit it, where a source is a document, never the claim's own code.
- Documented bypasses and limits of the claim rules (each described in the README, section
  "Uncited claims"): an adjacent but irrelevant Evidence line covers a claim; an unrelated
  verification verb credits an unrelated pin, across `;`, and the claim's own phrase credits one
  after a comma or "and"; a provenance marker binds an unrelated pin; a comma splice after a
  verification instruction is not flagged; a bare `[verified]` tag credits (a stricter option, a
  tag that must name a source or a check, has been considered, is not adopted and is undecided);
  empty provenance values credit ("Verified: no", "[verified: guess]"); a marker line that states
  a claim is credited by a marker below it; a URL permalink to code credits; `.txt` counts as a
  document; and the question and quote exemptions hide some real claims (tag questions, scare
  quotes, inch marks, lines that start with `>`, a clause that opens like a question). A note
  with a very large number of uncited claims is slow to check.
- C and C++ declaration prefixes are tracked, and anything uncertain falls back to the file
  (see Fixed). Macros defined in another file are not seen, and free functions, `using` and
  `typedef` aliases, unions and concepts always fall back.
- **After upgrading, a stored fingerprint of a C or C++ symbol whose tracked span grew (a
  `requires` clause, `alignas`, a multi-line template header, a qualifier line) or shrank reads
  `changed` once**, until the agent reads the code and saves the note again. Two more cases read
  `changed` once the same way: a C or C++ symbol that the scan now refuses (any shape listed
  above, and so tracked at file level; the message says "tracked at file level"), and a C# or
  Swift symbol with `#if` or `#region` lines among its attributes, whose span now takes in those
  lines and the attributes above them. A fingerprint of a symbol under a multi-line attribute,
  which used to fall back to the file, keeps comparing at file level until renewed. It is never a
  false `fresh`.

### Fixed

- First-run summary now shows the first time freshlint scans a memory folder, even if a save
  happened first.
- `bin/freshlint` starts Python with `-P`: a `types.py` or `json.py` in the project no longer
  makes the hook exit 1 or go silently inert. It also finds `python3.14`.
- A cited file in an unreadable directory is reported as "cannot be read", never as no longer
  existing, on Python 3.14 as well as on earlier versions. The same rule now holds for the
  evidence store, inferred mentions and the note listing.
- A symbol in a brace language is never reported as gone or "not defined" because a regex
  literal with a backtick or `/*` confused the scanner: it falls back to file-level tracking
  unless the name is absent from the file.
- A `changed` finding, and the save-time feedback, say "tracked at file level" when a symbol
  could not be isolated, also when it was isolated when the memory was written and the scan
  refuses it now.
- Re-saving an older memory without reading anything no longer clears its inferred findings.
  Each mentioned file keeps its own baseline until the mention leaves the note or a citation
  with a fingerprint covers it.
- Files that git ignores (build output, caches) are no longer inferred citations, so a clean
  build no longer reports them as gone. A mention git cannot answer for (beyond a symlink,
  inside a submodule) no longer switches that off for the whole scan; the batch is split and
  asked again, at most 16 extra git calls per scan.
- "may", "could", "possibly", "perhaps", "seems", "appears", "not tested", "never tested" and
  "<Name> confirmed ..." no longer count as unchecked certainty claims.
- The first-session summary no longer counts mentioned files that changed (a file-level estimate
  that was too often not a real problem); those stay in `/freshlint:memory-check`.
- The first-session summary is left out when it would count nothing (no missing files, no
  uncited claims), instead of ending with a call to action over two zeros.
- A mentioned file that no longer exists is not reported when every sentence that names it says,
  in the past tense, that it was deleted, removed, moved, renamed or retired (or is gone). A
  negated, conditional or future statement, a question, a deletion that was restored or
  reverted, and a deletion whose subject is something else ("`a.py` calls the shim, which was
  removed") or that the path itself carries out ("`a.py` moved the files") do not hide it. The
  check does not split sentences after e.g., i.e., vs. or etc., and skips paragraphs over 4000
  characters. Those words are read in prose only: a code span, or a file name such as
  "`TODO.md`", "`how-to.md`" or "`scripts/restore.sh`", does not make its sentence a to-do, a
  question or a restored deletion. Two words joined by a slash ("if/when", "TODO/FIXME") are
  prose, and still count.
- An `unread` finding for a citation freshlint has no record of (a note written before install,
  or a first scan) says it has no saved fingerprint yet and how to create one, instead of
  claiming the file was not read; a citation the save hook recorded without a read keeps the
  "not read when the memory was saved" wording.
- A certainty sentence is no longer flagged when it carries its own pin or named check: an ISO
  date, a commit, a version number, or "verified", "tested" or "checked" followed by "with", "by"
  or "on". A test, script or command in backticks counts only with a check word next to it
  (ran, running, passes, with, via, using), and a source file never counts unless its
  name is test-shaped. (The pin part is narrowed again below: a date, commit or version now
  credits only when the sentence binds it to verification.)
- Save feedback for a cited file that does not exist says so ("that file does not exist" or "no
  longer exists"), instead of asking the agent to read it.
- An `unread` finding for a citation the agent read but that could not be fingerprinted (a symbol
  that is not there, a file that changed after the read) says so instead of "was not read", and
  one for a cited file that does not exist says the file does not exist. The lock file records
  the reason (`problem`) for the first case; older lock files load unchanged.
- A closed output pipe no longer makes the hook exit 120.
- A `SyntaxWarning` from a cited Python file no longer reaches the output.
- The hooks no longer refresh a repository's index or take `.git/index.lock`.
- A brace-language declaration that ends in `typeof`, `await`, `new`, `void`, `in`,
  `instanceof` or `delete` is no longer cut short.
- A very long line (over 4,000 characters) no longer slows the certainty-claim check.
- One unrelated citation no longer silences a note. It used to hide the `gone` and
  `inferred-changed` findings of every file the note mentions and every uncited claim in it.
- Editing only a line above a C or C++ declaration that belongs to it (a `requires` clause, a
  `template <...>` header, `[[...]]`, `alignas`, `__attribute__`, `__declspec`, `export`,
  `inline` or `constexpr` on its own line, a trailing `requires`, `noexcept`, `const`, `override`
  or `final`) is no longer read as `fresh` for the forms the scan reads: the lines join the
  tracked span, or, when freshlint is not sure what a line is (a bare identifier above the
  declaration, a prefix line that names a macro the same file `#define`s, a conditional `#if`, a
  line splice, an unbalanced `#define`, an `#include` or `import` header name with a comment
  opener, a NUL byte anywhere in the file, a line separator other than LF, CR or CRLF, a digraph
  or trigraph), the citation falls back to file level with the "tracked at file level"
  disclosure. One shape is deliberately outside the span: a bare macro, identifier or `NAME(args)`
  line that the file does not `#define`, separated from the declaration by a blank line. An edit
  to it is not seen.
- A multi-line annotation or attribute above a declaration (Rust `#[...]`, Java, Kotlin,
  TypeScript and Swift `@Annotation(...)`, C# `[Attr]`) joins the span instead of making the
  citation fall back to the file.
- A C or C++ name that a backslash line splice splits over two lines is no longer reported as
  gone.
- A question, an instruction to verify, a quotation, a negation, a prerelease version and a
  bracketed verification tag no longer cause or split uncited-claim warnings (the contract
  items above).

## [0.1.0] - not released

First version. Not published.

### Added

- `freshlint check` with three modes: full scan of the working tree, `--staged`
  (index vs `HEAD`) and `--diff RANGE` (`A...B`, `A..B`, `A`).
- Freshness claims from section markers (`<!-- memory: verified=... review_by=... -->`,
  `cadence=evergreen`), file-level markers, frontmatter (`last_verified`, `review_by`,
  `cadence`) and OKF v0.2 `verified` / `stale_after` / `status`.
- Rules: `review-expired`, `missing-marker`, `invalid-marker`, `temporal-language`,
  `suppression`, `receipt-ordering`, `freshness-laundering`, `stale-refresh`,
  `receipt-append-only`.
- Verification receipts (`verified` and `edited`) in append-only markdown logs.
- Inline suppressions with a required reason and a bounded expiry.
- Output formats: `text`, `json` (schema 1), `github` annotations. `--today` for
  reproducible runs. Exit codes 0 / 1 / 2.
- Configuration in `.freshlint.toml` or `[tool.freshlint]`.
- pre-commit hook, composite GitHub Action, example workflow, and a demo with a
  fictional company memory.
