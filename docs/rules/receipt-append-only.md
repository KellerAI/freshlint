# receipt-append-only

**Default severity:** error
**Mode:** diff
**Inline suppression:** not allowed

A receipt that existed in the base version of a receipt log was edited or removed.
Receipts are evidence; rewriting one (for example changing an old entry's date to
today) would let it cover a new freshness bump. Append a new entry instead.

Only receipt entries are compared. Editing surrounding prose or headings that carry no
receipts is fine.

## Example

```diff
-## 2026-09-15
+## 2026-09-30
 - verified context/company.md#team — staffing confirmed in the weekly operations review
```

```text
VERIFICATIONS.md:1: error [receipt-append-only] 1 existing receipt was edited or removed (2026-09-15 verified context/company.md#team); receipt logs are append-only
```

## How to fix

Restore the original entry and add a new, dated one below it. Deleting the whole log is
reported the same way.

## Configuration

```toml
[rules]
receipt-append-only = "error"
```
