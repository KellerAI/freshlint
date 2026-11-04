"""Build a freshlint Team upload payload from ``freshlint check --format json`` output.

A pure transform: it reads the check report on stdin and writes a ``freshlint-team-run/1``
payload (``schemas/team-run-v1.schema.json``) on stdout. It has no network code; the composite
action's upload step sends the payload when the workflow sets ``team: true``.

What leaves the runner is built field by field, never copied:

* each finding keeps ``path``, ``line``, ``severity`` and its rule id as ``kind``;
* ``message`` is the rule's static catalog summary, never ``Finding.message``, because the
  CLI's messages and hints can quote note text (headings, marker and frontmatter values, time
  phrases, suppression rule names);
* ``hint`` is dropped.

Usage (in the action, the runner metadata comes from the default GitHub environment)::

    freshlint check --format json | python -m freshlint.team_payload \
        [--ref "$GITHUB_REF"] [--default-branch main] [--head-sha SHA] [--scope all|paths]

``--ref``, ``--default-branch``, ``--head-sha`` and ``--scope`` are optional and are only ever
taken from arguments: they let freshlint Team compare a run with the default branch.

Exit codes: 0 = payload written, 2 = bad input or metadata (nothing written to stdout).
Error messages never echo the input.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from typing import Any

from freshlint.catalog import RULES

SCHEMA = "freshlint-team-run/1"
UNKNOWN_RULE_MESSAGE = "Freshlint finding."
MAX_FINDINGS = 5000
MAX_PAYLOAD_BYTES = 1_048_576
MAX_PATH_CHARS = 4096
MAX_INT32 = 2_147_483_647  # line and summary counts
MAX_REPOSITORY_ID = 9_007_199_254_740_991  # 2**53 - 1: GitHub ids are bigints

KIND_RE = re.compile(r"[a-z0-9-]{1,64}")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
RAN_AT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z")
# A git ref or branch name: no control characters, spaces or ~^:?*[\ (git check-ref-format).
_REF_CHARS = r"[^\u0000-\u0020\u007f~^:?*\[\\]+"
REF_RE = re.compile("refs/" + _REF_CHARS)
BRANCH_RE = re.compile(_REF_CHARS)
MAX_REF_CHARS = 1024
MAX_BRANCH_CHARS = 255
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+([.+-][0-9A-Za-z.+-]{1,32})?")
MODES = ("full", "diff", "staged")
SCOPES = ("all", "paths")
SEVERITIES = ("error", "warning")


class PayloadError(ValueError):
    """The report or the runner metadata cannot be turned into a valid payload."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _count(summary: dict, key: str) -> int:
    value = summary.get(key)
    if not _is_int(value) or not 0 <= value <= MAX_INT32:
        raise PayloadError(f"summary.{key} must be an integer from 0 to {MAX_INT32}")
    return value


