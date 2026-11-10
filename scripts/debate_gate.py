#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
debate_gate.py — 고위험 주장 반박 기록(debate) 검증 (stdlib 전용)

보고서가 주장을 `[검증됨 C3]` 로 표시하면, 같은 보고서의 `## 반박 기록` 표에
그 주장에 대한 공격이 서로 다른 관점으로 2회 이상 남아 있어야 한다.
반증 검색을 못 돌린 주장은 `[귀속 C4]` 로 쓰고 "~에 따르면" 처럼 출처에 귀속해서만 서술한다.
표시가 하나도 없는 보고서는 검사하지 않는다(기존 보고서 동작 불변).

출처: hypercore skills/hyper-research/rules/claim-verification.md §3 (partial = attributed only),
      §7 (Debate Record). MIT, Copyright (c) 2026 alpoxdev,
      pinned 2d305cbf5db72e337189aa584b363bb9ced2d9bc. NOTICE 참조.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VERDICTS = {
    "holds": "holds", "weakened": "weakened", "withdrawn": "withdrawn",
    "유지": "holds", "약화": "weakened", "철회": "withdrawn",
}
MIN_PERSPECTIVES = 2

VERIFIED_TAG_RE = re.compile(r"\[(?:검증됨|verified)\s+([A-Za-z]+-?\d+)\]", re.IGNORECASE)
ATTRIBUTED_TAG_RE = re.compile(r"\[(?:귀속|attributed)\s+([A-Za-z]+-?\d+)\]", re.IGNORECASE)
HEADING_RE = re.compile(r"^#{1,6}\s*(?:반박\s*기록|debate(?:\s*record)?)\s*$", re.IGNORECASE | re.MULTILINE)
NEXT_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
ATTRIBUTION_RE = re.compile(r"에\s*따르면|라고\s*(?:한다|밝혔|주장|진술)|주장한다|진술(?:했|한다)|states? that|according to", re.IGNORECASE)
NO_CHANGE = {"", "-", "—", "없음", "none", "n/a"}


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_debate_rows(text: str) -> tuple[bool, list[dict[str, str]]]:
    m = HEADING_RE.search(text)
    if not m:
        return False, []
    body = text[m.end():]
    nxt = NEXT_HEADING_RE.search(body)
    if nxt:
        body = body[:nxt.start()]
    rows: list[dict[str, str]] = []
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) < 6 or set("".join(cells)) <= set("-: "):
            continue
        if not re.fullmatch(r"[A-Za-z]+-?\d+", cells[0]):
            continue
        rows.append({
            "claim": cells[0], "attack": cells[1], "perspective": cells[2],
            "defense": cells[3], "verdict": cells[4], "change": cells[5],
        })
    return True, rows


def check_debate(text: str) -> tuple[list[str], list[str]]:
    verified = {m.group(1) for m in VERIFIED_TAG_RE.finditer(text)}
    errors: list[str] = []
    warnings: list[str] = []
    has_section, rows = parse_debate_rows(text)

    for r in rows:
        if r["verdict"].strip().lower() not in VERDICTS:
            errors.append(f"반박 기록 {r['claim']}: 판정 '{r['verdict']}' 은 holds/weakened/withdrawn 이 아님")
        if not r["attack"] or not r["perspective"]:
            errors.append(f"반박 기록 {r['claim']}: 공격 또는 공격 관점 칸이 비어 있음")

    for cid in sorted(verified):
        mine = [r for r in rows if r["claim"] == cid]
        if not has_section or not mine:
            errors.append(f"[검증됨 {cid}] 반박 기록 없음 — 서로 다른 관점의 공격 {MIN_PERSPECTIVES}회 이상 필요, 없으면 [귀속 {cid}] 로 쓸 것")
            continue
        perspectives = {r["perspective"].strip().lower() for r in mine if r["perspective"].strip()}
        if len(perspectives) < MIN_PERSPECTIVES:
            errors.append(f"[검증됨 {cid}] 공격 관점 {len(perspectives)}종 — 서로 다른 관점 {MIN_PERSPECTIVES}종 이상 필요 (같은 관점 반복은 1회로 센다)")
        verdicts = {VERDICTS.get(r["verdict"].strip().lower()) for r in mine}
        if "withdrawn" in verdicts:
            errors.append(f"[검증됨 {cid}] 철회(withdrawn) 판정이 있는 주장을 검증됨으로 표시함")
        if "weakened" in verdicts and not any(
                VERDICTS.get(r["verdict"].strip().lower()) == "weakened"
                and r["change"].strip().lower() not in NO_CHANGE for r in mine):
            errors.append(f"[검증됨 {cid}] 약화(weakened) 판정인데 '주장 변경' 칸이 비어 있음 — 주장을 좁히거나 귀속/미해결로 옮길 것")

    for line_no, line in enumerate(text.splitlines(), start=1):
        for m in ATTRIBUTED_TAG_RE.finditer(line):
            if not ATTRIBUTION_RE.search(line):
                warnings.append(f"[귀속 {m.group(1)}] {line_no}행: 출처에 귀속하는 서술(\"~에 따르면\" 등)이 없음 — 사실로 단정하지 말 것")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="반박 기록(debate) 검증")
    ap.add_argument("report", help="보고서 (.md)")
    args = ap.parse_args(argv)
    path = Path(args.report)
    if not path.exists():
        print(f"[-] 파일 없음: {path}", file=sys.stderr)
        return 2
    errors, warnings = check_debate(path.read_text(encoding="utf-8", errors="replace"))
    for e in errors:
        print(f"ERROR: {e}")
    for w in warnings:
        print(f"WARN: {w}")
    if not errors and not warnings:
        print("PASS: 반박 기록 검사 통과 (또는 해당 표시 없음)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
