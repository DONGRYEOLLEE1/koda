from notes.store import NoteStore, slugify


def test_slugify():
    assert slugify("Hello, World!") == "hello-world"


def test_add_and_titles(tmp_path):
    s = NoteStore(tmp_path)
    s.add("B note", "x")
    s.add("A note", "y")
    assert s.titles() == ["A note", "B note"]
