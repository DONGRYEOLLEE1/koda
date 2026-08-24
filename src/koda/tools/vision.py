from __future__ import annotations

import base64
from pathlib import Path

import httpx
from langchain.tools import tool
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from ..llm import structured
from ..prompts import load_prompt
from ..workspace import SandboxError, Workspace


ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_MM_PROJ_ERROR = (
    "오류: 추론 서버가 비전(mmproj)을 지원하지 않는다. llama.cpp 서버를 mmproj 와 함께 "
    "재기동해야 한다. 이미지 분석 없이 진행하라."
)
_VISION_SUPPORT_CACHE: dict[str, bool] = {}


class VisionAnalysis(BaseModel):
    """analyze_image 구조화 출력 — 범용 이미지 설명. 특정 도메인(UI·도표·사진 등)에
    치우치지 않고 관찰된 내용을 그대로 기술한다."""

    summary: str = Field(description="이미지 핵심 내용의 한국어 요약")
    details: list[str] = Field(default_factory=list, description="주요 관찰 사항 목록")
    uncertain: list[str] = Field(default_factory=list, description="불확실하거나 추측이 필요한 부분")


def _vision_supported(props_url: str) -> bool:
    """llama.cpp /props 의 vision 지원 여부를 프로세스 내 캐시로 확인한다."""
    if props_url not in _VISION_SUPPORT_CACHE:
        try:
            resp = httpx.get(props_url, timeout=3)
            resp.raise_for_status()
            data = resp.json()
            modalities = data.get("modalities", {})
            _VISION_SUPPORT_CACHE[props_url] = modalities.get("vision") is True
        except Exception:
            _VISION_SUPPORT_CACHE[props_url] = False
    return _VISION_SUPPORT_CACHE[props_url]


def _extension(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def _analysis_instruction(question: str) -> str:
    instruction = load_prompt("vision_analyze")  # 지시 본문은 prompts/ 로 관리
    question = question.strip()
    if question:
        instruction += f"\n질문: {question}"
    return instruction


def _has_server_error(json_output: str) -> bool:
    """LLM 출력에 서버 측 오류 문자열이 섞여 들어갔는지 검사한다."""
    _SERVER_ERROR_MARKERS = [
        "Media upload error",
        "Failed to upload media",
        "Temporary failure in name resolution",
        "Queue item marked as done",
    ]
    return any(marker in json_output for marker in _SERVER_ERROR_MARKERS)


def make_vision_tools(ws: Workspace, model, props_url: str) -> list:
    """워크스페이스에 바인딩된 이미지 분석 도구 목록."""

    @tool
    def analyze_image(path: str, question: str = "") -> str:
        """워크스페이스 내 이미지 파일을 분석한다. 결과는 내부용 구조화 데이터이며,
        이 데이터를 참고해 사용자에게 자연어로 설명해야 한다. 원본 JSON 을 그대로
        출력하지 말고, summary·details·uncertain 을 바탕으로 이해하기 쉬운
        자연어 응답을 구성하라."""
        try:
            image_path = ws.resolve(path)
        except SandboxError as e:
            return f"오류: {e}"

        ext = _extension(image_path)
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))
            return f"오류: 허용되지 않는 이미지 확장자: .{ext or '없음'}. 지원: {allowed}."

        try:
            size = image_path.stat().st_size
        except OSError as e:
            return f"오류: 이미지 파일을 읽을 수 없다({type(e).__name__})."
        if size > MAX_IMAGE_BYTES:
            return f"오류: 이미지 파일이 너무 크다({size} bytes). 최대 {MAX_IMAGE_BYTES} bytes."

        if not _vision_supported(props_url):
            return _MM_PROJ_ERROR

        try:
            b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
            content = [
                {"type": "text", "text": _analysis_instruction(question)},
                {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
            ]
            result = structured(model, VisionAnalysis, [HumanMessage(content=content)])
            if result is None:
                return "오류: 이 엔드포인트는 구조화 출력을 지원하지 않는다. 텍스트 정보로만 진행하라."
            raw = result.model_dump_json(indent=None)
            # 서버 측 오류가 LLM 출력에 섞이면 손상된 결과 대신 복구 가능한 오류로 처리한다.
            if _has_server_error(raw):
                return "오류: 이미지 분석 중 서버 오류가 발생했다. 텍스트 정보로만 진행하라."
            return raw
        except Exception as e:
            return f"오류: 이미지 분석 실패({type(e).__name__}): 텍스트 정보로만 진행하라."

    return [analyze_image]
