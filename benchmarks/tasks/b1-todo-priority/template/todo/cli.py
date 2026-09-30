"""Command-line front-end."""
import argparse
import os
import sys

from .store import TaskStore

DEFAULT_DB = "todo.json"


def format_task(task) -> str:
    box = "[x]" if task.done else "[ ]"
    return f"{box} {task.id} {task.title}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="todo")
    p.add_argument("--db", default=os.environ.get("TODO_DB", DEFAULT_DB))
    sub = p.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="add a task")
    add.add_argument("title")
    sub.add_parser("list", help="list tasks")
    done = sub.add_parser("done", help="mark a task as done")
    done.add_argument("id", type=int)
    return p


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    store = TaskStore(args.db)
    if args.command == "add":
        task = store.add(args.title)
        print(f"added {task.id}", file=out)
    elif args.command == "list":
        for task in store.load():
            print(format_task(task), file=out)
    elif args.command == "done":
        try:
            task = store.mark_done(args.id)
        except KeyError:
            print(f"no such task: {args.id}", file=sys.stderr)
            return 1
        print(f"done {task.id}", file=out)
    return 0
