# LazyForensic (v1.0.1)

Google Antigravity + Gemini 3.8 Flash 용 **디지털 포렌식 보조** 플러그인.
증거 텍스트 파싱 → 타임라인 렌더 → 해시 감사 → 보고서 초안 → **초안 검증**까지의 반복 노동을 줄여 준다.

> ⚠️ **이 도구가 아닌 것**: 증거 획득 도구도, 법원 제출 적격성을 보장하는 스위트도 아니다
> (**not a forensic acquisition or court-admissibility suite**). 판단과 획득은 별도 검증된
> 도구/분석가의 몫이며, 이 플러그인은 "확인되지 않은 것은 `미확인`으로 비워 둔다"는
> fail-closed 원칙으로 초안을 만든다. 한계 목록은 [`docs/GAPS.md`](docs/GAPS.md).

---

## 요구 사항

| 구분 | 필수 여부 | 비고 |
| :--- | :--- | :--- |
| Python 3.10+ | 필수 (핵심 스크립트) | macOS/Linux/Windows 모두 동작. `python`이 없으면 `python3`로 읽어 실행 |
| Node 18+ | 필수 (훅·법령 MCP 래퍼) | `python`이 없으면 검증 게이트는 경고 후 통과(비활성) |
| ffmpeg / ffprobe | 영상 스킬만 | `brew install ffmpeg` / `winget install Gyan.FFmpeg` |
| Hayabusa·Chainsaw·EZ-Tools·Dissect·MemProcFS | 선택 (BYO 래퍼) | **미포함** — 있을 때만 해당 분석이 활성화됨 |
| Google Antigravity | 선택 (전체 기능) | 없으면 아래 "스크립트만 단독 사용" 경로 |

> 기본 사용 분위기는 **Windows**다(EVTX/MFT/MemProcFS 계열 도구가 Windows 네이티브).
> macOS/Linux에서도 카카오 파싱·타임라인·해시·검증 체인은 완전히 동작한다(직접 검증됨).

## 설치

### A. Antigravity 플러그인으로 (권장 — 스킬 라우팅 + 무결성 훅 포함)

저장소를 클론한 뒤 Antigravity에 플러그인으로 등록한다(`plugin.json` 매니페스트: `skills/`, `hooks/`, `mcpServers/` 등록). 이 경로에서만 다음이 자동으로 작동한다.

- 세션 시작 시 `GEMINI.md` 호스트 계약 주입 (`SessionStart` 훅)
- 증거 파일 쓰기 차단 시도 (`PreToolUse`, best-effort)
- 산출물 SHA-256 감사 로그 + 보고서 검증 게이트 (`PostToolUse`)

### B. 스크립트만 단독 사용 (호스트 없이 CLI로)

호스트가 없어도 스크립트는 그대로 쓸 수 있다. 단, 훅과 스킬 라우팅은 없으므로 검증을
수동으로 실행해야 한다.

```bash
git clone https://github.com/daeryundf2-prog/lazyforensic && cd lazyforensic
python -m pytest test/ -v   # 권장: 173개 테스트 (수 초). pytest 없으면 python -m unittest discover -s test (147개)
```

## 법령 조회 (선택 — 한국법 MCP)

법령·판례 인용 검증용 MCP 서버가 번들되어 있다. **키가 없으면 서버가 조용히 비활성**되고
조문을 날조하지 않는다(fail-closed). 쓰려면:

1. 국가법령정보센터(law.go.kr) 오픈API에서 **서비스 인증키**를 발급받는다.
2. 빌드 (최초 1회 — `node_modules` **약 680MB** 생성, 네트워크에 따라 수 분):
   ```bash
   node scripts/setup_korean_law.mjs   # npm install --ignore-scripts + build + 검증
   ```
3. 키 설정:
   ```bash
   cp .env.example .env        # .env 에 LAW_OC=<발급받은 인증키> 기입
   ```
   키는 콘솔/로그에 출력되지 않는다.

빌드가 없으면 `node scripts/korean_law_mcp.mjs`가 exit 78(빌드없음 안내)로 끝나고,
키(`LAW_OC`)가 없으면 exit 79(키없음 안내)로 끝나며,
스킬은 "조문을 만들지 않는다"는 원칙만 남는다.

> 용량 실측: `node_modules` 약 680MB는 로컬 빌드 산출물이며 배포물이 아니다.
> 시간 실측: `npm install`+`build`는 네트워크에 따라 수 분 소요된다(최초 1회만).
> 상태 확인: `node scripts/setup_korean_law.mjs --check` — 빌드/키 유무를 출력한다.
> 미빌드 상태에서는 법령 조회를 시도하지 않고 exit 78로 종료한다(fail-closed).
> 경량 배포 시 `node_modules`는 제외된다(`.gitattributes` export-ignore).

