---
name: forensic-timeline
description: 이벤트 JSON을 HTML 타임라인으로 그릴 때. --input 필수. $MFT/이벤트로그 수집·샘플 유출 사건 생성에는 쓰지 말 것.
---

# forensic-timeline: JSON → HTML 타임라인

이 스킬은 **이미 정규화된 이벤트 JSON**을 HTML로 시각화한다.  
PC 아티팩트, `$MFT`, 이벤트 로그, DLP를 자동 수집하거나 합성하지 않는다.

## 입력 스키마

JSON 배열. 각 객체:

- `timestamp` (문자열, 그대로 표시. 타임존 변환 없음)
- `category`: `critical` | `file` | `web` | `chat` | `system`
- `description`
- `details` (선택)

`--input` 없이 실행하면 종료 코드 2. 샘플 USB/카카오 유출 사건을 만들지 않는다.
모든 시각이 ISO 형식이고 타임존 표기가 일관되면 오름차순 정렬한다. 누락·혼합·형식 불명 시 입력 순서를 유지하고 HTML에 그 사실을 표시한다.
`critical`은 중립적인 "주의" 라벨이며 유출 확정 의미가 아니다.

## 실행

```bash
python skills/forensic-timeline/scripts/generate_timeline.py --input events.json --output timeline.html --title "사건 타임라인"
```

## 한계

- HTML 출력은 입력 JSON의 시각화일 뿐이다.
- XSS 방지를 위해 필드 값을 escape 한다.
- 법원 제출 적격성, 무결성, 원본 아티팩트 대체 주장을 하지 않는다.

## Antigravity

호스트 `Read`/`Write`만 쓴다. 파싱은 `invoke_subagent` `Model: "flash"`, HTML 검증은 `Model: "pro"`(세션 모델과 무관, Claude 할당량 절약). 도구 모양은 `../../docs/antigravity-tools.md`.

## 데이터 질문 답 보고 틀

"그 시간대에 몇 건이 있냐" 같은 집계 질문을 이벤트 JSON에 대고 답할 때는 질문이 묻는 것만 답한다. 보너스 차트·추가 분류는 질문이 요구할 때만 넣는다. 데이터로 답할 수 없는 질문("왜 그런 일이")은 계산한 변화를 먼저 쓰고 원인은 검증되지 않은 가설로 뒤에 둔다.

보고 순서는 답, 질문(지표·단위·모집단·필터·기간과 시간대), 실제로 돌린 질의 원문, 행 수(읽은 행/결과 행), null·중복 처리, 입력 파일 SHA-256과 소요 시간, 대표 수치의 두 번째 경로 교차검증이다. 측정하지 않은 행 수·시간은 "미측정"이라고 적고, 두 번째 경로가 없으면 일치한다고 쓰지 않는다.

```markdown
## 답

2026-03-11 09:00~12:00 KST 이벤트 17건

## 질문

지표: 이벤트 건수 / 단위: 건 / 모집단: events.json 전체 / 필터: 없음 / 기간: 위 시간대(KST)

## 질의 원문

(실제로 돌린 질의 또는 스크립트 인자)

## 행 수와 처리

읽은 행 120 / 결과 행 17 / 널·중복 처리: 시각 널 3건 제외, 중복 id 0건

## 입력과 소요 시간

events.json (SHA-256 ...), 미측정

## 교차검증

원본 대화 내보내기의 행 수와 대조: 일치 / 차이 N건 / 미실시

Result: answered
```

- `Result:` 줄은 하나만 쓰고 `answered`(답과 행 수·입력 해시를 적음), `prerequisite_missing`(필요한 도구가 없음 — 무엇이 없는지만 적고 설치하지 않음), `awaiting_input`(답이 달라지는 질문 하나가 남음: 시간대 경계, 두 파일 중 정본), `blocked`(읽을 수 없거나 접근 불가) 중 하나다.
- 같은 질문 안에서 질의를 다시 쓰는 것은 2회까지다. 세 번째도 실패하면 실패한 질의와 오류를 적고 `blocked` 로 끝낸다.

## 계약

| 항목 | 내용 |
|---|---|
| Intent | 정규화된 이벤트 JSON을 HTML 타임라인으로 그린다. |
| Scope | --input 필수. 아티팩트 수집이나 샘플 사건 생성은 하지 않는다. |
| Evidence | 입력 JSON 경로와 생성된 HTML. 필드는 escape된다. |
| Stop | HTML을 만들고 한계를 안내하면 끝. --input 없으면 exit 2. |

### 활성화 예시

- 긍정: "events.json으로 타임라인 그려줘"
- 부정: "카카오 유출 샘플 사건 만들어서 그려줘"
- 경계: "시각 형식이 섞여 있어" → 입력 순서를 유지하고 HTML에 그 사실을 표시

<!-- 계약 틀 출처: hypercore hyper-loop SKILL.md (MIT, NOTICE 참조) -->
<!-- 데이터 질문 답 보고 틀 출처: hypercore hyper-data rules/query-evidence.md (MIT, NOTICE 참조) -->
