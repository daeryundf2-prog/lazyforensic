#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
osint_username.py — 사용자명 OSINT 검색 (sherlock BYO 래퍼)

sherlock-project가 설치돼 있을 때만 동작한다. 미설치 시 결과를
만들지 않고 exit 3 + 설치 안내로 종료한다(fail-closed).

⚠️ 외부 네트워크 사용: sherlock은 수백 개 사이트에 HTTP 요청을 보낸다.
증거 데이터를 보내지 않지만 '수사 대상 닉네임' 자체가 조회 쿼리로
제3자 사이트에 노출된다. 의뢰인 동의·반출 승인 절차를 확인한 뒤에만
사용할 것.

대량 수집 게이트(contracts/collection-gate.md, 출처 hypercore MIT — NOTICE):
    - --scope "<대상·상한·속도 한 줄>" 필수 (없으면 exit 2)
    - 닉네임은 50개까지, 초과분은 건너뛰고 skipped로 기록
    - 닉네임 호출 사이 1초 이상, 429가 보이면 대기 후 간격 2배, 두 번째 429면 중단
    - 한계: robots.txt와 sherlock 내부 요청 속도는 강제하지 못한다(문서 참조)

사용:
    python scripts/osint_username.py suspect123 --scope "suspect123, 482개 사이트, 1건, 의뢰인 동의 확인"
    python scripts/osint_username.py a b c --json --scope "..."
    pip install sherlock-project   # 설치

출력: 사이트별 발견 URL 목록 (sherlock --print-found 기준)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collection_gate as gate  # noqa: E402

RATE_LIMIT_MARKERS = ("429", "too many requests")


def find_sherlock() -> list[str] | None:
    if shutil.which("sherlock"):
        return ["sherlock"]
    # python -m sherlock 형태도 시도
    proc = subprocess.run(
        [sys.executable, "-c", "import sherlock_project"],
        capture_output=True,
    )
    if proc.returncode == 0:
        return [sys.executable, "-m", "sherlock_project"]
    return None


def run_sherlock(cmd: list[str], username: str, timeout: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            cmd + [username, "--print-found", "--folderoutput", tmp, "--timeout", "10"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        urls = [
            line.strip().split()[-1]
            for line in proc.stdout.splitlines()
            if line.strip().startswith("[+]")
        ]
        return {
            "username": username,
            "found_count": len(urls),
            "urls": urls,
            "stderr": proc.stderr.strip()[:300] if proc.returncode != 0 else "",
            "rate_limited": hit_rate_limit(proc.stdout + proc.stderr),
        }


def hit_rate_limit(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in RATE_LIMIT_MARKERS)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="사용자명 OSINT 검색 (sherlock BYO, 외부 네트워크 사용)")
    ap.add_argument("usernames", nargs="+", help="검색할 사용자명")
    ap.add_argument("--timeout", type=int, default=300, help="sherlock 전체 타임아웃(초)")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--scope", help="범위 한 줄 확인(대상·상한·속도). 필수")
    ap.add_argument("--max-items", type=int, default=gate.ITEM_CAP,
                    help=f"닉네임 상한(최대 {gate.ITEM_CAP})")
    args = ap.parse_args(argv)

    try:
        scope = gate.validate_scope(args.scope)
        if args.max_items < 1:
            raise ValueError("--max-items는 1 이상이어야 합니다")
    except ValueError as exc:
        print(f"수집 게이트: {exc}", file=sys.stderr)
        return 2
    usernames, skipped = gate.cap_items(args.usernames, args.max_items)

    cmd = find_sherlock()
    if cmd is None:
        print(
            "sherlock이 설치돼 있지 않습니다. 검색을 생성하지 않고 종료합니다.\n"
            "  pip install sherlock-project\n"
            "주의: 조회 대상 닉네임이 외부 사이트에 쿼리로 노출됩니다.\n"
            "의뢰인 동의·반출 승인을 확인한 뒤 사용하세요.",
            file=sys.stderr,
        )
        return 3

    results = []
    limiter = gate.RateLimiter()
    status = "complete"
    for i, name in enumerate(usernames):
        limiter.wait()
        try:
            res = run_sherlock(cmd, name, args.timeout)
        except subprocess.TimeoutExpired:
            results.append({"username": name, "error": f"timeout {args.timeout}s"})
            continue
        results.append(res)
        if res.get("rate_limited") and not limiter.on_429():
            skipped = usernames[i + 1:] + skipped
            status = "fetched_partial"
            break
    if skipped and status == "complete":
        status = "capped"

    if args.json:
        print(json.dumps({"status": status, "scope": scope, "skipped": skipped,
                          "interval_sec": limiter.interval, "results": results},
                         ensure_ascii=False, indent=2))
    else:
        print(f"[게이트] 상태 {status} · 범위: {scope}")
        if skipped:
            print(f"[게이트] 건너뜀 {len(skipped)}개: {', '.join(skipped)}")
        for r in results:
            if "error" in r:
                print(f"{r['username']}: {r['error']}")
            else:
                print(f"{r['username']}: {r['found_count']}곳 발견")
                for u in r["urls"]:
                    print(f"    {u}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
