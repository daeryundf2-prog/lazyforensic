# 지원 범위와 한계 (v1.6.2)

이 목록은 광고와 구현의 차이를 고정한다. `plugin.json`/`README.md`는 이 문서를 따른다.
v1.0.1: 검증 체인·훅 배관·카카오 파서의 결함을 수리하고, 무라이선스 제3자 트리를 제거했다.
v1.1.0: 에이전트 없이 쓰는 `lazyforensic report`(보고서 초안·무결성 검증)와 E01 판독을 추가했다. 그 한계는 아래 첫 절.

## 보고서 초안 (`lazyforensic report`, `scripts/report_draft.py`)

- 초안은 도구가 측정한 값(해시, E01 기록값, 파일 수정 시각, EXIF 기록값, 선조사 결과)만 채운다. 사건 개요·인계 기록·쓰기 방지 장치·분석관 의견은 사람이 쓴다 — 입력이 없으면 "미확인"으로 남는다.
- 보고서 서식은 일반적인 분석 보고서 구성을 따른 기본안이다. 기관의 DOCX·HWPX 양식에 자리표시자를 넣어 같은 형식으로 초안을 만들 수 있다. [양식 사용법](REPORT_TEMPLATES.md) Markdown·HTML과 편집용 DOCX·HWPX를 만든다. HWPX의 글꼴, 긴 해시의 줄바꿈, 여러 쪽에 걸친 표는 사용하는 한글 버전에서 확인한다.
- 삭제 파일 복구, 카빙, 파일시스템(NTFS/FAT/APFS) 해석, 레지스트리·브라우저·메신저 DB 분석은 하지 않는다. 증거 폴더에 있는 파일 그대로만 본다.
- 파일 수정 시각은 분석 PC에서 본 `os.stat`의 정수 나노초(`st_mtime_ns`)를 KST로 환산한 값이다. 파일시스템 자체의 시각 해상도(FAT 2초, NTFS 100 ns 등)는 확인하지 않는다. `events.json`의 timestamp와 `타임라인.html`은 마이크로초까지만 표시하고 원값은 mtime·mtime_ns에 남긴다. 복사·내보내기로 바뀐 시각을 원래 시각과 구별하지 못한다.
- EXIF 촬영 시각은 DateTimeOriginal에 SubSecTimeOriginal·OffsetTimeOriginal을 붙여 싣는다. 오프셋 태그가 없거나(`offset_status` absent), 있으나 비었거나(empty), 형식이 맞지 않는 사진(malformed)은 정렬에서만 KST로 가정한다. 보고서·`timeline`·양식·독립 HTML 타임라인은 같은 정수 나노초 기준으로 정렬하고, 기록된 정밀도로 선후를 가릴 수 없는 이웃은 동률(`order_tie`)로 표시한다. 소수초는 9자리까지만 비교한다.
- 참조 해시 대조는 원문 전체 경로 → 유니코드 정규형(NFC)을 맞춘 전체 경로 → 대소문자만 다른 전체 경로 → 경로 끝부분 → 파일명 순으로 짝을 짓는다. 한 단계에서 후보가 여러 개면 대조를 보류하고, 이름이 바뀐 파일은 '증거에 없음'으로 나온다(해시 값만으로 역추적하지 않음).
- 초안 검증은 `verify_report.py`의 정적 검사(근거 없는 해시·금지 문구·해시-파일 결합)다. 문장의 사실 여부를 판단하지 않는다.
- PDF 인쇄본은 만들지 않는다. DOCX를 PDF로 바꾸는 PC에 한글 글꼴이 없으면 PDF의 텍스트 층에는 한글이 남아도 종이·화면에는 빈칸으로 나온다. `lazyforensic printcheck 인쇄본.pdf --docx 보고서초안.docx`(Poppler 필요)가 독립 렌더러로 그린 화면에서 단어와 한글 글자마다 잉크가 있는지, 서로 다른 글자가 같은 대체 모양으로 그려지지 않았는지, 1쪽 제목과 `--expect` 문자열이 있는지, 글꼴이 모두 포함됐는지 본다. 글자 모양이 원문과 같은지(다른 글자로 바뀌었는지)는 보지 않고, 한글과 영문이 섞인 단어는 단어 전체의 잉크만 본다.

