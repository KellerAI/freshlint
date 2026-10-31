# invalid-marker

**Default severity:** error
**Mode:** state
**Inline suppression:** not allowed

Freshness metadata exists but cannot be trusted as written:

- a marker that does not parse (unknown key, non-ISO date, missing `verified=`, neither
  `review_by=` nor `cadence=evergreen`, both at once, not closed with `-->`)
- a marker that is not the first non-blank line after its heading, or a second marker
  for the same section
- a frontmatter date that is not ISO, `review_by` without a verified date,
  `cadence: evergreen` together with a review date, a malformed OKF `verified` entry
- a verified date in the future (later than `--today`)
- `review_by` earlier than `verified`
- both frontmatter and a file-level marker claiming the same file

## Example

```markdown
## Carriers
<!-- memory: verified=2026-09-30 reviewby=2026-12-30 -->
```

```text
context/shipping.md:4: error [invalid-marker] unknown key 'reviewby' in marker (allowed: verified, review_by, cadence)
```

## How to fix

Correct the metadata. The accepted forms are:

```markdown
<!-- memory: verified=YYYY-MM-DD review_by=YYYY-MM-DD -->
<!-- memory: verified=YYYY-MM-DD cadence=evergreen -->
```

```yaml
---
last_verified: 2026-09-30
review_by: 2026-12-30
---
```

This rule cannot be suppressed: a claim that cannot be parsed cannot be checked.
