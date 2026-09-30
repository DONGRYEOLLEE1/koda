"""Hidden tests for b1: task priority across model, storage and CLI."""
import io
import json

import pytest

from todo.cli import main
from todo.models import Task
from todo.store import TaskStore


def run(db, *argv):
    out = io.StringIO()
    rc = main(["--db", str(db), *argv], out=out)
    return rc, out.getvalue()


def test_default_priority_is_normal():
    assert Task(id=1, title="a").priority == "normal"


@pytest.mark.parametrize("p", ["low", "normal", "high"])
def test_valid_priorities(p):
    assert Task(id=1, title="a", priority=p).priority == p


@pytest.mark.parametrize("p", ["urgent", "", None])
def test_invalid_priority_rejected(p):
    with pytest.raises(ValueError):
        Task(id=1, title="a", priority=p)


def test_store_roundtrip_keeps_priority(tmp_path):
    store = TaskStore(tmp_path / "todo.json")
    store.save([Task(id=1, title="a", priority="high"), Task(id=2, title="b", priority="low")])
    assert [(t.id, t.priority) for t in store.load()] == [(1, "high"), (2, "low")]


def test_legacy_file_without_priority(tmp_path):
    db = tmp_path / "todo.json"
    db.write_text(json.dumps({"version": 1, "tasks": [
        {"id": 1, "title": "old one", "done": False},
        {"id": 2, "title": "old two", "done": True},
    ]}), encoding="utf-8")
    tasks = TaskStore(db).load()
    assert [t.priority for t in tasks] == ["normal", "normal"]
    rc, text = run(db, "list")
    assert rc == 0 and text.splitlines() == ["[ ] 1 old one", "[x] 2 old two"]


def test_cli_add_with_priority(tmp_path):
    db = tmp_path / "todo.json"
    rc, _ = run(db, "add", "file taxes", "--priority", "high")
    assert rc == 0
    assert TaskStore(db).load()[0].priority == "high"


def test_cli_add_default_priority(tmp_path):
    db = tmp_path / "todo.json"
    run(db, "add", "water plants")
    assert TaskStore(db).load()[0].priority == "normal"


def test_cli_invalid_priority_is_usage_error(tmp_path):
    with pytest.raises(SystemExit) as e:
        run(tmp_path / "todo.json", "add", "x", "--priority", "urgent")
    assert e.value.code == 2


def test_cli_list_order_and_markers(tmp_path):
    db = tmp_path / "todo.json"
    run(db, "add", "a")                          # 1 normal
    run(db, "add", "b", "--priority", "low")     # 2 low
    run(db, "add", "c", "--priority", "high")    # 3 high
    run(db, "add", "d", "--priority", "high")    # 4 high
    run(db, "add", "e", "--priority", "low")     # 5 low
    run(db, "done", "2")
    rc, text = run(db, "list")
    assert rc == 0
    assert text.splitlines() == [
        "[ ] 3 (!) c",
        "[ ] 4 (!) d",
        "[ ] 1 a",
        "[x] 2 (~) b",
        "[ ] 5 (~) e",
    ]


def test_done_keeps_priority(tmp_path):
    db = tmp_path / "todo.json"
    run(db, "add", "c", "--priority", "high")
    run(db, "done", "1")
    assert TaskStore(db).load()[0].priority == "high"
