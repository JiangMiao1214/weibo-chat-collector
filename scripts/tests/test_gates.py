import ast
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mutation_check import classify
from quality_check import compare, find_cycles, local_complexity
from verify import run_check, test_output_valid


class QualityGateTests(unittest.TestCase):
    def test_new_complexity_fails_and_old_baseline_cannot_grow(self):
        snapshot = {"metrics": {"complexity:new": 21, "lines:old": 501}, "graph": {}, "duplicates": [], "violations": []}
        baseline = {"metrics": {"lines:old": 500}, "duplicates": []}
        self.assertEqual(len(compare(snapshot, baseline)), 2)

    def test_cycles_and_duplicate_growth_fail(self):
        self.assertTrue(find_cycles({"a": ["b"], "b": ["a"]}))
        self.assertFalse(find_cycles({"a": ["b"], "b": []}))
        self.assertTrue(compare({"metrics": {}, "graph": {}, "duplicates": ["a | b"], "violations": []}, {"metrics": {}, "duplicates": []}))

    def test_local_complexity_does_not_double_count_nested_function(self):
        node = ast.parse("def outer(x):\n def inner(y):\n  if y: return 1\n if x: return 2\n").body[0]
        self.assertEqual(local_complexity(node), 2)


class RunnerGateTests(unittest.TestCase):
    def test_failed_subprocess_and_timeout_are_recorded_as_failures(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            report = Path(directory)
            failure = run_check("bad-command", [sys.executable, "-c", "raise SystemExit(3)"], report, 5)
            self.assertEqual(failure["exit_code"], 3)
            timeout = run_check("slow-command", [sys.executable, "-c", "import time; time.sleep(10)"], report, 0.05)
            self.assertEqual(timeout["exit_code"], 124)
            self.assertIn("timed out", (report / "slow-command.log").read_text())

    def test_zero_skipped_or_unrecognized_tests_cannot_pass(self):
        for output in ("", "# tests 0\n# skipped 0\n# todo 0", "# tests 2\n# skipped 1\n# todo 0"):
            self.assertFalse(test_output_valid("browser-helper", output))
        self.assertTrue(test_output_valid("browser-helper", "# tests 4\n# skipped 0\n# todo 0"))
        self.assertFalse(test_output_valid("frontend-tests", "Tests 1 passed | 2 skipped"))
        self.assertTrue(test_output_valid("frontend-tests", "Tests 4 passed (4)"))

    def test_mutation_infrastructure_error_is_not_a_kill(self):
        result = unittest.TestResult()
        result.testsRun = 1
        result.errors = [(self, "ImportError")]
        self.assertEqual(classify(result, True, self.id()), "ERROR")
        result.errors = []
        self.assertEqual(classify(result, True, self.id()), "SURVIVED")
        result.failures = [(self, "AssertionError")]
        self.assertEqual(classify(result, True, self.id()), "KILLED")
        self.assertEqual(classify(result, True, "wrong_test"), "ERROR")
