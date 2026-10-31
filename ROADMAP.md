# Roadmap

Ideas for after v0.2, roughly in priority order. Nothing here is a commitment.

## Open decisions for v0.3

- **Other agents.** Codex memories and Cursor rules, using the same citation format.
- **Session reconciliation ledger.** A local record of which agent sessions touched memory and
  whether each was reviewed. Still open; see the v0.1 notes in git history.
- **OKF `verified` events as receipts.** Whether an OKF `verified` entry should count as a
  receipt. Still open.
- **License and name.** Still open. The license in the tree is Apache-2.0 and the name is `freshlint`.

## Checks

- Link checking for relative links inside governed files.
- Index coverage: every governed file is linked from an index file.
- Size caps per file (warning and error thresholds).
- Maximum review window, so `review_by=2099-01-01` cannot stand in for evergreen.
- Dangling receipts: a new receipt that names a file or section that does not exist.
- OKF `generated.at` moved forward without a content change.
- Optional heuristics for trivial edits (for example punctuation-only changes) that
  should not count as a content change for laundering.

## Usability

- Per-path overrides in config (different `section_level` or severities per glob).
- `freshlint verify PATH#slug --note "..."`: a helper that moves `verified=` and appends
  the matching receipt in one step (the only command that would write files).
- `freshlint init` to propose markers for an existing memory.
- SARIF output.
- Setext headings, and following a renamed heading by similarity.
