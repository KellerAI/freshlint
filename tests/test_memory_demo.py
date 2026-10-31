import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "examples" / "memory-demo-output.txt"


def run_demo():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "memory_demo.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_memory_demo_matches_the_golden_file():
    assert run_demo() == GOLDEN.read_text()


def test_readme_shows_the_memory_demo_verbatim():
    readme = (ROOT / "README.md").read_text()
    start = readme.index("<!-- memory-demo:start -->\n") + len("<!-- memory-demo:start -->\n")
    end = readme.index("<!-- memory-demo:end -->")
    assert readme[start:end] == "```text\n" + GOLDEN.read_text() + "```\n"
