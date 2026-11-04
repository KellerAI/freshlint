"""freshlint.team_payload (FT-3): the pure transform behind the opt-in Team upload, and its
contract, schemas/team-run-v1.schema.json."""

import ast
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from freshlint import team_payload
from freshlint.catalog import RULES
from freshlint.team_payload import PayloadError, build_payload, dumps
from team_support import MARKER, build_marker_repo

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "team"
SCHEMA = json.loads((ROOT / "schemas" / "team-run-v1.schema.json").read_text())
# format_checker: ran_at is format date-time (asserted with the rfc3339-validator dev extra).
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)
META = {
    "repository_id": 123456789,
    "repo": "KellerAI/freshlint-sandbox",
    "sha": "0123456789abcdef0123456789abcdef01234567",
    "ran_at": "2026-09-30T12:00:00Z",
}
ENV = {
    "GITHUB_REPOSITORY": META["repo"],
    "GITHUB_REPOSITORY_ID": str(META["repository_id"]),
    "GITHUB_SHA": META["sha"],
}


def report(*findings, mode="full", **summary):
    counts = {"errors": 0, "warnings": 0, "files": 1, **summary}
    return {
        "tool": "freshlint",
        "version": "0.3.0",
        "schema": 1,
        "mode": mode,
        "range": None,
        "today": "2026-09-30",
        "summary": counts,
        "findings": list(findings),
    }


def finding(rule="review-expired", message=f"Section {MARKER!r} is past review", **extra):
    return {
        "rule": rule,
        "severity": "error",
        "path": "context/company.md",
        "line": 7,
        "message": message,
        "hint": f"re-check {MARKER}",
        **extra,
    }


def run_module(stdin, *args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "freshlint.team_payload", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), **(env or {})},
    )


# ----------------------------------------------------------------- the transform


def test_finding_keeps_path_line_severity_and_rule_as_kind():
    payload = build_payload(report(finding()), **META)
    assert payload["findings"] == [
        {
            "path": "context/company.md",
            "line": 7,
            "kind": "review-expired",
            "severity": "error",
            "message": RULES["review-expired"].summary,
        }
    ]


def test_message_is_the_catalog_summary_never_the_finding_message():
    for rule in RULES:
        payload = build_payload(report(finding(rule=rule)), **META)
        assert payload["findings"][0]["message"] == RULES[rule].summary
        assert MARKER not in json.dumps(payload)


def test_unknown_rule_gets_the_generic_message():
    payload = build_payload(report(finding(rule="some-future-rule")), **META)
    assert payload["findings"][0]["kind"] == "some-future-rule"
    assert payload["findings"][0]["message"] == "Freshlint finding."


def test_hint_and_extra_fields_are_dropped():
    raw = finding(extra_field=MARKER, column=3)
    payload = build_payload(report(raw), **META)
    assert set(payload["findings"][0]) == {"path", "line", "kind", "severity", "message"}
    assert MARKER not in json.dumps(payload)


def test_top_level_metadata_and_summary():
    payload = build_payload(report(mode="diff", errors=2, warnings=1, files=4), **META)
    assert payload == {
        "schema": "freshlint-team-run/1",
        **META,
        "freshlint_version": "0.3.0",
        "mode": "diff",
        "summary": {"errors": 2, "warnings": 1, "files": 4},
        "findings": [],
    }


OPTIONAL_FIELDS = {"ref", "default_branch", "head_sha", "scope"}
GIT = {
    "ref": "refs/pull/7/merge",
    "default_branch": "main",
    "head_sha": "89abcdef0123456789abcdef0123456789abcdef",
    "scope": "all",
}


def test_git_fields_are_optional_and_left_out_when_empty():
    plain = build_payload(report(), **META)
    assert OPTIONAL_FIELDS.isdisjoint(plain)
    empty = build_payload(report(), **META, ref="", default_branch="", head_sha="")
    assert empty == plain
    VALIDATOR.validate(plain)


def test_git_fields_are_kept_when_given():
    payload = build_payload(report(), **META, **GIT)
    assert {key: payload[key] for key in OPTIONAL_FIELDS} == GIT
    VALIDATOR.validate(payload)


