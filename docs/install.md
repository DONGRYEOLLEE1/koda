# Install

**English** · [한국어](install.ko.md) · back to the [README](../README.md)

```sh
curl -fsSL https://raw.githubusercontent.com/DONGRYEOLLEE1/koda/main/install.sh | sh
```

The script installs [uv](https://docs.astral.sh/uv/) if it is missing, then installs koda and its TUI as an isolated uv tool on Python 3.13. Re-run it to upgrade. It supports macOS and Linux; on Windows, run it inside WSL2.

## Installer options

Pass flags through the pipe with `… | sh -s -- <flags>`, or set the environment variable next to them ([`install.sh`](../install.sh)).

| Flag | Environment | Effect |
|---|---|---|
| `--ref <git-ref>` | `KODA_REF` | Branch, tag or commit to install (default: `main`) |
| `--python <version>` | `KODA_PYTHON` | Python for koda's tool environment (default: 3.13) |
| `--no-modify-path` | `KODA_NO_MODIFY_PATH=1` | Never edit shell startup files |
| `--uninstall` | | Remove koda (`uv tool uninstall koda`) |

## Other ways to install

```sh
# pin a branch, tag or commit
curl -fsSL https://raw.githubusercontent.com/DONGRYEOLLEE1/koda/main/install.sh | sh -s -- --ref <ref>

# uv directly (pass --python: uv doesn't pick an interpreter from requires-python)
uv tool install --python 3.13 "koda[tui] @ git+https://github.com/DONGRYEOLLEE1/koda"

# from a clone; -e runs your working tree
git clone https://github.com/DONGRYEOLLEE1/koda && cd koda
uv tool install --python 3.13 -e ".[tui]"
```

Drop the `[tui]` extra for a headless install (`koda run`, `koda chat`). Remove koda with `uv tool uninstall koda`.

## Next

- First run: [Quick start](../README.md#quick-start) in the README.
- Settings and providers: [configuration.md](configuration.md).
- Copy vs editable installs, checking which code runs, and install troubleshooting: [onboarding.md §3](onboarding.md#3-설치) and [§12](onboarding.md#12-문제-해결) (Korean).
