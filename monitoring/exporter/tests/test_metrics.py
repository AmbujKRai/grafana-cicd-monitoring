import pytest
from cicd_exporter.metrics import CICDCollector, dora_families, percentile, quality_families
from cicd_exporter.store import Store, summarize_job, summarize_run
from conftest import api_jobs, api_run, ts
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families

BUILD_METRICS = {
    "tests": {"total": 30, "passed": 29, "failed": 1, "errors": 0, "skipped": 0, "duration_seconds": 1.2},
    "coverage": {"line_rate": 0.93, "branch_rate": 0.88},
    "lint": {"issues": 0},
    "security": {
        "bandit": {"high": 0, "medium": 1, "low": 2},
        "pip_audit": {"vulnerabilities": 0, "packages": []},
        "trivy": {"critical": 0, "high": 2, "medium": 5, "low": 20, "unknown": 0, "critical_fixable": 0},
    },
    "artifact": {"image": "ghcr.io/owner/repo:sha-abc", "size_bytes": 150_000_000},
    "smoke_test": {"avg_latency_ms": 3.5},
    "quality_gates": {"tests": False, "coverage": True, "sast": True, "sca": True, "container": True},
}


class FakeClient:
    rate_limit = {"limit": 60, "remaining": 42, "reset": 0}
    requests = {"200": 3, "304": 7}


def make_run(run_id, number, start, conclusion="success", failed_job=None, branch="main", metrics=None):
    run = summarize_run(api_run(run_id, number, start, conclusion, branch=branch))
    run["jobs"] = [summarize_job(j) for j in api_jobs(start, failed_job)]
    run["jobs_final"] = True
    run["build_metrics"] = metrics
    return run


def history():
    """success -> failure (tests) -> failure (build) -> success -> success, 30 minutes apart."""
    return [
        make_run(1, 1, 0),
        make_run(2, 2, 30, "failure", failed_job="Unit Tests & Coverage"),
        make_run(3, 3, 60, "failure", failed_job="Build & Scan Image"),
        make_run(4, 4, 90),
        make_run(5, 5, 120, metrics=BUILD_METRICS),
    ]


def samples(families, name):
    return [s for f in families for s in f.samples if s.name == name]


def test_percentile_interpolates():
    assert percentile([10.0], 0.95) == 10.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == pytest.approx(4.8)


def test_dora_metrics():
    runs = history()
    now = ts(200)
    families = dora_families(runs, "Deploy to Staging", "staging", "main", now)
    cfr = samples(families, "cicd_dora_change_failure_rate_ratio")[0].value
    assert cfr == pytest.approx(0.4)
    incidents = samples(families, "cicd_dora_incidents")[0].value
    assert incidents == 1
    restore = samples(families, "cicd_dora_time_to_restore_seconds")[0].value
    # failure detected when run 2 ended (30 min + 90 s), fixed when run 4 ended (90 min + 220 s)
    assert restore == pytest.approx(3730, abs=1)
    assert samples(families, "cicd_dora_open_incident")[0].value == 0
    lead = samples(families, "cicd_dora_lead_time_for_changes_seconds")[0].value
    assert 120 < lead < 600  # commit 2 min before the run + time until the deploy job finished
    frequency = samples(families, "cicd_dora_deployment_frequency_per_day")[0].value
    assert frequency == 3.0  # 3 successful deploys, observed for less than a day -> normalised to 1 day


def test_open_incident_when_latest_main_run_failed():
    runs = history()[:3]
    families = dora_families(runs, "Deploy to Staging", "staging", "main", ts(100))
    assert samples(families, "cicd_dora_open_incident")[0].value == 1


def test_quality_metrics_come_from_latest_run_with_metrics():
    families = quality_families(history(), recent=10)
    tests = {s.labels["result"]: s.value for s in samples(families, "cicd_build_tests")}
    assert tests == {"passed": 29, "failed": 1, "errors": 0, "skipped": 0}
    assert samples(families, "cicd_build_coverage_ratio")[0].value == 0.93
    trivy_high = [
        s.value
        for s in samples(families, "cicd_build_vulnerabilities")
        if s.labels["scanner"] == "trivy" and s.labels["severity"] == "high"
    ]
    assert trivy_high == [2]
    gates = {s.labels["gate"]: s.value for s in samples(families, "cicd_build_quality_gate")}
    assert gates["tests"] == 0 and gates["coverage"] == 1
    assert samples(families, "cicd_build_info")[0].labels["run_number"] == "5"


def test_collector_exposition(config):
    store = Store()
    for run in history():
        store.upsert_run(run)
        store.record_completion(run, config.deploy_job, config.deploy_environment, config.main_branch)
    in_progress = summarize_run(api_run(6, 6, 150, status="in_progress"))
    store.upsert_run(in_progress)

    registry = CollectorRegistry()
    status = {"last_success": ts(150), "last_duration": 0.4, "errors": 0}
    registry.register(CICDCollector(store, FakeClient(), config, status, clock=lambda: ts(200)))
    text = generate_latest(registry).decode()
    parsed = {s.name: s for f in text_string_to_metric_families(text) for s in f.samples}

    for name in (
        "cicd_workflow_runs_total",
        "cicd_workflow_run_duration_seconds_bucket",
        "cicd_stage_duration_seconds_count",
        "cicd_deployments_total",
        "cicd_workflow_runs_window",
        "cicd_workflow_duration_window_seconds",
        "cicd_workflow_last_run_status",
        "cicd_run_duration_seconds",
        "cicd_run_stage_duration_seconds",
        "cicd_step_duration_seconds",
        "cicd_github_api_rate_limit_remaining",
        "cicd_exporter_info",
    ):
        assert name in parsed, name

    families = {f.name: f for f in text_string_to_metric_families(text)}
    runs_total = sum(s.value for s in families["cicd_workflow_runs"].samples if s.name.endswith("_total"))
    assert runs_total == 5
    active = {s.labels["status"]: s.value for s in families["cicd_workflow_runs_active"].samples}
    assert active == {"queued": 0, "in_progress": 1}
    last_status = families["cicd_workflow_last_run_status"].samples[0]
    assert last_status.value == 1 and last_status.labels["branch"] == "main"
    success_24h = [
        s.value
        for s in families["cicd_workflow_runs_window"].samples
        if s.labels["window"] == "24h" and s.labels["conclusion"] == "success"
    ]
    assert success_24h == [3]
