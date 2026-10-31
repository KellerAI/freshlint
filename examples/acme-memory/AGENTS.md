# Working in this memory (instructions for agents)

This is the shared memory of Acme Rocket Supply, a fictional company used to
demonstrate freshlint. Nothing here describes a real business.

- Durable facts live in `context/` and `concepts/`.
- Every `##` section in `context/` starts with a freshness marker:
  `<!-- memory: verified=YYYY-MM-DD review_by=YYYY-MM-DD -->`, or
  `<!-- memory: verified=YYYY-MM-DD cadence=evergreen -->` for facts that do not decay.
- If you change a fact, move `verified=` to today and set a new `review_by=`.
- If you re-check a fact and it is still true, move `verified=` to today **and**
  append a receipt to `VERIFICATIONS.md` saying what you checked it against.
- If you edit wording without re-checking the facts (typos, formatting), leave the
  dates alone and append an `edited` receipt.
- Never move a date you did not re-check. CI runs `freshlint check --diff` and
  fails on freshness laundering.
