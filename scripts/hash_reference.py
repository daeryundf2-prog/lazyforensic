#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hash_reference.py — 참조 해시 목록(획득 로그·납품 해시표) 판독과 대조

분석관이 받은 '기준 해시'(획득 도구 로그, 의뢰인이 준 해시표, 공개 데이터셋 해시 목록)를
읽어 (파일명, 알고리즘, 해시) 항목으로 만든다. 알고리즘은 길이로 정한다
(32hex=MD5, 40hex=SHA-1, 64hex=SHA-256).

읽는 형식 (한 파일 안에 섞여 있어도 된다):
  1. 줄 단위: 한 줄에 해시 1개 + 파일명 1개 — `sha256sum`/`md5sum` 출력,
     `파일명  크기  해시` 표 등. `SHA256 (파일) = 해시`(BSD) 포함.
  2. CSV: 머리행에 파일명 열(filename/file/name/path/파일명/경로)과
     해시 열(md5/sha1/sha-1/sha256/sha-256)이 있는 표 (FTK·EnCase 내보내기).
  3. 블록: `Filename: …`(또는 File Name/파일명/Original Filename) 줄 뒤에
     오는 `MD5: …`/`SHA1: …`/`SHA 1: …`/`SHA-256: …` 줄. 빈 줄이 블록을 끊는다.

한 줄에 해시가 2개 이상이거나 파일명 후보가 2개 이상이면 그 줄은 버린다(추측 금지).

사용:
    python scripts/hash_reference.py 해시목록.txt [--json]

알려진 한계:
- 파일명 대조는 경로 끝부분 일치 → 고유한 파일명 일치 순으로 한다.
  같은 이름의 파일이 여러 개면 '미확인'으로 남긴다.
- 형식을 못 알아본 줄은 조용히 건너뛴다. 읽힌 항목 수를 반드시 확인할 것.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_json import print_json  # noqa: E402

HEX_RE = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{64}|[0-9A-Fa-f]{40}|[0-9A-Fa-f]{32})(?![0-9A-Fa-f])")
ALGO_BY_LEN = {32: "md5", 40: "sha1", 64: "sha256"}
FILENAME_LABEL_RE = re.compile(
    r"^\s*(?:original\s+)?(?:file\s*name|filename|file|파일명|파일 이름|파일)\s*[:=]\s*(.+?)\s*$",
    re.IGNORECASE)
HASH_LABEL_RE = re.compile(
    r"^\s*(md5|sha[\s_-]?1|sha[\s_-]?256)(?:\s*(?:hash|checksum|해시))?\s*[:=]\s*([0-9A-Fa-f]{32,64})\s*$",
    re.IGNORECASE)
BSD_RE = re.compile(r"^\s*(MD5|SHA1|SHA256)\s*\((.+)\)\s*=\s*([0-9A-Fa-f]{32,64})\s*$", re.IGNORECASE)
# 파일명 후보: 확장자가 있거나 경로 구분자가 있는 토큰
NAME_TOKEN_RE = re.compile(r"^[^\s]*(?:[/\\][^\s]+|\.[A-Za-z0-9]{1,8})$")
CSV_NAME_COLS = ("filename", "file name", "file", "name", "path", "full path", "파일명", "파일", "경로", "이름")
CSV_HASH_COLS = {"md5": "md5", "md5 hash": "md5", "sha1": "sha1", "sha-1": "sha1", "sha1 hash": "sha1",
                 "sha256": "sha256", "sha-256": "sha256", "sha256 hash": "sha256"}


def _norm_algo(label: str) -> str:
    s = re.sub(r"[\s_-]", "", label.lower())
    return {"md5": "md5", "sha1": "sha1", "sha256": "sha256"}[s]


def _entry(name: str, algo: str, value: str, source: str, line: int) -> dict:
    return {"name": name.strip().strip('"').replace("\\", "/"), "algorithm": algo,
            "hash": value.lower(), "source": source, "line": line}


def _parse_csv(text: str, source: str) -> list[dict] | None:
    first = text.lstrip("\ufeff").splitlines()[0] if text.strip() else ""
    if "," not in first and "\t" not in first:
        return None
    dialect = "excel-tab" if first.count("\t") > first.count(",") else "excel"
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff")), dialect=dialect))
    if not rows:
        return None
    header = [h.strip().lower() for h in rows[0]]
    name_idx = next((i for i, h in enumerate(header) if h in CSV_NAME_COLS), None)
    hash_idx = {i: CSV_HASH_COLS[h] for i, h in enumerate(header) if h in CSV_HASH_COLS}
    if name_idx is None or not hash_idx:
        return None
    out = []
    for n, row in enumerate(rows[1:], start=2):
        if name_idx >= len(row) or not row[name_idx].strip():
            continue
        for i, algo in hash_idx.items():
            if i < len(row):
                v = row[i].strip()
                if HEX_RE.fullmatch(v) and ALGO_BY_LEN[len(v)] == algo:
                    out.append(_entry(row[name_idx], algo, v, source, n))
    return out


