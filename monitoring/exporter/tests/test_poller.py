import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from cicd_exporter.metrics import CICDCollector
from cicd_exporter.poller import Poller
from cicd_exporter.server import make_handler, parse_grafana_webhook
from cicd_exporter.store import Store
from conftest import api_jobs, api_run
from prometheus_client import CollectorRegistry


class FakeGitHub:
    def __init__(self, runs, jobs, remaining=50):
        self.runs, self.jobs = runs, jobs
        self.rate_limit = {"limit": 60, "remaining": remaining, "reset": 10_000_000_000}
        self.requests = {}
        self.job_calls, self.metric_calls = [], []

    def list_runs(self):
        return self.runs

    def list_jobs(self, run_id):
        self.job_calls.append(run_id)
        return self.jobs[run_id]

    def fetch_build_metrics(self, run_id):
        self.metric_calls.append(run_id)
        return {"tests": {"total": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0}}


def test_poll_fetches_jobs_once_and_reads_build_metrics(config):
    runs = [api_run(2, 2, 30), api_run(1, 1, 0, "failure"), api_run(3, 3, 60, status="in_progress")]
    jobs = {1: api_jobs(0, failed_job="Lint"), 2: api_jobs(30)}
    client = FakeGitHub(runs, jobs)
    store = Store(config.data_dir / "state.json")
    poller = Poller(client, store, config)

    assert poller.poll_once() == 1  # one active run
    assert sorted(client.job_calls) == [1, 2]
    assert client.metric_calls == [2, 1]  # both runs ran the publish job
    assert store.runs["2"]["build_metrics"]["tests"]["passed"] == 1

    poller.poll_once()
    assert sorted(client.job_calls) == [1, 2]  # completed runs are never fetched twice
    assert (config.data_dir / "state.json").exists()


def test_poll_skips_ignored_events(config):
    dependabot = api_run(9, 9, 0)
    dependabot.update(event="dynamic", name="Dependabot Updates")
    client = FakeGitHub([dependabot], {9: api_jobs(0)})
    store = Store()
    Poller(client, store, config).poll_once()
    assert store.runs == {} and client.job_calls == []


def test_poll_respects_api_budget(config):
    runs = [api_run(i, i, i * 10) for i in range(1, 11)]
    jobs = {i: api_jobs(i * 10) for i in range(1, 11)}
    client = FakeGitHub(runs, jobs, remaining=8)  # 8 - reserve(5) = 3 job requests allowed
    poller = Poller(client, Store(), config)
    poller.poll_once()
    assert client.job_calls == [1, 2, 3]


def test_interval_backs_off_when_quota_is_low(config):
    client = FakeGitHub([], {}, remaining=10)
    client.rate_limit["reset"] = 1000 + 3000
    poller = Poller(client, Store(), config, clock=lambda: 1000)
    assert poller.next_interval(active=1) == 600  # 3000 s left / 5 spare requests
    client.rate_limit["remaining"] = 5
    assert poller.next_interval(active=0) == 3005  # exhausted: wait for reset


def test_webhook_parsing():
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "PipelineFailed", "severity": "critical"},
                "annotations": {"summary": "CI/CD Pipeline failed on main"},
                "startsAt": "2026-10-01T09:00:00Z",
            }
        ],
    }
    records = parse_grafana_webhook(payload)
    assert records[0]["alertname"] == "PipelineFailed"
    assert records[0]["severity"] == "critical"
    assert records[0]["summary"] == "CI/CD Pipeline failed on main"


def test_http_endpoints(config):
    store = Store(config.data_dir / "state.json")
    client = FakeGitHub([], {})
    poller = Poller(client, store, config)
    registry = CollectorRegistry()
    registry.register(CICDCollector(store, client, config, poller.status))
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(registry, store, poller, config, config.data_dir / "a.log")
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        metrics = urllib.request.urlopen(f"{base}/metrics").read().decode()
        assert "cicd_exporter_info" in metrics
        health = json.loads(urllib.request.urlopen(f"{base}/healthz").read())
        assert health["status"] == "ok" and health["repository"] == "owner/repo"
        body = json.dumps({"alerts": [{"status": "firing", "labels": {"alertname": "SlowBuild"}}]}).encode()
        req = urllib.request.Request(f"{base}/alerts", data=body, method="POST")
        assert json.loads(urllib.request.urlopen(req).read()) == {"received": 1}
        assert "cicd_alert_notifications_total" in urllib.request.urlopen(f"{base}/metrics").read().decode()
    finally:
        server.shutdown()
