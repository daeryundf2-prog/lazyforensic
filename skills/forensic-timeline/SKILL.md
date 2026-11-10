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
