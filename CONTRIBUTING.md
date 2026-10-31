# Contributing

Thanks for helping. A few ground rules keep freshlint small and trustworthy.

## Principles

- **No runtime dependencies.** The standard library only (`tomllib`, `re`, `json`,
  `argparse`, `subprocess` for git). Development tools such as pytest and ruff are fine.
- **Deterministic and offline.** No network access, no clock reads except the default
  for `--today`, sorted output.
- **Read-only.** freshlint never writes to the files or repositories it checks.
- **Stable rule IDs.** Renaming or removing a rule is a breaking change.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
ruff check . && ruff format --check .
```

## Changing behaviour

Write the test first and watch it fail before changing code. Diff-mode tests use real
temporary git repositories (see `tests/conftest.py`); please keep it that way rather
than mocking git.

To add a rule:

1. add it to `src/freshlint/catalog.py`,
2. implement it with tests,
3. add `docs/rules/<rule-id>.md` and a row in the README rule table (the test suite
   checks both),
4. add a CHANGELOG entry.

If you change any message shown in the demo, regenerate the golden output and the
README block:

```bash
FRESHLINT_CMD="python -m freshlint" PYTHONPATH=src examples/demo.sh > examples/demo-output.txt
```

then paste the file into the README between the `demo-output` markers.
`tests/test_demo.py` fails until both match.

## Commits

Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `ci:`, `chore:`), one logical
change each.

## Example data

Everything under `examples/` must be obviously fictional. Do not add real people,
companies or data, including in test fixtures.
