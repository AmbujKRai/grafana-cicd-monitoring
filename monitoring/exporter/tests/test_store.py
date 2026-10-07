from cicd_exporter.store import (
    Store,
    job_duration,
    run_duration,
    run_end,
    run_wait_time,
    summarize_job,
    summarize_run,
)
from conftest import api_jobs, api_run, ts


def completed_run(run_id=1, number=1, start=0, failed_job=None):
    run = summarize_run(api_run(run_id, number, start, "failure" if failed_job else "success"))
    run["jobs"] = [summarize_job(j) for j in api_jobs(start, failed_job)]
    run["jobs_final"] = True
    return run


def test_summarize_run_extracts_fields():
    run = summarize_run(api_run(42, 7, 0))
    assert run["workflow"] == "CI/CD Pipeline"
    assert run["number"] == 7
    assert run["sha"] == "0000042"
    assert run["title"] == "Commit for run 7"
    assert run["started_ts"] == ts(0)


def test_run_duration_uses_last_job_completion():
    run = completed_run()
    # 5 jobs, each waiting 5 s; work: 20 + 45 + 90 + 30 + 10 seconds
    assert run_end(run) == run["jobs"][-1]["completed_ts"]
    assert round(run_duration(run)) == 5 * 5 + 195
    assert round(run_wait_time(run)) == 5


def test_skipped_jobs_have_no_duration():
    run = completed_run(failed_job="Unit Tests & Coverage")
    durations = {j["name"]: job_duration(j) for j in run["jobs"]}
    assert durations["Build & Scan Image"] is None
    assert durations["Unit Tests & Coverage"] is not None


def test_record_completion_counts_each_attempt_once(tmp_path):
    store = Store(tmp_path / "state.json")
    run = completed_run()
    assert store.record_completion(run, "Deploy to Staging", "staging", "main") is True
    assert store.record_completion(run, "Deploy to Staging", "staging", "main") is False
    runs_total = store.counters["cicd_workflow_runs_total"]
    assert sum(runs_total.values()) == 1
    deployments = store.counters["cicd_deployments_total"]
    assert list(deployments.values()) == [1.0]
    hist = next(iter(store.histograms["cicd_workflow_run_duration_seconds"].values()))
    assert hist["count"] == 1 and hist["counts"][RUN_BUCKET_FOR_220S] == 1


RUN_BUCKET_FOR_220S = 5  # (30, 60, 90, 120, 180, 240, ...) -> 220 s falls in the 240 s bucket


def test_failed_run_does_not_count_as_deployment(tmp_path):
    store = Store(tmp_path / "state.json")
    store.record_completion(
        completed_run(failed_job="Build & Scan Image"), "Deploy to Staging", "staging", "main"
    )
    assert "cicd_deployments_total" not in store.counters
    jobs = store.counters["cicd_job_runs_total"]
    conclusions = sorted(key for key in jobs)
    assert any('"failure"' in key for key in conclusions)


def test_store_round_trip(tmp_path):
    path = tmp_path / "state.json"
    store = Store(path)
    run = completed_run()
    store.upsert_run(run)
    store.record_completion(run, "Deploy to Staging", "staging", "main")
    store.save()

    restored = Store(path)
    restored.load()
    assert restored.runs.keys() == store.runs.keys()
    assert restored.counted == store.counted
    assert restored.counters == store.counters


def test_upsert_keeps_jobs_for_same_attempt():
    store = Store()
    run = completed_run()
    store.upsert_run(run)
    refreshed = store.upsert_run(summarize_run(api_run(1, 1, 0)))
    assert refreshed["jobs_final"] is True and len(refreshed["jobs"]) == 5


def test_prune_keeps_newest_runs():
    store = Store(max_runs=2)
    for i in range(4):
        run = completed_run(run_id=i + 1, number=i + 1, start=i * 10)
        store.upsert_run(run)
        store.record_completion(run, "Deploy to Staging", "staging", "main")
    store.prune()
    assert sorted(store.runs) == ["3", "4"]
    assert store.counted == {"3:1", "4:1"}
