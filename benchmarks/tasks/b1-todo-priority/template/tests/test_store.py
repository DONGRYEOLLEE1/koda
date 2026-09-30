from todo.store import TaskStore


def test_add_and_load(tmp_path):
    store = TaskStore(tmp_path / "todo.json")
    store.add("a")
    store.add("b")
    assert [t.title for t in store.load()] == ["a", "b"]
    assert [t.id for t in store.load()] == [1, 2]


def test_mark_done(tmp_path):
    store = TaskStore(tmp_path / "todo.json")
    store.add("a")
    store.mark_done(1)
    assert store.load()[0].done is True
