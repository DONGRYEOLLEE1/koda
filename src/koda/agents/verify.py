from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ..prompts import load_prompt
from .streaming import _to_text


class Verdict(BaseModel):
    """검증 판정 (구조화 출력)."""

    verdict: Literal["pass", "fail"] = Field(description="원 요청을 만족하면 pass, 아니면 fail")
    reasoning: str = Field(description="판정 근거 (한국어, 2문장 이내)")


def _parse_verdict_text(text: str) -> str:
    """폴백 텍스트 판정. 첫 비어있지 않은 줄이 정확히 PASS 일 때만 pass, 그 외 안전하게 fail(부분 매칭 오탐 방지)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    first = lines[0].upper().rstrip(".!:") if lines else ""
    return "pass" if first == "PASS" else "fail"


async def _judge(model, vtask: str, config=None):
    """단일 구조화 판정 (tool-loop 없음 → 빠르고 안정적). 실패 시 텍스트 파싱 폴백."""
    msgs = [SystemMessage(load_prompt("verifier")), HumanMessage(vtask)]
    try:
        # json_schema: 스키마를 문법으로 강제해 형식 밖 토큰 생성이 불가하다. 미지원 endpoint 는 예외 → 폴백.
        v = await model.with_structured_output(Verdict, method="json_schema").ainvoke(msgs, config=config)
        return v.verdict, v.reasoning
    except Exception:
        pass
    try:  # 폴백: 일반 호출 + 텍스트 첫 줄 파싱
        resp = await model.ainvoke(
            [SystemMessage(load_prompt("verifier")), HumanMessage(vtask + "\n\n첫 줄에 PASS 또는 FAIL 만 써라.")],
            config=config,
        )
        text = _to_text(resp.content).strip()
        return _parse_verdict_text(text), text or "판정 텍스트 없음"
    except Exception as e:
        return "fail", f"검증 판정 실패: {type(e).__name__}"
