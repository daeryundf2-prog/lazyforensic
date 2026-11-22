"""EWF2 section descriptors, sector tables and chunk codecs (libewf EWF2 specification)."""
import bz2
import hashlib
import re
import struct
import zlib

EVF2 = b"EVF2\r\n\x81\x00"
LEF2 = b"LEF2\r\n\x81\x00"
MAX_METADATA = 64 * 1024 * 1024
MAX_CHUNK = 32 * 1024 * 1024


class EwfError(Exception):
    """Incomplete, invalid or unsupported evidence."""


def read_exact(f, size):
    data = f.read(size)
    if len(data) != size:
        raise EwfError("truncated EWF data")
    return data


def check_adler(data, checksum):
    if zlib.adler32(data) != int.from_bytes(checksum, "little"):
        raise EwfError("EWF Adler-32 mismatch")


def unpack_bzip_block(raw, limit):
    # EnCase stores a single bzip2 block without stream framing. The
    # end marker is bit-aligned. Accept only a complete CRC-checked stream.
    if len(raw) < 11 or len(raw) > 1024 * 1024:
        raise EwfError("unsupported bzip2 block size")
    trailer = int.from_bytes(bytes.fromhex("177245385090") + raw[6:10], "big")
    for pad in range(8):
        if raw[-1] & ((1 << pad) - 1):
            continue
        # Keep 8 - pad data bits and append 80 trailer bits and pad zeroes.
        tail = (raw[-1] << 80) | (trailer << pad)
        stream = bz2.BZ2Decompressor()
        try:
            data = stream.decompress(b"BZh9" + raw[:-1] + tail.to_bytes(11, "big"), limit + 1)
        except OSError:
            continue
        if stream.eof and not stream.unused_data and len(data) <= limit:
            return data
    raise EwfError("invalid or unsupported bzip2 block")


def unpack_stream(raw, method, limit, *, allow_trailing=False):
    if method == 0:
        if len(raw) <= limit:
            return raw
    elif method in (1, 2):
        if method == 2 and raw.startswith(b"1AY&SY"):
            return unpack_bzip_block(raw, limit)
        stream = zlib.decompressobj() if method == 1 else bz2.BZ2Decompressor()
        try:
            data = stream.decompress(raw, limit + 1)
        except (OSError, zlib.error) as exc:
            raise EwfError("invalid compressed EWF stream") from exc
        if stream.eof and (allow_trailing or not stream.unused_data) and len(data) <= limit:
            return data
    raise EwfError("unsupported, truncated or oversized EWF stream")


def descriptors(f):
    end = f.seek(0, 2)
    sections = []
    while True:
        pos = end - 64
        if pos < 32:
            raise EwfError("truncated EWF2 descriptor chain")
        f.seek(pos)
        desc = read_exact(f, 64)
        check_adler(desc[:60], desc[60:])
        kind, flags, previous, size, length, pad = struct.unpack_from("<IIQQII", desc)
        start = previous + 64 if previous else 32
        if length != 64 or pad > size or pos - size != start:
            raise EwfError("invalid EWF2 section bounds")
        if flags & ~1 or kind in (7, 11, 12):
            raise EwfError(f"unsupported EWF2 section {kind:#x} or flags {flags:#x}")
        if kind == 0x20 and not flags & 1:
            raise EwfError("missing EWF2 single files integrity hash")
        if flags & 1:
            f.seek(start)
            digest = hashlib.md5()
            left = size
            while left:
                block = read_exact(f, min(left, 1024 * 1024))
                digest.update(block)
                left -= len(block)
            if digest.digest() != desc[32:48]:
                raise EwfError("EWF2 section integrity hash mismatch")
        sections.append((kind, start, size, pad))
        if not previous:
            return sections[::-1]
        end = start


def sector_table(raw, chunks, segment, bounds, codec):
    if len(raw) < 48:
        raise EwfError("short EWF2 sector table")
    first, count = struct.unpack_from("<QI", raw)
    if first != len(chunks) or len(raw) != 48 + 16 * count:
        raise EwfError("non-contiguous or truncated EWF2 sector table")
    check_adler(raw[:16], raw[16:20])
    entries = memoryview(raw)[32:-16]
    check_adler(entries, raw[-16:-12])
    for offset, size, flags in struct.iter_unpack("<QII", entries):
        if not flags & 1:
            flags &= ~4  # PATTERNFILL only has meaning on compressed chunks.
        if flags == 1 and not codec:
            raise EwfError("compressed chunk without a compression method")
        if flags == 5 and size in (0, 8):
            mode = "pattern"
        elif flags in (0, 1, 2) and 0 < size <= MAX_CHUNK + 65536 and bounds[0] <= offset <= bounds[1] - size:
            mode = ("raw", "compressed", "adler")[flags]
        else:
            raise EwfError("unsupported EWF2 chunk flags, size or sector data range")
        chunks.append((segment, offset, size, mode))


