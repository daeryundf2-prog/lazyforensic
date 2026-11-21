#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ewf_image.py — EnCase(E01) 이미지 메타데이터 판독 + 저장 해시 재계산 대조 (표준 라이브러리 전용)

E01(EWF-E01, EnCase 6 계열) 세그먼트 파일을 직접 읽어
  1. 획득 기록값(사건번호·증거번호·검사자·메모·획득 도구 버전·획득 OS·획득 일시),
  2. 미디어 정보(섹터 크기·섹터 수·청크 크기),
  3. 이미지에 저장된 MD5(hash 섹션)·SHA-1(digest 섹션),
  4. 압축 해제한 미디어 전체의 MD5·SHA-1·SHA-256 재계산값
을 뽑고, 저장값과 재계산값을 대조한다. 외부 도구(libewf/FTK Imager)가 없어도 동작한다.

사용:
    python scripts/ewf_image.py image.E01                 # 메타데이터 + 해시 재계산 대조
    python scripts/ewf_image.py image.E01 --no-verify     # 메타데이터·저장 해시만
    python scripts/ewf_image.py image.E01 --json

종료 코드: 0 = 저장 해시와 재계산 해시 일치, 1 = 불일치, 3 = 판독 불가/미지원 형식,
           4 = 재계산은 했으나 대조할 저장 해시 없음(미확인)

알려진 한계 (fail-closed):
- EWF-E01(EnCase 1~7 계열 .E01/.E02…)만 읽는다. Ex01(EWF2), SMART(.S01), 논리 증거(.L01)는
  '미지원'으로 끝낸다 — 추측으로 해석하지 않는다.
- 'sectors' 섹션이 없는 초기 레이아웃은 미지원으로 처리한다.
- 헤더 값(검사자·일시 등)은 획득 도구가 기록한 값이다. 진위 판단은 별도다.
- 재계산은 이미지 전체를 읽으므로 이미지 크기에 비례해 시간이 걸린다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

EVF_SIGNATURE = b"EVF\x09\x0d\x0a\xff\x00"
EVF2_SIGNATURE = b"EVF2\x0d\x0a\x81\x00"
SECTION_DESCRIPTOR_SIZE = 76

HEADER_KEYS = {
    "c": "case_number",
    "n": "evidence_number",
    "a": "description",
    "e": "examiner",
    "t": "notes",
    "av": "acquiry_software_version",
    "ov": "acquiry_os",
    "m": "acquisition_date",
    "u": "system_date",
    "md": "model",
    "sn": "serial_number",
    "l": "device_label",
}

MEDIA_TYPES = {0x00: "removable disk", 0x01: "fixed disk", 0x03: "optical disk",
               0x0E: "logical evidence", 0x10: "memory (RAM)"}


class EwfError(Exception):
    """판독 불가 또는 미지원 형식."""


