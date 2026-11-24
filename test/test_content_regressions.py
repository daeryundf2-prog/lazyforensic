# -*- coding: utf-8 -*-
"""Content-correctness regressions from the D11 review (synthetic inputs only).

Each test reproduces one reported content error: committed WAL rows, Korean keyword
encodings, EXIF offset/sub-second, nanosecond mtime, Unicode path forms and console
JSON, SQLite view rows and EXIF string truncation.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import case_survey  # noqa: E402
import console_json  # noqa: E402
import evidence_manifest  # noqa: E402
import exif_audit  # noqa: E402
import hash_reference  # noqa: E402
import keyword_report  # noqa: E402
import merge_timeline  # noqa: E402
import report_draft  # noqa: E402
import sqlite_survey  # noqa: E402

TRUTH = "합성 표식"


def run_report(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = report_draft.main([str(a) for a in args])
    return code, buf.getvalue()


def snapshot(folder: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}


# ------------------------------------------------------------------ 4 committed WAL rows

def wal_evidence(tmp_path: Path) -> Path:
    """Body with one checkpointed row plus a WAL holding a second committed row."""
    writer_path = tmp_path / "writer.db"
    writer = sqlite3.connect(writer_path)
    writer.execute("PRAGMA journal_mode=wal")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE events(v INTEGER)")
    writer.execute("INSERT INTO events VALUES (1)")
    writer.commit()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    writer.execute("INSERT INTO events VALUES (2)")
    writer.commit()
    folder = tmp_path / "evidence"
    folder.mkdir()
    for suffix in ("", "-wal"):  # copied while the writer is idle, as an acquisition would
        shutil.copyfile(f"{writer_path}{suffix}", folder / f"rows.db{suffix}")
    writer.close()
    return folder


def test_committed_wal_row_is_counted_without_touching_source(tmp_path):
    folder = wal_evidence(tmp_path)
    before = snapshot(folder)
    rec = sqlite_survey.survey_db(folder / "rows.db")
    assert snapshot(folder) == before  # same file set and bytes: no -shm, no checkpoint
    assert rec["total_rows"] == 2
    assert rec["read_from"] == "working_copy"
    assert rec["integrity_ok"]
    wal, = rec["sidecars"]
    assert wal["file"] == "rows.db-wal"
    assert wal["sha256"] == before["rows.db-wal"] and rec["sha256"] == before["rows.db"]
    assert wal["frames_committed"] == wal["frames_in_file"] == 1


def test_report_survey_counts_wal_rows(tmp_path):
    folder = wal_evidence(tmp_path)
    before = snapshot(folder)
    code, out = run_report(folder, "-o", tmp_path / "out")
    assert code == 0, out
    survey = json.loads((tmp_path / "out" / "선조사.json").read_text(encoding="utf-8"))
    rec, = survey["steps"]["sqlite"]["result"]["records"]
    assert rec["total_rows"] == 2
    md = (tmp_path / "out" / "보고서초안.md").read_text(encoding="utf-8")
    assert "-wal/-journal 반영 1개" in md
    assert snapshot(folder) == before


def test_unreplayable_wal_marks_rows_unknown(tmp_path):
    folder = wal_evidence(tmp_path)
    with patch.object(sqlite_survey, "_copy_hashed", side_effect=OSError("synthetic copy failure")):
        rec = sqlite_survey.survey_db(folder / "rows.db")
    assert rec["total_rows"] is None
    assert "행 수 미확인" in rec["error"]
    assert "tables" not in rec


# ------------------------------------------------------------------ 16 view rows

def test_view_rows_are_not_stored_rows(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    with contextlib.closing(sqlite3.connect(db_path)) as db:
        db.execute("CREATE TABLE events(v INTEGER)")
        db.execute("INSERT INTO events VALUES (1)")
        db.execute("CREATE VIEW all_events AS SELECT * FROM events")
        db.commit()
    rec = sqlite_survey.survey_db(db_path)
    assert rec["total_rows"] == 1
    assert rec["view_rows"] == 1
    assert sqlite_survey.main([str(db_path)]) == 0
    text = capsys.readouterr().out
    assert "총 1행" in text and "뷰 1개 조회 결과 1행(저장 행 아님)" in text
    assert "총 2행" not in text


# ------------------------------------------------------------------ 5 Korean keyword encodings

@pytest.mark.parametrize("encoding", ["utf-8", "cp949", "utf-16", "utf-8-sig"])
def test_korean_keyword_found_in_each_encoding(tmp_path, encoding):
    item = tmp_path / "기록.txt"
    item.write_bytes(TRUTH.encode(encoding))
    res = keyword_report.search_file_result(item, ["표식"], 0)
    assert res["undecodable"] is None and res["error"] is None
    if encoding == "cp949":
        # D24: undeclared CP949 also reads as EUC-JP/Big5 kanji — a line candidate until the codec is named.
        assert res["hits"] == [] and [c["line"] for c in res["uncertain"]["candidate_hits"]] == [1]
        res = keyword_report.search_file_result(item, ["표식"], 0, "cp949")
    assert [h["line"] for h in res["hits"]] == [1]


def test_declared_markup_encoding_and_nfd_text(tmp_path):
    xml = tmp_path / "doc.xml"
    xml.write_bytes('<?xml version="1.0" encoding="euc-kr"?><a>합성 표식</a>'.encode("cp949"))
    res = keyword_report.search_file_result(xml, ["표식"], 0)
    assert (res["encoding"], res["basis"], len(res["hits"])) == ("cp949", "declared", 1)
    nfd = tmp_path / "nfd.txt"
    nfd.write_text(unicodedata.normalize("NFD", TRUTH), encoding="utf-8")
    assert len(keyword_report.search_file_result(nfd, ["표식"], 0)["hits"]) == 1


def test_undecodable_text_leaves_the_search_denominator(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    for name, enc in (("a-utf8.txt", "utf-8"), ("b-cp949.txt", "cp949"), ("c-utf16.txt", "utf-16")):
        (folder / name).write_bytes(TRUTH.encode(enc))
    (folder / "d-broken.txt").write_bytes(b"\x80\xff" * 4)  # no BOM or NUL, not UTF-8, not CP949
    survey = case_survey.survey(folder, ["표식"], False)
    kw = survey["steps"]["keywords"]["result"]
    assert (kw["files_searched"], kw["total_hits"], kw["files_undecodable"]) == (3, 2, 1)
    # undeclared CP949 adds one candidate line (D24); the broken file was already uncertain (BOM-less UTF-16 fits)
    assert (kw["files_encoding_uncertain"], kw["candidate_hits"]) == (2, 1)
    assert kw["status"] == "partial"
    assert kw["undecodable"][0]["file"].endswith("d-broken.txt")
    assert kw["encodings"] == {"utf-8": 1, "cp949": 1, "utf-16": 1}

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        keyword_report.main([str(folder), "표식", "--json"])
    report = json.loads(out.getvalue())
    assert (report["files_searched"], report["total_hits"], report["files_undecodable"]) == (3, 2, 1)
    assert report["candidate_hits"] == 1

    code, log = run_report(folder, "-o", tmp_path / "out", "--keywords", "표식")
    md = (tmp_path / "out" / "보고서초안.md").read_text(encoding="utf-8")
    row = next(line for line in md.splitlines() if line.startswith("| 키워드 검색 |"))
    assert "부분 완료" in row and "텍스트 파일 3개 검색" in row and "인코딩 판정 불가 1개" in row, row


# ------------------------------------------------------------------ 6 / 17 EXIF

class _FakeExif(dict):
    def __init__(self, base, exif_ifd):
        super().__init__(base)
        self._ifd = exif_ifd

    def get_ifd(self, tag):
        return self._ifd if tag == 0x8769 else {}


class _FakeImage:
    format, width, height = "JPEG", 8, 8

    def __init__(self, exif):
        self._exif = exif

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getexif(self):
        return self._exif


class _FakePillow:
    """Pillow's Image surface used by exif_audit, so the contract runs without Pillow."""

    def __init__(self, base, exif_ifd):
        self._exif = _FakeExif(base, exif_ifd)

    def open(self, path):
        return _FakeImage(self._exif)


