# missing-marker

**Default severity:** warning
**Mode:** state
**Inline suppression:** allowed

A governed file has no freshness metadata at all, or, when `section_level` is set, a
heading at that level has no marker.

## Example

With `section_level = 2`:

```markdown
## Rates

- Flat 12 USD per kit.
```

```text
context/shipping.md:8: warning [missing-marker] Section "Rates" has no freshness marker
```

## How to fix

Add a marker as the first non-blank line after the heading:

```markdown
## Rates
<!-- memory: verified=2026-09-30 review_by=2026-12-30 -->
```

Use `cadence=evergreen` instead of `review_by=` for facts that do not decay (see the
README for how evergreen differs from a suppression). For a whole file, add
`last_verified:` and `review_by:` frontmatter, or put a marker on the first line of
the body before any heading.

## Configuration

```toml
include = ["context/*.md"]   # which files are governed
section_level = 2            # 0 = only require file-level metadata

[rules]
missing-marker = "error"     # or "warn" (default), "off"
```

A heading whose marker is present but invalid is reported by
[invalid-marker](invalid-marker.md), not here.