def segment_paths(first: Path) -> list[Path]:
    """image.E01 → [image.E01, image.E02, …] (존재하는 연속 세그먼트만)."""
    m = re.match(r"^(.*)\.([Ee])01$", first.name)
    if not m:
        return [first]
    stem, letter = m.group(1), m.group(2)
    paths = [first]
    n = 2
    while True:
        if n <= 99:
            ext = f"{letter}{n:02d}"
        else:
            # E99 다음은 EAA, EAB … (libewf 명명 규칙)
            k = n - 100
            first_c = chr(ord(letter) + k // 26 // 26)
            ext = first_c + chr(ord("A") + (k // 26) % 26) + chr(ord("A") + k % 26)
            if letter.islower():
                ext = ext.lower()
        cand = first.with_name(f"{stem}.{ext}")
        if not cand.is_file():
            break
        paths.append(cand)
        n += 1
    return paths


def _read_sections(f, path: Path):
    """세그먼트 파일의 섹션 목록 [(type, offset, size)]."""
    f.seek(0)
    head = f.read(13)
    if head[:8] == EVF2_SIGNATURE[:8] or head[:4] == b"EVF2":
        raise EwfError(f"{path.name}: Ex01(EWF2) 형식은 미지원")
    if head[:8] != EVF_SIGNATURE:
        if head[:8] == b"LVF\x09\x0d\x0a\xff\x00":
            raise EwfError(f"{path.name}: 논리 증거(L01) 형식은 미지원")
        raise EwfError(f"{path.name}: EWF 서명이 아님")
    file_size = path.stat().st_size
    sections = []
    offset = 13
    seen = set()
    while True:
        if offset in seen or offset + SECTION_DESCRIPTOR_SIZE > file_size:
            raise EwfError(f"{path.name}: 섹션 체인 손상 (offset {offset})")
        seen.add(offset)
        f.seek(offset)
        desc = f.read(SECTION_DESCRIPTOR_SIZE)
        stype = desc[:16].rstrip(b"\x00").decode("ascii", "replace")
        next_off, size = struct.unpack_from("<QQ", desc, 16)
        stored_ck = struct.unpack_from("<I", desc, 72)[0]
        if zlib.adler32(desc[:72]) != stored_ck:
            raise EwfError(f"{path.name}: 섹션 기술자 체크섬 불일치 ({stype} @ {offset})")
        sections.append((stype, offset, size))
        if stype in ("done", "next") or next_off == offset:
            break
        offset = next_off
    return sections


def _parse_header_text(text: str) -> dict:
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n")]
    for i, ln in enumerate(lines):
        if ln.strip() == "main" and i + 2 < len(lines):
            keys = lines[i + 1].split("\t")
            vals = lines[i + 2].split("\t")
            return {k.strip(): (vals[j].strip() if j < len(vals) else "")
                    for j, k in enumerate(keys) if k.strip()}
    return {}


def _format_header_date(raw: str) -> str:
    """header2는 유닉스 초, header는 'YYYY MM DD HH MM SS'. 해석 실패 시 원문."""
    raw = raw.strip()
    if not raw:
        return ""
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    parts = raw.split()
    if len(parts) >= 6 and all(p.isdigit() for p in parts[:6]):
        y, mo, d, h, mi, s = (int(p) for p in parts[:6])
        # header 섹션의 시각은 획득 PC의 지역 시각이며 시간대가 기록되지 않는다.
        return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d} (시간대 미기록)"
    return raw


class EwfImage:
    def __init__(self, first_segment: str | Path):
        self.first = Path(first_segment)
        if not self.first.is_file():
            raise EwfError(f"파일 없음: {self.first}")
        self.segments = segment_paths(self.first)
        self.header: dict = {}
        self.media: dict = {}
        self.stored_md5: str | None = None
        self.stored_sha1: str | None = None
        self.chunks: list[tuple[int, int, int, bool]] = []  # (segment idx, offset, size, compressed)
        self.format_note = ""
        self._parse()

    def _parse(self):
        header2_seen = False
        last_done = False
        for seg_idx, path in enumerate(self.segments):
            with path.open("rb") as f:
                sections = _read_sections(f, path)
                sectors_range = None
                for stype, off, size in sections:
                    data_off = off + SECTION_DESCRIPTOR_SIZE
                    data_len = max(0, size - SECTION_DESCRIPTOR_SIZE)
                    if stype in ("header2", "header") and not (stype == "header" and header2_seen):
                        f.seek(data_off)
                        raw = f.read(data_len)
                        try:
                            dec = zlib.decompress(raw)
                        except zlib.error:
                            continue
                        if stype == "header2":
                            text = dec.decode("utf-16", "replace")
                            header2_seen = True
                        else:
                            text = dec.decode("cp1252", "replace")
                        parsed = _parse_header_text(text)
                        if parsed:
                            self.header = parsed
                    elif stype in ("volume", "disk") and not self.media:
                        f.seek(data_off)
                        raw = f.read(min(data_len, 1052))
                        if len(raw) < 24:
                            raise EwfError(f"{path.name}: volume 섹션이 짧음")
                        media_type = raw[0]
                        chunk_count, spc, bps = struct.unpack_from("<III", raw, 4)
                        if data_len >= 1052:
                            sector_count = struct.unpack_from("<Q", raw, 16)[0]
                            self.format_note = "EnCase (EWF-E01)"
                        else:
                            sector_count = struct.unpack_from("<I", raw, 16)[0]
                            self.format_note = "EWF-E01 (구형 volume)"
                        self.media = {
                            "media_type": MEDIA_TYPES.get(media_type, f"unknown(0x{media_type:02x})"),
                            "chunk_count": chunk_count,
                            "sectors_per_chunk": spc,
                            "bytes_per_sector": bps,
                            "sector_count": sector_count,
                            "media_size": sector_count * bps,
                        }
                    elif stype == "sectors":
                        sectors_range = (off, off + size)
                    elif stype == "table":
                        if sectors_range is None:
                            raise EwfError(f"{path.name}: 'sectors' 섹션 없는 레이아웃은 미지원")
                        f.seek(data_off)
                        hdr = f.read(24)
                        count = struct.unpack_from("<I", hdr, 0)[0]
                        base = struct.unpack_from("<Q", hdr, 8)[0]
                        raw = f.read(count * 4)
                        if len(raw) < count * 4:
                            raise EwfError(f"{path.name}: table 섹션이 잘림")
                        entries = struct.unpack(f"<{count}I", raw)
                        offs = [(e & 0x7FFFFFFF) + base for e in entries]
                        for i, e in enumerate(entries):
                            start = offs[i]
                            end = offs[i + 1] if i + 1 < count else sectors_range[1]
                            if not (sectors_range[0] <= start < end <= sectors_range[1]):
                                raise EwfError(f"{path.name}: 청크 오프셋이 sectors 범위를 벗어남")
                            self.chunks.append((seg_idx, start, end - start, bool(e & 0x80000000)))
                    elif stype == "hash":
                        f.seek(data_off)
                        raw = f.read(16)
                        if len(raw) == 16 and raw != b"\x00" * 16:
                            self.stored_md5 = raw.hex()
                    elif stype == "digest":
                        f.seek(data_off)
                        raw = f.read(36)
                        if len(raw) == 36:
                            if raw[:16] != b"\x00" * 16:
                                self.stored_md5 = raw[:16].hex()
                            if raw[16:36] != b"\x00" * 20:
                                self.stored_sha1 = raw[16:36].hex()
                    elif stype == "done":
                        last_done = True
        if not self.media:
            raise EwfError("volume/disk 섹션 없음 — 미디어 크기를 알 수 없음")
        if not last_done:
            raise EwfError("마지막 세그먼트에 'done' 섹션 없음 — 세그먼트 누락 또는 잘림")
        if self.media["chunk_count"] and len(self.chunks) != self.media["chunk_count"]:
            raise EwfError(f"청크 수 불일치: volume {self.media['chunk_count']} / table {len(self.chunks)}")

    def metadata(self) -> dict:
        acq = {}
        for k, name in HEADER_KEYS.items():
            v = self.header.get(k, "")
            if k in ("m", "u"):
                v = _format_header_date(v)
            if v:
                acq[name] = v
        return {
            "image": self.first.name,
            "segments": [p.name for p in self.segments],
            "format": self.format_note,
            "media": self.media,
            "acquisition": acq,
            "stored_hashes": {"md5": self.stored_md5, "sha1": self.stored_sha1},
        }

    def iter_media(self):
        chunk_size = self.media["sectors_per_chunk"] * self.media["bytes_per_sector"]
        remaining = self.media["media_size"]
        handles = {}
        try:
            for seg_idx, off, size, compressed in self.chunks:
                if remaining <= 0:
                    break
                fh = handles.get(seg_idx)
                if fh is None:
                    fh = handles[seg_idx] = self.segments[seg_idx].open("rb")
                fh.seek(off)
                raw = fh.read(size)
                if compressed:
                    try:
                        data = zlib.decompress(raw)
                    except zlib.error as e:
                        raise EwfError(f"청크 압축 해제 실패 (offset {off}): {e}") from e
                else:
                    want = min(chunk_size, remaining)
                    data = raw[:want]
                    tail = raw[want:want + 4]
                    if len(tail) == 4 and zlib.adler32(data) != struct.unpack("<I", tail)[0]:
                        raise EwfError(f"비압축 청크 체크섬 불일치 (offset {off})")
                data = data[:remaining]
                remaining -= len(data)
                yield data
        finally:
            for fh in handles.values():
                fh.close()
        if remaining:
            raise EwfError(f"미디어 데이터 부족: {remaining}바이트 모자람")

    def compute_hashes(self) -> dict:
        md5, sha1, sha256 = hashlib.md5(), hashlib.sha1(), hashlib.sha256()
        total = 0
        for data in self.iter_media():
            md5.update(data)
            sha1.update(data)
            sha256.update(data)
            total += len(data)
        return {"md5": md5.hexdigest(), "sha1": sha1.hexdigest(),
                "sha256": sha256.hexdigest(), "bytes": total}


def examine(path: str | Path, verify: bool = True) -> dict:
    """보고서용 결과 dict. 실패도 status로 기록한다(예외를 밖으로 던지지 않음)."""
    path = Path(path)
    try:
        img = EwfImage(path)
    except (EwfError, OSError, struct.error) as e:
        return {"image": path.name, "status": "unsupported", "error": str(e)}
    result = img.metadata()
    if not verify:
        result["status"] = "not_verified"
        return result
    try:
        computed = img.compute_hashes()
    except (EwfError, OSError) as e:
        result.update(status="read_error", error=str(e))
        return result
    result["computed_hashes"] = computed
    compared = {}
    for algo in ("md5", "sha1"):
        stored = result["stored_hashes"].get(algo)
        if stored:
            compared[algo] = "match" if stored == computed[algo] else "mismatch"
    result["comparison"] = compared
    if not compared:
        result["status"] = "no_stored_hash"
    elif all(v == "match" for v in compared.values()):
        result["status"] = "verified"
    else:
        result["status"] = "mismatch"
    return result


STATUS_EXIT = {"verified": 0, "not_verified": 0, "mismatch": 1, "unsupported": 3,
               "read_error": 3, "no_stored_hash": 4}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E01 이미지 메타데이터 + 저장 해시 재계산 대조 (로컬 전용)")
    ap.add_argument("image", help="첫 세그먼트 (.E01)")
    ap.add_argument("--no-verify", action="store_true", help="해시 재계산 생략")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    args = ap.parse_args(argv)

    res = examine(args.image, verify=not args.no_verify)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"이미지: {res['image']}  상태: {res['status']}")
        if res.get("error"):
            print(f"  오류: {res['error']}")
        for k, v in (res.get("acquisition") or {}).items():
            print(f"  {k}: {v}")
        media = res.get("media") or {}
        if media:
            print(f"  미디어: {media['media_size']} bytes ({media['sector_count']} × {media['bytes_per_sector']})")
        for algo, v in (res.get("stored_hashes") or {}).items():
            print(f"  저장 {algo.upper()}: {v or '없음'}")
        for algo, v in (res.get("computed_hashes") or {}).items():
            if algo != "bytes":
                print(f"  재계산 {algo.upper()}: {v}")
        for algo, v in (res.get("comparison") or {}).items():
            print(f"  대조 {algo.upper()}: {'일치' if v == 'match' else '불일치'}")
    return STATUS_EXIT.get(res["status"], 3)


if __name__ == "__main__":
    sys.exit(main())
