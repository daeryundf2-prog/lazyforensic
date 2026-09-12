# BYO 외부 도구 카탈로그

레포에 편입하지 않고 **다운받아 쓰는** 도구 목록. 각 항목은 '무엇을·왜·어떻게'만 적는다.
이 문서의 도구를 설치하면 대응 스크립트가 자동으로 활성화된다(fail-closed는 유지).

## 포렌식 직결

| 도구 | 용도 | 설치 | 연동 |
|---|---|---|---|
| **sherlock-project** | 닉네임→482개 사이트 존재 여부 OSINT 스캔 | `pip install sherlock-project` | `scripts/osint_username.py` 자동 탐지. ⚠️ 조회 닉네임이 외부 사이트에 노출 — 의뢰인 동의 필요 |
| **CrisperWhisper** | verbatim STT — 필러음·말더듬·단어 타임스탬프 보존. 증거 녹취에 표준 Whisper보다 적합 | faster-whisper로 HF에서 자동 다운로드 | `local_stt.py --model nyrahealth/CrisperWhisper --verbatim` |
| **transcribe.cpp** | whisper.cpp 확장, 16개 모델 계열 로컬 STT | GitHub 빌드 | `local_stt.py`의 binary 엔진 탐지 |
| **Subtitle Edit** | 전사문 사람 검토·수정 GUI (로컬 전용, MIT) | 릴리스 다운로드 | `local_stt.py` 산출물을 수동 교정할 때 |
| **korean-munseo-diff** | HWP·HWPX·PDF 신구대비표 (공무원 개정 문서용) | `github.com/obundh/korean-munseo-diff` | 바이너리 .hwp는 이걸로 — HWPX는 `extract_hwpx.py`가 커버 |
| **Unlimited-OCR** (Baidu) | 40p+ PDF 통째 OCR | GitHub | 스캔 증거 문서 대량 텍스트화 후 `keyword_report.py` |
| **Upscayl** | 저해상도 이미지 업스케일 (로컬, 오픈소스) | 릴리스 다운로드 | 증거 사진 선명화 — 단, 업스케일본은 파생물로 표시 |
| **claude-video (`/watch`)** | 영상 링크→프레임+전사+AI 분석 | `github.com/bradautomates/claude-video` | 프레임 추출은 `forensic-video`와 겹침 — 영상 '내용 읽기'용 |

## 워크플로 보조

| 도구 | 용도 |
|---|---|
| **dryforge** | bounded-autonomy 에이전트 하네스 — lazyagentic과 설계 철학 유사, 참고용 |
| **Paperthin** | 에이전트 컨텍스트 위생 스킬 (re0/ssotize/debloat) — 문서 팽창 제어 |
| **ARTEMIS** (google) | 안드로이드 자연어 자동화 — 모바일 앱 반복 조작 자동화에 응용 |
| **Letta CLI / Orca** | 장기메모리 LLM / 멀티작업 에이전트 IDE |
| **ego lite** | 에이전트 전용 Chromium — 로그인 필요 사이트 자동화 |

## 문서·보고서 품질

| 도구 | 용도 |
|---|---|
| **bluenyx 한국어 문장 검토 스킬** | 보고서·대본 문체 검토 (9개 문서 유형) |
| **avoid-ai-writing / patina** | AI 문체 습관 제거 — 의뢰인 제출 문서 최종 다듬기 |

## 규칙

- 이 표에 없는 도구를 스크립트가 호출하게 만들 때는 `check_tool.py`식 fail-closed 게이트를 둘 것
- 외부로 데이터가 나가는 도구(sherlock, API 계열)는 `.local_only_allowlist` 검토 대상이다
- 새 도구를 발견하면 이 표에 행을 추가하고 README 능력 표와 동기화할 것