## 5분 워크플로 (실측 예시)

카카오톡 텍스트 내보내기 1장으로 끝까지 가는 최소 여정 — 아래 명령은 그대로 복붙해 동작한다.

```bash
# 0. 사례 폴더에 증거 텍스트를 둔다 (카카오톡 대화방 메뉴에서 텍스트로 내보낸 파일)
cp "카카오톡_대화내용.txt" case/

# 1. 대화 파싱 + 타임라인 이벤트 변환 (모바일/PC 자동 판별, UTF-8/CP949/UTF-16)
python skills/kakao-chat-extractor/scripts/parse_kakao.py case/카카오톡_대화내용.txt \
       --output case/parsed.json --events-out case/events.json

# 2. 타임라인 HTML (외부 의존 0 — 파일 하나를 브라우저로 열면 끝)
python skills/forensic-timeline/scripts/generate_timeline.py --input case/events.json --output case/timeline.html

# 3. 원본 파일 해시 감사 → audit.json (검증의 근거가 된다)
python skills/forensic-audit/scripts/audit_timestamps.py case/카카오톡_대화내용.txt --json > case/audit.json

# 4. 보고서 초안 작성 후 검증 — 근거 없는 해시/금지 문구/High-Fidelity 비파라메트릭 게이트
python scripts/verify_report.py case/감정서초안.md --evidence case/audit.json --morph-grounding --high-fidelity --strict --json

# 5. Kiwi 형태소 기반 포렌식 그라운딩 단독 감사
python scripts/korean_morph_forensic.py --evidence case/audit.json --report case/감정서초안.md --high-fidelity --json
```

검증기가 잡는 것 (실제 동작 확인):

- 근거 없는 SHA-256/MD5 → `FAIL (근거 없는 해시 N개)`
- `명백히 입증`, `법원에 유효`, `court-admissible` 등 과단정 문구 → `FAIL`
- 법령 조문 상한(정보통신망법 76조 등) 및 미래 연도 판례 날조 차단
- Local High-Fidelity 비파라메트릭 게이트 (`--high-fidelity`): 증거 원문/`<evidence>` 태그 일치도 70% 미달 시 즉시 차단. Vertex API 호출 없음.
- 부정 문맥(`유출의심 아님`, `조작 가능성을 배제할 수 없다`)은 오탈 차단하지 않음
- 인코딩이 UTF-16(Windows PowerShell 기본 출력)이어도 금지 문구를 읽어 낸다

## 무엇이 지원되고 무엇이 아닌가

| 요청 | 상태 | 조건 |
| :--- | :--- | :--- |
| 카카오톡 텍스트 내보내기 파싱 (모바일/PC) | ✅ | 텍스트 내보내기만. SQLite/백업 DB ❌ |
| 타임라인 HTML 렌더 (XSS 이스케이프, 샘플 거부) | ✅ | events.json 필요 |
| 파일 MAC 시각 + SHA-256/MD5 감사 | ✅ | `os.stat` 표면값. `$MFT`/Timestomping 판정 ❌ |
| 보고서 초안 검증 (금지 문구/해시/법령상한/High-Fidelity/형태소 그라운딩) | ✅ | `verify_report.py` (--evidence, --morph-grounding, --high-fidelity) |
| EVTX 헌팅 (Hayabusa/Chainsaw) | 🟡 BYO | `python scripts/check_tool.py hayabusa.exe` 통과 시에만 |
| MFT/Prefetch/Shimcache (Dissect/EZ-Tools) | 🟡 BYO | 실패 시 `error` 필드 + exit 3 ("0건" 위장 없음) |
| 메모리 덤프 (MemProcFS/Volatility 3) | 🟡 BYO | 덤프+도구 모두 있을 때만 |
| 영상 프레임/메타데이터 감사 | ✅ | ffmpeg 필요 — `setup.py --check`로 확인 |
| AI 사용 흔적 포렌식 (md/txt/E01 역추적) | ✅ | `skills/ai-trace-detector/SKILL.md` |
| 딥페이크/AI 합성 미디어 1차 감사 (SHA-256, C2PA/JUMBF, 2D FFT) | ✅ | `python scripts/analyze_deepfake_evidence.py <파일> --output 리포트.md` — 최종 판정은 다중 탐지기 앙상블 별도 수행 |
| 영상 대화록(Whisper) | 🔒 동의 필요 | `--upload-audio` 명시 동의 없이 외부 전송 금지 |
| 법령/판례 조회 | 🟡 키 필요 | 위 "법령 조회" 참고 |
| 무결성 훅 (쓰기 차단/감사 로그) | 🟡 Antigravity | best-effort. OS 읽기전용(`chmod 444`) 병행 권장 |

## 트러블슈팅

