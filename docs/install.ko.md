# 설치

[English](install.md) · **한국어** · [README](../README.ko.md) 로 돌아가기

```sh
curl -fsSL https://raw.githubusercontent.com/DONGRYEOLLEE1/koda/main/install.sh | sh
```

[uv](https://docs.astral.sh/uv/) 가 없으면 먼저 설치하고, koda 를 TUI 까지 포함해 Python 3.13 기반의 독립된 uv 도구로 설치한다. 다시 실행하면 업그레이드된다. macOS·Linux 를 지원하고, Windows 에서는 WSL2 안에서 실행한다.

## 설치 스크립트 옵션

옵션은 `… | sh -s -- <옵션>` 처럼 파이프 뒤에 넘기거나, 표의 같은 줄에 있는 환경변수로 준다([`install.sh`](../install.sh)).

| 옵션 | 환경변수 | 하는 일 |
|---|---|---|
| `--ref <git-ref>` | `KODA_REF` | 설치할 브랜치·태그·커밋(기본 `main`) |
| `--python <version>` | `KODA_PYTHON` | koda 도구 환경의 Python(기본 3.13) |
| `--no-modify-path` | `KODA_NO_MODIFY_PATH=1` | 셸 설정 파일을 건드리지 않는다 |
| `--uninstall` | | koda 를 제거한다(`uv tool uninstall koda`) |

## 다른 설치 방법

```sh
# 브랜치·태그·커밋 고정
curl -fsSL https://raw.githubusercontent.com/DONGRYEOLLEE1/koda/main/install.sh | sh -s -- --ref <ref>

# uv 로 직접 (uv 는 requires-python 을 보고 인터프리터를 고르지 않으므로 --python 을 준다)
uv tool install --python 3.13 "koda[tui] @ git+https://github.com/DONGRYEOLLEE1/koda"

# 클론해서 설치. -e 면 작업 트리의 코드가 그대로 돈다
git clone https://github.com/DONGRYEOLLEE1/koda && cd koda
uv tool install --python 3.13 -e ".[tui]"
```

TUI 없이 `koda run`·`koda chat` 만 쓸 거라면 `[tui]` 를 뺀다. 화면 도구를 쓰려면 `visual` 을 더하고(`koda[tui,visual]`) 그 인터프리터로 `python -m playwright install chromium` 을 실행한다: [visual.ko.md](visual.ko.md#설치). 제거는 `uv tool uninstall koda`.

## 다음 단계

- 첫 실행: README 의 [빠른 시작](../README.ko.md#빠른-시작).
- 설정과 provider: [configuration.ko.md](configuration.ko.md).
- 사용자용·개발용 설치의 차이, 지금 어떤 코드가 도는지 확인하기, 설치 문제 해결: [onboarding.md 3절](onboarding.md#3-설치)·[12절](onboarding.md#12-문제-해결).
