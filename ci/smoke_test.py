"""Post-deployment smoke test for the TaskFlow API.

    python ci/smoke_test.py --base-url http://localhost:8000   # test a running deployment
    python ci/smoke_test.py --serve                            # start the app locally, then test it

Results (pass/fail and latency per check) are written to reports/smoke-test.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def call(method: str, url: str, body: dict | None = None):
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"unsupported URL: {url}")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 - scheme validated above
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None), (time.perf_counter() - start) * 1000


def wait_until_healthy(base_url: str, timeout: float) -> float:
    deadline = time.monotonic() + timeout
    while True:
        try:
            status, body, _ = call("GET", f"{base_url}/health")
            if status == 200 and body.get("status") == "ok":
                return timeout - (deadline - time.monotonic())
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"{base_url} did not become healthy within {timeout:.0f}s")
        time.sleep(1)


def run_checks(base_url: str) -> list[dict]:
    results = []

    def check(name, method, path, expected, body=None, verify=None):
        start = time.perf_counter()
        try:
            status, payload, ms = call(method, base_url + path, body)
            ok = status == expected and (verify is None or verify(payload))
        except urllib.error.HTTPError as err:
            ms = (time.perf_counter() - start) * 1000
            status, payload, ok = err.code, None, err.code == expected
        results.append({"check": name, "status": status, "ok": ok, "latency_ms": round(ms, 1)})
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<22} HTTP {status}  {ms:6.1f} ms")
        return payload

    check("health", "GET", "/health", 200, verify=lambda p: p["status"] == "ok")
    task = check("create task", "POST", "/api/tasks", 201, {"title": "smoke test", "priority": "high"})
    task_id = task["id"] if task else 0
    check("read task", "GET", f"/api/tasks/{task_id}", 200, verify=lambda p: p["title"] == "smoke test")
    check("complete task", "PATCH", f"/api/tasks/{task_id}", 200, {"done": True}, lambda p: p["done"])
    check("stats", "GET", "/api/stats", 200, verify=lambda p: p["total"] >= 1)
    check("delete task", "DELETE", f"/api/tasks/{task_id}", 204)
    check("validation error", "POST", "/api/tasks", 400, {"title": ""})
    return results


def start_local_server():
    sys.path.insert(0, str(ROOT))
    from waitress.server import create_server

    from app.main import create_app

    server = create_server(create_app(), host="127.0.0.1", port=0)
    threading.Thread(target=server.run, daemon=True).start()
    return server, f"http://127.0.0.1:{server.effective_port}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a TaskFlow API deployment")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--serve", action="store_true", help="start the app in-process before testing")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "smoke-test.json")
    args = parser.parse_args()

    server = None
    base_url = args.base_url.rstrip("/")
    if args.serve:
        server, base_url = start_local_server()
    try:
        startup = wait_until_healthy(base_url, args.timeout)
        print(f"Service healthy at {base_url} after {startup:.1f}s")
        results = run_checks(base_url)
    finally:
        if server is not None:
            server.close()

    passed = all(r["ok"] for r in results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "base_url": base_url,
                "passed": passed,
                "startup_seconds": round(startup, 2),
                "avg_latency_ms": round(sum(r["latency_ms"] for r in results) / len(results), 1),
                "checks": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Smoke test", "PASSED" if passed else "FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
