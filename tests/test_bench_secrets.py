"""benchmarks/run.py secret scrubbing: placeholder keys of local vision servers must not rewrite the judged workspace."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RUN_PY = Path(__file__).resolve().parents[1] / "benchmarks" / "run.py"


@pytest.fixture(scope="module")
def run_module():
    pytest.importorskip("yaml")
    spec = importlib.util.spec_from_file_location("bench_run", RUN_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_placeholder_keys_are_not_secrets(run_module):
    for placeholder in ("none", "EMPTY", "ollama", "sk-1234", "lm-studio", "x" * 15, "abcdefghijklmnopqrstuvwxyz"):
        assert not run_module.looks_like_secret(placeholder), placeholder
    for real in ("fake-0123456789abcdef0123456789abcdef", "fake-or-v1-" + "a1" * 20, "a1b2c3d4" * 4):
        assert run_module.looks_like_secret(real), real


def test_scrub_leaves_workspace_text_alone_for_placeholder_keys(run_module, tmp_path):
    (tmp_path / "site").mkdir()
    css = tmp_path / "site" / "style.css"
    css.write_text(".hero h1 { display: none; }\n", encoding="utf-8")
    answer = tmp_path / "answer.txt"
    answer.write_text("ollama model answered with key fake-0123456789abcdef0123456789abcdef\n", encoding="utf-8")

    secrets = [k for k in ("none", "ollama", "fake-0123456789abcdef0123456789abcdef") if run_module.looks_like_secret(k)]
    assert secrets == ["fake-0123456789abcdef0123456789abcdef"]
    assert run_module.scrub_secret(tmp_path, secrets) == 1
    assert css.read_text(encoding="utf-8") == ".hero h1 { display: none; }\n"
    assert answer.read_text(encoding="utf-8") == "ollama model answered with key [REDACTED:API_KEY]\n"


def test_read_secrets_skips_placeholders_and_logs(run_module, monkeypatch):
    values = {"DEEPSEEK_API_KEY": "fake-0123456789abcdef0123456789abcdef", "VISION_API_KEY": "none"}
    logged: list[str] = []
    monkeypatch.setattr(run_module, "_key_from_env_file", lambda name: values.get(name, ""))
    monkeypatch.setattr(run_module, "log", logged.append)
    assert run_module.read_secrets() == ["fake-0123456789abcdef0123456789abcdef"]
    assert logged and "VISION_API_KEY" in logged[0] and "placeholder" in logged[0]
