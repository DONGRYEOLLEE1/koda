"""artifact 재조회 도구 (읽기 전용). 절단된 도구 출력의 원문을 범위 지정으로 다시 본다.

trim 미들웨어가 저장한 `.koda/artifacts/` 원문에 대한 유일한 접근 경로다 —
`.koda/` 는 일반 파일 도구·스냅샷에서 제외되므로(ROADMAP §2.3) 이 도구로만 조회한다.
"""
from __future__ import annotations

from langchain.tools import tool

from ..artifacts import ArtifactStore


def make_artifact_tools(store: ArtifactStore) -> list:
    """artifact 저장소에 바인딩된 재조회 도구 목록."""

    @tool
    def read_artifact(artifact_id: str, start_line: int = 1, end_line: int = 200) -> str:
        """절단된 도구 출력의 원문에서 지정한 줄 범위를 읽는다.

        도구 결과에 "전체는 read_artifact('<id>', ...)" 안내가 있을 때 그 id 로 호출하라.

        Args:
            artifact_id: 안내 문구에 표시된 artifact id (예: "003-run_command")
            start_line: 시작 줄 (1-기반)
            end_line: 끝 줄 (포함)
        """
        return store.read_range(artifact_id, start_line, end_line)

    return [read_artifact]
