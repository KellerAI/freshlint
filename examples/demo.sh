#!/usr/bin/env bash
# freshlint five-minute demo: an agent "refreshes" a memory section by bumping its
# dates without re-checking anything, and freshlint's diff check catches it.
#
# Runs entirely in a throwaway git repository under a temp directory. Uses the
# fictional Acme Rocket Supply memory in examples/acme-memory/.
#
#   examples/demo.sh                         # uses `freshlint` from PATH
#   FRESHLINT_CMD="python -m freshlint" examples/demo.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TODAY="2026-09-30" # pinned so the output below is reproducible
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [[ -n "${FRESHLINT_CMD:-}" ]]; then
  freshlint() {
    # shellcheck disable=SC2086 # word splitting is intended: "python -m freshlint"
    $FRESHLINT_CMD "$@"
  }
fi

# Keep the user's git config (pagers, colors, hooks, signing) out of the demo.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_PAGER=cat
export GIT_AUTHOR_NAME="Demo" GIT_AUTHOR_EMAIL="demo@example.invalid"
export GIT_COMMITTER_NAME="Demo" GIT_COMMITTER_EMAIL="demo@example.invalid"

step() { printf '\n== %s\n' "$*"; }
run() {
  printf '$ %s\n' "$*"
  set +e
  "$@"
  local code=$?
  set -e
  printf '[exit %d]\n' "$code"
}

step "1. Commit a clean memory (one section is due for review in 2 days)"
cp -R "$HERE/acme-memory/." "$WORK/"
cd "$WORK"
git init -q -b main
git add -A
git commit -q -m "memory: import Acme Rocket Supply"
run freshlint check --today "$TODAY"

step "2. An agent silences the warning: new dates, same words"
perl -pi -e 's/verified=2026-07-02 review_by=2026-10-02/verified=2026-09-30 review_by=2026-12-30/' \
  context/shipping.md
run git diff --unified=1
git commit -q -a -m "memory: refresh shipping facts"
run freshlint check --today "$TODAY"

step "3. The diff check sees that nothing was re-verified"
run freshlint check --diff HEAD~1...HEAD --today "$TODAY"

step "4. The honest fix: re-check the carriers, then record a receipt"
cat >>VERIFICATIONS.md <<'EOF'

## 2026-09-30
- verified context/shipping.md#carriers — both carrier contracts confirmed on their portals
EOF
git commit -q -a --amend --no-edit
run freshlint check --diff HEAD~1...HEAD --today "$TODAY"
