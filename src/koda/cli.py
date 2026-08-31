from __future__ import annotations

import argparse
import asyncio
import sys
import time
import warnings

# with_structured_output 의 Pydantic v2 직렬화 과정에서 나오는 경고는 무해하므로 억제한다.
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")

from .agents import remember_from_turn, run_task, summarize_history
from .config import load_model_settings
from .memory import Memory, parse_remember
from .mcp import close_mcp, load_mcp_tools, mcp_dirs
from .session import Session
from .workspace import Workspace

_PHASE = {
    "route": "🔎 라우팅", "answer": "💬 답변 중", "read": "📖 읽는 중", "edit": "✏️  편집 중",
    "verify": "🔍 검증 중", "plan": "📋 계획 수립", "review": "🧐 독립 리뷰",
}


def _use_rich_renderer() -> bool:
    """Rich 마크다운 렌더링 사용 여부. stdout 이 TTY 일 때만 활성화한다(출력 대상 기준이지 stdin 기준이 아니다)."""
    return sys.stdout.isatty()


def _format_phase(phase: str) -> str:
    """phase 이벤트 → 표시 문자열 (rich/plain 공용)."""
    if phase in _PHASE:
        return f"[{_PHASE[phase]}…]"
    if phase.startswith("tool:"):
        return f"· 도구 실행: {phase[5:]}"
    if phase.startswith("retry:"):
        return f"· 빈 응답({phase[6:]}) — 변형 재시도"
    if phase.startswith("mcp:"):
        return f"· {phase[4:]}"
    if phase.startswith("heartbeat:"):
        return f"· 진행 중… ({phase[10:]} 경과)"
    if phase.startswith("think:"):
        return f"· 🤔 생각 중… (~{phase[6:]} tok)"
    return f"· {phase}"


class MarkdownStreamRenderer:
    """Rich Live + Markdown 스트리밍 렌더러. 본문은 TTY 는 finalize() 가 1회, non-TTY 는 on_token 실시간 출력으로 — 정확히 1회만 나온다."""

    def __init__(self, use_rich: bool):
        self._buffer = ""
        self._status = ""
        self._phase_status = ""    # 마지막 phase 라벨 (think 표시 해제 시 복귀 지점)
        self._last_flush = 0.0     # 시간 기반 쓰로틀
        self._char_since_flush = 0  # 문자 수 기반 쓰로틀
        if use_rich:
            from rich.console import Console, Group
            from rich.live import Live
            from rich.markdown import Markdown
            from rich.text import Text
            self._Group, self._Markdown, self._Text = Group, Markdown, Text
            self._console = Console()
            # transient=True: Live 중간 프레임은 종료 시 화면에서 제거된다.
            self._live = Live(Markdown(""), console=self._console, transient=True,
                              auto_refresh=False)
        else:
            self._live = None

    def __enter__(self):
        if self._live:
            self._live.start()
        return self

    def __exit__(self, *args):
        # 출력하지 않는다 — 최종 출력 지점은 finalize() 하나뿐이라, 이미 stop 했어도 예외를 무시한다.
        if self._live:
            try:
                self._live.stop()
            except Exception:
                pass
        return False

    def on_token(self, text: str):
        self._buffer += text
        if not self._live:
            sys.stdout.write(text)
            sys.stdout.flush()
            return
        if self._status != self._phase_status:
            self._status = self._phase_status  # 내용 생성 시작 → "생각 중" 표시 해제
        self._char_since_flush += len(text)
        elapsed = time.monotonic() - self._last_flush
        # 갱신 전략: 문단 경계 즉시 / 80자+50ms / 20자+100ms. 문단 경계는 버퍼 꼬리에서 찾아 청크
        # 2개에 걸쳐 쪼개져도 검출한다.
        if ("\n\n" in self._buffer[-(len(text) + 1):]
                or (self._char_since_flush >= 80 and elapsed >= 0.05)
                or (self._char_since_flush >= 20 and elapsed >= 0.1)):
            self._render()

    def on_phase(self, phase: str):
        if phase.startswith("retry:"):
            # 빈 응답 재시도: 1차 시도의 부분 스트림을 폐기해 "앞부분이 두 번 나오는" 버퍼 오염을 막는다.
            self._buffer = ""
        if phase.startswith("think:"):
            # reasoning 진행은 status line 만 갱신한다 — 영구 라벨은 스크롤백 스팸이고, plain 은 침묵(하트비트가 생존 신호 담당).
            if self._live:
                self._status = _format_phase(phase)
                self._render()
            return
        label = _format_phase(phase)
        self._status = label
        self._phase_status = label
        if self._live:
            # Live 가 소유한 같은 console 에 print — 별도 stderr Console 을 쓰면 커서 추적이 깨져 화면이 깨진다.
            self._console.print(label, style="dim italic")
            self._render()
        else:
            prefix = "\n\n" if phase in _PHASE else "  "
            sys.stderr.write(f"{prefix}{label}\n")
            sys.stderr.flush()

    def finalize(self, answer: str | None = None):
        """스트리밍 종료 후 최종 정본을 단 1회 출력한다 — 유일한 최종 출력 지점. TTY 는 렌더링본을, plain 은 이미 출력했으므로 재출력하지 않는다."""
        if answer and answer.strip():
            self._buffer = answer
        if self._live:
            self._live.stop()  # transient=True → 중간 프레임은 화면에서 제거됨
            if self._buffer.strip():
                self._console.print(self._Markdown(self._buffer))

    def _render(self):
        """누적 버퍼를 Rich Markdown 으로 파싱해 Live 업데이트한다. rich 마크업은 Markdown 안에서 해석 안 되므로 status 는 Text 로 합성한다."""
        md = self._Markdown(self._buffer)
        view = self._Group(md, self._Text(self._status, style="dim italic")) if self._status else md
        self._live.update(view)
        self._live.refresh()
        self._char_since_flush = 0
        self._last_flush = time.monotonic()


