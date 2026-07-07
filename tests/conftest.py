"""pytest 공용 픽스처."""
from __future__ import annotations

import pytest

from build_harness.workspace import Workspace


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
