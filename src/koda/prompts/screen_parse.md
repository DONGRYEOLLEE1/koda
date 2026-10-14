화면 이미지를 보고 UI 요소를 스키마에 맞게 구조화하라.

- type 은 BUTTON, LINK, TEXT, HEADING, INPUT, CHECKBOX, RADIO, SWITCH, DROPDOWN, IMAGE, ICON, CARD, LIST_ITEM, NAV, DIALOG, OTHER 16종 중 하나만 쓴다. 다른 이름을 만들지 않는다.
- bbox 는 [x0, y0, x1, y1] 이고, 좌표는 이미지 전체를 가로·세로 각각 0~1000 으로 본 정규화 값이다. 픽셀 좌표를 쓰지 않는다. x0 < x1, y0 < y1 이어야 한다.
- 컨테이너(CARD, NAV, DIALOG, LIST_ITEM) 안의 글자 요소(메뉴 항목, 카드 제목·가격·버튼)는 컨테이너와 별도로 각각 따로 낸다. 컨테이너의 text 는 빈 문자열로 두어도 된다.
- 요소는 큰 것부터 작은 것 순으로 최대 80개까지만 낸다.
- text 는 보이는 글자를 그대로 적는다. 줄이거나 번역하거나 고치지 않는다. 글자가 없으면 빈 문자열이다.
- 보이지 않는 것은 추측해서 넣지 않는다. 가려지거나 잘린 요소는 보이는 부분만 적는다.
- summary 는 화면 전체를 한국어 한 문장으로 쓰고, kind 는 page, dialog, form, list, card_grid, error, other 중 하나다.
