"""Prometheus collector that turns the run store into metric families at scrape time."""

from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily

from cicd_exporter import __version__
from cicd_exporter.store import (
    ACTIVE_STATUSES,
    FINISHED_CONCLUSIONS,
    QUEUE_BUCKETS,
    RUN_DURATION_BUCKETS,
    STAGE_DURATION_BUCKETS,
    find_job,
    job_duration,
    run_duration,
    run_end,
    run_wait_time,
    span,
)

WINDOWS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
DORA_WINDOW = 7 * 86400
RESTORE_WINDOW = 30 * 86400

COUNTERS = {
    "cicd_workflow_runs": ("Completed workflow runs", ["workflow", "branch", "event", "conclusion"]),
    "cicd_stage_runs": (
        "Completed pipeline stages (GitHub Actions jobs)",
        ["workflow", "stage", "conclusion"],
    ),
    "cicd_deployments": ("Deployments performed by the pipeline", ["environment", "conclusion"]),
    "cicd_alert_notifications": (
        "Alert notifications received from Grafana",
        ["alertname", "status", "severity"],
    ),
}
HISTOGRAMS = {
    "cicd_workflow_run_duration_seconds": ("Workflow run duration", ["workflow"], RUN_DURATION_BUCKETS),
    "cicd_stage_duration_seconds": (
        "Stage (GitHub Actions job) duration",
        ["workflow", "stage"],
        STAGE_DURATION_BUCKETS,
    ),
    "cicd_stage_queue_seconds": ("Time stages waited for a runner", ["workflow"], QUEUE_BUCKETS),
}


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low, high = int(rank), min(int(rank) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def completed(runs: list[dict]) -> list[dict]:
    """Completed runs whose jobs are known, oldest first."""
    done = [r for r in runs if r.get("status") == "completed" and r.get("jobs_final")]
    return sorted(done, key=lambda r: run_end(r) or 0)


def by_workflow(runs: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        grouped[run["workflow"]].append(run)
    return grouped


# -- cumulative counters/histograms ------------------------------------------
def counter_families(counters: dict) -> list:
    families = []
    for name, (doc, labels) in COUNTERS.items():
        family = CounterMetricFamily(name, doc, labels=labels)
        for key, value in counters.get(f"{name}_total", {}).items():
            values = json.loads(key)
            family.add_metric([str(values.get(label, "")) for label in labels], value)
        families.append(family)
    return families


def histogram_families(histograms: dict) -> list:
    families = []
    for name, (doc, labels, buckets) in HISTOGRAMS.items():
        family = HistogramMetricFamily(name, doc, labels=labels)
        for key, hist in histograms.get(name, {}).items():
            values = json.loads(key)
            cumulative, running = [], 0
            for upper, count in zip([*map(str, buckets), "+Inf"], hist["counts"], strict=True):
                running += count
                cumulative.append((upper, running))
            family.add_metric([str(values.get(label, "")) for label in labels], cumulative, hist["sum"])
        families.append(family)
    return families


# -- pipeline activity and performance --------------------------------------
def activity_families(runs: list[dict]) -> list:
    active = GaugeMetricFamily(
        "cicd_workflow_runs_active",
        "Workflow runs currently queued or in progress",
        labels=["workflow", "status"],
    )
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for run in runs:
        if run.get("status") in ACTIVE_STATUSES:
            status = "in_progress" if run["status"] == "in_progress" else "queued"
            counts[(run["workflow"], status)] += 1
    for workflow in {r["workflow"] for r in runs}:
        for status in ("queued", "in_progress"):
            active.add_metric([workflow, status], counts.get((workflow, status), 0))
    return [active]


def window_families(runs: list[dict], now: float) -> list:
    window_runs = GaugeMetricFamily(
        "cicd_workflow_runs_window",
        "Completed runs per conclusion inside a rolling window",
        labels=["workflow", "window", "conclusion"],
    )
    window_duration = GaugeMetricFamily(
        "cicd_workflow_duration_window_seconds",
        "Run duration statistics (avg, p50, p95, max) inside a rolling window",
        labels=["workflow", "window", "stat"],
    )
    for workflow, wf_runs in by_workflow(completed(runs)).items():
        for window, seconds in WINDOWS.items():
            inside = [r for r in wf_runs if (run_end(r) or 0) >= now - seconds]
            per_conclusion: dict[str, int] = defaultdict(int)
            for run in inside:
                per_conclusion[run.get("conclusion") or "unknown"] += 1
            for conclusion in ("success", "failure", "cancelled"):
                per_conclusion.setdefault(conclusion, 0)
            for conclusion, count in per_conclusion.items():
                window_runs.add_metric([workflow, window, conclusion], count)
            durations = [
                d for r in inside if r.get("conclusion") in FINISHED_CONCLUSIONS if (d := run_duration(r))
            ]
            if durations:
                stats = {
                    "avg": statistics.fmean(durations),
                    "p50": percentile(durations, 0.5),
                    "p95": percentile(durations, 0.95),
                    "max": max(durations),
                }
                for stat, value in stats.items():
                    window_duration.add_metric([workflow, window, stat], round(value, 1))
    return [window_runs, window_duration]


def last_run_families(runs: list[dict]) -> list:
    status = GaugeMetricFamily(
        "cicd_workflow_last_run_status",
        "Outcome of the latest run (1 = success, 0 = failure)",
        labels=["workflow", "branch"],
    )
    duration = GaugeMetricFamily(
        "cicd_workflow_last_run_duration_seconds", "Duration of the latest run", labels=["workflow", "branch"]
    )
    finished_at = GaugeMetricFamily(
        "cicd_workflow_last_run_timestamp_seconds",
        "Completion time of the latest run",
        labels=["workflow", "branch"],
    )
    number = GaugeMetricFamily(
        "cicd_workflow_last_run_number", "Run number of the latest run", labels=["workflow", "branch"]
    )
    latest: dict[tuple[str, str], dict] = {}
    for run in completed(runs):
        if run.get("conclusion") in FINISHED_CONCLUSIONS:
            latest[(run["workflow"], run["branch"])] = run
    for (workflow, branch), run in latest.items():
        labels = [workflow, branch]
        status.add_metric(labels, 1 if run["conclusion"] == "success" else 0)
        if (d := run_duration(run)) is not None:
            duration.add_metric(labels, d)
        finished_at.add_metric(labels, run_end(run) or 0)
        number.add_metric(labels, run.get("number") or 0)
    return [status, duration, finished_at, number]


RUN_LABELS = ["workflow", "run_number", "run_id", "branch", "event", "conclusion", "commit", "actor", "title"]


def recent_run_families(runs: list[dict], recent: int) -> list:
    # Duration and wait share the full label set so Grafana can merge them into one table row per run
    run_duration_g = GaugeMetricFamily(
        "cicd_run_duration_seconds", "Duration of each recent run (one series per run)", labels=RUN_LABELS
    )
    run_wait = GaugeMetricFamily(
        "cicd_run_wait_seconds", "Time from trigger until the first job started", labels=RUN_LABELS
    )
    run_jobs = GaugeMetricFamily(
        "cicd_run_stage_duration_seconds",
        "Duration of each stage in recent runs",
        labels=["workflow", "run_number", "stage"],
    )
    run_finished = GaugeMetricFamily(
        "cicd_run_end_timestamp_seconds",
        "Completion time of each recent run",
        labels=["workflow", "run_number"],
    )
    for workflow, wf_runs in by_workflow(completed(runs)).items():
        for run in wf_runs[-recent:]:
            number = str(run.get("number"))
            labels = [
                workflow,
                number,
                str(run["id"]),
                run["branch"],
                run["event"],
                run.get("conclusion") or "unknown",
                run.get("sha", ""),
                run.get("actor", ""),
                run.get("title", ""),
            ]
            if (d := run_duration(run)) is not None:
                run_duration_g.add_metric(labels, d)
            if (w := run_wait_time(run)) is not None:
                run_wait.add_metric(labels, w)
            run_finished.add_metric([workflow, number], run_end(run) or 0)
            for job in run.get("jobs") or []:
                if (jd := job_duration(job)) is not None:
                    run_jobs.add_metric([workflow, number, job["name"]], jd)
    return [run_duration_g, run_wait, run_jobs, run_finished]


def step_families(runs: list[dict]) -> list:
    steps = GaugeMetricFamily(
        "cicd_step_duration_seconds",
        "Step durations of the latest completed run (bottleneck analysis)",
        labels=["workflow", "stage", "step"],
    )
    for workflow, wf_runs in by_workflow(completed(runs)).items():
        latest = wf_runs[-1]
        for job in latest.get("jobs") or []:
            for step in job.get("steps") or []:
                duration = span(step.get("started_ts"), step.get("completed_ts"))
                if duration and step.get("conclusion") != "skipped":
                    steps.add_metric([workflow, job["name"], step["name"]], duration)
    return [steps]


# -- DORA metrics -------------------------------------------------------------
def deployments(runs: list[dict], deploy_job: str, main_branch: str) -> list[dict]:
    result = []
    for run in completed(runs):
        job = find_job(run, deploy_job)
        if run["branch"] == main_branch and job and job.get("conclusion") in FINISHED_CONCLUSIONS:
            result.append(
                {
                    "run": run,
                    "ts": job.get("completed_ts") or run_end(run),
                    "ok": job["conclusion"] == "success",
                }
            )
    return result


def dora_families(runs: list[dict], deploy_job: str, environment: str, main_branch: str, now: float) -> list:
    frequency = GaugeMetricFamily(
        "cicd_dora_deployment_frequency_per_day",
        "Successful deployments per day (last 7 days, normalised by observed days)",
        labels=["environment"],
    )
    lead_time = GaugeMetricFamily(
        "cicd_dora_lead_time_for_changes_seconds",
        "Median time from commit to successful deployment (last 30 days)",
        labels=["environment"],
    )
    last_deploy = GaugeMetricFamily(
        "cicd_dora_last_deployment_timestamp_seconds",
        "Time of the last successful deployment",
        labels=["environment"],
    )
    failure_rate = GaugeMetricFamily(
        "cicd_dora_change_failure_rate_ratio",
        "Share of main-branch pipeline runs that failed (last 7 days)",
        labels=["workflow"],
    )
    restore = GaugeMetricFamily(
        "cicd_dora_time_to_restore_seconds",
        "Mean time from a failed main-branch run to the next successful run (last 30 days)",
        labels=["workflow"],
    )
    incidents = GaugeMetricFamily(
        "cicd_dora_incidents", "Failure-to-recovery incidents on main (last 30 days)", labels=["workflow"]
    )
    open_incident = GaugeMetricFamily(
        "cicd_dora_open_incident", "1 while the latest main-branch run is failing", labels=["workflow"]
    )

    deploys = deployments(runs, deploy_job, main_branch)
    ok_deploys = [d for d in deploys if d["ok"]]
    recent = [d for d in ok_deploys if d["ts"] >= now - DORA_WINDOW]
    if ok_deploys:
        observed_days = min(7.0, max(1.0, (now - ok_deploys[0]["ts"]) / 86400))
        frequency.add_metric([environment], round(len(recent) / observed_days, 2))
        last_deploy.add_metric([environment], ok_deploys[-1]["ts"])
    leads = [
        d["ts"] - d["run"]["commit_ts"]
        for d in ok_deploys
        if d["ts"] >= now - RESTORE_WINDOW and d["run"].get("commit_ts") and d["ts"] >= d["run"]["commit_ts"]
    ]
    if leads:
        lead_time.add_metric([environment], round(statistics.median(leads), 1))

    deploy_workflows = {d["run"]["workflow"] for d in deploys}
    for workflow in deploy_workflows:
        main_runs = [
            r
            for r in completed(runs)
            if r["workflow"] == workflow
            and r["branch"] == main_branch
            and r.get("conclusion") in FINISHED_CONCLUSIONS
        ]
        window = [r for r in main_runs if (run_end(r) or 0) >= now - DORA_WINDOW]
        if window:
            failed = sum(1 for r in window if r["conclusion"] == "failure")
            failure_rate.add_metric([workflow], round(failed / len(window), 3))
        restore_times, failing_since = [], None
        for run in main_runs:
            end = run_end(run) or 0
            if run["conclusion"] == "failure" and failing_since is None:
                failing_since = end
            elif run["conclusion"] == "success" and failing_since is not None:
                if end >= now - RESTORE_WINDOW:
                    restore_times.append(end - failing_since)
                failing_since = None
        incidents.add_metric([workflow], len(restore_times))
        if restore_times:
            restore.add_metric([workflow], round(statistics.fmean(restore_times), 1))
        open_incident.add_metric([workflow], 1 if failing_since is not None else 0)
    return [frequency, lead_time, last_deploy, failure_rate, restore, incidents, open_incident]


# -- build quality (from the ci-metrics branch) --------------------------------
def quality_families(runs: list[dict], recent: int) -> list:
    info = GaugeMetricFamily(
        "cicd_build_info",
        "Run that the build-quality metrics refer to",
        labels=["workflow", "run_number", "commit"],
    )
    tests = GaugeMetricFamily(
        "cicd_build_tests", "Test results of the latest build", labels=["workflow", "result"]
    )
    test_time = GaugeMetricFamily(
        "cicd_build_test_duration_seconds", "Test suite duration", labels=["workflow"]
    )
    coverage = GaugeMetricFamily(
        "cicd_build_coverage_ratio", "Line coverage of the latest build", labels=["workflow"]
    )
    lint = GaugeMetricFamily(
        "cicd_build_lint_issues", "Lint findings in the latest build", labels=["workflow"]
    )
    vulns = GaugeMetricFamily(
        "cicd_build_vulnerabilities",
        "Security findings in the latest build",
        labels=["workflow", "scanner", "severity"],
    )
    image = GaugeMetricFamily("cicd_build_image_size_bytes", "Container image size", labels=["workflow"])
    gates = GaugeMetricFamily(
        "cicd_build_quality_gate", "Quality gate result (1 = passed, 0 = failed)", labels=["workflow", "gate"]
    )
    smoke = GaugeMetricFamily(
        "cicd_build_smoke_test_latency_ms",
        "Average API latency measured by the smoke test",
        labels=["workflow"],
    )
    run_cov = GaugeMetricFamily(
        "cicd_run_coverage_ratio", "Coverage per recent run", labels=["workflow", "run_number"]
    )
    run_tests = GaugeMetricFamily(
        "cicd_run_tests", "Test results per recent run", labels=["workflow", "run_number", "result"]
    )
    run_vulns = GaugeMetricFamily(
        "cicd_run_vulnerabilities",
        "Security findings per recent run",
        labels=["workflow", "run_number", "scanner"],
    )
    run_image = GaugeMetricFamily(
        "cicd_run_image_size_bytes", "Image size per recent run", labels=["workflow", "run_number"]
    )

    for workflow, wf_runs in by_workflow(completed(runs)).items():
        with_metrics = [r for r in wf_runs if r.get("build_metrics")]
        if not with_metrics:
            continue
        for run in with_metrics[-recent:]:
            number, bm = str(run.get("number")), run["build_metrics"]
            if bm.get("coverage"):
                run_cov.add_metric([workflow, number], bm["coverage"]["line_rate"])
            if bm.get("tests"):
                for result in ("passed", "failed", "errors", "skipped"):
                    run_tests.add_metric([workflow, number, result], bm["tests"].get(result, 0))
            for scanner, counts in _vulnerability_counts(bm).items():
                run_vulns.add_metric([workflow, number, scanner], sum(counts.values()))
            if (bm.get("artifact") or {}).get("size_bytes"):
                run_image.add_metric([workflow, number], bm["artifact"]["size_bytes"])

        def newest(extract, runs_with_metrics=with_metrics):
            """Value from the most recent run that produced it (skipped stages publish nothing)."""
            for run in reversed(runs_with_metrics):
                value = extract(run["build_metrics"])
                if value is not None:
                    return value
            return None

        latest = with_metrics[-1]
        info.add_metric([workflow, str(latest.get("number")), latest.get("sha", "")], 1)
        if (test_results := newest(lambda bm: bm.get("tests"))) is not None:
            for result in ("passed", "failed", "errors", "skipped"):
                tests.add_metric([workflow, result], test_results.get(result, 0))
            test_time.add_metric([workflow], test_results.get("duration_seconds", 0))
        if (cov := newest(lambda bm: bm.get("coverage"))) is not None:
            coverage.add_metric([workflow], cov["line_rate"])
        if (lint_result := newest(lambda bm: bm.get("lint"))) is not None:
            lint.add_metric([workflow], lint_result["issues"])
        for scanner in ("bandit", "pip-audit", "trivy"):
            counts = newest(lambda bm, name=scanner: _vulnerability_counts(bm).get(name))
            for severity, count in (counts or {}).items():
                vulns.add_metric([workflow, scanner, severity], count)
        if (size := newest(lambda bm: (bm.get("artifact") or {}).get("size_bytes"))) is not None:
            image.add_metric([workflow], size)
        latest_gates: dict[str, bool] = {}
        for run in with_metrics:  # oldest -> newest, so each gate keeps its latest result
            latest_gates.update(run["build_metrics"].get("quality_gates") or {})
        for gate, passed in latest_gates.items():
            gates.add_metric([workflow, gate], 1 if passed else 0)
        if (latency := newest(lambda bm: (bm.get("smoke_test") or {}).get("avg_latency_ms"))) is not None:
            smoke.add_metric([workflow], latency)
    return [
        info,
        tests,
        test_time,
        coverage,
        lint,
        vulns,
        image,
        gates,
        smoke,
        run_cov,
        run_tests,
        run_vulns,
        run_image,
    ]


def _vulnerability_counts(build_metrics: dict) -> dict[str, dict[str, int]]:
    security = build_metrics.get("security") or {}
    result = {}
    if security.get("bandit"):
        result["bandit"] = {sev: security["bandit"].get(sev, 0) for sev in ("high", "medium", "low")}
    if security.get("pip_audit"):
        result["pip-audit"] = {"any": security["pip_audit"].get("vulnerabilities", 0)}
    if security.get("trivy"):
        result["trivy"] = {
            sev: security["trivy"].get(sev, 0) for sev in ("critical", "high", "medium", "low")
        }
    return result


# -- collector -----------------------------------------------------------------
class CICDCollector:
    """Builds every metric family from a consistent snapshot of the store."""

    def __init__(self, store, client, config, status, clock=time.time) -> None:
        self.store = store
        self.client = client
        self.config = config
        self.status = status
        self.clock = clock

    def describe(self):
        return []

    def collect(self):
        now = self.clock()
        with self.store.lock:
            runs = self.store.snapshot()
            counters = json.loads(json.dumps(self.store.counters))
            histograms = json.loads(json.dumps(self.store.histograms))

        yield from self._exporter_families(len(runs))
        yield from counter_families(counters)
        yield from histogram_families(histograms)
        yield from activity_families(runs)
        yield from window_families(runs, now)
        yield from last_run_families(runs)
        yield from recent_run_families(runs, self.config.recent_runs)
        yield from step_families(runs)
        yield from dora_families(
            runs, self.config.deploy_job, self.config.deploy_environment, self.config.main_branch, now
        )
        yield from quality_families(runs, self.config.recent_runs)

    def _exporter_families(self, tracked: int):
        info = GaugeMetricFamily(
            "cicd_exporter_info", "Exporter build information", labels=["version", "repository"]
        )
        info.add_metric([__version__, self.config.repository], 1)
        yield info
        yield GaugeMetricFamily(
            "cicd_exporter_tracked_runs", "Workflow runs held in the local store", value=tracked
        )
        yield GaugeMetricFamily(
            "cicd_exporter_last_poll_timestamp_seconds",
            "Time of the last successful GitHub poll",
            value=self.status.get("last_success", 0),
        )
        yield GaugeMetricFamily(
            "cicd_exporter_last_poll_duration_seconds",
            "Duration of the last GitHub poll",
            value=self.status.get("last_duration", 0),
        )
        errors = CounterMetricFamily("cicd_exporter_poll_errors", "Failed GitHub polls")
        errors.add_metric([], self.status.get("errors", 0))
        yield errors
        requests_total = CounterMetricFamily(
            "cicd_github_api_requests", "GitHub API requests by HTTP status code", labels=["code"]
        )
        for code, count in sorted(self.client.requests.items()):
            requests_total.add_metric([code], count)
        yield requests_total
        limits = self.client.rate_limit
        if limits.get("remaining") is not None:
            yield GaugeMetricFamily(
                "cicd_github_api_rate_limit_remaining",
                "Remaining GitHub API requests",
                value=limits["remaining"],
            )
            yield GaugeMetricFamily(
                "cicd_github_api_rate_limit_limit", "GitHub API request quota per hour", value=limits["limit"]
            )
            yield GaugeMetricFamily(
                "cicd_github_api_rate_limit_reset_timestamp_seconds",
                "When the GitHub API quota resets",
                value=limits["reset"],
            )
