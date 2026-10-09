"""Local store of workflow runs and the cumulative counters derived from them.

Runs are normalised into small dicts and persisted as JSON, so a restart
neither loses history nor spends GitHub API quota again. Counters and
histograms are incremented exactly once per run attempt, which keeps them
valid (monotonic) Prometheus counters across restarts.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

RUN_DURATION_BUCKETS = (30, 60, 90, 120, 180, 240, 300, 420, 600, 900, 1200)
STAGE_DURATION_BUCKETS = (5, 10, 20, 30, 45, 60, 90, 120, 180, 300, 600)
QUEUE_BUCKETS = (1, 2, 5, 10, 20, 30, 60, 120, 300)

ACTIVE_STATUSES = {"queued", "in_progress", "waiting", "pending", "requested"}
FINISHED_CONCLUSIONS = ("success", "failure")  # runs that actually ran to an outcome


def parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def summarize_run(run: dict) -> dict:
    commit = run.get("head_commit") or {}
    title = run.get("display_title") or (commit.get("message") or "").split("\n")[0]
    return {
        "id": run["id"],
        "number": run.get("run_number"),
        "attempt": run.get("run_attempt", 1),
        "workflow": run.get("name") or "unknown",
        "event": run.get("event") or "unknown",
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "branch": run.get("head_branch") or "unknown",
        "sha": (run.get("head_sha") or "")[:7],
        "title": title[:80],
        "actor": (run.get("actor") or {}).get("login", ""),
        "commit_ts": parse_ts(commit.get("timestamp")),
        "created_ts": parse_ts(run.get("created_at")),
        "started_ts": parse_ts(run.get("run_started_at")),
        "updated_ts": parse_ts(run.get("updated_at")),
        "url": run.get("html_url"),
    }


def summarize_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "name": job.get("name") or "unknown",
        "status": job.get("status"),
        "conclusion": job.get("conclusion"),
        "created_ts": parse_ts(job.get("created_at")),
        "started_ts": parse_ts(job.get("started_at")),
        "completed_ts": parse_ts(job.get("completed_at")),
        "steps": [
            {
                "name": step.get("name") or "unknown",
                "conclusion": step.get("conclusion"),
                "started_ts": parse_ts(step.get("started_at")),
                "completed_ts": parse_ts(step.get("completed_at")),
            }
            for step in job.get("steps") or []
        ],
    }


def span(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or end < start:
        return None
    return end - start


def job_duration(job: dict) -> float | None:
    if job.get("conclusion") in (None, "skipped"):
        return None
    return span(job.get("started_ts"), job.get("completed_ts"))


def job_queue_time(job: dict) -> float | None:
    if job.get("conclusion") == "skipped":
        return None
    return span(job.get("created_ts"), job.get("started_ts"))


def run_end(run: dict) -> float | None:
    ends = [j["completed_ts"] for j in run.get("jobs") or [] if j.get("completed_ts")]
    return max(ends) if ends else run.get("updated_ts")


def first_job_start(run: dict) -> float | None:
    starts = [
        j["started_ts"]
        for j in run.get("jobs") or []
        if j.get("started_ts") and j.get("conclusion") != "skipped"
    ]
    return min(starts) if starts else None


def run_duration(run: dict) -> float | None:
    """Execution time: first job start -> last job end.

    Time spent waiting (concurrency queue, runner pick-up) is reported separately by
    run_wait_time, so a queued run does not look like a slow build.
    """
    return span(first_job_start(run) or run.get("started_ts"), run_end(run))


def run_wait_time(run: dict) -> float | None:
    """Time from the trigger until the first job started (queue + runner pick-up latency)."""
    start = first_job_start(run)
    if start is None or run.get("created_ts") is None:
        return None
    return max(0.0, start - run["created_ts"])


def find_job(run: dict, name: str) -> dict | None:
    return next((j for j in run.get("jobs") or [] if j.get("name") == name), None)


def label_key(labels: dict) -> str:
    return json.dumps(labels, sort_keys=True)


class Store:
    def __init__(self, path: Path | None = None, max_runs: int = 300) -> None:
        self.path = path
        self.max_runs = max_runs
        self.lock = threading.RLock()
        self.runs: dict[str, dict] = {}
        self.counted: set[str] = set()
        self.counters: dict[str, dict[str, float]] = {}
        self.histograms: dict[str, dict[str, dict]] = {}
        self.alerts: list[dict] = []

    # -- persistence -------------------------------------------------------
    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        with self.lock:
            self.runs = data.get("runs", {})
            self.counted = set(data.get("counted", []))
            self.counters = data.get("counters", {})
            self.histograms = data.get("histograms", {})
            self.alerts = data.get("alerts", [])

    def save(self) -> None:
        if not self.path:
            return
        with self.lock:
            payload = json.dumps(
                {
                    "runs": self.runs,
                    "counted": sorted(self.counted),
                    "counters": self.counters,
                    "histograms": self.histograms,
                    "alerts": self.alerts[-100:],
                }
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, self.path)

    # -- counters ----------------------------------------------------------
    def inc(self, name: str, labels: dict, value: float = 1.0) -> None:
        with self.lock:
            series = self.counters.setdefault(name, {})
            key = label_key(labels)
            series[key] = series.get(key, 0.0) + value

    def observe(self, name: str, labels: dict, buckets: tuple, value: float) -> None:
        with self.lock:
            hist = self.histograms.setdefault(name, {}).setdefault(label_key(labels), {})
            if not hist:
                hist.update({"counts": [0] * (len(buckets) + 1), "sum": 0.0, "count": 0})
            index = next((i for i, upper in enumerate(buckets) if value <= upper), len(buckets))
            hist["counts"][index] += 1
            hist["sum"] += value
            hist["count"] += 1

    # -- runs ---------------------------------------------------------------
    def upsert_run(self, summary: dict) -> dict:
        """Insert or refresh a run, keeping details already fetched for the same attempt."""
        key = str(summary["id"])
        with self.lock:
            existing = self.runs.get(key)
            if existing and existing.get("attempt") == summary.get("attempt"):
                for field in ("jobs", "jobs_final", "build_metrics", "metrics_checks"):
                    if field in existing:
                        summary[field] = existing[field]
            self.runs[key] = summary
            return summary

    def record_completion(
        self, run: dict, deploy_job: str, deploy_environment: str, main_branch: str
    ) -> bool:
        """Add a finished run attempt to the cumulative counters. Returns False if already counted."""
        key = f"{run['id']}:{run.get('attempt', 1)}"
        with self.lock:
            if key in self.counted:
                return False
            workflow, conclusion = run["workflow"], run.get("conclusion") or "unknown"
            self.inc(
                "cicd_workflow_runs_total",
                {
                    "workflow": workflow,
                    "branch": run["branch"],
                    "event": run["event"],
                    "conclusion": conclusion,
                },
            )
            duration = run_duration(run)
            if conclusion in FINISHED_CONCLUSIONS and duration is not None:
                self.observe(
                    "cicd_workflow_run_duration_seconds",
                    {"workflow": workflow},
                    RUN_DURATION_BUCKETS,
                    duration,
                )
            for job in run.get("jobs") or []:
                job_conclusion = job.get("conclusion")
                if job_conclusion in (None, "skipped"):
                    continue
                self.inc(
                    "cicd_stage_runs_total",
                    {"workflow": workflow, "stage": job["name"], "conclusion": job_conclusion},
                )
                duration = job_duration(job)
                if job_conclusion in FINISHED_CONCLUSIONS and duration is not None:
                    labels = {"workflow": workflow, "stage": job["name"]}
                    self.observe("cicd_stage_duration_seconds", labels, STAGE_DURATION_BUCKETS, duration)
                queued = job_queue_time(job)
                if queued is not None:
                    self.observe("cicd_stage_queue_seconds", {"workflow": workflow}, QUEUE_BUCKETS, queued)
                if (
                    job["name"] == deploy_job
                    and run["branch"] == main_branch
                    and job_conclusion in FINISHED_CONCLUSIONS
                ):
                    self.inc(
                        "cicd_deployments_total",
                        {"environment": deploy_environment, "conclusion": job_conclusion},
                    )
            self.counted.add(key)
            return True

    def prune(self) -> None:
        with self.lock:
            if len(self.runs) > self.max_runs:
                newest = sorted(self.runs.values(), key=lambda r: r.get("created_ts") or 0, reverse=True)
                self.runs = {str(r["id"]): r for r in newest[: self.max_runs]}
            self.counted = {k for k in self.counted if k.split(":")[0] in self.runs}

    def record_alert(self, alert: dict) -> None:
        with self.lock:
            self.alerts.append(alert)
            self.alerts = self.alerts[-100:]
            self.inc(
                "cicd_alert_notifications_total",
                {"alertname": alert["alertname"], "status": alert["status"], "severity": alert["severity"]},
            )

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.runs.values()]
