"""Test support for the freshlint Team upload (FT-3): the marker repository and a mock server.

Used by tests/test_team_payload.py and tests/test_action_scripts.py, and by the team-* CI jobs:

    python tests/team_support.py marker-repo DEST
    python tests/team_support.py serve --port-file FILE --log FILE --bodies DIR

The marker repository seeds MARKER into every place a finding's message or hint can echo note
text: headings (section titles and slugs), marker values and tokens, frontmatter values,
configured time phrases, suppression rule names, reasons and dates, receipt log headings and
receipt targets. Its head commit changes every file, so a diff-mode check also runs the diff
rules. The positive control (the marker IS in the raw check output) is part of every test.

The mock server plays both the GitHub OIDC token endpoint (GET /token) and the freshlint Team
ingest endpoint (POST /ingest). It records each request as one JSON line, with booleans for
the Authorization checks instead of the header values, and saves POST bodies to files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MARKER = "zqx-leak-4e1f"
TODAY = "2026-09-30"
REQUEST_TOKEN = "fake-runtime-request-token"
AUDIENCE = "freshlint-team"


def fake_oidc_token(seed: str) -> str:
    """The token the mock OIDC endpoint issues for a seed (JWT-shaped, not a real JWT)."""
    digest = hashlib.sha256(f"freshlint-fake-oidc:{seed}".encode()).hexdigest()
    return f"eyJmYWtlIjp0cnVlfQ.{digest}.{digest[::-1]}"


M = MARKER
CONFIG = f"""\
section_level = 2
temporal_phrases = ["{M} soon", "right now"]

[receipts]
paths = ["VERIFICATIONS.md"]
"""

NOTES_BASE = f"""\
---
last_verified: 2026-01-01
review_by: 2026-12-01
---
# Notes

## {M} laundered
<!-- memory: verified=2026-01-01 review_by=2026-12-01 -->
Facts that nobody re-checked.

## {M} edited
<!-- memory: verified=2026-01-01 review_by=2026-12-01 -->
Facts before the edit.

## {M} extended
<!-- memory: verified=2026-01-01 review_by=2026-06-01 -->
Facts with a deadline.
"""

NOTES_HEAD = f"""\
---
last_verified: 2026-01-01
review_by: 2026-12-01
---
# Notes

## {M} laundered
<!-- memory: verified=2026-03-01 review_by=2026-12-01 -->
Facts that nobody re-checked.

## {M} edited
<!-- memory: verified=2026-01-01 review_by=2026-12-01 -->
Facts after the edit.

## {M} extended
<!-- memory: verified=2026-01-01 review_by=2026-12-20 -->
Facts with a deadline.
"""

STATE = f"""\
---
last_verified: {M}
review_by: 2026-12-01
cadence: {M}
---
<!-- freshlint: allow={M} reason="{M} because" expires=2026-12-01 -->
<!-- freshlint: allow=missing-marker reason="{M} old waiver" expires=2026-01-01 -->
<!-- freshlint: allow=missing-marker reason="{M} odd date" expires={M} -->
# State

## {M} expired
<!-- memory: verified=2026-01-01 review_by=2026-02-01 -->
We will ship this {M} soon.

## {M} due
<!-- memory: verified=2026-09-01 review_by=2026-10-02 -->
Close to its review date.

## {M} unmarked
No marker here.

## {M} bad value
<!-- memory: verified={M} review_by=2026-12-01 -->
Bad marker value.

## {M} bad token
<!-- memory: {M} verified=2026-01-01 review_by=2026-12-01 -->
Bad marker token.

## {M} bad key
<!-- memory: verified=2026-01-01 {M}=1 -->
Bad marker key.

## {M} future
<!-- memory: verified=2027-01-01 review_by=2027-02-01 -->
Dated in the future.

## {M} stale
<!-- memory: verified=2026-01-01 review_by=2026-12-01 -->
The launch planned for {M} in March 2026 has passed.
"""

RECEIPTS_BASE = f"""\
# Verifications

## 2026-01-02

- 2026-01-02 verified notes.md#{M}-laundered — {M} checked against the source
"""

RECEIPTS_HEAD = f"""\
# Verifications

