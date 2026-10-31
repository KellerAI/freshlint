# suppression

**Default severity:** error
**Mode:** state
**Inline suppression:** not allowed

An inline suppression is itself broken: malformed, without a reason, naming an
unknown rule or a rule that cannot be suppressed, already expired, or expiring more
than `max_suppression_days` (default 90) from today. A broken suppression does not
suppress anything, so the original finding is reported too.

## Syntax

```markdown
<!-- freshlint: allow=RULE[,RULE] reason="why this is legitimate" expires=YYYY-MM-DD -->
```

Scope:

- on its own line before the first heading: the whole file
- on its own line inside a section: that section and its subsections
- at the end of a line: that line only

Suppressions inside fenced code blocks are ignored.

## Example

```markdown
<!-- freshlint: allow=temporal-language reason="" expires=2026-10-30 -->
```

```text
context/shipping.md:12: error [suppression] suppression has no reason; say why the exception is legitimate
```

## Boundaries

- `expires` equal to today is still valid; the day after, it is an error.
- `expires` exactly `max_suppression_days` from today is valid; one day more is not.

## Which rules can be suppressed

Only rules that describe a persistent condition: `review-expired`, `missing-marker`
and `temporal-language`. Diff rules (`freshness-laundering`, `stale-refresh`,
`receipt-append-only`) describe a one-time event and are resolved with a receipt.
`invalid-marker`, `receipt-ordering` and `suppression` cannot be suppressed.

## Why suppressions are not the same as `cadence=evergreen`

`cadence=evergreen` is a typed, permanent claim about the content: "this fact does not
decay" (a founding year, a definition). It still carries a verified date and is still
checked for laundering and stale refreshes; it is only exempt from expiry.

A suppression is a temporary, reasoned waiver of one rule at one place, for a
condition you intend to fix. It must expire, so it cannot quietly become permanent.

## Configuration

```toml
max_suppression_days = 90
```
