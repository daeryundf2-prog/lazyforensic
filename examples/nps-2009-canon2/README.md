# 공개 표본 시연: Digital Corpora `nps-2009-canon2` (gen6)

`lazyforensic report`를 공개 데이터로 처음부터 끝까지 돌린 기록이다. 실제 사건 데이터는 없다.

## 데이터와 라이선스

- 출처: Digital Corpora, NPS Test Disk Images — `nps-2009-canon2`
  (Canon PowerShot SD800IS로 찍은 32MB SD 카드 이미지와 복구 가능한 JPEG)
  - 목록: <https://digitalcorpora.org/corpora/disk-images/>
  - 파일: <https://digitalcorpora.s3.amazonaws.com/corpora/drives/nps-2009-canon2/>
- 라이선스: Digital Corpora 이용 약관은 사이트의 원본 콘텐츠를 CC0로 제공한다고 밝히고
  (<https://digitalcorpora.org/about-digitalcorpora/terms-of-use/>), Disk Images 페이지는 NPS 테스트 이미지의
  NPS 작성 데이터가 퍼블릭 도메인이며 비공개 개인정보가 없다고 적는다. (2026-11-21 확인)

내려받은 파일의 SHA-256 (2026-11-21):

| 파일 | 크기 (바이트) | SHA-256 |
| :--- | ---: | :--- |
| nps-2009-canon2-gen6.E01 | 31144390 | 10483722d84e0cefcb693b11dea2d32dbd3ad2f06f8c9656688c8c730fe41579 |
| files-gen6.zip | 28930148 | f6fe5113032ba1600c4331cc0defda4e5f9f10384baa1cac0deedce7f9c1e86a |
| narrative.txt | 2614 | 10613f6139d28f06d897fd9d19b2c3c679a1b28848c88f4877542f4c141fed84 |
| report.txt | 9410 | bc4649268c87810d34a53e5022e524a55581b10aa316bb7a2f740ec909cbf953 |

`narrative.txt`(원시 이미지 SHA-1)와 `report.txt`(복구 파일별 SHA-1)는 데이터셋이 함께 공개한 기준 해시라서
`--hashes`로 넘겨 대조했다.

## 실행한 명령 (macOS, 2026-11-21)

```sh
B=https://digitalcorpora.s3.amazonaws.com/corpora/drives/nps-2009-canon2
for f in nps-2009-canon2-gen6.E01 files-gen6.zip narrative.txt report.txt; do curl -fsSLO "$B/$f"; done
mkdir -p evidence/files-gen6
mv nps-2009-canon2-gen6.E01 evidence/
unzip -q files-gen6.zip -d evidence/files-gen6
# case.txt = 이 폴더의 case.txt (시연용 사건 정보)
lazyforensic report evidence --case-info case.txt --hashes narrative.txt report.txt
```

Windows PowerShell에서는 `curl -fsSLO` 대신 `Invoke-WebRequest "$B/$f" -OutFile $f`,
`unzip` 대신 `Expand-Archive files-gen6.zip evidence\files-gen6`를 쓴다.

화면 출력 (결과 폴더의 로컬 절대 경로가 찍히는 `완료:` 두 줄만 뺐다):

```text
LazyForensic 1.1.0 — 보고서 초안 생성 (로컬 전용, 증거 폴더는 읽기만 함)
[1/7] 파일 목록·해시 계산: evidence
      파일 38개
[2/7] E01 이미지 1개 판독·해시 재계산
      nps-2009-canon2-gen6.E01: 일치 (저장 해시 = 재계산 해시)
[3/7] 참조 해시 목록 narrative.txt: 6건 읽음
[3/7] 참조 해시 목록 report.txt: 40건 읽음
[4/7] 선조사 (시그니처·중복·EXIF·압축·PDF·DB·개인정보 패턴 등)
[5/7] 분석 후 재해시
[6/7] 보고서 초안 작성
[7/7] 초안 검증 (verify_report.py)

분석 전후 재해시: 일치 (38개)
E01 nps-2009-canon2-gen6.E01: 일치 (저장 해시 = 재계산 해시)
참조 해시: 일치 35 / 불일치 0 / 증거에 없음 11
초안 검증: PASS
```

## 결과 (`output/`)

- [`보고서초안.md`](output/보고서초안.md) / [`보고서초안.html`](output/보고서초안.html) — 보고서 초안
- [`무결성검증.json`](output/무결성검증.json), [`파일해시목록.csv`](output/파일해시목록.csv), [`타임라인.html`](output/타임라인.html),
  [`events.json`](output/events.json), [`선조사.json`](output/선조사.json), [`초안검증.json`](output/초안검증.json)

결과에서 읽을 점:

- E01에 저장된 MD5와 압축을 풀어 다시 계산한 MD5가 같았다. 다시 계산한 SHA-1
  `4742c325f10583dab1eb4c55d0d45ab3beb99eb3`은 `narrative.txt`에 공개된 `nps-2009-canon2-gen6.raw`의 SHA-1과 같다.
  같은 이미지는 libewf `ewfverify` 20140816으로도 MD5 일치(SUCCESS)를 확인했다.
- 참조 46건 = `narrative.txt` 6건 + `report.txt` 40건. `report.txt`는 34건이 복구 파일과 SHA-1이 같았고, 6건
  (`IMG_0020`, `IMG_0025`, `IMG_0030`, `IMG_0035`, 해시가 다른 `M0100.CTG` 2건)은 `files-gen6.zip`에 없어
  "증거에 없음"으로 나왔다. `narrative.txt`는 gen6 1건이 E01 미디어 해시와 같았고, 다른 세대(gen1~gen5) 5건은 증거에 없다.
- `IMG_0029.JPG`와 `IMG_0039.JPG`는 SHA-256이 같은 파일(정확 중복)로 나왔다. `narrative.txt`는 IMG_0039를
  "G2-3" 사진으로 설명하므로, 공개 zip에 들어 있는 IMG_0039는 설명과 다른 내용일 수 있다 — 도구가 판단하지 않고
  검토 대상으로만 싣는다.
- 분석 환경은 macOS(arm64), Python 3.12, Pillow 11.3. Pillow가 없으면 EXIF·유사 이미지 항목이 "미수행"으로 바뀐다.
