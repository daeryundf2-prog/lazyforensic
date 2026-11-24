# 종료 코드 레퍼런스 (Exit Codes)

스크립트를 셸/CI/다른 도구에 연동할 때 쓰는 종료 코드 계약이다.
`sys.exit(main())` 반환값 기준 — `main`이 반환하는 정수가 곧 종료 코드다.

## 공통 계약

| 코드 | 의미 | 사용 예 |
|:---:|:---|:---|
| `0` | 성공. 검증 게이트는 "발견된 위반 없음(PASS/WARN)"도 0 | 전 스크립트 |
| `1` | 검증 실패 또는 실행 오류. `verify_report`의 FAIL/`--strict` WARN 차단 | `verify_report.py`, `verify_claim_ledger.py`, `setup_forensic_env.py` |
| `2` | 사용법/입력 오류. 파일 없음, 읽기 실패, 인자 오류 (argparse 기본값과 동일) | `parse_kakao.py`, `audit_timestamps.py`, `generate_timeline.py` 등 대부분의 스크립트 |
| `3` | BYO 도구/엔진 부재 — **결과를 지어내지 않고** 실패폐쇄 | `local_stt.py`, `audio_fingerprint.py`, `parse_ntfs_artifacts.py`, `check_tool.py` 계열 |
| `78` | `EX_CONFIG` — 설정/빌드 미완 (korean-law-mcp 미빌드 또는 키 없음) | `scripts/setup_korean_law.mjs --check` |

## 스크립트별 표

| 스크립트 | 0 | 1 | 2 | 3 | 78 |
|:---|:---:|:---:|:---:|:---:|:---:|
| `verify_report.py` | PASS/WARN | FAIL, `--strict`+WARN | 보고서 없음/인자 오류 | — | — |
| `verify_claim_ledger.py` | 위반 없음 | 위반 있음 | (argparse) | — | — |
| `parse_kakao.py` | 파싱 완료 | — | 입력 파일 없음/판별 실패 | — | — |
| `audit_timestamps.py` | 감사 완료 | — | 파일 없음 | — | — |
| `generate_timeline.py` | 렌더 완료 | — | 입력 없음/JSON 오류 | — | — |
| `korean_morph_forensic.py` | 그라운딩 통과 | — | 인자 오류 | — | — |
| `merge_timeline.py` | 병합 완료 | — | 매니페스트/입력 오류 | — | — |
| `local_stt.py` | 전사 완료 | — | 인자 오류 | STT 엔진 없음 | — |
| `audio_fingerprint.py` | 지표 완료 | — | 인자 오류 | fpcalc 없음 | — |
| `parse_ntfs_artifacts.py` | 파싱 완료 | 일반 오류 | — | BYO 도구 없음 | — |
| `exif_audit.py` / `image_similarity.py` | 감사 완료 | — | 인자 오류 | exiftool 등 없음 | — |
| `report_draft.py` (`lazyforensic report`) | 초안 작성, 문제 없음 | 해시 불일치·판독 오류·**조사 범위 미확인**·초안 검증 FAIL (초안은 작성됨) | 인자 오류·출력 폴더가 증거 안 | — | — |
| `case_survey.py` (`lazyforensic scan`) | 단계 호출 완료, 열거 완전 | **조사 범위 미확인** | 인자 오류·출력 경로가 증거 안 | — | — |
| `evidence_manifest.py` (`lazyforensic manifest`) | 생성 완료 | 검증 실패, **조사 범위 미확인**(부분 매니페스트) | 인자 오류 | — | — |
| `case_envelope.py` | 생성 완료 | — | 인자 오류 | — | — |
| `print_check.py` (`lazyforensic printcheck`) | 인쇄본의 글자가 모두 그려짐 | 빈 글자·대체 글리프·누락 문자열·미포함 글꼴 | PDF/DOCX 없음·인자 오류 | Poppler 없음·PDF 판독 실패 (통과로 치지 않음) | — |
| `check_tool.py` | 도구 발견 | — | 하나 이상 미발견 (설치 안내만 출력) | — | — |
| `setup_forensic_env.py` | 설정 완료 | 단계 실패 | — | — | — |
| `setup_korean_law.mjs --check` | 빌드+키 OK | 빌드 실패 | — | — | 미빌드/키 없음 |
| `setup_korean_law.mjs` (설치) | 고정 커밋·잠금 파일로 빌드 완료 | 기존 체크아웃이 고정 커밋·잠금 파일과 다름, 클론·빌드 실패 | — | — | — |
| `analyze_deepfake_evidence.py` | 분석 완료 | 파일 없음 | FFT 플롯 실패 | — | — |
| `local_only_audit.py` | UNGATED 없음 | UNGATED 지점 발견 | — | — | — |

표에 없는 나머지 `scripts/*.py`는 `0`(성공)/`2`(사용법 오류) 관례를 따른다.

## 조사 범위 미확인 (부분 범위)

증거 폴더 안의 폴더를 열지 못하면(권한 거부 등) 그 아래 파일 수를 알 수 없다. 같은 트리에 대해
`report`·`scan`·`manifest`는 모두 다음과 같이 동작한다.

- 종료 코드 `1` (`evidence_manifest.PARTIAL_SCOPE_EXIT`). 정상 트리에서는 `0`.
- 표준 오류에 같은 형식의 한 줄: `조사 범위 미확인(부분 결과): 열거하지 못한 폴더 N개 (…) — 그 아래 파일 수를 알 수 없습니다. 종료 코드 1`.
- 기계 판독 기록: `report`는 `무결성검증.json`의 `scope.status = "partial"`(전체 수 `null`),
  `scan`은 `scope.complete = false`와 `processing_receipt`의 `Incomplete scope` 경고·`exit_code: 1`,
  `manifest`는 `complete = false`와 `enumeration_errors`.

종료 코드 `1`은 해시 불일치 등 다른 문제와 같은 코드이므로, 원인은 위 기록과 표준 오류 줄로 구별한다.

## 연동 지침

- **CI에서 게이트로 쓸 때**: `1`만 실패로 취급하고 `2`/`3`/`78`은 환경·입력 문제로
  구분해 알림 경로를 달리하는 것이 좋다.
- **`3`은 "측정 0건"이 아니다**: 도구가 없어 아무것도 하지 못한 상태다.
  출력 JSON은 항상 `error` 필드를 포함하며, 빈 결과를 성공으로 위장하지 않는다.
- **`78`은 재시도하지 않는다**: `setup_korean_law.mjs` 빌드+키 설정 후 다시 실행.
