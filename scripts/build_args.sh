#!/usr/bin/env bash
# Builds the `freshlint check` argument list for the composite action (action.yml) from its
# FL_* environment variables. The action's inputs reach it only through the environment,
# never by interpolation into a script.
#
# Both action steps use it, so the upload scans exactly what the check scanned; they differ
# only in the FORMAT argument.
#
#   . build_args.sh FORMAT     sets the array FRESHLINT_ARGS in the calling shell
#   bash build_args.sh FORMAT  prints the arguments to stdout, each followed by a NUL byte
#
# Inputs: FL_MODE (auto, diff, staged, full), FL_BASE, FL_HEAD, FL_EVENT, FL_STRICT, FL_TODAY,
# FL_PATHS. On a bad input it prints a GitHub ::error:: command to stdout and exits 2 (when
# sourced, it exits the calling shell, as the inline script it replaces did).

freshlint_build_args() {
  local format="$1"
  local mode="$FL_MODE"
  local -a extra
  if [[ "$mode" == "auto" ]]; then
    if [[ "$FL_EVENT" == pull_request* && -n "$FL_BASE" ]]; then mode="diff"; else mode="full"; fi
  fi
  FRESHLINT_ARGS=(check --format "$format")
  case "$mode" in
    diff)
      if [[ -z "$FL_BASE" ]]; then
        echo "::error title=freshlint::diff mode needs a base revision"
        return 2
      fi
      FRESHLINT_ARGS+=(--diff "$FL_BASE...$FL_HEAD")
      ;;
    staged) FRESHLINT_ARGS+=(--staged) ;;
    full) ;;
    *)
      echo "::error title=freshlint::unknown mode '$mode'"
      return 2
      ;;
  esac
  if [[ "$FL_STRICT" == "true" ]]; then FRESHLINT_ARGS+=(--strict); fi
  if [[ -n "$FL_TODAY" ]]; then FRESHLINT_ARGS+=(--today "$FL_TODAY"); fi
  if [[ -n "$FL_PATHS" ]]; then
    read -r -a extra <<<"$FL_PATHS"
    FRESHLINT_ARGS+=("${extra[@]}")
  fi
}

if ! freshlint_build_args "${1-}"; then
  exit 2
fi

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf '%s\0' "${FRESHLINT_ARGS[@]}"
fi