def _finding(index: int, raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise PayloadError(f"finding {index} is not an object")
    kind = raw.get("rule")
    if not isinstance(kind, str) or not KIND_RE.fullmatch(kind):
        raise PayloadError(f"finding {index} has a rule id that is not a valid kind")
    path = raw.get("path")
    if not isinstance(path, str) or not 0 < len(path) <= MAX_PATH_CHARS:
        raise PayloadError(f"finding {index} has no valid path")
    line = raw.get("line")
    if not _is_int(line) or not 0 <= line <= MAX_INT32:
        raise PayloadError(f"finding {index} has no valid line")
    severity = raw.get("severity")
    if severity not in SEVERITIES:
        raise PayloadError(f"finding {index} has no valid severity")
    rule = RULES.get(kind)
    return {
        "path": path,
        "line": line,
        "kind": kind,
        "severity": severity,
        "message": rule.summary if rule else UNKNOWN_RULE_MESSAGE,
    }


def _valid_ran_at(value: object) -> bool:
    """An RFC 3339 UTC timestamp (YYYY-MM-DDTHH:MM:SS[.ffffff]Z) naming a real instant."""
    if not isinstance(value, str) or not RAN_AT_RE.fullmatch(value):
        return False
    try:
        dt.datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:  # February 30, hour 24, a leap second
        return False
    return True


def _optional_git_fields(
    ref: str | None, default_branch: str | None, head_sha: str | None, scope: str | None
) -> dict:
    """The optional ref, default_branch, head_sha and scope fields; an empty value is left out."""
    fields: dict[str, str] = {}
    if scope:
        if scope not in SCOPES:
            raise PayloadError('scope must be "all" or "paths"')
        fields["scope"] = scope
    if ref:
        if not isinstance(ref, str) or len(ref) > MAX_REF_CHARS or not REF_RE.fullmatch(ref):
            raise PayloadError("ref must be a full git ref (refs/...)")
        fields["ref"] = ref
    if default_branch:
        if (
            not isinstance(default_branch, str)
            or len(default_branch) > MAX_BRANCH_CHARS
            or not BRANCH_RE.fullmatch(default_branch)
        ):
            raise PayloadError("default branch must be a branch name")
        fields["default_branch"] = default_branch
    if head_sha:
        if not isinstance(head_sha, str) or not SHA_RE.fullmatch(head_sha):
            raise PayloadError("head sha must be 40 lowercase hex characters")
        fields["head_sha"] = head_sha
    return fields


def build_payload(
    report: Any,
    *,
    repository_id: int,
    repo: str,
    sha: str,
    ran_at: str,
    ref: str | None = None,
    default_branch: str | None = None,
    head_sha: str | None = None,
    scope: str | None = None,
) -> dict:
    """Return the payload for one check report. Raises PayloadError on any invalid field.

    ``ref``, ``default_branch``, ``head_sha`` and ``scope`` are optional; each is left out when
    empty. ``scope`` is ``"paths"`` when the check was limited to some paths, else ``"all"``.
    """
    if not _is_int(repository_id) or not 1 <= repository_id <= MAX_REPOSITORY_ID:
        raise PayloadError(f"repository id must be an integer from 1 to {MAX_REPOSITORY_ID}")
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise PayloadError("repository must be owner/name")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise PayloadError("sha must be 40 lowercase hex characters")
    if not _valid_ran_at(ran_at):
        raise PayloadError("ran_at must be an RFC 3339 UTC timestamp (YYYY-MM-DDTHH:MM:SSZ)")
    if not isinstance(report, dict) or report.get("tool") != "freshlint":
        raise PayloadError("input is not a freshlint check report")
    if report.get("schema") != 1:
        raise PayloadError("unsupported freshlint report schema")
    version = report.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise PayloadError("report has no valid freshlint version")
    mode = report.get("mode")
    if mode not in MODES:
        raise PayloadError("report has no valid mode")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise PayloadError("report has no summary")
    findings = report.get("findings")
    if not isinstance(findings, list):
        raise PayloadError("report has no findings list")
    if len(findings) > MAX_FINDINGS:
        raise PayloadError(f"report has more than {MAX_FINDINGS} findings")
    git_fields = _optional_git_fields(ref, default_branch, head_sha, scope)
    return {
        "schema": SCHEMA,
        "repository_id": repository_id,
        "repo": repo,
        "sha": sha,
        **git_fields,
        "ran_at": ran_at,
        "freshlint_version": version,
        "mode": mode,
        "summary": {key: _count(summary, key) for key in ("errors", "warnings", "files")},
        "findings": [_finding(index, raw) for index, raw in enumerate(findings)],
    }


def dumps(payload: dict) -> str:
    """Serialize a payload compactly and deterministically. Raises PayloadError over the cap."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    if len(text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise PayloadError(f"payload is larger than {MAX_PAYLOAD_BYTES} bytes")
    return text


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m freshlint.team_payload",
        description="Turn `freshlint check --format json` output (stdin) into a freshlint Team "
        "upload payload (stdout). Never copies finding messages or hints.",
    )
    parser.add_argument(
        "--repository", help="owner/name (default: $GITHUB_REPOSITORY)", default=None
    )
    parser.add_argument(
        "--repository-id", help="numeric id (default: $GITHUB_REPOSITORY_ID)", default=None
    )
    parser.add_argument("--sha", help="commit SHA (default: $GITHUB_SHA)", default=None)
    parser.add_argument("--ran-at", help="RFC 3339 UTC time (default: now)", default=None)
    parser.add_argument("--ref", help="full git ref, e.g. refs/heads/main (optional)", default=None)
    parser.add_argument(
        "--default-branch",
        help="the repository's default branch, e.g. main (optional)",
        default=None,
    )
    parser.add_argument(
        "--head-sha",
        help="the pull request head commit, when sha is a merge (optional)",
        default=None,
    )
    parser.add_argument(
        "--scope",
        help='"paths" when the check was limited to some paths, else "all" (optional)',
        default=None,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    raw_id = args.repository_id or os.environ.get("GITHUB_REPOSITORY_ID", "")
    try:
        if not raw_id.isascii() or not raw_id.isdigit():
            raise PayloadError("repository id must be a positive integer")
        try:
            report = json.loads(sys.stdin.read())
        except (ValueError, UnicodeDecodeError):
            raise PayloadError("input is not JSON") from None
        payload = build_payload(
            report,
            repository_id=int(raw_id),
            repo=args.repository or os.environ.get("GITHUB_REPOSITORY", ""),
            sha=args.sha or os.environ.get("GITHUB_SHA", ""),
            ran_at=args.ran_at or _now(),
            ref=args.ref,
            default_branch=args.default_branch,
            head_sha=args.head_sha,
            scope=args.scope,
        )
        text = dumps(payload)
    except PayloadError as exc:
        print(f"freshlint team_payload: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
