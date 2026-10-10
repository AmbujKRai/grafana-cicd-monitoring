"""In-memory task store with simple validation rules."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

VALID_PRIORITIES = ("low", "medium", "high")
MAX_TITLE_LENGTH = 120


class ValidationError(ValueError):
    """Raised when a task payload is invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Task:
    id: int
    title: str
    priority: str = "medium"
    done: bool = False
    created_at: str = field(default_factory=_now)
    completed_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def validate_title(title) -> str:
    if not isinstance(title, str) or not title.strip():
        raise ValidationError("title must be a non-empty string")
    title = title.strip()
    if len(title) > MAX_TITLE_LENGTH:
        raise ValidationError(f"title must be at most {MAX_TITLE_LENGTH} characters")
    return title


def validate_priority(priority) -> str:
    if priority not in VALID_PRIORITIES:
        raise ValidationError(f"priority must be one of {', '.join(VALID_PRIORITIES)}")
    return priority


class TaskStore:
    """Thread-safe in-memory storage for tasks."""

    def __init__(self) -> None:
        self._tasks: dict[int, Task] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def create(self, title, priority="medium") -> Task:
        title = validate_title(title)
        priority = validate_priority(priority)
        with self._lock:
            task = Task(id=self._next_id, title=title, priority=priority)
            self._tasks[task.id] = task
            self._next_id += 1
        return task

    def get(self, task_id: int) -> Task | None:
        return self._tasks.get(task_id)

    def list(self, done: bool | None = None, priority: str | None = None) -> list[Task]:
        tasks = sorted(self._tasks.values(), key=lambda t: t.id)
        if done:
            tasks = [t for t in tasks if t.done == done]
        if priority is not None:
            tasks = [t for t in tasks if t.priority == priority]
        return tasks

    def update(self, task_id: int, **changes) -> Task | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if "title" in changes:
            task.title = validate_title(changes["title"])
        if "priority" in changes:
            task.priority = validate_priority(changes["priority"])
        if "done" in changes:
            if not isinstance(changes["done"], bool):
                raise ValidationError("done must be a boolean")
            if changes["done"] and not task.done:
                task.completed_at = _now()
            elif not changes["done"]:
                task.completed_at = None
            task.done = changes["done"]
        return task

    def delete(self, task_id: int) -> bool:
        with self._lock:
            return self._tasks.pop(task_id, None) is not None

    def stats(self) -> dict:
        tasks = list(self._tasks.values())
        done = sum(1 for t in tasks if t.done)
        by_priority = {p: sum(1 for t in tasks if t.priority == p) for p in VALID_PRIORITIES}
        completion_rate = round(done / len(tasks), 2) if tasks else 0.0
        return {
            "total": len(tasks),
            "done": done,
            "open": len(tasks) - done,
            "completion_rate": completion_rate,
            "by_priority": by_priority,
        }
