"""Exporter configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


@dataclass(frozen=True)
class Config:
    repository: str
    token: str | None = None
    port: int = 9200
    bind: str = "0.0.0.0"  # nosec B104 - containers need to listen on all interfaces
    data_dir: Path = Path(".data/exporter")
    poll_interval: int = 120  # seconds between polls when no run is active
    active_poll_interval: int = 30  # seconds between polls while runs are queued/in progress
    max_runs: int = 300  # runs kept in the local store
    recent_runs: int = 30  # runs per workflow exposed as per-run series
    metrics_branch: str = "ci-metrics"
    deploy_job: str = "Deploy to Staging"
    deploy_environment: str = "staging"
    main_branch: str = "main"
    # "dynamic" runs are GitHub-managed jobs (Dependabot update checks, etc.), not pipelines
    ignore_events: frozenset[str] = frozenset({"dynamic"})
    api_url: str = "https://api.github.com"
    raw_url: str = "https://raw.githubusercontent.com"

    @classmethod
    def from_env(cls) -> Config:
        repository = os.getenv("GITHUB_REPOSITORY", "").strip()
        if repository.count("/") != 1:
            raise SystemExit("GITHUB_REPOSITORY must be set to <owner>/<repo>")
        token = os.getenv("GITHUB_TOKEN", "").strip() or None
        return cls(
            repository=repository,
            token=token,
            port=_int("EXPORTER_PORT", 9200),
            bind=os.getenv("EXPORTER_BIND", cls.bind),
            data_dir=Path(os.getenv("EXPORTER_DATA_DIR", ".data/exporter")),
            # Unauthenticated clients get 60 API requests/hour, so poll less often without a token.
            poll_interval=_int("POLL_INTERVAL_SECONDS", 60 if token else 120),
            active_poll_interval=_int("ACTIVE_POLL_INTERVAL_SECONDS", 15 if token else 30),
            max_runs=_int("MAX_RUNS", 300),
            recent_runs=_int("RECENT_RUNS", 30),
            metrics_branch=os.getenv("METRICS_BRANCH", "ci-metrics"),
            deploy_job=os.getenv("DEPLOY_JOB_NAME", "Deploy to Staging"),
            deploy_environment=os.getenv("DEPLOY_ENVIRONMENT", "staging"),
            main_branch=os.getenv("MAIN_BRANCH", "main"),
            ignore_events=frozenset(
                event.strip() for event in os.getenv("IGNORE_EVENTS", "dynamic").split(",") if event.strip()
            ),
        )
