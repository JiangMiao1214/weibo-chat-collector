"""unittest runner that fails closed for missing or skipped tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "backend/tests"), pattern="test_*.py")
    if not suite.countTestCases():
        print("ERROR: no backend tests discovered", file=sys.stderr)
        return 2
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
