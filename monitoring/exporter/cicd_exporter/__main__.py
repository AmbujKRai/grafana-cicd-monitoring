"""Entry point: python -m cicd_exporter"""

from __future__ import annotations

import logging
import os
import threading
from http.server import ThreadingHTTPServer

from prometheus_client import CollectorRegistry

from cicd_exporter import __version__
from cicd_exporter.config import Config
from cicd_exporter.github import GitHubClient
from cicd_exporter.metrics import CICDCollector
from cicd_exporter.poller import Poller
from cicd_exporter.server import make_handler
from cicd_exporter.store import Store

log = logging.getLogger("cicd_exporter")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    config = Config.from_env()
    store = Store(config.data_dir / "state.json", config.max_runs)
    store.load()
    client = GitHubClient(config)
    poller = Poller(client, store, config)

    registry = CollectorRegistry()
    registry.register(CICDCollector(store, client, config, poller.status))
    handler = make_handler(registry, store, poller, config, config.data_dir / "alerts.log")
    server = ThreadingHTTPServer((config.bind, config.port), handler)

    threading.Thread(target=poller.run_forever, name="poller", daemon=True).start()
    log.info(
        "cicd-exporter %s watching %s (%s) - metrics on http://%s:%d/metrics",
        __version__,
        config.repository,
        "token" if config.token else "unauthenticated, 60 req/h",
        config.bind,
        config.port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        poller.stop()
        server.server_close()
        store.save()


if __name__ == "__main__":
    main()
