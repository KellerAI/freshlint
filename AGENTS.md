# AGENTS.md: freshlint

Guidance for coding agents and people working in this repository. The
[README](README.md) is the user documentation; [CONTRIBUTING.md](CONTRIBUTING.md) is the
source for the ground rules summarised here.

## What this repo is

freshlint ties each Claude Code memory note to the code it cites, tells the agent at session
start which notes rest on code that has changed or gone, and pushes back when a note claims
certainty without evidence. It also checks freshness markers in committed markdown. It ships
as a Claude Code plugin (`.claude-plugin/`, `hooks/`, `skills/`, `commands/`, `bin/freshlint`),
a CLI (`src/freshlint/`, entry point `freshlint = "freshlint.cli:main"`), a pre-commit hook
(`.pre-commit-hooks.yaml`) and a composite GitHub Action (`action.yml`).

## Setup and tests

These are the commands in CONTRIBUTING.md and `.github/workflows/ci.yml`:

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
ruff check . && ruff format --check .
```

CI also runs `shellcheck examples/demo.sh bin/freshlint`, compares `examples/demo.sh` and
`examples/memory_demo.py` output with the golden files, and runs `freshlint check` on
`examples/acme-memory`. If you change a message shown in the demo, regenerate the golden
output and the README block:

```bash
FRESHLINT_CMD="python -m freshlint" PYTHONPATH=src examples/demo.sh > examples/demo-output.txt
```

## Conventions

- **No runtime dependencies.** Standard library only; pytest and ruff are dev-only.
- **Deterministic and offline.** The CLI and the plugin never access the network; no clock
  reads except the default for `--today`, sorted output. The only network code is the
  composite action's opt-in upload (`team: true`, `scripts/team_upload.sh`); the payload
  transform (`freshlint.team_payload`) and the job-summary renderer (`freshlint.team_summary`)
  it uses are pure and send nothing.
- **Read-only.** freshlint never writes to the files or repositories it checks.
- **Stable rule IDs.** Renaming or removing a rule is a breaking change. A new rule needs an
  entry in `src/freshlint/catalog.py`, tests, `docs/rules/<rule-id>.md`, a README rule-table
  row and a CHANGELOG entry (`tests/test_docs.py` checks the docs).
- **Test first.** Write the failing test before changing code. Diff-mode tests use real
  temporary git repositories (`tests/conftest.py`); do not mock git.
- **Fictional examples only.** Nothing under `examples/` or in test fixtures may name real
  people, companies or data.
- **Ruff settings** live in `pyproject.toml` (line length 100, target py311).
- Keep agent guidance in this file. Do not add a CLAUDE.md.

## Commits and pull requests

- Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `ci:`, `chore:`), one logical
  change each.
- Agent-authored PRs come from the `son-of-anton-ai` account, on a branch off `main`.
- `main` is protected: every change lands through a PR with one approval, code-owner review
  (see `.github/CODEOWNERS`) and resolved review conversations. No direct pushes, no
  force-pushes, no admin-bypass merges.
- Link the Linear issue in the PR title or body (for example `OPS-673`).
- Fill in the PR template: what changed, why, the linked issue, and test evidence (the
  commands you ran and their results).
- Security reports follow [SECURITY.md](SECURITY.md), not public issues.
