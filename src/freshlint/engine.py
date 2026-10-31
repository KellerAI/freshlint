"""The checks themselves, and the full-scan / diff orchestration."""

from __future__ import annotations

import datetime as dt
import os
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from freshlint import gitio
from freshlint.catalog import RULES
from freshlint.config import Config
from freshlint.markdown import FILE_KEY, Claim, Document, parse_document
from freshlint.markers import Freshness
from freshlint.receipts import Receipt, check_order, parse_receipt_log
from freshlint.report import Finding
from freshlint.suppressions import Suppression, is_suppressed, parse_suppressions

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
# Ported idea: deadline-ish words near a quarter or month that has already ended.
DEADLINE_RE = re.compile(
    r"(?i)\b(?:target(?:s|ed)?|deadline|due|by|until|through|launch(?:es|ed|ing)?|"
    r"close[sd]?|closing|expected|planned|scheduled|current|now)\b.{0,60}?"
    r"(?:\bQ(?P<quarter>[1-4])\s+(?P<qyear>20\d{2})\b|"
    r"\b(?P<month>" + "|".join(MONTHS) + r")\s+(?P<myear>20\d{2})\b)"
)
INLINE_CODE_RE = re.compile(r"`[^`]*`")
DAY = dt.timedelta(days=1)


def _days(count: int) -> str:
    return f"{count} day{'' if count == 1 else 's'}"


def _period_end(match: re.Match[str]) -> tuple[str, dt.date]:
    if match.group("quarter"):
        quarter, year = int(match.group("quarter")), int(match.group("qyear"))
        label = f"Q{quarter} {year}"
        month = quarter * 3
    else:
        month = MONTHS.index(match.group("month").capitalize()) + 1
        year = int(match.group("myear"))
        label = f"{MONTHS[month - 1]} {year}"
    end = dt.date(year, 12, 31) if month == 12 else dt.date(year, month + 1, 1) - DAY
    return label, end


def _who(claim_title: str | None, is_file: bool) -> str:
    return "File" if is_file else f'Section "{claim_title}"'


def _extension(old: Freshness, new: Freshness) -> str | None:
    """How the freshness claim moved forward, if it did."""
    if new.verified is not None and (old.verified is None or new.verified > old.verified):
        return "verified"
    if new.evergreen and not old.evergreen:
        return "evergreen"
    old_exp, new_exp = old.expires_on(), new.expires_on()
    if old_exp is not None and new_exp is not None and new_exp > old_exp:
        return "deadline"
    if old_exp is not None and new_exp is None and not new.deprecated:
        return "deadline-removed"
    return None


def _deadline_text(fresh: Freshness) -> str:
    if fresh.evergreen:
        return "cadence=evergreen"
    parts = []
    if fresh.review_by:
        parts.append(f"review_by={fresh.review_by.isoformat()}")
    if fresh.stale_after:
        parts.append(f"stale_after={fresh.stale_after.isoformat()}")
    return " ".join(parts) or "no review date"


def _iso(day: dt.date | None) -> str:
    return day.isoformat() if day else "none"


