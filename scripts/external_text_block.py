#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
external_text_block.py — 외부 텍스트를 모델에 넘기기 전 데이터 블록으로 감싼다 (stdlib 전용)

카카오/텔레그램 추출 결과 같은 상대방 작성 텍스트는 지시문이 아니라 증거(데이터)다.
JSON 문자열로 직렬화해 라벨 붙은 블록에 담으면 줄바꿈과 따옴표가 이스케이프되고 `<` 도 `\u003c` 로 바뀔다.
본문 속 "이전 지시를 무시하라" 나 닫는 태그가 지시 위치를 차지하지 못한다.
블록에는 원문 SHA-256, 숨은 문자·주입 표지 검사 결과, `authority: none` 이 함께 들어간다.

출처: hypercore skills/hyper-documentation/rules/prompt/context-source-safety.md
      (Authority Boundary, Prompt Injection Handling). MIT, Copyright (c) 2026 alpoxdev,
      pinned 2d305cbf5db72e337189aa584b363bb9ced2d9bc. NOTICE 참조.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from scripts.hidden_char_scan import scan_text
except ImportError:
    from hidden_char_scan import scan_text

BLOCK_OPEN = "<external_text_data>"
BLOCK_CLOSE = "</external_text_data>"


def wrap_external_text(text: str, *, source: str, kind: str = "chat_export") -> str:
    findings = scan_text(text)
    codes: dict[str, int] = {}
    for f in findings:
        codes[f["code"]] = codes.get(f["code"], 0) + 1
    payload = {
        "kind": kind,
        "source": source,
        "authority": "none",
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
        "scan_findings": codes,
        "text": text,
    }
    body = json.dumps(payload, ensure_ascii=True, indent=2).replace("<", "\\u003c")
    return f"{BLOCK_OPEN}\n{body}\n{BLOCK_CLOSE}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="외부 텍스트를 데이터 블록으로 감싸 출력")
    ap.add_argument("file", help="추출 결과 텍스트/JSON 파일")
    ap.add_argument("--kind", default="chat_export")
    ap.add_argument("--source", default=None, help="출처 표기 (기본: 파일 이름)")
    args = ap.parse_args(argv)
    path = Path(args.file)
    if not path.exists():
        print(f"[-] 파일 없음: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8", errors="replace")
    print(wrap_external_text(text, source=args.source or path.name, kind=args.kind))
    return 0


if __name__ == "__main__":
    sys.exit(main())
