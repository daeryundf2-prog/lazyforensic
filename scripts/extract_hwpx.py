#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_hwpx.py — HWPX 텍스트 추출기 (표준 라이브러리 전용)

HWPX는 ZIP 컨테이너 + XML이다. Contents/section*.xml 안의
<hp:t> 노드 텍스트를 순서대로 뽑아 평문을 만든다.
바이너리 HWP(.hwp, CFB 형식)는 이 도구로 읽지 않는다 —
그쪽은 외부 도구(obundh/korean-munseo-diff 등) 영역이다.
선택 사항: --kordoc 를 주고 kordoc(chrisryugj/kordoc, MIT) CLI가 설치돼 있으면,
위 경로가 실패한 파일(.hwp 포함)에 한해 kordoc로 폴백한다(KORDOC_OFFLINE=1 고정,
네트워크 없음). kordoc가 없거나 --kordoc 를 안 주면 동작은 이전과 완전히 같다.

사용:
    python scripts/extract_hwpx.py doc.hwpx                  # 텍스트 출력
    python scripts/extract_hwpx.py dir/ -o out/              # 일괄 추출
    python scripts/extract_hwpx.py doc.hwpx --json           # 구조화 출력
    python scripts/extract_hwpx.py doc.hwp --kordoc          # 실패 시 kordoc 폴백(선택)
    # kordoc 위치: PATH 의 `kordoc` 또는 환경변수 LAZYFORENSIC_KORDOC_BIN

알려진 한계:
- 표 셀 구조·서식은 잃는다. 텍스트 순서만 보존한다.
- 암호 걸린 HWPX는 ZIP 헤더도 못 열므로 실패로 보고한다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

HWP_NS = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
SECTION_CAP = 8 * 1024 * 1024
TOTAL_CAP = 64 * 1024 * 1024
MEMBER_CAP = 10000
TEXT_CAP = 64 * 1024 * 1024
KORDOC_TIMEOUT = 120
KORDOC_SUFFIXES = {".hwp", ".hwpx"}


def extract_text(hwpx_path: Path) -> list[str]:
    """section XML들에서 <hp:t> 텍스트를 문서 순서대로 반환한다.

    리소스 상한: 섹션당 8MB, 문서 누적 64MB, 멤버 10,000개, 텍스트 64MB.
    초과 시 RuntimeError로 fail-closed — 지어내지 않는다.
    """
    if not zipfile.is_zipfile(hwpx_path):
        raise RuntimeError("HWPX가 아니거나 손상된 파일 (ZIP 시그니처 없음)")
    texts: list[str] = []
    text_len = 0
    with zipfile.ZipFile(hwpx_path) as zf:
        names = zf.namelist()
        if len(names) > MEMBER_CAP:
            raise RuntimeError(f"멤버 수 상한 초과 ({len(names)}>{MEMBER_CAP})")
        sections = sorted(
            (n for n in names
             if re.match(r"Contents/section\d+\.xml", n)),
            key=lambda n: int(re.search(r"\d+", n).group(0)),
        )
        if not sections:
            raise RuntimeError("Contents/section*.xml이 없습니다")
        total = 0
        for name in sections:
            remaining = min(SECTION_CAP, TOTAL_CAP - total)
            info = zf.getinfo(name)
            if info.file_size > remaining:
                raise RuntimeError("Section or aggregate read limit exceeded")
            with zf.open(info) as stream:
                raw = stream.read(remaining + 1)
            if len(raw) > remaining:
                raise RuntimeError("Section or aggregate read limit exceeded")
            total += len(raw)
            if total > TOTAL_CAP:
                raise RuntimeError(f"문서 누적 크기 상한 초과 ({TOTAL_CAP}B) — 처리 중단")
            if len(raw) > SECTION_CAP:
                raise RuntimeError(f"섹션 크기 상한 초과: {name} ({len(raw)}B)")
            root = ET.fromstring(raw)
            for t in root.iter(f"{HWP_NS}t"):
                if t.text:
                    text_len += len(t.text.encode("utf-8"))
                    if text_len > TEXT_CAP:
                        raise RuntimeError(f"추출 텍스트 상한 초과 ({TEXT_CAP}B)")
                    texts.append(t.text)
    return texts