def parse_text(text: str, source: str = "") -> list[dict]:
    csv_entries = _parse_csv(text, source)
    if csv_entries is not None:
        return csv_entries
    out: list[dict] = []
    block_name: str | None = None
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            block_name = None
            continue
        m = BSD_RE.match(line)
        if m:
            algo = _norm_algo(m.group(1))
            if len(m.group(3)) == {"md5": 32, "sha1": 40, "sha256": 64}[algo]:
                out.append(_entry(m.group(2), algo, m.group(3), source, n))
            continue
        m = FILENAME_LABEL_RE.match(line)
        if m:
            block_name = m.group(1)
            # 'Original Filename: X  in: image.raw' 같은 꼬리 정보 제거
            block_name = re.split(r"\s{2,}in:\s|\s+in:\s", block_name)[0].strip()
            continue
        m = HASH_LABEL_RE.match(line)
        if m:
            algo = _norm_algo(m.group(1))
            value = m.group(2)
            if block_name and len(value) == {"md5": 32, "sha1": 40, "sha256": 64}[algo]:
                out.append(_entry(block_name, algo, value, source, n))
            continue
        hashes = HEX_RE.findall(line)
        if len(hashes) != 1:
            continue
        # sha256sum uses one space plus a mode character (space or "*").
        # Everything after it is one path, including embedded spaces.
        checksum = re.fullmatch(r"([0-9a-fA-F]+) [ *](.+)", line)
        if checksum and checksum[1] == hashes[0]:
            out.append(_entry(checksum[2], ALGO_BY_LEN[len(hashes[0])], hashes[0], source, n))
            continue
        rest = HEX_RE.sub(" ", line)
        tokens = [t.lstrip("*") for t in rest.split() if NAME_TOKEN_RE.match(t.lstrip("*"))]
        if len(tokens) != 1:
            continue
        out.append(_entry(tokens[0], ALGO_BY_LEN[len(hashes[0])], hashes[0], source, n))
    return out


def load_reference(path: str | Path) -> list[dict]:
    p = Path(path)
    data = p.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16", "replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp949", "replace")
    return parse_text(text, p.name)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def match_reference(entries: list[dict], files: list[dict]) -> list[dict]:
    """files: [{'path': 상대경로, 'md5':…, 'sha1':…, 'sha256':…}] → 항목별 대조 결과.

    result: match | mismatch | not_found | ambiguous

    문자 그대로 같은 이름이 없을 때만 유니코드 정규형(NFC)을 맞춰 다시 찾는다 (macOS의 NFD 한글 파일명 등).
    이때 후보가 하나일 때만 대조하고 normalization에 방식을 남긴다. name·file은 각각 목록과 증거의 원형이다.
    """
    by_base: dict[str, list[tuple[str, dict]]] = {}
    by_nfc: dict[str, list[tuple[str, dict]]] = {}
    for f in files:
        path = f["path"].replace("\\", "/")
        base = path.rsplit("/", 1)[-1]
        by_base.setdefault(base.casefold(), []).append((path, f))
        by_nfc.setdefault(_nfc(base).casefold(), []).append((_nfc(path), f))
    results = []
    for e in entries:
        name = e["name"].replace("\\", "/")
        folded = name.casefold()
        candidates = by_base.get(folded.rsplit("/", 1)[-1], [])
        normalized = {}
        if not candidates:
            name = _nfc(name)
            folded = name.casefold()
            candidates = by_nfc.get(folded.rsplit("/", 1)[-1], [])
            if candidates:
                normalized = {"normalization": "NFC",
                              "note": "목록과 증거의 유니코드 정규형이 달라 NFC로 맞춰 대조"}
        exact = [f for p, f in candidates if p == name]
        suffix = [f for p, f in candidates if p.casefold() == folded
                  or p.casefold().endswith("/" + folded) or folded.endswith("/" + p.casefold())]
        matches = exact or suffix or [f for _, f in candidates]
        how = "path" if exact else ("path_suffix" if suffix else "basename")
        if len(matches) > 1:
            note = f"같은 이름 {len(matches)}개"
            if normalized:
                note = f"유니코드 정규화(NFC) 후 같은 이름 {len(matches)}개 — 대조 보류"
            results.append({**e, "result": "ambiguous", "file": None, **normalized, "note": note})
            continue
        target = matches[0] if matches else None
        if target is None:
            results.append({**e, "result": "not_found", "file": None})
            continue
        actual = target.get(e["algorithm"])
        if not actual:
            results.append({**e, "result": "not_found", "file": target["path"], **normalized,
                            "note": f"{e['algorithm']} 미계산"})
            continue
        results.append({**e, "result": "match" if actual == e["hash"] else "mismatch",
                        "file": target["path"], "matched_by": how, "actual": actual, **normalized})
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="참조 해시 목록 판독 (로컬 전용)")
    ap.add_argument("reference", nargs="+", help="해시 목록 파일")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    entries = [e for r in args.reference for e in load_reference(r)]
    if args.json:
        print_json(entries, indent=2)
    else:
        for e in entries:
            print(f"{e['algorithm']:6} {e['hash']}  {e['name']}  ({e['source']}:{e['line']})")
        print(f"읽은 항목 {len(entries)}개", file=sys.stderr)
    return 0 if entries else 3


if __name__ == "__main__":
    sys.exit(main())
