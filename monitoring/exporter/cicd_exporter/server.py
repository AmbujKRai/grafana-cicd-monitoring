"""HTTP endpoints: /metrics (Prometheus), /alerts (Grafana webhook receiver) and /healthz."""

from __future__ import annotations

import json
import logging
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from cicd_exporter import __version__

alert_log = logging.getLogger("cicd_exporter.alerts")

MAX_BODY = 1024 * 1024

INDEX = """<html><head><title>CI/CD metrics exporter</title></head><body style="font-family:sans-serif">
<h2>CI/CD metrics exporter {version}</h2><p>Repository: <b>{repo}</b></p>
<ul><li><a href="/metrics">/metrics</a> - Prometheus metrics</li>
<li><a href="/healthz">/healthz</a> - health and poll status</li>
<li><a href="/alerts">/alerts</a> - alert notifications received from Grafana (POST target)</li></ul>
</body></html>"""


def parse_grafana_webhook(payload: dict) -> list[dict]:
    """Flatten a Grafana alerting webhook payload into one record per alert."""
    records = []
    for alert in payload.get("alerts") or []:
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        records.append(
            {
                "received_at": round(time.time(), 3),
                "status": alert.get("status") or payload.get("status") or "unknown",
                "alertname": labels.get("alertname", "unknown"),
                "severity": labels.get("severity", "none"),
                "summary": annotations.get("summary", ""),
                "description": annotations.get("description", ""),
                "starts_at": alert.get("startsAt"),
                "labels": labels,
                "values": alert.get("values"),
            }
        )
    return records


def make_handler(registry, store, poller, config, alerts_file: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"cicd-exporter/{__version__}"

        def log_message(self, format, *args):  # noqa: A002 - silence per-request access logs
            return

        def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - http.server naming
            path = self.path.split("?")[0]
            if path == "/metrics":
                self._send(200, generate_latest(registry), CONTENT_TYPE_LATEST)
            elif path == "/healthz":
                body = {
                    "status": "ok",
                    "version": __version__,
                    "repository": config.repository,
                    "authenticated": bool(config.token),
                    "tracked_runs": len(store.runs),
                    "poller": poller.status,
                    "rate_limit": poller.client.rate_limit,
                }
                self._send(200, json.dumps(body).encode())
            elif path == "/alerts":
                with store.lock:
                    recent = list(reversed(store.alerts[-50:]))
                self._send(200, json.dumps(recent, indent=2).encode())
            elif path == "/":
                html = INDEX.format(version=__version__, repo=config.repository)
                self._send(200, html.encode(), "text/html; charset=utf-8")
            else:
                self._send(404, b'{"error": "not found"}')

        def do_POST(self):  # noqa: N802
            if self.path.split("?")[0] != "/alerts":
                self._send(404, b'{"error": "not found"}')
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._send(413, b'{"error": "payload too large"}')
                return
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send(400, b'{"error": "invalid JSON"}')
                return
            records = parse_grafana_webhook(payload)
            alerts_file.parent.mkdir(parents=True, exist_ok=True)
            with alerts_file.open("a", encoding="utf-8") as fh:
                for record in records:
                    store.record_alert(record)
                    fh.write(json.dumps(record) + "\n")
                    alert_log.warning(
                        "ALERT %-8s %s [%s] %s",
                        record["status"].upper(),
                        record["alertname"],
                        record["severity"],
                        record["summary"],
                    )
            store.save()
            self._send(200, json.dumps({"received": len(records)}).encode())

    return Handler