@pytest.mark.parametrize(
    "ref",
    ["refs/heads/main", "refs/pull/12/merge", "refs/tags/v1.2.3", "refs/heads/feat/ünï-côdé"],
)
def test_valid_refs_are_accepted_by_transform_and_schema(ref):
    payload = build_payload(report(), **META, ref=ref, default_branch=ref.split("/", 2)[2])
    VALIDATOR.validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ref", "main"),
        ("ref", "heads/main"),
        ("ref", "refs/"),
        ("ref", "refs/heads/a b"),
        ("ref", "refs/heads/a~1"),
        ("ref", "refs/heads/a\\b"),
        ("ref", "refs/heads/a\nb"),
        ("ref", "refs/heads/main\n"),
        ("ref", "refs/heads/" + "x" * 1020),
        ("default_branch", "a b"),
        ("default_branch", "a:b"),
        ("default_branch", "main\n"),
        ("default_branch", "x" * 256),
        ("head_sha", "89abcdef"),
        ("head_sha", "G" * 40),
        ("head_sha", "89ABCDEF0123456789ABCDEF0123456789ABCDEF"),
    ],
)
def test_invalid_git_fields_are_rejected_by_transform_and_schema(field, value):
    with pytest.raises(PayloadError):
        build_payload(report(), **META, **{field: value})
    payload = {**build_payload(report(), **META), field: value}
    if not value.endswith("\n"):  # Python's re `$` also matches before a final newline
        assert not VALIDATOR.is_valid(payload)


@pytest.mark.parametrize("scope", ["all", "paths"])
def test_scope_is_kept_when_given(scope):
    payload = build_payload(report(), **META, scope=scope)
    assert payload["scope"] == scope
    VALIDATOR.validate(payload)


@pytest.mark.parametrize("scope", ["some", "ALL", "path", " paths"])
def test_invalid_scope_is_rejected_by_transform_and_schema(scope):
    with pytest.raises(PayloadError, match="scope"):
        build_payload(report(), **META, scope=scope)
    assert not VALIDATOR.is_valid({**build_payload(report(), **META), "scope": scope})


def test_schema_scope_matches_the_transform():
    assert SCHEMA["properties"]["scope"]["enum"] == list(team_payload.SCOPES)


def test_golden_payload_with_scope_paths_is_the_transform_of_the_golden_report():
    proc = run_module(
        (FIXTURES / "report-sample.json").read_text(),
        *("--repository", META["repo"], "--repository-id", str(META["repository_id"])),
        *("--sha", META["sha"], "--ran-at", META["ran_at"], "--ref", "refs/heads/main"),
        *("--default-branch", "main", "--scope", "paths"),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == (FIXTURES / "valid-with-scope-paths.payload.json").read_text()


def test_schema_patterns_match_the_transform():
    props = SCHEMA["properties"]
    assert props["ref"]["pattern"] == f"^{team_payload.REF_RE.pattern}$"
    assert props["default_branch"]["pattern"] == f"^{team_payload.BRANCH_RE.pattern}$"
    assert props["head_sha"]["pattern"] == f"^{team_payload.SHA_RE.pattern}$"
    assert props["ref"]["maxLength"] == team_payload.MAX_REF_CHARS
    assert props["default_branch"]["maxLength"] == team_payload.MAX_BRANCH_CHARS


def test_range_and_today_are_not_copied():
    raw = report()
    raw["range"] = f"origin/{MARKER}...HEAD"
    payload = build_payload(raw, **META)
    assert "range" not in payload and "today" not in payload
    assert MARKER not in json.dumps(payload)


@pytest.mark.parametrize(
    "kind",
    ["Review-Expired", "review expired", "", "a" * 65, 'Section "x"', "rule\n", 7, None],
)
def test_rule_must_be_a_valid_kind(kind):
    with pytest.raises(PayloadError, match="not a valid kind"):
        build_payload(report(finding(rule=kind)), **META)


def test_sixty_four_character_kind_is_accepted():
    payload = build_payload(report(finding(rule="a" * 64)), **META)
    assert payload["findings"][0]["kind"] == "a" * 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("severity", "note"),
        ("line", -1),
        ("line", "7"),
        ("line", True),
        ("path", ""),
        ("path", None),
        ("path", "x" * 4097),
    ],
)
def test_invalid_finding_fields_are_rejected(field, value):
    with pytest.raises(PayloadError):
        build_payload(report(finding(**{field: value})), **META)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("repository_id", 0),
        ("repository_id", "123"),
        ("repository_id", True),
        ("repo", "no-slash"),
        ("repo", "owner/name/extra"),
        ("repo", f"owner/{MARKER} x"),
        ("sha", "0123456"),
        ("sha", "0123456789ABCDEF0123456789ABCDEF01234567"),
        ("ran_at", "2026-09-30 12:00:00"),
        ("ran_at", "2026-09-30T12:00:00+02:00"),
        ("ran_at", "2026-02-30T12:00:00Z"),
        ("ran_at", "2026-02-29T12:00:00Z"),
        ("ran_at", "2026-09-30T24:00:00Z"),
        ("ran_at", "2026-09-30T12:60:00Z"),
        ("ran_at", "2026-12-31T23:59:60Z"),
        ("repository_id", 9007199254740992),
    ],
)
def test_invalid_metadata_is_rejected(key, value):
    with pytest.raises(PayloadError):
        build_payload(report(), **{**META, key: value})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(tool="other"),
        lambda r: r.update(schema=2),
        lambda r: r.update(mode="auto"),
        lambda r: r.update(version="dev build"),
        lambda r: r.pop("summary"),
        lambda r: r["summary"].update(errors=-1),
        lambda r: r["summary"].pop("files"),
        lambda r: r.update(findings={}),
    ],
)
def test_invalid_reports_are_rejected(mutate):
    raw = report()
    mutate(raw)
    with pytest.raises(PayloadError):
        build_payload(raw, **META)


