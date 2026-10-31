# freshness-laundering

**Default severity:** error
**Mode:** diff
**Inline suppression:** not allowed

A freshness claim moved forward, but the content it covers did not change and no
verification receipt in the same diff explains why.

This is the reverse of the usual "you changed it, so bump the date" check. It catches
the common failure where someone (often an agent) updates `verified=` or
`last_verified` to make a section look fresh without changing or re-checking it.

## What counts as "moved forward"

- `verified=` / `last_verified` / the latest OKF `verified[].at` is later than before
- `review_by=` or `stale_after` is later than before, with `verified` unchanged
- a dynamic section switched to `cadence=evergreen`
- a file-level review date was removed

## What counts as "unchanged"

For a section: the lines governed by its marker (see the section model in
[docs/design.md](../design.md)), ignoring the marker itself, freshlint suppression
comments, trailing whitespace and blank lines. For a file-level claim: the whole body,
ignoring frontmatter and every marker. `whitespace = "exact"` makes whitespace count.

## Example

```diff
 ## Carriers
-<!-- memory: verified=2026-07-02 review_by=2026-10-02 -->
+<!-- memory: verified=2026-09-30 review_by=2026-12-30 -->
 - Kits ship with two regional ground carriers, Parcel Owl and Desert Freight.
```

```text
context/shipping.md:4: error [freshness-laundering] Section "Carriers" claims re-verification (verified 2026-07-02 -> 2026-09-30) but its content is unchanged
    hint: if you re-checked it without editing, record a receipt in VERIFICATIONS.md:
          - 2026-09-30 verified context/shipping.md#carriers — <what you checked it against>
```

## How to fix

If you really re-checked the content and it is still true, add a receipt in the same
commit or pull request:

```markdown
## 2026-09-30
- verified context/shipping.md#carriers — both carrier contracts confirmed on their portals
```

The receipt must be **added** in the same diff, use the verb `verified`, name the file
(and the section slug, or no slug for the whole file), and carry the **same date** as
the new `verified=`. A receipt that already existed, is dated differently, or names a
different section does not count. The hint names the date or the target that did not match.

If you did not re-check it, revert the date change.

Extending only `review_by=` (or switching to evergreen) cannot be covered by a receipt:
re-verify and move `verified=` forward instead.

## Configuration

```toml
[rules]
freshness-laundering = "error"   # or "warn", "off"
```

Runs only with `--staged` or `--diff`.
