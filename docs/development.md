# Development

**English** · [한국어](development.ko.md) · back to the [README](../README.md)

```sh
git clone https://github.com/DONGRYEOLLEE1/koda && cd koda
uv sync --extra tui                                          # without the extra, TUI tests fail
uv run python -m pytest --ignore=tests/test_live_llm.py -q   # python -m: `uv run pytest` may not find the script
```

- 404 tests pass on Python 3.13.13 and 3.14.5 (macOS, 2026-09-30, pytest reports 34-38 s). The CI workflow runs the same command on `ubuntu-latest` with Python 3.14 (from `.python-version`) for pushes to `main` and for pull requests. `tests/test_live_llm.py` calls a real endpoint and is excluded.
- About 6.9k lines of Python in 44 modules (2.7k of them the TUI), 23 prompt files, 5.8k lines of tests.
- `uv tool install --python 3.13 -e ".[tui]" --force` makes the `koda` on your PATH run your working tree.

Adding an agent: [architecture.md → Components](architecture.md#components) (the **Prompts** item). Running the benchmark suite: [benchmarks/README.md → Reproduce](../benchmarks/README.md#reproduce). Test isolation from shell variables, a local Langfuse stack and troubleshooting: [onboarding.md §11](onboarding.md#11-개발-환경) (Korean).