def test_not_a_report():
    for raw in ([], "text", None, 3):
        with pytest.raises(PayloadError):
            build_payload(raw, **META)


def test_integer_limits():
    payload = build_payload(
        report(finding(line=2147483647), errors=2147483647, warnings=2147483647, files=2147483647),
        **{**META, "repository_id": 9007199254740991, "ran_at": "2028-02-29T23:59:59.5Z"},
    )
    VALIDATOR.validate(payload)
    with pytest.raises(PayloadError):
        build_payload(report(finding(line=2147483648)), **META)
    for key in ("errors", "warnings", "files"):
        with pytest.raises(PayloadError):
            build_payload(report(**{key: 2147483648}), **META)


def test_the_format_checker_really_checks_date_time():
    """Without rfc3339-validator, jsonschema silently skips format: date-time."""
    assert "date-time" in Draft202012Validator.FORMAT_CHECKER.checkers
    good = {**json.loads((FIXTURES / "valid-sample.payload.json").read_text())}
    bad = {**good, "ran_at": "2026-02-30T12:00:00Z"}
    assert team_payload.RAN_AT_RE.fullmatch(bad["ran_at"])  # the pattern alone accepts it
    assert VALIDATOR.is_valid(good) and not VALIDATOR.is_valid(bad)


def test_findings_cap():
    many = [finding() for _ in range(team_payload.MAX_FINDINGS)]
    assert len(build_payload(report(*many), **META)["findings"]) == team_payload.MAX_FINDINGS
    with pytest.raises(PayloadError, match="more than 5000"):
        build_payload(report(*many, finding()), **META)


def test_payload_bytes_cap():
    long_path = "d/" * 2000 + "x.md"
    payload = build_payload(report(*[finding(path=long_path)] * 300), **META)
    with pytest.raises(PayloadError, match="larger than 1048576"):
        dumps(payload)


def test_dumps_is_compact_and_deterministic():
    payload = build_payload(report(finding()), **META)
    text = dumps(payload)
    assert text.endswith("\n") and "\n" not in text[:-1]
    assert text == dumps(copy.deepcopy(payload))
    assert json.loads(text) == payload


def test_every_catalog_summary_fits_the_schema():
    for rule in RULES.values():
        assert 0 < len(rule.summary) <= 200, rule.id


# ----------------------------------------------------------------- the module CLI


def test_module_reads_stdin_and_github_env():
    proc = run_module(json.dumps(report(finding())), "--ran-at", META["ran_at"], env=ENV)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["repo"] == META["repo"]
    assert payload["repository_id"] == META["repository_id"]
    assert payload["sha"] == META["sha"]
    assert payload["ran_at"] == META["ran_at"]
    VALIDATOR.validate(payload)


