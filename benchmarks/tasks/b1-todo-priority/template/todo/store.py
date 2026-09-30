"""JSON file storage for tasks."""
import json
from pathlib import Path

from .models import Task

FORMAT_VERSION = 1


class TaskStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self) -> list[Task]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [Task.from_dict(d) for d in data.get("tasks", [])]

    def save(self, tasks: list[Task]) -> None:
        payload = {"version": FORMAT_VERSION, "tasks": [t.to_dict() for t in tasks]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, title: str) -> Task:
        tasks = self.load()
        task = Task(id=max((t.id for t in tasks), default=0) + 1, title=title)
        tasks.append(task)
        self.save(tasks)
        return task

    def mark_done(self, task_id: int) -> Task:
        tasks = self.load()
        for t in tasks:
            if t.id == task_id:
                t.done = True
                self.save(tasks)
                return t
        raise KeyError(task_id)
