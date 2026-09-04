from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware

from ..artifacts import redact
from ..config import DEFAULT_CONTEXT_BUDGET, budget_scale

# 오픈웹 벌크 검색/크롤류는 한 번에 수천~수만 자를 뱉어 턴 누적의 주범이라 더 낮은 상한을 적용한다.
_SEARCH_TOOLS = ("web_search", "crawl", "scrape", "deep_research")


class ToolOutputTrimMiddleware(AgentMiddleware):
    def __init__(self, max_chars: int = 8000, store=None, search_max_chars: int = 3000,
                 context_budget: int = DEFAULT_CONTEXT_BUDGET):
        """store 가 있으면 절단 전 원문을 저장하고 재조회 안내를 붙인다. search_max_chars 는 벌크 검색/크롤류 전용 상한이며, 두 상한 모두 context_budget 에 비례해 커진다."""
        super().__init__()
        scale = budget_scale(context_budget)
        self._max = int(max_chars * scale)
        self._search_max = int(search_max_chars * scale)
        self._store = store

    def _cap_for(self, tool_name: str) -> int:
        name = (tool_name or "").lower()
        return self._search_max if any(k in name for k in _SEARCH_TOOLS) else self._max

    def _cut_note(self, tool_name: str, content: str, cap: int) -> str:
        """절단 안내 문구. artifact 저장이 가능하면 원문 참조를 포함한다."""
        if self._store is not None:
            ref = self._store.save(tool_name or "tool", content)
            return (
                f"\n…(도구 출력 {len(content)}자 중 {cap}자로 절단 — 전체는 "
                f"read_artifact('{ref['id']}', start_line, end_line) 로 조회, 총 {ref['lines']}줄)"
            )
        return f"\n…(도구 출력 {len(content)}자 중 {cap}자로 절단)"

    def _trim(self, result):
        content = getattr(result, "content", None)
        tool_name = getattr(result, "name", "") or ""
        cap = self._cap_for(tool_name)
        if isinstance(content, str):
            content, _ = redact(content)  # secret 마스킹 — 접근 경로(cat/find/read)와 무관하게 적용
            if len(content) > cap:
                content = content[:cap] + self._cut_note(tool_name, content, cap)
            result.content = content
        elif isinstance(content, list):  # MCP 도구는 content block 리스트
            full = "\n".join(redact(b["text"])[0] for b in content
                             if isinstance(b, dict) and b.get("text"))
            used, out, truncated = 0, [], False
            for block in content:
                text = block.get("text") if isinstance(block, dict) else None
                if text is None:
                    out.append(block)
                    continue
                text = redact(text)[0]  # 블록별 secret 마스킹
                if used >= cap:
                    truncated = True
                    break  # 이후 텍스트 블록은 안내 블록으로 대체
                if used + len(text) > cap:
                    text = text[: cap - used] + "…(절단)"
                    truncated = True
                out.append({**block, "text": text})
                used += len(text)
            if truncated:
                out.append({"type": "text", "text": self._cut_note(tool_name, full, cap).strip()})
            result.content = out
        return result

    def wrap_tool_call(self, request, handler):
        return self._trim(handler(request))

    async def awrap_tool_call(self, request, handler):
        return self._trim(await handler(request))
