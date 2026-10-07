import pytest

from app.store import MAX_TITLE_LENGTH, TaskStore, ValidationError


@pytest.fixture
def store():
    return TaskStore()


def test_create_assigns_incrementing_ids(store):
    first = store.create("Write Jenkinsfile")
    second = store.create("Configure Grafana", "high")
    assert (first.id, second.id) == (1, 2)
    assert second.priority == "high"
    assert first.done is False


def test_create_strips_title(store):
    assert store.create("  Add alert rule  ").title == "Add alert rule"


@pytest.mark.parametrize("title", ["", "   ", None, 42])
def test_create_rejects_invalid_title(store, title):
    with pytest.raises(ValidationError):
        store.create(title)


def test_create_rejects_long_title(store):
    with pytest.raises(ValidationError):
        store.create("x" * (MAX_TITLE_LENGTH + 1))


def test_create_rejects_unknown_priority(store):
    with pytest.raises(ValidationError):
        store.create("Tune scrape interval", "urgent")


def test_list_filters_by_done_and_priority(store):
    store.create("a", "low")
    b = store.create("b", "high")
    store.create("c", "high")
    store.update(b.id, done=True)
    assert [t.title for t in store.list(done=True)] == ["b"]
    assert [t.title for t in store.list(priority="high")] == ["b", "c"]
    assert [t.title for t in store.list(done=False, priority="high")] == ["c"]


def test_update_marks_task_done_and_back(store):
    task = store.create("Review dashboard")
    store.update(task.id, done=True)
    assert task.done and task.completed_at is not None
    store.update(task.id, done=False)
    assert not task.done and task.completed_at is None


def test_update_changes_title_and_priority(store):
    task = store.create("Old title")
    store.update(task.id, title="New title", priority="low")
    assert (task.title, task.priority) == ("New title", "low")


def test_update_rejects_non_boolean_done(store):
    task = store.create("Check flaky test")
    with pytest.raises(ValidationError):
        store.update(task.id, done="yes")


def test_update_missing_task_returns_none(store):
    assert store.update(99, title="nothing") is None


def test_delete(store):
    task = store.create("Temporary")
    assert store.delete(task.id) is True
    assert store.delete(task.id) is False
    assert store.get(task.id) is None


def test_stats(store):
    assert store.stats()["completion_rate"] == 0.0
    a = store.create("a", "high")
    store.create("b", "low")
    store.update(a.id, done=True)
    stats = store.stats()
    assert stats["total"] == 2
    assert stats["done"] == 1
    assert stats["open"] == 1
    assert stats["completion_rate"] == 0.5
    assert stats["by_priority"] == {"low": 1, "medium": 0, "high": 1}
