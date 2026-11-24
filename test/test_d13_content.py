# -*- coding: utf-8 -*-
"""Content regressions from the D13 review (synthetic inputs only).

1 incomplete WAL acquisition, 2 mixed-encoding and binary "text" files, 3 one integer
ordering key across report/timeline/forms, 5 SQLite sidecars outside the walker's
inventory, 8 reference path matching order, 12 malformed EXIF offsets.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import unicodedata
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import case_survey  # noqa: E402
import evidence_manifest  # noqa: E402
import exif_audit  # noqa: E402
import hash_reference  # noqa: E402
import keyword_report  # noqa: E402
import merge_timeline  # noqa: E402
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


def form_rows(path: Path) -> list[list[str]]:
    with ZipFile(path) as package:
        root = ET.fromstring(package.read("Contents/section0.xml"))
    return [["".join(t.text or "" for t in c.iterfind(".//{*}t")) for c in row.findall("{*}tc")]
            for row in root.iterfind(".//{*}tr")]


# ------------------------------------------------------------------ 1 incomplete WAL

def wal_folder(tmp_path: Path, rows_in_wal: list[bytes]) -> Path:
    """Body with one checkpointed row; each item of rows_in_wal is one committed WAL transaction."""
    writer_path = tmp_path / "writer.db"
    writer = sqlite3.connect(writer_path)
    writer.execute("PRAGMA journal_mode=wal")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE sample(v)")
    writer.execute("INSERT INTO sample VALUES (1)")
    writer.commit()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    for value in rows_in_wal:
        writer.execute("INSERT INTO sample VALUES (?)", (value,))
        writer.commit()
    folder = tmp_path / "evidence"
    folder.mkdir()
    for suffix in ("", "-wal"):
        shutil.copyfile(f"{writer_path}{suffix}", folder / f"sample.db{suffix}")
    writer.close()
    return folder


def test_intact_wal_is_a_complete_acquisition(tmp_path):
    folder = wal_folder(tmp_path, [b"2"])
    rec = sqlite_survey.survey_db(folder / "sample.db")
    assert rec["total_rows"] == 2 and "status" not in rec and "limitations" not in rec
    audit = rec["sidecars"][0]["audit"]
    assert audit["complete"] and audit["frames_valid"] == audit["last_commit_frame"] == 1
    assert audit["trailing_bytes"] == 0


def test_truncated_wal_frame_reports_visible_rows_and_unknown_total(tmp_path):
    folder = wal_folder(tmp_path, [b"2"])
    wal = folder / "sample.db-wal"
    wal.write_bytes(wal.read_bytes()[:-1])  # acquisition lost the last byte
    rec = sqlite_survey.survey_db(folder / "sample.db")
    assert rec["total_rows"] is None and rec["visible_rows"] == 1 and rec["status"] == "partial"
    audit = rec["sidecars"][0]["audit"]
    assert not audit["complete"] and audit["frames_valid"] == 0 and audit["trailing_bytes"] == wal.stat().st_size - 32
    assert any("잘린 취득본" in t for t in rec["limitations"])

    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out)
    survey = read_json(out / "선조사.json")
    step = survey["steps"]["sqlite"]["result"]
    assert step["status"] == "partial" and step["rows_unconfirmed"] == 1
    assert "Incomplete stage: sqlite" in survey["processing_receipt"]["warnings"]
    md = (out / "보고서초안.md").read_text(encoding="utf-8")
    row = next(line for line in md.splitlines() if line.startswith("| SQLite 데이터베이스 |"))
    assert "마지막 커밋까지 취득했는지 확인 불가" in row, row
    assert "SQLite sample.db: 보이는 행 1행, 최종 행 수 미확인" in md


def test_wal_checksum_and_uncommitted_tail_are_incomplete(tmp_path):
    folder = wal_folder(tmp_path, [b"2", b"3"])
    wal = folder / "sample.db-wal"
    data = bytearray(wal.read_bytes())
    data[-1] ^= 0xFF  # corrupt the second frame's page bytes
    wal.write_bytes(bytes(data))
    rec = sqlite_survey.survey_db(folder / "sample.db")
    assert rec["visible_rows"] == 2 and rec["total_rows"] is None
    assert any("체크섬 불일치" in t for t in rec["limitations"])

    # A multi-page transaction cut at a frame boundary: frames are whole, but no commit frame.
    multi = tmp_path / "multi"
    multi.mkdir()
    folder = wal_folder(multi, [os.urandom(9000)])
    wal = folder / "sample.db-wal"
    frame = 24 + struct.unpack(">I", wal.read_bytes()[8:12])[0]
    assert (wal.stat().st_size - 32) // frame > 1
    wal.write_bytes(wal.read_bytes()[:32 + frame])
    rec = sqlite_survey.survey_db(folder / "sample.db")
    assert rec["visible_rows"] == 1 and rec["total_rows"] is None
    assert any("커밋 표시 없는 프레임 1개" in t for t in rec["limitations"])


# ------------------------------------------------------------------ 2 mixed encodings and binary records

def test_mixed_utf8_and_cp949_lines_find_both_markers(tmp_path):
    item = tmp_path / "log.txt"
    item.write_bytes("기록".encode("utf-8") + b"\n" + "표식".encode("cp949"))
    res = keyword_report.search_file_result(item, ["기록", "표식"], 0)
    assert res["encoding"] == "mixed" and res["basis"] == "per_line"
    assert [h["keyword"] for h in res["hits"]] == ["기록", "표식"]
    assert [s["encoding"] for s in res["segments"]] == ["utf-8", "cp949"]
    assert res["unsearched_lines"] == []


def test_undecodable_lines_are_reported_as_unsearched(tmp_path):
    item = tmp_path / "log.txt"
    item.write_bytes("기록".encode("utf-8") + b"\n\x80\xff\n" + "표식".encode("utf-8"))
    res = keyword_report.search_file_result(item, ["기록", "표식"], 0)
    assert len(res["hits"]) == 2 and res["unsearched_lines"] == [[2, 2]]


def test_binary_record_with_text_extension_is_not_searched_text(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    (folder / "numbers.txt").write_bytes(struct.pack(">HHH", 258, 45217, 772))  # decodes as CP949
    (folder / "mixed.log").write_bytes("기록".encode("utf-8") + b"\n" + "표식".encode("cp949"))
    (folder / "single.txt").write_bytes("표식".encode("cp949") + b"\nother")  # single-codec control
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        keyword_report.main([str(folder), "기록", "표식", "--json"])
    report = json.loads(out.getvalue())
    assert report["files_searched"] == 2 and report["files_not_text"] == 1
    assert report["not_text"][0]["file"].endswith("numbers.txt")
    assert report["encodings"] == {"mixed": 1, "cp949": 1} and report["files_mixed_encoding"] == 1
    assert report["total_hits"] == 3 and report["status"] == "partial"

    survey = case_survey.survey(folder, ["기록", "표식"], False)
    kw = survey["steps"]["keywords"]["result"]
    assert (kw["files_searched"], kw["files_not_text"], kw["total_hits"]) == (2, 1, 3)
    code, log = run_report(folder, "-o", tmp_path / "out", "--keywords", "기록,표식")
    md = (tmp_path / "out" / "보고서초안.md").read_text(encoding="utf-8")
    row = next(line for line in md.splitlines() if line.startswith("| 키워드 검색 |"))
    assert "부분 완료" in row and "텍스트 파일 2개 검색" in row and "텍스트 아님(바이너리 기록) 1개" in row, row


def test_single_codec_file_keeps_its_result(tmp_path):
    item = tmp_path / "only.txt"
    item.write_bytes("표식".encode("cp949") + b"\nother")
    res = keyword_report.search_file_result(item, ["표식"], 0)
    assert (res["encoding"], res["basis"], res["segments"], res["not_text"]) == ("cp949", "strict", None, None)


# ------------------------------------------------------------------ 3 one integer ordering key

def test_timeline_manifest_and_report_share_nanosecond_order(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    points = {"fat-even": 0, "fat-even-plus-2s": 2_000_000_000, "exfat-plus-10ms": 10_000_000,
              "a-ntfs-later": 900, "z-ntfs-earlier": 100}
    for name, delta in points.items():
        (folder / name).write_bytes(b"x")
        os.utime(folder / name, ns=(BASE_NS + delta, BASE_NS + delta))
    stored = {name: (folder / name).stat().st_mtime_ns for name in points}
    expected = sorted(points, key=lambda n: (stored[n], n))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(evidence_manifest.build_manifest(folder)), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/merge_timeline.py"), "--manifest", str(manifest)],
                          capture_output=True, text=True, encoding="utf-8", check=True)
    merged = json.loads(proc.stdout)
    assert [e["file"] for e in merged] == expected
    assert [e["mtime_ns"] for e in merged] == [stored[n] for n in expected]
    code, log = run_report(folder, "-o", tmp_path / "out")
    assert code == 0, log
    assert [e["file"] for e in read_json(tmp_path / "out" / "events.json")] == expected


def test_exif_subsecond_orders_against_mtime_in_events_and_form(tmp_path):
    image_mod = pytest.importorskip("PIL.Image")
    folder = tmp_path / "evidence"
    folder.mkdir()
    photo = folder / "clock.jpg"
    tags = image_mod.Exif()
    tags[0x8769] = {0x9003: "2024:01:01 00:00:00", 0x9011: "+00:00", 0x9291: "000000900"}
    image_mod.new("RGB", (3, 3)).save(photo, exif=tags)
    os.utime(photo, ns=(BASE_NS + 100, BASE_NS + 100))
    if photo.stat().st_mtime_ns != BASE_NS + 100:
        pytest.skip("filesystem does not keep 100 ns mtimes")
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--template", ROOT / "templates/report_form.hwpx")
    assert code == 0, log
    events = read_json(out / "events.json")
    assert [e["source"] for e in events] == ["파일시스템(mtime)", "EXIF"]
    assert events[1]["instant_ns"] == BASE_NS + 900 and events[1]["precision_ns"] == 1
    assert not any(e.get("order_tie") for e in events)
    rows = [r for r in form_rows(out / "양식초안.hwpx") if len(r) == 4 and r[2] == "clock.jpg"]
    assert [r[1] for r in rows] == ["파일시스템(mtime)", "EXIF"]


def test_exif_subsecond_beyond_nanoseconds_and_ties():
    shot = exif_audit.capture_time({"DateTimeOriginal": "2024:01:01 00:00:00", "SubSecTimeOriginal": "0000001239",
                                    "OffsetTimeOriginal": "+00:00"})
    assert shot["instant_ns"] == BASE_NS + 123 and shot["subsec_truncated"] and shot["subsec"] == "0000001239"
    coarse = exif_audit.capture_time({"DateTimeOriginal": "2024:01:01 00:00:00", "OffsetTimeOriginal": "+00:00"})
    assert coarse["precision_ns"] == 10**9
    file_ev = {"source": "파일시스템(mtime)", "mtime_ns": BASE_NS + 500, "timestamp": ""}
    exif_ev = {"source": "EXIF", **exif_audit.event_fields(coarse)}
    later = {"source": "파일시스템(mtime)", "mtime_ns": BASE_NS + 10**9, "timestamp": ""}
    ordered = merge_timeline.order_events([later, file_ev, exif_ev])
    assert ordered == [exif_ev, file_ev, later]
    # A seconds-only EXIF time cannot be ordered against a write inside that same second.
    assert exif_ev.get("order_tie") and file_ev.get("order_tie") and not later.get("order_tie")


# ------------------------------------------------------------------ 5 sidecars outside the inventory

def test_sidecar_outside_inventory_is_not_read(tmp_path):
    folder = wal_folder(tmp_path, [b"2"])
    db, wal = folder / "sample.db", folder / "sample.db-wal"
    opened: list[str] = []
    real_hash, real_copy = sqlite_survey.sha256_file, sqlite_survey._copy_hashed

    def spy_hash(path):
        opened.append(Path(path).name)
        return real_hash(path)

    def spy_copy(src, dst):
        opened.append(Path(src).name)
        return real_copy(src, dst)

    with patch.object(sqlite_survey, "sha256_file", spy_hash), patch.object(sqlite_survey, "_copy_hashed", spy_copy):
        rec, = sqlite_survey.scan(folder, files=[db])  # the walker approved only the body
    assert "sample.db-wal" not in opened
    assert rec["out_of_scope_sidecars"] == [{"file": "sample.db-wal", "reason": rec["out_of_scope_sidecars"][0]["reason"]}]
    assert rec["sidecars"] == [] and rec["total_rows"] is None and rec["visible_rows"] == 1

    rec, = sqlite_survey.scan(folder, files=[db, wal])
    entry, = rec["sidecars"]
    assert entry["in_inventory"] and entry["sha256"] == hashlib.sha256(wal.read_bytes()).hexdigest()
    assert rec["total_rows"] == 2


def test_linked_sidecar_is_out_of_scope_in_report(tmp_path):
    folder = wal_folder(tmp_path, [b"2"])
    outside = tmp_path / "elsewhere-wal"
    (folder / "sample.db-wal").rename(outside)
    try:
        (folder / "sample.db-wal").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    survey = case_survey.survey(folder, [], False)
    assert any(e["path"] == "sample.db-wal" for e in survey["scope"]["excluded"])
    rec, = survey["steps"]["sqlite"]["result"]["records"]
    assert [o["file"] for o in rec["out_of_scope_sidecars"]] == ["sample.db-wal"]
    assert rec["total_rows"] is None and rec["visible_rows"] == 1  # the linked WAL's row was not used
    assert sqlite_survey.survey_db(folder / "sample.db")["out_of_scope_sidecars"][0]["reason"] == \
        evidence_manifest.LINK_REASON


# ------------------------------------------------------------------ 8 reference matching order

def nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


@pytest.mark.parametrize("label,ref,items,expected", [
    ("single", "기록.bin", [nfd("기록.bin")], ("match", "path", nfd("기록.bin"))),
    ("parent-only", "기록/item.bin", [nfd("기록") + "/item.bin"], ("match", "path", nfd("기록") + "/item.bin")),
    ("other-root", "폴더/기록.bin", ["other/기록.bin", "폴더/" + nfd("기록.bin")], ("match", "path", "폴더/" + nfd("기록.bin"))),
    ("ambiguous", "기록.bin", ["a/" + nfd("기록.bin"), "b/" + nfd("기록.bin")], ("ambiguous", "path_suffix", None)),
    ("literal-first", "폴더/기록.bin", ["폴더/기록.bin", nfd("폴더/기록.bin")], ("match", "path", "폴더/기록.bin")),
    ("nfc-duplicates", "폴더/기록.bin", [nfd("폴더/기록.bin"), "폴더/" + nfd("기록.bin")], ("ambiguous", "path", None)),
])
def test_reference_prefers_full_path_over_file_name(label, ref, items, expected):
    sha = hashlib.sha256(b"x").hexdigest()
    result, = hash_reference.match_reference([{"name": ref, "algorithm": "sha256", "hash": sha}],
                                             [{"path": p, "sha256": sha} for p in items])
    assert (result["result"], result["matched_by"], result["file"]) == expected
    assert result["name"] == ref
    if expected[0] == "match":
        assert result["path_differs"] is False
    else:
        assert sorted(result["candidates"]) == sorted(items)


# ------------------------------------------------------------------ 12 EXIF offset states

def test_exif_offset_valid_absent_and_malformed_stay_distinct(tmp_path):
    image_mod = pytest.importorskip("PIL.Image")
    folder = tmp_path / "evidence"
    folder.mkdir()
    for name, offset in (("valid.jpg", "+05:45"), ("malformed.jpg", "+09:75"), ("absent.jpg", None)):
        tags = image_mod.Exif()
        ifd = {0x9003: "2024:01:01 12:30:00", 0x9291: "123456789"}
        if offset:
            ifd[0x9011] = offset
        tags[0x8769] = ifd
        image_mod.new("RGB", (2, 2)).save(folder / name, exif=tags)
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--template", ROOT / "templates/report_form.hwpx")
    assert code == 0, log
    events = {e["file"]: e for e in read_json(out / "events.json") if e["source"] == "EXIF"}
    states = {name: (e["offset_status"], e["recorded"]["OffsetTimeOriginal"], e.get("timezone_assumed"))
              for name, e in events.items()}
    assert states == {"valid.jpg": ("valid", "+05:45", None),
                      "malformed.jpg": ("malformed", "+09:75", "KST (정렬 전용)"),
                      "absent.jpg": ("absent", None, "KST (정렬 전용)")}
    survey = read_json(out / "선조사.json")["steps"]["exif"]["result"]["records"]
    assert {Path(r["file"]).name: r["capture_time"]["offset_status"] for r in survey} == {
        "valid.jpg": "valid", "malformed.jpg": "malformed", "absent.jpg": "absent"}
    rows = {r[2]: r[3] for r in form_rows(out / "양식초안.hwpx") if len(r) == 4 and r[1] == "EXIF"}
    assert "UTC+05:45 기록" in rows["valid.jpg"]
    assert "형식 오류(원문 '+09:75')" in rows["malformed.jpg"] and "정렬만 KST 가정" in rows["malformed.jpg"]
    assert "시간대 미기록" in rows["absent.jpg"] and "형식 오류" not in rows["absent.jpg"]
    md = (out / "보고서초안.md").read_text(encoding="utf-8")
    assert "1건은 UTC 오프셋 기록이 형식 오류다(원문 '+09:75')" in md
    assert "1건은 UTC 오프셋(OffsetTimeOriginal)이 기록되지 않아" in md
