"""Collects the reports of one pipeline run into a single build-metrics.json.

The file is published to the ``ci-metrics`` branch (see publish_metrics.sh)
and read by the Prometheus exporter, which turns it into build-quality
metrics for Grafana: test results, coverage, lint issues, vulnerabilities,
image size and quality-gate status.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reports import (  # noqa: E402
    find_report,
    load_json,
    parse_bandit,
    parse_coverage,
    parse_junit,
    parse_pip_audit,
    parse_ruff,
    parse_trivy,
)

COVERAGE_THRESHOLD = 0.80


def job_results() -> dict:
    """Map job id -> result using the ``needs`` context passed in NEEDS_JSON."""
    try:
        needs = json.loads(os.getenv("NEEDS_JSON", "{}"))
    except ValueError:
        return {}
    return {job: info.get("result") for job, info in needs.items()}


def quality_gates(tests, coverage, bandit, pip_audit, trivy) -> dict:
    gates = {}
    if tests is not None:
        gates["tests"] = tests["total"] > 0 and tests["failed"] == 0 and tests["errors"] == 0
    if coverage is not None:
        gates["coverage"] = coverage["line_rate"] >= COVERAGE_THRESHOLD
    if bandit is not None:
        gates["sast"] = bandit["high"] == 0
    if pip_audit is not None:
        gates["sca"] = pip_audit["vulnerabilities"] == 0
    if trivy is not None:
        gates["container"] = trivy["critical_fixable"] == 0
    return gates


def collect(reports_dir: Path) -> dict:
    tests = parse_junit(find_report(reports_dir, "junit.xml"))
    coverage = parse_coverage(find_report(reports_dir, "coverage.xml"))
    bandit = parse_bandit(find_report(reports_dir, "bandit.json"))
    pip_audit = parse_pip_audit(find_report(reports_dir, "pip-audit.json"))
    trivy = parse_trivy(find_report(reports_dir, "trivy.json"))
    lint = parse_ruff(find_report(reports_dir, "ruff.json"))
    image = load_json(find_report(reports_dir, "image.json"))
    smoke = load_json(find_report(reports_dir, "smoke-test.json"))

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "run": {
            "id": int(os.getenv("GITHUB_RUN_ID", "0")),
            "number": int(os.getenv("GITHUB_RUN_NUMBER", "0")),
            "attempt": int(os.getenv("GITHUB_RUN_ATTEMPT", "1")),
            "workflow": os.getenv("GITHUB_WORKFLOW", ""),
            "sha": os.getenv("GITHUB_SHA", ""),
            "branch": os.getenv("GITHUB_REF_NAME", ""),
            "event": os.getenv("GITHUB_EVENT_NAME", ""),
            "actor": os.getenv("GITHUB_ACTOR", ""),
        },
        "jobs": job_results(),
        "tests": tests,
        "coverage": coverage,
        "lint": lint,
        "security": {"bandit": bandit, "pip_audit": pip_audit, "trivy": trivy},
        "artifact": image,
        "smoke_test": smoke,
        "quality_gates": quality_gates(tests, coverage, bandit, pip_audit, trivy),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect pipeline reports into build-metrics.json")
    parser.add_argument("--reports", type=Path, default=Path("artifacts"))
    parser.add_argument("--out", type=Path, default=Path("build-metrics.json"))
    args = parser.parse_args()

    metrics = collect(args.reports)
    args.out.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