def main_fields(raw, codec):
    data = unpack_stream(raw, codec, MAX_METADATA)
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-16-le"
    text = data.decode(encoding).rstrip("\0").replace("\r\n", "\n")
    lines = text.split("\n")
    if len(lines) < 4 or lines[:2] != ["1", "main"]:
        raise EwfError("unsupported EWF2 device or case metadata object")
    keys = lines[2].split("\t")
    values = lines[3].split("\t")
    if len(keys) != len(values) or len(set(keys)) != len(keys) or any(lines[4:]):
        raise EwfError("invalid EWF2 metadata columns")
    escapes = {1: "\n", 2: "\r", 3: "\t"}
    return {key: value.translate(escapes) for key, value in zip(keys, values)}


def geometry_value(value, bits):
    digits = value.lstrip("0") or "0"
    if not value.isascii() or not value.isdecimal() or len(digits) > 20 or int(digits) >= 1 << bits:
        raise EwfError("invalid EWF2 geometry")
    return str(int(digits))


def parse(image):
    device, case = {}, {}
    identity = None
    tree = None
    for index, path in enumerate(image.segments):
        with path.open("rb") as f:
            signature, major, minor, codec, number, guid = struct.unpack("<8sBBHI16s", read_exact(f, 32))
            current = signature, codec, guid
            if (signature != image.signature or (major, minor) != (2, 1) or codec not in (0, 1, 2)
                    or number != index + 1 or (identity is not None and current != identity)):
                raise EwfError(f"{path.name}: invalid EWF2 version, codec or segment identity")
            identity = current
            image.codec = codec
            sections = descriptors(f)
            terminal = 15 if index + 1 == len(image.segments) else 13
            if sections[-1][0] != terminal or sections[-1][2] or any(s[0] in (13, 15) for s in sections[:-1]):
                raise EwfError(f"{path.name}: missing segment or invalid EWF2 terminator")
            sectors = None
            for kind, start, size, pad in sections[:-1]:
                if kind == 3:
                    if sectors is not None:
                        raise EwfError("missing EWF2 sector table")
                    sectors = start, start + size  # includes inter-chunk alignment
                    continue
                if kind in (5, 6, 10, 14, 16) or (image.logical and kind in (0x21, 0x22, 0x23)):
                    image.unparsed_sections.add(hex(kind))
                    continue
                if kind not in (1, 2, 4, 8, 9, 0x20) or size > MAX_METADATA:
                    raise EwfError(f"unsupported EWF2 section {kind:#x} or metadata size")
                if kind in (8, 9):
                    if terminal != 15 or size != 32 or pad != (12 if kind == 8 else 8):
                        raise EwfError("invalid EWF2 media hash location, size or padding")
                f.seek(start)
                raw = read_exact(f, size if kind == 4 else size - pad)
                if kind in (1, 2):
                    fields = main_fields(raw, codec)
                    target = device if kind == 1 else case
                    for key, value in fields.items():
                        if value and key in ("bp", "sb", "ts", "tb"):
                            value = geometry_value(value, 32 if key in ("bp", "sb") else 64)
                            if target.get(key) not in (None, "0", value):
                                raise EwfError("conflicting EWF2 geometry")
                        if value:
                            target[key] = value
                elif kind == 4:
                    if sectors is None:
                        raise EwfError("EWF2 table without sector data")
                    if pad not in (0, 24):
                        raise EwfError("unsupported EWF2 table padding")
                    sector_table(raw, image.chunks, index, sectors, codec)
                    sectors = None
                elif kind in (8, 9):
                    algo, length = ("md5", 16) if kind == 8 else ("sha1", 20)
                    check_adler(raw[:length], raw[length:])
                    value = raw[:length].hex()
                    if value not in image.stored[algo]:
                        image.stored[algo].append(value)
                elif tree is None and image.signature == LEF2:
                    tree = raw
                else:
                    raise EwfError("unexpected EWF2 single files data")
            if sectors is not None:
                raise EwfError("missing EWF2 sector table")
    try:
        bps, spc = int(device["bp"]), int(case["sb"])
        sectors = int(device.get("ts") or 0)
        count = int(case.get("tb") or 0)
    except (KeyError, ValueError) as exc:
        raise EwfError("invalid EWF2 geometry") from exc
    if min(bps, spc) <= 0 or bps * spc > MAX_CHUNK or sectors < 0 or count < 0:
        raise EwfError("unsupported EWF2 geometry")
    if image.signature == EVF2 and not re.fullmatch(r"[0-9]+", device.get("ts", "")):
        raise EwfError("missing EWF2 sector count")
    drives = {"f": "fixed disk", "r": "removable disk", "m": "memory", "c": "optical disk",
              "l": "logical evidence", "a": "RAM disk", "p": "PALM"}
    image.media = dict(media_type=drives.get(device.get("dt"), "unknown"), bytes_per_sector=bps,
                       sectors_per_chunk=spc, sector_count=sectors, chunk_count=count,
                       media_size=sectors * bps)
    keys = {"cn": "c", "en": "n", "nm": "a", "ex": "e", "nt": "t",
            "av": "av", "os": "ov", "tt": "m", "at": "u"}
    image.header = {dest: case[key] for key, dest in keys.items() if key in case}
    image.header.update({dest: device[key] for key, dest in (("lb", "l"), ("sn", "sn"), ("md", "md")) if key in device})
    image.format_note = "EWF2-Lx01" if image.signature == LEF2 else "EWF2-Ex01"
    return tree
