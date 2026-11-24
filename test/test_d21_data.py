# -*- coding: utf-8 -*-
"""Data regressions from the D21 review (synthetic inputs only).

1 undeclared bytes that read naturally in several codecs (CP949 '캡처' as UTF-8, EUC-JP/Big5
kanji as CP949) must not end as a complete zero-hit search, and hits on such lines are not
confirmed; 2 SQLite user tables whose names start with 'sqlite' (LIKE '_' wildcard) and views
that cannot be read; 6 EXIF rows in forms keep the recorded sub-second digits and offset status.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import case_survey  # noqa: E402
import keyword_report  # noqa: E402
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


# ------------------------------------------------------------------ 1 keyword encodings

def _search(tmp_path: Path, data: bytes, keywords: list[str], encoding: str | None = None) -> dict:
    item = tmp_path / "sample.log"
    item.write_bytes(data)
    return keyword_report.search_files([item], keywords, 0, encoding)


def _candidates(found: dict) -> list[tuple[int, str, str]]:
    return [(h["line"], h["keyword"], h["encoding"]) for u in found["uncertain"] for h in u["candidate_hits"]]


def _hits(found: dict) -> list[tuple[int, str]]:
    return [(h["line"], h["keyword"]) for r in found["results"] for h in r["hits"]]


@pytest.mark.parametrize("phrase,codec", [
    ("09:05 캡처", "cp949"), ("캡처", "cp949"),
    ("東京", "euc_jp"), ("日本語", "euc_jp"), ("検査記録", "euc_jp"), ("受付記録", "euc_jp"),
    ("中文", "big5"), ("測試", "big5"), ("資料", "big5"), ("封存記錄", "big5"),
])
def test_bytes_valid_in_several_codecs_are_not_a_complete_zero_hit_search(tmp_path, phrase, codec):
    found = _search(tmp_path, phrase.encode(codec), [phrase])
    assert found["status"] == "partial" and _hits(found) == []
    assert (1, phrase, codec) in _candidates(found)
    explicit = _search(tmp_path, phrase.encode(codec), [phrase], codec)
    assert explicit["status"] == "complete" and _hits(explicit) == [(1, phrase)]


@pytest.mark.parametrize("phrase", ["인수 기록", "봉인 상태 확인", "접수했습니다", "첨부파일 확인", "찜", "괜찮습니다",
                                    "2024-01-01 점검 완료", "됐습니다"])
def test_ordinary_korean_cp949_stays_a_confirmed_hit(tmp_path, phrase):
    found = _search(tmp_path, phrase.encode("cp949"), [phrase])
    assert found["status"] == "complete" and found["uncertain"] == []
    assert _hits(found) == [(1, phrase)] and found["encodings"] == {"cp949": 1}


def test_hits_on_an_uncertain_line_are_never_confirmed(tmp_path):
    # Shift-JIS '東京' also reads as CP949 '뱦떈': neither reading of that line is a confirmed hit.
    raw = "東京".encode("shift_jis")
    found = _search(tmp_path, raw + b"\n" + "표식".encode("cp949"), [raw.decode("cp949"), "東京", "표식"])
    assert _hits(found) == [(2, "표식")]
    assert sorted(_candidates(found)) == [(1, "東京", "shift_jis"), (1, raw.decode("cp949"), "cp949")]
    # A CP949 syllable from the extension area keeps the file uncertain (BOM-less UTF-16 also fits).
    lone = _search(tmp_path, "뷁".encode("cp949"), ["뷁"])
    assert lone["status"] == "partial" and _hits(lone) == [] and (1, "뷁", "cp949") in _candidates(lone)


def test_mixed_log_and_declared_codecs_keep_confirmed_hits(tmp_path):
    mixed = _search(tmp_path, "기록".encode() + b"\n" + "표식".encode("cp949"), ["기록", "표식"])
    assert mixed["status"] == "complete" and _hits(mixed) == [(1, "기록"), (2, "표식")]
    declared = _search(tmp_path, b'<?xml version="1.0" encoding="euc-jp"?>\n' + "東京".encode("euc_jp"), ["東京"])
    assert declared["status"] == "complete" and _hits(declared) == [(2, "東京")]


def test_report_passes_the_confirmed_codec_to_the_keyword_step(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    (folder / "검사.log").write_bytes("検査記録".encode("euc_jp"))
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--keywords", "検査記録", "--no-hwpx", "--no-docx")
    assert code == 0, log
    step = read_json(out / "선조사.json")["steps"]["keywords"]["result"]
    assert (step["status"], step["total_hits"], step["files_encoding_uncertain"], step["candidate_hits"]) == \
        ("partial", 0, 1, 1)
    out2 = tmp_path / "out2"
    code, log = run_report(folder, "-o", out2, "--keywords", "検査記録", "--keyword-encoding", "euc_jp",
                           "--no-hwpx", "--no-docx")
    assert code == 0, log
    step = read_json(out2 / "선조사.json")["steps"]["keywords"]["result"]
    assert (step["status"], step["total_hits"], step["encoding_requested"]) == ("complete", 1, "euc_jp")
    assert step["results"][0]["encoding_basis"] == "explicit"
    md = (out2 / "보고서초안.md").read_text(encoding="utf-8")
    assert "지정 코덱 euc_jp로만 읽음" in md
    assert run_report(folder, "-o", tmp_path / "out3", "--keywords", "x", "--keyword-encoding", "no_such")[0] == 2


def test_case_survey_cli_accepts_keyword_encoding(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    (folder / "기록.log").write_bytes("中文".encode("big5"))
    report = case_survey.survey(folder, ["中文"], False, keyword_encoding="big5")
    step = report["steps"]["keywords"]["result"]
    assert (step["status"], step["total_hits"]) == ("complete", 1)


# ------------------------------------------------------------------ 2 SQLite names and views

def test_sqlite_prefixed_user_tables_match_the_catalog(tmp_path, capsys):
    db = tmp_path / "names.db"
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE sqliteLog(n)")
        conn.execute("CREATE TABLE sqlite(n)")
        conn.execute('CREATE TABLE "로그""항목"(n)')
        conn.execute("CREATE TABLE empty(n)")
        conn.execute("CREATE TABLE seq(id INTEGER PRIMARY KEY AUTOINCREMENT, n)")  # creates sqlite_sequence
        for table in ("sqliteLog", "sqlite", '"로그""항목"'):
            conn.executemany(f"INSERT INTO {table} VALUES (?)", [(1,), (2,)])
        conn.execute("INSERT INTO seq(n) VALUES (1)")
        conn.commit()
        catalog = {name: conn.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                   for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                   if not name.startswith("sqlite_")}
    rec = sqlite_survey.survey_db(db)
    assert {t["name"]: t["row_count"] for t in rec["tables"]} == catalog
    assert catalog == {"sqliteLog": 2, "sqlite": 2, '로그"항목': 2, "empty": 0, "seq": 1}
    assert rec["total_rows"] == 7 and "sqlite_sequence" not in {t["name"] for t in rec["tables"]}
    assert sqlite_survey.main([str(db)]) == 0
    assert "테이블 5개 총 7행" in capsys.readouterr().out


def test_unreadable_view_is_unconfirmed_and_empty_view_is_zero(tmp_path, capsys):
    db = tmp_path / "views.db"
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.create_function("is_received", 1, lambda v: 1)
        conn.execute("CREATE TABLE observations(state)")
        conn.executemany("INSERT INTO observations VALUES (?)", [("a",), ("b",)])
        conn.execute("CREATE VIEW received AS SELECT * FROM observations WHERE is_received(state)")
        conn.execute("CREATE VIEW none_left AS SELECT * FROM observations WHERE 0")
        conn.commit()
    rec = sqlite_survey.survey_db(db)
    assert rec["total_rows"] == 2 and "limitations" not in rec
    views = {t["name"]: t["row_count"] for t in rec["tables"] if t["type"] == "view"}
    assert views == {"received": None, "none_left": 0}
    assert rec["view_rows"] is None and rec["visible_view_rows"] == 0 and "received" in rec["view_note"]
    assert sqlite_survey.main([str(db)]) == 0
    out = capsys.readouterr().out
    assert "뷰 2개 조회 결과 미확인" in out and "received (view): 미확인(" in out and "none_left (view): 0행" in out
    # Only readable views: a plain total.
    with contextlib.closing(sqlite3.connect(db)) as conn:
        conn.execute("DROP VIEW received")
        conn.commit()
    rec = sqlite_survey.survey_db(db)
    assert rec["view_rows"] == 0 and "view_note" not in rec


# ------------------------------------------------------------------ 6 EXIF sub-second in forms

def _texts(package: Path, part: str) -> list[str]:
    with ZipFile(package) as z:
        root = ET.fromstring(z.read(part))
    return ["".join(t.text or "" for t in p.iterfind(".//{*}t")) for p in root.iterfind(".//{*}p")]


def test_exif_rows_show_recorded_subsecond_and_offset_status_with_ties(tmp_path):
    image_mod = pytest.importorskip("PIL.Image")
    folder = tmp_path / "evidence"
    folder.mkdir()
    for name, subsec in (("a200.jpg", "000000200"), ("b600.jpg", "000000600")):
        tags = image_mod.Exif()
        tags[0x8769] = {0x9003: "2024:01:01 09:00:00", 0x9291: subsec}  # no offset: sort-only KST
        image_mod.new("RGB", (2, 2)).save(folder / name, exif=tags)
    # Same KST instant as the 600 ns photo; the other files are far away.
    (folder / "same.txt").write_bytes(b"x")
    os.utime(folder / "same.txt", ns=(BASE_NS + 600, BASE_NS + 600))
    for name in ("a200.jpg", "b600.jpg"):
        os.utime(folder / name, ns=(BASE_NS + 10**12, BASE_NS + 10**12))
    if (folder / "same.txt").stat().st_mtime_ns != BASE_NS + 600:
        pytest.skip("filesystem does not keep 100 ns mtimes")
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--template", ROOT / "templates/report_form.hwpx")
    assert code == 0, log
    events = read_json(out / "events.json")
    exif = {e["file"]: e for e in events if e["source"] == "EXIF"}
    assert {k: (e["recorded"]["SubSecTimeOriginal"], e["instant_ns"]) for k, e in exif.items()} == \
        {"a200.jpg": ("000000200", BASE_NS + 200), "b600.jpg": ("000000600", BASE_NS + 600)}
    same = next(e for e in events if e["file"] == "same.txt")
    assert exif["b600.jpg"].get("order_tie") and same.get("order_tie") and not exif["a200.jpg"].get("order_tie")

    with ZipFile(out / "양식초안.hwpx") as package:
        root = ET.fromstring(package.read("Contents/section0.xml"))
    rows = [["".join(t.text or "" for t in c.iterfind(".//{*}t")) for c in row.findall("{*}tc")]
            for row in root.iterfind(".//{*}tr")]
    form = [(r[0], r[1], r[2]) for r in rows if len(r) == 4 and r[0].startswith("2024-01-01")]
    assert form[:3] == [
        ("2024-01-01 09:00:00.000000200 (시간대 미기록, 정렬만 KST 가정)", "EXIF", "a200.jpg"),
        ("2024-01-01T09:00:00.000000600+09:00 (동률)", "파일시스템(mtime)", "same.txt"),
        ("2024-01-01 09:00:00.000000600 (시간대 미기록, 정렬만 KST 가정) (동률)", "EXIF", "b600.jpg")]
    for package, part in (("보고서초안.docx", "word/document.xml"), ("보고서초안.hwpx", "Contents/section0.xml")):
        body = _texts(out / package, part)
        assert "2024-01-01 09:00:00.000000200 (시간대 미기록, 정렬만 KST 가정)" in body, package
        assert "2024-01-01 09:00:00.000000600 (시간대 미기록, 정렬만 KST 가정)" in body, package
