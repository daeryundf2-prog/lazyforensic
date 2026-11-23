#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keyword_report.py — 증거형 키워드 검색 리포트

"해당 키워드가 있는가"를 grep 결과가 아니라 감정서에 붙일 수 있는
형태로 만든다: 파일 경로 + 줄 번호 + 원문 + 검색 대상 파일의 SHA-256.
'없음'도 '검색 안 함'이 아니라 'N개 파일 전수 검색 결과 미검출'로
기록한다 — 부재 증명에 필요한 검색 범위가 보고서에 남는다.

사용:
    python scripts/keyword_report.py evidence/ "보이스피싱" "계좌이체"
    python scripts/keyword_report.py dir/ 키워드 --context 2 --json
    python scripts/keyword_report.py dir/ 키워드 -o report.json

알려진 한계:
- 리터럴 문자열 검색이다. OCR 오류·유사표현·이미지 속 글자는
  잡지 못한다. 유의어 확장 검색은 이 도구의 역할이 아니다.
- 바이너리 파일은 건너뛴다 (읽기 실패 파일은 skipped로 기록).
- 인코딩은 BOM → 파일 안의 선언(encoding=/charset=) → 엄격한 UTF-8 →
  엄격한 CP949 순으로 정한다. 어느 것으로도 오류 없이 해석되지 않으면
  '인코딩 판정 불가'로 따로 세고 검색한 파일 수에 넣지 않는다.
  BOM 없는 UTF-16은 NUL 바이트 위치로만 알아본다. 한글만 든 BOM 없는
  UTF-16처럼 다른 인코딩으로도 해석되는 입력은 구별하지 못한다.
- 줄과 키워드를 NFC로 맞춘 뒤 비교한다 (macOS 등의 NFD 한글 대응).
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_json import print_json  # noqa: E402

TEXT_EXTS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm",
    ".log", ".ini", ".cfg", ".yaml", ".yml", ".py", ".js", ".mjs",
    ".java", ".c", ".cpp", ".h", ".sh", ".ps1", ".sql",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


