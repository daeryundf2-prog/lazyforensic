# -*- coding: utf-8 -*-
"""Data regressions from the D17 review (synthetic inputs only).

F1 WAL salt break followed by current-generation frames, F2 full-path reference matching
before suffix candidates, F3 Shift-JIS and BOM-less UTF-16 keyword false negatives,
F5 nanosecond order in the standalone HTML timeline and Plaso merge, F8 quoted SQLite
table names and uncountable tables, F10 case-alias WAL approved by the walker,
F11 empty EXIF offset tag versus absent tag, F14 timeline badges for capture and
unclassified events.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills/forensic-timeline/scripts"))

import generate_timeline  # noqa: E402
import hash_reference  # noqa: E402
import keyword_report  # noqa: E402
import merge_timeline  # noqa: E402
import exif_audit  # noqa: E402
import report_draft  # noqa: E402
import sqlite_survey  # noqa: E402

BASE_NS = 1704067200 * 10**9  # 2024-01-01T00:00:00Z


def run_report(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = report_draft.main([str(a) for a in args])
    return code, buf.getvalue()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def badges(page: str) -> list[tuple[str, str, str]]:
    """(data-category, badge text, description) per timeline card."""
    return re.findall(r'data-category="([^"]+)">.*?<span class="item-badge [^"]+">([^<]+)</span>'
                      r'.*?<div class="item-desc">(.*?)</div>', page, re.S)


# ------------------------------------------------------------------ F1 WAL salt break

def _writer(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE sample(n)")
    conn.execute("INSERT INTO sample VALUES (0)")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    return conn


def _commit(conn: sqlite3.Connection, *values: int) -> None:
    for n in values:
        conn.execute("INSERT INTO sample VALUES (?)", (n,))
        conn.commit()


def _snapshot(src: Path, folder: Path) -> Path:
    folder.mkdir()
    for suffix in ("", "-wal"):
        shutil.copyfile(f"{src}{suffix}", folder / f"sample.db{suffix}")
    return folder / "sample.db"


def test_wal_salt_break_before_current_generation_frames_is_not_final(tmp_path):
    conn = _writer(tmp_path / "writer.db")
    _commit(conn, 1, 2, 3)  # three independent transactions, one frame each
    db = _snapshot(tmp_path / "writer.db", tmp_path / "damaged")
    conn.close()
    wal = db.with_name("sample.db-wal")
    data = bytearray(wal.read_bytes())
    frame = int.from_bytes(data[8:12], "big") + 24
    data[32 + frame + 8] ^= 1  # salt-1 of the second frame only; the third keeps the current salt
    wal.write_bytes(bytes(data))
    rec = sqlite_survey.survey_db(db)
    audit = rec["sidecars"][0]["audit"]
    assert (audit["frames_valid"], audit["stale_frames"], audit["complete"]) == (1, 2, False)
    assert audit["generation_undetermined_frames"] == 2
    assert rec["total_rows"] is None and rec["visible_rows"] == 2 and rec["status"] == "partial"
    assert any("현 세대 salt 1개" in t for t in rec["limitations"])


def test_wal_prior_generation_tail_after_restart_stays_complete(tmp_path):
    conn = _writer(tmp_path / "writer.db")
    _commit(conn, 1, 2, 3)
    conn.execute("PRAGMA wal_checkpoint(RESTART)").fetchall()
    _commit(conn, 4)  # rewrites frame 1 with a new salt; frames 2-3 are the previous generation
    db = _snapshot(tmp_path / "writer.db", tmp_path / "reuse")
    conn.close()
    rec = sqlite_survey.survey_db(db)
    audit = rec["sidecars"][0]["audit"]
    assert (audit["frames_valid"], audit["stale_frames"], audit["complete"]) == (1, 2, True)
    assert rec["total_rows"] == 5 and "limitations" not in rec


# ------------------------------------------------------------------ F2 reference matching

SHA = hashlib.sha256(b"sample").hexdigest()


def _match(ref: str, paths: list[str]) -> dict:
    result, = hash_reference.match_reference([{"name": ref, "hash": SHA, "algorithm": "sha256"}],
                                             [{"path": p, "sha256": SHA} for p in paths])
    return result


@pytest.mark.parametrize("ref,paths,expected", [
    ("자료/item.bin", [nfd("자료/item.bin"), "archive/자료/item.bin"],
     ("match", "path", nfd("자료/item.bin"), "NFC")),
    ("set/ITEM.bin", ["Set/item.bin", "archive/set/item.bin"], ("match", "path_casefold", "Set/item.bin", None)),
    (nfd("자료/ITEM.bin"), ["자료/item.bin", "archive/자료/item.bin"],
     ("match", "path_casefold", "자료/item.bin", "NFC")),
    ("자료/item.bin", ["자료/item.bin", "archive/자료/item.bin"], ("match", "path", "자료/item.bin", None)),
    ("item.bin", ["a/item.bin"], ("match", "path_suffix", "a/item.bin", None)),
])
def test_reference_full_path_tiers_win_over_suffix(ref, paths, expected):
    r = _match(ref, paths)
    assert (r["result"], r["matched_by"], r["file"], r.get("normalization")) == expected
    assert r["name"] == ref and r["path_differs"] is False


def test_reference_two_equivalent_full_paths_are_held():
    r = _match("set/item.bin", ["Set/item.bin", "SET/item.bin", "archive/set/item.bin"])
    assert (r["result"], r["matched_by"]) == ("ambiguous", "path_casefold")
    assert r["candidates"] == ["SET/item.bin", "Set/item.bin"]


def test_report_attributes_reference_to_the_normalized_full_path(tmp_path):
    ev = tmp_path / "tree"
    for rel in (nfd("자료/item.bin"), "archive/자료/item.bin"):
        p = ev / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"sample")
    listing = tmp_path / "refs.txt"
    listing.write_text(f"{SHA}  자료/item.bin\n", encoding="utf-8")
    code, log = run_report(ev, "--hashes", listing, "-o", tmp_path / "out")
    entry, = read_json(tmp_path / "out" / "무결성검증.json")["reference"]["entries"]
    assert entry["result"] == "match", log
    assert entry["matched_by"] in ("path", "path_casefold")
    assert unicodedata.normalize("NFC", entry["file"]) == "자료/item.bin"
    assert entry["name"] == "자료/item.bin" and entry["path_differs"] is False


# ------------------------------------------------------------------ F3 keyword encodings

def _search(tmp_path: Path, data: bytes, keywords: list[str], encoding: str | None = None) -> dict:
    item = tmp_path / "sample.txt"
    item.write_bytes(data)
    return keyword_report.search_files([item], keywords, 0, encoding)


def _candidates(found: dict) -> list[tuple[int, str, str]]:
    return [(h["line"], h["keyword"], h["encoding"]) for u in found["uncertain"] for h in u["candidate_hits"]]


def _hits(found: dict) -> list[tuple[int, str]]:
    return [(h["line"], h["keyword"]) for r in found["results"] for h in r["hits"]]


def test_undeclared_shift_jis_is_not_a_complete_zero_hit_search(tmp_path):
    found = _search(tmp_path, "東京".encode("shift_jis"), ["東京"])
    assert found["status"] == "partial" and _hits(found) == []
    assert (1, "東京", "shift_jis") in _candidates(found)
    explicit = _search(tmp_path, "東京".encode("shift_jis"), ["東京"], "shift_jis")
    assert explicit["status"] == "complete" and _hits(explicit) == [(1, "東京")]
    assert explicit["results"][0]["encoding_basis"] == "explicit"


def test_shift_jis_line_in_korean_file_is_marked_uncertain(tmp_path):
    found = _search(tmp_path, "東京".encode("shift_jis") + b"\n" + "표식".encode("euc_kr"), ["東京", "표식"])
    assert found["status"] == "partial" and _hits(found) == [(2, "표식")]
    assert _candidates(found) == [(1, "東京", "shift_jis")]
    assert found["uncertain"][0]["lines"] == [[1, 1]]


@pytest.mark.parametrize("word", ["한글", "자료", "기억", "문서"])
@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_bomless_utf16_without_ascii_is_uncertain_and_searched_both_ways(tmp_path, word, codec):
    found = _search(tmp_path, word.encode(codec), [word])
    assert found["status"] == "partial" and _hits(found) == []
    assert (1, word, codec) in _candidates(found)


@pytest.mark.parametrize("data,keywords,expected,encoding", [
    ("표식".encode("euc_kr"), ["표식"], [(1, "표식")], "cp949"),
    ("x표식".encode("utf-16-le"), ["표식"], [(1, "표식")], "utf-16-le"),
    ("x표식".encode("utf-16-be"), ["표식"], [(1, "표식")], "utf-16-be"),
    (b"x" * (1024 * 1024) + "표식".encode(), ["표식"], [(1, "표식")], "utf-8"),
    (b"x" * (1024 * 1024) + "기록".encode() + b"\n" + "표식".encode("cp949"), ["기록", "표식"],
     [(1, "기록"), (2, "표식")], "mixed"),
], ids=["euc-kr", "utf16le-ascii", "utf16be-ascii", "long-utf8", "long-mixed"])
def test_decidable_encodings_stay_complete(tmp_path, data, keywords, expected, encoding):
    found = _search(tmp_path, data, keywords)
    assert found["status"] == "complete" and found["uncertain"] == []
    assert _hits(found) == expected and found["encodings"] == {encoding: 1}


def test_keyword_cli_reports_uncertain_files_and_explicit_codec(tmp_path, capsys):
    folder = tmp_path / "evidence"
    folder.mkdir()
    (folder / "jp.txt").write_bytes("東京".encode("shift_jis"))
    keyword_report.main([str(folder), "東京", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "partial" and report["total_hits"] == 0
    assert report["files_encoding_uncertain"] == 1 and report["candidate_hits"] == 1
    assert "인코딩 불확실 1개" in keyword_report.scope_note(report)
    keyword_report.main([str(folder), "東京", "--json", "--encoding", "shift_jis"])
    report = json.loads(capsys.readouterr().out)
    assert (report["status"], report["total_hits"], report["encoding_requested"]) == ("complete", 1, "shift_jis")
    assert keyword_report.main([str(folder), "東京", "--encoding", "no-such-codec"]) == 2


# ------------------------------------------------------------------ F5 nanosecond order

def _iso(ns: int, zone: str = "Z") -> str:
    return f"2024-01-01T00:00:00.{ns:09d}{zone}"


def test_standalone_html_keeps_nanosecond_order_and_ties(tmp_path):
    events = [{"timestamp": _iso(900), "description": "900", "category": "system"},
              {"timestamp": "2024-01-01T09:00:00.000000100+09:00", "description": "100", "category": "system"},
              {"timestamp": _iso(500), "description": "tie-a", "category": "system"},
              {"timestamp": "2024-01-01T09:00:00.000000500+09:00", "description": "tie-b", "category": "system"}]
    src, dest = tmp_path / "events.json", tmp_path / "timeline.html"
    src.write_text(json.dumps(events), encoding="utf-8")
    assert generate_timeline.main(["--input", str(src), "--output", str(dest)]) == 0
    page = dest.read_text(encoding="utf-8")
    assert [c[2] for c in badges(page)] == ["100", "tie-a", "tie-b", "900"]
    assert "시각 오름차순" in page and "동률 표시는 기록 정밀도 안에서 선후 미확정" in page
    assert page.count('<span class="badge-tie"') == 2


def test_standalone_html_floating_and_mixed_zones():
    floating = [{"timestamp": "2024-01-01T00:00:00.000000900", "description": "900"},
                {"timestamp": "2024-01-01T00:00:00.000000100", "description": "100"}]
    ordered, label = generate_timeline.order_events(floating)
    assert [e["description"] for e in ordered] == ["100", "900"] and "시간대 미표기" in label
    mixed = [{"timestamp": "2024-01-01T00:00:00", "description": "floating"},
             {"timestamp": "2024-01-01T02:00:00Z", "description": "UTC"}]
    ordered, label = generate_timeline.order_events(mixed)
    assert [e["description"] for e in ordered] == ["floating", "UTC"] and label == "입력 순서 (타임존 표기 혼합)"


def test_plaso_iso_keeps_source_fraction_and_order(tmp_path):
    src = tmp_path / "plaso.jsonl"
    src.write_text("\n".join(json.dumps({"datetime": _iso(ns), "message": str(ns)}) for ns in (900, 100)),
                   encoding="utf-8")
    events = merge_timeline.merge([], [], [], [], {}, plasos=[str(src)])["events"]
    assert [e["timestamp"] for e in events] == [_iso(100), _iso(900)]
    assert [e["instant_ns"] - BASE_NS for e in events] == [100, 900]
    assert not any(e.get("order_tie") for e in events)
    micro = merge_timeline._plaso_record_event({"timestamp": 1704067200000001, "message": "m"}, "p")
    assert (micro["instant_ns"], micro["precision_ns"]) == (BASE_NS + 1000, 1000)


# ------------------------------------------------------------------ F8 SQLite table names

def test_quoted_table_name_is_counted(tmp_path, capsys):
    db = tmp_path / "ordinary.db"
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute('CREATE TABLE "sample""table"(n)')
        conn.execute('CREATE TABLE "empty"(n)')
        conn.executemany('INSERT INTO "sample""table" VALUES (?)', [(1,), (2,)])
        conn.commit()
    rec = sqlite_survey.survey_db(db)
    assert {t["name"]: t["row_count"] for t in rec["tables"]} == {'sample"table': 2, "empty": 0}
    assert rec["total_rows"] == 2 and "limitations" not in rec
    assert sqlite_survey.main([str(db)]) == 0
    assert "총 2행" in capsys.readouterr().out


def test_uncountable_table_leaves_total_unconfirmed(tmp_path, capsys):
    db = tmp_path / "ghost.db"
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE t(n)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("INSERT INTO sqlite_master(type, name, tbl_name, rootpage, sql) VALUES "
                     "('table', 'ghost', 'ghost', 0, 'CREATE VIRTUAL TABLE ghost USING nosuchmodule()')")
        conn.commit()
    rec = sqlite_survey.survey_db(db)
    assert rec["total_rows"] is None and rec["visible_rows"] == 1 and rec["status"] == "partial"
    assert any("집계 실패(ghost)" in t and "최종 합계 미확인" in t for t in rec["limitations"])
    sqlite_survey.main([str(db)])
    out = capsys.readouterr().out
    assert "보이는 행 1행(최종 행 수 미확인)" in out and "총 1행" not in out


# ------------------------------------------------------------------ F10 case-alias WAL

def _wal_folder(tmp_path: Path) -> Path:
    conn = _writer(tmp_path / "writer.db")
    _commit(conn, 1)
    folder = tmp_path / "evidence"
    folder.mkdir()
    shutil.copyfile(tmp_path / "writer.db", folder / "sample.db")
    shutil.copyfile(f"{tmp_path / 'writer.db'}-wal", folder / "sample.db-WAL")
    conn.close()
    return folder


def test_case_alias_of_approved_wal_is_read_by_both_entry_points(tmp_path):
    folder = _wal_folder(tmp_path)
    db, upper, lower = folder / "sample.db", folder / "sample.db-WAL", folder / "sample.db-wal"
    if not lower.exists():  # case-sensitive volume: a hard link gives SQLite's name to the same file
        os.link(upper, lower)
    assert os.path.samefile(upper, lower)
    digest = hashlib.sha256(upper.read_bytes()).hexdigest()
    directory, = sqlite_survey.scan(folder, files=[db, upper])
    direct = sqlite_survey.survey_db(db)
    for rec in (directory, direct):
        assert rec["total_rows"] == 2 and "out_of_scope_sidecars" not in rec
        assert rec["sidecars"][0]["sha256"] == digest and rec["sidecars"][0]["in_inventory"] is True
    assert directory["sidecars"][0]["file"] == "sample.db-WAL"


def test_case_variant_that_is_another_file_stays_out_of_scope(tmp_path):
    folder = _wal_folder(tmp_path)
    db, upper, lower = folder / "sample.db", folder / "sample.db-WAL", folder / "sample.db-wal"
    if lower.exists():
        pytest.skip("case-insensitive volume: two names differing only in case cannot coexist")
    shutil.copyfile(upper, lower)  # same bytes, different file
    rec, = sqlite_survey.scan(folder, files=[db, upper])
    assert rec["total_rows"] is None and rec["visible_rows"] == 1
    assert [o["file"] for o in rec["out_of_scope_sidecars"]] == ["sample.db-wal"]


# ------------------------------------------------------------------ F11 EXIF offset tag states

@pytest.mark.parametrize("tags,expected", [
    ({}, ("absent", False, None)),
    ({"OffsetTimeOriginal": ""}, ("empty", True, "")),
    ({"OffsetTimeOriginal": "   "}, ("empty", True, "   ")),
    ({"OffsetTimeOriginal": "+05:45"}, ("valid", True, "+05:45")),
    ({"OffsetTimeOriginal": "+09:75"}, ("malformed", True, "+09:75")),
])
def test_exif_offset_tag_presence_raw_and_status_are_independent(tags, expected):
    shot = exif_audit.capture_time({"DateTimeOriginal": "2024:01:01 00:00:00", **tags})
    assert (shot["offset_status"], shot["offset_present"], shot["offset_raw"]) == expected
    fields = exif_audit.event_fields(shot)
    assert fields["recorded"]["OffsetTimeOriginal"] == expected[2]
    assert ("timezone_assumed" in fields) is (expected[0] != "valid")


def test_report_and_form_separate_empty_offset_from_absent(tmp_path):
    image_mod = pytest.importorskip("PIL.Image")
    folder = tmp_path / "evidence"
    folder.mkdir()
    for name, offset in (("empty.jpg", "   "), ("absent.jpg", None)):
        exif = image_mod.Exif()
        ifd = {0x9003: "2024:01:01 12:30:00"}
        if offset is not None:
            ifd[0x9011] = offset
        exif[0x8769] = ifd
        image_mod.new("RGB", (2, 2)).save(folder / name, exif=exif)
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--template", ROOT / "templates/report_form.hwpx")
    assert code == 0, log
    events = {e["file"]: e for e in read_json(out / "events.json") if e["source"] == "EXIF"}
    assert {k: (e["offset_status"], e["offset_present"], e["recorded"]["OffsetTimeOriginal"])
            for k, e in events.items()} == {"empty.jpg": ("empty", True, "   "),
                                            "absent.jpg": ("absent", False, None)}
    md = (out / "보고서초안.md").read_text(encoding="utf-8")
    assert "1건은 UTC 오프셋 태그(OffsetTimeOriginal)는 있으나 값이 비어 있다" in md
    assert "1건은 UTC 오프셋(OffsetTimeOriginal)이 기록되지 않아" in md
    with ZipFile(out / "양식초안.hwpx") as package:
        root = ET.fromstring(package.read("Contents/section0.xml"))
    rows = [["".join(t.text or "" for t in c.iterfind(".//{*}t")) for c in row.findall("{*}tc")]
            for row in root.iterfind(".//{*}tr")]
    exif_rows = {r[2]: r[3] for r in rows if len(r) == 4 and r[1] == "EXIF"}
    assert "값이 비어 있음" in exif_rows["empty.jpg"] and "값이 비어 있음" not in exif_rows["absent.jpg"]


# ------------------------------------------------------------------ F14 timeline badges

def test_report_timeline_badges_capture_records_as_media(tmp_path):
    image_mod = pytest.importorskip("PIL.Image")
    folder = tmp_path / "evidence"
    folder.mkdir()
    (folder / "note.bin").write_bytes(b"x")
    exif = image_mod.Exif()
    exif[0x8769] = {0x9003: "2024:01:01 00:00:00", 0x9011: "+00:00"}
    image_mod.new("RGB", (2, 2)).save(folder / "image.jpg", exif=exif)
    code, log = run_report(folder, "-o", tmp_path / "out")
    assert code == 0, log
    cards = badges((tmp_path / "out" / "타임라인.html").read_text(encoding="utf-8"))
    capture = [c for c in cards if c[2].startswith("촬영 시각")]
    files = [c for c in cards if c[2].startswith("파일 수정 시각")]
    assert capture and all(c[:2] == ("media", "촬영기록") for c in capture)
    assert files and all(c[:2] == ("file", "파일행위") for c in files)


def test_unknown_or_missing_category_is_neutral():
    page = generate_timeline.generate_html([
        {"timestamp": "2024-01-01T00:00:00Z", "description": "none"},
        {"timestamp": "2024-01-01T00:00:01Z", "description": "odd", "category": "nonsense"},
        {"timestamp": "2024-01-01T00:00:02Z", "description": "file", "category": "file"}])
    assert badges(page) == [("unclassified", "미분류", "none"), ("unclassified", "미분류", "odd"),
                            ("file", "파일행위", "file")]
    assert "setCategory('media', this)" in page and "setCategory('unclassified', this)" in page


def test_every_generated_category_is_known_to_the_renderer():
    # 타임라인 이벤트를 만드는 두 생성기의 범주 문자열은 모두 렌더러의 범주 목록에 있어야 한다.
    source = (ROOT / "scripts/report_draft.py").read_text(encoding="utf-8") + \
        (ROOT / "scripts/merge_timeline.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'"category": "(\w+)"', source))
    assert {"file", "media", "system", "chat"} <= emitted
    assert emitted <= set(generate_timeline.CATEGORIES)
