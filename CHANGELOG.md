# Changelog

## v1.7.1

GPT 3차 적대적 리뷰(D17) 14건과 D13 남은 부분을 고친 판이다.

- SQLite WAL에서 salt가 어긋난 프레임 뒤에 현 세대 salt(또는 현 세대보다 앞서지 않은 salt)의 프레임이 다시 나오면 이전 세대 꼬리로 분류하지 않는다. 보이는 행 수와 최종 행 수 미확인, 사유(`generation_undetermined_frames`)를 남긴다. 실제 `RESTART` 뒤 이전 세대 꼬리는 그대로 완전한 취득으로 본다.
- SQLite 테이블 이름을 SQLite 식별자 규칙대로 인용해 `"`가 든 이름도 센다. 저장 테이블 하나라도 행 수를 세지 못하면 최종 합계를 0이 아니라 미확인으로 두고, 센 테이블의 소계를 `visible_rows`·한계 절에 적는다.
- 증거 열거기가 `x.db-WAL`로 승인한 파일을 SQLite가 `x.db-wal`로 여는 경우(대소문자를 구별하지 않는 볼륨) 같은 폴더·같은 파일(`samefile`)일 때만 그 승인 항목으로 읽고 해시한다. 디렉터리 조사와 단일 DB 조사가 같은 행 수·해시를 낸다. 이름만 비슷한 다른 파일과 링크는 계속 범위 밖이다.
- 참조 해시 대조가 원문 전체 경로 → NFC 전체 경로 → 대소문자만 다른 전체 경로(`path_casefold`)를 모두 본 뒤에만 경로 끝부분·파일명 후보를 쓴다. 정규형만 다른 실제 파일이 있으면 다른 폴더의 같은 이름 파일에 귀속하지 않고, 같은 단계의 전체 경로 후보가 둘이면 보류한다.
- 키워드 검색이 바이트만으로 코덱을 정할 수 없는 파일을 '검색 완료'로 세지 않는다. CP949로 읽은 줄이 Shift-JIS로도 자연스럽게 읽히거나, BOM 없는 UTF-16의 바이트 순서를 ASCII 글자로 정할 수 없으면 '인코딩 불확실'(`encoding_uncertain`)로 세고 후보 코덱으로도 검색해 `candidate_hits`(미확정)로 남긴다. `kw --encoding <코덱>`으로 명시해 다시 검색한다.
- 독립 HTML 타임라인(`generate_timeline.py`)과 `timeline --plaso`가 보고서와 같은 정수 나노초 정렬기를 쓴다. Plaso ISO 시각은 원문 소수부(9자리)를 그대로 싣고, HTML은 기록 정밀도 안의 동률을 카드와 정렬 안내에 표시한다.
- EXIF `OffsetTimeOriginal`이 있으나 비었거나 공백뿐이면 `offset_status: empty`로 태그 부재(`absent`)와 구별하고, 태그 존재(`offset_present`)·원문을 events.json·양식·본문에 따로 남긴다.
- 타임라인 HTML이 촬영 기록(`media`)을 '촬영기록'으로 표시하고 범주가 없거나 모르는 사건은 '미분류'로 중립 표시한다. 파일행위 필터에 들어가지 않는다. `timeline` 병합 이벤트에도 범주를 싣는다.
- `verify_report.py --morph-grounding`·`--high-fidelity`가 결론 문장이 한 근거 레코드(JSON 객체 하나, 관찰 문장 하나, 텍스트 한 줄)의 대상에 다른 레코드의 값을 붙였는지 본다. 대응 위치는 JSON 키 경로나 문장 단어 정렬로 맞추고, 그 레코드에 그 자리의 다른 값이 있는데 주장에 없을 때만 엇갈림으로 본다(여러 대상을 나열한 문장은 해당 없음). `<evidence>`로 정확한 원문을 인용해도 결론이 다른 레코드의 값을 쓰면(예: 청색 표시는 상자 A인데 '상자 B') `주장-근거 결합 불일치`로 `--high-fidelity`에서 FAIL, `--morph-grounding`만이면 WARN이다. 결과 JSON `claim_binding`에 문장별 `bound`/`unbound`와 엇갈린 두 용어를 남긴다. PASS 출력과 레인 지시(GEMINI.md)에 자동 검사 범위를 적고, 결론의 의미는 사람이 검토한다고 밝힌다.
- 설치 스크립트가 요청한 태그와 릴리스 조회가 돌려준 태그가 같은지 자산을 보기 전에 확인한다. 다른(이전) 태그를 돌려주면 설치하지 않는다. 릴리스 설치 스크립트는 자기 태그를 설치할 때 박힌 SHA-256 대조를 반드시 하고, 다른 태그는 `LAZYFORENSIC_SHA256`을 함께 줄 때만 설치한다. 로컬 가짜 릴리스 서버로 합계 파일만 변경, 다른 자산, 다른 태그 응답, 신뢰할 수 없는 인증서 네 경우 모두 비성공 종료와 기존 앱 트리·영수증의 바이트 동일을 시험한다.
- `--law-cache` 대조가 조문 본문 속 기호 예시(예: `‘②’`)를 항 번호로 쓰지 않는다. 항 필드·항 목록·조문 식별자가 있으면 그것만 쓰고, 없을 때만 본문의 항 시작 표지(첫머리나 공백 뒤 ①부터 차례로)를 쓴다.
- 일반 DOCX·HWPX 초안의 `{{…}}`를 같은 이름 Markdown이 있다는 이유로 통과시키지 않는다. 근거 파일(`--evidence`)의 측정값(파일명·검색어 등) 안의 중괄호만 면제하고, 그 밖의 미작성 칸은 동반 파일 유무와 관계없이 실패한다. `report`는 편집용 초안을 무결성·선조사·사건 기록과 대조한다. 양식 대체 텍스트·제목·설명에 남은 `{{review}}`는 실행과 재검증 모두에서 실패한다(회귀 시험 추가).
- `산출물목록.json`을 같은 폴더의 임시 파일에 완성해 쓴 뒤 원자적으로 교체한다(확보 시에는 하드 링크로 배타 게시). 게시 중 어느 순간에 종료돼도 목록은 `in_progress` 또는 `complete`로 읽히고, 다시 실행하면 새 폴더에 쓴다. 복구 규칙은 [빠른 시작](docs/QUICKSTART.md)에 적었다. 실제 전원 장애와 저장장치의 쓰기 영속성은 시험하지 않았다.
- `coverage_audit.mjs`의 분모가 표 행·목록 항목뿐 아니라 일반 문장도 센다. 목록 옆 일반 문장의 관찰이 빠지면 100%가 되지 않고, 요약과 수신증 `denominator`에 정의와 "구조화 항목 a/b, 일반 문장 c/d"를 남긴다.

