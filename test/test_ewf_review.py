"""Independent boundary and interoperability checks using small synthetic evidence."""
import bz2
import hashlib
import json
import struct
import zlib

import pytest

from test_ewf_formats import (FILE, MEDIA, adler, build_l01, build_v2, entry_tree,
                              ewf_image, metadata, report_draft, single_bzip_block, v2_section)

ewf2 = ewf_image.ewf2


def table_record(offset, size, flags):
    header = struct.pack("<QI4x", 0, 1)
    entry = struct.pack("<QII", offset, size, flags)
    return header + adler(header) + bytes(12) + entry + adler(entry) + bytes(12)


@pytest.mark.parametrize(("flags", "mode"), [(4, "raw"), (6, "adler")])
def test_pattern_bit_without_compression_keeps_the_stored_chunk(flags, mode):
    chunks = []
    ewf2.sector_table(table_record(32, 16, flags), chunks, 0, (32, 48), 1)
    assert chunks == [(0, 32, 16, mode)]


def small_ex01(path, *, sectors="1", hashes=(), terminal=15):
    out = bytearray(struct.pack("<8sBBHI16s", ewf2.EVF2, 2, 1, 1, 1, bytes(16)))
    previous = 0
    for kind, fields in [(1, dict(ts=sectors, bp="512", dt="f", lb="디스크")),
                         (2, dict(sb="2", tb="1", nt="first\x01next\x03field", tt="86400"))]:
        previous = v2_section(out, previous, kind, zlib.compress(metadata(fields)), flags=1)
    offset = len(out)
    payload = zlib.compress(MEDIA[:512])
    previous = v2_section(out, previous, 3, payload)
    previous = v2_section(out, previous, 4, table_record(offset, len(payload), 1), pad=24)
    for kind, digest in hashes:
        previous = v2_section(out, previous, kind, digest + adler(digest))
    v2_section(out, previous, terminal)
    path.write_bytes(out)


def test_partial_final_chunk_and_repeated_hash_sections(tmp_path):
    path = tmp_path / "sample.Ex01"
    digest = hashlib.md5(MEDIA[:512]).digest()
    sha1 = hashlib.sha1(MEDIA[:512]).digest()
    for first_md5 in (digest, bytes(16)):
        small_ex01(path, hashes=[(8, first_md5), (8, digest), (9, sha1)])
        result = ewf_image.examine(path)
        assert result["status"] == ("verified" if first_md5 == digest else "mismatch")
        assert result["computed_hashes"]["bytes"] == 512
        assert result["comparison"]["sha1"] == "match"
        assert result["acquisition"]["notes"] == "first\nnext\tfield"
        assert result["acquisition"]["device_label"] == "디스크"
        assert result["acquisition"]["acquisition_date"] == "1970-01-02 00:00:00 UTC"


def test_geometry_respects_serialized_integer_width(tmp_path):
    path = tmp_path / "sample.Ex01"
    small_ex01(path, sectors=str(2**64))
    result = ewf_image.examine(path, verify=False)
    assert result["status"] == "unsupported"
    assert "geometry" in result["error"]


def test_lx01_tree_requires_its_section_integrity_record(tmp_path):
    path = tmp_path / "tree.Lx01"
    build_v2(path, tree=entry_tree())
    data = bytearray(path.read_bytes())
    pos = struct.unpack_from("<Q", data, len(data) - 64 + 8)[0]
    assert struct.unpack_from("<I", data, pos)[0] == 0x20
    struct.pack_into("<I", data, pos + 4, 0)
    data[pos + 32:pos + 48] = bytes(16)
    data[pos + 60:pos + 64] = adler(data[pos:pos + 60])
    path.write_bytes(data)
    assert ewf_image.examine(path, verify=False)["status"] == "unsupported"


