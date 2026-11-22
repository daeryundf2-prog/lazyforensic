"""Synthetic EWF2 segment chains and logical files with known source bytes."""
import bz2
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ewf_image
import report_draft


def adler(data):
    return struct.pack("<I", zlib.adler32(data))


def single_bzip_block(data):
    framed = bz2.compress(data)
    # Strip the BZh header and the bit-aligned end marker and combined CRC.
    bits = "".join(f"{b:08b}" for b in framed[4:])
    end = bits.rindex(f"{0x177245385090:048b}")
    block = bits[:end].ljust((end + 7) // 8 * 8, "0")
    return int(block, 2).to_bytes(len(block) // 8, "big")


def v2_section(image, previous, kind, payload=b"", pad=None, flags=0):
    if pad is None:
        pad = -len(payload) % 16
        payload += bytes(pad)
    offset = len(image) + len(payload)
    digest = hashlib.md5(payload).digest() if flags & 1 else bytes(16)
    desc = struct.pack("<IIQQII16s12x", kind, flags, previous, len(payload), 64, pad, digest)
    image.extend(payload + desc + adler(desc))
    return offset


def metadata(fields):
    text = "1\nmain\n" + "\t".join(fields) + "\n" + "\t".join(fields.values()) + "\n\n"
    return b"\xff\xfe" + text.encode("utf-16le")


MEDIA = bytes(range(256)) * 2 + b"EWF data" * 64
FILE = MEDIA[508:523]


def entry_tree(*, corrupt=False, extent="2 1fc 4 200 b", short=False):
    hashes = hashlib.md5(b"wrong" if corrupt else FILE).hexdigest(), hashlib.sha1(FILE).hexdigest()
    text = ("2\nrec\ntb\n1024\n\nentry\n1\t1\np\tn\tls\tbe\tcr\tha\tsha\n"
            "26\t1\n1\tLogicalEntries\t\t0\t\t\t\n"
            "26\t1\n1\tfolder\t\t0\t\t\t\n"
            f"26\t0\n\titem.txt\t15\t{extent}\t1234567890\t{hashes[0]}\t{hashes[1]}\n\n")
    if short:
        text = text.replace("26\t1\n1\tfolder", "26\t2\n1\tfolder")
    return text.encode("utf-16le")


def build_v2(path, *, codec=1, split=False, chunk_mode="compressed", tree=None, wrong=False,
             first_chunk_delta=0, second_guid=None, section_flags=0):
    compress = (lambda data: data, zlib.compress, bz2.compress)[codec]
    chunks = [MEDIA[:512], MEDIA[512:]]
    groups = [[0], [1]] if split else [[0, 1]]
    for seq, group in enumerate(groups, 1):
        guid = second_guid if seq == 2 and second_guid else bytes(range(16))
        sig = b"LEF2" if tree is not None else b"EVF2"
        out = bytearray(struct.pack("<8sBBHI16s", sig + b"\r\n\x81\0", 2, 1, codec, seq, guid))
        prev = v2_section(out, 0, 1, compress(metadata(dict(ts="2", bp="512", dt="f", md="SYNTH"))))
        prev = v2_section(out, prev, 2, compress(metadata(dict(sb="1", tb="2", cn="TEST", tt="1234567890"))))
        data = bytearray()
        entries = bytearray()
        for i in group:
            raw = chunks[i]
            offset = len(out) + len(data)
            if chunk_mode == "pattern" and i == 1:
                entries.extend(struct.pack("<QII", int.from_bytes(raw[:8], "little"), 0, 5))
                continue
            if chunk_mode == "raw":
                stored, flags = raw, 0
            elif chunk_mode == "block":
                stored, flags = single_bzip_block(raw), 1
            elif chunk_mode == "compressed" and codec:
                stored, flags = compress(raw), 1
            else:
                stored, flags = raw + adler(raw), 2
            data.extend(stored)
            data.extend(bytes(-len(stored) % 16))
            entries.extend(struct.pack("<QII", offset, len(stored), flags))
        prev = v2_section(out, prev, 3, bytes(data), pad=0)
        h = struct.pack("<QI4x", group[0] + first_chunk_delta, len(group))
        table = h + adler(h) + bytes(12) + entries + adler(entries) + bytes(12)
        prev = v2_section(out, prev, 4, table, pad=24)
        if seq == len(groups):
            if tree is not None:
                prev = v2_section(out, prev, 0x20, tree, flags=1)
            else:
                for kind, digest in [(8, hashlib.md5(b"wrong" if wrong else MEDIA).digest()),
                                     (9, hashlib.sha1(MEDIA).digest())]:
                    prev = v2_section(out, prev, kind, digest + adler(digest))
        v2_section(out, prev, 15 if seq == len(groups) else 13, flags=section_flags)
        path.with_suffix(path.suffix[:-2] + f"{seq:02d}").write_bytes(out)


def build_l01(path, tree, signature=b"LVF", split=False):
    header = hashlib.md5(tree).digest() + struct.pack("<Q24x", len(tree))
    ltree = header[:24] + adler(header) + header[28:] + tree
    groups = [[MEDIA[:512]], [MEDIA[512:]]] if split else [[MEDIA[:512], MEDIA[512:]]]
    for seq, chunks in enumerate(groups, 1):
        data = bytearray(signature + b"\x09\r\n\xff\0" + struct.pack("<BHH", 1, seq, 0))

        def section(kind, payload=b""):
            size = len(payload) + 76 if kind not in ("done", "next") else 0
            desc = struct.pack("<16sQQ40x", kind.encode(), len(data)+size, size)
            data.extend(desc + adler(desc) + payload)

        if seq == 1:
            section("volume", struct.pack("<B3xIIIQ", 14, 0, 1, 512, 999).ljust(1052, b"\0"))
        base = len(data) + 76
        stored = bytearray()
        entries = bytearray()
        for chunk in chunks:
            entries.extend(struct.pack("<I", 0x80000000 | len(stored)))
            stored.extend(zlib.compress(chunk))
        section("sectors", stored)
        h = struct.pack("<I4xQ4x", len(chunks), base)
        section("table", h + adler(h) + entries + adler(entries))
        if seq == len(groups):
            section("ltree", ltree)
        section("done" if seq == len(groups) else "next")
        path.with_suffix(f".L{seq:02d}").write_bytes(data)


@pytest.mark.parametrize(("codec", "mode"), [(0, "stored"), (0, "raw"), (1, "compressed"), (1, "pattern"),
                                             (2, "compressed"), (2, "block")])
@pytest.mark.parametrize("split", [True, False])
def test_physical_media(tmp_path, codec, mode, split):
    path = tmp_path / "disk.Ex01"
    build_v2(path, codec=codec, chunk_mode=mode, split=split)
    result = ewf_image.examine(path)
    assert result["status"] == "verified"
    assert result["comparison"] == {"md5": "match", "sha1": "match"}
    assert result["computed_hashes"]["sha256"] == hashlib.sha256(MEDIA).hexdigest()
    assert result["acquisition"]["case_number"] == "TEST"
    assert result["acquisition"]["model"] == "SYNTH"
    assert len(result["segments"]) == (2 if split else 1)


def test_media_mismatch(tmp_path):
    path = tmp_path / "disk.Ex01"
    build_v2(path, wrong=True)
    result = ewf_image.examine(path)
    assert result["status"] == "mismatch"
    assert result["comparison"] == {"md5": "mismatch", "sha1": "match"}


@pytest.mark.parametrize("damage", ["missing", "truncated", "identity", "chunk_number", "encrypted", "descriptor"])
def test_invalid_v2_chain(tmp_path, damage):
    path = tmp_path / "disk.Ex01"
    build_v2(path, split=True, second_guid=b"x"*16 if damage == "identity" else None,
             first_chunk_delta=int(damage == "chunk_number"), section_flags=2 if damage == "encrypted" else 0)
    second = path.with_suffix(".Ex02")
    if damage == "missing":
        second.unlink()
    elif damage == "truncated":
        second.write_bytes(second.read_bytes()[:-4])
    elif damage == "descriptor":
        second.write_bytes(second.read_bytes()[:-4] + bytes(4))
    assert ewf_image.examine(path, verify=False)["status"] == "unsupported"


@pytest.mark.parametrize("fmt", ["LVF", "LEF", "LEF2"])
@pytest.mark.parametrize("split", [False, True])
def test_logical_listing_extract_and_verify(tmp_path, fmt, split):
    path = tmp_path / ("logical.Lx01" if fmt == "LEF2" else "logical.L01")
    if fmt == "LEF2":
        build_v2(path, split=split, tree=entry_tree(), codec=2)
    else:
        build_l01(path, entry_tree(), fmt.encode(), split)
    res = ewf_image.examine(path)
    assert res["status"] == "verified"
    folder, file = res["entries"]
    assert folder["type"] == "directory"
    assert (file["path"], file["size"], file["timestamps"]) == ("folder/item.txt", 15, {"created": "1234567890"})
    assert file["comparison"] == {"md5": "match", "sha1": "match"}
    output = tmp_path / "chosen.bin"
    assert ewf_image.main([str(path), "--extract", str(file["id"]), "--output", str(output)]) == 0
    assert output.read_bytes() == FILE
    output.write_bytes(b"existing")
    assert ewf_image.main([str(path), "--extract", str(file["id"]), "--output", str(output)]) == 3
    assert output.read_bytes() == b"existing"


@pytest.mark.parametrize("fmt", ["L01", "Lx01"])
def test_entry_mismatch_and_metadata_only(tmp_path, fmt):
    path = tmp_path / ("logical." + fmt)
    tree = entry_tree(corrupt=True)
    if fmt == "L01":
        build_l01(path, tree)
    else:
        build_v2(path, tree=tree)
    assert ewf_image.examine(path, verify=False)["entries"][1]["status"] == "not_verified"
    res = ewf_image.examine(path)
    assert res["status"] == "mismatch"
    assert res["entries"][1]["comparison"] == {"md5": "mismatch", "sha1": "match"}
    out = tmp_path / "extract"
    assert ewf_image.main([str(path), "--extract", "1", "--output", str(out)]) == 1
    assert not out.exists()


@pytest.mark.parametrize("damage", ["missing", "short", "ltree_md5", "extent", "typed", "child_count"])
def test_bad_logical_structures(tmp_path, damage):
    path = tmp_path / "logical.L01"
    extent = {"extent": "1 3ff f", "typed": "1 S 1 f"}.get(damage, "1 1fc f")
    build_l01(path, entry_tree(extent=extent, short=damage == "child_count"), split=True)
    second = path.with_suffix(".L02")
    if damage == "missing":
        second.unlink()
    elif damage == "short":
        second.write_bytes(second.read_bytes()[:-64])
    elif damage == "ltree_md5":
        data = bytearray(second.read_bytes())
        data[data.index(b"ltree") + 76 + 48] ^= 1
        second.write_bytes(data)
    assert ewf_image.examine(path, verify=False)["status"] == "unsupported"


def test_report_routes_each_first_segment_and_lists_entries(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    build_v2(root / "disk.Ex01", split=True)
    build_l01(root / "files.L01", entry_tree())
    build_v2(root / "other.Lx01", tree=entry_tree(corrupt=True))
    out = tmp_path / "report"
    assert report_draft.main([str(root), "-o", str(out)]) == 1
    report = (out / "보고서초안.md").read_text(encoding="utf-8")
    assert "folder/item.txt" in report and "EWF2-Ex01" in report
    evidence = json.loads((out / "무결성검증.json").read_text(encoding="utf-8"))
    assert {img["file"]: img["status"] for img in evidence["images"]} == {
        "disk.Ex01": "verified", "files.L01": "verified", "other.Lx01": "mismatch"}


@pytest.mark.skipif(os.name != "posix", reason="POSIX wrapper")
@pytest.mark.parametrize("command", ["ewf", "e01"])
def test_cli_aliases(tmp_path, command):
    path = tmp_path / "disk.Ex01"
    build_v2(path)
    res = subprocess.run([str(ROOT / "bin/lazyforensic"), command, str(path), "--json"],
                         capture_output=True, check=True, env={**os.environ, "LF_PYTHON": sys.executable})
    assert json.loads(res.stdout)["status"] == "verified"


def test_report_detects_a_continuation_without_the_first_segment(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    first = root / "files.Lx01"
    build_v2(first, split=True, tree=entry_tree())
    first.unlink()
    out = tmp_path / "result"
    assert report_draft.main([str(root), "-o", str(out)]) == 1
    evidence = json.loads((out / "무결성검증.json").read_text(encoding="utf-8"))
    image, = evidence["images"]
    assert (image["file"], image["status"]) == ("files.Lx02", "unsupported")


def test_physical_reference_keeps_ambiguous_ewf_formats_unverified():
    md5 = hashlib.md5(MEDIA).hexdigest()
    ref = {"name": "disk.raw", "algorithm": "md5", "hash": md5}
    images = [{"file": "disk.Ex01", "computed_hashes": {"md5": md5}}]
    result, = report_draft.compare_references([ref], [], images)
    assert (result["result"], result["file"]) == ("match", "disk.Ex01")
    result, = report_draft.compare_references([ref], [], images + [{"file": "disk.E01"}])
    assert (result["result"], result["file"]) == ("ambiguous", None)


@pytest.mark.parametrize("damage", ["hash", "table", "stored", "compressed", "tree"])
def test_corrupt_v2_payloads(tmp_path, damage):
    logical = damage == "tree"
    path = tmp_path / ("files.Lx01" if logical else "disk.Ex01")
    build_v2(path, tree=entry_tree() if logical else None,
             chunk_mode="stored" if damage == "stored" else "compressed")
    section = {"hash": 8, "table": 4, "stored": 3, "compressed": 3, "tree": 0x20}[damage]
    data = bytearray(path.read_bytes())
    offset = len(data) - 64
    while struct.unpack_from("<I", data, offset)[0] != section:
        offset = struct.unpack_from("<Q", data, offset + 8)[0]
    previous = struct.unpack_from("<Q", data, offset + 8)[0]
    data[previous + 64] ^= 1
    path.write_bytes(data)
    assert ewf_image.examine(path)["status"] == ("read_error" if section == 3 else "unsupported")


def test_older_logical_entry_columns_and_unhashed_empty_file(tmp_path):
    md5 = hashlib.md5(FILE).hexdigest()
    tree = ("2\nentry\n2\t1\np\tn\tha\tls\tbe\twr\n"
            "0\t2\n1\t\t\t\t0\t\n"
            f"0\t0\n0\t한글.txt\t{md5}\t15\t1 1fc f\t1234567890\n"
            "0\t0\n\tempty\t00000000000000000000000000000000\t0\t1 ff 1\t\n\n"
            "rec\ntb\n1024\n\n").encode("utf-16le")
    path = tmp_path / "older.L01"
    build_l01(path, tree)
    result = ewf_image.examine(path)
    assert result["status"] == "partial"
    file, empty = result["entries"]
    assert (file["name"], file["timestamps"], file["comparison"]) == (
        "한글.txt", {"written": "1234567890"}, {"md5": "match"})
    assert (empty["stored_hashes"], empty["status"]) == ({}, "no_stored_hash")
    output = tmp_path / "empty.bin"
    assert ewf_image.main([str(path), "--extract", "1", "--output", str(output)]) == 4
    assert output.read_bytes() == b""