## v1.7.0

GPT 적대적 리뷰 두 차례(D11 17건, D13 13건)에서 나온 문제를 모두 고친 판이다. 설치 스크립트가 이 판부터 릴리스 자산의 SHA-256을 확인하므로 v1.6.2 이하 릴리스로는 새 설치 스크립트가 동작하지 않는다.

- 보고서 출력 폴더를 원자적으로 확보한다. 산출물 목록 자리를 O_EXCL로 먼저 만든 실행만 그 폴더를 쓰고, 같은 위치를 동시에 요청한 다른 실행은 `_2` 다음 번호로 간다. `산출물목록.json`은 폴더를 다시 훑지 않고 그 실행이 게시한 파일로 만들며, 다른 무엇이 넣은 파일은 `not_from_this_run`으로 따로 적고 종료 코드 1로 알린다.
- 폴더 열거 실패로 조사 범위가 부분이면 `report`·`scan`·`manifest` 모두 종료 코드 1과 같은 형식의 표준 오류 한 줄을 남긴다. `scan`은 이전에 0이었다. 선조사 영수증에 `Incomplete scope` 경고와 실제 종료 코드를 싣는다. [종료 코드](docs/EXIT_CODES.md)
- `--law-cache` 대조가 JSON 원문 부분 문자열이 아니라 역직렬화한 조문 레코드(법령명·법령 ID·시행일·조·항)를 비교한다. 유니코드 이스케이프·공백만 다른 캐시는 같은 판정을 내리고, 다른 법령·시행일의 같은 번호 조문, 캐시에 없는 항, 다른 조문 본문 속 언급은 근거로 쓰지 않는다. 인용마다 판정과 근거 레코드를 결과 JSON `law_cache`에 남긴다.
- 양식 초안의 남은 자리표시자를 빈 양식에서 판정한다. 파일명이 `{{year}}.txt`인 증거처럼 측정값에 든 중괄호는 그대로 보존하고 통과하며, 빈 양식에서 치환되지 않는 위치에 남은 토큰은 계속 실패한다. Markdown 초안과 같은 본문인 기본 DOCX·HWPX 초안도 측정값 중괄호로 실패하지 않는다. `verify_report.py --template-source`로 빈 양식을 지정한다.
- 양식 검증 영수증(`초안검증.json`의 `template.input_template`)에 받은 빈 양식의 이름·바이트 수·SHA-256을 기록하고, 양식 대조 파일 둘째 줄에 같은 SHA-256을 묶는다. 다른 빈 양식으로 다시 검증하면 실패한다.
- 설치 스크립트가 릴리스 자산 `lazyforensic-<태그>.zip`·`.tar.gz`만 받고 SHA-256을 `SHA256SUMS`·스크립트에 박힌 값·(Windows) GitHub 자산 digest와 대조한다. 릴리스 조회나 대조에 실패하면 main 브랜치로 대신 설치하지 않고 기존 설치를 그대로 둔다. Pillow는 `requirements-install.txt`의 고정 해시로만 설치하고(`--require-hashes`), 설치 영수증 `install-receipt.json`에 태그·아카이브 SHA-256을 남긴다. 릴리스 자산은 `.github/workflows/release-assets.yml`이 붙인다. [설치 파일 확인](README.md#설치-파일-확인)
- `setup_korean_law.mjs`가 기존 `korean-law-mcp/`를 재사용하기 전에 실제 커밋이 고정 커밋과 같고 `package-lock.json` 등 추적 파일이 바뀌지 않았는지 확인한다. 다르면 차이를 보여 주고 멈춘다. `npm ci`로 잠금 파일대로 설치하고 빌드 입력(커밋·잠금 파일 SHA-256·Node·npm)을 `.lazyforensic-build.json`에 기록한다.
- SQLite WAL을 SQLite가 열 수 있는지와 별개로 프레임 경계·salt·체크섬으로 검사한다. 마지막 프레임이 잘렸거나 체크섬이 맞지 않거나 커밋 표시 없는 꼬리 프레임이 있으면 보이는 행 수(`visible_rows`)와 최종 행 수 미확인을 함께 적고, 사유를 SQLite 단계·영수증·보고서 본문에 남긴다.
- SQLite 조사가 증거 열거 규칙이 허용한 `-wal`·`-journal`만 읽는다. 링크 등 범위 밖 부수 파일은 열지 않고 `out_of_scope_sidecars`로 남기며 그 DB의 행 수는 미확인으로 둔다. 읽은 부수 파일마다 인벤토리 항목과 SHA-256을 기록한다.
- 키워드 검색이 줄마다 UTF-8·CP949를 다시 판정해 한 파일에 두 인코딩이 섞여도 두 표식을 모두 찾는다(`mixed`, 코덱 구간 기록). 해석하지 못한 줄은 검색하지 않은 구간으로 적고, 디코딩은 되지만 제어 문자가 많은 바이너리 기록은 '텍스트 아님'으로 따로 세어 검색 수에서 뺀다.
- 보고서·`timeline --manifest`·양식이 같은 정수 나노초 기준으로 시간순을 정한다. `timeline --manifest` 출력에 `mtime_ns`를 싣고, EXIF는 SubSecTimeOriginal 원문 9자리까지(더 긴 자릿수는 원문 보존) 비교한다. 기록된 정밀도로 선후를 가릴 수 없는 이웃은 `order_tie`·양식의 '(동률)'로 표시한다.
- 참조 해시 대조가 원문 전체 경로 → NFC로 맞춘 전체 경로 → 파일명 순으로 후보를 찾는다. 다른 폴더의 같은 이름 파일보다 정규형만 다른 같은 경로를 먼저 고르고, 정규화한 경로가 겹치면 후보 경로와 함께 귀속을 보류한다.
- EXIF UTC 오프셋을 유효·미기록·형식 오류 세 상태(`offset_status`)로 구별하고 원문을 events.json·선조사·양식·본문에 남긴다. 형식 오류는 미기록과 따로 적고 정렬에서만 KST를 가정했음을 표시한다.
- SQLite 본체 옆에 `-wal`·`-journal`이 있으면 원본은 그대로 두고 임시 작업 사본에서 커밋된 상태를 읽는다. 본체와 부수 파일의 SHA-256, WAL 프레임 범위를 기록하고 반영하지 못하면 행 수를 미확인으로 둔다. 총 행 수에서 뷰가 돌려주는 행을 빼고 따로 센다.
- 키워드 검색이 BOM·XML/HTML 선언·엄격한 UTF-8·CP949 순으로 인코딩을 정해 CP949·UTF-16 한글 문서에서도 찾는다. 해석할 수 없는 파일은 '인코딩 판정 불가'로 따로 세고 보고서 본문에 검색 수와 함께 적는다.
- EXIF 촬영 시각에 SubSecTimeOriginal·OffsetTimeOriginal을 붙여 실제 순간으로 정렬하고 events.json·양식에 그대로 싣는다. 오프셋이 없는 사진만 KST 가정을 표시한다. EXIF 문자열은 원자료에 전체를 남기고 본문 발췌에는 원래 길이를 적는다.
- 파일 수정 시각을 정수 나노초(`st_mtime_ns`)로 환산·정렬해 KST 자정 직전 파일이 다음 날로 넘어가거나 같은 초 안의 순서가 뒤바뀌지 않게 한다. CSV·본문·양식은 나노초까지, events.json timestamp는 마이크로초까지 싣고 생략한 정밀도를 본문에 밝힌다.
- 참조 해시 목록과 증거 파일명이 한글 유니코드 정규형(NFC/NFD)만 다르면 후보가 하나일 때만 대조하고 방식을 기록한다. 명령의 JSON 표준 출력은 콘솔 인코딩이 표현하지 못하는 문자를 `\u` 이스케이프로 내보내 CP949 파이프에서도 원래 경로가 복원된다.
- 이미 결과가 있는 출력 폴더를 지정하면 덮어쓰지 않고 새 폴더(`결과_2`)에 쓴다. 이전 사건의 양식·영수증이 다음 사건의 결과 묶음에 섞이지 않으며, 실행마다 `산출물목록.json`에 만든 파일과 SHA-256을 기록한다.
- 보고서·선조사·매니페스트가 같은 열거 규칙을 쓴다. 링크와 일반 파일이 아닌 항목은 해시·키워드 검색 등 모든 단계에서 제외하고 이유를 남긴다. 열지 못한 폴더는 `scope`에 기록하고 전체 파일 수를 미확인, 조사 범위를 부분 완료로 표시한다(종료 코드 1).
- 참조 해시 목록의 SHA-256·크기와 데이터 행별 채택·거절(줄 번호·사유) 수를 기록한다. 참조에 적힌 경로와 다른 위치의 같은 이름 파일은 해시가 같아도 "해시 일치·경로 미확인"으로 표시하고, 거절한 행이 있으면 전체 일치로 표시하지 않는다.
- 분석 방법 절은 EWF 이미지마다 실제로 대조한 알고리즘만 적는다. 판독하지 못한 이미지는 본문과 검증표 모두 미수행으로 표시한다.
- 압축 파일·SQLite 후보 중 판독하지 못한 파일을 따로 센다. 빈 ZIP은 "판독 불가", SQLite 매직이 없는 DB는 "무결성 검사 미수행"으로 표시한다.
- HTML 초안의 표 셀을 Word·한글 초안과 같이 문자 그대로 싣는다. 백틱이 든 파일명이 다른 파일명처럼 보이지 않는다.
- 서증 목록표의 입증취지는 상대 경로 지정이 파일명 지정보다 우선한다. 파일명 지정이 동명 파일 여러 개에 적용되거나 쓰이지 않은 키는 표 아래에 밝힌다.
- `timeline --plaso`의 요약(`--report`)에 입력·채택·탈락 레코드 수와 탈락 사유를 남긴다. Dissect 래퍼는 `limit`과 상한 뒤 레코드가 더 있었는지(`truncated`)를 출력한다.
- `manifest -o`와 `scan -o/--markdown`은 증거 폴더 안 출력 경로를 파일 생성 전에 거부한다.

## v1.6.2

- Windows 설치 경로에 한글이 있어도 콘솔 코드 페이지와 관계없이 `lazyforensic`이 설치된 프로그램을 찾도록 실행 파일의 상대 경로를 사용한다.
- Windows에서 출력을 파이프나 파일로 받을 때 코드 페이지에 없는 문자가 있어도 멈추지 않는다(`PYTHONIOENCODING=:replace`, 사용자가 설정한 값은 유지).

## v1.6.1

- 무결성 표는 모든 참조 해시를 대조한 경우에만 전체 일치를 표시한다. 전후 재해시 결과에는 두 시점에서 측정한 값의 일치 여부를 기록한다.
- Word 양식의 머리말·꼬리말·주석·일반 콘텐츠 컨트롤 셀과 그림 대체 텍스트를 검사한다. 양식 대조값에 문단과 셀 경계 및 병합 열 위치를 포함하며 이름을 바꾼 양식도 대조 파일을 요구한다.
- 한글 양식의 텍스트 미리보기를 갱신하고 실제 본문과 비교한다. CDATA 자리표시자에도 특수문자가 포함된 측정값을 입력할 수 있다.
- 위험한 압축 경로·심볼릭 링크·DTD와 과도하게 큰 XML을 거부한다. 양식은 파일명의 제어 문자를 구별 가능한 문자열로 표시한다. [양식 사용법](docs/REPORT_TEMPLATES.md)

## v1.6.0

- 한글 HWPX 양식에도 DOCX와 같은 자리표시자를 사용할 수 있다. `--template 양식.hwpx`로 새 표를 삽입하거나 기존 표의 데이터 행을 항목별로 반복한다. [빈 한글 양식](templates/report_form.hwpx)
- 검증기는 저장된 HWPX의 섹션과 중첩 표 셀을 원본 양식·측정값에서 계산한 SHA-256과 대조한다. 초안 판정과 실패 사유는 `초안검증.json`의 `template`에 기록한다. [작성 및 재검증 방법](docs/REPORT_TEMPLATES.md)

## v1.5.0

- `lazyforensic report --template 양식.docx`로 사건 정보와 증거·무결성·타임라인 표를 채운 양식 초안을 만든다. 검증기는 저장된 초안의 본문을 예상값과 대조하고 금지 문구, 근거 없는 해시, 남은 자리표시자를 검사한다. [자리표시자 목록과 빈 양식](docs/REPORT_TEMPLATES.md)

---

이 레포의 실제 변경을 기록한다. 형식은 Keep a Changelog 를 따르고, 항목은 커밋 해시로 추적한다.

## v1.4.0

- `lazyforensic report`가 한글 편집용 `보고서초안.hwpx`를 기본 생성한다. A4 초안에 증거 파일별 MD5·SHA-1·SHA-256, 무결성 결과, 타임라인 발췌와 미확인 항목을 싣는다. `--no-hwpx`와 `--no-docx`로 두 형식을 각각 생략할 수 있다.
- 초안 검증기가 HWPX 본문 XML에서 문단과 표 셀을 읽어 Markdown과 대조하고 근거 없는 해시와 금지 문구를 검사한다. 결과는 `초안검증.json`의 `hwpx` 항목에 기록한다.
- 공개 예제의 보관된 측정값으로 HWPX를 비롯한 초안을 다시 생성할 수 있다.

## v1.3.0

- EWF2 Ex01 단일·분할 이미지에서 획득 기록과 저장 MD5·SHA-1을 읽고, 원본·deflate·bzip2·반복 패턴 청크를 복원해 미디어 해시를 대조한다.
- L01·Lx01의 논리 트리를 읽어 경로·크기·시각·저장 해시를 표시한다. `lazyforensic ewf --extract ID --output 경로`로 한 항목을 추출하고 검증할 수 있다. `e01` 별칭을 유지한다.
- 보고서가 EWF 첫 세그먼트를 인식하고 논리 항목별 검증 결과를 무결성 JSON에 기록한다. 일부 항목에만 저장 해시가 있는 경우 부분 검증으로 표시한다.

## v1.2.1

- 참조 해시를 파일 경로와 대조하고 같은 이름의 E01은 경로로 구별한다. 초안 검증은 표에 적힌 MD5·SHA-1·SHA-256과 근거 파일 경로를 함께 확인한다.
- E01의 세그먼트 번호·종료 표지, 섹션 경계, 마지막 청크의 길이·체크섬을 확인한다. 저장 해시끼리 충돌하거나 세그먼트가 누락되면 보고서 명령이 오류를 알린다.
- CP949 초안과 UTF-16 사건 정보 파일을 판독한다. 타임라인 대조에 초와 시간대 차이를 반영하고, 잘린 초안이나 읽을 수 없는 타임라인은 검증 실패로 기록한다.
- DOCX 검증기가 패키지에 지정된 본문을 읽는다. 사건 정보의 줄바꿈으로 표 셀 내용이 유실되던 문제를 수정했다.
- 매니페스트가 중복·불완전한 기록을 거부하고 캐시 디렉터리의 파일도 검사한다. 이전 Windows 매니페스트를 다른 운영체제에서 대조할 수 있다.
- POSIX 설치기가 파일 복사를 확인한 뒤 기존 설치를 교체하고 새 셸 프로필에 명령 경로를 등록한다.

## v1.2.0

- `lazyforensic report`가 A4 편집용 `보고서초안.docx`를 기본 생성한다. `--no-docx`로 기존 형식을 선택할 수 있다.
- Word 출력 시 세 초안의 증거 목록에 MD5·SHA-1·SHA-256을 함께 싣는다. 저장된 DOCX 본문을 Markdown과 대조하고 기존 해시·금지 문구 검증을 적용한다.
- 열기 실패한 파일을 성공한 재해시 건수에서 제외한다. 해시 목록과 검증기가 공백이 있는 증거 파일명을 온전히 대조한다.

### Added — 에이전트 없이 쓰는 보고서 초안·E01 검증·한 줄 설치 (1.1.0)

- `scripts/report_draft.py` (`lazyforensic report <증거폴더>`): 증거 폴더 하나로 한국어 분석 보고서 초안(`보고서초안.md`/`.html`)과 무결성 검증 결과를 만든다. 전체 파일 MD5·SHA-1·SHA-256, E01 저장 해시 재계산 대조, 참조 해시 대조(`--hashes`), 선조사(시그니처·중복·EXIF·압축·PDF·SQLite·개인정보 패턴), 타임라인(mtime KST 환산·EXIF 시간대 미기록·E01 획득 시각), 분석 전후 재해시, 작성한 초안의 `verify_report.py` 검증까지 한 번에 수행. 사건 정보는 `--case-info`(양식: `--case-template`), 없으면 "미확인". 증거 폴더 안 출력은 거부하고, 출력물에 분석 PC 절대 경로를 남기지 않는다. 종료 코드 1 = 해시 불일치·판독 오류·초안 FAIL.
- `scripts/ewf_image.py` (`lazyforensic e01`): 표준 라이브러리만으로 EWF-E01(단일·분할 세그먼트, deflate/비압축) 판독 — 헤더 기록값(사건번호·검사자·획득 도구·OS·일시), 미디어 정보, 저장 MD5/SHA-1, 미디어 전체 MD5·SHA-1·SHA-256 재계산과 대조. Ex01/L01/S01·손상 체인·세그먼트 누락은 '미지원/판독 오류'로 끝낸다. libewf `ewfverify`와 공개 이미지 4개에서 결과 일치 확인.
- `scripts/hash_reference.py`: 참조 해시 목록 판독 — `sha256sum`/`md5sum`, BSD `SHA1 (f) = h`, `이름 크기 해시` 표, 머리행 있는 CSV, `Filename:`/`SHA1:` 블록. 한 줄에 해시나 파일명이 둘 이상이면 버리고, 같은 이름이 여럿이면 대조를 보류한다.
- `install.ps1` / `install.sh`: Windows `irm … | iex`, macOS/Linux `curl … | sh` 한 줄 설치(오프라인 설치 폴더 실행 지원). Python 3.10+ 확인(Windows는 winget, macOS는 Homebrew로 자동 설치 시도), `~/.lfenv` venv + Pillow, `lazyforensic` 명령 PATH 등록.
- `docs/QUICKSTART.md`(동료용 한국어 빠른 시작), `examples/nps-2009-canon2/`(Digital Corpora 공개 표본으로 돌린 명령과 결과 전체), `templates/case_info_example.txt`.
- `bin/lazyforensic`·`bin/lazyforensic.ps1`: `report`, `e01` 서브커맨드. `.ps1`은 Windows PowerShell 5.1에서 한글이 깨지지 않도록 UTF-8 BOM으로 저장.
- `scripts/case_survey.py`: `survey(..., manifest=)`로 호출자가 계산한 매니페스트를 재사용(같은 파일 이중 해시 방지).
- CI: macOS 러너와 Python 3.10 최소 버전 잡 추가, 설치 스크립트 → 새 셸 → `lazyforensic report` 스모크(Windows PowerShell 5.1·macOS·Linux, 한글·공백 경로) 추가.
- `test/test_report_draft.py` + `test/fixtures/ewf/`(libewf로 획득한 합성 E01, 실제 데이터 없음).

### Removed — 미사용 카카오 DB 래퍼 정리 및 antigravity-tools 문서 위치 이전 (1.0.4)

- `skills/kakao-db-decryptor`: 복호화 기능을 제공하지 않고 안내만 담고 있던 래퍼 스킬 삭제. 카카오톡 SQLite 분석은 `lazyothers` 플러그인의 `sqlite-evidence-query` 스킬로 안내하도록 `GEMINI.md`, `README.md`, `skills/lazyforensic/SKILL.md`, `docs/GAPS.md`, `docs/USER_GUIDE.md` 갱신.
- `docs/antigravity-tools.md`: 스킬 루트에 위치하던 `skills/references/antigravity-tools.md` 문서를 `docs/` 디렉터리로 이전(`git mv`)하고 관련 참조 경로 수정.
- `test/test_gemini_catalog.py`: 노출 스킬 수 상한(`VISIBLE_SKILL_CAP`) 18개로 조정.

### Changed — Claude Opus/Sonnet 5.5 및 Gemini 3.8 Flash 세션 모델 연동 대응 (1.0.3)

- `plugin.json`: Gemini 3.8 Flash 외 Claude Opus 5.5 / Sonnet 5.5 세션 모델 지원 명시 및 버전 패치 갱신 (1.0.3).
- `GEMINI.md`: 세션 UI 고정 지침을 '사용자가 선택한 세션 모델을 따른다'로 갱신. 서브에이전트 검증 레인은 Gemini 세션 시 `pro`, Claude 5.5 세션 시 `inherit` 사용 규칙 명시.
- `skills/references/antigravity-tools.md`: Antigravity 세션 모델 중립화 및 검증 레인 모델 힌트 조건 분기 반영.
- `skills/lazyforensic`, `forensic-video`, `forensic-timeline`, `kakao-chat-extractor`: 서브에이전트 모델 힌트에 Claude 5.5 세션 `inherit` 및 Gemini 세션 `pro` 가이드 반영.
- `skills/forensic-video/scripts/frames.py`: 비전 분석 docstring 모델 표기에 Claude Opus/Sonnet 5.5 추가.

### Added — lazyothers 브릿지·DLP 정리·Windows CLI

- `scripts/evidence_export.py`: manifest/survey JSON → lazyothers `evidence.json` (`generate_evidence_doc.py`/`bind_court_pdf.py` 호환).
- `scripts/dlp_log_table.py`: 확보된 로그 CSV를 4축 체크리스트 표로 재배열 (탐지 아님).
- `bin/lazyforensic.ps1`/`lazyforensic.cmd`: Windows 네이티브 CLI 래퍼.
- `scripts/py`: `~/.lfenv` venv를 자동 해석하는 python 리졸버.

### Fixed — archive_survey 압축폭탄 검증 2중 무력화

- tar 계열이 `comp_size=0` 하드코딩으로 폭탄 플래그를 영구 우회하던 결함 수정 — 컨테이너 파일 크기를 대리값으로 사용.
- 멤버 해시가 전체 복호(`f.read()`)를 선행해 폭탄이 감사기를 OOM으로 죽이던 자기-DoS 수정 — 64MB 읽기 상한 + 증분 해시, 초과 시 `읽기상한(해시생략)` 플래그.
- `setup_korean_law.mjs`가 korean-law-mcp를 main HEAD에서 무보정 clone하던 것을 커밋 핀으로 변경 (`KOREAN_LAW_MCP_REF`로 재지정 가능).
- `setup_forensic_env.py`의 `venv.create()` 무보호 실패를 안내 메시지 + exit 1로 변경.

### Changed — session default Gemini 3.8 Flash

- Plugin description, GEMINI.md, README, and Antigravity tool mapping now treat Gemini 3.8 Flash (High) as the session default. Verify lanes still use `Model: "pro"`.

### Fixed — Antigravity hook matchers and High-Fidelity branding

- `hooks.json` PreToolUse/PostToolUse matchers now cover `write_file`/`WriteToFile`/`MultiEdit`/`SearchReplace`/`Bash`/`Shell` so Antigravity write/shell tools cannot skip FAIL_CLOSED guards.
- `--high-fidelity` help and docs describe a local evidence-tag/morpheme gate. No Vertex API call.

### Added — Section 5.1 #3 한국사 사건/조약/학술논문 날조 차단 & Section 5.1 #4 불가능한 사법절차 차단 & Section 7-8 포렌식 헬스체크 (100점 감사)

- `scripts/verify_report.py`:
  * **Section 5.1 #3 한국사 사건 및 조약 날조 차단**: 한자 숫자(`四`, `五`, `二` 등) 및 한자 접두사(`第`), 한자 사건명(`甲午改革`, `乙巳條約`, `東學農民`, `壬辰倭亂` 등) 지원 및 단일 발생 사건/개혁(`을미개혁`, `을미사변`, `한일병합조약`, `조미수호통상조약`, `한일기본조약`, `남북기본합의서`, `4·19 혁명`, `5·18 민주화운동`, `6월 민주항쟁`) 2차 이상 차수 날조 전수 차단.
  * **Section 5.1 #3 학술 논문 및 저널 인용 날조 차단**: 실존하지 않는 가짜 학술지/논문집(`대한인공지능법학회지`, `한국사이버포렌식학회논문집`, `한국디지털증거법학회지` 등) 및 미래 연도(> 2026년) 학술 논문 인용 날조를 기계적으로 차단.
  * **Section 5.1 #4 불가능한 사법 절차 날조 차단**: 대한민국 실정법(형사소송법 제448조, 제246조, 헌법 제12조, 제111조 등)상 성립할 수 없는 사법 절차(`대검찰청의 약식명령 청구`, `고등검찰청의 약식기소`, `경찰의 영장 직접 청구`, `경찰의 직접 기소/공소제기`, `헌법재판소의 징역형 선고`, `민사소송에서의 징역형 선고` 등)를 문장 내 장문 복문 수식어(25자 이상)와 상관없이 탐지하며, 상급 검찰청의 합법적인 수사 지휘/검토 지시 문맥은 허용하도록 음성 룩어헤드 완비.
  * **Section 7-8 종합 헬스체크 CLI (`--health-check`)**: 금지문구, 해시 그라운딩, 조문 상한, 판례 연도, 공공기관 및 학술지 명칭, 역사 사건 차수(한자 차수 포함), 사법 절차(장문 복문 포함), Evidence-First 프로토콜 전수 10대 카테고리 기계적 테스트 및 100점 만점 판정 시스템 신설.
- `skills/forensic-report/SKILL.md` 및 `skills/report-guard/SKILL.md`: Section 5.1 #3 & #4 환각 방어 프로토콜 및 `--health-check` 가이드 반영, 트리거 동기화.
- `test/test_hallucination_guard.py`: 역사 사건 날조 차단, 한자 차수/단일 사건 차단, 사법 절차 날조 차단(장문 복문), 학술 인용 날조 차단, 종합 헬스체크 100점 검증 테스트 추가 (총 22개 전수 통과).

### Added — Section 5.1 #2 공공기관 및 폐지 부처 명칭 날조 차단 & Section 6 포렌식 Claim Ledger 프로토콜

- `scripts/verify_report.py`: 가짜 수사/포렌식 기관(`FABRICATED_AGENCY_RE`, e.g. 사이버수사처, 디지털포렌식청, 개인정보보호청 등) 및 폐지된 25개 구 정부 부처명(`ABOLISHED_GOV_AGENCIES`, e.g. 정보통신부, 문화공보부, 재정경제부 등) 인용을 한국어 조사/접미사(`와`, `과`, `도`, `만`, `부터`, `까지` 등) 전수 경계 검사로 탐지. 현행 승계 부처 명칭 병기 시 경고 처리 및 `--allow-historical` 플래그 지원.
- `scripts/verify_claim_ledger.py`: Section 6 Claim Ledger 규약 검증기 신설 — 2개 이상의 독립 도메인(IPv4 직접 지원) 또는 2개 이상의 독립 포렌식 아티팩트/해시(`.pf`, `.reg`, `.dat`, `.sqlite`, `.db`, `$MFT`, `$LogFile` 등 확장 아티팩트 지원), 서문/메타데이터 테이블이 선행하는 마크다운 문서 파싱 강건화, 명시적 반대 가설/반증 검색(Counter-Search) 결과 기록, 1차 증거 출처 강제, 보고서 본문 인용 잠금(`[Claim X]`는 `VERIFIED` 상태만 인용 허용) 검증.
- `scripts/hallucination_guard.mjs`: `claim-ledger.md` / `claim_ledger.md` 자동 탐지 시 `--claim-ledger` 연동 검증 자동 실행.
- `scripts/verify_report.py --claim-ledger <path>`: 감정보고서 검증 시 원장 일치성 연계 검증 지원.
- 단위 테스트 신설 및 보강: `test/test_verify_forensic_claim_ledger.py` 신설(8개), `test/test_forensic_scripts.py`에 날조 기관 조사 결합/폐지 부처 역사적 병기/원장 연동 테스트 추가.

### Added — Guard Pack (GUARD_PACK_VERSION 1.0.0) 및 통합 불변식

- `scripts/markdown_structure_guard.mjs` 신설 + `hooks.json` PostToolUse(문서 쓰기·bash 리다이렉트)에 FAIL_CLOSED 배선: 생성 중 스트리핑(빈 링크 `[](`, 빈 불릿 `-  : `, 빈 강조, 고아 `$수식`, 미닫힘 코드펜스, 표 열 불일치)을 쓰기 직후 탐지해 재작성 강제. `--check` 일괄 모드는 CI 단계로도 편입. NTFS 속성명(`$MFT` 등)·인라인 코드 내 `$`는 오탐 제외.
- `test/test_integration_invariants.py` 신설(레포 전체, 7개): (1) 모든 `skills/*/SKILL.md`가 GEMINI.md에 라우팅 등록돼 있는지(예외 목록 명시), (2) GEMINI.md가 참조하는 경로 실존, (3) Forensic 레인 헤더 수-표 행 수 일치, (4) 문서화된 python 명령의 스크립트 실존, (5) 문서화된 CLI 플래그의 스크립트 등록(--plot-fft 사례 재발 방지, 25개 문서화 명령 대상), (6) skills 트리 스트리핑 흔적 0건, (7) GAPS.md 정직 문서 유지.
- CI 개선: `unittest discover` → `pytest`(기존 러너는 함수형 테스트를 수집하지 못해 CI가 절반만 검증하고 있었다), markdown guard CI 단계 추가.
- `scripts/coverage_audit.mjs` 신설 (GUARD_PACK_VERSION 1.0.0) + pytest 6개(`test/test_coverage_audit.py`) + GEMINI.md 계약 규칙: "전수/100% 커버리지" 주장은 `--source` 원문 파일 기반 감사 수신증으로만 뒷받침한다. 원문 부재 시 실행 자체를 거부(exit 2)해 순환 감사를 구조적으로 차단하고, 수신증에 항목별 원문 행 → 산출 위치 매핑을 남긴다. 강한 키(숫자 포함·6자 이상) 우선 매칭으로 산출물 제목 오염에도 정확 판정. 표 헤더 행은 항목에서 제외. 딥페이크 사건 재현 픽스처에서 누락 항목(L8 Mochi, L9 Sora)을 정확히 지목하는 것까지 검증.
- `scripts/sync_guard_pack.sh` 신설 — 캐노니컬 가드(lazyforensic) → lazyantigravity/lazyothers 동기화.
- 리뷰 발견 즉시 수정: `02`의 `$1/f^\alpha$` 수식 잔존 손상 복원, `skills/lazyforensic/SKILL.md` 표 4행 열 불일치 복원, `ai-trace-detector`가 GEMINI.md/README에 미등록이던 통합 누락 등록(15레인).
- 훅 배선 테스트 강화: markdown guard의 문서 쓰기·bash 양쪽 FAIL_CLOSED 배선까지 검증.

### Added — deepfake-forensic-radar 통합 보강 (335b1c6 후속 정비)

- `GEMINI.md` Forensic 라우팅 테이블에 `deepfake-forensic-radar` 등록(13→14) — 스킬 추가 직후 라우팅 문서가 갱신되지 않아 호스트가 딥페이크 요청을 라우팅하지 못하던 통합 누락 수정
- `README.md` 지원 표에 딥페이크 1차 감사 행 추가
- `test_deepfake_forensic_radar.py` 보강: JPEG APP11/MP4 c2pa 박스 감지, SKILL.md에 문서화된 CLI 플래그 실존 검증(문서-구현 드리프트 가드), 레퍼런스 파일 스트리핑 가드(빈 불릿/빈 링크 텍스트 탐지), GEMINI.md 라우팅 행 존재 검증

### Fixed — deepfake-forensic-radar 레퍼런스·스크립트 결함

- `01_generation_synthesis.md`: ComfyUI 13대 노드의 저장소명이 전부 공란으로 스트리핑된 결함 복원(13개 링크), 원문 누락 모델 14종 보충(Sora, Vidu, SimSwap, Ghost, XTTS-v2, StyleTTS2, Mini-Omni, VoiceCraft, V-Express, AniPortrait, Champ, MimicMotion, AnimateAnyone, LivePortrait-Realtime) 및 가중치 파일명(`inswapper_128.onnx` 등) 복원
- `02_detection_forensics.md`: 누락 탐지기 6종 보충(FreqNet, LGrad, RECCE, SBI, VIGIL, CNNDetection), 벤치마크 데이터셋 9종 섹션 신설, 출처 없는 정량 AUC 그리드(예: DIRE GenImage 97.8%)를 근거 있는 수치(UnivFD 92.3%, NPR 91.4%) + 정성 평가표로 교체 — 근거-결론 분리 원칙 정합화
- `03_c2pa_watermarks.md`: 스트리핑된 필드명(`c2pa.actions`, `c2pa.hash.data`)과 도구 링크 텍스트(c2pa-rs, c2pa-js, stable_signature, tree-ring-watermark) 복원
- `04_legal_and_court.md`: " 타임스탬프" 공란 → 전자소송(ECFS) 이력·파일시스템 타임스탬프 명시
- `05_evasion_and_limits.md`: 메신저 전송 방식 항목의 플랫폼명(Telegram 사진 모드/파일 모드) 복원
- `analyze_deepfake_evidence.py`: SKILL.md에만 문서화되고 구현되지 않았던 `--plot-fft` 플래그 구현(matplotlib 선택 의존, 실패 시 exit 2), 비디오 컨테이너 FFT를 ffmpeg 1프레임 추출로 지원, 항상 0.0이던 죽은 필드 `high_freq_anomaly_score` 제거, "해시 일치 시 법적 증거능력 인정" 과단정 문구를 무결성 확인서 수준으로 교정, MD5/SHA-1 충돌 취약성 주석 추가, 미사용 `math` import 제거

### Fixed — 외부 리뷰 후속 수정 (검증 게이트)

2026-08-30 외부 코드 리뷰(보안 7/10, 종합 7.5/10)에서 확인된 결함 수리. 테스트는 guard pack 추가분과 합산해 최종 96개.

### Fixed — 검증 게이트 (P1)

- `hooks.json` PostToolUse 매처에 `write|edit|apply_patch` 추가 — 호스트 Write/Edit 경로로 수정한 보고서는 검증 게이트를 아예 통과하지 않던 배선 불일치 (PreToolUse와 정렬)
- `verify_report.py`: 보고서에 해시가 있는데 `--evidence`가 없으면 WARN(통과) 대신 **FAIL**로 격상 — '감사 생략 + 조작 해시'라는 핵심 위협이 WARN으로 게이트를 통과하던 구멍. README의 "근거 없는 해시 → FAIL" 주장이 이제 무조건 참

### Fixed — 검증 게이트 (P2)

- `verify_report.py`: 줄바꿈·공백으로 분할된 64hex 해시 검출(표 정렬·하드랩 우회 차단), "오전 9시 30분" 식 한국어 시각 인식(이전은 날짜만 추출해 거짓 시각이 grounding을 통과), 콜론식 시각 병기 유지
- `generate_timeline.py` UTF-8 stdio 누락 보강 — e3fa4e6("all CLI scripts")에서 이 스크립트만 빠져 있었다
- evidence_guard: 읽기전용 검사 명령(sha256sum/파서 실행 등)의 증거 '읽기' 허용 — 가드가 evidence/ 관례를 권하면서 감사 워크플로 자체를 막던 모순 해소. 쓰기 신호(리다이렉트·cp·mv·tee·dd·sed -i)와 `python -c` 인라인 코드는 여전히 차단
- evidence_guard 보호 확장자에 .mem/.vmem/.img/.l01/.ad1/.aff/.aff4/.vmdk/.vhd 추가


## [1.0.1] — 2026-08-29

2026-08 종합 리뷰(문서·정직성 9/10, korean-law-mcp 8/10, 스킬 6/10, 스크립트·훅 4/10, 패키징 3/10)에서 발견된 결함을 수리한 시리즈. 리뷰에서 "치명적"으로 분류된 5가지가 모두 해소됐다.

### Fixed — 검증 체인 (플래그십)

- `audit_timestamps.py --json` 플래그 추가 — 문서화된 체인(`--json > audit.json` → `verify_report.py --evidence`)이 처음으로 실제로 순환한다 (422679c)
- `verify_report.py`: UTF-16/CP949 자동 감지(PowerShell Out-File 입력 포함), 금지문구 부정 문맥 허용("유출의심 아님" 오탐 제거), 라벨 붙은 MD5/SHA-1 grounding, 한국식 날짜 형식 인식, 타임라인 검사 전역→줄 단위, `제X조` 인용 추출 경고 (7db94f5)
- 검증 게이트가 `.lazyforensic/audit_trail.jsonl`(이 플러그인 자신의 감사 로그)을 근거 파일로 조회 — 두 파이프라인 연결 (7db94f5)

### Fixed — 훅 배관 (Linux/Windows 에서 FAIL_CLOSED 가 죽어 있던 문제)

- stdin JSON을 `fstatSync().size` 판정 대신 1.5초 데드라인 비동기 읽기로 교체 — POSIX 파이프에서 size가 항상 0이어 두 가드 모두 아무것도 검사하지 않던 치명 버그 (7db94f5)
- 경로 추출을 `\S` 기반으로 — ASCII `\w` 정규식이 한글/공백 파일명을 놓쳐 한글 보고서가 검증을 스킵하던 문제 (7db94f5)
- JSON 소스 독립 파싱, `\/` 이스케이프 우회 차단, `py -3`/`python`/`python3` 탐색 + Windows Store 별칭 오탐 배제, exit 규약 문서화 (7db94f5)
- evidence_guard: 보고서 본문(content) 스캔 제거로 과차단 해소, `cp x evidence/` 상대 경로 차단 추가, 감사 로그 이중 미러 폐지, 2GiB 초과는 `sha256: null` 명시 기록, 스트리밍 해시 (7db94f5)
- hooks.json PostToolUse에 bash 매처 추가 — `python gen.py > 보고서.md` 우회 경로도 동일 게이트 (7db94f5)

### Fixed — 포렌식 데이터 경로

- `parse_kakao.py` 재작성: 실제 PC "대화내용 저장" 포맷(`===` 헤더 + `[오후 2:15] 닉네임 : 메시지`) 지원 — 구버전은 합성 포맷만 읽어 실제 PC 내보내기가 0건으로 파싱됨. 첨부 판정을 확정 표기 전용으로("사진 좀 보내줘"가 `has_attachment:true`로 위장하던 결함), 시스템 이벤트/인식 불가 행을 `type` 레코드로 보존, CP949 지원, 콜론 포함 발신자 분할 수정 (422679c)
- `parse_ntfs_artifacts.py`: 파싱 실패가 "레코드 0건 + exit 0"으로 위장하던 결함 → `error` 필드 + exit 3, stdout JSON 전용화 (422679c)
- `generate_timeline.py`: 에러 메시지에만 존재하던 `--max-events` 플래그 실제 추가 (422679c)
- `download_dfir_binaries.ps1`: 중첩 폴더로 인한 무한 재다운로드 수정, 자산 미매칭 무음→경고 (422679c)
- `kakao-db-decryptor` SQL 예시 epoch 밀리초(`/1000`) 수정, `forensic_video_audit.py` POSIX ctime 라벨 수정, 감정서 템플릿에 Chain of Custody 섹션 추가 (422679c)
- 영상 스킬 `setup.py --check`가 무출력으로 exit 2 하던 UX 결함 — 리포트+설치 명령 출력 후 종료 코드 반환 (34fb062)

### Fixed — Windows 전반

- 한국어 출력 스크립트 전반이 cp1252/cp949 콘솔에서 `UnicodeEncodeError`로 크래시 — CI windows-latest에서 실제 발견, `sys.stdout/stderr.reconfigure(utf-8)`을 CLI 전체에 적용 (e3fa4e6)

### Added

- `scripts/check_tool.py` BYO 게이트 — 스킬 3종(EVTX/MFT/Memory)이 명령 생성 전 도구 존재를 실행으로 확인 (422679c)
- `parse_kakao.py --events-out` — 파싱→타임라인 수동 변환 단계 제거. 시각 미상·unparsed 레코드는 근거 없는 시각이므로 의도적으로 제외 (c898df2)
- GitHub Actions CI — Ubuntu/Windows 유닛 테스트 + stdin 파이프 가드 단언, 노드 문법 체크, korean-law-mcp 빌드+vitest (3a0c0b6)
- 테스트 35 → 79개: 두 가드의 첫 직접 회귀 테스트(test_guards.py), 실제 PC 포맷/CP949/첨부 오음성/검증 체인 E2E/Windows 인코딩 (7db94f5, 422679c, e3fa4e6, c898df2)

### Removed — 라이선스 미검증 제3자 트리 (b3f08d8)

배포 라이선스 검증을 끝내지 못해 다음을 제거했다. 상세 사유는 `NOTICE`.

- `mengto-skills/` (90MB, 127 스킬 — 라이선스 파일 전무)
- `design-systems/` (74 브랜드 토큰 — 제3자 상표·전용 폰트)
- `vendor/antv-infographic/` (파생 참고 문서 — 라이선스 파일 없음)
- `skills/slopslap/` (upstream `private:true`, 무라이선스)
- 의존 UI 피커 스킬 3종(`ui-studio`, `design-system`, `frontend-ui-ux`) + 카탈로그 인덱서
- korean-law-mcp 잔여물: 4.3MB demo.gif, upstream 세션 노트, `npx` 설치를 광고하던 낡은 `.claude-plugin/`

HTML 뷰어 스타일링은 인라인 CSS로 직접 작성하는 것으로 대체했다.

### Docs

- README 사용자 관점 재작성 — 요구사항/설치 2경로/법령 MCP 셋업(법제처 키 발급, 680MB 빌드)/복붙 검증된 4단계 워크플로/지원-미지원 표/트러블슈팅 (34fb062)
- `GEMINI.md`·`docs/GAPS.md`: "무조건 검증"을 사후 게이트로 정직 서술, 스킵 경로 기록, 스킬 카운트 정정 (b3f08d8)

## [1.0.0] — 2026-08 이전

초기 공개: Antigravity + Gemini용 포렌식 보조 플러그인 4 lanes(help/forensic/visual/legal), korean-law-mcp 번들.