BOMS = (  # UTF-32 LE BOM이 UTF-16 LE BOM으로 시작하므로 긴 것부터 본다
    (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"),
)
# 선언은 XML 머리말과 HTML meta에서만 읽는다 (소스 코드 속 'encoding=' 문자열은 선언이 아니다).
DECLARED_RE = re.compile(
    rb"""^\s*<\?xml[^>]*?\sencoding\s*=\s*["']([A-Za-z][A-Za-z0-9_.:-]*)"""
    rb"""|<meta\s[^>]*?charset\s*=\s*["']?([A-Za-z][A-Za-z0-9_.:-]*)""", re.IGNORECASE)


def decode_text(data: bytes) -> tuple[str | None, str | None, str]:
    """(본문, 코덱, 판정 근거)를 돌려준다. 본문이 None이면 근거는 판정 불가 사유다.

    치환 문자로 읽어 넘기지 않는다 — 잘못 읽은 파일의 '미검출'은 거짓 음성이 된다.
    """
    for bom, codec in BOMS:
        if data.startswith(bom):
            try:
                return data.decode(codec), codec, "bom"
            except UnicodeDecodeError:
                return None, None, f"{codec} BOM이 있으나 내용이 {codec}로 해석되지 않음"
    if b"\x00" in data:
        even = data[0::2].count(0)
        odd = data[1::2].count(0)
        codec = "utf-16-le" if odd and not even else "utf-16-be" if even and not odd else None
        if codec and len(data) % 2 == 0:
            try:
                return data.decode(codec), codec, "nul_pattern"
            except UnicodeDecodeError:
                pass
        return None, None, "NUL 바이트가 있어 인코딩을 판정할 수 없음"
    declared = DECLARED_RE.search(data[:1024])
    if declared:
        label = (declared.group(1) or declared.group(2)).decode("ascii").lower()
        try:
            codec = codecs.lookup(label).name
        except LookupError:
            return None, None, f"선언된 인코딩 '{label}'을 알 수 없음"
        # 웹 표준처럼 EUC-KR 표기는 그 상위 집합인 CP949로 읽는다.
        codec = "cp949" if codec == "euc_kr" else codec
        try:
            return data.decode(codec), codec, "declared"
        except UnicodeDecodeError:
            return None, None, f"선언된 인코딩 '{label}'로 해석되지 않음"
    for codec in ("utf-8", "cp949"):
        try:
            return data.decode(codec), codec, "strict"
        except UnicodeDecodeError:
            continue
    return None, None, "UTF-8·CP949 어느 쪽으로도 해석되지 않음"


def search_file_result(path: Path, keywords: list[str], context: int) -> dict:
    """{'hits', 'error', 'encoding', 'basis', 'undecodable'}을 반환한다.

    읽기 실패(error)와 인코딩 판정 불가(undecodable)는 '히트 없음'이 아니라 검색 불가다.
    """
    try:
        data = path.read_bytes()
    except OSError as e:
        return {"hits": [], "error": str(e), "encoding": None, "basis": None, "undecodable": None}
    text, codec, basis = decode_text(data)
    if text is None:
        return {"hits": [], "error": None, "encoding": None, "basis": None, "undecodable": basis}
    lines = text.splitlines()
    wanted = [(kw, unicodedata.normalize("NFC", kw)) for kw in keywords]
    hits = []
    for i, line in enumerate(lines):
        normalized = unicodedata.normalize("NFC", line)
        for kw, nfc in wanted:
            if nfc in normalized:
                hit = {
                    "line": i + 1,
                    "keyword": kw,
                    "text": line.strip(),
                }
                if context:
                    lo = max(0, i - context)
                    hi = min(len(lines), i + context + 1)
                    hit["context"] = [
                        {"line": j + 1, "text": lines[j].strip()}
                        for j in range(lo, hi) if j != i
                    ]
                hits.append(hit)
                break  # 같은 줄에서 키워드 여러 개면 한 번만
    return {"hits": hits, "error": None, "encoding": codec, "basis": basis, "undecodable": None}


def search_file(path: Path, keywords: list[str], context: int) -> list[dict]:
    result = search_file_result(path, keywords, context)
    if result["error"] is not None:
        raise OSError(result["error"])
    if result["undecodable"] is not None:
        raise ValueError(result["undecodable"])
    return result["hits"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="증거형 키워드 검색 리포트 (로컬 전용)")
    ap.add_argument("target", help="검색 대상 파일 또는 디렉터리")
    ap.add_argument("keywords", nargs="+", help="검색할 키워드(리터럴)")
    ap.add_argument("--context", type=int, default=0, help="히트 전후 맥락 줄 수")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("-o", "--output", default=None, help="리포트 출력 경로")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if target.is_dir():
        files = sorted(p for p in target.rglob("*") if p.is_file())
    elif target.is_file():
        files = [target]
    else:
        print(f"대상을 찾을 수 없습니다: {target}", file=sys.stderr)
        return 2

    searched, skipped, results = [], [], []
    read_errors, undecodable = [], []
    encodings: dict[str, int] = {}
    for f in files:
        if f.suffix.lower() not in TEXT_EXTS:
            skipped.append(str(f))
            continue
        res = search_file_result(f, args.keywords, args.context)
        if res["error"] is not None:
            skipped.append(str(f))
            read_errors.append({"file": str(f), "error": res["error"][:200]})
            continue
        if res["undecodable"] is not None:
            undecodable.append({"file": str(f), "reason": res["undecodable"]})
            continue
        searched.append(str(f))
        encodings[res["encoding"]] = encodings.get(res["encoding"], 0) + 1
        if res["hits"]:
            results.append({
                "file": str(f),
                "sha256": sha256_file(f),
                "encoding": res["encoding"],
                "encoding_basis": res["basis"],
                "hit_count": len(res["hits"]),
                "hits": res["hits"],
            })

    report = {
        "keywords": args.keywords,
        "scope": str(target),
        "status": "partial" if read_errors or undecodable else "complete",
        "files_searched": len(searched),
        "files_skipped_binary": len(skipped),
        "files_read_failed": len(read_errors),
        "read_errors": read_errors[:50],
        "files_undecodable": len(undecodable),
        "undecodable": undecodable[:50],
        "encodings": encodings,
        "files_with_hits": len(results),
        "total_hits": sum(r["hit_count"] for r in results),
        "results": results,
        "skipped": skipped[:50],
    }

    if args.json or args.output:
        out = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
            print(f"→ {args.output}")
        if args.json:
            print_json(report, indent=2)
    else:
        if results:
            for r in results:
                print(f"{r['file']}  (sha256:{r['sha256'][:16]}…)  {r['hit_count']}히트")
                for h in r["hits"]:
                    print(f"    L{h['line']}  [{h['keyword']}]  {h['text'][:120]}")
        print(f"\n검색 {report['files_searched']}개 파일(바이너리/읽기실패 {report['files_skipped_binary']}개 제외), "
              f"히트 {report['total_hits']}건 / {report['files_with_hits']}개 파일")
        if report["files_read_failed"]:
            print(f"주의: 읽기 실패 {report['files_read_failed']}개 파일 — '미검출'이 아니라 '검색 불가'다", file=sys.stderr)
        if report["files_undecodable"]:
            print(f"주의: 인코딩 판정 불가 {report['files_undecodable']}개 파일 — '미검출'이 아니라 '검색 불가'다",
                  file=sys.stderr)
        if report["total_hits"] == 0:
            print("결과: 전수 검색 범위 내 미검출" if report["status"] == "complete"
                  else "결과: 검색한 파일에서 미검출 (검색 불가 파일 제외 — 전수 검색 아님)")

    return 1 if report["total_hits"] == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
