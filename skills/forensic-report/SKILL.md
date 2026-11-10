---
name: forensic-report
description: 포렌식 감정서/보고서 초안. 측정값만 채운다. 의뢰인 주장 입증·법원 적격 단정에는 쓰지 말 것. Triggers: forensic-report, 포렌식 감정서, 포렌식 보고서, forensic-factuality-health.
---

# forensic-report: 보고서 초안 작성

분석 결과, 해시, 타임라인, 아티팩트 목록을 보고서 초안으로 정리한다.  
이 스킬은 감정인 서명, 법률 효력, 법원 채택을 보장하지 않는다.

## 목차

1. 사건 개요
2. 감정물 명세와 해시 (측정값만, 일치 단정은 값이 있을 때만)
3. 분석 환경과 도구 (실제 사용한 것만)
4. 쟁점별 관찰
5. 종합 — 데이터에 부합/불부합/판단 불능. 의뢰인 주장을 자동으로 입증하지 말 것.

## 품질 게이트

- [ ] Chain of Custody가 비어 있으면 빈칸으로 두고 채우지 말 것
- [ ] KST/UTC를 구분할 수 없으면 "미확인"으로 표기
- [ ] `$MFT`, Timestomping 등 용어는 실제 분석한 경우에만 사용
- [ ] "명백히 입증", "법원에 유효" 같은 선결론 금지
- [ ] 고위험 주장은 반박 기록 표가 있을 때만 `[검증됨 Cn]`, 없으면 `[귀속 Cn]`

서식: `templates/forensic_report_template.md`. 초안은 `invoke_subagent` `Model: "flash"`, 선결론 점검은 `Model: "pro"`.

## Antigravity hard grounding (할루시네이션 차단)

