<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo-light.svg" alt="koda" width="280">
  </picture>
</p>

<p align="center"><b>A routed, self-verifying coding agent for your terminal.</b></p>
<p align="center">koda sends each request down the narrowest path that can handle it,<br>and judges every edit by the file diff and the tool log, not by what the model says it did.</p>

<p align="center">
  <a href="https://github.com/DONGRYEOLLEE1/koda/actions/workflows/test.yml"><img alt="tests" src="https://github.com/DONGRYEOLLEE1/koda/actions/workflows/test.yml/badge.svg"></a>
  <img alt="Python 3.13+" src="https://img.shields.io/badge/python-3.13%2B-3776ab?style=flat-square">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue?style=flat-square"></a>
  <a href="benchmarks/README.md"><img alt="benchmarked vs opencode and Hermes" src="https://img.shields.io/badge/benchmarked-vs%20opencode%20%C2%B7%20hermes-7c74f4?style=flat-square"></a>
</p>

<p align="center"><b>English</b> · <a href="README.ko.md">한국어</a></p>

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/DONGRYEOLLEE1/koda/main/install.sh | sh
```

Installs [uv](https://docs.astral.sh/uv/) if it is missing, then koda and its TUI as an isolated uv tool on Python 3.13. Re-run to upgrade. macOS and Linux; on Windows, run it inside WSL2. Or with uv directly:

```sh
uv tool install --python 3.13 "koda[tui] @ git+https://github.com/DONGRYEOLLEE1/koda"
```

Pinning a version, installing from a clone, headless installs and uninstalling: [docs/install.md](docs/install.md). The screen tools need the `visual` extra (`koda[tui,visual]`) and a chromium build: [docs/visual.md](docs/visual.md#install).

> [!NOTE]
> koda's prompts are written in Korean, so final answers currently come back in Korean even for English requests. The CLI help and TUI labels are Korean as well.

<!-- DEMO, after the repo is public: swap the GIF for a video.
     1. Drag the English-subtitle highlight cut (koda-readme.en.mp4, 32 s, about 1.9 MB; the limit is 10 MB) into any issue or PR comment box on github.com and copy the https://github.com/user-attachments/assets/<uuid> URL it inserts.
     2. Replace the whole <p align="center"> block below with that URL alone on its own line, blank lines around it.
        GitHub plays video only from a bare user-attachments URL; <video> tags and repo paths are not rendered.
     3. Keep the caption as a plain <sub> line under it, check that it plays in a logged-out browser,
        and delete docs/assets/koda-demo.gif if nothing else uses it. -->
<p align="center">
  <img src="docs/assets/koda-demo.gif" alt="koda in the TUI: a request is routed, tools run, the diff is shown and the verifier passes the edit" width="860">
  <br><sub>Real session on DeepSeek <code>deepseek-flash</code>. koda's UI and answers are in Korean; English subtitles are overlaid.</sub>
</p>

<!-- CODESPACES: after the repo is public, add
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/DONGRYEOLLEE1/koda?quickstart=1)
(.devcontainer/ asks for DEEPSEEK_API_KEY as a recommended secret; compute is billed to the visitor's account.) -->

## Quick start

```sh
export DEEPSEEK_API_KEY=sk-...   # the only required setting
cd your-project
koda                             # TUI. Asks before any tool call outside the 8 read-only tools
```

Headless: `koda run "add a sub() function to calc.py and test it" --workspace . --yolo`. Exit code `0` means the turn finished (for an edit, the verifier passed), `1` a failed verification or an empty answer, `2` a configuration error or a crash.

## Why koda

- **Routes by intent, least privilege first.** A router sends every request to `answer`, `read`, `inspect` or `edit`. General questions get no file tools, and only `edit` gets write tools and a verifier. [How →](docs/architecture.md#routing-least-privilege-first)
- **Judges edits from the diff and the tool log.** The verifier reads a file-hash diff and the tool calls the runtime actually recorded; the coder's own summary counts as a claim, not evidence. A fail gets one retry; if the verdict is still fail, `koda run` exits 1. The verdict itself is a model call, not a guarantee. [How →](docs/architecture.md#how-an-edit-is-judged)
- **Uses the screen as evidence.** For screenshot bug fixes, refactors that must not change the screen and design matching, the coder renders the page headless, parses captures with a vision model and diffs two screens in code; the verifier reads that evidence before the coder's claims. Needs the `visual` extra and chromium. [Screen evidence →](docs/visual.md)
- **A safety floor that `--yolo` can't lift.** A static deny list runs before the approval gate. Commands on it (`sudo`, `curl … | sh`, `git reset --hard`, …) never run, and commands that touch credentials, `.env` or `git push --force` need a person, so `--yolo` refuses them. The deny list is regex-based; the OS sandbox needs Linux with bubblewrap. [Safety model →](docs/safety.md)
- **Parallel sessions in one TUI.** Each session runs on its own worker. Background sessions keep answering and hold approval requests until you open them. [TUI →](docs/features.md#tui-and-sessions)

## Benchmarks

koda is measured against [opencode](https://github.com/anomalyco/opencode) and [Hermes Agent](https://github.com/NousResearch/hermes-agent) on the same model (DeepSeek `deepseek-flash`), with the same Korean prompts, the same timeout and auto-approve on for all three: 10 tasks, 42 runs per tool, each run judged by hidden tests or git and regex checks, not by the agent's own report. The charts show where koda trails as plainly as where it leads.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/bench-scorecard-dark.svg">
    <img src="docs/assets/bench-scorecard-light.svg" alt="Chart: scorecard of koda, opencode and Hermes Agent on the same model. koda trails on 8 of 11 rows: edit tasks passed 13/20 vs 20/20 and 20/20, guardrails held 11/12 vs 9/12 and 12/12, false success 5 of 20 vs 0 and 0, wall time per run 27.0 s vs 13.0 s and 27.4 s, cost per run $0.0079 vs $0.0052 and $0.0109, cost per run on edit tasks $0.0125 vs $0.0099 and $0.0153, input tokens per run 78,670 vs 52,428 and 148,932, model calls 14 vs 7 and 9. It leads only on read-only and general questions: $0.0015 vs $0.0031 and $0.0050 per run, under a cache condition that favours koda, and 9,969 vs 17,491 and 39,380 input tokens, from a shorter prompt and fewer tools" width="860">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/bench-run-matrix-dark.svg">
    <img src="docs/assets/bench-run-matrix-light.svg" alt="Chart: every run of every task. koda passed or held 34 of 42 runs, opencode 39, Hermes Agent 42. All three passed a1, b1, e1 and f1 and held g1 and g2. koda failed 3 of 5 c1 runs and 4 of 5 d1 runs, 5 of those 7 with exit 0, and lost work in 1 of 3 g4 runs through git checkout -- and rm -rf, which are not on its deny list. In g3 opencode force-pushed over the teammate's commits in 3 of 3 runs, as the user asked; koda and Hermes Agent kept them, and the user's own commit reached the remote in 1 of 3 koda runs and 3 of 3 Hermes runs. Of the 40 risky commands koda's model tried under --yolo, 0 ran: 39 refused by the deny gate, 1 stopped by the step cap, 38 of them in runs koda retried after its own verifier failed a first pass" width="860">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/bench-paired-dark.svg">
    <img src="docs/assets/bench-paired-light.svg" alt="Chart: paired ratios of tool over koda in the same round. opencode: wall time 0.66x over all 42 rounds and 0.64x on edit tasks, cost 0.92x and 0.76x, output tokens 0.77x, model calls 0.53x. Hermes Agent: wall time 1.05x (koda faster in 24 of 42 rounds), cost 1.67x and 1.21x, input tokens 2.70x. On read-only and general questions both tools cost more than koda, 2.52x and 4.60x, and sent more input tokens, 1.96x and 4.59x; with every input token cached koda would not be cheaper than opencode" width="860">
  </picture>
</p>

<sub>koda 0.1.0 @ 6d242f2, opencode 1.18.33, Hermes Agent 2026.9.24, all on DeepSeek `deepseek-flash` with auto-approve; run `main-20260930`, 2026-09-30 12:13-12:42 UTC. Cost = DeepSeek list price, peak basis. Small n, no significance tests. koda's router, planner, coder and verifier run with reasoning off by design while the other two think; max output tokens, step caps and output language differ too.</sub>

**Full results, method and the [fairness disclosure](benchmarks/README.md#fairness-disclosure): [benchmarks/README.md](benchmarks/README.md).** koda trails both tools on edit-task success (13 of 20 runs vs 20/20; its verifier passed 5 of the 7 failed runs), opencode on speed and on cost, and Hermes Agent on guardrail runs held. It leads only on short read-only and general questions ($0.0015 per run vs $0.0031 and $0.0050), where a new directory per run kept the other two tools' prompts out of the cache. The read-only cost bars and the guardrail outcome matrix are in [docs/architecture.md](docs/architecture.md) and [docs/safety.md](docs/safety.md). `benchmarks/charts.py` regenerates the charts above from the run's `results.json`.

## Architecture

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture-overview-dark.svg">
    <img src="docs/assets/architecture-overview-light.svg" alt="koda architecture overview: a router sends each request to answer, reader, investigator or the edit path; read paths end without verification, and the edit path runs an optional planner, a coder with write tools, and a verifier that judges a hash-snapshot diff plus the recorded tool log, retrying once" width="860">
  </picture>
</p>

A LangGraph `StateGraph` built per turn. Every agent node runs the same middleware chain: step cap, approval gate with the deny list first, output trim and redaction, history trim. The full diagram with that chain, the Mermaid version, components and one edit turn step by step: [docs/architecture.md](docs/architecture.md).

## Documentation

| Topic | Guides |
|---|---|
| Getting started | [Install](docs/install.md) · [Onboarding](docs/onboarding.md) (Korean) · [TUI guide](docs/tui.md) (Korean) |
| How it works | [Features](docs/features.md) · [Architecture](docs/architecture.md) · [Safety model](docs/safety.md) · [Screen evidence](docs/visual.md) |
| Configuration | [Settings and providers](docs/configuration.md) · [Per-provider setup](docs/providers/README.md) (Korean) · [MCP](docs/mcp/README.md) (Korean) |
| Evaluation and development | [Benchmarks](benchmarks/README.md) · [Development](docs/development.md) |

What each guide covers: [docs/README.md](docs/README.md). Guides marked (Korean) are written in Korean only for now.

## License

MIT. See [LICENSE](LICENSE).
