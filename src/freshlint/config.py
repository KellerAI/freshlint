"""Configuration loading and validation.

Config lives in ``.freshlint.toml`` (top-level keys) or in ``[tool.freshlint]`` of
``pyproject.toml``. It is discovered from the starting directory upward, stopping
at ``stop_at`` (normally the git top level). The directory that holds the config
file is the *project root*: include/exclude globs and receipt targets are relative
to it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from freshlint.catalog import RULES
from freshlint.globs import match_any

DEFAULT_EXCLUDE = [
    ".git/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.tox/**",
    "**/dist/**",
    "**/build/**",
    "**/vendor/**",
]

DEFAULT_TEMPORAL_PHRASES = [
    "currently",
    "as of now",
    "as of today",
    "right now",
    "at the moment",
    "at present",
    "these days",
    "this week",
    "next week",
    "last week",
    "this month",
    "next month",
    "last month",
    "this quarter",
    "next quarter",
    "last quarter",
    "recently",
    "soon",
    "yesterday",
    "tomorrow",
]

TOP_LEVEL_KEYS = {
    "include",
    "exclude",
    "extend_exclude",
    "section_level",
    "review_warn_days",
    "max_age_days",
    "max_suppression_days",
    "whitespace",
    "temporal_phrases",
    "rules",
    "receipts",
}
RECEIPT_KEYS = {"paths", "order"}
SEVERITY_ALIASES = {"error": "error", "warning": "warning", "warn": "warning", "off": "off"}


class ConfigError(Exception):
    """Invalid or unreadable configuration (exit code 2)."""


@dataclass
class Config:
    root: Path
    source: Path | None = None
    include: list[str] = field(default_factory=lambda: ["**/*.md"])
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    extend_exclude: list[str] = field(default_factory=list)
    section_level: int = 0
    review_warn_days: int = 14
    max_age_days: int = 0
    max_suppression_days: int = 90
    whitespace: str = "ignore"
    temporal_phrases: list[str] = field(default_factory=lambda: list(DEFAULT_TEMPORAL_PHRASES))
    rules: dict[str, str] = field(default_factory=dict)
    receipt_paths: list[str] = field(default_factory=lambda: ["VERIFICATIONS.md"])
    receipt_order: str = "ascending"

    def severity(self, rule_id: str) -> str:
        return self.rules.get(rule_id, RULES[rule_id].default)

    def is_receipt_file(self, path: str) -> bool:
        return match_any(self.receipt_paths, path)

    def is_governed(self, path: str) -> bool:
        if self.is_receipt_file(path):
            return False
        if not match_any(self.include, path):
            return False
        return not match_any(self.exclude + self.extend_exclude, path)


def _string_list(key: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{key} must be a list of strings")
    return list(value)


def _int(key: str, value: Any, low: int, high: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise ConfigError(f"{key} must be an integer >= {low}")
    if high is not None and value > high:
        raise ConfigError(f"{key} must be between {low} and {high}")
    return value


def _choice(key: str, value: Any, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise ConfigError(f"{key} must be one of: {', '.join(choices)}")
    return value


def _apply(cfg: Config, data: dict[str, Any], origin: str) -> Config:
    for key in data:
        if key not in TOP_LEVEL_KEYS:
            raise ConfigError(f"{origin}: unknown key {key!r}")
    try:
        if "include" in data:
            cfg.include = _string_list("include", data["include"])
        if "exclude" in data:
            cfg.exclude = _string_list("exclude", data["exclude"])
        if "extend_exclude" in data:
            cfg.extend_exclude = _string_list("extend_exclude", data["extend_exclude"])
        if "section_level" in data:
            cfg.section_level = _int("section_level", data["section_level"], 0, 6)
        if "review_warn_days" in data:
            cfg.review_warn_days = _int("review_warn_days", data["review_warn_days"], 0)
        if "max_age_days" in data:
            cfg.max_age_days = _int("max_age_days", data["max_age_days"], 0)
        if "max_suppression_days" in data:
            cfg.max_suppression_days = _int("max_suppression_days", data["max_suppression_days"], 1)
        if "whitespace" in data:
            cfg.whitespace = _choice("whitespace", data["whitespace"], ("ignore", "exact"))
        if "temporal_phrases" in data:
            cfg.temporal_phrases = _string_list("temporal_phrases", data["temporal_phrases"])
        if "rules" in data:
            if not isinstance(data["rules"], dict):
                raise ConfigError("rules must be a table of rule-id = severity")
            for rule_id, value in data["rules"].items():
                if rule_id not in RULES:
                    raise ConfigError(f"unknown rule {rule_id!r} in [rules]")
                if value not in SEVERITY_ALIASES:
                    raise ConfigError(f"rules.{rule_id} must be one of: error, warn, off")
                cfg.rules[rule_id] = SEVERITY_ALIASES[value]
        if "receipts" in data:
            receipts = data["receipts"]
            if not isinstance(receipts, dict):
                raise ConfigError("receipts must be a table")
            for key in receipts:
                if key not in RECEIPT_KEYS:
                    raise ConfigError(f"{origin}: unknown key 'receipts.{key}'")
            if "paths" in receipts:
                cfg.receipt_paths = _string_list("receipts.paths", receipts["paths"])
            if "order" in receipts:
                cfg.receipt_order = _choice(
                    "receipts.order", receipts["order"], ("ascending", "descending")
                )
    except ConfigError as exc:
        raise ConfigError(f"{origin}: {exc}") from None
    return cfg


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: invalid TOML ({exc})") from None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None


def _from_file(path: Path) -> Config | None:
    data = _read_toml(path)
    if path.name == "pyproject.toml":
        table = data.get("tool", {}).get("freshlint")
        if table is None:
            return None
        return _apply(Config(root=path.parent, source=path), table, "[tool.freshlint]")
    return _apply(Config(root=path.parent, source=path), data, path.name)


def load_config(start: Path, explicit: Path | None = None, stop_at: Path | None = None) -> Config:
    start = start.resolve()
    if explicit is not None:
        explicit = explicit.resolve()
        if not explicit.is_file():
            raise ConfigError(f"config file not found: {explicit}")
        cfg = _from_file(explicit)
        if cfg is None:
            raise ConfigError(f"{explicit.name} has no [tool.freshlint] table")
        return cfg
    boundary = stop_at.resolve() if stop_at is not None else None
    directory = start
    while True:
        dotfile = directory / ".freshlint.toml"
        if dotfile.is_file():
            return _from_file(dotfile)
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            cfg = _from_file(pyproject)
            if cfg is not None:
                return cfg
        if directory == boundary or directory.parent == directory:
            break
        directory = directory.parent
    return Config(root=boundary if boundary is not None else start)
