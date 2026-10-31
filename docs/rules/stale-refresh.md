# stale-refresh

**Default severity:** error
**Mode:** diff
**Inline suppression:** not allowed

Governed content changed, but the claim that covers it still carries the old
`verified` date. This is the forward direction: if the truth changed, the freshness
metadata should say when it was last checked.

## Example

```diff
 ## Rates
 <!-- memory: verified=2026-09-10 review_by=2026-12-10 -->

-- Flat 12 USD per kit inside the home region, 25 USD elsewhere.
+- Flat 14 USD per kit inside the home region, 25 USD elsewhere.
```

```text
context/shipping.md:9: error [stale-refresh] Section "Rates" changed but its marker still says verified=2026-09-10
```

## How to fix

- If the edit re-verified the facts, move `verified=` (and `review_by=`) forward.
- If it did not (a typo or formatting fix), record an `edited` receipt in the same diff:

  ```markdown
  ## 2026-09-30
  - edited context/shipping.md#rates — reworded only; prices not re-checked
  ```

## Details

- A section is compared by slug. A renamed heading is a new section, so it is not
  checked here (`missing-marker` covers new sections when `section_level` is set).
- The file-level claim only covers content that is **not** inside a marked section.
  Editing one marked section does not force a re-claim of the whole file.
- If `verified` already equals today, the edit is fine (same-day follow-up edits).
- Claims without a verified date (for example an OKF file with only `stale_after`)
  are not checked by this rule.

## Configuration

```toml
[rules]
stale-refresh = "warn"
```
