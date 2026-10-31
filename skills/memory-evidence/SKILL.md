---
name: memory-evidence
description: Use when saving or updating a Claude Code memory note that states something about code, configuration, commands or system behavior, and when freshlint reports that a memory needs re-checking. Covers Evidence lines, citation forms, and how to re-check a stale memory.
---

# Memory evidence

freshlint fingerprints the code a memory cites. At session start it tells you which memories
rest on code that changed or is gone. When you save a memory, it pushes back if the memory
claims certainty without evidence or cites code you have not read.

## When you save a memory about code

Add an Evidence line naming what the memory rests on:

```markdown
**Evidence:** `src/auth/session.ts#refreshToken`, `package.json#scripts.test`
```

Put the Evidence line on its own line, starting with `**Evidence:**` (a list bullet is fine).
`Evidence:` in the middle of a sentence is not read: nothing is cited, and freshlint tells you
so when you save.

Citation forms:

- `path/to/file`: the whole file
- `path/to/file#L10-L24`: a line range (a moved range of 3 or more non-blank lines is tracked)
- `path/to/file.py#function_name` or `#Class.method`: a symbol (Python, and brace languages such as TypeScript, Go, Rust, Java, C# and C/C++)
- `config.json#a.b.c` or `pyproject.toml#tool.x`: a key

A symbol citation includes the attribute and annotation lines directly above the declaration
and, in C and C++, its `template`, `requires` and `[[...]]` lines. When freshlint cannot tell
what a line above or inside a C or C++ declaration is (an unknown macro, a conditional `#if`),
it tracks the whole file instead, and a finding then says "tracked at file level": it can
report a change elsewhere in the file, so re-read the symbol before deciding.

Rules:

- Read the file with the Read tool in this session before citing it. freshlint only
  fingerprints code you have read. It takes the first fingerprint when you save the memory
  after reading the file.
- Cite the narrowest thing that supports the memory: a function or key, not a whole folder.
- A citation covers the files it names and the claims written next to it. A claim of certainty
  about code (a sentence with "verified", "root cause", "is fixed" and similar) counts as
  covered when its sentence names a cited file, symbol or key (in backticks, or as a path with a
  `/`, a `.` or a `:line` suffix: a bare word such as `src` names nothing), or when an Evidence
  line is in its paragraph or on the first non-blank line after it. An Evidence line elsewhere in
  the note
  covers a claim only if the sentence names what the line cites, and a file the note mentions
  without citing it is still tracked at file level.
- Coverage is syntactic. freshlint never checks that the cited code supports the claim, so put
  the Evidence line next to a claim only when the cited code is what supports it.
- If you have not checked something, say so ("unverified", "probably") instead of
  "confirmed" or "root cause". A question, an instruction to verify ("Check that `a.py` is
  fixed."), a quotation or a negation ("never confirmed") is not a claim.
- A claim can also say how it was checked, when that is true. A date, version or commit counts
  only together with a verification word or a marker in the same sentence ("verified
  2026-10-01", "tested on v2.3.1", "as of 4f9c2ab", "at commit 4f9c2ab"); "the deadline is
  2026-10-09" next to a claim counts for nothing. A line directly below the claim that starts
  with `Verified:`, `Checked:`, `Tested:`, `Source:` or `Evidence (manual):`, a tag such as
  `[verified: main.py]` that ends the sentence, or "per `docs/policy.md`" naming a document also
  counts. For the rules, see the README, section "Uncited claims".
  This policy is a default; feedback welcome.
- Memories about people, preferences or rules (`user` and `feedback` types) need no evidence.

## When freshlint reports a memory

Do not re-check everything at session start. When a reported memory becomes relevant, act on
what freshlint reported:

- Cited code changed, has no saved fingerprint, or was never read (the memory has an Evidence
  line). "No saved fingerprint yet" is what freshlint says about a citation it has no record
  of, such as one written before it was installed; it does not mean the code was not read:
  1. Read the cited code with the Read tool. If the cited file no longer exists, there is
     nothing to read: remove the citation or rewrite the memory.
  2. If the memory still holds, save it again with the Write tool, even unchanged. freshlint
     renews the fingerprints of the citations you re-read and logs a receipt for each renewal.
  3. If it is wrong, rewrite it with new evidence, or delete it.
- Cited code is gone: rewrite the memory with new evidence, or delete it.
- Certainty claim without evidence (`uncited-claim`): the sentence is not covered. Put an
  Evidence line right after its paragraph, or name the cited file in the sentence, or say how
  it was checked (see the rules above), or soften the claim. An Evidence line for other code,
  further up or down the note, does not cover it.
- A mentioned file that no citation covers (the memory has no Evidence line, or its Evidence
  lines name other files) is tracked at file level, and flagged when the file changed or is
  gone. Clear it by reading the file, citing it in an Evidence line and saving the note, or by
  dropping the mention. Citing other files does not clear it, and neither does citing the file
  without reading it. If the file no longer exists (an inferred `gone`), there is nothing to read:
  citing a file that no longer exists clears nothing (the citation is reported `unread` next to
  the `gone` mention); remove the mention, and that citation if you added it, to clear it. A
  re-save alone does not clear these findings.
- Invalid citation: fix the citation syntax.
- Evidence that is not on its own line (`misplaced-evidence`): move it onto a line that starts
  with `**Evidence:**`.

A re-save without changes clears nothing except citations you re-read in this session.

Never edit the files in the memory directory's `.freshlint/` folder. freshlint maintains them.
