#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hidden_char_scan.py — 증거 문서의 숨은 문자·주입 표지 검사 (stdlib 전용)

카카오/텔레그램 내보내기, 제출 문서, 보고서 초안처럼 사람이 눈으로 읽는 텍스트에는
제로폭·bidi 제어문자와 모델을 향한 지시문이 눈에 띄지 않게 섞일 수 있다.
해시 한가운데 끼어든 제로폭 문자는 육안 대조를 통과시키므로 error, 나머지는 warn 이다.
이모지 결합용 ZWJ 와 파일 맨 앞 BOM 은 정상이라 건드리지 않는다.

출처: hypercore skills/hyper-documentation/rules/memory/safety.md §4 (쓰기 전 검사 목록:
      은닉문자, 주입 표지). MIT, Copyright (c) 2026 alpoxdev,
      pinned 2d305cbf5db72e337189aa584b363bb9ced2d9bc. NOTICE 참조.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

ZERO_WIDTH = {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x180E}
BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A)) | {0x200E, 0x200F, 0x061C}
TAG_BLOCK = range(0xE0000, 0xE0080)
HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
HASH_RUN_MIN = 32  # 이 길이 이상의 16진 연속열을 해시로 본다 (MD5 32 / SHA-1 40 / SHA-256 64)

INJECTION_RE = re.compile(
    r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?)"
    r"|disregard\s+(?:the\s+)?(?:previous|prior|above)"
    r"|이전\s*(?:의\s*)?(?:지시|지침|명령)(?:을|를)?\s*(?:모두\s*)?무시"
    r"|위\s*(?:의\s*)?(?:지시|지침)(?:을|를)?\s*무시"
    r"|<\s*/?\s*(?:system|tool_call|function_call)\b"
    r"|<\|im_start\|>|\[/?INST\]"
    r"|system\s+prompt\s*[:：]",
    re.IGNORECASE,
)

MAX_PER_CODE = 20


def _is_hidden(ch: str) -> bool:
    o = ord(ch)
    return o in ZERO_WIDTH or o in BIDI or o in TAG_BLOCK


def _code_for(ch: str) -> str:
    o = ord(ch)
    if o in BIDI:
        return "BIDI_CONTROL"
    if o in TAG_BLOCK:
        return "TAG_CHAR"
    return "HIDDEN_CHAR"


def _pictographic(ch: str) -> bool:
    return ord(ch) >= 0x1F000 or ord(ch) == 0xFE0F or unicodedata.category(ch) in ("So", "Sk")


def _hex_run_around(text: str, idx: int) -> tuple[int, int]:
    left = 0
    i = idx - 1
    while i >= 0 and (text[i] in HEX_DIGITS or _is_hidden(text[i])):
        left += text[i] in HEX_DIGITS
        i -= 1
    right = 0
    i = idx + 1
    while i < len(text) and (text[i] in HEX_DIGITS or _is_hidden(text[i])):
        right += text[i] in HEX_DIGITS
        i += 1
    return left, right


def scan_text(text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def add(code: str, severity: str, line: int, col: int, detail: str, cp: int | None = None):
        counts[code] = counts.get(code, 0) + 1
        if counts[code] <= MAX_PER_CODE:
            item = {"code": code, "severity": severity, "line": line, "col": col, "detail": detail}
            if cp is not None:
                item["codepoint"] = f"U+{cp:04X}"
            findings.append(item)

    line = 1
    line_start = 0
    for idx, ch in enumerate(text):
        if ch == "\n":
            line += 1
            line_start = idx + 1
            continue
        if not _is_hidden(ch):
            continue
        o = ord(ch)
        col = idx - line_start + 1
        if idx == 0 and o == 0xFEFF:
            continue
        if o == 0x200D:
            prev_ch = text[idx - 1] if idx > 0 else ""
            next_ch = text[idx + 1] if idx + 1 < len(text) else ""
            if (prev_ch and _pictographic(prev_ch)) or (next_ch and _pictographic(next_ch)):
                continue
        left, right = _hex_run_around(text, idx)
        if left >= 1 and right >= 1 and left + right >= HASH_RUN_MIN:
            add("ZW_IN_HASH", "error", line, col,
                f"해시 형태의 16진 문자열 한가운데에 숨은 문자 (좌 {left} / 우 {right})", o)
        else:
            add(_code_for(ch), "warn", line, col, "눈에 보이지 않는 제어/제로폭 문자", o)

    for lineno, raw in enumerate(text.splitlines(), start=1):
        m = INJECTION_RE.search(raw)
        if m:
            add("INJECTION_MARKER", "warn", lineno, m.start() + 1,
                f"모델을 향한 지시문 형태: {m.group(0)[:40]!r} — 데이터로만 다룰 것")
    return findings


def summarize(findings: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    by_code: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        by_code.setdefault(f["code"], []).append(f)
    for code, items in by_code.items():
        first = items[0]
        msg = (f"[{code}] {len(items)}건 (첫 위치 {first['line']}행 {first['col']}열): {first['detail']}")
        (errors if first["severity"] == "error" else warnings).append(msg)
    return errors, warnings


def _read(path: Path) -> str:
    try:
        from scripts.report_core import read_text_smart
    except ImportError:
        from report_core import read_text_smart
    return read_text_smart(path)[0]


def _targets(args: list[str]) -> list[Path]:
    out: list[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            out.extend(sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in (".txt", ".md", ".json", ".csv", ".html")))
        else:
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="증거 문서 숨은 문자·주입 표지 검사")
    ap.add_argument("target", nargs="+", help="텍스트 파일 또는 디렉터리")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--strict", action="store_true", help="warn 도 exit 1")
    args = ap.parse_args(argv)

    results = []
    worst = 0
    for path in _targets(args.target):
        if not path.exists():
            print(f"[-] 파일 없음: {path}", file=sys.stderr)
            return 2
        findings = scan_text(_read(path))
        results.append({"file": str(path), "findings": findings})
        if any(f["severity"] == "error" for f in findings):
            worst = max(worst, 1)
        elif findings and args.strict:
            worst = max(worst, 1)

    if args.json:
        print(json.dumps({"files": results, "verdict": "FAIL" if worst else "PASS"}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            errs, warns = summarize(r["findings"])
            tag = "FAIL" if errs else ("WARN" if warns else "PASS")
            print(f"[{tag}] {r['file']}")
            for m in errs + warns:
                print(f"  - {m}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
