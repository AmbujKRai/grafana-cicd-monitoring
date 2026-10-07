import pytest

from app import __version__
from app.main import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json["status"] == "ok"
    assert resp.json["version"] == __version__


def test_create_and_get_task(client):
    created = client.post("/api/tasks", json={"title": "Provision Grafana", "priority": "high"})
    assert created.status_code == 201
    task_id = created.json["id"]
    fetched = client.get(f"/api/tasks/{task_id}")
    assert fetched.status_code == 200
    assert fetched.json["title"] == "Provision Grafana"
    assert fetched.json["priority"] == "high"


def test_create_uses_default_priority(client):
    created = client.post("/api/tasks", json={"title": "Write README"})
    assert created.json["priority"] == "medium"


def test_create_without_body_is_rejected(client):
    resp = client.post("/api/tasks", data="not json", content_type="text/plain")
    assert resp.status_code == 400
    assert "title" in resp.json["error"]


def test_create_with_bad_priority_is_rejected(client):
    resp = client.post("/api/tasks", json={"title": "x", "priority": "critical"})
    assert resp.status_code == 400


def test_list_tasks_with_filters(client):
    client.post("/api/tasks", json={"title": "one", "priority": "low"})
    second = client.post("/api/tasks", json={"title": "two", "priority": "high"}).json
    client.patch(f"/api/tasks/{second['id']}", json={"done": True})
    assert len(client.get("/api/tasks").json) == 2
    assert [t["title"] for t in client.get("/api/tasks?done=true").json] == ["two"]
    assert [t["title"] for t in client.get("/api/tasks?done=false").json] == ["one"]
    assert [t["title"] for t in client.get("/api/tasks?priority=low").json] == ["one"]


def test_patch_ignores_unknown_fields(client):
    task = client.post("/api/tasks", json={"title": "Pin action SHAs"}).json
    resp = client.patch(f"/api/tasks/{task['id']}", json={"id": 500, "title": "Pin all action SHAs"})
    assert resp.status_code == 200
    assert resp.json["id"] == task["id"]
    assert resp.json["title"] == "Pin all action SHAs"


def test_patch_missing_task(client):
    assert client.patch("/api/tasks/404", json={"done": True}).status_code == 404


def test_get_missing_task(client):
    assert client.get("/api/tasks/12345").status_code == 404


def test_delete_task(client):
    task = client.post("/api/tasks", json={"title": "Delete me"}).json
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 404


def test_unknown_route_returns_json_404(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    assert resp.json == {"error": "not found"}


def test_stats_endpoint(client):
    task = client.post("/api/tasks", json={"title": "a"}).json
    client.post("/api/tasks", json={"title": "b"})
    client.patch(f"/api/tasks/{task['id']}", json={"done": True})
    stats = client.get("/api/stats").json
    assert stats["total"] == 2
    assert stats["completion_rate"] == 0.5
