# receipt-ordering

**Default severity:** error
**Mode:** state
**Inline suppression:** not allowed

A receipt log is not a trustworthy append-only record:

- a `##` heading that is not an ISO date (`## YYYY-MM-DD`)
- a receipt line with a non-ISO date, no date (and no dated heading above it), or no
  note
- dates out of order: by default each date must be the same as or later than the one
  before it (oldest first); with `order = "descending"` the reverse (newest first,
  the convention of OKF `log.md` files)
- a date later than today

Receipt logs are the files matched by `[receipts] paths` (default
`VERIFICATIONS.md`). Evidence logs with dated headings can be added to the same list to
get the same ordering check.

## Example

```markdown
## 2026-09-20
- verified context/a.md — checked
## 2026-09-01
- verified context/b.md — checked
```

```text
VERIFICATIONS.md:3: error [receipt-ordering] date 2026-09-01 is earlier than 2026-09-20 on line 1; receipt logs are append-only (oldest first)
```

## Receipt line grammar

```text
- [YYYY-MM-DD ]verified|edited PATH[#section-slug] — NOTE
```

The separator may be `—`, `--` or `-`. Bullets that do not name a `.md` path after
`verified`/`edited` are treated as prose and ignored.

## Configuration

```toml
[receipts]
paths = ["VERIFICATIONS.md", "evidence/*.md"]
order = "ascending"   # or "descending"
```
