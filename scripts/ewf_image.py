#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read physical EWF media and logical file entries using the standard library."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import zlib
from contextlib import contextmanager
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__:
    from . import ewf2, ewf_logical
else:
    import ewf2
    import ewf_logical

EwfError = ewf2.EwfError

EVF_SIGNATURE = b"EVF\x09\x0d\x0a\xff\x00"
EVF2_SIGNATURE = ewf2.EVF2
LOGICAL_V1 = (b"LVF" + EVF_SIGNATURE[3:], b"LEF" + EVF_SIGNATURE[3:])
SECTION_DESCRIPTOR_SIZE = 76
MAX_L01_TABLE_ENTRIES = 65534  # EWF specification, EnCase 6/7 table header

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


def segment_paths(first: Path) -> list[Path]:
    """image.E01 → [image.E01, image.E02, …] (존재하는 연속 세그먼트만)."""
    m = re.match(r"^(.*)\.([EeLl][Xx]?)01$", first.name)
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
            first_c = letter[:-1] + chr(ord(letter[-1]) + k // 676)
            ext = first_c + chr(ord("A") + (k // 26) % 26) + chr(ord("A") + k % 26)
            if letter[0].islower():
                ext = ext.lower()
        cand = first.with_name(f"{stem}.{ext}")
        if cand.is_symlink():
            raise EwfError(f"{cand.name}: linked continuation segment is unsupported")
        if not cand.is_file():
            break
        paths.append(cand)
        n += 1
    return paths


def _read_sections(f, path: Path, number: int, signature: bytes):
    """세그먼트 파일의 섹션 목록 [(type, offset, size)]."""
    f.seek(0)
    head = f.read(13)
    if head[:8] != signature or signature not in (EVF_SIGNATURE, *LOGICAL_V1):
        raise EwfError(f"{path.name}: invalid EWF segment signature")
    if len(head) != 13 or struct.unpack_from("<H", head, 9)[0] != number:
        raise EwfError(f"{path.name}: 세그먼트 번호 불일치 ({number})")
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
        if stype in ("done", "next"):
            if signature in LOGICAL_V1 and (next_off != offset or size not in (0, SECTION_DESCRIPTOR_SIZE)
                                            or offset + SECTION_DESCRIPTOR_SIZE != file_size):
                raise EwfError(f"{path.name}: invalid logical segment terminator")
            break
        if size < SECTION_DESCRIPTOR_SIZE or offset + size > min(file_size, next_off):
            raise EwfError(f"{path.name}: 섹션 범위 오류 ({stype} @ {offset})")
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
    if re.fullmatch(r"-?[0-9]+", raw):
        try:
            dt = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=int(raw))
        except (ValueError, OverflowError):
            return raw
        return dt.isoformat(sep=" ", timespec="seconds")[:-6] + " UTC"
    parts = raw.split()
    if len(parts) >= 6 and all(re.fullmatch(r"[0-9]{1,4}", p) for p in parts[:6]):
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
        self.stored: dict[str, list[str]] = {"md5": [], "sha1": []}
        self.chunks: list[tuple[int, int, int, str]] = []
        self.format_note = ""
        with self.first.open("rb") as f:
            self.signature = f.read(8)
        self.logical = self.signature in (*LOGICAL_V1, ewf2.LEF2)
        self.codec = 1
        self.entries = []
        self.ranges = []
        self.unparsed_sections = set()
        if self.signature in (ewf2.EVF2, ewf2.LEF2):
            tree = ewf2.parse(self)
        else:
            tree = self._parse()
        if self.logical:
            size, self.entries, self.ranges = ewf_logical.parse_tree(tree)
            self.media["media_size"] = size
            self.format_note = "EWF2-Lx01" if self.signature == ewf2.LEF2 else "EWF-L01"
        if self.logical or self.signature == ewf2.EVF2:
            chunk_size = self.media["sectors_per_chunk"] * self.media["bytes_per_sector"]
            if not 0 < chunk_size <= ewf2.MAX_CHUNK:
                raise EwfError("unsupported EWF chunk size")
            expected = (self.media["media_size"] + chunk_size - 1) // chunk_size
            if len(self.chunks) != expected or self.media["chunk_count"] not in (0, expected):
                raise EwfError("EWF chunk count disagrees with stream length")

    @property
    def stored_md5(self):
        return self.stored["md5"][-1] if self.stored["md5"] else None

    @property
    def stored_sha1(self):
        return self.stored["sha1"][-1] if self.stored["sha1"] else None

    def _parse(self):
        tree = None
        header2_seen = False
        set_identifier = None
        for seg_idx, path in enumerate(self.segments):
            with path.open("rb") as f:
                sections = _read_sections(f, path, seg_idx + 1, self.signature)
                expected = "done" if seg_idx + 1 == len(self.segments) else "next"
                if sections[-1][0] != expected:
                    raise EwfError(f"{path.name}: 세그먼트 종료 섹션 불일치 ({expected} 필요)")
                sectors_range = None
                for stype, off, size in sections:
                    data_off = off + SECTION_DESCRIPTOR_SIZE
                    data_len = max(0, size - SECTION_DESCRIPTOR_SIZE)
                    if self.logical and stype in ("ltypes", "map", "table2", "session", "error2"):
                        self.unparsed_sections.add(stype)
                        continue
                    if self.logical and stype not in ("header2", "header", "volume", "disk", "data", "sectors", "ltree",
                                                      "table", "hash", "digest", "next", "done"):
                        raise EwfError(f"unsupported logical section {stype!r}")
                    if stype in ("header2", "header") and not (stype == "header" and header2_seen):
                        f.seek(data_off)
                        if self.logical and data_len > ewf2.MAX_METADATA:
                            raise EwfError("unsupported logical header size")
                        raw = f.read(data_len)
                        try:
                            dec = (ewf2.unpack_stream(raw, 1, ewf2.MAX_METADATA) if self.logical
                                   else zlib.decompress(raw))
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
                    elif (stype in ("volume", "disk") and not self.media) or (self.logical and stype == "data"):
                        f.seek(data_off)
                        raw = f.read(min(data_len, 1052))
                        if len(raw) < 24:
                            raise EwfError(f"{path.name}: volume 섹션이 짧음")
                        media_type = raw[0]
                        chunk_count, spc, bps = struct.unpack_from("<III", raw, 4)
                        if self.logical and media_type != 14:
                            raise EwfError("unexpected logical volume type")
                        if self.logical:
                            if data_len != 1052:
                                raise EwfError("invalid logical volume/data length")
                            if any(raw[-4:]):
                                ewf2.check_adler(raw[:-4], raw[-4:])
                            guid = raw[64:80]
                            if any(guid):
                                if set_identifier is not None and guid != set_identifier:
                                    raise EwfError("conflicting logical segment set identifiers")
                                set_identifier = guid
                            if stype == "data":
                                if (spc, bps) != (self.media.get("sectors_per_chunk"), self.media.get("bytes_per_sector")):
                                    raise EwfError("conflicting logical chunk geometry")
                                continue
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
                    elif stype == "ltree":
                        if not self.logical or tree is not None:
                            raise EwfError("unexpected or duplicate ltree")
                        f.seek(data_off)
                        tree = ewf_logical.read_ltree(f, data_len)
                    elif stype == "sectors":
                        if self.logical and sectors_range is not None:
                            raise EwfError("missing logical sector table")
                        sectors_range = (data_off, off + size)
                    elif stype == "table":
                        if sectors_range is None:
                            raise EwfError(f"{path.name}: 'sectors' 섹션 없는 레이아웃은 미지원")
                        f.seek(data_off)
                        if data_len < 24:
                            raise EwfError(f"{path.name}: table 헤더가 잘림")
                        hdr = f.read(24)
                        count = struct.unpack_from("<I", hdr, 0)[0]
                        base = struct.unpack_from("<Q", hdr, 8)[0]
                        if count > (data_len - 24) // 4:
                            raise EwfError(f"{path.name}: table 섹션이 잘림")
                        if self.logical and (count > MAX_L01_TABLE_ENTRIES or count * 4 + 28 != data_len):
                            raise EwfError("unsupported logical sector table layout")
                        raw = f.read(count * 4)
                        if len(raw) < count * 4:
                            raise EwfError(f"{path.name}: table 섹션이 잘림")
                        if self.logical:
                            ewf2.check_adler(hdr[:20], hdr[20:])
                            ewf2.check_adler(raw, ewf2.read_exact(f, 4))
                        entries = struct.unpack(f"<{count}I", raw)
                        offs = [(e & 0x7FFFFFFF) + base for e in entries]
                        for i, e in enumerate(entries):
                            start = offs[i]
                            end = offs[i + 1] if i + 1 < count else sectors_range[1]
                            if not (sectors_range[0] <= start < end <= sectors_range[1]):
                                raise EwfError(f"{path.name}: 청크 오프셋이 sectors 범위를 벗어남")
                            mode = "compressed" if e & 0x80000000 else "v1_raw"
                            if self.logical and end - start > ewf2.MAX_CHUNK + 65536:
                                raise EwfError("unsupported logical chunk size")
                            self.chunks.append((seg_idx, start, end - start, mode))
                        if self.logical:
                            sectors_range = None
                    elif stype in ("hash", "digest"):
                        required = 16 if stype == "hash" else 36
                        if data_len < required:
                            raise EwfError(f"{path.name}: {stype} 섹션이 잘림")
                        f.seek(data_off)
                        if self.logical:
                            length = 36 if stype == "hash" else 80
                            if data_len != length:
                                raise EwfError("invalid logical media hash record length")
                            record = ewf2.read_exact(f, length)
                            ewf2.check_adler(record[:-4], record[-4:])
                            raw = record[:required]
                        else:
                            raw = f.read(required)
                        for algo, value in (("md5", raw[:16]), ("sha1", raw[16:])):
                            if any(value) and value.hex() not in self.stored[algo]:
                                self.stored[algo].append(value.hex())
                if self.logical and sectors_range is not None:
                    raise EwfError("missing logical sector table")
        if not self.media:
            raise EwfError("volume/disk 섹션 없음 — 미디어 크기를 알 수 없음")
        if self.media["chunk_count"] and len(self.chunks) != self.media["chunk_count"]:
            raise EwfError(f"청크 수 불일치: volume {self.media['chunk_count']} / table {len(self.chunks)}")

        return tree

    def metadata(self) -> dict:
        acq = {}
        for k, name in HEADER_KEYS.items():
            v = self.header.get(k, "")
            if k in ("m", "u"):
                v = _format_header_date(v)
            if v:
                acq[name] = v
        result = {
            "image": self.first.name,
            "segments": [p.name for p in self.segments],
            "format": self.format_note,
            "media": self.media,
            "acquisition": acq,
            "stored_hashes": {"md5": self.stored_md5, "sha1": self.stored_sha1},
        }

        if self.unparsed_sections:
            result["unparsed_sections"] = sorted(self.unparsed_sections)
        if self.logical:
            result["entries"] = self.entries
        return result

    @contextmanager
    def chunk_reader(self):
        chunk_size = self.media["sectors_per_chunk"] * self.media["bytes_per_sector"]
        handle = None
        current = None

        @lru_cache(maxsize=1)
        def read(index):
            nonlocal handle, current
            segment, offset, size, mode = self.chunks[index]
            want = min(chunk_size, self.media["media_size"] - index * chunk_size)
            if want <= 0:
                raise EwfError("chunk exceeds media size")
            lengths = {want, chunk_size}
            if self.logical:
                sector = self.media["bytes_per_sector"]
                lengths.add((want + sector - 1) // sector * sector)
            if mode == "pattern":
                return (offset.to_bytes(8, "little") * ((want + 7) // 8))[:want]
            if segment != current:
                if handle:
                    handle.close()
                handle = self.segments[segment].open("rb")
                current = segment
            handle.seek(offset)
            raw = ewf2.read_exact(handle, size)
            if mode == "compressed":
                data = ewf2.unpack_stream(raw, self.codec, min(chunk_size, sys.maxsize - 1),
                                          allow_trailing=self.signature == EVF_SIGNATURE)
            elif mode == "adler" or (mode == "v1_raw" and (self.logical or size - 4 in lengths)):
                data = raw[:-4]
                ewf2.check_adler(data, raw[-4:])
            else:
                data = raw
            if len(data) not in lengths:
                raise EwfError(f"invalid chunk length at offset {offset}")
            return data[:want]

        try:
            yield read
        finally:
            read.cache_clear()
            if handle:
                handle.close()

    def iter_media(self):
        total = 0
        with self.chunk_reader() as read:
            for index in range(len(self.chunks)):
                data = read(index)
                total += len(data)
                yield data
        if total != self.media["media_size"]:
            raise EwfError("incomplete media data")

    def iter_entry(self, entry_id, read):
        if not self.logical or not 0 <= entry_id < len(self.entries):
            raise EwfError("unknown logical entry ID")
        chunk_size = self.media["bytes_per_sector"] * self.media["sectors_per_chunk"]
        for offset, length in self.ranges[entry_id]:
            while length:
                index, start = divmod(offset, chunk_size)
                data = read(index)[start:start + length]
                if not data:
                    raise EwfError("incomplete logical extent")
                yield data
                offset += len(data)
                length -= len(data)

    def compute_hashes(self):
        return hash_blocks(self.iter_media())


def hash_blocks(blocks, output=None):
    hashes = {k: hashlib.new(k) for k in ("md5", "sha1", "sha256")}
    total = 0
    for data in blocks:
        for digest in hashes.values():
            digest.update(data)
        if output is not None:
            output.write(data)
        total += len(data)
    return {**{k: h.hexdigest() for k, h in hashes.items()}, "bytes": total}


def verify_entry(img, entry, read, output=None):
    computed = hash_blocks(img.iter_entry(entry["id"], read), output)
    compared = {k: "match" if computed[k] == v else "mismatch"
                for k, v in entry["stored_hashes"].items()}
    status = "no_stored_hash" if not compared else "verified"
    if "mismatch" in compared.values():
        status = "mismatch"
    entry.update(computed_hashes=computed, comparison=compared, status=status)
    return status


def extract_entry(img, entry_id, destination):
    if not img.logical or not 0 <= entry_id < len(img.entries):
        raise EwfError("unknown logical entry ID")
    entry = img.entries[entry_id]
    if entry["type"] == "directory" and not entry["size"]:
        raise EwfError("directory has no file data")
    destination = Path(destination)
    output = destination.open("xb")
    try:
        with output, img.chunk_reader() as read:
            status = verify_entry(img, entry, read, output)
    except BaseException:
        destination.unlink()
        raise
    result = {"image": img.first.name, **entry}
    if status == "mismatch":
        destination.unlink()
    else:
        result["extracted_to"] = str(destination)
    return result


def examine(path: str | Path, verify: bool = True, *, extract_id=None, output=None) -> dict:
    """보고서용 결과 dict. 실패도 status로 기록한다(예외를 밖으로 던지지 않음)."""
    path = Path(path)
    try:
        img = EwfImage(path)
    except (EwfError, OSError, UnicodeError, struct.error) as e:
        return {"image": path.name, "status": "unsupported", "error": str(e)}
    if extract_id is not None:
        try:
            return extract_entry(img, extract_id, output)
        except (EwfError, OSError) as exc:
            return {"image": path.name, "status": "read_error", "error": str(exc)}
    result = img.metadata()
    conflicts = {k: v for k, v in img.stored.items() if len(v) > 1}
    if conflicts:
        result["conflicting_stored_hashes"] = conflicts
    if img.logical:
        for entry in img.entries:
            entry["status"] = "not_verified"
    if not verify:
        result["status"] = "not_verified"
        return result
    try:
        computed = img.compute_hashes()
        if img.logical:
            statuses = set()
            with img.chunk_reader() as read:
                for entry in img.entries:
                    if entry["type"] == "file" or entry["size"] or entry["stored_hashes"]:
                        statuses.add(verify_entry(img, entry, read))
            result["entry_verification"] = ("mismatch" if "mismatch" in statuses else
                                            "verified" if statuses == {"verified"} else
                                            "partial" if "verified" in statuses else "no_stored_hash")
    except (EwfError, OSError) as e:
        result.update(status="read_error", error=str(e))
        return result
    result["computed_hashes"] = computed
    compared = {}
    for algo in ("md5", "sha1"):
        stored = result["stored_hashes"].get(algo)
        if stored:
            compared[algo] = "match" if stored == computed[algo] and algo not in conflicts else "mismatch"
    result["comparison"] = compared
    if not compared:
        result["status"] = "no_stored_hash"
    elif all(v == "match" for v in compared.values()):
        result["status"] = "verified"
    else:
        result["status"] = "mismatch"
    if img.logical:
        checks = {result["entry_verification"]}
        if compared:
            checks.add(result["status"])
        result["status"] = ("mismatch" if "mismatch" in checks else
                            "verified" if checks == {"verified"} else
                            "no_stored_hash" if checks == {"no_stored_hash"} else "partial")
    return result


STATUS_EXIT = {"verified": 0, "not_verified": 0, "mismatch": 1, "unsupported": 3,
               "read_error": 3, "no_stored_hash": 4, "partial": 4}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EWF 이미지 메타데이터 + 저장 해시 재계산 대조 (로컬 전용)")
    ap.add_argument("image", help="첫 세그먼트 (.E01, .Ex01, .L01, .Lx01)")
    ap.add_argument("--no-verify", action="store_true", help="해시 재계산 생략")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--extract", type=int, metavar="ID", help="논리 항목 번호로 파일 추출 및 검증")
    ap.add_argument("--output", type=Path, help="추출할 새 파일 경로")
    args = ap.parse_args(argv)
    if (args.extract is None) != (args.output is None) or (args.extract is not None and args.no_verify):
        ap.error("--extract ID requires --output PATH and verification")

    res = examine(args.image, verify=not args.no_verify, extract_id=args.extract, output=args.output)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"이미지: {res['image']}  상태: {res['status']}")
        if res.get("error"):
            print(f"  오류: {res['error']}")
        if res.get("unparsed_sections"):
            print("  해석을 생략한 보조 섹션: " + ", ".join(res["unparsed_sections"]))
        for k, v in (res.get("acquisition") or {}).items():
            print(f"  {k}: {v}")
        media = res.get("media") or {}
        if "entries" in res:
            print(f"  논리 스트림: {media['media_size']} bytes")
            for entry in res["entries"]:
                print(f"  [{entry['id']}] {entry['type']} {entry['path']!r} ({entry['size']} bytes) "
                      f"{entry['status']} {entry['stored_hashes']} {entry['timestamps']}")
        elif media:
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