- **증개 가드에 쓰기가 막힌다**: 사본도 같은 확장자(`.raw`, `.E01`)면 차단된다. 사본은
  `img.raw.analysis.txt`처럼 확장자를 바꾸거나 `evidence/` 밖에서 작업하라.
- **증거 디렉토리 원클릭 잠금**: `sh scripts/lock_evidence.sh [evidence_dir]` (Windows는
  `lock_evidence.ps1`) — `chmod 444` 상당의 OS 읽기전용으로 훅 가드를 보강한다.
  `perl -e`/`ruby -e`/`powershell -enc` 인라인 실행은 증거 경로와 무관하게 차단된다.
- **`korean_law`가 exit 78(빌드없음)/79(키없음)**: `node scripts/setup_korean_law.mjs --check`로
  어느 쪽인지 확인한다. exit 79는 `LAW_OC` 미설정 — **키가 없다고 조문을 만들어내지 않는다**.
  exit 78은 `setup_korean_law.mjs` 미실행 — 정상 동작이다.
- **영상 스킬이 조용히 안 될 때**: `python skills/forensic-video/scripts/setup.py --check` —
  무엇이 없는지 + 플랫폼별 설치 명령을 출력한다.
- **Windows에 python이 없어서**: 검증 게이트가 경고 후 통과한다(비활성). `python3` 설치를 권장.
- **카카오톡 파일이 깨져 보인다**: UTF-8/CP949/UTF-16을 자동 판별한다. 그래도 실패하면
  모바일 앱에서 텍스트로 재내보내기하고, 파일을 다른 인코딩으로 재저장하지 말 것.
- **감사 체인 검증**: `python scripts/verify_audit_chain.py` (기본 `.lazyforensic/audit_trail.jsonl`, 파일 없으면 PASS·empty session)
- JSON 출력은 `python scripts/verify_audit_chain.py --json`, 빈 체인 거부(엄격 모드)는 `--no-empty` 추가
- 깨짐 시 `FAIL` 줄번호·`prev_hash` 기대값을 출력하므로 해당 줄부터 원본 대조 후 재생성하라

## 정직 선언 / 아키텍처

- **[`docs/GAPS.md`](docs/GAPS.md)** — 구현하지 않은 능력 목록 (광고-구현 차이 고정)
- **[`GEMINI.md`](GEMINI.md)** — Antigravity 호스트 계약, 스킬 라우팅, 실패 폐쇄 규칙
- **[`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)** — 요청 문구별 사용 예시
- 검증 게이트의 정체: `PostToolUse` **사후 게이트**다. 파일이 쓰인 뒤 검증하며, 호스트가
  `failurePolicy: FAIL_CLOSED`(exit 1)을 지원할 때 후속 제출이 막힌다. 우회 경로는 GAPS.md에 기록.

## 테스트 / CI

```bash
python -m pytest test/ -v
```

`.github/workflows/ci.yml`: Ubuntu/Windows 유닛 테스트 + 훅 가드 단언, korean-law-mcp 빌드+vitest.

## Lazy 생태계 (레포 경계)

- `LAZYANTIGRAVITY` — 런타임 우산: 훅 집계·공유 스킬 물질화·번들 MCP 런타임
- `lazyforensic` (본 레포) — 포렌식/한국법률 도메인 플러그인
- `lazyothers` — 리걸 문서·HWP·humanize 도메인 플러그인
- `lazyagentic` — 규칙 전용 거버넌스 플러그인 (Dual-Mount `~/agentic`)
- [`korean-law-mcp`](https://github.com/daeryundf2-prog/korean-law-mcp) — 한국법 조회 MCP 서버.
  본 레포가 `setup_korean_law.mjs`로 클론·빌드해 사용하고, LAZYANTIGRAVITY는 빌드본을 번들한다.

공유 자산: `scripts/coverage_audit.mjs`는 lazyforensic(캐노니컬)·lazyothers·LAZYANTIGRAVITY
3곳에 바이트 동일 사본으로 유지된다 — 수정 시 3곳 동기화 필수 (파일 헤더 주석 참조).

## 라이선스

본 레포 코드는 MIT ([`LICENSE`](LICENSE)). 한국 법령 MCP 서버는 별도 레포
[`daeryundf2-prog/korean-law-mcp`](https://github.com/daeryundf2-prog/korean-law-mcp)로 분리되어 있고
동일하게 MIT다 ([`NOTICE`](NOTICE)). `node scripts/setup_korean_law.mjs`가 최초 실행 시
해당 레포를 `./korean-law-mcp/`로 클론해 빌드한다. 무라이선스 제3자 디자인 카탈로그
(mengto-skills, design-systems 등)는 배포 리스크 때문에 제거되어 있다 — 자세한 사유는
[`NOTICE`](NOTICE)와 [`docs/GAPS.md`](docs/GAPS.md).
