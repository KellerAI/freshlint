#!/usr/bin/env bash
# The composite action's opt-in upload to freshlint Team. action.yml runs it only when the
# workflow sets `team: true`; with the default `team: false` this file is never executed.
#
# It runs the same check as the "Run freshlint" step (same arguments from build_args.sh, with
# --format json), turns the report into a payload with `python -m freshlint.team_payload`
# (paths, rule ids, counts and static rule summaries; never file contents, finding messages
# or hints), requests a GitHub Actions OIDC token, and sends exactly one POST to FL_TEAM_URL.
#
# The token is masked as soon as it is read, is written only to a 0600 header file under
# RUNNER_TEMP that curl reads with -H @file, and never appears on a command line. The
# temporary directory is removed on exit.
#
# Inputs (environment): the FL_* variables build_args.sh reads, FL_TEAM_URL, FL_TEAM_AUDIENCE,
# ACTIONS_ID_TOKEN_REQUEST_URL and ACTIONS_ID_TOKEN_REQUEST_TOKEN (set by the runner when the
# job has `permissions: id-token: write`), GITHUB_REPOSITORY, GITHUB_REPOSITORY_ID, GITHUB_SHA
# and GITHUB_ACTION_PATH.
set -euo pipefail

notice() { echo "::notice title=freshlint team::$1"; }
warn() { echo "::warning title=freshlint team::$1"; }

if [[ -z "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" || -z "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" ]]; then
  notice "upload skipped: no GitHub OIDC token is available (fork pull requests and Dependabot runs get none; other workflows need 'permissions: id-token: write'). The check result is unchanged."
  exit 0
fi

if ! python - <<'PY'; then
import os
import sys
from urllib.parse import urlsplit

url = os.environ.get("FL_TEAM_URL", "")
try:
    parts = urlsplit(url)
    host = parts.hostname
    port_ok = parts.port is None or parts.port > 0
except ValueError:
    sys.exit(1)
local = host in ("127.0.0.1", "localhost", "::1")
ok = (
    port_ok
    and parts.username is None
    and parts.password is None
    and bool(host)
    and (parts.scheme == "https" or (parts.scheme == "http" and local))
)
sys.exit(0 if ok else 1)
PY
  warn "upload skipped: team-url must be an https:// URL (http:// only for localhost). The check result is unchanged."
  exit 1
fi

tmp="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/freshlint-team.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
chmod 700 "$tmp"
umask 077

# 1. The same check as the "Run freshlint" step, as JSON. A bad input was already reported by
#    that step, so its ::error:: line goes to a file here instead of being repeated.
if ! bash "$GITHUB_ACTION_PATH/scripts/build_args.sh" json >"$tmp/args"; then
  warn "upload skipped: the check arguments are invalid (see the Run freshlint step)."
  exit 1
fi
args=()
while IFS= read -r -d '' arg; do args+=("$arg"); done <"$tmp/args"

status=0
freshlint "${args[@]}" >"$tmp/report.json" 2>/dev/null || status=$?
if [[ "$status" -gt 1 ]]; then
  warn "upload skipped: freshlint check exited $status (see the Run freshlint step)."
  exit 1
fi

# 2. The payload: a pure transform with no network code.
if ! python -m freshlint.team_payload <"$tmp/report.json" >"$tmp/payload.json"; then
  warn "upload skipped: could not build the upload payload."
  exit 1
fi

# 3. The OIDC token. The runner's request token also goes in a header file, not on argv.
audience="$(python -c 'import os, urllib.parse; print(urllib.parse.quote(os.environ.get("FL_TEAM_AUDIENCE", ""), safe=""))')"
if [[ -z "$audience" ]]; then
  warn "upload skipped: team-audience is empty."
  exit 1
fi
printf 'Authorization: Bearer %s\n' "$ACTIONS_ID_TOKEN_REQUEST_TOKEN" >"$tmp/request-header"
if ! curl -sS --fail --max-time 30 --max-redirs 0 --proto '=http,https' \
  -H @"$tmp/request-header" -o "$tmp/token.json" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=${audience}"; then
  warn "upload skipped: could not get a GitHub OIDC token. The check result is unchanged."
  exit 1
fi
rm -f "$tmp/request-header"

if ! token="$(
  python - "$tmp/token.json" <<'PY'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        value = json.load(handle).get("value")
except (OSError, ValueError, AttributeError):
    sys.exit(1)
if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,16384}", value):
    sys.exit(1)
print(value)
PY
)"; then
  warn "upload skipped: the OIDC token response was not usable."
  exit 1
fi
echo "::add-mask::$token"
rm -f "$tmp/token.json"
printf 'Authorization: Bearer %s\n' "$token" >"$tmp/auth-header"
unset token

# 4. Exactly one POST. No retries and no redirects, so the token goes to team-url only.
code="$(
  curl -sS --max-time 60 --max-redirs 0 --proto '=http,https' \
    -H @"$tmp/auth-header" -H 'Content-Type: application/json' \
    --data-binary @"$tmp/payload.json" -o "$tmp/response" -w '%{http_code}' \
    "$FL_TEAM_URL"
)" || code="000"
rm -f "$tmp/auth-header"

if [[ "$code" == 2?? ]]; then
  echo "freshlint team: uploaded the run summary (HTTP $code)"
else
  warn "upload failed (HTTP $code). The check result is unchanged."
  exit 1
fi