SOFTWARE = "A" * 100 + "-TAIL"
CAMERA_IFD = {0x9003: "2024:01:01 10:00:00", 0x9011: "+00:00", 0x9291: "500"}


def test_exif_keeps_offset_subsecond_and_full_strings(tmp_path):
    rec = exif_audit.audit_image(tmp_path / "camera.jpg", _FakePillow({0x0131: SOFTWARE}, CAMERA_IFD))
    other = exif_audit.audit_image(tmp_path / "other.jpg", _FakePillow({0x0131: "A" * 100 + "-OTHER"}, CAMERA_IFD))
    assert rec["tags"]["Software"] == SOFTWARE  # 105 characters, not 100
    assert rec["tags"]["Software"] != other["tags"]["Software"]
    shot = rec["capture_time"]
    assert shot["timestamp"] == "2024-01-01T10:00:00.500+00:00"
    assert datetime.fromisoformat(shot["timestamp"]) == datetime(2024, 1, 1, 10, 0, 0, 500000, tzinfo=timezone.utc)
    shown = exif_audit.excerpt(SOFTWARE)
    assert shown.startswith("A" * 100) and "발췌" in shown and "105자" in shown


def test_exif_without_offset_is_marked_as_assumption(tmp_path):
    shot = exif_audit.capture_time({"DateTimeOriginal": "2024:01:01 10:00:00"})
    assert shot["timestamp"] == "2024-01-01T10:00:00" and shot["offset"] is None
    src = tmp_path / "exif.json"
    src.write_text(json.dumps({"records": [
        {"file": "a.jpg", "tags": {"DateTimeOriginal": "2024:01:01 10:00:00"}},
        {"file": "b.jpg", "tags": {**{exif_audit.TAG_NAMES[k]: v for k, v in CAMERA_IFD.items()}}},
    ]}), encoding="utf-8")
    a, b = merge_timeline.events_from_exif(str(src))
    assert a["timestamp"] == "2024-01-01T10:00:00+09:00" and "KST로 가정" in a["note"]
    assert b["timestamp"] == "2024-01-01T10:00:00.500+00:00" and "note" not in b


