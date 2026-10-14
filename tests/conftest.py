from __future__ import annotations

import os

import pytest

from koda.workspace import Workspace

# koda 설정 계열 환경변수. 개발자 셸에 export 되어 있으면 ModelSettings 가 그 값을 읽어 테스트
# 결과가 사람마다 갈린다(koda 로 koda 를 개발하면 이 변수들이 셸에 상주한다). 값이 필요한 테스트는
# monkeypatch.setenv 로 직접 세운다.
_SETTINGS_ENV_PREFIXES = ("OPENAI_", "LOCAL_", "DEEPSEEK_", "LANGFUSE_", "KODA_",
                          "THINKING_BUDGET_", "REASONING_", "VISION_")
_SETTINGS_ENV_NAMES = ("MODEL", "PRIMARY", "FALLBACK_ENABLED", "STRUCTURED_METHOD",
                       "CONTEXT_BUDGET_TOKENS")


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch):
    """설정 계열 환경변수를 걷어낸다. 테스트는 어떤 셸에서도 같은 결과를 내야 한다."""
    for key in list(os.environ):
        if key in _SETTINGS_ENV_NAMES or key.startswith(_SETTINGS_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def make_workspace(tmp_path):
    """임시 파일을 깔고 Workspace 를 반환하는 팩토리."""

    def _make(files: dict | None = None) -> Workspace:
        for rel, content in (files or {}).items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return Workspace(tmp_path)

    return _make
