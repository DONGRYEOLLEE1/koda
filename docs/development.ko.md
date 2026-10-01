# 개발

[English](development.md) · **한국어** · [README](../README.ko.md) 로 돌아가기

```sh
git clone https://github.com/DONGRYEOLLEE1/koda && cd koda
uv sync --extra tui                                          # extra 를 빼면 TUI 테스트가 깨진다
uv run python -m pytest --ignore=tests/test_live_llm.py -q   # `uv run pytest` 는 스크립트를 못 찾을 때가 있다
```

- 테스트 404건이 Python 3.13.13·3.14.5 에서 모두 통과한다(macOS, 2026-09-30, pytest 보고 34~38초). CI 워크플로는 `main` push 와 PR 마다 `ubuntu-latest` 의 Python 3.14(`.python-version`)로 같은 명령을 돌린다. `tests/test_live_llm.py` 는 실제 엔드포인트를 불러서 뺀다.
- 파이썬 44개 모듈 약 6.9k줄(그중 TUI 2.7k), 프롬프트 23개, 테스트 5.8k줄.
- `uv tool install --python 3.13 -e ".[tui]" --force` 로 설치하면 PATH 의 `koda` 가 작업 트리의 코드로 돈다.

에이전트 추가는 [architecture.ko.md 의 구성 요소](architecture.ko.md#구성-요소)(**프롬프트** 항목), 벤치마크 재현은 [benchmarks/README.md 의 Reproduce](../benchmarks/README.md#reproduce)(영문), 셸에 export 한 설정 변수를 테스트가 걷어내는 방식(격리), 로컬 Langfuse, 문제 해결은 [onboarding.md 11절](onboarding.md#11-개발-환경).
