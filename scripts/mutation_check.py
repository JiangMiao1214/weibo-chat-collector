"""Four deterministic source mutations, each in a fresh interpreter.

Modified source is compiled in memory BEFORE tests import the target. Repository
source, the user's DB and auth directory are never copied or changed.
"""
from __future__ import annotations

import argparse
import importlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

# Stable anchors deliberately fail when implementation moves: review, don't skip.
MUTATIONS = {
    "account-filter": (
        "app.api.messages", "if account_id is not None:", "if False:",
        "test_messages_http.MessagesHttpTests.test_account_only_filter_isolates_messages_and_attachment_totals",
    ),
    "attachment-count": (
        "app.api.messages", "COUNT(att.id) AS total_count", "COUNT(DISTINCT m.id) AS total_count",
        "test_messages_http.MessagesHttpTests.test_account_only_filter_isolates_messages_and_attachment_totals",
    ),
    "resume-cursor": (
        "app.api.collection_jobs",
        "SET status = 'queued', finished_at = NULL, stop_requested_at = NULL,",
        "SET status = 'queued', next_max_mid = '0', finished_at = NULL, stop_requested_at = NULL,",
        "test_collection_worker.CollectionWorkerIntegrationTests.test_failed_page_is_not_retried_and_same_job_resumes_from_checkpoint",
    ),
    "deduplication": (
        "app.importer", "return row is not None", "return False",
        "test_weibo_api_integration.WeiboApiDatabaseIntegrationTests.test_repeated_ingest_is_idempotent_even_after_soft_delete",
    ),
}


def classify(result: unittest.TestResult, mutate: bool, expected_test: str) -> str:
    if result.errors or result.skipped or result.testsRun != 1:
        return "ERROR"
    if mutate:
        # An assertion in the named behavior test is the only accepted kill.
        if len(result.failures) == 1 and result.failures[0][0].id() == expected_test:
            return "KILLED"
        return "SURVIVED" if result.wasSuccessful() else "ERROR"
    return "BASELINE_PASS" if result.wasSuccessful() else "BASELINE_FAIL"


def run_one(name: str, mutate: bool) -> int:
    module_name, original, replacement, test_name = MUTATIONS[name]
    if mutate:
        module = importlib.import_module(module_name)
        source = Path(module.__file__).read_text(encoding="utf-8")
        expected_matches = 2 if name == "deduplication" else 1
        if source.count(original) != expected_matches:
            print(json.dumps({"name": name, "status": "ERROR", "reason": "source anchor changed"}))
            return 2
        exec(compile(source.replace(original, replacement), module.__file__, "exec"), module.__dict__)
    output = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName(test_name)
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    status = classify(result, mutate, test_name)
    print(json.dumps({"name": name, "status": status, "tests": result.testsRun, "log": output.getvalue()}))
    return 0 if status in {"KILLED", "BASELINE_PASS"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--one", choices=MUTATIONS)
    parser.add_argument("--mutate", action="store_true")
    args = parser.parse_args()
    if args.one:
        return run_one(args.one, args.mutate)
    records = []
    for mutate in (False, True):
        for name in MUTATIONS:
            command = [sys.executable, __file__, "--one", name]
            if mutate:
                command.append("--mutate")
            try:
                run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
                record = json.loads(run.stdout)
            except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as error:
                record = {"name": name, "status": "ERROR", "reason": str(error)}
                records.append(record)
                break
            records.append(record)
            print(f"{name}: {record['status']}", flush=True)
            if run.returncode != 0:
                print(record.get("log", run.stderr), file=sys.stderr)
                break
        if len(records) != (8 if mutate else 4) or any(r["status"] not in {"KILLED", "BASELINE_PASS"} for r in records):
            break
    report = ROOT / ".tdd-results"
    report.mkdir(exist_ok=True)
    (report / "mutation.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = len(records) == 8 and all(r["status"] in {"KILLED", "BASELINE_PASS"} for r in records)
    print(f"Targeted mutation check: {'PASS (4/4)' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
