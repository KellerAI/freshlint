---
description: Review which memories rest on code that changed, is gone, or was never read
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}/bin/freshlint" memory scan:*), Read, Write, Edit
---

## Memory evidence report

!`"${CLAUDE_PLUGIN_ROOT}/bin/freshlint" memory scan --project . 2>&1 || true`

## Your task

Go through the findings above with the user, most serious first: `gone`, then `changed`,
`unread`, `uncited-claim`, and low-confidence findings last.

For each memory, read the memory note and the code it cites or mentions with the Read tool,
say in one line whether the memory still holds, and propose the fix that fits the finding:

- `changed` or `unread`, on a note with an Evidence line: read the cited code. If the memory
  still holds, save the note again (even unchanged) so its fingerprints renew. Otherwise
  rewrite it. If the cited file no longer exists, there is nothing to read: remove the
  citation or rewrite the memory.
- `gone`: the cited code no longer exists. Rewrite the memory with new evidence, or delete it.
- `uncited-claim`: no citation covers the sentence. A citation covers the files it names and the
  claims written next to it, and coverage is syntactic, so freshlint does not check that the
  cited code supports the claim. Put an Evidence line right after the claim's paragraph (or name
  the cited file in the sentence, in backticks or as a path: a bare word such as `src` names
  nothing), or state how the claim was checked, when that is true: in the same sentence (the test
  or command you ran; a date, version or commit together with a word
  like "verified" or "as of"), or on a `Verified:` or `Source:` line directly below it. Or
  soften the claim. An Evidence line elsewhere in the note covers a claim only if the sentence
  names what the line cites, and a bare date such as a deadline credits nothing. For the rules,
  see the README, section "Uncited claims".
  This policy is a default; feedback welcome.
- `invalid-citation`: fix the citation syntax.
- `misplaced-evidence`: the note mentions `Evidence:` inside a sentence, so nothing was cited.
  Move the citations onto their own line that starts with `**Evidence:**`.
- low-confidence `inferred-changed` (the note mentions a file that no citation covers: it has no
  Evidence line, or its Evidence lines name other files): read the file the note mentions. If
  the memory holds, add an Evidence line citing it and save the note. Otherwise rewrite the
  memory so it no longer names that file, or delete it. Citing other files, or citing this one
  without reading it, does not clear the finding.
- an inferred `gone` (the note mentions a file that no longer exists and no citation covers
  it): there is nothing to read, and citing a file that no longer exists clears nothing (the
  citation is reported `unread` next to the `gone` mention); remove the mention, and that
  citation if you added it, to clear it. Rewrite the memory so it no longer names that file, or
  delete it.
- a finding that says "tracked at file level": freshlint could not isolate the cited symbol (in
  C and C++ an unknown macro or a conditional `#if` near the declaration is enough), so it
  watches the whole file and may report a change that is not in the symbol. Read the symbol
  before deciding.

Saving a note again without changes clears nothing except Evidence citations you re-read in
this session.

Apply a fix only after the user agrees.

Do not edit anything in the memory directory's `.freshlint/` folder.
