# review-expired

**Default severity:** error
**Mode:** state
**Inline suppression:** allowed

A section's `review_by=`, a file's `review_by`, or an OKF `stale_after` date has
passed (error), or will pass within `review_warn_days` (warning, default 14).

## Example

```markdown
## Carriers
<!-- memory: verified=2026-07-02 review_by=2026-09-28 -->
```

```text
context/shipping.md:4: error [review-expired] Section "Carriers" is past review: review_by=2026-09-28 (2 days ago)
context/shipping.md:9: warning [review-expired] Section "Rates" review is due in 2 days (review_by=2026-10-02)
```

## Boundaries

- `review_by` is the last good day. With `--today` equal to `review_by` the finding is
  a warning ("due today"); the day after it is an error.
- OKF `stale_after` is an instant: the content is stale once `now >= stale_after`.
  freshlint works at day granularity and treats the whole UTC day containing
  `stale_after` as stale.
- `cadence=evergreen` claims and OKF `status: deprecated` files never expire.
- `max_age_days = N` (off by default) treats a claim with a verified date but no review
  date as due N days after it was verified.

## How to fix

Re-check the content against its source. Then either edit it and move the dates, or,
if it is still true, move `verified=` forward and add a receipt (see
[freshness-laundering](freshness-laundering.md)). Moving the date without either is
exactly what `freshness-laundering` reports.

## Configuration

```toml
review_warn_days = 14
max_age_days = 0

[rules]
review-expired = "error"   # "warn" turns expiries into warnings
```
