#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
court_evidence_sheet.py — 서증 목록표(증거설명서) 초안 생성기

evidence_manifest.py 또는 case_survey.py의 JSON을 읽어
관행상의 서증 목록 양식(서증번호·증거명칭·작성일자·입증취지·해시)의
**초안**을 Markdown 표로 만든다.

사용:
    python scripts/court_evidence_sheet.py manifest.json --party 갑
    python scripts/court_evidence_sheet.py survey.json --party 을 --start 3
    python scripts/court_evidence_sheet.py m.json \
        --purpose "call.wav=금전 대여 약정 내용 입증" \
        --purpose-file purposes.json        # {"파일명": "입증취지"}

중요 — 이 도구의 한계 (읽고 쓸 것):
- '대법원 ECFS 표준 규격' 같은 공인 기계 형식은 존재하지 않는다.
  이 출력은 관행 양식의 **초안**이며, 제출 전 사람이 검토·수정해야 한다.
- 입증취지는 파일에서 추론하지 않는다 — --purpose로 받거나 빈칸으로 둔다.
  빈칸은 '미기재'가 아니라 '사람이 채울 칸'이다.
- --purpose 키는 상대 경로가 파일명보다 우선한다. 파일명만으로 지정한 취지가
  같은 이름의 파일 여러 개에 적용되면 표 아래에 그 행들을 밝힌다.
- 서증번호는 --party/--start의 단순 연번이다 — 실제 번호 부여는
  소송 전략에 속하므로 이 도구가 결정하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_files(path: Path) -> list[dict]:
    """manifest.json 또는 case_survey.json에서 파일 목록을 꺼낸다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    # case_survey 출력: steps.manifest.result.files
    try:
        files = data["steps"]["manifest"]["result"]["files"]
        return files
    except (KeyError, TypeError):
        pass
    # evidence_manifest 출력: files
    if isinstance(data.get("files"), list):
        return data["files"]
    raise ValueError("인식 가능한 파일 목록이 없습니다 "
                     "(evidence_manifest 또는 case_survey JSON을 주세요)")


def build_rows(files: list[dict], party: str, start: int,
               purposes: dict[str, str]) -> list[dict]:
    rows = []
    for i, f in enumerate(files):
        name = Path(f["path"]).name
        # 명시한 상대 경로가 파일명 기본값보다 우선한다 — 동명 파일에 다른 취지를 줄 수 있어야 한다.
        if f["path"] in purposes:
            purpose, matched_by = purposes[f["path"]], "path"
        elif name in purposes:
            purpose, matched_by = purposes[name], "name"
        else:
            purpose, matched_by = "", None
        rows.append({
            "no": f"{party} 제{start + i}호증",
            "name": name,
            "path": f["path"],
            "size": f.get("size", 0),
            "mtime": f.get("mtime", ""),
            "sha256": f.get("sha256", ""),
            "purpose": purpose,
            "purpose_matched_by": matched_by,
        })
    return rows


def purpose_notes(rows: list[dict], purposes: dict[str, str]) -> list[str]:
    """파일명 키가 여러 동명 파일에 적용된 경우와 어느 행에도 쓰이지 않은 키를 밝힌다."""
    notes = []
    by_name: dict[str, list[dict]] = {}
    for r in rows:
        if r["purpose_matched_by"] == "name":
            by_name.setdefault(r["name"], []).append(r)
    for name, hits in by_name.items():
        if len(hits) > 1:
            notes.append(f"파일명 `{name}`으로 지정한 입증취지가 동명 파일 {len(hits)}건에 같이 적용됨 ("
                         + ", ".join(f"{r['no']} {r['path']}" for r in hits) + ") — 경로로 지정해 확인할 것")
    used = {r["path"] for r in rows if r["purpose_matched_by"] == "path"} | set(by_name)
    for key in purposes:
        if key not in used:
            notes.append(f"입증취지 키 `{key}`가 어느 행에도 적용되지 않음")
    return notes


def to_markdown(rows: list[dict], party: str, notes: list[str] | None = None) -> str:
    lines = [
        "# 서증 목록표 (자동 생성 초안 — 제출 전 검토 필수)",
        "",
        "> ⚠️ 본 표는 도구가 만든 초안이다. 입증취지의 적정성·증거 제출 여부·",
        "> 서증번호 부여는 제출자가 검토·결정해야 하며, 본 목록은 그 판단을 대신하지 않는다.",
        "",
        "| 서증번호 | 증거명칭 | 파일 경로 | 크기 | 수정시각(기록값) | SHA-256 | 입증취지 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        purpose = r["purpose"] if r["purpose"] else "　　　　　"
        sha = r["sha256"][:16] + "…" if r["sha256"] else ""
        lines.append(
            f"| {r['no']} | {r['name']} | {r['path']} | {r['size']:,}B | "
            f"{r['mtime'][:19]} | `{sha}` | {purpose} |")
    blank = sum(1 for r in rows if not r["purpose"])
    lines += ["",
              f"총 {len(rows)}건 — 입증취지 미기재 {blank}건 (사람이 채울 칸)"]
    if notes:
        lines += ["", "입증취지 확인 필요:"] + [f"- {n}" for n in notes]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="서증 목록표 초안 생성 (관행 양식, 검토 필요)")
    ap.add_argument("input", help="evidence_manifest 또는 case_survey JSON")
    ap.add_argument("--party", choices=["갑", "을"], default="갑")
    ap.add_argument("--start", type=int, default=1, help="시작 호증 번호")
    ap.add_argument("--purpose", action="append", default=[],
                    help='"파일명=입증취지" 형식, 반복 가능')
    ap.add_argument("--purpose-file", default=None,
                    help='{"파일명": "입증취지"} JSON')
    ap.add_argument("-o", "--output", default=None, help="Markdown 출력 경로")
    args = ap.parse_args(argv)

    src = Path(args.input)
    if not src.is_file():
        print(f"입력을 찾을 수 없습니다: {src}", file=sys.stderr)
        return 2

    purposes: dict[str, str] = {}
    if args.purpose_file:
        raw = json.loads(Path(args.purpose_file).read_text(encoding="utf-8"))
        purposes.update({str(k).strip().replace("\\", "/"): v for k, v in raw.items()})
    for p in args.purpose:
        if "=" not in p:
            print(f"--purpose 형식 오류(파일명=취지 필요): {p}", file=sys.stderr)
            return 2
        k, v = p.split("=", 1)
        purposes[k.strip().replace("\\", "/")] = v.strip()

    try:
        files = load_files(src)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"입력 파싱 실패: {e}", file=sys.stderr)
        return 2

    rows = build_rows(files, args.party, args.start, purposes)
    notes = purpose_notes(rows, purposes)
    for n in notes:
        print(f"주의: {n}", file=sys.stderr)
    md = to_markdown(rows, args.party, notes)
    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"서증 목록표 초안 → {args.output} ({len(rows)}건)", file=sys.stderr)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