def test_stream_hash_must_be_in_the_final_segment(tmp_path):
    path = tmp_path / "sample.Ex01"
    small_ex01(path, hashes=[(8, hashlib.md5(MEDIA[:512]).digest())], terminal=13)
    following = bytearray(struct.pack("<8sBBHI16s", ewf2.EVF2, 2, 1, 1, 2, bytes(16)))
    v2_section(following, 0, 15)
    path.with_suffix(".Ex02").write_bytes(following)
    assert ewf_image.examine(path)["status"] == "unsupported"


def l01_section(kind, payload, offset):
    size = 0 if kind in ("done", "next") else 76 + len(payload)
    descriptor = struct.pack("<16sQQ40x", kind.encode(), offset + size, size)
    return descriptor + adler(descriptor) + payload


def l01_volume(guid):
    body = bytearray(1048)
    struct.pack_into("<B3xIIIQ", body, 0, 14, 0, 1, 512, 2)
    body[64:80] = guid
    return body + adler(body)


def make_stored_l01(path, *, checksum=True, extra_sections=(), guid=bytes(16), tree=None,
                    split=False, data_guid=None):
    tree = entry_tree() if tree is None else tree
    head = hashlib.md5(tree).digest() + struct.pack("<Q24x", len(tree))
    ltree = head[:24] + adler(head) + head[28:] + tree
    groups = [[MEDIA[:512]], [MEDIA[512:]]] if split else [[MEDIA[:512], MEDIA[512:]]]
    for seq, group in enumerate(groups, 1):
        out = bytearray(b"LVF\x09\r\n\xff\0" + struct.pack("<BHH", 1, seq, 0))
        kind = "volume" if seq == 1 else "data"
        out.extend(l01_section(kind, l01_volume(guid if seq == 1 else data_guid), len(out)))
        stored = bytearray()
        offsets = bytearray()
        base = len(out) + 76
        for chunk in group:
            offsets.extend(struct.pack("<I", len(stored)))
            stored.extend(chunk)
            if checksum:
                stored.extend(adler(chunk))
        header = struct.pack("<I4xQ4x", len(group), base)
        table = header + adler(header) + offsets + adler(offsets)
        sections = [("sectors", stored), ("table", table)]
        if seq == len(groups):
            sections.extend([("ltree", ltree), *extra_sections, ("done", b"")])
        else:
            sections.append(("next", b""))
        for kind, payload in sections:
            out.extend(l01_section(kind, payload, len(out)))
        path.with_suffix(f".L{seq:02d}").write_bytes(out)


def test_stored_l01_chunk_requires_its_checksum(tmp_path):
    path = tmp_path / "files.L01"
    make_stored_l01(path, checksum=False)
    assert ewf_image.examine(path)["status"] == "read_error"


@pytest.mark.parametrize("section", ["hash", "digest"])
@pytest.mark.parametrize("damage", ["short", "checksum", "none"])
def test_l01_media_digest_record_integrity(tmp_path, section, damage):
    path = tmp_path / "files.L01"
    digests = hashlib.md5(MEDIA).digest()
    if section == "digest":
        digests += hashlib.sha1(MEDIA).digest()
    body = digests.ljust(32 if section == "hash" else 76, b"\0")
    record = digests if damage == "short" else body + (bytes(4) if damage == "checksum" else adler(body))
    make_stored_l01(path, extra_sections=[(section, record)])
    assert ewf_image.examine(path)["status"] == ("verified" if damage == "none" else "unsupported")


@pytest.mark.parametrize("same_set", [False, True])
def test_l01_continuation_set_identifier(tmp_path, same_set):
    path = tmp_path / "files.L01"
    guid = bytes(range(16))
    make_stored_l01(path, split=True, guid=guid, data_guid=guid if same_set else guid[::-1])
    assert ewf_image.examine(path)["status"] == ("verified" if same_set else "unsupported")


@pytest.mark.parametrize("kind", ["volume", "data"])
def test_l01_geometry_record_checksum(tmp_path, kind):
    path = tmp_path / "files.L01"
    make_stored_l01(path, split=True, guid=bytes(range(16)), data_guid=bytes(range(16)))
    changed = path if kind == "volume" else path.with_suffix(".L02")
    data = bytearray(changed.read_bytes())
    data[13 + 76 + 1048] ^= 1
    changed.write_bytes(data)
    assert ewf_image.examine(path, verify=False)["status"] == "unsupported"


