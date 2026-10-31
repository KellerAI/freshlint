"""Command-line entry point.

Exit codes: 0 = no errors, 1 = errors (or warnings with --strict; for ``memory check``, any
finding that would be reported to the agent), 2 = usage, configuration, or git error.
``hook`` always exits 0: Claude Code treats a hook exit of 2 as blocking, and nothing may be.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from freshlint import __version__, gitio, hooks, memory, memreport, store
from freshlint.catalog import RULES, docs_path
from freshlint.checks import Context, scan
from freshlint.config import ConfigError, load_config
from freshlint.dates import parse_date
from freshlint.engine import Linter, run_diff, run_full
from freshlint.report import FORMATTERS, summarize


class UsageError(Exception):
    """Bad command-line usage (exit code 2)."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="freshlint",
        description="Evidence-backed memory for Claude Code (memory, hook), and a memory "
        "honesty linter that checks freshness metadata in markdown knowledge bases (check).",
    )
    parser.add_argument("--version", action="version", version=f"freshlint {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    commands.required = True

    check = commands.add_parser("check", help="lint the working tree, the index, or a diff")
    check.add_argument("paths", nargs="*", help="limit the check to these files or directories")
    mode = check.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="check staged changes (index vs HEAD)")
    mode.add_argument(
        "--diff",
        metavar="RANGE",
        help="check a commit range: BASE...HEAD (merge base), BASE..HEAD, or BASE",
    )
    check.add_argument("--format", choices=sorted(FORMATTERS), default="text")
    check.add_argument("--today", metavar="YYYY-MM-DD", help="evaluate dates as of this day")
    check.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    check.add_argument("--config", metavar="FILE", help="use this config file")

    rules = commands.add_parser("rules", help="list the rule catalog")
    rules.add_argument("--format", choices=["text", "json"], default="text")

    mem = commands.add_parser(
        "memory", help="check Claude Code memory notes against the code they cite"
    )
    mem_commands = mem.add_subparsers(dest="memory_command", metavar="ACTION")
    mem_commands.required = True
    for name, text in (
        ("scan", "report every finding (exit 0)"),
        ("check", "exit 1 if any finding would be reported to the agent"),
    ):
        sub = mem_commands.add_parser(name, help=text)
        sub.add_argument("--project", metavar="PATH", default=".", help="project (default: .)")
        sub.add_argument(
            "--memory-dir",
            metavar="DIR",
            help="check this directory instead of the project's memory",
        )
        sub.add_argument(
            "--include",
            metavar="GLOB",
            action="append",
            help="note files (default: *.md); repeatable",
        )
        sub.add_argument("--format", choices=["text", "json"], default="text")

    commands.add_parser("hook", help="run as a Claude Code hook (reads the payload on stdin)")
    return parser


def _rules(fmt: str) -> int:
    if fmt == "json":
        data = [
            {
                "id": r.id,
                "default": r.default,
                "mode": r.mode,
                "suppressible": r.suppressible,
                "summary": r.summary,
                "docs": docs_path(r.id),
            }
            for r in RULES.values()
        ]
        print(json.dumps(data, indent=2))
        return 0
    width = max(len(r) for r in RULES)
    for r in RULES.values():
        print(f"{r.id:<{width}}  {r.default:<7}  {r.mode:<5}  {r.summary}")
    return 0


def _resolve_range(root: Path, spec: str) -> tuple[str, str]:
    if "..." in spec:
        left, right = spec.split("...", 1)
        head = gitio.verify_rev(root, right or "HEAD")
        base = gitio.merge_base(root, gitio.verify_rev(root, left or "HEAD"), head)
    elif ".." in spec:
        left, right = spec.split("..", 1)
        base = gitio.verify_rev(root, left or "HEAD")
        head = gitio.verify_rev(root, right or "HEAD")
    else:
        head = gitio.verify_rev(root, "HEAD")
        base = gitio.merge_base(root, gitio.verify_rev(root, spec), head)
    return base, head


def _filters(paths: list[str], root: Path) -> list[str]:
    result = []
    for raw in paths:
        absolute = (Path.cwd() / raw).resolve()
        try:
            rel = absolute.relative_to(root).as_posix()
        except ValueError:
            raise UsageError(f"{raw} is outside the project root {root}") from None
        result.append("" if rel == "." else rel)
    return result


def _check(args: argparse.Namespace) -> int:
    if args.today:
        today = parse_date(args.today)
        if today is None:
            raise UsageError(f"--today must be an ISO date (YYYY-MM-DD), got {args.today!r}")
    else:
        today = dt.date.today()
    cwd = Path.cwd().resolve()
    top = gitio.toplevel(cwd)
    cfg = load_config(cwd, Path(args.config) if args.config else None, stop_at=top)
    root = cfg.root
    prefix = ""
    in_git = False
    if top is not None:
        try:
            rel = root.relative_to(top).as_posix()
            in_git = True
            prefix = "" if rel == "." else rel + "/"
        except ValueError:
            in_git = False
    filters = _filters(args.paths, root)
    linter = Linter(cfg, today, lambda path: prefix + path)

    if args.staged or args.diff:
        if not in_git:
            flag = "--staged" if args.staged else "--diff"
            raise UsageError(f"{flag} needs a git repository")
        if args.staged:
            base = "HEAD" if gitio.has_head(root) else None
            changes = gitio.diff_changes(root, base or gitio.empty_tree(root), cached=True)

            def read_old(path: str) -> str | None:
                return gitio.show(root, base, path) if base else None

            def read_new(path: str) -> str | None:
                return gitio.show_index(root, path)

            mode, rng = "staged", None
        else:
            base, head = _resolve_range(root, args.diff)
            changes = gitio.diff_changes(root, base, head)

            def read_old(path: str) -> str | None:
                return gitio.show(root, base, path)

            def read_new(path: str) -> str | None:
                return gitio.show(root, head, path)

            mode, rng = "diff", args.diff
        files = run_diff(linter, changes, read_old, read_new, filters)
    else:
        files = run_full(linter, root, in_git, filters)
        mode, rng = "full", None

    findings = sorted(linter.findings, key=lambda f: f.sort_key)
    summary = summarize(findings, files, mode, rng, today.isoformat())
    sys.stdout.write(FORMATTERS[args.format](findings, summary))
    if summary.errors or (args.strict and summary.warnings):
        return 1
    return 0


def _memory(args: argparse.Namespace) -> int:
    given = Path(args.project)
    if not given.is_dir():
        raise UsageError(f"--project {args.project} is not a directory")
    project = memory.project_root_for(given.resolve())
    if args.memory_dir:
        memory_dir = Path(args.memory_dir).resolve()
    else:
        memory_dir = memory.memory_dir_for(project)
    if not memory_dir.is_dir():
        raise UsageError(f"no memory directory at {memory_dir}")
    ctx = Context(project, Path.home())
    patterns = tuple(args.include or ("*.md",))
    result = scan(memory_dir, ctx, store.load_store(memory_dir), patterns=patterns)
    report = memreport.json_report if args.format == "json" else memreport.full_report
    sys.stdout.write(report(result))
    if args.memory_command == "check" and any(f.reported for f in result.findings):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    words = sys.argv[1:] if argv is None else argv
    if words and words[0] == "hook":
        return hooks.main()  # before argparse: a hook must never exit 2, which blocks the agent
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    try:
        if args.command == "rules":
            return _rules(args.format)
        if args.command == "memory":
            return _memory(args)
        return _check(args)
    except (UsageError, ConfigError, gitio.GitError) as exc:
        print(f"freshlint: {exc}", file=sys.stderr)
        return 2
