from __future__ import annotations

from koda.agents import _parse_verdict_text


def test_parse_verdict_text_pass_only_when_exact():
    assert _parse_verdict_text("PASS\n근거...") == "pass"
    assert _parse_verdict_text("PASS.") == "pass"
    assert _parse_verdict_text("FAIL\n누락됨") == "fail"
    # 'PASS 기준 미달…' 이 pass 로 뒤집히면 안 된다(false-PASS 방지 → 안전하게 fail)
    assert _parse_verdict_text("PASS 기준 미달: return 누락") == "fail"
    assert _parse_verdict_text("") == "fail"
    assert _parse_verdict_text("<think>reasoning</think>") == "fail"


def test_judge_falls_back_to_text_when_schema_unsupported():
    # 폴백 경계: json_schema 미지원 endpoint → 텍스트 첫 줄 파싱으로 판정
    import asyncio

    from langchain_core.messages import AIMessage

    from koda.agents.verify import _judge

    class FakeStructured:
        async def ainvoke(self, msgs, config=None):
            raise RuntimeError("response_format not supported")

    class FakeModel:
        def with_structured_output(self, *a, **k):
            return FakeStructured()

        async def ainvoke(self, msgs, config=None):
            return AIMessage("PASS\n증거 확인됨")

    verdict, detail = asyncio.run(_judge(FakeModel(), "판정 과제"))
    assert verdict == "pass" and "증거" in detail


def test_judge_double_failure_is_safe_fail():
    # 구조화·폴백 모두 실패 → 안전하게 fail (false-PASS 금지 불변식)
    import asyncio

    from koda.agents.verify import _judge

    class Broken:
        def with_structured_output(self, *a, **k):
            return self

        async def ainvoke(self, msgs, config=None):
            raise RuntimeError("dead endpoint")

    verdict, detail = asyncio.run(_judge(Broken(), "판정 과제"))
    assert verdict == "fail" and "실패" in detail
