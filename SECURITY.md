# Security

## Reporting a vulnerability

Please report security issues privately rather than in a public issue. A dedicated
contact address will be added here before the project is published; until then,
contact the maintainers at Keller AI directly.

Please include the freshlint version, how you ran it, and a minimal reproduction.

## Scope and design

- freshlint reads markdown, TOML and JSON files, the files that memory notes cite, and git
  objects. It never executes content from them, and it never modifies a memory note, a cited
  file or a checked document.
- The CLI and the Claude Code plugin never make network requests.
- Apart from installing Python and freshlint (`actions/setup-python` and `pip`), the composite
  GitHub Action makes no network request unless the workflow sets `team: true`. Then, after
  the check, it requests a GitHub Actions OIDC token and sends one POST to `team-url` with a
  run summary: file paths, line numbers, rule ids, severities, counts and the static rule
  summaries from the catalog. It never sends file contents, finding
  messages or hints, which can quote note text (`python -m freshlint.team_payload` builds the
  body; `schemas/team-run-v1.schema.json` is its contract). The token is masked, kept off
  command lines in a 0600 header file under `RUNNER_TEMP`, and deleted after the request.
  After a 201, the response's comparison is rendered into the job summary by
  `python -I -m freshlint.team_summary`, a pure function that treats the response as
  untrusted: every path, rule id and branch name is shown inside a code span, and the link is
  used only if it is a plain https URL. No further request is made.
  The upload never runs code from the checked-out repository: its Python calls use isolated
  mode (`python -I`, so the working directory is not on `sys.path`), and the runner's
  `ACTIONS_ID_TOKEN_REQUEST_*` variables are removed from the environment before any child
  process starts.
- Git is invoked through `subprocess` with an argument list (no shell), with
  `GIT_OPTIONAL_LOCKS=0`, and only with subcommands that do not change content (`rev-parse`,
  `ls-files`, `show`, `diff`, `merge-base`, `log`, `rev-list`, `cat-file`, `check-ignore`,
  `hash-object` without `-w`). The one side effect is that `git diff`, which `freshlint check
  --staged` and `--diff` run, may refresh git's index stat cache; the plugin's hooks never run it.
- The composite GitHub Action passes every input to its script through environment
  variables rather than interpolating them into the script, to avoid script injection.

### Files the Claude Code plugin writes (v0.2)

- In the memory directory, under `.freshlint/`: `lock.json` (fingerprints: SHA-256 hashes and
  line positions, never code), `receipts.md` (an append-only log) and `lock.lock` (an empty
  advisory lock file).
- In the cache directory (`$FRESHLINT_CACHE_DIR`, else `$XDG_CACHE_HOME/freshlint`, else
  `~/.cache/freshlint`): `sessions/<id>.jsonl` with the path and byte hash of every file the
  agent read or edited in a session, and `sessions/<id>.report` with a report digest, both
  deleted after 7 days; `hook.log` with tracebacks of hook errors (not rotated); and
  `events.jsonl` only when `FRESHLINT_EVENTS=1`.
- None of these hold file contents, but the session ledger lists the paths the agent read, and
  it uses your default file permissions. A fingerprint of a short or guessable value is an
  unsalted hash that can be recovered by guessing, so do not cite secrets.
- `freshlint hook` is built to exit 0 and to print only hook JSON on stdout, so it never blocks
  a tool call.

Treat memory files as untrusted input in your own tooling too: freshness metadata says
when something was checked, not that it is true.
