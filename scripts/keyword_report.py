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
- 파일 전체가 UTF-8이 아니면 줄마다 UTF-8 → CP949로 다시 판정한다. 줄마다 코덱이 다르면
  'mixed'로 표시하고 코덱 구간을 남긴다. 해석하지 못한 줄은 검색하지 않은 구간으로 적고
  그 파일은 부분 검색으로 센다. 한 줄 안에서 코덱이 섞인 경우는 구별하지 못한다.
- 디코딩에 성공해도 제어 문자 비율이 1%를 넘으면 텍스트가 아닌 기록으로 보고
  텍스트 검색 수에서 빼 따로 센다.
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


# 텍스트에 나오지 않는 C0 제어 문자 (탭·줄바꿈·폼피드·ESC 제외). 디코딩에 성공해도 이 비율이 높으면 텍스트가 아니다.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0e-\x1a\x1c-\x1f\x7f]")
NOT_TEXT_RATIO = 0.01


def _runs(numbers: list[int]) -> list[list[int]]:
    """오름차순 줄 번호를 [처음, 끝] 구간으로 묶는다."""
    runs: list[list[int]] = []
    for n in numbers:
        if runs and runs[-1][1] == n - 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return runs


def decode_lines(data: bytes) -> dict | None:
    """줄마다 엄격한 UTF-8 → CP949 순으로 해석한다 (BOM·NUL·선언이 없는 파일 전용).

    한 파일에 UTF-8 줄과 CP949 줄이 섞이면 파일 전체 CP949 해석은 UTF-8 한글을 다른
    글자로 바꿔 '미검출'을 만든다. 줄 단위 판정 결과 코덱이 섞였거나 해석하지 못한 줄이
    있으면 {'lines', 'encoding', 'segments', 'unsearched_lines'}를 돌려준다.
    모든 줄이 한 코덱으로 해석되면 None (파일 단위 판정을 그대로 쓴다).
    """
    lines: list[str] = []
    kinds: list[str] = []
    for raw in data.split(b"\n"):
        if raw.isascii():
            lines.append(raw.decode("ascii"))
            kinds.append("ascii")
            continue
        for codec in ("utf-8", "cp949"):
            try:
                lines.append(raw.decode(codec))
                kinds.append(codec)
                break
            except UnicodeDecodeError:
                continue
        else:
            lines.append("")
            kinds.append("undecodable")
    used = {k for k in kinds if k not in ("ascii", "undecodable")}
    bad = [i + 1 for i, k in enumerate(kinds) if k == "undecodable"]
    if not used or (not bad and len(used) == 1):
        return None
    segments: list[dict] = []
    for i, k in enumerate(kinds):
        if k == "ascii":
            continue
        if segments and segments[-1]["encoding"] == k and segments[-1]["last_line"] == i:
            segments[-1]["last_line"] = i + 1
        else:
            segments.append({"first_line": i + 1, "last_line": i + 1, "encoding": k})
    return {"lines": lines, "encoding": "mixed" if len(used) > 1 else used.pop(),
            "segments": segments, "unsearched_lines": _runs(bad)}


def not_text_reason(text: str) -> str | None:
    """디코딩된 문자열을 텍스트로 볼 수 없는 근거 (제어 문자 비율). 텍스트면 None."""
    controls = len(_CONTROL_RE.findall(text))
    if text and controls > len(text) * NOT_TEXT_RATIO:
        return f"제어 문자 {controls}개 / {len(text)}자 — 텍스트가 아닌 바이너리 기록으로 판단"
    return None


def search_file_result(path: Path, keywords: list[str], context: int) -> dict:
    """{'hits', 'error', 'encoding', 'basis', 'undecodable', 'not_text', 'segments', 'unsearched_lines'}.

    읽기 실패(error)·인코딩 판정 불가(undecodable)·텍스트 아님(not_text)은 '히트 없음'이 아니라
    검색 불가다. 디코딩 성공은 텍스트라는 뜻이 아니다. unsearched_lines는 해석하지 못해
    검색하지 않은 줄 구간, segments는 줄 단위로 판정한 코덱 구간이다.
    """
    empty = {"hits": [], "error": None, "encoding": None, "basis": None, "undecodable": None,
             "not_text": None, "segments": None, "unsearched_lines": []}
    try:
        data = path.read_bytes()
    except OSError as e:
        return {**empty, "error": str(e)}
    text, codec, basis = decode_text(data)
    segments, unsearched = None, []
    if codec in ("cp949", None) and basis in ("strict", "UTF-8·CP949 어느 쪽으로도 해석되지 않음"):
        per_line = decode_lines(data)
        if per_line is not None:
            text = "\n".join(per_line["lines"])
            codec, basis = per_line["encoding"], "per_line"
            segments, unsearched = per_line["segments"], per_line["unsearched_lines"]
    if text is None:
        return {**empty, "undecodable": basis}
    reason = not_text_reason(text)
    if reason:
        return {**empty, "encoding": codec, "basis": basis, "not_text": reason}
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
    return {**empty, "hits": hits, "encoding": codec, "basis": basis,
            "segments": segments, "unsearched_lines": unsearched}