def _interactive_approver(name, args) -> bool:
    sys.stderr.write(f"\n[승인 필요] 도구={name} args={args}\n실행을 승인하시겠습니까? [y/N] ")
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def _auto_approver(name, args) -> bool:
    return True


_auto_approver.auto = True  # denygate confirm 등급이 자동 승인을 거부하는 근거 (HITL 필수)


async def _run_with_heartbeat(coro, on_phase, interval: float = 30.0):
    """장시간 무출력 생성 중에도 살아있음을 표시한다. on_phase("heartbeat:Ns") 로 통합 — rich 는 status line, plain 은 stderr."""
    task = asyncio.ensure_future(coro)
    start = time.monotonic()
    while True:
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=interval)
        except asyncio.TimeoutError:
            on_phase(f"heartbeat:{int(time.monotonic() - start)}s")


def _cmd_run(args) -> int:
    if (rc := _preflight_config()) is not None:
        return rc
    ws = Workspace(args.workspace)
    approver = _auto_approver if args.yolo else _interactive_approver
    mcps_dir = None if args.no_mcp else mcp_dirs(ws.root, args.mcps_dir)
    with MarkdownStreamRenderer(use_rich=_use_rich_renderer()) as renderer:
        try:
            result = asyncio.run(_run_with_heartbeat(
                run_task(args.task, ws, approver, renderer.on_token, renderer.on_phase,
                         mcps_dir=mcps_dir),
                renderer.on_phase,
            ))
        except Exception as e:
            print(f"\n실행 실패: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        renderer.finalize(result.get("final"))
    verdict = result.get("verdict")
    tail = f" | verdict: {verdict}" if verdict else ""
    print(f"\n\n--- route: {result['route']}{tail} ---")
    if result.get("verify_detail"):
        print(f"판정 근거: {result['verify_detail']}")
    if result.get("trace_url"):
        print(f"langfuse trace: {result['trace_url']}")
    if result.get("empty_final"):  # 빈 final 은 verdict 유무와 무관하게 실패다
        print(f"실행 실패: empty_final ({result.get('empty_reason') or 'unknown'})", file=sys.stderr)
        return 1
    return 0 if verdict in (None, "", "pass") else 1


def _err(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _preflight_config() -> int | None:
    """쓸 수 있는 엔드포인트가 하나도 없으면 첫 호출의 401/연결 오류 대신 안내를 낸다. 정상이면 None."""
    from pydantic import ValidationError
    from .llm import select_provider
    try:
        settings = load_model_settings()
    except ValidationError as e:
        _err(f"설정값이 잘못됐습니다: {e}")
        return 2
    provider, _reason = select_provider(settings)
    if provider == "openai" and not settings.openai_api_key:
        key_env = "DEEPSEEK_API_KEY" if settings.provider_name == "deepseek" else "OPENAI_API_KEY"
        _err(f"API 키가 없습니다. 모델 '{settings.openai_model}' ({settings.provider_name}, {settings.openai_base_url}) 에 쓸 키를 넘기세요:")
        _err(f"  export {key_env}=...")
        _err("  # 모델 전환은 MODEL=gpt-5 / MODEL=deepseek-v4-flash 처럼 모델명만 바꾸면 된다 (base_url·파라미터는 자동)")
        _err(f"  # 로컬 llama.cpp 를 쓰려면: PRIMARY=local LOCAL_OPENAI_BASE_URL=... (현재 {settings.local_base_url} 미응답)")
        return 2
    return None


def _deny_approver(name, args) -> bool:
    """비대화형 입력에서 승인 질문은 불가 — stdin 을 읽으면 다음 작업 줄을 승인 답변으로 삼킨다. --yolo 없인 안전하게 거부한다."""
    _err(f"[승인 거부] 비대화형 모드에서 --yolo 없이 위험 도구 '{name}' 는 실행하지 않습니다.")
    return False


def _cmd_chat(args) -> int:
    """멀티턴 REPL. stdin 이 tty 가 아니면 줄 단위 배치 실행(파이프 검증 경로)."""
    if (rc := _preflight_config()) is not None:
        return rc
    ws = Workspace(args.workspace)
    if args.yolo:
        approver = _auto_approver
    elif sys.stdin.isatty():
        approver = _interactive_approver
    else:
        approver = _deny_approver
    memory = Memory(ws.root)

    if args.resume:
        try:
            session = Session.load(ws.root, args.resume)
            _err(f"세션 재개: {session.id} (턴 {len(session.turns)}개 로드)")
        except FileNotFoundError:
            _err(f"세션 없음: {args.resume} (목록: {', '.join(Session.list_ids(ws.root)) or '없음'})")
            return 2
    else:
        session = Session(ws.root)
        _err(f"새 세션: {session.id}")

    # MCP 는 세션당 1회 로드 (툴 목록 안정 = 프롬프트 캐시 prefix 안정, 턴당 로드 지연 제거)
    mcp_tools: list = []
    if not args.no_mcp:
        try:
            mcp_tools = asyncio.run(load_mcp_tools(mcp_dirs(ws.root, args.mcps_dir), on_log=lambda m: _err(f"  · {m}"), cwd=ws.root))
        except Exception as e:
            _err(f"  · MCP 로드 실패({type(e).__name__}) — 로컬 도구만 사용")

    interactive = sys.stdin.isatty()
    if interactive:
        _err("명령: /remember [semantic|episodic|procedural] <내용> /memory /exit  (Ctrl-D 종료)")

    while True:
        if interactive:
            sys.stderr.write("\n» ")
            sys.stderr.flush()
        line = sys.stdin.readline()
        if not line:  # EOF
            break
        line = line.strip()
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        if line.startswith("/remember"):
            kind, note = parse_remember(line[len("/remember"):])
            saved = memory.remember(note, kind)
            _err(f"기억함({kind})." if saved else "이미 기억하고 있거나 내용이 비어 있음.")
            continue
        if line == "/memory":
            _err(memory.read() or "(비어 있음)")
            continue

        # 제목이 없으면 run_task 가 턴 끝에 짓는다(빈약하면 빈 문자열). 2회 실패한 세션은 질의 앞부분을 trace 이름으로 쓴다.
        trace_name = session.title or (" ".join(line.split())[:15] if session.title_tries >= 2 else "")
        # 턴마다 새 renderer — Live 는 턴 종료 시 완전히 정리되고 프롬프트는 context 밖에서 표시되므로 충돌하지 않는다.
        with MarkdownStreamRenderer(use_rich=_use_rich_renderer()) as renderer:
            try:
                result = asyncio.run(_run_with_heartbeat(run_task(
                    line, ws, approver, renderer.on_token, renderer.on_phase,
                    history=session.history, mcp_tools=mcp_tools, mcps_dir=None,
                    run_id=session.id,  # artifact 를 세션 단위로 이어 쓴다
                    trace_name=trace_name,
                ), renderer.on_phase))
            except Exception as e:
                _err(f"턴 실패: {type(e).__name__}: {e}")
                continue
            renderer.finalize(result["final"])
        if not session.title and session.title_tries < 2:
            session.title_tries += 1
            session.title = result.get("title", "")
        verdict = result.get("verdict")
        _err(f"\n--- route: {result['route']}{f' | verdict: {verdict}' if verdict else ''} ---")
        if result.get("empty_final"):
            # 빈 final 은 히스토리에 저장하지 않는다 — 다음 턴 프롬프트·compaction·메모리 추출 입력을 오염시키기 때문.
            _err(f"(빈 응답 — 재시도 권장. 사유: {result.get('empty_reason') or 'unknown'})")
            continue
        session.add_turn(line, result["final"], result["route"], verdict)
        # 메모리는 세션 종료가 아니라 턴마다 판정해 저장한다 (저장할 게 없으면 조용히 지나간다)
        try:
            for kind, note in asyncio.run(remember_from_turn(memory, line, result["final"])):
                _err(f"  + 기억({kind}): {note[:60]}")
        except Exception as e:
            _err(f"  메모리 판정 생략({type(e).__name__})")

        old = session.old_messages() if session.needs_compaction else []
        if old:  # 임계 초과 + 자를 오래된 턴이 있을 때만 (압축 = 캐시 prefix 재구성 1회)
            _err("[문맥 압축 중…]")
            summary, new_title = asyncio.run(summarize_history(old))
            session.apply_compaction(summary)  # 빈 요약·빈 대상은 내부 no-op
            if new_title and not session.title_manual:
                session.title = new_title
        session.save()

    if session.history:
        session.save()
        _err(f"세션 저장됨: {session.id}  (재개: koda chat --resume {session.id})")
    return 0


def _cmd_tui(args) -> int:
    """Textual TUI. lazy import — textual(extra: tui) 미설치 환경에서 run/chat 경로는 영향받지 않는다."""
    try:
        from .tui.app import run_tui
    except ModuleNotFoundError as e:
        if (e.name or "").split(".")[0] == "textual":
            _err('textual 미설치 — TUI extra 로 설치: uv tool install "koda[tui]"  (개발: uv sync --extra tui)')
            return 2
        raise
    if (rc := _preflight_config()) is not None:
        return rc
    return run_tui(args)


def main(argv=None) -> int:
    # chat/tui 및 bare `koda` 가 공유하는 사람용 옵션. top-level 에도 얹어 `koda --yolo` 처럼 서브커맨드 없이 쓸 수 있게 한다.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=".")
    common.add_argument("--yolo", action="store_true", help="위험 도구 자동 승인")
    common.add_argument("--resume", metavar="SESSION_ID", help="저장된 세션 재개")
    common.add_argument("--mcps-dir", default=None,
                        help="프로젝트 MCP 설정 폴더 (기본: <workspace>/mcps). 전역 ~/.koda/mcps 는 항상 함께 읽는다")
    common.add_argument("--no-mcp", action="store_true", help="외부 MCP 로드 생략(로컬만, 빠름)")

    # 서브커맨드 없이 `koda [옵션]` 만 실행하면 TUI 로 진입한다 (사람용 기본 진입점).
    p = argparse.ArgumentParser(prog="koda", parents=[common],
                                description="로컬 LLM 기반 coding-agent koda (분기 멀티에이전트)")
    p.set_defaults(func=_cmd_tui)
    sub = p.add_subparsers(dest="cmd")

    pr = sub.add_parser("run", help="작업 실행 (답변 스트리밍, 단계 표시, langfuse 추적)")
    pr.add_argument("task")
    pr.add_argument("--workspace", default=".")
    pr.add_argument("--yolo", action="store_true", help="위험 도구 자동 승인")
    pr.add_argument("--mcps-dir", default=None,
                    help="프로젝트 MCP 설정 폴더 (기본: <workspace>/mcps). 전역 ~/.koda/mcps 는 항상 함께 읽는다")
    pr.add_argument("--no-mcp", action="store_true", help="외부 MCP 로드 생략(로컬만, 빠름)")
    pr.set_defaults(func=_cmd_run)

    pc = sub.add_parser("chat", parents=[common], help="멀티턴 대화 REPL (세션 저장/재개, 영구 메모리)")
    pc.set_defaults(func=_cmd_chat)

    pt = sub.add_parser("tui", parents=[common], help='Textual TUI 조종석 (사람용 · extra 필요: uv tool install "koda[tui]")')
    pt.set_defaults(func=_cmd_tui)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    finally:
        close_mcp()   # 실행기 스레드가 살아 있을 때 세션(stdio 서버 프로세스)을 닫는다
