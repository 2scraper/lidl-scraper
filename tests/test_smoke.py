"""Wraps smoke_test.py as a single pytest test so `pytest` works as an
entry point too, without a second copy of the checks. smoke_test.py's
checks all run at import time (see its `check()` decorator's docstring);
this just imports it and asserts nothing failed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smoke_test  # noqa: E402


def test_smoke():
    failed = [(name, detail) for name, ok, detail in smoke_test.RESULTS if not ok]
    assert not failed, "\n".join(f"{name}: {detail}" for name, detail in failed)