def test_exif_offset_orders_events_against_file_mtime(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    photo = folder / "camera.jpg"
    photo.write_bytes(b"synthetic")
    six_utc = int(datetime(2024, 1, 1, 6, tzinfo=timezone.utc).timestamp()) * 10**9
    os.utime(photo, ns=(six_utc, six_utc))
    inventory = report_draft.build_inventory(folder, [photo])
    tags = {exif_audit.TAG_NAMES[k]: v for k, v in CAMERA_IFD.items()}
    survey = {"steps": {"exif": {"status": "ok", "result": {"image_files": 1, "records": [
        {"file": str(photo), "exif_present": True, "tags": tags}]}}}}
    events = report_draft.build_events(folder, inventory, survey, [])
    # 06:00 UTC file write precedes the 10:00:00.500 UTC capture (19:00:00.500 KST).
    assert [e["source"] for e in events] == ["파일시스템(mtime)", "EXIF"]
    shot = events[1]
    assert shot["timestamp"] == "2024-01-01T10:00:00.500+00:00"
    assert shot["recorded"] == {"DateTimeOriginal": "2024:01:01 10:00:00", "SubSecTimeOriginal": "500",
                                "OffsetTimeOriginal": "+00:00"}
    assert "timezone_assumed" not in shot


def test_exif_report_end_to_end_with_pillow(tmp_path):
    image_mod = pytest.importorskip("PIL.Image")
    folder = tmp_path / "evidence"
    folder.mkdir()
    jpeg = folder / "camera.jpg"
    tags = image_mod.Exif()
    tags[0x8769] = CAMERA_IFD
    tags[0x0131] = SOFTWARE
    image_mod.new("RGB", (8, 8)).save(jpeg, exif=tags)
    six_utc = int(datetime(2024, 1, 1, 6, tzinfo=timezone.utc).timestamp())
    os.utime(jpeg, (six_utc, six_utc))
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--template", ROOT / "templates/report_form.hwpx")
    assert code == 0, log
    events = json.loads((out / "events.json").read_text(encoding="utf-8"))
    assert [(e["source"], e["timestamp"]) for e in events] == [
        ("파일시스템(mtime)", "2024-01-01T15:00:00.000000+09:00"), ("EXIF", "2024-01-01T10:00:00.500+00:00")]
    survey = json.loads((out / "선조사.json").read_text(encoding="utf-8"))
    assert survey["steps"]["exif"]["result"]["records"][0]["tags"]["Software"] == SOFTWARE
    md = (out / "보고서초안.md").read_text(encoding="utf-8")
    assert "…(발췌, 원래 105자)" in md
    assert "2024-01-01 10:00:00.500+00:00" in md
    verify = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    assert verify["verdict"] == "PASS", verify


# ------------------------------------------------------------------ 7 nanosecond mtime

def test_mtime_keeps_kst_date_and_subsecond_order(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    base = 1704067200 * 10**9  # 2024-01-01T09:00:00+09:00
    for name, frac in (("a-late.bin", 900_000_000), ("z-early.bin", 100_000_000)):
        (folder / name).write_bytes(b"123")
        os.utime(folder / name, ns=(base + frac, base + frac))
    edge = folder / "edge.bin"
    edge.write_bytes(b"edge")
    edge_ns = 1704121200 * 10**9 - 1  # last nanosecond of 2024-01-01 KST
    os.utime(edge, ns=(edge_ns, edge_ns))
    stored = edge.stat().st_mtime_ns  # filesystems with coarser clocks truncate, never round up
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out)
    assert code == 0, log
    events = json.loads((out / "events.json").read_text(encoding="utf-8"))
    assert [e["file"] for e in events] == ["z-early.bin", "a-late.bin", "edge.bin"]
    last = events[-1]
    assert last["mtime_ns"] == stored
    assert last["mtime"].startswith("2024-01-01T23:59:59.9999999") and last["mtime"].endswith("+09:00")
    assert last["timestamp"] == "2024-01-01T23:59:59.999999+09:00"
    rows = list(csv.reader((out / "파일해시목록.csv").read_text(encoding="utf-8-sig").splitlines()))
    edge_row = next(r for r in rows if r[1] == "edge.bin")
    assert edge_row[4] == last["mtime"].removesuffix("+09:00").replace("T", " ")
    md = (out / "보고서초안.md").read_text(encoding="utf-8")
    assert edge_row[4] in md and "마이크로초까지만 표시" in md
    manifest = evidence_manifest.build_manifest(folder)
    assert next(f for f in manifest["files"] if f["path"] == "edge.bin")["mtime"].startswith("2024-01-01T23:59:59.")


# ------------------------------------------------------------------ 15 Unicode path forms / console JSON

def test_reference_matches_single_nfd_candidate_and_holds_several(tmp_path):
    sha = hashlib.sha256(b"abc").hexdigest()
    nfd = unicodedata.normalize("NFD", "copy/기록.bin")
    entry = {"name": "copy/기록.bin", "algorithm": "sha256", "hash": sha, "source": "list.csv", "line": 2}
    result, = hash_reference.match_reference([entry], [{"path": nfd, "sha256": sha}])
    assert (result["result"], result["matched_by"], result["normalization"]) == ("match", "path", "NFC")
    assert result["file"] == nfd and result["name"] == "copy/기록.bin"  # both original forms kept
    two = [{"path": unicodedata.normalize("NFD", f"{d}/기록.bin"), "sha256": sha} for d in ("a", "b")]
    held, = hash_reference.match_reference([{**entry, "name": "기록.bin"}], two)
    assert held["result"] == "ambiguous" and held["file"] is None and held["normalization"] == "NFC"


def test_reference_nfc_list_against_nfd_evidence_in_report(tmp_path):
    folder = tmp_path / "evidence"
    (folder / "copy").mkdir(parents=True)
    name = unicodedata.normalize("NFD", "기록.bin")
    (folder / "copy" / name).write_bytes(b"abc")
    actual = next((folder / "copy").iterdir()).name  # what the filesystem returns
    ref = tmp_path / "list.csv"
    ref.write_text("path,sha256\ncopy/기록.bin," + hashlib.sha256(b"abc").hexdigest() + "\n", encoding="utf-8")
    code, log = run_report(folder, "-o", tmp_path / "out", "--hashes", ref)
    assert code == 0, log
    entry, = json.loads((tmp_path / "out" / "무결성검증.json").read_text(encoding="utf-8"))["reference"]["entries"]
    assert entry["result"] == "match"
    assert entry["file"] == f"copy/{actual}"


class _Stream:
    def __init__(self, encoding):
        self.encoding = encoding


def test_console_json_escapes_only_when_the_stream_cannot_encode():
    record = {"file": "기록🐟.db"}
    assert console_json.dumps_for(_Stream("utf-8"), record) == '{"file": "기록🐟.db"}'
    assert console_json.dumps_for(_Stream("cp949"), {"file": "기록.db"}) == '{"file": "기록.db"}'
    escaped = console_json.dumps_for(_Stream("cp949"), record)
    escaped.encode("cp949")
    assert json.loads(escaped) == record


@pytest.mark.parametrize("encoding", ["utf-8", "cp949"])
def test_db_json_round_trips_through_console_encoding(tmp_path, encoding):
    db_path = tmp_path / "기록🐟.db"
    with contextlib.closing(sqlite3.connect(db_path)) as db:
        db.execute("CREATE TABLE item(v)")
        db.commit()
    env = dict(os.environ, PYTHONIOENCODING=f"{encoding}:replace")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/sqlite_survey.py"), str(db_path), "--json"],
                          capture_output=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    record, = json.loads(proc.stdout.decode(encoding))["records"]
    assert Path(record["file"]).name == "기록🐟.db"