## EWF 판독 (`scripts/ewf_image.py`)

- 물리 이미지 E01 및 EWF2 2.1 Ex01의 단일·분할 세그먼트를 지원한다. Ex01은 원본 청크, Adler-32, deflate, bzip2 스트림, 단일 bzip2 블록, 8바이트 반복 패턴을 읽는다. 세그먼트 번호·세트 ID, 섹션 체인·체크섬, 테이블, 기록된 미디어 길이를 확인하고 미디어 MD5·SHA-1을 대조한다.
- L01(LVF/LEF)의 `ltree`와 Lx01(LEF2)의 단일 파일 데이터 섹션(`0x20`)에서 UTF-16LE `entry` 트리를 읽는다. 논리 스트림 길이는 `rec.tb`, 각 파일 내용은 `be`의 16진수 오프셋·길이를 기준으로 한다. 여러 일반 extent, 빈 파일, 데이터가 있는 폴더, 명시적인 중복 오프셋을 가진 sparse 항목을 지원한다. 파일 이름에 경로 구분자가 있을 수 있으므로 추출에는 ID를 쓴다. 이름에 짝이 없는 UTF-16 서로게이트가 있으면 화면에는 `\uNNNN`으로 표시하고, JSON의 `name_utf16le`에 원래 이름 바이트를 16진수로 보존한다.
- 논리 목록의 시각은 기록된 유닉스 초다. 저장 해시는 트리의 `ha`·`sha`에서 읽는다. 별도 인덱스·권한·원본 파일시스템 속성의 복원은 지원하지 않는다. 형식이 표시된 extent, 중복 오프셋 없는 sparse fill, 비표준 트리·손상된 UTF-16은 미지원이다.
- EWF2 암호화, 증분·재시작으로 비연속인 데이터, 메모리 extent, SMART(S01), AFF4, VMDK/VHD는 이 판독기의 범위 밖이다. EWF2 섹션 메타데이터·논리 트리는 64 MiB, 새 형식의 청크는 32 MiB까지 지원한다.
- 공개 참조 시험은 로컬 사본만 읽는다. 고정된 manifest의 `ex01-fast`, `ex01-none`, `logical` 표본 경로를 각각 `LF_EX01_FAST_SAMPLE`, `LF_EX01_NONE_SAMPLE`, `LF_L01_SAMPLE`로 지정하고 `python -m pytest test/test_ewf_public.py -q`를 실행한다. 두 Ex01은 독립 획득기의 원본 SHA-256과, L01은 컨테이너·미디어 및 9,219개 파일의 export digest와 대조한다. Lx01과 bzip2는 합성 시험으로 검증했다. [공개 표본 및 기준 해시](https://github.com/abrignoni/ewfprobe/blob/d14572626e54f3bb99763287b3adfea212c47d7e/tests/fixtures/manifest.json)
- `sectors` 섹션이 없는 초기 레이아웃은 미지원이다. 시험한 E01은 모두 libewf로 만든 EnCase 6 형식이다(`ewfacquire` 20140816 합성 픽스처, Digital Corpora `nps-2009-canon2-gen6.E01` — 기록상 획득 도구 20090927). FTK Imager·EnCase가 만든 E01로는 시험하지 않았다.
- 헤더의 사건번호·검사자·획득 일시는 획득 도구가 적은 값이다. 이 도구는 획득 과정을 확인하지 않는다. `header` 섹션(구형)의 시각에는 시간대가 없다.
- 미디어 해시 재계산은 이미지 전체를 읽는다(수백 GB면 수십 분 이상). `--no-image-verify`로 생략하면 결과는 '미확인'이다.

## DLP

`dlp-leakage-detector`는 분석관이 이미 확보한 로그를 맞추는 체크리스트다. 자동 유출 탐지 엔진이 없다.

`scripts/dlp_log_table.py`는 확보된 로그 CSV를 4축 체크리스트 표로 **재배열**만 한다 — 없는 로그를 만들거나 유출을 탐지하지 않는다.

## 카카오톡

`kakao-chat-extractor`는 모바일/PC **텍스트 내보내기**만 읽는다. SQLite `chat_logs`나 백업 DB 파서는 없다.
카카오톡 SQLite DB 및 앱 데이터베이스 분석은 `lazyothers` 플러그인의 `sqlite-evidence-query` 스킬을 활용한다 (복호화 키는 별도 확보 필요).
PC 파서는 실제 "대화내용 저장" 포맷(`===` 헤더 + `[오후 2:15] 닉네임 : 메시지`)과 레거시 합성 포맷을 읽는다.
첨부 판정은 확정 표기(사진/동영상/`파일:`/`파일 전송`)뿐이다. 시스템 이벤트와 인식 불가 행은 `type` 필드로 보존된다.

## 파일시스템 고급 분석

`$MFT`, `$SI` vs `$FN`, Timestomping 판정, Maya, MOV `mvhd`는 없다. `forensic-audit`는 `os.stat`과 해시만 본다.
`forensic-mft-parser`/`dfir-evtx-hunter`/`memory-triage`는 **bring-your-own-binary 래퍼**다. Dissect/Hayabusa/Chainsaw/MemProcFS/Volatility 바이너리는 미포함, 미설치 시 결과 생성 안 함 — 스킬은 명령 생성 전 `scripts/check_tool.py` 게이트를 실행하도록 지시한다(글이 아닌 메커니즘).
`parse_ntfs_artifacts.py`는 실패 시 `error` 필드 + exit 3으로 끝난다. "레코드 0건"과 파싱 실패를 혼동하지 말 것.
`scripts/download_dfir_binaries.ps1`은 별도 다운로드 래퍼이며 SHA256 검증은 수동이다(`-VerifyHash`는 계산만 수행).

## 검증 게이트 (verify_report / hallucination_guard)

`verify_report.py`의 해시 grounding은 **파일 단위 집합 비교 + 파일-해시 인접성 휴리스틱(`check_hash_file_binding`, ±2줄)**이다. 해시 A를 파일 B 서술에 붙이는 결합 오류는 완전 차단이 아니라 경고 수준으로만 잡는다.
2026-08 수정: 보고서에 해시가 있는데 `--evidence`가 없으면 과거 WARN(통과) 대신 **FAIL**로 바뀌었다(감사 생략+조작 해시 차단). 줄바꿈으로 분할된 64hex 해시와 "9시 30분" 식 한국어 시각도 검출한다. 여전히 잡지 못하는 것: 두 32hex 해시가 줄 경로에서 결합된 환영 해시(실패폐쇄 방향의 오탐), evidence 폴더 읽기 허용 후 `python -c` 인라인 쓰기(가드는 best-effort).
법령 조문은 `--law-cache`로 받은 korean_law MCP 응답 캐시의 조문 레코드(법령명·법령 ID·시행일·조·항)와만 대조한다. JSON 표기와 무관하게 역직렬화한 구조를 보며, 다른 법령·다른 시행일의 같은 번호 조문이나 항 정보가 없는 항 인용은 미확인(WARN)으로 남긴다. 항 구조 필드가 없는 평문 본문은 줄 첫머리의 항 표지(①부터 차례로, 첫 줄은 조 제목 뒤 허용)만 항으로 복원한다. 한 문장 속 기호 예시나 줄을 나누지 않고 평탄화한 본문의 표지는 근거로 쓰지 않고 그 이유를 판정에 남긴다 — 실제로 여러 항인 평탄화 본문도 미확인이 된다. 캐시가 없으면 출처 표기 없는 조문 인용은 WARN일 뿐이고, 어느 경우든 조문 텍스트 진위는 검증하지 않는다. 보고서 문장에서 법령명은 인용 바로 앞의 법령명·「」·`같은 법` 표현으로만 찾는다.
주장-근거 결합(`--morph-grounding`·`--high-fidelity`)은 용어의 위치만 본다. 부정(‘있지 않다’·‘없다’)이나 비교(‘보다’·‘이후’·‘와 같다’) 문장은 위치가 맞아도 거짓일 수 있고 엇갈려도 참일 수 있어 판정하지 않고 사람 검토(WARN, `claim_binding`의 `review`)로 넘긴다. 표현 목록에 없는 부정·비교는 일반 문장으로 판정된다.
`hallucination_guard.mjs`는 **PostToolUse 사후 게이트**다 — 파일이 이미 쓰인 뒤 검사하며, 실제 차단은 호스트가 `failurePolicy: FAIL_CLOSED`(exit 1)을 지원할 때 작동한다. exit 코드 규약: 차단 1, 통과 0, 검증 불가(보고서 미발견) 0+경고, python 전무 0+설치 안내(FAIL_OPEN).
python이 아예 없는 Windows에서는 검증 자체가 불가능해 경고 후 통과한다 — 이 경우 게이트는 사실상 비활성이다.

## 증거 무결성 Hook

`evidence_guard.mjs`는 **best-effort 가드**다. stdin JSON(1.5초 데드라인) + 환경변수 + CLI 인자의 경로/명령 **필드만** 검사하며, 호스트가 stdin을 주지 않거나, 매처 밖의 도구를 쓰거나, 직접 `python open(..., 'w')` 등 호스트 미후킹 경로로 쓰면 우회된다. OS 수준 읽기전용(`chmod 444`)을 대체하지 않는다.
보고서 본문(content)은 검사하지 않는다 — 본문 언급만으로 쓰기가 막히는 과차단을 막기 위함이다.
PostToolUse 감사 로그는 `<cwd>/.lazyforensic/audit_trail.jsonl` 단일 파일이다. 해시가 `null`이면 2GiB 초과로 의도적으로 건너뛴 것이다(부재≠실패). 각 행은 이전행 원문의 sha256을 `prev_hash`로 연결한 해시체인이다(위변조 탐지용, HMAC 아님).

## 제거된 제3자 트리 (라이선스)

무라이선스 제3자 콘텐츠 — `mengto-skills/`(Meng To/DesignCode 카탈로그), `design-systems/`(VoltAgent 브랜드 카탈로그, 제3자 상표), `vendor/antv-infographic/`, `skills/slopslap/`(upstream private), 그리고 이들에 의존하던 UI 피커 스킬(`ui-studio`, `design-system`, `frontend-ui-ux`) — 은 배포 라이선스 검증을 끝내지 못해 **제거**했다. `NOTICE` 참고. HTML 뷰어 스타일은 인라인 CSS로 직접 작성한다. 필요하면 권리를 확보해 별도 옵션 팩으로 재도입한다.

## 제거된 미배선 헬퍼 (고립 유산)

`skills/video-editor/helpers/pack_transcripts.py`와 `helpers/transcribe_batch.py`는 `SKILL.md`·테스트·타 헬퍼 어디에서도 참조되지 않는 고립 유산이라 **제거**했다. 전사 일괄 파이프라인이 필요하면 `skills/forensic-video/scripts/`(watch/whisper/transcribe BYO 체인)를 쓸 것. 대용량 트랜스크립트는 `verify_report.py`의 지연 I/O(스트리밍 해시+청크 읽기) 경로로 처리한다.

## 법령 MCP

소스는 별도 레포 [`daeryundf2-prog/korean-law-mcp`](https://github.com/daeryundf2-prog/korean-law-mcp)에 있고,
`node scripts/setup_korean_law.mjs`가 `./korean-law-mcp/`로 클론해 빌드한다. 빌드와 `LAW_OC`가 없으면 조회하지 않는다. 법률 자문 엔진이 아니다. 이 레포에서 `fly deploy`를 하지 않는다.

## Manim

`video-editor/manim-video/REFERENCE.md`는 참고 문서다. 교육 영상 렌더 파이프라인이 아니다.

## 로컬 STT / 미디어 매칭 / PII (v1.0.2 추가분의 한계)

- `scripts/local_stt.py`는 화자 분리(diarization)를 하지 않는다. "누가 말했는가"가 필요한 문항은 전사 결과를 '미확인'으로 두고 별도 도구를 쓴다.
- `scripts/image_similarity.py`의 지각 해시는 강한 크롭·회전·색반전에 약하다. 유사도 부재는 '없음'이 아니라 '해시 근접 없음'이다.
- `scripts/video_fingerprint.py`는 화면 내용만 비교한다 — 오디오 지문·강한 편집(속도 변경, 크롭)은 잡지 못한다.
- `scripts/pii_mask.py`는 정규식 기반이라 이름·주소 같은 비정형 PII는 탐지하지 못하고, 날짜-계좌번호 같은 경계는 패턴으로만 구분한다. 마스킹본도 사람 검토가 필요하다.
- `scripts/local_only_audit.py`는 정적 검사라 subprocess가 띄운 외부 바이너리의 송신은 못 본다. 실제 반출 차단은 OS 방화벽·네트워크 격리의 몫이다.
- `scripts/evidence_manifest.py`는 `os.stat` 표면값이다 — 타임스탬핑 위조는 못 잡는다. '무결성 보장'이 아니라 '변경 탐지'다.
- `scripts/extract_hwpx.py`는 HWPX(ZIP+XML)만 읽고 바이너리 .hwp는 못 읽는다. 표 구조·서식은 잃고 텍스트 순서만 남는다.
- `scripts/doc_diff.py`는 줄 단위 비교라 문장 내 부분 변경은 줄 전체가 '변경'으로 표시된다.
- `scripts/audio_survey.py`의 RMS 발화 구간은 '후보'다 — 배경소음이 크면 무음도 발화로 잡힌다.
- `scripts/exif_audit.py`의 EXIF는 자유롭게 편집 가능하다. '기록된 값'이지 '진실'이 아니며, EXIF 부재는 메신저 경유 흔적일 뿐 조작 증거가 아니다.
- `scripts/osint_username.py`는 sherlock BYO다. 조회 닉네임이 외부 사이트에 노출되므로 의뢰인 동의 없이 쓰지 않는다.
- `scripts/keyword_report.py`는 리터럴 검색이다. 유의어·OCR 오류·이미지 속 글자는 못 잡는다. 인코딩은 BOM → XML/HTML 선언 → 엄격한 UTF-8 → 엄격한 CP949 순으로 정하고, 어느 것으로도 해석되지 않는 파일은 '인코딩 판정 불가'로 따로 세어 검색 수에서 뺀다. 파일 전체가 UTF-8이 아니면 줄마다 다시 판정해 섞인 인코딩(`mixed`)과 해석하지 못한 줄 구간을 기록한다. CP949 줄이 Shift-JIS로도 자연스럽게 읽히거나 BOM 없는 UTF-16의 바이트 순서를 ASCII 글자로 정할 수 없으면 '인코딩 불확실'로 세고 후보 코덱 히트를 미확정으로 남긴다(`--encoding`으로 재검색). 판정은 휴리스틱이다: KS X 1001 범위 바이트만으로 된 BOM 없는 UTF-16, EUC-JP·Big5 등 다른 코덱, 한 줄 안에서 섞인 인코딩은 구별하지 못한다. 제어 문자가 1%를 넘는 파일은 텍스트가 아닌 것으로 보고 검색 수에서 뺀다.
- `scripts/signature_check.py`는 선두 수십 바이트만 본다. 시그니처를 위조한 파일·UNKNOWN(시그니처 없는 텍스트류)은 정상일 수 있다.
- `scripts/dedup_files.py`는 정확 중복만 확실하다. 유사 그룹은 이미지뿐이고 '어느 쪽이 원본인가'는 판정하지 않는다.
- `scripts/archive_survey.py`는 zip/tar만 본다 — RAR/7z는 외부 도구. 플래그는 '수동 확인 필요 신호'이지 악성 판정이 아니다.
- `scripts/pdf_audit.py`는 바이트 패턴 표면 감사다 — PDF 1.5+ 객체 스트림에 숨은 /Encrypt·/JavaScript는 못 잡는다. 정밀 파싱은 qpdf/pikepdf 영역.
- `scripts/sqlite_survey.py`는 원본을 immutable 읽기전용으로만 열고 스키마·행 수만 본다 — 어느 컬럼이 메시지인지 같은 해석은 수동이다. `-wal`/`-journal`이 있으면 본체와 함께 임시 작업 사본으로 복사해 커밋된 상태를 읽고 두 파일의 SHA-256을 기록한다. 부수 파일은 증거 열거 규칙이 허용한 것만 쓰고, 범위 밖이면 읽지 않고 행 수를 미확인으로 둔다. WAL은 프레임 경계·salt·체크섬을 직접 검사해 잘린 취득본이면 보이는 행 수만 적는다. 반영하지 못하면 행 수를 미확인으로 둔다. `total_rows`는 테이블 행 수 합계이고 뷰가 돌려주는 행은 `view_rows`로 따로 센다. 삭제된 레코드(프리 페이지·미커밋 WAL 프레임) 복구는 하지 않는다.
- `scripts/merge_timeline.py`의 STT 이벤트는 '--anchor'로 사용자가 준 녹음 기준시각에 의존한다 — 앵커가 틀리면 전 구간이 어긋나며, 앵커 없는 STT는 타임라인에서 제외된다(벽시계를 지어내지 않음). mtime·EXIF·카톡 시각도 '기록된 값'이지 위조 불가능한 진실이 아니다.
- `scripts/court_evidence_sheet.py`의 출력은 관행 양식 **초안**이다 — ECFS 공인 기계 형식은 없고, 입증취지는 --purpose 지정분만 채운다. 제출 전 사람의 법률 검토가 필수다.
- `scripts/video_integrity.py`는 '어디까지 읽히나' 진단이다 — 복구 자체는 untrunc 등 별도 도구. ffprobe/ffmpeg가 없으면 실행 자체가 안 된다(exit 3).
- `scripts/audio_fingerprint.py`는 fpcalc(chromaprint) BYO다 — 재인코딩은 잡지만 편집된 구간 일치는 못 잡는다. 유사도는 '동일 녹취' 판정이 아니다. 순수음·정적 신호 같은 퇴화 오디오는 서로 다른 소리도 지문이 같아질 수 있다(chromaprint 자체 한계 — 실제 음성/음악에서 유효).
- `scripts/local_stt.py`의 moonshine 엔진은 영어 전용이고 세그먼트 타임스탬프를 주지 않는다 — 한국어 증거에는 쓰지 않는다(자동탐지 최후순위).

## 후속 과제 (알려진 미해결 — v1.0.1 시점)

코드로 끝나지 않는 결정/외부 기록이 필요한 항목. 해결되면 이 목록에서 지운다.

1. **law.go.kr 안티봇 우회의 ToS/정책 검토 (배포 전 필수)** — `korean-law-mcp/src/lib/law-antibot.ts`가 법제처의 난독화 JS 챌린지를 파싱해 우회하고, Chrome UA 스푼핑(`fetch-with-retry.ts`)을 쓴다. 실용적이지만 데이터 제공처 약관의 회색 지대다. 우회 사용 시 `LAW_TOS_ACK=1` 명시적 동의가 필요하며, 미설정 시 우회를 쓰지 않고 원본 응답을 유지한다. 공개 배포/상용 제공 전에 권리 관계 확인과 판단 기록이 필요하다.
2. **korean-law-mcp CHANGELOG 4.10.0 항목 누락** — vendored 소스의 `CHANGELOG.md` 최신 항목이 4.9.7인데 package.json/CLAUDE.md는 4.10.0이다. 폐지법령 기능의 정확한 변경 내역을 업스트림 기록으로 확인해야 쓸 수 있어 비워 뒀다. 임의로 채우면 그것이 조작이다.
3. **CI Node.js 20 지원 종료 경고 (해결)** — `actions/checkout@v5`·`setup-node@v5`·`setup-python@v6` + Node 24로 상향했다.
4. **해시-파일 결합 오류는 부분 완화** — `verify_report.py`의 `check_hash_file_binding`이 파일명-해시 인접성(±2줄)을 대조해 경고한다. 완전 검증은 아니라 위 "검증 게이트" 섹션과 동일 한계가 남는다. 후보 개선: audit_trail에 파일-해시 바인딩을 저장하고 강제 차단으로 승격.
5. **법령 조문 자동 대조는 캐시 범위까지** — `--law-cache`로 MCP 응답 캐시를 넘기면 법령·시행일·조·항 단위로 대조한다(2026-11). 캐시에 없는 판본이나 법령명을 생략한 동번호 조문은 미확인으로 남는다. 캐시 자체가 공식 원문인지는 확인하지 않는다(보조 대조).

