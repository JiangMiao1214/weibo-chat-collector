"""One local/CI entry point. Fast skips browser and mutation tests, never failures."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def execute(command, environment, timeout):
    process = subprocess.Popen(command, cwd=ROOT, env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, start_new_session=os.name != "nt")
    try:
        output, _ = process.communicate(timeout=timeout)
        return output, process.returncode
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            output, _ = process.communicate(timeout=5)
        return output + "\nERROR: check timed out\n", 124


def test_output_valid(name: str, output: str) -> bool:
    if name == "gate-tests":
        return bool(re.search(r"Ran [1-9]\d* tests?", output)) and "skipped=" not in output
    if name in {"browser-helper", "browser-e2e"}:
        tests = re.search(r"# tests (\d+)", output)
        skipped = re.search(r"# skipped (\d+)", output)
        todo = re.search(r"# todo (\d+)", output)
        return bool(tests and int(tests[1]) > 0 and skipped and int(skipped[1]) == 0 and todo and int(todo[1]) == 0)
    if name == "frontend-tests":
        return bool(re.search(r"Tests\s+\d+ passed", output)) and not re.search(r"\d+ (?:skipped|todo)", output)
    return True


def run_check(name, command, report_dir, timeout):
    started = time.monotonic()
    print(f"\n>>> {name}: {' '.join(command)}", flush=True)
    environment = {**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0", "CI": "true"}
    try:
        output, code = execute(command, environment, timeout)
        if code == 0 and not test_output_valid(name, output):
            code = 2
            output += "\nERROR: tests missing, skipped or unrecognized result format.\n"
    except subprocess.TimeoutExpired as error:
        captured = error.stdout or b""
        output = (captured.decode(errors="replace") if isinstance(captured, bytes) else captured) + "\nERROR: check timed out\n"
        code = 124
    except OSError as error:
        output, code = str(error), 2
    print(output, flush=True)
    (report_dir / f"{name}.log").write_text(output, encoding="utf-8")
    return {"name": name, "command": command, "exit_code": code,
            "status": "passed" if code == 0 else "failed", "seconds": round(time.monotonic() - started, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args()
    npm = shutil.which("npm")
    if not npm:
        print("ERROR: install Node.js/npm first", file=sys.stderr)
        return 2
    steps = [
        ("policy-reminder", ["node", "scripts/tdd-reminder.mjs"], 10),
        ("backend-tests", [sys.executable, "scripts/run_backend_tests.py"], 120),
        ("gate-tests", [sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests", "-p", "test_*.py"], 30),
        ("browser-helper", [npm, "--prefix", "browser-helper", "test", "--", "--test-reporter=tap"], 30),
        ("frontend-tests", [npm, "--prefix", "frontend", "test"], 120),
        ("frontend-build", [npm, "--prefix", "frontend", "run", "build"], 120),
        ("architecture", [sys.executable, "scripts/quality_check.py"], 30),
    ]
    if not args.fast:
        steps.extend([
            ("mutations", [sys.executable, "scripts/mutation_check.py"], 180),
            ("browser-e2e", [npm, "--prefix", "frontend", "run", "test:e2e"], 90),
        ])
    report_dir = ROOT / ".tdd-results" / ("fast" if args.fast else "full")
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {"mode": "fast" if args.fast else "full", "started_at": datetime.now(timezone.utc).isoformat(),
              "status": "running", "checks": []}
    report_path = report_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, command, timeout in steps:
        record = run_check(name, command, report_dir, timeout)
        report["checks"].append(record)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = all(item["exit_code"] == 0 for item in report["checks"])
    report["status"] = "passed" if passed else "failed"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'PASS' if passed else 'FAIL'}: {report_path}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
