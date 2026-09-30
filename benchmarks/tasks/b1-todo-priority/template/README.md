# todo

A tiny command-line to-do list stored in a JSON file.

    python -m todo add "Buy milk"
    python -m todo list
    python -m todo done 1

Options: `--db PATH` (default `todo.json`, or the `TODO_DB` environment variable).

## Layout

- `todo/models.py` - the `Task` dataclass and its (de)serialization
- `todo/store.py`  - JSON file storage (`TaskStore`)
- `todo/cli.py`    - argparse front-end (`main(argv, out)`)

## Tests

    python -m pytest
