import pytest

from todo.models import Task


def test_roundtrip_dict():
    t = Task(id=1, title="write report", done=True)
    assert Task.from_dict(t.to_dict()) == t


def test_empty_title_rejected():
    with pytest.raises(ValueError):
        Task(id=1, title="   ")