## {M}

- 2026-01-03 verified notes.md#{M}-edited — {M} rewritten
- {M} verified notes.md — {M} bad date
"""


def _git(dest: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Marker",
        "GIT_AUTHOR_EMAIL": "marker@example.invalid",
        "GIT_COMMITTER_NAME": "Marker",
        "GIT_COMMITTER_EMAIL": "marker@example.invalid",
        "GIT_AUTHOR_DATE": "2026-09-01T12:00:00Z",
        "GIT_COMMITTER_DATE": "2026-09-01T12:00:00Z",
    }
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(var, None)
    proc = subprocess.run(
        ["git", *args], cwd=dest, env=env, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def build_marker_repo(dest: Path) -> Path:
    """Create the marker repository at dest (two commits) and return dest."""
    dest.mkdir(parents=True, exist_ok=True)
    _git(dest, "init", "-q", "-b", "main")
    (dest / ".freshlint.toml").write_text(CONFIG)
    (dest / "notes.md").write_text(NOTES_BASE)
    (dest / "state.md").write_text("# State\n")
    (dest / "VERIFICATIONS.md").write_text(RECEIPTS_BASE)
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "base")
    (dest / "notes.md").write_text(NOTES_HEAD)
    (dest / "state.md").write_text(STATE)
    (dest / "VERIFICATIONS.md").write_text(RECEIPTS_HEAD)
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "head")
    return dest


class MockServer:
    """Mock GitHub OIDC token endpoint and freshlint Team ingest endpoint on 127.0.0.1."""

    def __init__(self, log: Path, bodies: Path, token: str, status: int = 202):
        self.log = log
        self.bodies = bodies
        self.token = token
        self.status = status
        self.lock = threading.Lock()
        bodies.mkdir(parents=True, exist_ok=True)
        log.touch()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep stderr quiet
                pass

            def _record(self, entry: dict) -> None:
                with server.lock, server.log.open("a") as handle:
                    handle.write(json.dumps(entry, sort_keys=True) + "\n")

            def do_GET(self):  # noqa: N802
                parts = urlsplit(self.path)
                query = parse_qs(parts.query)
                auth = self.headers.get("Authorization", "")
                entry = {
                    "method": "GET",
                    "path": parts.path,
                    "audience": query.get("audience", [None])[0],
                    "api_version": query.get("api-version", [None])[0],
                    "request_token_ok": auth == f"Bearer {REQUEST_TOKEN}",
                }
                self._record(entry)
                if parts.path != "/token" or not entry["request_token_ok"]:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps({"count": 1, "value": server.token}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                with server.lock:
                    index = len(list(server.bodies.iterdir()))
                    (server.bodies / f"body-{index}.json").write_bytes(body)
                self._record(
                    {
                        "method": "POST",
                        "path": urlsplit(self.path).path,
                        "content_type": self.headers.get("Content-Type"),
                        "token_ok": self.headers.get("Authorization") == f"Bearer {server.token}",
                        "body_file": f"body-{index}.json",
                    }
                )
                self.send_response(server.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_PUT(self):  # noqa: N802
                self._record({"method": "PUT", "path": urlsplit(self.path).path})
                self.send_response(405)
                self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> MockServer:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def requests(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="team_support.py")
    sub = parser.add_subparsers(dest="command", required=True)
    repo = sub.add_parser("marker-repo", help="build the marker repository at DEST")
    repo.add_argument("dest", type=Path)
    serve = sub.add_parser("serve", help="run the mock OIDC and ingest server until killed")
    serve.add_argument("--port-file", type=Path, required=True)
    serve.add_argument("--log", type=Path, required=True)
    serve.add_argument("--bodies", type=Path, required=True)
    serve.add_argument("--status", type=int, default=202)
    args = parser.parse_args(argv)
    if args.command == "marker-repo":
        build_marker_repo(args.dest)
        return 0
    seed = os.environ.get("FAKE_OIDC_SEED", "local")
    server = MockServer(args.log, args.bodies, fake_oidc_token(seed), args.status)
    args.port_file.write_text(str(server.port))
    try:
        server.httpd.serve_forever()
    finally:
        server.httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