def find_kordoc() -> str | None:
    """선택 의존 kordoc CLI 경로. 없으면 None (그러면 폴백은 꺼진 것과 같다)."""
    cand = os.environ.get("LAZYFORENSIC_KORDOC_BIN") or shutil.which("kordoc")
    if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    return None


def kordoc_extract(path: Path, kordoc_bin: str) -> str:
    """kordoc CLI로 Markdown을 뽑는다. 오프라인 고정, 접근 루트는 파일 폴더로 제한."""
    path = path.resolve()
    env = dict(os.environ, KORDOC_OFFLINE="1", KORDOC_ROOT=str(path.parent))
    with tempfile.TemporaryDirectory(prefix="lf-kordoc-") as tmp:
        out = Path(tmp) / "out.md"
        try:
            p = subprocess.run(
                [kordoc_bin, str(path), "-o", str(out)],
                capture_output=True, text=True, env=env, cwd=tmp,
                timeout=KORDOC_TIMEOUT, stdin=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RuntimeError(f"kordoc 실행 실패: {type(e).__name__}") from e
        if p.returncode != 0 or not out.is_file():
            raise RuntimeError(f"kordoc 실패 (rc={p.returncode})")
        if out.stat().st_size > TEXT_CAP:
            raise RuntimeError(f"kordoc 출력 상한 초과 ({TEXT_CAP}B)")
        return out.read_text(encoding="utf-8", errors="replace")


def process(path: Path, use_kordoc: bool = False) -> dict:
    try:
        texts = extract_text(path)
    except (RuntimeError, ET.ParseError, zipfile.BadZipFile, OSError) as e:
        failed = {"file": str(path), "status": "failed", "error": str(e)[:200]}
        kordoc_bin = find_kordoc() if use_kordoc else None
        if kordoc_bin is None:
            return failed
        try:
            md = kordoc_extract(path, kordoc_bin)
        except RuntimeError as ke:
            failed["kordoc_error"] = str(ke)[:200]
            return failed
        return {
            "file": str(path),
            "status": "ok",
            "engine": "kordoc",
            "fallback_reason": failed["error"],
            "text_units": sum(1 for ln in md.splitlines() if ln.strip()),
            "text": md,
        }
    return {
        "file": str(path),
        "status": "ok",
        "text_units": len(texts),
        "text": "\n".join(texts),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HWPX 텍스트 추출 (stdlib 전용)")
    ap.add_argument("input", help=".hwpx 파일 또는 디렉터리")
    ap.add_argument("-o", "--outdir", default=None, help="디렉터리 입력 시 추출 텍스트 출력 위치")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--kordoc", action="store_true",
                    help="실패한 파일(.hwp 포함)에 kordoc CLI 폴백(선택, 오프라인)")
    args = ap.parse_args(argv)

    target = Path(args.input)
    if target.is_dir():
        files = sorted(target.rglob("*.hwpx"))
        if args.kordoc and find_kordoc():
            files = sorted(set(files) | set(target.rglob("*.hwp")))
    elif target.is_file():
        files = [target]
    else:
        print(f"대상을 찾을 수 없습니다: {target}", file=sys.stderr)
        return 2
    if not files:
        print(f".hwpx 파일이 없습니다: {target}", file=sys.stderr)
        return 2

    if args.kordoc and find_kordoc() is None:
        print("[WARN] --kordoc 지정됐지만 kordoc CLI를 찾을 수 없어 폴백 없이 진행", file=sys.stderr)
    records = [process(f, args.kordoc) for f in files]
    ok = sum(1 for r in records if r["status"] == "ok")

    if args.outdir:
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        for f, r in zip(files, records):
            if r["status"] == "ok":
                name = f.stem + ".txt" if f.suffix.lower() == ".hwpx" else f.name + ".txt"
                (outdir / name).write_text(r["text"], encoding="utf-8")
        print(f"추출 완료: {ok}/{len(files)}개 → {outdir}")
    elif args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        for r in records:
            if r["status"] == "ok":
                print(f"=== {r['file']} ({r['text_units']} units) ===")
                print(r["text"])
            else:
                print(f"[FAIL] {r['file']}: {r['error']}", file=sys.stderr)

    return 0 if ok == len(files) else 1


if __name__ == "__main__":
    sys.exit(main())
