"""Small synthetic E01 segments with an independently known media stream."""
import hashlib
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ewf_image


def section(kind, payload, offset):
    terminal = kind in ("next", "done")
    size = 0 if terminal else 76 + len(payload)
    desc = struct.pack("<16sQQ40x", kind.encode(), offset + size, size)
    return desc + struct.pack("<I", zlib.adler32(desc)) + payload


def segment(path, number, sections):
    data = bytearray(ewf_image.EVF_SIGNATURE + struct.pack("<BHH", 1, number, 0))
    for kind, payload in sections:
        data += section(kind, payload, len(data))
    path.write_bytes(data)


def mixed_image(path, *, table_cut=False, offset_delta=0, terminal="done", date=None):
    plain = b"a" * 512
    compressed = zlib.compress(plain)
    raw = b"b" * 512
    media = plain + raw
    volume = struct.pack("<B3xIIIQ", 1, 2, 1, 512, 2)
    sections = [("volume", volume)]
    if date is not None:
        header = f"main\nm\n{date}\n"
        sections.append(("header2", zlib.compress(header.encode("utf-16"))))
    base = 13 + sum(76 + len(payload) for _, payload in sections) + 76
    table = struct.pack("<I4xQ4x", 2, base + offset_delta)
    table += struct.pack("<I", zlib.adler32(table))
    offsets = struct.pack("<II", 1 << 31, len(compressed))
    sections.extend([("sectors", compressed + raw + struct.pack("<I", zlib.adler32(raw))),
                     ("table", table if table_cut else table + offsets + struct.pack("<I", zlib.adler32(offsets))),
                     ("hash", hashlib.md5(media).digest() + bytes(20)), (terminal, b"")])
    segment(path, 1, sections)
    return media


def test_mixed_chunk_boundaries(tmp_path):
    path = tmp_path / "disk.E01"
    media = mixed_image(path)
    result = ewf_image.examine(path)
    assert result["status"] == "verified"
    assert result["computed_hashes"]["sha256"] == hashlib.sha256(media).hexdigest()


@pytest.mark.parametrize("malformation", ["table_cut", "descriptor_offset"])
def test_table_is_bounded_by_its_payload_and_sector_data(tmp_path, malformation):
    path = tmp_path / "disk.E01"
    mixed_image(path, table_cut=malformation == "table_cut",
                offset_delta=-1 if malformation == "descriptor_offset" else 0)
    result = ewf_image.examine(path, verify=False)
    assert result["status"] == "unsupported"
    assert ("table" if malformation == "table_cut" else "sectors") in result["error"]


@pytest.mark.parametrize(("first_end", "second_number", "second_end"),
                         [("done", 2, "next"), ("next", 3, "done")])
def test_segments_must_form_one_ordered_chain(tmp_path, first_end, second_number, second_end):
    first = tmp_path / "disk.E01"
    mixed_image(first, terminal=first_end)
    segment(first.with_suffix(".E02"), second_number, [(second_end, b"")])
    assert ewf_image.examine(first)["status"] == "unsupported"


@pytest.mark.parametrize(("raw", "expected"), [
    ("999999999999999999999", "999999999999999999999"),
    ("-86400", "1969-12-31 00:00:00 UTC"),
])
def test_epoch_metadata_preserves_out_of_range_values(tmp_path, raw, expected):
    first = tmp_path / "disk.E01"
    mixed_image(first, date=raw)
    result = ewf_image.examine(first)
    assert result["status"] == "verified"
    assert result["acquisition"]["acquisition_date"] == expected


def test_short_hash_section_cannot_borrow_bytes_from_next_descriptor(tmp_path):
    path = tmp_path / "disk.E01"
    segment(path, 1, [("volume", struct.pack("<B3xIIIQ", 1, 0, 1, 512, 0)),
                      ("hash", b"short"), ("done", b"")])
    assert ewf_image.examine(path)["status"] == "unsupported"


def test_conflicting_stored_md5_is_a_mismatch(tmp_path):
    path = tmp_path / "disk.E01"
    media = mixed_image(path)
    data = path.read_bytes()[:-76]
    digest = bytes(16) + hashlib.sha1(media).digest()
    corrupt_md5 = bytes.fromhex("ab" * 16)
    for kind, payload in [("digest", corrupt_md5 + digest[16:] + bytes(44)),
                          ("hash", hashlib.md5(media).digest() + bytes(20)), ("done", b"")]:
        data += section(kind, payload, len(data))
    path.write_bytes(data)
    result = ewf_image.examine(path)
    assert result["status"] == "mismatch"
    assert result["comparison"]["md5"] == "mismatch"


def test_incomplete_segment_set_marks_report_run_incomplete(tmp_path):
    import report_draft

    root = tmp_path / "input"
    root.mkdir()
    mixed_image(root / "disk.E01", terminal="next")
    out = tmp_path / "output"
    assert report_draft.main([str(root), "-o", str(out)]) == 1
    assert (out / "보고서초안.docx").is_file()
