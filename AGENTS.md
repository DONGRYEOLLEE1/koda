# build-harness 에이전트 지침 (Instruction Layer)

하네스의 LLM 판단 계층이 따라야 하는 영구 규칙. (PLAN §3.1-1, 패턴 1)

## 판단 위임
- 요청에서 파생되는 모든 판단(계획, 도구 선택, 권한 위험도, 검증)은 LLM이 내린다.
- 규칙 코드는 집행·검증만 한다.

## 작업/코드 원칙
- 요청을 만족하는 최소 변경을 계획한다. 불필요한 파일 생성·추상화를 피한다.
- 테스트는 변경된 동작을 보호할 때만 계획한다. 커버리지용 남발 금지.

## 안전 (HITL)
- 삭제·되돌릴 수 없는 변경·외부 시스템 변경은 `permission.decision`과
  `requires_user_authorization`을 반드시 표시한다.
- 위험 작업을 `allow`로만 표시하면 하네스가 `invalid_permission_decision`으로 차단한다.

## 판단 출력 형식
- explore: `{intent, actions:[{tool,args}]}`
- plan: `{planned_actions:[{tool,args,permission:{decision,requires_user_authorization,reason}}], verification_plan:[{tool,args}]}`
- verify: `{verdict:"pass"|"fail", rationale}`
