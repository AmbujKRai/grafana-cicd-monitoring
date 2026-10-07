from datetime import UTC, datetime, timedelta

import pytest
from cicd_exporter.config import Config

BASE = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)
JOBS = [("Lint", 20), ("Unit Tests & Coverage", 45), ("Build & Scan Image", 90), ("Deploy to Staging", 30)]


def iso(minutes: float) -> str:
    return (BASE + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts(minutes: float) -> float:
    return (BASE + timedelta(minutes=minutes)).timestamp()


def api_run(
    run_id,
    number,
    start_min,
    conclusion="success",
    branch="main",
    status="completed",
    workflow="CI/CD Pipeline",
):
    return {
        "id": run_id,
        "run_number": number,
        "run_attempt": 1,
        "name": workflow,
        "event": "push",
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "head_branch": branch,
        "head_sha": f"{run_id:07d}abcdef",
        "display_title": f"Commit for run {number}",
        "actor": {"login": "student"},
        "head_commit": {"timestamp": iso(start_min - 2), "message": f"Commit for run {number}"},
        "created_at": iso(start_min),
        "run_started_at": iso(start_min),
        "updated_at": iso(start_min + 4),
        "html_url": f"https://github.com/owner/repo/actions/runs/{run_id}",
    }


def api_jobs(start_min, failed_job=None, queue_seconds=5):
    """Sequential jobs; everything after ``failed_job`` is skipped."""
    jobs, clock, failed = [], start_min, False
    for index, (name, seconds) in enumerate([*JOBS, ("Publish Build Metrics", 10)]):
        created = clock
        if failed and name != "Publish Build Metrics":
            jobs.append(
                {
                    "id": index,
                    "name": name,
                    "status": "completed",
                    "conclusion": "skipped",
                    "created_at": iso(created),
                    "started_at": iso(created),
                    "completed_at": iso(created),
                    "steps": [],
                }
            )
            continue
        started = created + queue_seconds / 60
        completed = started + seconds / 60
        conclusion = "failure" if name == failed_job else "success"
        failed = failed or conclusion == "failure"
        jobs.append(
            {
                "id": index,
                "name": name,
                "status": "completed",
                "conclusion": conclusion,
                "created_at": iso(created),
                "started_at": iso(started),
                "completed_at": iso(completed),
                "steps": [
                    {
                        "name": "Set up job",
                        "conclusion": "success",
                        "started_at": iso(started),
                        "completed_at": iso(started + 0.05),
                    },
                    {
                        "name": f"Run {name}",
                        "conclusion": conclusion,
                        "started_at": iso(started + 0.05),
                        "completed_at": iso(completed),
                    },
                ],
            }
        )
        clock = completed
    return jobs


@pytest.fixture
def config(tmp_path):
    return Config(repository="owner/repo", data_dir=tmp_path, recent_runs=10)
