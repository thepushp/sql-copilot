import shutil
import subprocess
from pathlib import Path

import pytest


JS_TESTS = Path(__file__).resolve().parent / "js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_viewer_layout_and_selection_logic():
    """Runs tests/js/er_viewer.test.js with Node's built-in test runner."""

    result = subprocess.run(
        ["node", "--test", *map(str, sorted(JS_TESTS.glob("*.test.js")))],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    assert "# fail 0" in result.stdout
