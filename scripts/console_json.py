#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
console_json.py — 표준 출력으로 내보내는 기계용 JSON

Windows 한국어 콘솔·파이프(CP949)는 이모지 등 코드 페이지 밖의 문자를
'?'로 바꾼다. 그 JSON을 다시 읽으면 파일 경로가 원래 값과 달라진다.
출력 스트림이 JSON 전체를 표현할 수 있으면 그대로 쓰고, 한 글자라도
표현하지 못하면 \\uXXXX 이스케이프(ASCII)로 써서 역직렬화하면 원래
문자열이 그대로 복원되게 한다.
"""
from __future__ import annotations

import json
import sys


def dumps_for(stream, obj, **kwargs) -> str:
    text = json.dumps(obj, ensure_ascii=False, **kwargs)
    try:
        text.encode(getattr(stream, "encoding", None) or "utf-8")
    except (UnicodeEncodeError, LookupError):
        return json.dumps(obj, ensure_ascii=True, **kwargs)
    return text


def print_json(obj, **kwargs) -> None:
    print(dumps_for(sys.stdout, obj, **kwargs))