def test_module_defaults_ran_at_to_now_in_utc():
    proc = run_module(json.dumps(report()), env=ENV)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert team_payload.RAN_AT_RE.fullmatch(payload["ran_at"])
    VALIDATOR.validate(payload)


def test_arguments_override_env():
    proc = run_module(
        json.dumps(report()),
        "--repository",
        "other/repo",
        "--repository-id",
        "42",
        "--sha",
        "f" * 40,
        "--ran-at",
        META["ran_at"],
        env=ENV,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert (payload["repo"], payload["repository_id"], payload["sha"]) == (
        "other/repo",
        42,
        "f" * 40,
    )


@pytest.mark.parametrize(
    ("stdin", "env"),
    [
        (f"not json {MARKER}", ENV),
        (json.dumps(report(finding(rule=f"{MARKER} X"))), ENV),
        (json.dumps({"tool": MARKER}), ENV),
        (json.dumps(report()), {**ENV, "GITHUB_REPOSITORY_ID": ""}),
        (json.dumps(report()), {**ENV, "GITHUB_REPOSITORY_ID": "１２"}),
        (json.dumps(report()), {**ENV, "GITHUB_SHA": ""}),
    ],
)
def test_module_fails_closed_without_echoing_input(stdin, env):
    proc = run_module(stdin, env=env)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.startswith("freshlint team_payload: ")
    assert MARKER not in proc.stderr


def test_module_has_no_network_code():
    """The transform is stdlib only and imports nothing that can open a connection."""
    tree = ast.parse((ROOT / "src" / "freshlint" / "team_payload.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    stdlib = {"__future__", "argparse", "datetime", "json", "os", "re", "sys", "typing"}
    assert imported <= stdlib | {"freshlint"}
    internal = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} - stdlib
    assert internal == {"freshlint.catalog"}


NETWORK_MODULES = {
    "socket",
    "ssl",
    "http",
    "urllib",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "xmlrpc",
    "asyncio",
    "socketserver",
    "requests",
    "httpx",
    "urllib3",
}


@pytest.mark.parametrize(
    "path", sorted((ROOT / "src" / "freshlint").glob("*.py")), ids=lambda p: p.name
)
def test_package_never_imports_network_modules(path):
    """The CLI and the plugin never make network requests: no module imports a network API."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            assert name.split(".")[0] not in NETWORK_MODULES, f"{path.name} imports {name}"


# ------------------------------------------------- the marker test on the real CLI


@pytest.fixture(scope="module")
def marker_repo(tmp_path_factory):
    return build_marker_repo(tmp_path_factory.mktemp("marker") / "repo")


def real_check(cwd, *args):
    """Run the real `freshlint check --format json` in a subprocess."""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "freshlint", "check", "--format", "json", "--today", "2026-09-30"]
        + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 1, proc.stderr
    return proc.stdout


@pytest.mark.parametrize("mode", [[], ["--diff", "HEAD~1...HEAD"]], ids=["full", "diff"])
def test_marker_never_reaches_the_payload(marker_repo, mode):
    raw = real_check(marker_repo, *mode)
    findings = json.loads(raw)["findings"]

    # Positive control: the CLI's own messages and hints do echo the note text.
    echoing = {f["rule"] for f in findings if MARKER in f["message"] + (f["hint"] or "")}
    if mode:
        assert echoing == set(RULES), sorted(set(RULES) - echoing)
    else:
        assert echoing == {r for r in RULES if RULES[r].mode == "state"}

    proc = run_module(raw, "--ran-at", META["ran_at"], env=ENV)
    assert proc.returncode == 0, proc.stderr
    assert MARKER.lower() not in proc.stdout.lower()
    assert MARKER.split("-")[1] not in proc.stdout  # not even a fragment of it
    payload = json.loads(proc.stdout)
    VALIDATOR.validate(payload)
    assert len(payload["findings"]) == len(findings)
    assert {f["message"] for f in payload["findings"]} <= {r.summary for r in RULES.values()}


# ------------------------------------------------------------------- the contract


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def _objects(node, where="$"):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield where, node
        for key, value in node.items():
            yield from _objects(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _objects(value, f"{where}[{index}]")


def test_every_object_in_the_schema_forbids_additional_properties():
    objects = list(_objects(SCHEMA))
    assert len(objects) == 3  # the run, its summary, a finding
    for where, node in objects:
        assert node.get("additionalProperties") is False, where
        optional = set(node["properties"]) - set(node["required"])
        assert set(node["required"]) <= set(node["properties"]), where
        # team-run/1 grew four optional fields (FT-10); everything else stays required.
        assert optional == (OPTIONAL_FIELDS if where == "$" else set()), where


def test_schema_constants_match_the_transform():
    assert SCHEMA["properties"]["schema"]["const"] == team_payload.SCHEMA
    assert SCHEMA["properties"]["findings"]["maxItems"] == team_payload.MAX_FINDINGS
    assert SCHEMA["$defs"]["finding"]["properties"]["kind"]["pattern"] == "^[a-z0-9-]{1,64}$"
    assert SCHEMA["properties"]["mode"]["enum"] == list(team_payload.MODES)


def test_golden_payload_is_the_transform_of_the_golden_report():
    proc = run_module(
        (FIXTURES / "report-sample.json").read_text(),
        "--repository",
        META["repo"],
        "--repository-id",
        str(META["repository_id"]),
        "--sha",
        META["sha"],
        "--ran-at",
        META["ran_at"],
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == (FIXTURES / "valid-sample.payload.json").read_text()


def test_golden_payload_with_git_fields_is_the_transform_of_the_golden_report():
    proc = run_module(
        (FIXTURES / "report-sample.json").read_text(),
        "--repository",
        META["repo"],
        "--repository-id",
        str(META["repository_id"]),
        "--sha",
        META["sha"],
        "--ran-at",
        META["ran_at"],
        "--ref",
        GIT["ref"],
        "--default-branch",
        GIT["default_branch"],
        "--head-sha",
        GIT["head_sha"],
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == (FIXTURES / "valid-with-git-refs.payload.json").read_text()


def test_git_fields_never_come_from_the_environment():
    env = {**ENV, "GITHUB_REF": "refs/heads/main", "GITHUB_HEAD_REF": "feature"}
    proc = run_module(json.dumps(report()), "--ran-at", META["ran_at"], env=env)
    assert proc.returncode == 0, proc.stderr
    assert OPTIONAL_FIELDS.isdisjoint(json.loads(proc.stdout))


def test_invalid_git_argument_fails_closed():
    proc = run_module(json.dumps(report()), "--ref", f"refs/heads/{MARKER} x", env=ENV)
    assert proc.returncode == 2 and proc.stdout == ""
    assert MARKER not in proc.stderr


def test_schema_integer_limits():
    props = SCHEMA["properties"]
    assert props["repository_id"]["maximum"] == team_payload.MAX_REPOSITORY_ID == 2**53 - 1
    assert SCHEMA["$defs"]["finding"]["properties"]["line"]["maximum"] == team_payload.MAX_INT32
    for key in ("errors", "warnings", "files"):
        assert props["summary"]["properties"][key]["maximum"] == team_payload.MAX_INT32 == 2**31 - 1
    assert props["ran_at"]["format"] == "date-time"


@pytest.mark.parametrize(
    "path", sorted(FIXTURES.glob("valid-*.payload.json")), ids=lambda p: p.name
)
def test_valid_fixtures_validate(path):
    VALIDATOR.validate(json.loads(path.read_text()))


@pytest.mark.parametrize(
    "path", sorted(FIXTURES.glob("invalid-*.payload.json")), ids=lambda p: p.name
)
def test_invalid_fixtures_do_not_validate(path):
    assert not VALIDATOR.is_valid(json.loads(path.read_text()))


def test_fixture_sets_are_not_empty():
    assert len(list(FIXTURES.glob("valid-*.payload.json"))) >= 2
    assert len(list(FIXTURES.glob("invalid-*.payload.json"))) >= 5
    for name in (
        "valid-integer-limits",
        "invalid-line-over-int32",
        "invalid-summary-count-over-int32",
        "invalid-repository-id-over-2-53",
        "invalid-ran-at-impossible-date",
    ):
        assert (FIXTURES / f"{name}.payload.json").is_file(), name
