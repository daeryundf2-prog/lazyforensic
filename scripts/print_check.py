#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
print_check.py — 보고서 인쇄본(PDF)에 글자가 실제로 그려졌는지 확인한다 (로컬 전용).

LazyForensic은 PDF를 직접 만들지 않는다. 분석관이 `보고서초안.docx`를 Word·한글·LibreOffice로
PDF로 바꾼다. 변환하는 PC에 한글 글꼴이 없으면 PDF의 텍스트 층에는 한글이 그대로 남아 복사·검색은
되지만, 종이와 화면에는 한글 자리가 빈칸으로 나온다. 텍스트 추출 성공만으로는 인쇄본을 채택할 수 없다.

이 도구는 텍스트 층과 별개로 독립 렌더러(Poppler pdftoppm)가 그린 화면을 본다.
- 텍스트 층의 모든 단어(pdftotext -bbox-layout) 자리에 잉크가 있어야 한다.
- 한글 음절만으로 된 단어(앞뒤 괄호·문장부호 두 개까지 허용)는 글자마다 잉크가 있어야 한다.
  한글 음절은 전각 폭이라 단어 폭을 글자 수로 나눠 글자 자리를 정한다.
- --docx를 주면 그 문서의 첫 문단(제목)이 1쪽에 있어야 한다. --expect 문자열(파일명·해시 등)은
  어느 쪽이든 있어야 한다. 비교할 때 공백·줄바꿈은 무시한다(표 칸에서 긴 해시가 줄바꿈된다).
- 모든 글꼴이 PDF에 포함(embedded)되어야 한다. 포함되지 않은 글꼴은 여는 PC마다 다르게 그려진다.

종료 코드: 0 통과, 1 실패(빈 글자·누락 문자열·미포함 글꼴), 2 사용법 오류, 3 확인 불가(Poppler 없음·PDF 판독 실패).

알려진 한계:
- 글자 모양이 맞는지(다른 글자로 바뀌었는지)는 보지 않는다. 빈 자리만 찾는다.
- 한글과 영문·숫자가 섞인 단어는 단어 전체의 잉크만 본다.
- 흰 바탕 위 글자를 가정한다. 짙은 바탕은 잉크가 아니라 고른 색으로 보고 빈 자리로 판정한다.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TOOLS = ("pdftotext", "pdftoppm", "pdffonts")
PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', re.S)
EDGE_PUNCT = set("()[]{}「」『』<>“”‘’\"'.,·:;!?~-")
INK_LEVEL = 160    # 이보다 어두운 화소가 있어야 하고
INK_CONTRAST = 60  # 그 자리의 밝기 차가 이만큼 있어야 잉크로 본다(고른 바탕색은 잉크가 아니다)
SHOW = 20


