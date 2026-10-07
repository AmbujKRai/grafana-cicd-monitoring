"""Background poller that syncs workflow runs from GitHub into the store.

The polling interval adapts to activity (faster while runs are queued or in
progress) and to the remaining API quota, so the exporter also works without
a token (60 requests/hour).
"""

from __future__ import annotations

import logging
import threading
import time

import requests

from cicd_exporter.github import RateLimitedError
from cicd_exporter.store import ACTIVE_STATUSES, find_job, run_duration, summarize_job, summarize_run

log = logging.getLogger(__name__)

METRICS_JOB = "Publish Build Metrics"
MAX_METRICS_CHECKS = 6
QUOTA_RESERVE = 5


class Poller:
    def __init__(self, client, store, config, clock=time.time) -> None:
        self.client = client
        self.store = store
        self.config = config
        self.clock = clock
        self.status = {
            "last_success": 0.0,
            "last_duration": 0.0,
            "errors": 0,
            "active_runs": 0,
            "last_error": "",
        }
        self._stop = threading.Event()

    def _job_budget(self) -> int:
        """How many job-list requests this poll may spend."""
        remaining = self.client.rate_limit.get("remaining")
        if remaining is None:
            return 10
        return max(0, remaining - QUOTA_RESERVE)

    def poll_once(self) -> int:
        started = time.monotonic()
        api_runs = self.client.list_runs()
        budget = self._job_budget()
        active = 0
        for api_run in api_runs:  # newest first, so fresh runs get the quota first
            if api_run.get("event") in self.config.ignore_events:
                continue
            run = self.store.upsert_run(summarize_run(api_run))
            if run["status"] in ACTIVE_STATUSES:
                active += 1
                if self.config.token and budget > 0:  # live stage progress only when authenticated
                    budget -= 1
                    jobs = [summarize_job(j) for j in self.client.list_jobs(run["id"])]
                    with self.store.lock:
                        run["jobs"] = jobs
                continue
            if run["status"] != "completed":
                continue
            if not run.get("jobs_final"):
                if budget <= 0:
                    continue
                budget -= 1
                jobs = [summarize_job(j) for j in self.client.list_jobs(run["id"])]
                with self.store.lock:
                    run["jobs"] = jobs
                    run["jobs_final"] = bool(jobs) and all(j["status"] == "completed" for j in jobs)
                if run["jobs_final"] and self.store.record_completion(
                    run, self.config.deploy_job, self.config.deploy_environment, self.config.main_branch
                ):
                    duration = run_duration(run)
                    log.info(
                        "%s #%s on %s finished: %s in %s s",
                        run["workflow"],
                        run["number"],
                        run["branch"],
                        run["conclusion"],
                        round(duration) if duration is not None else "?",
                    )
            self._fetch_build_metrics(run)
        self.store.prune()
        self.store.save()
        self.status.update(
            last_success=self.clock(), last_duration=round(time.monotonic() - started, 3), active_runs=active
        )
        return active

    def _fetch_build_metrics(self, run: dict) -> None:
        if run.get("build_metrics") or run.get("metrics_checks", 0) >= MAX_METRICS_CHECKS:
            return
        job = find_job(run, METRICS_JOB)
        if not job or job.get("conclusion") != "success":
            return
        try:
            data = self.client.fetch_build_metrics(run["id"])
        except (requests.RequestException, ValueError) as err:
            log.warning("Could not read build metrics for run %s: %s", run["id"], err)
            data = None
        with self.store.lock:
            run["metrics_checks"] = run.get("metrics_checks", 0) + 1
            if data:
                run["build_metrics"] = data

    def next_interval(self, active: int) -> float:
        interval = float(self.config.active_poll_interval if active else self.config.poll_interval)
        remaining, reset = self.client.rate_limit.get("remaining"), self.client.rate_limit.get("reset")
        if remaining is not None and reset:
            seconds_left = max(1.0, reset - self.clock())
            spare = remaining - QUOTA_RESERVE
            if spare <= 0:
                return seconds_left + 5  # quota exhausted: wait for the reset
            interval = max(interval, seconds_left / spare)
        return interval

    def run_forever(self) -> None:
        while not self._stop.is_set():
            active = 0
            try:
                active = self.poll_once()
                self.status["last_error"] = ""
            except RateLimitedError as err:
                self.status["errors"] += 1
                self.status["last_error"] = str(err)
                log.warning("%s", err)
            except Exception as err:  # keep polling whatever happens
                self.status["errors"] += 1
                self.status["last_error"] = str(err)
                log.exception("Poll failed")
            wait = self.next_interval(active)
            log.debug("Next poll in %.0f s", wait)
            self._stop.wait(wait)

    def stop(self) -> None:
        self._stop.set()