class Linter:
    def __init__(self, cfg: Config, today: dt.date, display: Callable[[str], str]):
        self.cfg = cfg
        self.today = today
        self.display = display
        self.findings: list[Finding] = []
        phrases = sorted(cfg.temporal_phrases, key=len, reverse=True)
        self.relative_re = (
            re.compile(
                r"(?i)(?<![\w-])(?:"
                + "|".join(r"\s+".join(map(re.escape, p.split())) for p in phrases)
                + r")(?![\w-])"
            )
            if phrases
            else None
        )

    # ------------------------------------------------------------------ helpers
    def emit(
        self,
        rule: str,
        path: str,
        line: int,
        message: str,
        hint: str | None = None,
        natural: str = "error",
        suppressions: list[Suppression] | None = None,
    ) -> None:
        configured = self.cfg.severity(rule)
        if configured == "off":
            return
        if suppressions and RULES[rule].suppressible and is_suppressed(suppressions, rule, line):
            return
        severity = "warning" if "warning" in (configured, natural) else "error"
        self.findings.append(Finding(rule, severity, self.display(path), line, message, hint))

    @property
    def receipt_log_name(self) -> str:
        for pattern in self.cfg.receipt_paths:
            if not any(c in pattern for c in "*?["):
                return pattern
        return "your receipt log"

    # ------------------------------------------------------------- state rules
    def lint_document(self, path: str, text: str) -> Document:
        doc = parse_document(text, self.cfg.whitespace)
        suppressions, problems = parse_suppressions(doc, self.today, self.cfg.max_suppression_days)
        for line, message in problems:
            self.emit("suppression", path, line, message)
        for line, message in doc.problems:
            self.emit("invalid-marker", path, line, message)
        claims = []
        for claim in doc.claims:
            fresh = claim.freshness
            who = _who(claim.title, claim.key == FILE_KEY)
            if fresh.verified and fresh.verified > self.today:
                self.emit(
                    "invalid-marker",
                    path,
                    fresh.line,
                    f"{who} has a verified date in the future "
                    f"({fresh.verified.isoformat()}; today is {self.today.isoformat()})",
                )
                continue
            if fresh.verified and fresh.review_by and fresh.review_by < fresh.verified:
                self.emit(
                    "invalid-marker",
                    path,
                    fresh.line,
                    f"{who} has review_by={fresh.review_by.isoformat()} before "
                    f"verified={fresh.verified.isoformat()}",
                )
                continue
            claims.append(claim)
        self._review(path, claims, suppressions)
        self._missing(path, doc, suppressions)
        self._temporal(path, doc, suppressions)
        return doc

    def _review(self, path: str, claims: list[Claim], suppressions: list[Suppression]) -> None:
        max_age = self.cfg.max_age_days
        for claim in claims:
            fresh = claim.freshness
            if fresh.evergreen or fresh.deprecated:
                continue
            expires = fresh.expires_on(max_age)
            if expires is None:
                continue
            who = _who(claim.title, claim.key == FILE_KEY)
            label = fresh.deadline_label(max_age)
            remaining = (expires - DAY - self.today).days
            stale_kind = label.startswith("stale_after")
            if remaining < 0:
                if stale_kind:
                    message = f"{who} is stale: {label} has been reached"
                elif label.startswith("review_by"):
                    message = f"{who} is past review: {label} ({_days(-remaining)} ago)"
                else:
                    age = _days((self.today - fresh.verified).days)
                    message = f"{who} is past review: verified {age} ago ({label})"
                self.emit(
                    "review-expired",
                    path,
                    fresh.line,
                    message,
                    hint="re-check it against its source, then move its verified and review "
                    "dates forward (add a receipt if the content did not need to change)",
                    suppressions=suppressions,
                )
            elif remaining < self.cfg.review_warn_days:
                if stale_kind:
                    when = f"becomes stale in {_days((expires - self.today).days)}"
                else:
                    when = "is due today" if remaining == 0 else f"is due in {_days(remaining)}"
                self.emit(
                    "review-expired",
                    path,
                    fresh.line,
                    f"{who} review {when} ({label})",
                    natural="warning",
                    suppressions=suppressions,
                )

    def _missing(self, path: str, doc: Document, suppressions: list[Suppression]) -> None:
        level = self.cfg.section_level
        hint = (
            'add "<!-- memory: verified=YYYY-MM-DD review_by=YYYY-MM-DD -->" (or '
            "cadence=evergreen) as the first line after the heading"
        )
        if level:
            targets = [s for s in doc.sections if s.level == level]
            if targets:
                for section in targets:
                    if section.marker is None and not section.marker_invalid:
                        self.emit(
                            "missing-marker",
                            path,
                            section.line,
                            f'Section "{section.title}" has no freshness marker',
                            hint=hint,
                            suppressions=suppressions,
                        )
                return
        if doc.claims or doc.file_claim_invalid or any(s.marker_invalid for s in doc.sections):
            return
        self.emit(
            "missing-marker",
            path,
            1,
            "File has no freshness metadata (frontmatter last_verified or section markers)",
            hint="add last_verified: and review_by: frontmatter, or " + hint,
            suppressions=suppressions,
        )

    def _temporal(self, path: str, doc: Document, suppressions: list[Suppression]) -> None:
        if self.cfg.severity("temporal-language") == "off":
            return
        for number, text in doc.content_lines():
            if number in doc.code_lines:
                continue
            clean = INLINE_CODE_RE.sub("", text)
            message = None
            for match in DEADLINE_RE.finditer(clean):
                label, end = _period_end(match)
                if end < self.today:
                    message = (
                        f"deadline-style reference to {label} has passed (period ended "
                        f"{end.isoformat()}); restate it as a dated fact or update it"
                    )
                    break
            if message is None and self.relative_re is not None:
                relative = self.relative_re.search(clean)
                if relative:
                    phrase = " ".join(relative.group(0).lower().split())
                    message = (
                        f'relative time phrase "{phrase}" goes stale silently; anchor it to a '
                        f'date (for example "as of {self.today.isoformat()}")'
                    )
            if message:
                self.emit(
                    "temporal-language",
                    path,
                    number,
                    message,
                    natural="error",
                    suppressions=suppressions,
                )

    def lint_receipt_log(self, path: str, text: str) -> list[Receipt]:
        receipts, problems, dated = parse_receipt_log(text)
        for line, message in problems:
            self.emit("receipt-ordering", path, line, message)
        for line, message in check_order(dated, self.cfg.receipt_order):
            self.emit("receipt-ordering", path, line, message)
        for line, day in dated:
            if day > self.today:
                self.emit(
                    "receipt-ordering",
                    path,
                    line,
                    f"date {day.isoformat()} is in the future (today is {self.today.isoformat()})",
                )
        return receipts

    # -------------------------------------------------------------- diff rules
    def lint_diff(self, path: str, old: Document, new: Document, added: list[Receipt]) -> None:
        if old.file_claim is not None and new.file_claim is not None:
            self._compare(
                path,
                None,
                None,
                old.file_claim,
                new.file_claim,
                unchanged=old.body_content == new.body_content,
                changed=old.unit_content(FILE_KEY) != new.unit_content(FILE_KEY),
                added=added,
            )
        previous = old.marked_sections
        for slug, section in new.marked_sections.items():
            before = previous.get(slug)
            if before is None:
                continue
            same = old.unit_content(slug) == new.unit_content(slug)
            self._compare(
                path,
                slug,
                section.title,
                before.marker,
                section.marker,
                unchanged=same,
                changed=not same,
                added=added,
            )

    def _compare(
        self,
        path: str,
        slug: str | None,
        title: str | None,
        old: Freshness,
        new: Freshness,
        *,
        unchanged: bool,
        changed: bool,
        added: list[Receipt],
    ) -> None:
        who = _who(title, slug is None)
        target = path + (f"#{slug}" if slug else "")
        extension = _extension(old, new)
        if extension and unchanged:
            if extension == "verified":
                if any(
                    r.verb == "verified"
                    and r.path == path
                    and r.date == new.verified
                    and (r.slug is None if slug is None else r.slug in (None, slug))
                    for r in added
                ):
                    return
                body = "content" if slug else "body"
                message = (
                    f"{who} claims re-verification (verified {_iso(old.verified)} -> "
                    f"{_iso(new.verified)}) but its {body} is unchanged"
                )
                hint = (
                    f"if you re-checked it without editing, record a receipt in "
                    f"{self.receipt_log_name}:\n"
                    f"- {new.verified.isoformat()} verified {target} — "
                    "<what you checked it against>"
                )
                hint += self._near_misses(path, slug, new.verified, added)
            else:
                change = {
                    "evergreen": "switched to cadence=evergreen",
                    "deadline": f"extended its review date ({_deadline_text(old)} -> "
                    f"{_deadline_text(new)})",
                    "deadline-removed": f"dropped its review date ({_deadline_text(old)})",
                }[extension]
                message = f"{who} {change} without re-verifying or changing its content"
                hint = (
                    "re-verify the content and move verified= forward (with a receipt if the "
                    "content did not need to change), or revert the date change"
                )
            self.emit("freshness-laundering", path, new.line, message, hint=hint)
            return
        if not changed or old.verified is None:
            return
        if new.verified is not None and new.verified > old.verified:
            return
        if new.verified == self.today:
            return
        if any(
            r.verb == "edited"
            and r.path == path
            and (r.slug is None if slug is None else r.slug in (None, slug))
            for r in added
        ):
            return
        if slug is None:
            message = (
                f"File content outside marked sections changed but its verified date is still "
                f"{_iso(old.verified)}"
            )
        else:
            message = f"{who} changed but its marker still says verified={_iso(old.verified)}"
        hint = (
            "if this edit re-verified the facts, move verified= (and review_by=) forward; if it "
            f"did not (typo, formatting), record in {self.receipt_log_name}:\n"
            f"- {self.today.isoformat()} edited {target} — <why the facts were not re-checked>"
        )
        self.emit("stale-refresh", path, new.line, message, hint=hint)

    def _near_misses(
        self, path: str, slug: str | None, verified: dt.date, added: list[Receipt]
    ) -> str:
        notes = []
        for r in added:
            if r.verb != "verified" or r.path != path:
                continue
            slug_ok = r.slug is None if slug is None else r.slug in (None, slug)
            if slug_ok and r.date != verified:
                notes.append(
                    f"a receipt for {r.target} was added but it is dated {r.date.isoformat()}; "
                    f"it must match verified={verified.isoformat()}"
                )
            elif not slug_ok:
                anchor = f"#{slug}" if slug else "no anchor (whole file)"
                notes.append(f"the added receipt names #{r.slug}; this claim's target is {anchor}")
        return "".join(f"\nnote: {n}" for n in notes)

    def lint_append_only(self, path: str, old: list[Receipt], new: list[Receipt]) -> None:
        missing = Counter(r.key for r in old) - Counter(r.key for r in new)
        if not missing:
            return
        lost = [r for r in old if r.key in missing]
        shown = "; ".join(r.render() for r in lost[:3])
        more = f" and {len(lost) - 3} more" if len(lost) > 3 else ""
        self.emit(
            "receipt-append-only",
            path,
            1,
            f"{len(lost)} existing receipt{'s were' if len(lost) != 1 else ' was'} edited or "
            f"removed ({shown}{more}); receipt logs are append-only",
            hint="restore the original entries and append a new, dated entry instead",
        )


