# LazyForensic 빠른 시작 (보고서 초안 만들기)

증거 폴더 하나를 넣으면 한국어 분석 보고서 **초안**과 해시·무결성 검증 결과가 나온다.
에이전트(Antigravity 등)는 필요 없다. 모든 처리는 내 PC 안에서 끝나고 인터넷으로 증거를 보내지 않는다.

## 1. 설치 (한 번만)

### Windows

PowerShell을 연다(관리자 권한 필요 없음). 아래 한 줄을 붙여 넣고 Enter.

```powershell
irm https://raw.githubusercontent.com/daeryundf2-prog/lazyforensic/main/install.ps1 | iex
```

- Python 3.10 이상이 없으면 winget으로 Python 3.12를 사용자 계정에 설치한다.
- 프로그램은 `%LOCALAPPDATA%\LazyForensic`에 들어가고, `lazyforensic` 명령이 PATH에 추가된다.
- 끝나면 **PowerShell 창을 새로 연다.**

### macOS

터미널을 열고 아래 한 줄을 붙여 넣는다.

```sh
curl -fsSL https://raw.githubusercontent.com/daeryundf2-prog/lazyforensic/main/install.sh | sh
```

- Python 3.10 이상이 필요하다. 없으면 Homebrew가 있을 때 자동 설치하고, 없으면
  python.org에서 설치하라는 안내가 나온다(설치 후 같은 명령을 다시 실행).
- 끝나면 **터미널 창을 새로 연다.**

### 인터넷이 없는 분석 PC

인터넷 되는 PC에서 [Releases](https://github.com/daeryundf2-prog/lazyforensic/releases)의 소스 zip을 받아
분석 PC로 옮기고 압축을 푼 폴더에서 실행한다.

- Windows: `powershell -ExecutionPolicy Bypass -File install.ps1`
- macOS: `sh install.sh`

오프라인이면 Pillow 설치가 건너뛰어져 사진 EXIF 점검만 "미수행"으로 나오고, 나머지는 그대로 동작한다.

## 2. 실행

```text
lazyforensic report <증거 폴더>
```

예: `lazyforensic report D:\사건\증거` (Windows), `lazyforensic report ~/사건/증거` (macOS)

- 증거 폴더에는 E01 이미지(`.E01`, `.E02` …)나 이미지에서 내보낸 파일을 그대로 둔다.
- 결과는 증거 폴더 **옆**의 `<폴더명>_분석결과`에 생긴다. 증거 폴더에는 아무것도 쓰지 않는다.
- 끝나면 화면에 `보고서초안.html` 경로가 나온다. 브라우저로 열어 검토하고, 한글·Word로 옮길 때는
  HTML 화면을 복사해 붙여 넣는다.

### 있으면 더 채워지는 것 (선택)

| 옵션 | 하는 일 |
| :--- | :--- |
| `--case-info 사건정보.txt` | 사건번호·사건명·의뢰인·분석 목적·분석 담당을 1장에 채운다. 빈 양식은 `lazyforensic report --case-template 사건정보.txt` |
| `--hashes 해시목록.txt` | 획득 로그·의뢰인이 준 해시표·`sha256sum` 결과와 증거 해시를 대조한다. 여러 파일 가능 |
| `--keywords 계좌,송금` | 텍스트 파일에서 키워드를 찾아 건수를 싣는다 |
| `-o 출력폴더` | 결과 폴더 위치를 바꾼다 (증거 폴더 안은 거부) |
| `--no-image-verify` | 큰 E01의 전체 해시 재계산을 건너뛴다 (결과는 "미확인") |

## 3. 결과물

| 파일 | 내용 |
| :--- | :--- |
| `보고서초안.html` / `.md` | 보고서 초안 (사건 개요 → 증거 목록·해시 → 무결성 검증 → 획득·분석 환경 → 분석 방법 → 분석 결과·타임라인 → 종합 → 한계) |
| `파일해시목록.csv` | 전체 파일의 MD5·SHA-1·SHA-256·크기·수정 시각 (엑셀에서 바로 열림) |
| `무결성검증.json` | 분석 전/후 해시, E01 판독, 참조 해시 대조 원자료 |
| `타임라인.html` | 파일 수정 시각·사진 촬영 시각·이미지 획득 시각을 한 줄로 |
| `초안검증.json` | 초안 자체 검사 결과 (근거 없는 해시·과단정 표현이 있으면 FAIL) |

화면 마지막 줄이 `확인 필요: …`이면 해시 불일치나 판독 오류가 있다는 뜻이다(종료 코드 1).
초안은 그래도 만들어지며, 보고서 3장에 무엇이 다른지 나온다.

## 4. 초안에서 사람이 확인할 것

1. **1장 사건 개요**: "미확인" 칸을 실제 값으로 채운다.
2. **3장 무결성 검증**: 모든 행이 "일치"인지. "불일치"·"판독 오류"면 원본과 사본을 다시 대조한다.
   쓰기 방지 장치·인계 기록은 도구가 알 수 없으므로 직접 적는다.
3. **4장 획득 환경**: E01에 적힌 기록값이다. 실제 획득 기록(획득 확인서 등)과 맞는지 본다.
4. **6장 분석 결과**: 시그니처 불일치·중복·EXIF는 검토 대상 목록이지 결론이 아니다. 해당 파일을 직접 열어 본다.
5. **7장 분석관 의견**: 도구는 비워 둔다. 분석관이 쓴다.
6. **8장 한계**: 이번 사건에서 하지 않은 분석(삭제 파일 복구, 메신저 DB 등)을 지우지 말고 남긴다.

한계 전체 목록: [`docs/GAPS.md`](GAPS.md). 공개 표본으로 돌린 실제 결과: [`examples/nps-2009-canon2`](../examples/nps-2009-canon2/).