def is_hangul(c: str) -> bool:
    return "\uac00" <= c <= "\ud7a3"


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def run(args: list[str]) -> str:
    proc = subprocess.run(args, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{Path(args[0]).name} 실패: {proc.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return proc.stdout.decode("utf-8", "replace")


def read_pgm(path: Path):
    """P5(8비트 회색) → (폭, 높이, 바이트). pdftoppm -gray 출력만 읽는다."""
    data = path.read_bytes()
    fields, pos = [], 0
    while len(fields) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        fields.append(data[pos:end])
        pos = end
    if fields[0] != b"P5" or int(fields[3]) != 255:
        raise RuntimeError("pdftoppm 회색 출력이 아님")
    width, height = int(fields[1]), int(fields[2])
    return width, height, data[pos + 1:pos + 1 + width * height]


def inked(image, scale: float, box) -> bool:
    width, height, pixels = image
    x0, y0, x1, y1 = box
    left, right = max(0, int(x0 * scale)), min(width, int(x1 * scale + 0.999))
    top, bottom = max(0, int(y0 * scale)), min(height, int(y1 * scale + 0.999))
    darkest, lightest = 255, 0
    for y in range(top, bottom):
        row = pixels[y * width + left:y * width + right]
        if row:
            darkest, lightest = min(darkest, min(row)), max(lightest, max(row))
    return darkest < INK_LEVEL and lightest - darkest >= INK_CONTRAST


def shape(image, scale: float, box, grid: int = 4) -> tuple:
    """글자 자리의 대략적 모양: grid×grid 칸의 평균 어두움을 네 단계로. 대체 글리프(빈 상자 등)는
    글자가 달라도 같은 모양이 된다."""
    width, height, pixels = image
    left, top = int(box[0] * scale), int(box[1] * scale)
    right, bottom = max(left + grid, int(box[2] * scale)), max(top + grid, int(box[3] * scale))
    cells = []
    for gy in range(grid):
        for gx in range(grid):
            xs = range(left + (right - left) * gx // grid, left + (right - left) * (gx + 1) // grid)
            ys = range(top + (bottom - top) * gy // grid, top + (bottom - top) * (gy + 1) // grid)
            values = [255 - pixels[y * width + x] for y in ys if 0 <= y < height for x in xs if 0 <= x < width]
            cells.append((sum(values) // len(values)) // 64 if values else 0)
    return (right - left, bottom - top, *cells)


def substitutes(samples: list[tuple]) -> list[str]:
    """서로 다른 한글 글자가 같은 모양으로 그려진 글자(대체 글리프·빈 상자).

    실제 한글 글꼴은 거친 모양이 같은 글자가 한 무리에 몇 자뿐이다(보고서 한 부 약 250자 중 최대 7자).
    대체 글리프는 모든 글자가 한 모양(화소 위치 차로 두세 무리)이 된다. 세 글자 이상이 같은 모양인 무리 중
    가장 큰 셋이 서로 다른 글자의 절반 이상을 덮을 때만 대체 글리프로 판정한다."""
    chars_by_shape: dict[tuple, set] = {}
    for char, key in samples:
        chars_by_shape.setdefault(key, set()).add(char)
    distinct = {char for char, _ in samples}
    largest = sorted((chars for chars in chars_by_shape.values() if len(chars) >= 3), key=len, reverse=True)[:3]
    shared = set().union(*largest, set())
    return sorted(shared) if len(distinct) >= 5 and len(shared) * 2 >= len(distinct) else []


def hangul_boxes(text: str, box):
    """한글 음절 단어의 글자 자리(가운데 80%). 섞인 단어는 빈 목록."""
    others = [c for c in text if not is_hangul(c)]
    if not any(is_hangul(c) for c in text) or len(others) > 2 or any(c not in EDGE_PUNCT for c in others):
        return []
    x0, y0, x1, y1 = box
    step = (x1 - x0) / len(text)
    return [(c, (x0 + (i + 0.1) * step, y0, x0 + (i + 0.9) * step, y1)) for i, c in enumerate(text) if is_hangul(c)]


def fonts(pdf: Path) -> list[dict]:
    out = []
    lines = run([shutil.which("pdffonts"), str(pdf)]).splitlines()
    for line in lines[2:]:
        parts = line.split()
        # name type… encoding emb sub uni object ID  — 끝에서 다섯째가 emb
        if len(parts) >= 7:
            out.append({"name": parts[0], "embedded": parts[-5] == "yes"})
    return out


def first_paragraph(docx: Path) -> str:
    import report_docx
    for line in report_docx.read_docx(docx).splitlines():
        if line.strip():
            return line.strip()
    return ""


def check(pdf: Path, docx: Path | None = None, expect=(), dpi: int = 150) -> dict:
    result = {"pdf": pdf.name, "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(), "renderer": None,
              "status": "unverified", "pages": 0, "words": 0, "hangul_chars": 0, "blank": [],
              "missing_text": [], "substitute_glyphs": [], "fonts": [], "fonts_not_embedded": []}
    missing = [t for t in TOOLS if not shutil.which(t)]
    if missing:
        result["reason"] = f"Poppler 도구 없음: {', '.join(missing)} — 인쇄본 확인 불가"
        return result
    # pdftoppm -v는 판에 따라 stdout이나 stderr에 버전을 쓴다.
    proc = subprocess.run([shutil.which("pdftoppm"), "-v"], capture_output=True)
    version = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip().splitlines()
    result["renderer"] = version[0] if version else "pdftoppm"
    try:
        layout = run([shutil.which("pdftotext"), "-bbox-layout", str(pdf), "-"])
        result["fonts"] = fonts(pdf)
        pages = PAGE_RE.findall(layout)
        if not pages:
            raise RuntimeError("텍스트 층에서 쪽을 찾지 못함")
        texts, samples = [], []
        scale = dpi / 72
        with tempfile.TemporaryDirectory() as tmp:
            for number, (_, _, body) in enumerate(pages, 1):
                prefix = Path(tmp) / f"p{number}"
                run([shutil.which("pdftoppm"), "-gray", "-r", str(dpi), "-f", str(number), "-l", str(number),
                     "-singlefile", str(pdf), str(prefix)])
                image = read_pgm(prefix.with_suffix(".pgm"))
                words = []
                for x0, y0, x1, y1, raw in WORD_RE.findall(body):
                    text = html.unescape(raw)
                    box = tuple(float(v) for v in (x0, y0, x1, y1))
                    words.append(text)
                    result["words"] += 1
                    if not inked(image, scale, box):
                        result["blank"].append({"page": number, "word": text})
                        continue
                    for char, char_box in hangul_boxes(text, box):
                        result["hangul_chars"] += 1
                        if not inked(image, scale, char_box):
                            result["blank"].append({"page": number, "word": text, "char": char})
                        elif all(is_hangul(c) for c in text):
                            samples.append((char, shape(image, scale, char_box)))
                texts.append(" ".join(words))
        result["pages"] = len(pages)
    except (RuntimeError, OSError, ValueError) as exc:
        result["reason"] = f"PDF 판독 실패: {exc}"
        return result
    wanted = []
    if docx is not None:
        title = first_paragraph(docx)
        if title:
            result["title"] = title
            if squash(title) not in squash(texts[0]):
                result["missing_text"].append({"text": title, "where": "1쪽 제목"})
    wanted += [t for t in expect if t]
    whole = squash("".join(texts))
    result["missing_text"] += [{"text": t, "where": "본문"} for t in wanted if squash(t) not in whole]
    result["fonts_not_embedded"] = [f["name"] for f in result["fonts"] if not f["embedded"]]
    result["substitute_glyphs"] = substitutes(samples)
    failed = (result["blank"] or result["missing_text"] or result["fonts_not_embedded"]
              or result["substitute_glyphs"] or not result["words"])
    result["status"] = "fail" if failed else "pass"
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="보고서 인쇄본(PDF)의 글자가 실제로 그려졌는지 확인 (Poppler 필요)")
    ap.add_argument("pdf", help="확인할 PDF (보고서초안.docx를 변환한 인쇄본)")
    ap.add_argument("--docx", help="변환 원본 DOCX — 첫 문단(제목)이 1쪽에 있어야 한다")
    ap.add_argument("--expect", action="append", default=[], help="PDF에 있어야 할 문자열 (파일명·해시 등, 반복 가능)")
    ap.add_argument("--dpi", type=int, default=150, help="렌더링 해상도 (기본 150)")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    args = ap.parse_args(argv)
    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"PDF를 찾을 수 없습니다: {pdf}", file=sys.stderr)
        return 2
    docx = Path(args.docx) if args.docx else None
    if docx is not None and not docx.is_file():
        print(f"DOCX를 찾을 수 없습니다: {docx}", file=sys.stderr)
        return 2
    result = check(pdf, docx, args.expect, args.dpi)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["status"] == "unverified":
        print(f"[확인 불가] {result['reason']}")
    else:
        print(f"[{'통과' if result['status'] == 'pass' else '실패'}] {pdf.name}: {result['pages']}쪽, 단어 {result['words']}개, "
              f"한글 글자 {result['hangul_chars']}개 확인 ({result['renderer']})")
        for item in result["blank"][:SHOW]:
            where = f"'{item['char']}' ({item['word']})" if "char" in item else f"'{item['word']}'"
            print(f"  빈 글자: {item['page']}쪽 {where}")
        if len(result["blank"]) > SHOW:
            print(f"  … 빈 글자 {len(result['blank']) - SHOW}건 더")
        for item in result["missing_text"]:
            print(f"  없는 문자열({item['where']}): {item['text']}")
        for name in result["fonts_not_embedded"]:
            print(f"  포함되지 않은 글꼴: {name}")
        if result["substitute_glyphs"]:
            print(f"  서로 다른 한글 {len(result['substitute_glyphs'])}자가 같은 모양(대체 글리프·빈 상자)으로 그려짐: "
                  + "".join(result["substitute_glyphs"][:SHOW]))
        if result["blank"] or result["substitute_glyphs"]:
            print("  → 변환한 PC에 한글 글꼴(맑은 고딕·나눔고딕·Noto Sans CJK KR 등)이 있는지 확인하고 다시 변환할 것. "
                  "텍스트 복사가 된다고 인쇄본을 채택하지 않는다.")
    return {"pass": 0, "fail": 1}.get(result["status"], 3)


if __name__ == "__main__":
    sys.exit(main())
