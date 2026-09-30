import io

import pytest

from todo.cli import main
from todo.models import Task


def run(db, *argv):
    out = io.StringIO()
    return main(["--db", str(db), *argv], out=out), out.getvalue()


def test_priority_default_and_validation():
    assert Task(id=1, title="a").priority == "normal"
    with pytest.raises(ValueError):
        Task(id=1, title="a", priority="urgent")


def test_list_sorted_by_priority(tmp_path):
    db = tmp_path / "todo.json"
    run(db, "add", "a")
    run(db, "add", "b", "--priority", "low")
    run(db, "add", "c", "--priority", "high")
    assert run(db, "list")[1].splitlines() == ["[ ] 3 (!) c", "[ ] 1 a", "[ ] 2 (~) b"]
