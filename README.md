# freshlint

**Memory your agent can check.** freshlint ties each Claude Code memory to the code it was
based on, tells the agent at session start which memories rest on code that has changed or
gone, and pushes back when the agent saves a memory that claims certainty without evidence.

It runs as a Claude Code plugin, a CLI, or a CI check. It is deterministic, makes no network
requests, never stores your code (only hashes), and has no runtime dependencies beyond
Python 3.11. It uses `git` when it is available (to see what an older memory's files looked like
and when a note was written) and works without it, using file modification times instead.

> Status: v0.2.0, early release, open to feedback.

## Why

Agents reload their memory in every session. When a memory is wrong, every later session
inherits the mistake. People report agents saving guesses as "verified" root causes, treating
a one-off error as a permanent limit, and following notes about code that has since moved.
The usual fix is turning memory off.

What people ask for is memory that records what it was based on and notices when that changes.
GitHub Copilot Memory does this for Copilot. freshlint does it for Claude Code, locally.

## Claude Code plugin

```text
/plugin marketplace add KellerAI/freshlint@v0.2.0
/plugin install freshlint@freshlint
```

To try it for one session without installing: `claude --plugin-dir /path/to/freshlint`.

What it does:

- **When the agent saves a memory**, it checks the note. A sentence that says "confirmed" or
  "root cause" about code and that no citation covers (see [Uncited claims](#uncited-claims)),
  or a citation of code the agent has not read, gets one line of feedback per problem. Claims
  are checked sentence by sentence, also in a note that cites other code. Nothing is blocked.
- **At session start**, it checks every memory's evidence and adds a short report (at most ten
  lines, and nothing at all when everything is fine, apart from a one-line summary in the
  first session that appears only when it counts a missing file or an uncited claim).
- **An agent can only vouch for code it looked at.** A memory's fingerprints are renewed only
  if the agent read or edited the cited file in the same session, and each renewal is logged
  in an append-only receipt file.
- **Paths a note mentions but does not cite** are checked too: in an older memory that has no
  Evidence line, and in a note whose citations name other files. freshlint uses git history to
  see what a mentioned file looked like when the memory was written (file modification times
  when git has no record). A citation covers the files it names and the claims written next to
  it; coverage is syntactic (see [What a citation covers](#what-a-citation-covers)).

The plugin needs Python 3.11 or newer on `PATH`; without it, the plugin says at session start
that it is inactive. It is built not to interrupt a session: `freshlint hook` exits 0 whatever
goes wrong inside it (a broken install, a closed output pipe, a file named `types.py` in your
project) and writes the error to a log instead. A hook that Claude Code or the operating system
kills (on a timeout, say) is outside that promise. It finds the project from
`CLAUDE_PROJECT_DIR`, which Claude Code sets, rather than from wherever the agent has changed
directory to.

### Citing evidence

A memory cites evidence on an `Evidence:` line, next to the `Why:` and `How to apply:` lines
Claude Code memories already use:

```markdown
**Evidence:** `src/auth/session.ts#refreshToken`, `package.json#scripts.test`, `docs/api.md#L10-L24`
```

Write each citation in backticks on the `Evidence:` line; a bare path on that line is ignored.
The label must start its own line (a list bullet or bold is fine). `Evidence:` in the middle of a
sentence is not read as evidence; when a `project`, `reference` or untyped note is saved that
way, freshlint says so, and `/freshlint:memory-check` lists it as low confidence.

| Form | Fingerprints |
|---|---|
| `path/to/file` | the whole file |
| `path/to/file#L10-L24` | those lines, found again if they move (see Limits) |
| `path/to/file.py#name`, `#Class.method` | that symbol (Python; brace languages such as TypeScript, Go, Rust, Java, C# and C/C++, see Declaration prefixes) |
| `config.json#a.b`, `pyproject.toml#tool.x` | that key's value |

Trailing whitespace, line-ending style and blank lines at the start or end of the cited text
do not count as changes. Any other edit does, including re-indenting. A symbol in a language
freshlint does not parse, or one it cannot locate with confidence, is tracked as the whole
file, and a finding about it says "tracked at file level". A citation that cannot be
fingerprinted (a symbol, key or line range that does not exist, or a directory) is kept without
a fingerprint: freshlint reports it when the note is saved ("is not defined in ..."), and from
then on it shows as unread at every session start, until the agent reads code that exists and
saves the note again. A citation with no saved record at all (a note written before install, or
a first scan) is reported as having no saved fingerprint yet: that does not say it was not read,
only that freshlint cannot tell; reading it and saving the note fingerprints it.

#### Declaration prefixes

A symbol citation tracks the declaration and the lines directly above it that belong to it, so
that, for the forms listed here, editing only such a line is not read as `fresh`:

- **Rust, Java, Kotlin, TypeScript, Swift and C#:** attributes and annotations (`#[...]`,
  `@Annotation(...)`, `[Attr]`), also one that spans lines, when its brackets balance and the
  group holds nothing but attributes. `#if` lines directly around an attribute join in C# and
  Swift.
- **C and C++** (`.c`, `.h`, `.cc`, `.cpp`, `.hpp`): `template <...>` headers (one line or
  several, angle brackets balanced); `requires` clauses with their continuation lines;
  `[[...]]`, `alignas(...)`, `__attribute__((...))` and `__declspec(...)`; `export`, `inline`
  and `constexpr` on a line of their own; and a `requires`, `noexcept`, `const`, `override` or
  `final` on the line after the declarator.

When freshlint is not sure what a line above or inside a declaration is, it does not guess: the
citation is tracked at the whole file and says so ("tracked at file level"). A file-level
citation reports any edit to the file, so it can report a change that is not in the symbol.
In C and C++ this happens for:

- an uncertain prefix: a template header or `requires` clause that does not balance or ends on
  `&&`, `||`, `,`, `<`, `(` or `=`; a bare identifier, keyword or `NAME(args)` alone on a line
  (`EXPORT_API`, `static`, `DECL(a, b)`) directly above the declaration or its prefix, or one
  that the same file `#define`s; a `requires` expression with a body; a prefix with braces in it
  (`T{}`);
- a line of the prefix that names an identifier the same file `#define`s (`requires Small<T> &&
  LIMIT` with a `#define LIMIT ...` anywhere in the file, or a `template <typename T = DEFAULT_T>`
  parameter): the macro may expand to part of the prefix, its `#define` is outside the span, and
  freshlint does not expand macros, so an edit to it would otherwise read as `fresh`;
- a conditional directive (`#if`, `#ifdef`, `#ifndef`, `#elif`, `#else`, `#endif`) between the
  code line above the declaration and the declaration, or inside it: freshlint does not run the
  preprocessor, so it cannot tell which branch is live;
- a backslash line splice outside a preprocessor line, in code or in a comment;
- a `#define` whose braces do not balance;
- an `#include` header name that contains `/*` or `//`, and a NUL byte or U+180E anywhere in the
  file;
- a C++20 header-unit `import <a/*b>;` or `import "a//b";` whose header name contains `/*` or
  `//` (an import is code, not a preprocessor line, so freshlint reads every line that holds the
  word `import`, and a comment or string that spells one refuses too);
- a line separator other than LF, CR or CRLF (a form feed, for example), a digraph (`<%`, `%>`,
  `%:`), or a trigraph for a brace, a bracket, `#` or a backslash;
- a few similar shapes where the scan cannot tell how a compiler would read the text (an unclosed
  quote on a preprocessor line or before a comment opener, a raw string over several lines, a
  preprocessor line between a declarator and its body, a parenthesised `requires` clause after a
  declarator, which hides the declaration below it). The module docstring of
  `src/freshlint/symbols.py` describes the shapes the scan checks for.

Free functions, `using` and `typedef` aliases, unions and concepts in C and C++ are not
isolated at all, so they always fall back to the file.

One shape is deliberately not refused, and an edit to it is not seen: a bare identifier (not a
keyword) or `NAME(args)` line that this file does not `#define`, with a blank line between it and
the declaration (or its prefix) and a statement end, a block boundary or the start of the file
above it, is taken for an unrelated line and stays outside the span (a comment line does not
separate). Such lines are usually unrelated macros, so refusing them would cost more fallbacks.

freshlint does not preprocess or expand macros: a macro that another file defines, and that
expands to prefix text or opens or closes a body, is not seen. A fingerprint stored before this
version reads `changed` once after upgrading, until the agent reads the code and saves the note
again, in three cases: a C or C++ symbol whose tracked span grew (a `requires` clause, `alignas`,
a multi-line template header, a qualifier line); a C or C++ symbol that the scan now refuses, for
any shape listed above, and so tracks at file level (the message says "tracked at file level");
and a C# or Swift symbol with `#if` or `#region` lines among its attributes, whose span now takes
in those lines and the attributes above them.

#### What a citation covers

A citation covers the files it names and the claims written next to it. Coverage is bounded and
syntactic, judged per path and per claim. This rule replaces an earlier behaviour, in which a note
with any valid citation was skipped by file-level tracking and by the uncited-claim check.

- **Per path.** A path the note mentions but no citation names keeps its file-level tracking
  whatever else the note cites: its baseline survives, and `gone` and `inferred-changed` are
  reported as before. Adding, renewing or removing a citation of another file never deletes or
  re-baselines it. A mention is handed over to a citation only once a citation of that file has a
  fingerprint, that is, once the agent has read the file and saved the note. Until then the
  mention keeps its record, so adding an unread citation and removing it again cannot launder a
  stale mention. While a citation of the same file is unread, or names a file that no longer
  exists, the note shows both findings if the mention already had a baseline; a mention with no
  baseline yet shows only the citation's `unread`.
- **Baseline.** The baseline of a mentioned file is its content at the last commit, on or before
  the note's time, that touched that path. Each path is answered on its own, so a repository
  with merge commits gets the same answer as one without. A path that no commit touched, or that
  the commit deleted, falls back to its modification time if the file exists now, and has no
  record if it does not.
- **Per claim.** The uncited-claim check runs for every claim sentence, also in a note that has
  citations. A claim is covered, and not flagged, when (i) its sentence names a file that a
  parsed citation names (in backticks or as a path-like token: one with a `/`, a `.` or a
  `:line` suffix, so a bare word such as `src` or `test` names nothing), or names, as a code
  span, a symbol or key that a citation cites; or (ii) a parsed Evidence line is in its
  paragraph, or is the first non-blank line after it. A paragraph is a run of non-blank lines
  outside code fences: a heading ends it, a code fence breaks it, and every list item is a
  paragraph of its own. An Evidence line with no parsed
  citation (empty, a bare path, a URL) covers nothing.
- **Syntactic, not semantic.** A citation never asserts that the cited code supports the claim.
  freshlint checks only that a citation sits where a reader looks for one. The known limit: an
  adjacent Evidence line that has nothing to do with the claim still covers the claims in its
  paragraph.
  An unrelated citation elsewhere in the note no longer quiets other claims or other mentioned
  files.
- **Order and first-scan cost.** Explicit citations are checked first, for every note, before any
  inferred baseline is taken. Baselines are taken within the 3-second startup budget and carried
  over later sessions; the report says when the scan stopped early. A note that cites code used
  to skip file-level tracking altogether, so the first session after upgrading takes baselines
  for every path such notes mention without citing. `freshlint memory scan` has no time limit.

### Memory demo

[`examples/memory_demo.py`](examples/memory_demo.py) calls freshlint's hook handler in process,
against a throwaway project, with the fields Claude Code sends that freshlint reads. The output
below is produced by the script and checked by the test suite.

<!-- memory-demo:start -->
```text

== 1. The agent saves a memory that claims certainty, with no evidence
freshlint: api_limits.md says "root cause" but cites no evidence. Add a line that starts with **Evidence:** followed by backticked citations (for example `**Evidence:** `path/to/file.py#function_name``), or say when and how it was checked (for example "checked 2026-10-01 by running `pytest`").

== 2. It cites code it has not read
freshlint: api_limits.md cites src/limits.py#retry_after, which you have not read in this session. Read it with the Read tool before citing it, or remove the citation.

== 3. It reads the code and saves again: fingerprinted, no feedback
(silent)

== 4. Someone changes the code. The next session starts
freshlint: 1 memory needs re-checking before you rely on it.
- api_limits.md: src/limits.py#retry_after changed after this memory was written.
When one of these becomes relevant, re-read the cited code, then update or delete the memory.

== 5. Saving again without re-reading renews nothing
freshlint: api_limits.md rests on src/limits.py#retry_after, which changed after this memory was written. Re-read it before relying on or updating this memory.

== 6. The agent re-reads the code, fixes the memory and saves: renewed, with a receipt
(silent)
receipts.md: - verified api_limits.md#evidence — re-read src/limits.py#retry_after (session 7d3b5e90)

== 7. The next session starts clean
(silent)
```
<!-- memory-demo:end -->

### CLI

```text
freshlint memory scan                 # every finding for this project's Claude Code memory
freshlint memory check                # exit 1 if anything would be reported to the agent
freshlint memory scan --memory-dir . --include CLAUDE.md --include AGENTS.md
```

`freshlint memory check` compares the code with the fingerprints in `.freshlint/lock.json`, next
to the memory notes. To use it in CI, commit that file with the notes; without it every citation
reads as unread and the check exits 1. Do not commit `.freshlint/lock.lock`.

### What freshlint writes

- **Next to your memory notes**, in `.freshlint/`: `lock.json` (the fingerprints: hashes and
  line positions, no code), `receipts.md` (one line per renewal) and `lock.lock` (an empty
  file that stops two parallel saves from overwriting each other). It never modifies the notes
  themselves or the code they cite.
- **In the cache directory** (`$FRESHLINT_CACHE_DIR`, else `$XDG_CACHE_HOME/freshlint`, else
  `~/.cache/freshlint`): `sessions/<session id>.jsonl` holds the path and a SHA-256 of the
  bytes of every file the agent read or edited in that session, which is how the vouching rule
  works. Files in `sessions/` (those and a digest of the last report, so a resumed session is
  not told the same thing twice) are deleted after 7 days, at the next session start.
  `hook.log` holds the traceback of any error inside a hook and is never rotated.
  `events.jsonl` exists only if you set `FRESHLINT_EVENTS=1`: session ids, counts and note
  file names, for evaluating the plugin.
- **Hashes are not secrets.** A fingerprint of a short or guessable value (a key whose value is
  `true` or a port number, a one-line range such as `}`) is an unsalted SHA-256 and can be
  recovered by guessing. Do not cite keys or lines that hold secrets. The cache files get
  your default file permissions.

### Uncited claims

When the agent saves a `project` or `reference` note (or one with no type), freshlint looks for
sentences that claim certainty about code. `user` and `feedback` notes hold preferences and
rules, and are skipped. Claims are raised when the note is saved, listed by
`/freshlint:memory-check` and counted in the first-session summary; they never appear in the
session-start report.

A sentence is a claim when it uses a certainty phrase ("verified", "confirmed", "root cause",
"is fixed", "is broken", "does not support", "is not supported", "always fails", "never works",
"tested", "proven") and names code (a code span or a file path), unless it is not an assertion or
something in it credits it. Claims are checked sentence by sentence, whether or not the note has
citations; [what a citation covers](#what-a-citation-covers) decides which of them are already
accounted for. Three older rules are unchanged:

- Sentences that hedge ("probably", "may", "could", "seems", "will", "should", "not tested") or
  credit a person ("confirmed by Sam", "Sam confirmed") are not claims.
- A named check in the sentence credits it: "verified", "tested" or "checked" followed by
  "with", "by" or "on" ("checked 2026-10-01 by running `pytest`").
- A test, script or command in backticks credits only with a check word right next to it ("ran",
  "run", "running", "passes", "passed", "with", "via", "using"): "`pytest` passes" does,
  "`npm install` always fails" does not. A source file (`.py`, `.ts`, `.js`, `.rs`, `.go` and
  similar) is never such a check unless its name is test-shaped (`test_*`, `*_test.*`,
  `*.spec.*`, `*.test.*`).

The rules added since fall into two groups with different standing, so they are labelled.

#### Contract

These bring the check back to what this README already promised: that freshlint "pushes back when
the agent saves a memory that claims certainty without evidence", and that a sentence counts only
if it uses a certainty phrase and names code. Each applies inside one sentence. Items 1, 2, 3
and 5 are **contract (headline inference)**: the README states the headline and the sentence
rule, not these cases, so they are read off those two. Items 4 and 6 are directly documented
("not tested" is a listed hedge, and "a version number" is a listed pin).

1. **Questions**: contract (headline inference). A sentence that ends in `?`, or opens with an
   interrogative or an inverted auxiliary, asserts nothing: "Has `a.py` been tested?" This also
   hides some real claims (see the limits below).
2. **Instructions to verify**: contract (headline inference). The complement of a verification
   verb is not asserted: "Check that `a.py` is fixed.", "Make sure `retry()` always succeeds.",
   "Record the root cause in `issue.md`." The verbs are check, verify, confirm, ensure, make sure,
   test, record, document, and note or determine whether. Only that complement is exempt, up to
   the first clause break ("and", "or", "but", "because", "which", a relative "that", a
   parenthesis, `;`, `:`, a dash); every other certainty phrase in the sentence is read as usual.
   Other imperatives ("Avoid ...", "Use ...") exempt nothing.
3. **Quotes and blockquotes**: contract (headline inference). A phrase inside `"..."` or
   `“...”` is mentioned, not asserted, and a line that starts with `>` is not checked. This also
   hides some real claims (see the limits below).
4. **Negation**: contract (directly documented). "It is false that `a.py` is fixed.", "not
   always", "isn't fixed" and "never confirmed" assert nothing. What the README listed before
   was "not tested" and "never tested"; the other forms extend them by inference.
5. **Tags attach to their sentence**: contract (headline inference). A bracketed verification tag
   (`[verified]`, `[verified: main.py]`, `(verified)`, `[checked 2026-10-01]`) belongs to the
   sentence before it. It never forms a sentence of its own and never splits one. A tag holds a
   verification word (verified, checked, tested, confirmed) and at most a short source; a bracket
   that makes a statement ("(Tested only on Linux; `a.py` is broken.)") is a sentence. Whether a
   tag credits its sentence is policy P2, below.
6. **Prerelease versions**: contract (directly documented). `0.3.0b2`, `1.2.0-rc.1`,
   `v2.0.0-beta` and `1.0.0a1` are versions for the version credit in P1.

#### Policy

This policy is a default; feedback welcome.

- **P1. A pin credits only when it is bound to verification.** A date, a commit or a version
  credits a claim only if the same sentence also holds a verification verb (verified, tested,
  checked, confirmed, reproduced, measured, ran, observed), read where the sentence asserts it
  ("never confirmed" binds nothing), or a provenance marker ("as of", "at commit", "pinned", "in
  v2.3.1" or "in version 3.12", "since v0.4.1"; "per <source>" credits on its own, see P3). An
  unrelated deadline ("the deadline is 2026-10-09") or version ("supports version 4.0") no
  longer credits a claim. The claim's own certainty phrase binds a pin only within its own
  clause, split at `;`: in "`route()` is verified; the deadline is 2026-10-09." the date credits
  nothing, while "`route()` confirmed on 2026-09-20." still does.
- **P2. Adjacent or structured provenance credits.** A claim is credited when its line is
  directly followed by a line that starts with `Verified:`, `Checked:`, `Tested:`, `Source:` or
  `Evidence (manual):` (after a list marker or emphasis, with something after the colon), or by a
  standalone verification tag: after a full stop, on a line of its own, or ending the sentence,
  line or bullet. A line that continues the claim's bullet, or a bullet nested under it, counts;
  a marker in a sibling bullet, after a blank line or above the claim does not. A marker line
  that serves a claim is read without its label ("Verified: by hand in `a.py`" is provenance,
  not a claim of its own).
- **P3. A convention or a source credits.** "per <source>", "by convention (<source>)" and
  "Source: <name>" in the claim's sentence credit it. A source is a document: a code span that
  names one (a `.md`, `.rst`, `.txt` or `.adoc` file, or a path with no extension that has a
  document directory in it, such as `docs`, `adr`, `rfcs`, `policies`, `runbooks`, `handbook`,
  `wiki` or `guides`), a URL, a markdown link to one, a capitalized phrase that ends in Policy,
  Guide, Standard, Runbook, README, Spec, Handbook, Playbook or Charter, or RFC, ADR or PEP with
  a number. It is never the claim's own code, never a source or config file, and never a location
  in code: a line or symbol suffix (`docs/policy.md:42`) makes a path code, in prose as well as in
  a code span (the anchor of a markdown link does not). The markers are read in prose, outside
  code spans. "per second", "per the team" and "Source: Sam" name nothing.

Unsupported present-tense certainty claims with no pin, check or source keep warning after all
of this, including ones that start with "Note that" or "Remember:" and ones next to an unrelated
date.

#### Documented bypasses and limits

Each of these is a known limit. The examples quoted here are pinned by tests.

- **Adjacent evidence.** An Evidence line next to a claim covers it, relevant or not.
- **An unrelated verb with an unrelated pin.** A verification verb about one thing and a date or
  version about another credits the claim, across `;` as well: "`route()` is broken; the
  deployment was checked 2026-10-09." The claim's own phrase is held to its own clause at `;`
  only, so a comma or "and" joins it to an unrelated pin: "`route()` is verified, the deadline is
  2026-10-09."
- **A marker binds an unrelated pin.** A provenance marker such as "as of" or "pinned" binds any
  date, version or commit in the sentence, whatever it is about: "`a.py` is fixed as of
  2026-10-09 in version control." "`a.py` is fixed; the pinned issue says 2026-10-09."
- **A comma splice after a verification instruction.** "Check `x`, the root cause is `y`." goes
  unflagged, because the exempt complement runs to the first clause break and a comma is not
  one.
- **A bare `[verified]` tag credits.** A stricter option, where a tag credits only when it names a
  source or a check, has been considered and is not adopted; whether to adopt it is undecided.
- **URL permalinks to code credit.** "per https://github.com/o/r/blob/main/src/a.py#L10" counts
  as external provenance.
- **`.txt` counts as a document**, and so does any path with a document directory in it, even a
  code one (`src/policy/engine`).
- **Weak provenance is accepted, and empty values credit.** A P2 marker credits whatever follows
  its colon, and a tag credits whatever follows its colon: "Verified: yes", "Verified: no",
  "Tested: failed", "Checked: pending", "Source: unknown", "[verified: no]", "[verified: guess]",
  "by convention [verified: trust me]". "per <document>" says a document exists, not that it
  supports the claim. Credit is syntactic here as well.
- **Marker chains.** A marker line that states a claim of its own is credited by a marker line
  below it, so a claim restated in a marker line, or a chain of such lines that ends in a bare
  marker, stays quiet: "`a.py` is fixed." then "Verified: `b.py` is broken." then "Source:
  `docs/x.md`." (A last line that states a claim with nothing below it still warns.)
- **What the question and quote exemptions hide.** A sentence that ends in `?` is not asserted,
  so a tag question hides its claim: "`x` is fixed, right?", "`x` is fixed?", "Did you know `x`
  is broken?". A clause that opens like a question and is not one is read as a question too:
  "What was verified in `src/a.py` is the cache path." A phrase in quotation marks is not
  asserted, so scare quotes hide it ("The "root cause" is `retry()`."), and two straight quote
  marks that are inch marks pair up and hide what lies between them ("Resize to 5" and `x` is
  fixed in the 3" mode."). A line that starts with `>` is skipped whole, which also takes a line
  such as ">= 3 callers: `x` is fixed." and an author's own callout in a blockquote.
- **Some honest sentences still warn.** "Ensure `a` is tested and `b` is fixed." warns on its
  second part. "Suppose `route()` is broken" warns, because exempting hypotheses would let a
  premise into memory unflagged. "Checked by running `pytest`" without a colon, and a marker
  above the claim, do not credit. P2 works per line: a marker credits the claim on the line
  directly above it, and every claim on that line, but not a claim earlier in a wrapped
  paragraph. The tag words are limited to verified, checked, tested and confirmed.
- **Performance.** A note with a very large number of uncited claims is slow to check.

### Limits

- Reads made through shell commands (`cat`, `sed`) are not seen; the agent must use the Read
  tool for files it cites.
- Memory notes written through shell commands are not checked when they are saved, because the
  hook sees only the file tools (Write, Edit). They are still checked at the next session start.
- Reads vouch only for the agent that made them: a subagent's reads count only for memories
  that subagent saves itself.
- Claude Code reads project settings and project hooks from the directory it was launched in,
  while memory belongs to the git root. If you enable the plugin only in a repository's
  `.claude/settings.json` and launch Claude Code in a subdirectory, the hooks may not run.
  Installing the plugin for your user avoids this.
- The memory-directory lookup was checked against Claude Code for git projects with ASCII
  paths. Non-git projects and very long non-ASCII paths are untested. If freshlint resolves the
  directory wrongly, it stays silent rather than reporting anything. The `autoMemoryDirectory`
  setting is honored only when it is an absolute path or starts with `~/`; a relative value is
  ignored, as in Claude Code. `CLAUDE_CONFIG_DIR` is not honored: if you moved Claude Code's
  configuration directory, freshlint looks under `~/.claude/projects/` and stays silent. The
  CLI's `--memory-dir` can point it at the right place.
- A memory is checked against the code it cites, not against the truth. A fresh fingerprint
  means the code has not changed since someone read it, not that the memory is right.
- Paths a note mentions without citing them (older memories without Evidence lines, and the
  uncovered mentions of notes that cite other files) are checked at file level. A citation
  hands a mention over only once that citation has a fingerprint (see
  [What a citation covers](#what-a-citation-covers)). A mentioned file that no longer exists is
  reported at session start, unless every sentence that names it says in the past tense that it
  was deleted, removed, moved, renamed or retired (or is gone), as a note that records a cleanup
  does; a negated, conditional or future statement ("not deleted", "if removed", "will be
  removed"), a question, a deletion that was restored or reverted, and a deletion that is about
  something else in the sentence ("`a.py` calls the shim, which was removed") do not hide it. A
  restore or revert written as a command in backticks ("`git restore`") does not count as
  undoing the deletion. A mentioned file that changed is a low-confidence finding: it is listed
  by `/freshlint:memory-check`, but never in the first-session summary or the per-session
  report.
- A line range is found again after it moves only when it spans 3 or more non-blank lines, was
  unique in the file when it was fingerprinted, and matches exactly once now. Otherwise a move
  counts as a change.
- A symbol in a brace language falls back to whole-file tracking whenever the scanner is not
  sure (for example a block comment inside the declaration, or unusual formatting). That can
  report a change outside the symbol, and the finding then says it is tracked at file level
  (see [Declaration prefixes](#declaration-prefixes) for what C and C++ do). The scanner is
  designed to catch every change inside a symbol, but it is heuristic and does not preprocess or
  expand macros: shapes it does not model can make a span shorter than the declaration.
- A cited file that exists but cannot be read is reported as "cannot be read", never as gone.
- Certainty claims are bounded and syntactic.
  See [Uncited claims](#uncited-claims) for the rules and their documented bypasses.

## Freshness markers for committed documents

freshlint also checks markdown knowledge bases that carry freshness dates. The rest of this
README covers that mode (`freshlint check`), which catches **freshness laundering**: a
`verified=` date bumped while the content stayed the same. Memory notes about facts outside
the repository (people, vendors, external systems) cannot cite code; they can carry these
markers and be checked the same way.

If you run `freshlint check` on a memory directory that the plugin also uses, add
`extend_exclude = [".freshlint/**"]` to that directory's `.freshlint.toml`. The plugin keeps
its receipt log in `.freshlint/receipts.md`, a markdown file that carries no freshness markers.

### Five-minute demo (freshness markers)

[`examples/demo.sh`](examples/demo.sh) runs this in a throwaway git repository using a
fictional company memory, [`examples/acme-memory/`](examples/acme-memory/). The output
below is produced by the script and checked by the test suite.

<!-- demo-output:start -->
```text

== 1. Commit a clean memory (one section is due for review in 2 days)
$ freshlint check --today 2026-09-30
context/shipping.md:4: warning [review-expired] Section "Carriers" review is due in 2 days (review_by=2026-10-02)
freshlint: 0 errors, 1 warning in 5 files (full scan, today 2026-09-30)
[exit 0]

== 2. An agent silences the warning: new dates, same words
$ git diff --unified=1
diff --git a/context/shipping.md b/context/shipping.md
index 9314ce0..7a3e03d 100644
--- a/context/shipping.md
+++ b/context/shipping.md
@@ -3,3 +3,3 @@
 ## Carriers
-<!-- memory: verified=2026-07-02 review_by=2026-10-02 -->
+<!-- memory: verified=2026-09-30 review_by=2026-12-30 -->
 - Kits ship with two regional ground carriers, Parcel Owl and Desert Freight.
[exit 0]
$ freshlint check --today 2026-09-30
freshlint: 0 errors, 0 warnings in 5 files (full scan, today 2026-09-30)
[exit 0]

== 3. The diff check sees that nothing was re-verified
$ freshlint check --diff HEAD~1...HEAD --today 2026-09-30
context/shipping.md:4: error [freshness-laundering] Section "Carriers" claims re-verification (verified 2026-07-02 -> 2026-09-30) but its content is unchanged
    hint: if you re-checked it without editing, record a receipt in VERIFICATIONS.md:
          - 2026-09-30 verified context/shipping.md#carriers — <what you checked it against>
freshlint: 1 error, 0 warnings in 1 file (diff HEAD~1...HEAD, today 2026-09-30)
[exit 1]

== 4. The honest fix: re-check the carriers, then record a receipt
$ freshlint check --diff HEAD~1...HEAD --today 2026-09-30
freshlint: 0 errors, 0 warnings in 2 files (diff HEAD~1...HEAD, today 2026-09-30)
[exit 0]
```
<!-- demo-output:end -->

Step 2 is the important one: after the agent's date bump, the ordinary full scan is
clean. Only the diff check notices that the claim moved and the content did not.

### Install

freshlint needs Python 3.11 or newer and git (for `--staged` and `--diff`). From a
checkout of this repository:

```bash
pipx install .
# or
uv tool install .
```

### Quickstart

1. Put a marker on the first line after each heading you want to govern:

   ```markdown
   ## Carriers
   <!-- memory: verified=2026-09-30 review_by=2026-12-30 -->

   - Kits ship with two regional ground carriers.
   ```

   Use `cadence=evergreen` instead of `review_by=` for facts that do not decay. Whole
   files can use frontmatter instead:

   ```yaml
   ---
   last_verified: 2026-09-30
   review_by: 2026-12-30
   ---
   ```

2. Tell freshlint which files are governed, in `.freshlint.toml` (or
   `[tool.freshlint]` in `pyproject.toml`):

   ```toml
   include = ["memory/**/*.md", "CLAUDE.md"]
   section_level = 2          # every ## heading needs a marker

   [rules]
   missing-marker = "error"
   ```

3. Run it:

   ```bash
   freshlint check                          # full scan of the working tree
   freshlint check --staged                 # staged changes vs HEAD (pre-commit)
   freshlint check --diff origin/main...HEAD   # a pull request (CI)
   ```

4. When you re-check something and it is still true, move `verified=` forward and add a
   receipt in the same commit:

   ```markdown
   ## 2026-09-30
   - verified memory/shipping.md#carriers — both carrier contracts confirmed on their portals
   ```

### Verification receipts

Receipts live in an append-only markdown log, `VERIFICATIONS.md` by default:

```markdown
# Verifications

## 2026-09-30
- verified context/shipping.md#carriers — both carrier contracts confirmed on their portals
- edited context/company.md#overview — fixed a typo; facts not re-checked
- 2026-09-30 verified context/products.md — full review against the catalogue
```

* Grammar: `- [YYYY-MM-DD ]verified|edited PATH[#section-slug] — NOTE`. The separator
  may be `—`, `--` or `-`. An entry without a date inherits the `## YYYY-MM-DD` heading
  above it. The slug is the heading's GitHub-style anchor; no slug means the whole file.
* A `verified` receipt covers a date bump with no content change when it is **added in
  the same diff**, names the same file (and section, or the whole file), and carries
  the **same date** as the new `verified=`.
* An `edited` receipt covers the opposite case: a wording change that deliberately did
  not re-check the facts, so `verified=` does not need to move.
* The log is append-only. Dates must be ISO and in order (`receipt-ordering`), and
  existing entries cannot be edited or removed (`receipt-append-only`).

The full reasoning is in [docs/design.md](docs/design.md#verification-receipts).

### Rules

| Rule | Mode | Default | What it reports |
|---|---|---|---|
| [`review-expired`](docs/rules/review-expired.md) | state | error | A review_by or stale_after date has passed (warning when it is close). |
| [`missing-marker`](docs/rules/missing-marker.md) | state | warning | A governed file or section carries no freshness metadata. |
| [`invalid-marker`](docs/rules/invalid-marker.md) | state | error | Freshness metadata is malformed, misplaced, contradictory, or dated in the future. |
| [`temporal-language`](docs/rules/temporal-language.md) | state | warning | Governed content uses relative time words or a deadline that has already passed. |
| [`suppression`](docs/rules/suppression.md) | state | error | An inline suppression is malformed, has no reason, has expired, or lasts too long. |
| [`receipt-ordering`](docs/rules/receipt-ordering.md) | state | error | A receipt log entry is malformed, out of date order, or dated in the future. |
| [`freshness-laundering`](docs/rules/freshness-laundering.md) | diff | error | A freshness claim moved forward but the content it covers did not change. |
| [`stale-refresh`](docs/rules/stale-refresh.md) | diff | error | Governed content changed but its verified date did not move. |
| [`receipt-append-only`](docs/rules/receipt-append-only.md) | diff | error | An existing verification receipt was edited or removed. |

*State* rules run in every mode. *Diff* rules run with `--staged` or `--diff`. In diff
modes, state rules only look at changed files; run a scheduled full scan to catch
reviews that expire without a pull request. `freshlint rules` prints this table.

#### Suppressions and evergreen

Two escape hatches exist and they are deliberately different:

* `cadence=evergreen` is a permanent, typed claim that a fact does not decay. It keeps
  its verified date and is still checked for laundering; it is only exempt from expiry.
* An inline suppression is a temporary waiver of one state rule, with a reason and an
  expiry at most 90 days away:

  ```markdown
  <!-- freshlint: allow=temporal-language reason="direct customer quote" expires=2026-12-01 -->
  ```

  Diff rules cannot be suppressed inline. Expired, reasonless or over-long suppressions
  are errors themselves ([`suppression`](docs/rules/suppression.md)).

### Configuration

All keys are optional. Unknown keys are an error, so a typo cannot silently disable a
check.

```toml
# .freshlint.toml  (or [tool.freshlint] in pyproject.toml)
include = ["**/*.md"]          # governed files, relative to this file's directory
exclude = [".git/**", "**/node_modules/**", "**/.venv/**", "**/venv/**",
           "**/__pycache__/**", "**/.tox/**", "**/dist/**", "**/build/**",
           "**/vendor/**"]     # setting this replaces the defaults
extend_exclude = []            # added to exclude
section_level = 0              # N > 0: every heading of level N needs its own marker
review_warn_days = 14          # warn when fewer than N days remain before review
max_age_days = 0               # N > 0: claims without a review date expire N days after verified
max_suppression_days = 90      # longest allowed suppression
whitespace = "ignore"          # "exact": trailing whitespace and blank lines count as changes
temporal_phrases = ["currently", "as of now", "next week", "..."]  # replaces the default list

[rules]                        # per-rule severity: "error", "warn" or "off"
missing-marker = "warn"

[receipts]
paths = ["VERIFICATIONS.md"]   # globs; matching files are receipt logs, not governed content
order = "ascending"            # "descending" for newest-first logs such as OKF log.md
```

Config is discovered from the current directory upward to the git top level. The
directory that holds the config is the project root: globs and receipt paths are
relative to it, so a memory can live in a subdirectory of a larger repository.

`--today YYYY-MM-DD` pins the evaluation date for reproducible runs and tests.

**Exit codes:** `0` no errors (warnings allowed), `1` errors found (or warnings with
`--strict`), `2` usage, configuration or git error.

**Output formats:** `--format text` (default), `--format json` (stable schema, version
1), `--format github` (GitHub Actions annotations).

### Integrations

#### pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: <path or URL of this repository>
    rev: v0.2.0
    hooks:
      - id: freshlint
```

The hook runs `freshlint check --staged`. See
[`examples/pre-commit-config.yaml`](examples/pre-commit-config.yaml).

#### GitHub Actions

[`action.yml`](action.yml) is a composite action. On pull requests it runs
`freshlint check --diff BASE...HEAD`; on other events (for example a weekly schedule)
it runs a full scan. Diff mode needs history, so check out with `fetch-depth: 0`. See
[`examples/github-workflow.yml`](examples/github-workflow.yml).

#### Anything else

`freshlint check --diff "$BASE...HEAD" --format json` works in any CI that has git.

### Supported formats

* **Section markers** (`<!-- memory: ... -->`) in any markdown file.
* **Frontmatter** `last_verified` / `last-verified`, `review_by` / `review-by`,
  `cadence: evergreen`.
* **OKF v0.2** (Open Knowledge Format) trust and lifecycle fields: `verified` (a
  `{by, at}` mapping or list; the latest `at` counts), `stale_after`, and
  `status: deprecated`. Other OKF fields are ignored. A new OKF `verified` event is not
  treated as a receipt in v0.1; see [docs/design.md](docs/design.md#okf-v02-support-level).

## How this compares

<!-- memory: verified=2026-10-01 review_by=2026-12-30 -->

This section makes claims about other projects, so it carries its own freshness marker
and is checked by this repository's CI. We surveyed the space at the end of September
2026 and checked it again on 2026-10-01, when we added the entries on memory tools.

* **GitHub Copilot Memory** stores code citations with each repository fact and checks them
  against the current branch before use. It is built into Copilot and stored by GitHub.
  freshlint brings the same idea to Claude Code, locally, with a rule of its own: evidence is
  renewed only by an agent that read it.
* **Docs-to-code anchors** such as fiberplane/drift, docpin, kedge and Swimm bind
  documentation to code and fail CI when the code changes. They need people to add anchors by
  hand and cover repository docs, not agent memory.
* **OpenWiki** ties each fact on the pages it generates to line ranges and reconciles them with
  an LLM. It covers only its own pages.
* **Claude Code `/doctor prompt-audit`** uses an LLM, on demand, to report stale paths and
  commands in instruction files. freshlint is deterministic and runs automatically on memory.
* **ctxlint** counts commits to paths a context file mentions since the file last changed.
  It works per file and per path, not per claim.
* **Date stampers**, such as the `yaml-update-last-modified-date` pre-commit hook, set a
  date in the frontmatter of every edited markdown file. That is the forward direction,
  and it records editing rather than verification.
* **Age checks** on `last_verified` or `last-verified` fields fail when a date is too
  old. Several projects have built or proposed them. They are exactly the check that a
  date bump defeats.
* **Agent-instruction linters**, such as agents-lint and AgentLinter, check that paths,
  scripts and commands referenced in `AGENTS.md` or `CLAUDE.md` still exist. That is
  complementary: they check references, freshlint checks freshness claims.
* **memlint** (on PyPI) scores facts in vector-store agent memories by age and
  contradiction at retrieval time. It works on a different layer.

We did not find a tool that checks the reverse direction: the date moved, the content
did not, and nothing records a re-verification. If one exists, we would like to hear
about it and will correct this section.

## Limitations

* freshlint detects date bumps with **no** content change. A one-character edit counts
  as a change, so a determined launderer can disguise a bump. Code review still matters.
* A receipt is also a claim. freshlint checks that it exists, matches the bump and is
  not rewritten; it cannot check that the work it describes happened.
* Headings are matched by slug, so renaming a heading starts a new section.
* Setext headings are not recognised as sections.
* OKF support covers `verified`, `stale_after` and `status` only.

## Project

* [docs/design.md](docs/design.md): section model, comparison rules, receipts
* [docs/rules/](docs/rules/): one page per rule
* [ROADMAP.md](ROADMAP.md), [CHANGELOG.md](CHANGELOG.md),
  [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [NAMING.md](NAMING.md)

freshlint grew out of the memory practice at Keller AI, where agents and people share a
markdown memory and the lint runs on every commit. It is licensed under the Apache
License 2.0 (see [LICENSE](LICENSE) and [NOTICE](NOTICE)).
