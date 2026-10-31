# temporal-language

**Default severity:** warning
**Mode:** state
**Inline suppression:** allowed

Governed content uses wording that silently goes stale:

1. relative time phrases such as "currently", "as of now", "next week", "this
   quarter", "recently" or "soon" (configurable list), or
2. a deadline-style phrase ("target", "deadline", "due", "by", "until", "launch",
   "close", "expected", "current", ...) within 60 characters of a quarter or month that
   has already ended, such as "Target close by June 2026" on 2026-09-30.

Code spans and fenced code blocks are ignored. This rule is advisory: it cannot tell
whether a sentence is still true, only that its wording depends on when it is read.

## Example

```markdown
- We currently ship to three regions.
- Target launch by June 2026.
```

```text
context/shipping.md:12: warning [temporal-language] relative time phrase "currently" goes stale silently; anchor it to a date (for example "as of 2026-09-30")
context/shipping.md:13: warning [temporal-language] deadline-style reference to June 2026 has passed (period ended 2026-06-30); restate it as a dated fact or update it
```

## How to fix

Replace relative wording with absolute dates ("As of 2026-09-30, we ship to three
regions"), or update the expired target. Where the phrase is legitimate, for example a
quotation, suppress it with a reason and an expiry:

```markdown
<!-- freshlint: allow=temporal-language reason="direct customer quote" expires=2026-12-01 -->
```

## Configuration

```toml
temporal_phrases = ["currently", "as of now", "for now"]  # replaces the default list

[rules]
temporal-language = "off"
```
