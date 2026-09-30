import io

from todo.cli import main


def run(db, *argv):
    out = io.StringIO()
    rc = main(["--db", str(db), *argv], out=out)
    return rc, out.getvalue()


def test_add_list_done(tmp_path):
    db = tmp_path / "todo.json"
    assert run(db, "add", "Buy milk") == (0, "added 1\n")
    run(db, "add", "Call mom")
    run(db, "done", "1")
    rc, text = run(db, "list")
    assert rc == 0
    assert text.splitlines() == ["[x] 1 Buy milk", "[ ] 2 Call mom"]


def test_done_unknown_id(tmp_path):
    rc, _ = run(tmp_path / "todo.json", "done", "9")
    assert rc == 1