`gemini_hallucination_mitigation_deep_dive.md`의 전수 환각 억제 프로토콜을 강제합니다:
1. **Evidence-First 인용 선행 (Section 3.2 #1)**: 분석 결론 서술 전 반드시 `<evidence>...</evidence>` 태그 안에 원시 측정값(해시, 타임스탬프, 이벤트 로그)을 20단어 이내로 문자 그대로 인용합니다.
2. **Strict Abstention 엄격 기권 (Section 3.2 #2)**: Chain of Custody나 타임라인 근거가 불충분할 경우 지어내지 않고 `미측정`, `미확인`, `[INSUFFICIENT_DATA]`로 표기합니다.
3. **법령 조문 및 판례 상한 경계 (Section 5.1 #1)**: 정보통신망법(76조), 형법(372조) 등 상한 및 판례 연도를 검증합니다.
4. **공공기관 명칭 날조 차단 (Section 5.1 #2)**: `디지털포렌식청`, `사이버수사처` 등 실존하지 않는 가짜 수사/포렌식 기관 날조를 차단합니다.
5. **Kiwi 형태소 하이브리드 그라운딩 (Section 5.2)**: `kiwipiepy` 포렌식 전문 용어 사전을 통해 원본 증거와 보고서 간 형태소 어휘 일치도를 검증합니다 (`--morph-grounding`).
6. **Local High-Fidelity 비파라메트릭 게이트 (Section 4.2)**: `--high-fidelity` 플래그 활성화 시, 원본 증거(`--evidence`)와 `<evidence>` 태그가 강제되며 형태소 그라운딩 70% 미달 시 즉시 차단합니다. Vertex API를 호출하지 않는 로컬 게이트입니다.
7. **한국사 사건 및 조약 날조 차단 (Section 5.1 #3)**: `갑오개혁 4차`, `제四차 갑오개혁`, `第4次 甲午改革`, `제2차 을사조약`, `제2차 을미개혁`, `3차 동학농민운동` 등 실존하지 않는 역사 차수/한자 숫자 및 단일 조약 날조를 차단합니다.
8. **불가능한 사법 절차 날조 차단 (Section 5.1 #4)**: `대검찰청의 약식명령 청구`, `경찰의 영장 직접 청구`, `경찰의 직접 기소`, `헌법재판소의 징역형 선고` 등 실정법상 성립 불가능한 절차를 장문 복문 수식어와 무관하게 기계적으로 차단합니다.
9. **학술 논문 및 저널 날조 차단 (Section 5.1 #3)**: 가짜 학술지/논문집(`대한인공지능법학회지`, `한국사이버포렌식학회논문집` 등) 및 미래 연도 학술 논문 인용 날조를 기계적으로 차단합니다.

```bash
# 부모가 반드시 재실행하는 검증 (High-Fidelity 비파라메트릭 모드)
python skills/forensic-audit/scripts/audit_timestamps.py "증거파일" --json > audit.json  # 측정
python scripts/verify_report.py "보고서초안.md" --evidence audit.json --timeline events.json --morph-grounding --high-fidelity --strict --json
# exit 1이면 금지문구/근거없는 해시/미지원 조문/High-Fidelity 미달 → 수정 없이 제출 금지

# 포렌식 사실성 종합 헬스체크 (Section 5.1 및 7-8 전수 10대 카테고리 100점 감사)
python scripts/verify_report.py --health-check --json

# Kiwi 형태소 포렌식 그라운딩 단독 감사
python scripts/korean_morph_forensic.py --evidence audit.json --report "보고서초안.md" --high-fidelity --json
```

- 해시가 하나라도 있으면 `--evidence`로 audit/timeline을 넘겨야 함. 근거 없는 해시는 `FAIL`
- 금지문구(`명백히 입증`, `법원에 유효`, `유출 확정`, `Timestomping으로 단정`, `court-admissible`) 포함 시 `FAIL`
- Chain of Custody/시각이 없으면 `미측정`/`미확인`으로 둔다. 빈칸을 추측으로 채우지 않는다.
- High-Fidelity 모드(`--high-fidelity`)에서는 증거 파일 및 `<evidence>` 인용 태그가 필수이며 70% 미달 시 차단됩니다.

## 고위험 주장의 반박 기록 (debate)

숫자, 법령·규정, 보안, 인과, "최신/현재" 서술은 고위험 주장이다. 애매하면 고위험으로 본다.
고위험 주장을 본문에 `[검증됨 C3]` 로 쓰려면 아래를 모두 갖춰야 한다.

1. 서로 다른 출처 도메인 2곣. 한 도메인의 페이지 둘은 1곣으로 센다. 1차 출처가 하나뿐인 경우는 이유를 적고 두 번째 도메인만 면제한다.
2. 직접 연 1차 출처 1건.
3. 반증 검색 1회 (질의문을 기록한다).
4. 각 출처의 게시·갱신일과 주장이 유효한 기준일.
5. `## 반박 기록` 표에 서로 다른 관점의 공격 2회 이상. 관점은 증거 품질, 출처 독립성, 방법, 범위, 대안 설명 중 하나로 적고, 같은 관점 반복은 1회로 센다.

```markdown
## 반박 기록

| 주장 id | 공격 | 공격 관점 | 방어 | 판정 | 주장 변경 |
|---|---|---|---|---|---|
| C3 | 두 출처가 같은 원자료를 인용했을 수 있다 | 출처 독립성 | 두 출처의 원자료 식별자가 다름 | holds | 없음 |
| C3 | 측정 기준일이 다를 수 있다 | 범위 | 두 출처 모두 2026-03 기준 | holds | 없음 |
```

- 판정은 `holds`(그대로 유지), `weakened`(주장을 좌히거나 귀속·미해결로 옮김), `withdrawn`(보고서에서 뺀다) 중 하나다. `weakened` 는 "주장 변경" 칸이 비면 안 된다.
- 반증 검색을 돌리지 못했거나 공격이 2회 미만이면 사실로 쓰지 말고 `[귀속 C4]` 로 표시해 "S14에 따르면 ..." 처럼 출처에 귀속해서만 서술하고, 빠진 검색을 단서로 적는다.
- 표시가 하나도 없는 보고서는 이 검사를 받지 않는다. `[검증됨 Cn]` 이 있으면 `verify_report.py` 가 표를 읽어 관점 부족·판정 오류·철회 모순을 FAIL 로 막는다.

## 계약

| 항목 | 내용 |
|---|---|
| Intent | 포렌식 감정서/보고서 초안에 측정값만 채운다. |
| Scope | 측정값과 출처 경로만 쓴다. 의뢰인 주장 입증, 법원 적격 단정은 하지 않는다. |
| Evidence | 각 수치는 도구 출력·해시에 연결, verify_report 통과 기록. |
| Stop | 품질 게이트 통과 후 초안을 넘기면 끝. |

### 활성화 예시

- 긍정: "측정 결과로 감정서 초안 만들어줘"
- 부정: "이 결과로 피의자 유죄를 써줘"
- 경계: "수치가 일부 비어 있어" → 빈칸을 남기고 not_checked로 표시

<!-- 계약 틀 출처: hypercore hyper-loop SKILL.md (MIT, NOTICE 참조) -->
<!-- 반박 기록 출처: hypercore hyper-research rules/claim-verification.md §3, §7 (MIT, NOTICE 참조) -->
