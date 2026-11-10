#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collection_gate.py — 대량 수집 게이트 (stdlib 전용 공용 모듈)

정책 본문: contracts/collection-gate.md
출처: hypercore skills/hyper-browsing/rules/access-policy.md §3 (Bulk Gate),
      rules/access-ladder.md §3 (예산). MIT, Copyright (c) 2026 alpoxdev,
      pinned 2d305cbf5db72e337189aa584b363bb9ced2d9bc. NOTICE 참조.

이 모듈은 네트워크를 쓰지 않는다. 호출 스크립트가 넘긴 값만 판정한다.
- cap_items      : 항목 수 상한(기본 50)
- validate_scope : 사용자 범위 한 줄 확인
- RateLimiter    : 초당 1요청, 429면 속도 절반, 두 번째 429면 중단
- robots_allows  : 이미 가져온 robots.txt 본문으로 허용 여부 판정(RFC 9309)
"""
from __future__ import annotations

import time
import urllib.robotparser
from typing import Callable, Sequence, TypeVar

ITEM_CAP = 50
MIN_INTERVAL = 1.0  # 초. 1요청/초 = 간격 1.0초

T = TypeVar("T")


def cap_items(items: Sequence[T], cap: int = ITEM_CAP) -> tuple[list[T], list[T]]:
    """(처리 대상, 상한 초과로 건너뛴 항목). cap은 ITEM_CAP을 넘길 수 없다."""
    if cap < 1:
        raise ValueError("cap must be >= 1")
    cap = min(cap, ITEM_CAP)
    return list(items[:cap]), list(items[cap:])


def validate_scope(scope: str | None) -> str:
    """범위 확인 문구는 비어 있지 않은 한 줄이어야 한다. 아니면 ValueError."""
    text = (scope or "").strip()
    if not text:
        raise ValueError("범위 한 줄 확인(--scope)이 필요합니다")
    if "\n" in text or "\r" in text:
        raise ValueError("--scope는 한 줄이어야 합니다")
    return text


class RateLimiter:
    """호스트 하나 기준 요청 간격 제어. clock/sleep은 테스트에서 주입한다."""

    def __init__(self, interval: float = MIN_INTERVAL,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.interval = max(interval, MIN_INTERVAL)
        self.throttled = 0
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    @property
    def stopped(self) -> bool:
        return self.throttled >= 2

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self._last + self.interval - now
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()

    def on_429(self, retry_after: float | None = None) -> bool:
        """429 수신. 첫 번째: 지정 시간(없으면 현재 간격) 한 번 대기 후 속도 절반.
        두 번째: 중단(False). 계속해도 되면 True."""
        self.throttled += 1
        if self.stopped:
            return False
        self._sleep(retry_after if retry_after is not None else self.interval)
        self.interval *= 2  # 속도 절반 = 간격 두 배
        self._last = self._clock()
        return True


def robots_allows(robots_txt: str, url: str, agent: str = "lazyforensic") -> bool:
    """robots.txt 본문(이미 가져온 것)으로 url 허용 여부를 판정한다."""
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(robots_txt.splitlines())
    return parser.can_fetch(agent, url)