def test_l01_terminator_must_be_last(tmp_path):
    path = tmp_path / "files.L01"
    build_l01(path, entry_tree())
    with path.open("ab") as stream:
        stream.write(l01_section("done", b"", stream.tell()))
    assert ewf_image.examine(path)["status"] == "unsupported"


def logical_rows(rows, rec="tb\n1024", parent_count=0):
    fields = ("p", "n", "ls", "be", "ha", "du", "opr")
    lines = ["2", "rec", *rec.splitlines(), "", "entry", "1\t1", "\t".join(fields),
             f"0\t{len(rows)}", "1\t\t\t0\t\t\t"]
    for row in rows:
        lines += [f"{parent_count}\t0", "\t".join(row.get(k, "") for k in fields)]
    return ("\n".join(lines) + "\n\n").encode("utf-16le", errors="surrogatepass")


def logical_row(name, **fields):
    return dict(n=name, ls=str(len(FILE)), be="2 1fc 4 200 b", ha=hashlib.md5(FILE).hexdigest(), **fields)


@pytest.mark.parametrize("rec", ["tb\ttb\n1024\t1024", "tb\tcl\n1024"])
def test_ambiguous_logical_record_columns(rec):
    with pytest.raises(ewf2.EwfError):
        ewf_image.ewf_logical.parse_tree(logical_rows([logical_row("file")], rec=rec))


def test_child_entry_can_record_the_number_of_siblings(tmp_path):
    path = tmp_path / "files.L01"
    tree = logical_rows([logical_row("one"), logical_row("two")], parent_count=2)
    build_l01(path, tree)
    result = ewf_image.examine(path)
    assert result["status"] == "verified"
    assert [e["path"] for e in result["entries"]] == ["one", "two"]


@pytest.mark.parametrize("name", ["../sample", "/absolute", "a\x00b", "a\\b", "測試·stream"])
def test_entry_name_does_not_select_the_output_path(tmp_path, name):
    path = tmp_path / "files.L01"
    output = tmp_path / "chosen"
    build_l01(path, logical_rows([logical_row(name)]))
    result = ewf_image.examine(path, extract_id=0, output=output)
    assert (result["name"], result["status"]) == (name, "verified")
    assert output.read_bytes() == FILE
    assert {p.name for p in tmp_path.iterdir()} == {"files.L01", "chosen"}


def test_duplicate_extent_and_data_bearing_directory(tmp_path):
    path = tmp_path / "files.L01"
    duplicate = logical_row("duplicate", du="508", opr=str(0x04000000))
    duplicate["be"] = "1 0 1"
    tree = logical_rows([duplicate, logical_row("folder", p="1")])
    build_l01(path, tree)
    assert ewf_image.examine(path)["status"] == "verified"
    for index, kind in enumerate(("file", "directory")):
        output = tmp_path / f"extracted-{index}"
        result = ewf_image.examine(path, extract_id=index, output=output)
        assert (result["status"], result["type"]) == ("verified", kind)
        assert output.read_bytes() == FILE


