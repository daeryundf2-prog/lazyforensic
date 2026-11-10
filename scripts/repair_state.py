#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repair_state.py — 보고서 보정 패스 기록기 (stdlib 전용)

verify_report.py 가 FAIL 을 낸 뒤 고치고 다시 검증하는 일을 최대 2회까지만 기록·허용한다.
`record` 는 검증 결과 한 건을 상태 파일에 쌓고 `stop:` 판정(oscillation → plateau → limit 순)을 낸다.
`finish` 는 보고서가 비었거나, 검증 기록이 없거나, FAIL 이 남았으면 인도를 거부하고
그렇지 않으면 outcome.json 을 쓴다. 끝까지 FAIL 인 고위험 주장은 완화하지 말고 제외하거나
미해결로 옮긴다(스킬 문서 규칙). 실행마다 상태 파일을 따로 쓴다.

구현 범위: hyper-research SKILL.md 의 계약 문장(보정 2회 상한, stop 판정 순서, finish 거부 조건,
실행별 상태 파일)만. 원본 report-state.sh 본문은 읽지 못해 그 내부 구현은 옮기지 않았다.

출처: hypercore skills/hyper-research/SKILL.md <loop_policy> 와 <skill_architecture>.
      MIT, Copyright (c) 2026 alpoxdev, pinned 2d305cbf5db72e337189aa584b363bb9ced2d9bc. NOTICE 참조.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_REPAIR_PASSES = 2


def _load(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"entries": []}


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def stop_verdict(entries: list[dict[str, Any]]) -> str | None:
    if not entries or entries[-1]["verdict"] != "FAIL":
        return None
    sets = [set(e["errors"]) for e in entries]
    if len(sets) >= 3 and sets[-1] == sets[-3] and sets[-1] != sets[-2]:
        return "oscillation"
    if len(sets) >= 2 and sets[-1] == sets[-2]:
        return "plateau"
    if len(entries) - 1 >= MAX_REPAIR_PASSES:
        return "limit"
    return None


def cmd_record(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = _load(state_path)
    entries = state["entries"]
    if len(entries) - 1 >= MAX_REPAIR_PASSES and entries and entries[-1]["verdict"] == "FAIL":
        print(f"stop: limit (보정 패스 {MAX_REPAIR_PASSES}회 소진 — 더 기록하지 않음)", file=sys.stderr)
        return 3
    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    entries.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "verdict": result["verdict"],
        "errors": list(result.get("errors", [])),
        "warnings": list(result.get("warnings", [])),
    })
    reason = stop_verdict(entries)
    state["stop"] = reason
    _save(state_path, state)
    repairs = max(0, len(entries) - 1)
    if reason:
        print(f"stop: {reason} (보정 패스 {repairs}/{MAX_REPAIR_PASSES})")
        return 3
    print(f"continue (보정 패스 {repairs}/{MAX_REPAIR_PASSES}, verdict {entries[-1]['verdict']})")
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = _load(state_path)
    entries = state["entries"]
    report = Path(args.report)
    if not report.exists() or not report.read_text(encoding="utf-8", errors="replace").strip():
        print("refuse: 보고서가 비어 있음", file=sys.stderr)
        return 1
    if not entries:
        print("refuse: 검증 기록 없음(게이트 미실행) — verify_report.py --json 결과를 record 로 먼저 남길 것", file=sys.stderr)
        return 1
    last = entries[-1]
    if last["verdict"] == "FAIL":
        print(f"refuse: 차단 요소 {len(last['errors'])}건 남음 — 해당 주장은 제외하거나 미해결로 옮긴 뒤 재검증", file=sys.stderr)
        return 1
    outcome = {
        "outcome": "delivered" if last["verdict"] == "PASS" else "delivered_with_warnings",
        "verdict": last["verdict"],
        "repair_passes": max(0, len(entries) - 1),
        "stop": state.get("stop"),
        "warnings": last["warnings"],
        "report": str(report),
    }
    out_path = Path(args.outcome) if args.outcome else state_path.with_name("outcome.json")
    out_path.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"finish: {outcome['outcome']} (보정 패스 {outcome['repair_passes']}/{MAX_REPAIR_PASSES}) -> {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="보고서 보정 패스 기록 (최대 2회)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="verify_report.py --json 결과 한 건을 기록")
    rec.add_argument("--state", required=True, help="실행별 상태 파일 (예: .lazyforensic/<run>/report-state.json)")
    rec.add_argument("--result", required=True, help="verify_report.py --json 출력 파일")
    rec.set_defaults(func=cmd_record)
    fin = sub.add_parser("finish", help="인도 가능 여부 판정과 outcome.json 기록")
    fin.add_argument("--state", required=True)
    fin.add_argument("--report", required=True)
    fin.add_argument("--outcome", default=None)
    fin.set_defaults(func=cmd_finish)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
