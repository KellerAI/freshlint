"""The README shows the demo output verbatim; these tests keep all three in sync."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_cli

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "demo.sh"
GOLDEN = ROOT / "examples" / "demo-output.txt"
ACME = ROOT / "examples" / "acme-memory"

needs_shell = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("perl") is None, reason="needs bash and perl"
)


def run_demo():
    env = dict(os.environ)
    env["FRESHLINT_CMD"] = f"{sys.executable} -m freshlint"
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(
        ["bash", str(DEMO)], capture_output=True, text=True, env=env, cwd=ROOT, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@needs_shell
def test_demo_output_matches_golden_file():
    assert run_demo() == GOLDEN.read_text()


def test_readme_shows_the_golden_output_verbatim():
    readme = (ROOT / "README.md").read_text()
    match = re.search(
        r"<!-- demo-output:start -->\n```text\n(.*?)```\n<!-- demo-output:end -->", readme, re.S
    )
    assert match, "README is missing the demo-output block"
    assert match.group(1) == GOLDEN.read_text()


def test_golden_shows_the_headline_failure_and_the_fix():
    text = GOLDEN.read_text()
    assert "[freshness-laundering]" in text
    assert text.count("[exit 1]") == 1
    assert text.rstrip().endswith("[exit 0]")


def test_acme_memory_is_clean_on_the_demo_date():
    result = run_cli(ACME, "check", "--today", "2026-09-30", "--format", "json")
    assert result.code == 0
    assert result.rules() == ["review-expired"]  # the one documented warning
    assert result.rules("warning") == ["review-expired"]


def test_acme_memory_fails_after_its_review_dates():
    result = run_cli(ACME, "check", "--today", "2026-12-31", "--format", "json")
    assert result.code == 1
    assert set(result.rules()) == {"review-expired"}
    assert len(result.findings) == 7
