---
name: report-guard
description: 보고서/문서 할루시네이션 무조건 검증 — 금지문구/근거없는 해시 차단. Triggers: 보고서, 검토해줘, 검증해줘, 검증, 할루시네이션, 할루체크, 팩트체크, 거짓말검사, 사실확인, 무결성검사, verify
---

# report-guard: 무조건 검증 게이트

채팅에 아래 **어느 하나라도** 포함되면 호스트 `PostToolUse` + 이 스킬이 **무조건** `verify_report.py`를 실행한다. 이것은 **사후 게이트**다 — 파일이 이미 쓰인 뒤 검증하며, LLM이 스킵해도 훅이 동일 검증을 재실행한다. 실제 쓰기 차단은 호스트가 `failurePolicy: FAIL_CLOSED`(exit 1)을 지원할 때 작동하고, 파이썬이 없거나 대상을 특정할 수 없으면 경고 후 통과한다. 스킵 경로는 `docs/GAPS.md`에 기록되어 있다.

**트리거 (모두 동일 게이트)**:
`보고서`, `검토해줘`, `검증해줘`, `검증`, `할루시네이션`, `할루체크`, `팩트체크`, `거짓말검사`, `사실확인`, `무결성검사`, `verify`, `/verify`, `/할루체크`, `/검증`

## 동작

1. 대상 보고서(`.md`/`.html`)를 찾는다 — 가장 최근 `Write` 파일 또는 명시 경로
2. 아래를 무조건 검사:
   - 금지문구: `명백히 입증`, `법원에 유효`, `유출 확정`, `court-admissible`, `Timestomping으로 단정`
   - 해시 grounding: 보고서 내 64자 해시가 `--evidence` (audit.json/timeline.json) 에 없으면 FAIL
   - 공공기관/법원 명칭: 폐지된 구 부처명, `디지털포렌식청`, `사이버수사처` 등 가짜 기관명 차단
   - 역사적 사건/조약 차수 날조: `갑오개혁 4차`, `제2차 을사조약`, `3차 동학농민운동` 등 차단
   - 불가능한 사법 절차 날조: `대검찰청의 약식명령 청구`, `경찰의 영장 직접 청구`, `헌법재판소의 징역형 선고` 등 차단
   - 빈칸 추측: `미확인/미측정`을 추측으로 채웠는지
   - 숨은 문자: 해시 한가운데 끼인 제로폭 문자는 FAIL, bidi 제어·제로폭·태그 문자와 모델을 향한 지시문 형태(`ignore previous` 등)는 WARN (`scripts/hidden_char_scan.py`)
   - 반박 기록: `[검증됨 Cn]` 표시가 있으면 `## 반박 기록` 표에 서로 다른 관점의 공격 2회 이상, 유효한 판정, 철회 모순 없음을 확인. `[귀속 Cn]` 은 출처 귀속 서술이 없으면 WARN (`scripts/debate_gate.py`)
3. `FAIL`이면 파일 유지하되 에러를 띄우고 **제출 금지**. `PASS/WARN`이면 통과 (WARN은 부모가 pro로 재대조)

## 실행 (스킬이 직접 하는 일 — 호스트도 동일하게 자동 실행)

```bash
# 스킬이 invoke_subagent Model: "pro"로 실행하는 검증
python scripts/verify_report.py "보고서초안.md" --evidence audit.json --timeline events.json --json
# 슬래시/채팅 어느 쪽이든 동일
python scripts/verify_report.py "보고서초안.md" --evidence audit.json --json
```

## Antigravity / Gemini

- 이 스킬 자체는 `Model: "pro"` 로 검증을 수행한다
- 동시에 호스트 `hallucination_guard.mjs`가 `PostToolUse`에서 동일 검증을 **자동 재실행**하므로 LLM이 이 스킬을 안 불러도 차단된다
- `보고서` 또는 `검토해줘`가 포함된 모든 쓰기는 이 게이트를 통과해야 한다 — `docs/GAPS.md` 위반 시 차단

## 슬래시 명령어

```
/verify <보고서경로> [--evidence audit.json]
/할루체크 <보고서경로>
/검증 <보고서경로>
검증해줘: <보고서경로>
보고서 검증해줘: <보고서경로>
팩트체크해줘: <보고서경로>
```

어느 형태로 불러도 동일 검증 (`verify_report.py`)이 돈다.

## 보정 패스 상한 (2회)

FAIL 이 나온 뒤 고치고 다시 검증하는 보정은 기록된 2회까지다. 매 검증 결과를 실행별 상태 파일에 쌓는다.

```bash
python scripts/verify_report.py "보고서초안.md" --evidence audit.json --json > verify-0.json
python scripts/repair_state.py record --state .lazyforensic/run1/report-state.json --result verify-0.json
# 고친 뒤 verify-1.json, verify-2.json 도 같은 방식으로 record. 인도 전에 finish
python scripts/repair_state.py finish --state .lazyforensic/run1/report-state.json --report "보고서초안.md"
```

- `record` 가 `stop: oscillation`(이전 상태로 되돌아감), `stop: plateau`(실패 목록이 그대로), `stop: limit`(2회 소진) 중 하나를 내면 보정을 멈춘다. 판정 순서는 이 순서다. exit 3.
- 끝까지 실패한 고위험 주장은 문구를 완화해 남기지 말고 보고서에서 빼거나 미해결 절로 옮긴 뒤 다시 검증한다.
- `finish` 는 보고서가 비었거나, 검증 기록이 없거나, FAIL 이 남아 있으면 인도를 거부하고(exit 1), 그렇지 않으면 결과를 `outcome.json` 에 남긴다.
- 실행마다 상태 파일을 따로 둔다. 두 실행이 한 파일을 공유하면 횟수가 섞인다.

## 계약

| 항목 | 내용 |
|---|---|
| Intent | 보고서/문서의 금지 문구와 근거 없는 해시를 무조건 검증한다. |
| Scope | verify_report/verify_claim_ledger를 실행한다. 보고서 내용을 새로 지어내지 않는다. |
| Evidence | 검증기 실행 기록과 종료 코드. 실행하지 않았으면 not_checked. |
| Stop | 검증 결과를 보고하면 끝. FAIL은 숨기지 않는다. |

### 활성화 예시

- 긍정: "이 보고서 검증해줘"
- 부정: "검증 없이 통과시켜줘"
- 경계: "초안이 아직 비었어" → 있는 부분만 검증하고 빈 구간을 알린다

<!-- 계약 틀 출처: hypercore hyper-loop SKILL.md (MIT, NOTICE 참조) -->
<!-- 보정 패스·숨은 문자·반박 기록 출처: hypercore hyper-research SKILL.md loop_policy, hyper-documentation memory/safety.md §4 (MIT, NOTICE 참조) -->
