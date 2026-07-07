"""도구 출력 길이 상한 middleware.

초대형 도구 결과(MCP 문서 조회 등 수천~수만 자)가 대화 컨텍스트를 폭증시키면 이후 모든 모델
호출의 prefill 이 커지고, 추론 모델(Qwen)의 사고가 길어져 수 분짜리 무출력 생성이 발생한다
(실측: 재시도 라운드에서 단일 생성 160s). 도구 결과를 상한에서 절단해 이를 구조적으로 막는다.
"""
from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware


class ToolOutputTrimMiddleware(AgentMiddleware):
    def __init__(self, max_chars: int = 8000, store=None):
        """store(ArtifactStore)가 있으면 절단 전에 원문을 저장하고 재조회 안내를 붙인다."""
        super().__init__()
        self._max = max_chars
        self._store = store

    def _cut_note(self, tool_name: str, content: str) -> str:
        """절단 안내 문구. artifact 저장이 가능하면 원문 참조를 포함한다 (ROADMAP §2.3)."""
        if self._store is not None:
            ref = self._store.save(tool_name or "tool", content)
            return (
                f"\n…(도구 출력 {len(content)}자 중 {self._max}자로 절단 — 전체는 "
                f"read_artifact('{ref['id']}', start_line, end_line) 로 조회, 총 {ref['lines']}줄)"
            )
        return f"\n…(도구 출력 {len(content)}자 중 {self._max}자로 절단)"

    def _trim(self, result):
        content = getattr(result, "content", None)
        tool_name = getattr(result, "name", "") or ""
        if isinstance(content, str) and len(content) > self._max:
            result.content = content[: self._max] + self._cut_note(tool_name, content)
        elif isinstance(content, list):  # MCP 도구는 content block 리스트
            full = "\n".join(b.get("text") for b in content if isinstance(b, dict) and b.get("text"))
            used, out, truncated = 0, [], False
            for block in content:
                text = block.get("text") if isinstance(block, dict) else None
                if text is None:
                    out.append(block)
                    continue
                if used >= self._max:
                    truncated = True
                    break  # 이후 텍스트 블록은 안내 블록으로 대체
                if used + len(text) > self._max:
                    block = {**block, "text": text[: self._max - used] + "…(절단)"}
                    truncated = True
                out.append(block)
                used += len(text)
            if truncated:
                out.append({"type": "text", "text": self._cut_note(tool_name, full).strip()})
            result.content = out
        return result

    def wrap_tool_call(self, request, handler):
        return self._trim(handler(request))

    async def awrap_tool_call(self, request, handler):
        return self._trim(await handler(request))
