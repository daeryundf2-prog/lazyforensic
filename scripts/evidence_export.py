#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence_export.py — lazyothers 증거 JSON(evidence_list)보내기

evidence_manifest.py 또는 case_survey.py의 JSON을 읽어
lazyothers의 generate_evidence_doc.py / bind_court_pdf.py가
그대로 소비하는 evidence.json 형식으로 변환한다.

사용:
    python scripts/evidence_export.py manifest.json -o evidence.json --party 갑
    python scripts/evidence_export.py survey.json --party 을 --start 3 \
        --purpose "call.wav=금전 대여 약정 내용 입증"

출력 형식 (lazyothers 호환):
    {"evidence_list": [
      {"label": "갑 제1호증", "title": "call.wav", "file": "/abs/path",
       "author": "작성자불상", "date": "2026-09-11",
       "purpose": "...", "sha256": "..."}
    ]}

중요 — 이 도구의 한계:
- 출력은 '초안 데이터'다. 입증취지는 --purpose로 받거나 빈 문자열로 둔다 —
  파일에서 추론하지 않는다.
- file 경로는 절대 경로로 기록한다 — lazyothers 쪽 스크립트가
  input-json 위치 기준 상대경로도 받지만, 절대 경로가 이동 시 혼동이 적다.
- 서증번호는 단순 연번이며 실제 번호 부여는 소송 전략에 속한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from court_evidence_sheet import build_rows, load_files  # noqa: E402


def to_evidence_json(rows: list[dict]) -> dict:
    items = []
    for r in rows:
        date = (r["mtime"] or "")[:10]
        items.append({
            "label": r["no"],
            "title": r["name"],
            "file": str(Path(r["path"]).resolve()),
            "author": "작성자불상",
            "date": date,
            "purpose": r["purpose"],
            "sha256": r["sha256"],
        })
    return {"evidence_list": items}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="manifest/survey JSON → lazyothers evidence.json 변환 (초안)")
    ap.add_argument("input", help="evidence_manifest 또는 case_survey JSON")
    ap.add_argument("--party", choices=["갑", "을"], default="갑")
    ap.add_argument("--start", type=int, default=1, help="시작 호증 번호")
    ap.add_argument("--purpose", action="append", default=[],
                    help='"파일명=입증취지" 형식, 반복 가능')
    ap.add_argument("--purpose-file", default=None,
                    help='{"파일명": "입증취지"} JSON')
    ap.add_argument("-o", "--output", default=None, help="출력 JSON 경로")
    args = ap.parse_args(argv)

    src = Path(args.input)
    if not src.is_file():
        print(f"입력을 찾을 수 없습니다: {src}", file=sys.stderr)
        return 2

    purposes: dict[str, str] = {}
    if args.purpose_file:
        purposes.update(json.loads(Path(args.purpose_file).read_text(encoding="utf-8")))
    for p in args.purpose:
        if "=" not in p:
            print(f"--purpose 형식 오류(파일명=취지 필요): {p}", file=sys.stderr)
            return 2
        k, v = p.split("=", 1)
        purposes[k.strip()] = v.strip()

    try:
        files = load_files(src)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"입력 파싱 실패: {e}", file=sys.stderr)
        return 2

    rows = build_rows(files, args.party, args.start, purposes)
    payload = to_evidence_json(rows)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"evidence.json 초안 → {args.output} ({len(rows)}건)", file=sys.stderr)
        print("다음 단계: lazyothers generate_evidence_doc.py --input-json <이 파일>",
              file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
