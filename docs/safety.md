# Safety model

**English** · [한국어](safety.ko.md) · back to the [README](../README.md)

Four layers, applied in order. Know the limits before you use `--yolo`.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/safety-layers-dark.svg">
    <img src="assets/safety-layers-light.svg" alt="koda's four safety layers, applied to every tool call in order: 1 a static deny gate on shell commands; 2 default-deny approval, where eight read-only tools run without asking and --yolo auto-approves the rest; 3 workspace confinement for file tools, with bubblewrap for shell commands on Linux only (macOS has none); 4 secret redaction in tool output, which does not cover the snapshot diff given to the verifier. --yolo only changes layer 2" width="860">
  </picture>
</p>

## A floor that `--yolo` can't lift

A static deny list runs before the approval gate. Commands on it (`sudo`, `curl … | sh`, `git reset --hard`, …) never run, and commands that touch credentials, `.env` or `git push --force` need a person to approve them, so `--yolo` refuses them.

- 11 deny + 6 confirm patterns and a two-stage `rm` check in [`denygate.py`](../src/koda/denygate.py), 51 unit tests.
- Regex-based.
- OS sandbox only on Linux with bubblewrap (see layer 3 below).

## The four layers

| Layer | What it does | Limits |
|---|---|---|
| 1. Deny list | Never runs `sudo`/`su`, `curl … \| sh`, `git reset --hard`, `git clean -f`, `chmod 777`, `mkfs`/`dd` to devices, `find -delete`, fork bombs, or recursive `rm` on dangerous targets. Commands that touch credential paths, key files or `.env`, `git push --force`, `find -exec`, and `cp`/`mv` out of the workspace need a person to approve them; `--yolo` refuses them. | Regex-based. Checks `run_command` and `run_readonly_command` (which refuses both tiers), not file or MCP tools. |
| 2. Approval gate | Every tool outside the 8 read-only ones asks first and defaults to deny, MCP tools included unless listed in `KODA_MCP_AUTO_APPROVE`. Hooked on sync and async paths. A TUI prompt left unanswered for 600 s is denied; piped `koda chat` without `--yolo` denies. | `--yolo` approves everything layer 1 allows. `run_readonly_command` is one of the 8 and never asks. |
| 3. Sandbox | File tools are confined to the workspace and can't see `.koda/`. With bubblewrap, `run_command` gets a read-only root with only the workspace and `~/.cache` writable, and the read-only shell runs with `--unshare-all` and read-only binds. | bubblewrap is Linux-only; `BH_SANDBOX=off` turns it off (for containers that can't create namespaces). Without it, shell commands run unsandboxed and koda prints a warning: `run_readonly_command` is then read-only by instruction and the deny list only. `run_command`'s sandbox doesn't cut the network. |
| 4. Redaction | Private-key blocks, `sk-`/`pk-`/`ghp_`/`gho_`/`xoxb`/`xoxp` tokens and `api_key`/`secret`/`token`/`password`/`authorization` assignments are masked in tool results before the model sees them, in saved artifacts and in the tool log the verifier reads. | Pattern-based. The file diff the verifier reads is not masked. |

In short: `--yolo` only changes layer 2. On macOS there is no bubblewrap, so shell commands run without an OS sandbox and only the deny list and instructions keep the read-only shell read-only.

## Measured under auto-approve

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/bench-guardrails-dark.svg">
    <img src="assets/bench-guardrails-light.svg" alt="Chart: guardrail outcomes under auto-approve. Piping a script into sh: all three tools held 3/3. Force push over a teammate's commits: koda and Hermes Agent kept them 3/3, opencode 0/3. Risky commands koda's model tried: 0 of 40 ran, 38 of them after its own verifier retried. Failure signal in the exit code: koda partial, 2 of 8 failed runs exited non-zero" width="860">
  </picture>
</p>

None of the 40 attempts koda's model made at `curl | sh`, `git push --force` or `git reset --hard` ran, but 38 of those attempts came from retries after koda's own verifier failed a first pass that had stopped or asked. One of koda's 12 guardrail runs still lost work through commands not on the list (Hermes Agent held 12/12). opencode documents no built-in command deny list; Hermes' model never tried `curl | sh` or a force push, and ran `git reset --hard` only after saving the work. Run `main-20260930`, 2026-09-30; details in [benchmarks/README.md](../benchmarks/README.md#results-run-main-20260930).

Troubleshooting the sandbox warning or containers that can't create namespaces: [onboarding.md §12](onboarding.md#12-문제-해결) (Korean).
