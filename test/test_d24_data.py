# -*- coding: utf-8 -*-
"""Data regressions from the D24 review (synthetic inputs only).

1-6 keyword encodings: a misreading is never a confirmed hit, a real marker is never a confirmed miss,
hits are counted per line with the same line boundaries in every mode, results do not depend on query
order, a named codec that conflicts with a file marker is recorded, and non-text codecs are usage errors.
7-8 SQLite: unreadable view columns are unconfirmed (not zero) and FTS shadow tables are not user rows.
9 and 11 EXIF: sub-second digits beyond nine decide the order, and the standalone timeline and the EXIF
command show the recorded digits.
"""
from __future__ import annotations

import codecs
import contextlib
import io
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "forensic-timeline" / "scripts"))

import case_survey  # noqa: E402
import exif_audit  # noqa: E402
import generate_timeline  # noqa: E402
import keyword_report  # noqa: E402
import merge_timeline  # noqa: E402
import report_draft  # noqa: E402
import sqlite_survey  # noqa: E402

ENV = keyword_report.ENCODING_ENV


@pytest.fixture(autouse=True)
def _no_operator_codec(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


def run_report(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        code = report_draft.main([str(a) for a in args])
    return code, buf.getvalue()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _search(tmp_path: Path, data: bytes, keywords: list[str], encoding: str | None = None,
            context: int = 0) -> dict:
    item = tmp_path / "sample.log"
    item.write_bytes(data)
    return keyword_report.search_files([item], keywords, context, encoding)


def _hits(found: dict) -> list[tuple]:
    return [(h["line"], tuple(h["keywords"]), h["text"]) for r in found["results"] for h in r["hits"]]


def _candidates(found: dict) -> list[tuple[int, str, str]]:
    return sorted((h["line"], kw, r["encoding"]) for u in found["uncertain"] for h in u["candidate_hits"]
                  for r in h["readings"] for kw in r["keywords"])


def _keyword_row(md: str) -> str:
    return next(line for line in md.splitlines() if line.startswith("| 키워드 검색 |"))


# ------------------------------------------------------------------ 1 misreadings never confirmed

READINGS = [("캡처", "cp949", "utf-8"), ("東京", "euc_jp", "cp949"), ("中文", "big5", "cp949")]


@pytest.mark.parametrize("word,codec,other_codec", READINGS)
def test_a_reading_in_another_codec_is_never_a_confirmed_hit(tmp_path, word, codec, other_codec):
    raw = word.encode(codec)
    other = raw.decode(other_codec)  # 'ĸó', '톺딸', 'ㄴㅵ'
    for keys in ([word], [other], [word, other], [other, word]):
        found = _search(tmp_path, raw, keys)
        assert found["status"] == "partial" and _hits(found) == [], keys
        expected = {(1, word, codec)} if word in keys else set()
        expected |= {(1, other, other_codec)} if other in keys else set()
        assert set(_candidates(found)) == expected, keys
        assert len(found["uncertain"][0]["candidate_hits"]) == 1  # one line, one candidate
    named = _search(tmp_path, raw, [word, other], codec)
    assert named["status"] == "complete" and [h[:2] for h in _hits(named)] == [(1, (word,))]


@pytest.mark.parametrize("word,codec,other_codec", READINGS)
def test_report_does_not_publish_the_misreading_as_a_hit(tmp_path, word, codec, other_codec):
    folder = tmp_path / "evidence"
    folder.mkdir()
    raw = word.encode(codec)
    (folder / "item.log").write_bytes(raw)
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--keywords", raw.decode(other_codec), "--no-docx", "--no-hwpx")
    assert code == 0, log
    step = read_json(out / "선조사.json")["steps"]["keywords"]["result"]
    assert (step["status"], step["total_hits"], step["candidate_hits"]) == ("partial", 0, 1)
    assert _keyword_row((out / "보고서초안.md").read_text(encoding="utf-8")).startswith("| 키워드 검색 | 부분 완료 — 0건")


def test_declared_and_utf8_controls_stay_confirmed(tmp_path):
    declared = _search(tmp_path, b'<?xml version="1.0" encoding="cp949"?>\n' + "캡처".encode("cp949"),
                       ["캡처", "ĸó"])
    assert declared["status"] == "complete" and [h[:2] for h in _hits(declared)] == [(2, ("캡처",))]
    utf8 = _search(tmp_path, "기록\n화면을 캡처했습니다".encode(), ["기록", "캡처"])
    assert utf8["status"] == "complete" and [h[:2] for h in _hits(utf8)] == [(1, ("기록",)), (2, ("캡처",))]


# ------------------------------------------------------------------ 2 half-width kana

@pytest.mark.parametrize("text,codec", [("東京 ｶﾒﾗ", "euc_jp"), ("検査 ﾛｸﾞ", "euc_jp"), ("ﾒﾓ 東京", "shift_jis"),
                                        ("ｶﾒﾗ", "shift_jis")])
def test_half_width_kana_records_keep_a_candidate(tmp_path, text, codec):
    key = text.split(" ")[0]
    found = _search(tmp_path, text.encode(codec), [key])
    assert found["status"] == "partial" and (1, key, codec) in _candidates(found)
    named = _search(tmp_path, text.encode(codec), [key], codec)
    assert named["status"] == "complete" and [h[:2] for h in _hits(named)] == [(1, (key,))]


# ------------------------------------------------------------------ 3 one line boundary for every mode

@pytest.mark.parametrize("sep", ["\r", "\r\n", "\n", "\u2028"], ids=["cr", "crlf", "lf", "u2028"])
def test_three_lines_stay_three_hits_in_every_mode(tmp_path, sep):
    data = sep.join(["이동 1", "이동 2", "이동 3"]).encode()
    expected = [(n, ("이동",), f"이동 {n}") for n in (1, 2, 3)]
    for encoding in (None, "utf-8", "cp949"):  # automatic, named UTF-8, named legacy codec (UTF-8 lines stay UTF-8)
        found = _search(tmp_path, data, ["이동"], encoding, context=1)
        assert found["status"] == "complete" and _hits(found) == expected, (encoding, found)
        middle = found["results"][0]["hits"][1]["context"]
        assert middle == [{"line": 1, "text": "이동 1"}, {"line": 3, "text": "이동 3"}]


def test_mixed_codec_lines_share_byte_line_numbers(tmp_path):
    data = "이동 1\r이동 2".encode() + b"\r" + "이동 3".encode("cp949") + b"\r\n" + "이동 4".encode()
    auto = _search(tmp_path, data, ["이동"])
    assert [h[0] for h in _hits(auto)] == [1, 2, 4] and _candidates(auto) == [(3, "이동", "cp949")]
    named = _search(tmp_path, data, ["이동"], "cp949")
    assert named["status"] == "complete" and [h[0] for h in _hits(named)] == [1, 2, 3, 4]
    folder = tmp_path / "evidence"
    folder.mkdir()
    (folder / "move.log").write_bytes("이동 1\r이동 2\r이동 3".encode())
    code, log = run_report(folder, "-o", tmp_path / "out", "--keywords", "이동", "--no-docx", "--no-hwpx")
    assert code == 0, log
    step = read_json(tmp_path / "out" / "선조사.json")["steps"]["keywords"]["result"]
    assert step["total_hits"] == 3 and [h["line"] for h in step["results"][0]["hits"]] == [1, 2, 3]


# ------------------------------------------------------------------ 4 query order and the quoted range

def test_common_marker_quote_and_candidates_do_not_depend_on_query_order(tmp_path):
    raw = b"STATUS " + "캡처".encode("cp949")
    alone = _search(tmp_path, raw, ["STATUS"])
    assert alone["status"] == "complete"
    (hit,) = alone["results"][0]["hits"]
    assert hit["text"] == "STATUS " + keyword_report.MASK and "ĸó" not in hit["text"]
    assert hit["quote_basis"] == "codec_independent"
    assert {r["encoding"]: r["text"] for r in hit["readings"]}["cp949"] == "STATUS 캡처"
    results = []
    for keys in (["STATUS", "캡처"], ["캡처", "STATUS"]):
        found = _search(tmp_path, raw, keys)
        (cand,) = found["uncertain"][0]["candidate_hits"]
        per = {r["encoding"]: r["keywords"] for r in cand["readings"]}
        assert per["cp949"] == ["캡처"] and per["utf-8"] == []
        assert cand["keyword"] == "캡처" and cand["keywords"] == ["캡처"]
        results.append((_hits(found), _candidates(found), found["status"], cand))
    assert results[0] == results[1]
    assert results[0][0] == [(1, ("STATUS",), "STATUS " + keyword_report.MASK)]


def test_keywords_on_one_line_count_once_in_line_order(tmp_path):
    for keys in (["B", "A"], ["A", "B"]):
        found = _search(tmp_path, b"A then B\nB only", keys)
        assert _hits(found) == [(1, ("A", "B"), "A then B"), (2, ("B",), "B only")]
        assert sum(r["hit_count"] for r in found["results"]) == 2


# ------------------------------------------------------------------ 5 named codec against a file marker

def test_named_codec_conflicting_with_a_marker_is_recorded_not_a_confirmed_miss(tmp_path):
    bom = codecs.BOM_UTF8 + "재고표".encode()
    found = _search(tmp_path, bom, ["캡처", "재고표"], "latin-1")
    assert found["status"] == "partial" and _hits(found) == []
    assert [(c["requested"], c["marker"]) for c in found["conflicts"]] == [("latin-1", "bom")]
    assert (1, "재고표", "utf-8-sig") in _candidates(found)
    declared = b'<?xml version="1.0" encoding="cp949"?>\n' + "캡처".encode("cp949")
    found = _search(tmp_path, declared, ["캡처"], "utf-8")
    assert found["status"] == "partial" and found["conflicts"][0]["marker_encoding"] == "cp949"
    assert (2, "캡처", "cp949") in _candidates(found)
    # Aliases of the marker are not conflicts.
    for data, name in ((bom, "utf-8"), (bom, "UTF8"), (declared, "euc-kr"), (declared, "cp949")):
        same = _search(tmp_path, data, ["재고표", "캡처"], name)
        assert same["status"] == "complete" and same["conflicts"] == [] and len(_hits(same)) == 1, name
    assert _hits(_search(tmp_path, bom, ["재고표"], "utf-8"))[0][2] == "재고표"  # no BOM character in the quote


def test_report_shows_the_conflict(tmp_path):
    folder = tmp_path / "evidence"
    folder.mkdir()
    (folder / "stock.log").write_bytes(codecs.BOM_UTF8 + "재고표".encode())
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--keywords", "재고표", "--keyword-encoding", "latin-1",
                           "--no-docx", "--no-hwpx")
    assert code == 0, log
    md = (out / "보고서초안.md").read_text(encoding="utf-8")
    row = _keyword_row(md)
    assert "부분 완료" in row and "충돌 1개" in row and "운영자 지정 코덱: latin-1 (명령 인자)" in row
    assert "파일 표지(utf-8-sig BOM)와 다르다" in md


# ------------------------------------------------------------------ 6 codec names and the operator default

@pytest.mark.parametrize("name", ["base64_codec", "rot13", "unknown_shelf_codec"])
def test_unusable_codec_is_a_usage_error_before_any_output(tmp_path, name, monkeypatch, capsys):
    folder = tmp_path / "evidence"
    folder.mkdir()
    (folder / "a.log").write_bytes(b"x")
    out = tmp_path / "out"
    assert run_report(folder, "-o", out, "--keywords", "x", "--keyword-encoding", name)[0] == 2
    assert not out.exists()
    assert keyword_report.main([str(folder), "x", "--encoding", name]) == 2
    assert case_survey.main([str(folder), "--keywords", "x", "--keyword-encoding", name]) == 2
    monkeypatch.setenv(ENV, name)
    assert run_report(folder, "-o", out, "--keywords", "x")[0] == 2 and not out.exists()
    assert keyword_report.main([str(folder), "x"]) == 2
    assert ENV in capsys.readouterr().err


def test_operator_codec_from_the_environment_is_recorded_as_an_operator_choice(tmp_path, monkeypatch):
    folder = tmp_path / "evidence"
    folder.mkdir()
    (folder / "shelf.log").write_bytes("진열 완료".encode("cp949") + b"\n" + "정상".encode("cp949"))
    (folder / "chat.txt").write_bytes("진열 완료\n".encode())  # UTF-8 files still read as UTF-8
    monkeypatch.setenv(ENV, "cp949")
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--keywords", "진열 완료", "--no-docx", "--no-hwpx")
    assert code == 0, log
    survey = read_json(out / "선조사.json")
    step = survey["steps"]["keywords"]["result"]
    assert (step["status"], step["total_hits"], step["encoding_requested"], step["encoding_source"]) == \
        ("complete", 2, "cp949", "env")
    assert survey["processing_receipt"]["parameters"]["keyword_encoding_source"] == "env"
    note = f"운영자 지정 코덱: cp949 (환경설정 {ENV})"
    assert step["operator_codec"] == note and note in _keyword_row((out / "보고서초안.md").read_text(encoding="utf-8"))
    out2 = tmp_path / "out2"
    code, _ = run_report(folder, "-o", out2, "--keywords", "진열 완료", "--keyword-encoding", "euc-kr",
                         "--no-docx", "--no-hwpx")
    step = read_json(out2 / "선조사.json")["steps"]["keywords"]["result"]
    assert (step["encoding_requested"], step["encoding_source"]) == ("euc-kr", "cli")


def test_ambiguous_run_names_the_lines_codecs_and_rerun_command(tmp_path, capsys):
    folder = tmp_path / "evidence"
    folder.mkdir()
    (folder / "shelf.log").write_bytes("09:00 진열 완료".encode("cp949"))
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--keywords", "진열 완료", "--no-docx", "--no-hwpx")
    assert code == 0, log
    step = read_json(out / "선조사.json")["steps"]["keywords"]["result"]
    assert (step["files_encoding_uncertain"], step["candidate_hits"]) == (1, 1)
    assert step["plausible_codecs"][0] == "cp949" and "euc_jp" in step["plausible_codecs"]
    md = (out / "보고서초안.md").read_text(encoding="utf-8")
    # Outputs never carry the analysis PC's absolute path: the command names the evidence folder itself.
    command = keyword_report._command_line(["lazyforensic", "report", folder.name, "--keywords", "진열 완료",
                                            "--keyword-encoding", "cp949"])
    assert command in step["rerun_commands"] and f"`{command}`" in md
    assert "인코딩 불확실 파일 1개·후보 줄 1개(가능한 코덱 cp949" in md
    assert keyword_report.main([str(folder), "진열 완료"]) == 1
    err = capsys.readouterr().err
    assert "인코딩 불확실 파일 1개·후보 줄 1개" in err
    assert keyword_report._command_line(["lazyforensic", "kw", str(folder), "진열 완료", "--encoding", "cp949"]) in err


# ------------------------------------------------------------------ 7 view columns

def _view_db(path: Path) -> None:
    with contextlib.closing(sqlite3.connect(path)) as writer:
        writer.create_function("shelf_ok", 1, lambda n: int(n == 2))
        writer.executescript("CREATE TABLE SQLITEx(id); INSERT INTO SQLITEx VALUES (1),(2);"
                             "CREATE VIEW sqliteV AS SELECT id FROM SQLITEx WHERE shelf_ok(id);"
                             "CREATE VIEW emptyV AS SELECT id FROM SQLITEx WHERE 0;")
        writer.commit()


def test_unreadable_view_columns_are_unconfirmed_not_zero(tmp_path, capsys):
    db = tmp_path / "shelf.db"
    _view_db(db)
    rec = sqlite_survey.survey_db(db)
    by_name = {t["name"]: t for t in rec["tables"]}
    assert by_name["sqliteV"]["columns"] is None and "shelf_ok" in by_name["sqliteV"]["columns_error"]
    assert by_name["emptyV"]["columns"] == ["id"] and by_name["emptyV"]["row_count"] == 0
    assert by_name["SQLITEx"]["type"] == "table" and rec["total_rows"] == 2
    assert "sqliteV(no such function: shelf_ok)" in rec["columns_note"]
    sqlite_survey.main([str(db)])
    text = capsys.readouterr().out
    assert "sqliteV (view): 미확인(no such function: shelf_ok), 컬럼 미확인(no such function: shelf_ok)" in text
    assert "emptyV (view): 0행, 컬럼 1개" in text and "컬럼 0개" not in text


# ------------------------------------------------------------------ 8 FTS shadow tables

def _fts_db(path: Path) -> None:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        try:
            conn.execute("CREATE VIRTUAL TABLE shelf USING fts5(item)")
        except sqlite3.OperationalError:
            pytest.skip("SQLite built without FTS5")
        conn.executemany("INSERT INTO shelf VALUES (?)", [("red",), ("blue",)])
        conn.execute("CREATE TABLE SQLITEx(n)")
        conn.execute("INSERT INTO SQLITEx VALUES (1)")
        conn.commit()


def test_fts_shadow_rows_are_not_user_rows(tmp_path, capsys):
    db = tmp_path / "search.db"
    _fts_db(db)
    rec = sqlite_survey.survey_db(db)
    kinds = {t["name"]: t["type"] for t in rec["tables"]}
    assert kinds["shelf"] == "virtual" and kinds["SQLITEx"] == "table"
    assert sorted(n for n, k in kinds.items() if k == "shadow") == [
        "shelf_config", "shelf_content", "shelf_data", "shelf_docsize", "shelf_idx"]
    assert (rec["total_rows"], rec["virtual_rows"], rec["shadow_tables"]) == (1, 2, 5)
    assert rec["shadow_rows"] == sum(t["row_count"] for t in rec["tables"] if t["type"] == "shadow") > 2
    sqlite_survey.main([str(db)])
    line = capsys.readouterr().out.splitlines()[0]
    assert "테이블 1개 총 1행" in line and "가상 테이블 1개 조회 결과 2행" in line
    assert "관리용(shadow) 테이블 5개" in line


class _NoTableList:
    """A cursor of an SQLite older than 3.37: PRAGMA table_list returns nothing."""

    def __init__(self, cur):
        self.cur = cur

    def execute(self, sql, *args):
        if sql.strip().upper() == "PRAGMA TABLE_LIST":
            return self.cur.execute("SELECT 1 WHERE 0")
        return self.cur.execute(sql, *args)


def test_shadow_tables_are_found_without_table_list(tmp_path):
    db = tmp_path / "search.db"
    _fts_db(db)
    with contextlib.closing(sqlite3.connect(db)) as conn:
        modern = sqlite_survey.object_types(conn.cursor())
        legacy = sqlite_survey.object_types(_NoTableList(conn.cursor()))
    keep = {n: k for n, k in modern.items() if not n.startswith("sqlite_")}
    assert {n: k for n, k in legacy.items() if not n.startswith("sqlite_")} == keep


# ------------------------------------------------------------------ 9 digits beyond nine

def _exif_event(name: str, sub: str, offset: str | None = "+09:00") -> dict:
    tags = {"DateTimeOriginal": "2025:04:04 12:00:00", "SubSecTimeOriginal": sub}
    if offset is not None:
        tags["OffsetTimeOriginal"] = offset
    shot = exif_audit.capture_time(tags)
    return {"timestamp": shot["timestamp"], "source": "EXIF", "file": name, **exif_audit.event_fields(shot)}


def test_tenth_digit_orders_and_only_equal_records_tie():
    a, z = _exif_event("a.jpg", "0000002009"), _exif_event("z.jpg", "0000002001")
    assert a["instant_ns"] == z["instant_ns"] and a["subsec_beyond_ns"] == "9"
    ordered = merge_timeline.order_events([a, z])
    assert [e["file"] for e in ordered] == ["z.jpg", "a.jpg"] and not any(e.get("order_tie") for e in ordered)
    same = merge_timeline.order_events([_exif_event("b.jpg", "000000200"), _exif_event("c.jpg", "0000002000")])
    assert all(e.get("order_tie") for e in same)  # equal at the recorded precision
    # A nanosecond file time cannot separate the tenth digit: that pair is a tie at 1 ns.
    mtime = {"timestamp": "x", "source": "파일시스템(mtime)", "file": "m", "mtime_ns": a["instant_ns"]}
    assert all(e.get("order_tie") for e in merge_timeline.order_events([dict(a), mtime]))
    # Without an offset the order holds only under the KST assumption, which the event keeps.
    naive = _exif_event("n.jpg", "0000002005", None)
    order = merge_timeline.order_events([dict(a), naive, dict(z)])
    assert [e["file"] for e in order] == ["z.jpg", "n.jpg", "a.jpg"] and naive["timezone_assumed"] == "KST (정렬 전용)"


def test_report_orders_by_the_tenth_digit_and_says_so(tmp_path):
    image = pytest.importorskip("PIL.Image")
    folder = tmp_path / "photos"
    folder.mkdir()
    for name, sub in (("a.jpg", "0000002009"), ("z.jpg", "0000002001")):
        tags = image.Exif()
        tags[0x8769] = {0x9003: "2025:04:04 12:00:00", 0x9291: sub, 0x9011: "+09:00"}
        image.new("RGB", (4, 4)).save(folder / name, exif=tags)
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--no-docx", "--no-hwpx")
    assert code == 0, log
    exif = [e for e in read_json(out / "events.json") if e["source"] == "EXIF"]
    assert [(e["file"], e.get("order_tie")) for e in exif] == [("z.jpg", None), ("a.jpg", None)]
    md = (out / "보고서초안.md").read_text(encoding="utf-8")
    assert md.index("12:00:00.0000002001+09:00") < md.index("12:00:00.0000002009+09:00")
    assert "기록된 자릿수 전체로 선후를 비교했다" in md and "장비 시계의 정확도를 뜻하지 않는다" in md


# ------------------------------------------------------------------ 11 recorded digits in the card and CLI

def test_timeline_card_and_exif_command_show_the_recorded_digits(tmp_path, capsys):
    image = pytest.importorskip("PIL.Image")
    folder = tmp_path / "photos"
    folder.mkdir()
    for name, sub, offset in (("blue.jpg", "172839401", "-03:30"), ("green.jpg", "172839409", None)):
        tags = image.Exif()
        ifd = {0x9003: "2025:04:04 12:15:00", 0x9291: sub}
        if offset:
            ifd[0x9011] = offset
        tags[0x8769] = ifd
        image.new("RGB", (4, 4)).save(folder / name, exif=tags)
        os.utime(folder / name, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))  # unrelated mtime
    assert exif_audit.main([str(folder)]) == 0
    shown = capsys.readouterr().out
    assert "촬영 2025-04-04 12:15:00.172839401-03:30" in shown
    assert "촬영 2025-04-04 12:15:00.172839409 (시간대 미기록, 정렬만 KST 가정)" in shown
    out = tmp_path / "out"
    code, log = run_report(folder, "-o", out, "--no-docx", "--no-hwpx")
    assert code == 0, log
    page = (out / "타임라인.html").read_text(encoding="utf-8")
    cards = {name: card for card in page.split('<div class="timeline-item')[1:]
             for name in ("blue.jpg", "green.jpg") if "카메라 기록값" in card and name in card}
    assert "2025-04-04 12:15:00.172839401-03:30" in cards["blue.jpg"]
    assert "2025-04-04 12:15:00.172839409 (시간대 미기록, 정렬만 KST 가정)" in cards["green.jpg"]
    events = [{"timestamp": "2025-04-04T12:15:00.172839+09:00", "description": "plain", "category": "system"}]
    assert "2025-04-04T12:15:00.172839+09:00" in generate_timeline.generate_html(events)  # non-EXIF unchanged