# ---------------------------------------------------------------- orchestration
def _in_filters(path: str, filters: list[str]) -> bool:
    if not filters:
        return True
    return any(f in ("", ".") or path == f or path.startswith(f.rstrip("/") + "/") for f in filters)


def list_project_files(root: Path, use_git: bool) -> list[str]:
    if use_git:
        return [p for p in gitio.ls_files(root) if (root / p).is_file()]
    found = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in filenames:
            rel = (Path(directory) / name).relative_to(root).as_posix()
            found.append(rel)
    return sorted(found)


def run_full(linter: Linter, root: Path, use_git: bool, filters: list[str]) -> int:
    cfg = linter.cfg
    files = 0
    for rel in list_project_files(root, use_git):
        if not _in_filters(rel, filters):
            continue
        if cfg.is_governed(rel):
            linter.lint_document(rel, (root / rel).read_text(encoding="utf-8", errors="replace"))
            files += 1
        elif cfg.is_receipt_file(rel):
            linter.lint_receipt_log(rel, (root / rel).read_text(encoding="utf-8", errors="replace"))
            files += 1
    return files


def run_diff(
    linter: Linter,
    changes: list[gitio.Change],
    read_old: Callable[[str], str | None],
    read_new: Callable[[str], str | None],
    filters: list[str],
) -> int:
    cfg = linter.cfg
    files = 0
    added: list[Receipt] = []
    for change in changes:
        old_is_log = change.old_path is not None and cfg.is_receipt_file(change.old_path)
        new_is_log = change.new_path is not None and cfg.is_receipt_file(change.new_path)
        if not (old_is_log or new_is_log):
            continue
        before: list[Receipt] = []
        if old_is_log:
            text = read_old(change.old_path)
            if text is not None:
                before = parse_receipt_log(text)[0]
        after: list[Receipt] = []
        report_path = change.new_path or change.old_path
        reported = _in_filters(report_path, filters)
        if new_is_log:
            text = read_new(change.new_path)
            if text is not None:
                if reported:
                    after = linter.lint_receipt_log(change.new_path, text)
                    files += 1
                else:
                    after = parse_receipt_log(text)[0]
        new_keys = Counter(r.key for r in after) - Counter(r.key for r in before)
        for receipt in after:
            if new_keys[receipt.key] > 0:
                added.append(receipt)
                new_keys[receipt.key] -= 1
        if reported:
            linter.lint_append_only(report_path, before, after)
    for change in changes:
        if change.status == "D" or change.new_path is None:
            continue
        if not cfg.is_governed(change.new_path) or not _in_filters(change.new_path, filters):
            continue
        new_text = read_new(change.new_path)
        if new_text is None:
            continue
        new_doc = linter.lint_document(change.new_path, new_text)
        files += 1
        if change.status in ("M", "R") and change.old_path is not None:
            old_text = read_old(change.old_path)
            if old_text is not None:
                old_doc = parse_document(old_text, cfg.whitespace)
                linter.lint_diff(change.new_path, old_doc, new_doc, added)
    return files
