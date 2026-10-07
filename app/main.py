"""Flask application factory and HTTP routes for the TaskFlow API."""

from __future__ import annotations

import os

from flask import Flask, jsonify, request

from app import __version__
from app.store import TaskStore, ValidationError


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() in ("1", "true", "yes")


def create_app(store: TaskStore | None = None) -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    tasks = store or TaskStore()

    @app.errorhandler(ValidationError)
    def handle_validation_error(err):
        return jsonify(error=str(err)), 400

    @app.errorhandler(404)
    def handle_not_found(_err):
        return jsonify(error="not found"), 404

    @app.get("/health")
    def health():
        return jsonify(
            status="ok",
            version=__version__,
            environment=os.getenv("APP_ENV", "development"),
        )

    @app.get("/api/tasks")
    def list_tasks():
        done = _parse_bool(request.args.get("done"))
        priority = request.args.get("priority")
        return jsonify([t.to_dict() for t in tasks.list(done=done, priority=priority)])

    @app.post("/api/tasks")
    def create_task():
        payload = request.get_json(silent=True) or {}
        task = tasks.create(payload.get("title"), payload.get("priority", "medium"))
        return jsonify(task.to_dict()), 201

    @app.get("/api/tasks/<int:task_id>")
    def get_task(task_id: int):
        task = tasks.get(task_id)
        if task is None:
            return jsonify(error="task not found"), 404
        return jsonify(task.to_dict())

    @app.patch("/api/tasks/<int:task_id>")
    def update_task(task_id: int):
        payload = request.get_json(silent=True) or {}
        allowed = {k: v for k, v in payload.items() if k in ("title", "priority", "done")}
        task = tasks.update(task_id, **allowed)
        if task is None:
            return jsonify(error="task not found"), 404
        return jsonify(task.to_dict())

    @app.delete("/api/tasks/<int:task_id>")
    def delete_task(task_id: int):
        if not tasks.delete(task_id):
            return jsonify(error="task not found"), 404
        return "", 204

    @app.get("/api/stats")
    def stats():
        return jsonify(tasks.stats())

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