def search_file(path: Path, keywords: list[str], context: int) -> list[dict]:
    result = search_file_result(path, keywords, context)
    if result["error"] is not None:
        raise OSError(result["error"])
    if result["undecodable"] is not None:
        raise ValueError(result["undecodable"])
    if result.get("not_text") is not None:
        raise ValueError(result["not_text"])
    return result["hits"]


def search_files(files: list[Path], keywords: list[str], context: int) -> dict:
    """텍스트 확장자 파일을 검색하고 검색 범위를 분류한다 (CLI·선조사 공통).

    searched만 검색 분모다. read_errors·undecodable·not_text는 검색 불가이고,
    partial은 일부 줄을 해석하지 못해 그 구간을 검색하지 못한 파일이다.
    """
    out: dict = {"searched": [], "read_errors": [], "undecodable": [], "not_text": [],
                 "partial": [], "mixed": [], "encodings": {}, "results": []}
    for f in files:
        res = search_file_result(f, keywords, context)
        if res["error"] is not None:
            out["read_errors"].append({"file": str(f), "error": res["error"][:200]})
            continue
        if res["undecodable"] is not None:
            out["undecodable"].append({"file": str(f), "reason": res["undecodable"]})
            continue
        if res.get("not_text") is not None:
            out["not_text"].append({"file": str(f), "encoding": res["encoding"], "reason": res["not_text"]})
            continue
        out["searched"].append(str(f))
        out["encodings"][res["encoding"]] = out["encodings"].get(res["encoding"], 0) + 1
        if res.get("unsearched_lines"):
            out["partial"].append({"file": str(f), "unsearched_lines": res["unsearched_lines"],
                                   "segments": res["segments"]})
        if res["encoding"] == "mixed":
            out["mixed"].append({"file": str(f), "segments": res["segments"]})
        if res["hits"]:
            row = {"file": str(f), "sha256": sha256_file(f), "encoding": res["encoding"],
                   "encoding_basis": res["basis"], "hit_count": len(res["hits"]), "hits": res["hits"]}
            if res.get("segments"):
                row["encoding_segments"] = res["segments"]
            if res.get("unsearched_lines"):
                row["unsearched_lines"] = res["unsearched_lines"]
            out["results"].append(row)
    out["status"] = ("partial" if out["read_errors"] or out["undecodable"] or out["not_text"] or out["partial"]
                     else "complete")
    return out


def scope_note(result: dict) -> str:
    """검색 결과 요약에 붙일 검색 범위 제한 문구 (보고서·선조사 공통). 없으면 빈 문자열."""
    parts = []
    if result.get("files_not_text"):
        parts.append(f"텍스트 아님(바이너리 기록) {result['files_not_text']}개")
    if result.get("files_partially_searched"):
        parts.append(f"일부 줄 해석 불가(그 구간 검색 안 함) {result['files_partially_searched']}개")
    if result.get("files_mixed_encoding"):
        parts.append(f"줄마다 인코딩이 다른 파일 {result['files_mixed_encoding']}개(줄 단위 해석)")
    return "".join(", " + p for p in parts)


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

    text_files = []
    skipped = []
    for f in files:
        (text_files if f.suffix.lower() in TEXT_EXTS else skipped).append(f)
    found = search_files(text_files, args.keywords, args.context)
    skipped = [str(f) for f in skipped] + [e["file"] for e in found["read_errors"]]
    results = found["results"]
    report = {
        "keywords": args.keywords,
        "scope": str(target),
        "status": found["status"],
        "files_searched": len(found["searched"]),
        "files_skipped_binary": len(skipped),
        "files_read_failed": len(found["read_errors"]),
        "read_errors": found["read_errors"][:50],
        "files_undecodable": len(found["undecodable"]),
        "undecodable": found["undecodable"][:50],
        "files_not_text": len(found["not_text"]),
        "not_text": found["not_text"][:50],
        "files_partially_searched": len(found["partial"]),
        "partially_searched": found["partial"][:50],
        "files_mixed_encoding": len(found["mixed"]),
        "encodings": found["encodings"],
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
        if report["files_not_text"]:
            print(f"주의: 텍스트 확장자지만 텍스트가 아닌 파일 {report['files_not_text']}개 — 텍스트 검색 수에서 제외",
                  file=sys.stderr)
        if report["files_partially_searched"]:
            print(f"주의: 일부 줄을 해석하지 못한 파일 {report['files_partially_searched']}개 — 그 구간은 검색 불가",
                  file=sys.stderr)
        if report["files_mixed_encoding"]:
            print(f"참고: 줄마다 인코딩이 다른 파일 {report['files_mixed_encoding']}개 — 줄 단위로 해석해 검색",
                  file=sys.stderr)
        if report["total_hits"] == 0:
            print("결과: 전수 검색 범위 내 미검출" if report["status"] == "complete"
                  else "결과: 검색한 파일에서 미검출 (검색 불가 파일 제외 — 전수 검색 아님)")

    return 1 if report["total_hits"] == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
