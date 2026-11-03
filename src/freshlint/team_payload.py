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

    freshlint check --format json | python -m freshlint.team_payload

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

KIND_RE = re.compile(r"[a-z0-9-]{1,64}")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
RAN_AT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+([.+-][0-9A-Za-z.+-]{1,32})?")
MODES = ("full", "diff", "staged")
SEVERITIES = ("error", "warning")


class PayloadError(ValueError):
    """The report or the runner metadata cannot be turned into a valid payload."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _count(summary: dict, key: str) -> int:
    value = summary.get(key)
    if not _is_int(value) or value < 0:
        raise PayloadError(f"summary.{key} must be a non-negative integer")
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
    if not _is_int(line) or line < 0:
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


def build_payload(report: Any, *, repository_id: int, repo: str, sha: str, ran_at: str) -> dict:
    """Return the payload for one check report. Raises PayloadError on any invalid field."""
    if not _is_int(repository_id) or repository_id < 1:
        raise PayloadError("repository id must be a positive integer")
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise PayloadError("repository must be owner/name")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise PayloadError("sha must be 40 lowercase hex characters")
    if not isinstance(ran_at, str) or not RAN_AT_RE.fullmatch(ran_at):
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
    return {
        "schema": SCHEMA,
        "repository_id": repository_id,
        "repo": repo,
        "sha": sha,
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
        )
        text = dumps(payload)
    except PayloadError as exc:
        print(f"freshlint team_payload: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
