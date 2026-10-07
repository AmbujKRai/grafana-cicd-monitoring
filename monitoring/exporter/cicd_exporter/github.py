"""Minimal GitHub REST API client with ETag caching and rate-limit tracking."""

from __future__ import annotations

import logging
from collections import Counter

import requests

from cicd_exporter import __version__
from cicd_exporter.config import Config

log = logging.getLogger(__name__)


class RateLimitedError(RuntimeError):
    """Raised when GitHub refuses a request because the rate limit is exhausted."""


class GitHubClient:
    def __init__(self, config: Config, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": f"cicd-exporter/{__version__}",
            }
        )
        if config.token:
            self.session.headers["Authorization"] = f"Bearer {config.token}"
        self._etags: dict[str, tuple[str, object]] = {}
        self.rate_limit: dict[str, int | None] = {"limit": None, "remaining": None, "reset": None}
        self.requests: Counter[str] = Counter()

    def _update_rate_limit(self, resp: requests.Response) -> None:
        headers = resp.headers
        if "X-RateLimit-Remaining" in headers:
            self.rate_limit = {
                "limit": int(headers.get("X-RateLimit-Limit", 0)),
                "remaining": int(headers["X-RateLimit-Remaining"]),
                "reset": int(headers.get("X-RateLimit-Reset", 0)),
            }

    def get_json(self, path: str, params: dict | None = None):
        """GET an API path. Unchanged resources are served from the ETag cache (HTTP 304)."""
        url = f"{self.config.api_url}{path}"
        key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        cached = self._etags.get(key)
        headers = {"If-None-Match": cached[0]} if cached else {}
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=20)
        except requests.RequestException:
            self.requests["error"] += 1
            raise
        self.requests[str(resp.status_code)] += 1
        self._update_rate_limit(resp)
        if resp.status_code == 304 and cached:
            return cached[1]
        if resp.status_code in (403, 429) and self.rate_limit.get("remaining") == 0:
            raise RateLimitedError(f"GitHub API rate limit exhausted (resets at {self.rate_limit['reset']})")
        resp.raise_for_status()
        data = resp.json()
        etag = resp.headers.get("ETag")
        if etag:
            self._etags[key] = (etag, data)
        return data

    def list_runs(self, per_page: int = 100) -> list[dict]:
        data = self.get_json(
            f"/repos/{self.config.repository}/actions/runs",
            {"per_page": per_page, "exclude_pull_requests": "true"},
        )
        return data.get("workflow_runs", [])

    def list_jobs(self, run_id: int) -> list[dict]:
        data = self.get_json(f"/repos/{self.config.repository}/actions/runs/{run_id}/jobs", {"per_page": 100})
        return data.get("jobs", [])

    def fetch_build_metrics(self, run_id: int) -> dict | None:
        """Read runs/<run_id>.json from the metrics branch (raw.githubusercontent.com, no API quota)."""
        url = (
            f"{self.config.raw_url}/{self.config.repository}/{self.config.metrics_branch}/runs/{run_id}.json"
        )
        resp = requests.get(url, timeout=20, headers={"User-Agent": f"cicd-exporter/{__version__}"})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