def test_unpaired_name_code_unit_survives_listing_report_and_extraction(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    path = root / "files.L01"
    name = "名\ud800.txt"
    build_l01(path, logical_rows([logical_row(name)]))
    out = tmp_path / "report"
    assert report_draft.main([str(root), "-o", str(out)]) == 0
    image, = json.loads((out / "무결성검증.json").read_text(encoding="utf-8"))["images"]
    assert image["status"] == "verified"
    entry, = image["entries"]
    assert entry["name"] == r"名\ud800.txt"
    assert bytes.fromhex(entry["name_utf16le"]) == name.encode("utf-16le", "surrogatepass")
    extracted = tmp_path / "chosen"
    assert ewf_image.examine(path, extract_id=0, output=extracted)["status"] == "verified"
    assert extracted.read_bytes() == FILE


def test_report_finds_alphabetic_orphans_without_misclassifying_other_files(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    # First alphabetic rollover of EWF1 and second rollover of EWF2.
    (root / "one.LAA").write_bytes(b"LVF\x09\r\n\xff\0" + struct.pack("<BHH", 1, 100, 0))
    (root / "two.EyAA").write_bytes(struct.pack("<8sBBHI16s", ewf2.EVF2, 2, 1, 1, 776, bytes(16)))
    (root / "note.log").write_text("ordinary file")
    out = tmp_path / "report"
    assert report_draft.main([str(root), "-o", str(out)]) == 1
    images = json.loads((out / "무결성검증.json").read_text(encoding="utf-8"))["images"]
    assert {i["file"]: i["status"] for i in images} == {"one.LAA": "unsupported", "two.EyAA": "unsupported"}


@pytest.mark.parametrize("kind", [8, 9])
def test_fixed_hash_record_rejects_excess_padding(tmp_path, kind):
    path = tmp_path / "physical.Ex01"
    small_ex01(path)
    out = bytearray(path.read_bytes()[:-64])
    previous = len(out) - 64
    digest = hashlib.new("md5" if kind == 8 else "sha1", MEDIA[:512]).digest()
    record = digest + adler(digest)
    padding = 48 - len(record)
    previous = v2_section(out, previous, kind, record + bytes(padding), pad=padding)
    v2_section(out, previous, 15)
    path.write_bytes(out)
    assert ewf_image.examine(path)["status"] == "unsupported"


def test_logical_sector_data_must_have_an_index(tmp_path):
    path = tmp_path / "logical.L01"
    make_stored_l01(path, extra_sections=[("sectors", MEDIA[:512] + adler(MEDIA[:512]))])
    assert ewf_image.examine(path, verify=False)["status"] == "unsupported"


def test_legacy_calendar_field_preserves_unrecognized_digits():
    raw = "² 01 01 00 00 00"
    assert ewf_image._format_header_date(raw) == raw


def test_l01_checks_table_entry_limit_before_bulk_read(tmp_path, monkeypatch):
    # Substitute a one-entry budget and exercise both sides with ordinary chunks.
    monkeypatch.setattr(ewf_image, "MAX_L01_TABLE_ENTRIES", 1, raising=False)
    path = tmp_path / "logical.L01"
    make_stored_l01(path)
    assert ewf_image.examine(path, verify=False)["status"] == "unsupported"
    make_stored_l01(path, split=True, guid=bytes(16), data_guid=bytes(16))
    assert ewf_image.examine(path)["status"] == "verified"


@pytest.mark.parametrize("codec", [0, 1, 2])
def test_decode_limit_includes_exact_size(codec):
    payload = (MEDIA, zlib.compress(MEDIA), bz2.compress(MEDIA))[codec]
    assert ewf2.unpack_stream(payload, codec, len(MEDIA)) == MEDIA
    with pytest.raises(ewf2.EwfError):
        ewf2.unpack_stream(payload, codec, len(MEDIA) - 1)


def test_single_bzip_block_checks_crc():
    raw = bytearray(single_bzip_block(MEDIA))
    raw[6] ^= 1
    with pytest.raises(ewf2.EwfError):
        ewf2.unpack_stream(bytes(raw), 2, len(MEDIA))


def test_l01_continuation_geometry_agrees_with_first_volume(tmp_path):
    path = tmp_path / "files.L01"
    make_stored_l01(path, split=True, guid=bytes(16), data_guid=bytes(16))
    second = path.with_suffix(".L02")
    data = bytearray(second.read_bytes())
    start = 13 + 76
    struct.pack_into("<I", data, start + 12, 1024)
    data[start + 1048:start + 1052] = adler(data[start:start + 1048])
    second.write_bytes(data)
    assert ewf_image.examine(path, verify=False)["status"] == "unsupported"
