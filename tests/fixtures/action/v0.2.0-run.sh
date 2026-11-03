# shellcheck shell=bash
# The "Run freshlint" step script of action.yml at v0.2.0, verbatim (test oracle only).
# tests/test_action_scripts.py checks that scripts/build_args.sh reproduces its stdout,
# stderr and exit code byte for byte. Do not edit.
set -euo pipefail
mode="$FL_MODE"
if [[ "$mode" == "auto" ]]; then
  if [[ "$FL_EVENT" == pull_request* && -n "$FL_BASE" ]]; then mode="diff"; else mode="full"; fi
fi
args=(check --format "$FL_FORMAT")
case "$mode" in
  diff)
    if [[ -z "$FL_BASE" ]]; then
      echo "::error title=freshlint::diff mode needs a base revision"
      exit 2
    fi
    args+=(--diff "$FL_BASE...$FL_HEAD")
    ;;
  staged) args+=(--staged) ;;
  full) ;;
  *)
    echo "::error title=freshlint::unknown mode '$mode'"
    exit 2
    ;;
esac
if [[ "$FL_STRICT" == "true" ]]; then args+=(--strict); fi
if [[ -n "$FL_TODAY" ]]; then args+=(--today "$FL_TODAY"); fi
if [[ -n "$FL_PATHS" ]]; then
  read -r -a extra <<<"$FL_PATHS"
  args+=("${extra[@]}")
fi
freshlint "${args[@]}"
