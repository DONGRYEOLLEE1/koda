# 화면 증거

[English](visual.md) · **한국어** · [README](../README.ko.md) 로 돌아가기

koda 는 코드를 고치는 동안 화면을 증거로 쓴다. coder 가 페이지를 headless 로 렌더하고, 캡처나 렌더를 비전 모델로 파싱하고, 두 화면을 코드로 비교한다. verifier 는 coder 의 주장보다 이 결과를 먼저 본다. GUI 를 조작하는 에이전트는 없다. 도구 세 개는 워크스페이스를 읽고 `.koda/renders/` 아래에만 쓴다.

[시나리오](#시나리오) · [도구](#도구) · [파일](#파일) · [설치](#설치) · [설정](#설정) · [한계](#한계)

## 시나리오

요청은 평소처럼 TUI 나 `koda chat` 에 넣는다. 코드를 고치는 요청이라 라우터가 편집 경로로 보내고, "이 캡처에서 뭐가 깨졌어?" 같은 분석만 하는 질문은 읽기 경로로 간다. 읽기 경로에도 `parse_screen` 이 있다.

| | 요청 예시 | coder 가 하는 일 |
|---|---|---|
| 스크린샷 기반 버그 수정 | "bug.png 캡처처럼 제목이 메뉴랑 겹쳐 보여. 원인 찾아서 고치고 다시 렌더해서 확인해줘" | `parse_screen(bug.png)` 으로 요소와 겹침 이슈를 얻는다. 요소의 글자로 소스를 찾아 고친 뒤 `render_page` 와 렌더의 `parse_screen` 으로 이슈가 사라졌는지 확인한다. |
| 화면을 그대로 두는 리팩토링 | "카드 4개가 복붙돼 있어. 데이터 배열과 템플릿 함수 하나로 뽑아서 렌더하게 리팩토링해줘. 화면은 지금과 똑같아야 해" | 편집 전에 `render_page(..., name="before")`, 편집 뒤에 `render_page(..., name="after")`, 그리고 `screen_diff` 가 `동등` 이어야 한다. 첫 편집 뒤에 찍은 기준은 인정되지 않는다. |
| 시안 대비 구현 차이 | "design.png 시안대로 맞춰줘. 빠진 섹션이랑 글자 다른 데 고쳐" | `parse_screen(design.png)` 으로 시안 크기를 읽고 그 viewport 로 렌더한 뒤 `screen_diff(design.png, 렌더)` 로 누락 요소와 글자 불일치 목록을 얻는다. 남은 차이는 숨기지 않고 답변에 적는다. |

verifier 가 이 증거를 읽는 방식: [architecture.ko.md](architecture.ko.md#편집을-판정하는-방식).

## 도구

세 도구 모두 승인 게이트 없는 읽기 도구이고 `analyze_image` 처럼 모든 에이전트가 받는다. planner 는 `parse_screen` 만 받는다. 오류는 예외 대신 `오류:` 로 시작하는 문자열로 돌아오고, verifier 는 그런 호출을 없었던 것으로 본다.

### `render_page(target, viewport="1280x800", name="", full_page=False)`

- `target` 은 워크스페이스 안 HTML 파일(`file://` 로 연다)이거나 `http://localhost`, `http://127.0.0.1` 로 시작하는 URL 이다. 그 밖은 거부한다.
- headless chromium, device scale factor 1, 애니메이션 비활성, 커서 숨김, `Date` 와 `Math.random` 고정. 그래서 같은 소스를 두 번 렌더하면 바이트까지 같다. `viewport` 는 `WxH` 로 각 변 200~2000, 면적 2.4MP 이하다. `full_page=True` 면 문서 전체를 4000px 높이까지 찍는다. 5MB 를 넘는 PNG 는 지우고 오류를 낸다.
- 네트워크: 대상이 파일이면 워크스페이스 아래 `file:` 요청(`.koda/` 는 `.koda/renders/` 만)과 `data:` URL 만 통과하고, localhost URL 이면 같은 origin 만 통과하며 리다이렉트도 같은 규칙으로 본다. 나머지는 모두 끊고 차단 건수로 센다. 팝업은 닫고, service worker 와 WebSocket 은 연결되지 않는다.
- 산출물: `<name>.png`, `<name>.dom.json`(보이는 DOM 박스의 타입·글자·픽셀 bbox), `<name>.meta.json`. `name` 을 비우면 대상 경로에서 `/` 를 `_` 로 바꾼 값(`site_index`)이고 URL 은 `host_port` 다. 같은 이름으로 다시 렌더하면 파일을 덮어쓰고 이전 파싱을 지운다.
- 첫 줄은 `렌더 완료 · .koda/renders/<name>.png · 1280x800 · 콘솔 에러 N건 · 외부 차단 M건 · DOM 요소 K개`, 그 뒤 파일 URL, DOM 파일 경로, 콘솔 에러 최대 10줄이다.

### `parse_screen(path, focus="")`

- `path` 는 워크스페이스 안 이미지(`png`, `jpg`, `jpeg`, `webp`, 5MB 이하)이거나 `.koda/renders/` 의 렌더다.
- 비전 모델은 장면 요약과 최대 80개 요소를 16종 타입(`BUTTON`, `LINK`, `TEXT`, `HEADING`, `INPUT`, `CHECKBOX`, `RADIO`, `SWITCH`, `DROPDOWN`, `IMAGE`, `ICON`, `CARD`, `LIST_ITEM`, `NAV`, `DIALOG`, `OTHER`)으로 내고, 글자는 보이는 그대로, bbox 는 0~1000 정규화 좌표다. id 매기기, 박스 자르기, 이슈 계산(잎 요소끼리의 `overlap`, `out_of_bounds`)은 코드가 한다. 모델에게 이슈를 묻지 않는다.
- 결과는 `.koda/renders/<name>.parse.json` 에 저장되고 같은 바이트의 이미지를 다시 파싱하면 재사용한다. `focus` 를 주면 그 영역이나 주제를 빠짐없이 적게 하고 캐시를 건너뛴다.
- 첫 줄은 `요소 N개 · 이슈 M개 · 크기 WxH · 저장 .koda/renders/<name>.parse.json`, 그 뒤 장면, 요소마다 한 줄(`e1 HEADING "오늘의 추천" [40,120,420,180]`), 이슈 목록이다.

### `screen_diff(before, after, tolerance=0)`

- 입력은 렌더 PNG, 워크스페이스 이미지, `.parse.json` 경로다.
- PNG 바이트가 같으면 바로 `동등 · 픽셀 동일` 로 끝난다. 둘 다 DOM 박스가 있는 렌더면 픽셀 단위로 비교한다(`DOM 비교`, 기본 허용 2px). 하나라도 워크스페이스 이미지면 둘 다 비전 모델로 파싱해 정규화 좌표에서 비교한다(`VLM 비교`, 기본 허용 30). 이때 두 이미지 크기가 같아야 한다.
- DOM 비교는 타입과 글자로 먼저 짝짓고 남은 것은 거리로 짝짓는다. VLM 비교는 정규화한 글자만으로 먼저 짝짓고 남은 것은 거리로 짝지으며, 짝의 타입이 다르면 `type_changed` 로, 글자 없는 컨테이너가 한쪽에만 있으면 `container_diff` 로 따로 센다. 둘 다 `equal` 에는 들어가지 않고 둘째 줄에 수만 적힌다(`참고: type 변경 t · 컨테이너 차이 c`). 결과는 `missing`, `added`, `text_changed`, `moved` 목록이고, 넷이 모두 비어야 `equal` 이다. 첫 줄이 결론이다: `동등 · DOM 비교 · 요소 N/N 일치 · 이동 0 · 글자 변경 0 (허용 2px)` 또는 `차이 K건 · VLM 비교 · 누락 a · 추가 b · 글자 변경 c · 이동 d (허용 30)`.
- 워크스페이스가 바뀌지 않은 두 렌더에는 `소스 동일(리팩토링 증거 아님)` 표시가 붙는다. koda 밖에서 바뀐 `.koda/renders/` 파일은 거부하고, 이전 실행이 남긴 파일은 참고 표시와 함께 받는다.
- `.koda/renders/<before>__<after>.diff.json` 에 저장된다.

## 파일

모두 `<workspace>/.koda/renders/` 아래다: `<name>.png`, `<name>.dom.json`, `<name>.meta.json`, `<name>.parse.json`(워크스페이스 이미지는 `src__<경로>.parse.json`), `<before>__<after>.diff.json`. `.koda` 는 verifier 가 보는 워크스페이스 diff 에서 빠지고, `read_file` 로는 이 파일들을 열 수 없다. 에이전트는 도구가 돌려준 경로를 다음 도구에 그대로 넘긴다.

## 설치

`visual` extra(Playwright)와 chromium 이 필요하다:

```sh
uv sync --extra tui --extra visual
.venv/bin/python -m playwright install chromium
```

없으면 `render_page` 가 이 두 명령을 적은 오류를 돌려주고, `parse_screen` 과 `screen_diff` 는 이미지에 대해 그대로 동작한다. uv tool 로 설치했다면 extras 에 `visual` 을 더하고(`"koda[tui,visual] @ git+https://github.com/DONGRYEOLLEE1/koda"`) Playwright 가 든 인터프리터로 `python -m playwright install chromium` 을 실행한다.

## 설정

이미지 호출(`parse_screen`, `analyze_image`)은 따로 비전 모델을 두지 않으면 fast 모델(생각을 끈 주 모델)이 받는다:

| 변수 | 기본값 | 의미 |
|---|---|---|
| `VISION_MODEL` | 비움 | 이미지 호출용 모델. 비우면 fast 모델(생각을 끈 주 모델)이 받는다. |
| `VISION_BASE_URL` | 비움 | 그 모델의 엔드포인트. 비우면 실제로 쓰는 provider 주소를 쓴다. llama.cpp 비전 지원을 `/props` 로 확인하는 주소이기도 하다. |
| `VISION_API_KEY` | 비움 | 그 엔드포인트의 키. 비우면 실제로 쓰는 provider 키를 쓴다. |
| `VISION_STRUCTURED_METHOD` | 비움 | 비전 모델만의 구조화 출력 방식(`json_schema`, `function_calling`, `auto`). 비우면 `STRUCTURED_METHOD` 를 따른다. 여기서 난 실패가 텍스트 호출의 방식을 바꾸지는 않는다. |

로컬 llama.cpp 서버(Qwen3.6-35B-A3B + mmproj)에서 1280x800 한 장 파싱에 약 43초가 걸렸고 요소 21개와 정확한 한국어 글자가 나왔다.

## 한계

- 색과 폰트는 비교하지 않는다. 비교는 요소 타입, 글자, 위치만 본다.
- `overlap` 이슈는 잎 요소 두 개가 부딪힐 때 난다(IoU 0.2 이상 또는 작은 쪽의 30% 이상이 덮임). 잎 요소끼리는 완전히 품는 경우도 겹침으로 센다. 메뉴 링크를 통째로 덮는 제목이 바로 찾으려는 결함이기 때문이다. 90% 이상 포함을 중첩으로 보는 예외는 중첩이 정상인 경우에만 적용한다. NAV·DIALOG 안의 잎 요소, 다른 잎 요소를 품는 ICON·INPUT, 같은 글자가 두 번 보고된 경우다.
- localhost 서버가 문서를 HTML content-type 없이(없음, `text/plain`, `application/octet-stream`) 보내도 HTML 로 간주해 렌더하고, 원래 값을 경고 줄로 알린다. 파일 경로를 직접 넘기면 이 문제가 없다.
- 비전 모델의 좌표에는 오차가 있다. VLM 비교는 `이동` 과 type 변경도 보고하지만 verifier 는 거기서 누락과 글자 변경만 믿는다. 렌더끼리의 DOM 비교는 정확하다.
- 모델이 픽셀로 답하면 1000 을 넘는 좌표가 하나라도 있을 때 그 박스를 픽셀로 읽는다. 픽셀로도 1000 을 넘지 않는 작은 박스는 정규화 좌표로 읽혀 엉뚱한 자리에 놓인다.
- 렌더는 외부 네트워크 요청을 모두 막는다. CDN 에서 CSS·폰트·스크립트를 받는 페이지는 그것 없이 렌더되므로, 그런 파일은 워크스페이스 안으로 vendoring 한다.
- 픽셀이 같으면 동등의 증거지만, 픽셀이 다르다고 레이아웃이 다른 것은 아니다. 비교는 `screen_diff` 로 한다.
- viewport 보다 긴 시안은 `full_page` 가 아니라 viewport 높이를 시안 높이로 맞춰 렌더한다. 그래야 두 크기가 같다.
- TUI 는 도구 줄(`렌더 · ...`, `화면 분석 · ...`, `화면 비교 · ...`)만 보여 주고 이미지는 표시하지 않는다.
- 워크스페이스 밖 이미지는 읽지 않는다. 먼저 워크스페이스 안으로 복사한다.
