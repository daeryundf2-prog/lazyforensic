"""Report scope, enumeration, forms and integrity statements (D11 review items). All inputs are synthetic."""
import csv
import gzip
import hashlib
import importlib.util
import json
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import case_survey
import court_evidence_sheet
import evidence_manifest
import merge_timeline
import report_draft

TEMPLATES = ROOT / "templates"
EWF = ROOT / "test/fixtures/ewf"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def report_text(out):
    return (out / "보고서초안.md").read_text(encoding="utf-8")


def tree_digests(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def make_link(link, target):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")


def deny_listing(monkeypatch, folder):
    """Deterministic access denial for one folder on every OS (no chmod or ACL needed)."""
    # Compare resolved paths: the report resolves its root (macOS /var → /private/var, Windows 8.3 names).
    denied, real_scandir = folder.resolve(), os.scandir

    def scandir(path="."):
        if isinstance(path, (str, os.PathLike)) and Path(path).resolve() == denied:
            raise PermissionError(13, "Permission denied", str(denied))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)


# 1 -------------------------------------------------------------------------------------------


def test_second_case_into_used_output_gets_its_own_bundle(tmp_path):
    case_a, case_b, out = tmp_path / "사건A", tmp_path / "사건B", tmp_path / "결과"
    case_a.mkdir()
    case_b.mkdir()
    (case_a / "only-A.txt").write_bytes(b"A")
    (case_b / "only-B.txt").write_bytes(b"B")
    info = tmp_path / "a.json"
    info.write_text('{"case_number": "SYN-A"}', encoding="utf-8")
    assert report_draft.main([str(case_a), "-o", str(out), "--case-info", str(info),
                              "--template", str(TEMPLATES / "report_form.hwpx")]) == 0
    a_files = tree_digests(out)
    assert "양식초안.hwpx" in a_files and "산출물목록.json" in a_files

    assert report_draft.main([str(case_b), "-o", str(out)]) == 0
    # Case A's bundle, including its form and receipts, is untouched.
    assert tree_digests(out) == a_files
    b_out = tmp_path / "결과_2"
    b_files = tree_digests(b_out)
    assert not any(name.startswith("양식초안") for name in b_files)
    assert [f["path"] for f in read_json(b_out / "무결성검증.json")["files"]] == ["only-B.txt"]
    listing = read_json(b_out / "산출물목록.json")
    # The run listing names exactly the files this run created, with their digests.
    assert {f["path"]: f["sha256"] for f in listing["files"]} == {
        name: digest for name, digest in b_files.items() if name != "산출물목록.json"}
    assert listing["evidence_root"] == "사건B"


# 2 -------------------------------------------------------------------------------------------


def test_unlistable_folder_makes_scope_partial(tmp_path, monkeypatch):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    (ev / "closed").mkdir(parents=True)
    (ev / "closed" / "present.txt").write_bytes(b"sample")
    (ev / "open.txt").write_bytes(b"other")
    deny_listing(monkeypatch, ev / "closed")
    assert report_draft.main([str(ev), "-o", str(out)]) == 1
    integrity = read_json(out / "무결성검증.json")
    assert integrity["scope"] == {
        "status": "partial", "files_found": 1, "total_files": None,
        "unlisted_folders": [{"path": "closed", "error": "Permission denied"}], "excluded": []}
    assert integrity["post_check"]["status"] == "incomplete"
    assert integrity["post_check"]["unlisted_folders"] == ["closed"]
    text = report_text(out)
    assert "확인 1개" in text and "열거 실패 폴더 1개" in text and "전체 파일 수 미확인" in text
    assert "| 조사 범위(폴더 열거) | 부분 완료 |" in text
    assert "모든 파일" not in text
    survey = read_json(out / "선조사.json")
    assert survey["processing_receipt"]["status"] == "partial"


def test_manifest_records_unlistable_folder(tmp_path, monkeypatch):
    ev = tmp_path / "증거"
    (ev / "closed").mkdir(parents=True)
    (ev / "open.txt").write_bytes(b"other")
    deny_listing(monkeypatch, ev / "closed")
    manifest = evidence_manifest.build_manifest(ev)
    assert manifest["complete"] is False and manifest["file_count"] == 1
    assert manifest["enumeration_errors"] == [{"path": "closed", "error": "Permission denied"}]
    assert evidence_manifest.main([str(ev), "-o", str(tmp_path / "m.json")]) == 1


# 3 -------------------------------------------------------------------------------------------


def test_hash_list_and_survey_share_one_link_policy(tmp_path):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    (ev / "one.txt").write_text("표식", encoding="utf-8")
    make_link(ev / "alias.txt", "one.txt")
    assert report_draft.main([str(ev), "-o", str(out), "--keywords", "표식"]) == 0
    integrity = read_json(out / "무결성검증.json")
    assert [f["path"] for f in integrity["files"]] == ["one.txt"]
    assert integrity["post_check"]["checked"] == 1
    excluded = [{"path": "alias.txt", "reason": evidence_manifest.LINK_REASON}]
    assert integrity["scope"]["excluded"] == excluded
    survey = read_json(out / "선조사.json")
    assert survey["scope"]["excluded"] == excluded
    keywords = survey["steps"]["keywords"]["result"]
    assert (keywords["files_searched"], keywords["total_hits"]) == (1, 1)
    assert [Path(r["file"]).name for r in keywords["results"]] == ["one.txt"]
    assert survey["steps"]["signatures"]["result"]["total"] == 1
    assert "제외한 항목 1개" in report_text(out) and "`alias.txt`" in report_text(out)


# 14 ------------------------------------------------------------------------------------------


def test_analysis_outputs_are_refused_inside_the_evidence_tree(tmp_path):
    ev = tmp_path / "증거"
    ev.mkdir()
    (ev / "one.txt").write_bytes(b"one")
    inside = ev / "manifest.json"
    assert evidence_manifest.main([str(ev), "-o", str(inside)]) == 2
    assert case_survey.main([str(ev), "-o", str(ev / "sub" / "survey.json")]) == 2
    assert case_survey.main([str(ev), "--markdown", str(ev / "survey.md")]) == 2
    assert sorted(p.name for p in ev.iterdir()) == ["one.txt"]
    sibling = tmp_path / "manifest.json"
    assert evidence_manifest.main([str(ev), "-o", str(sibling)]) == 0
    assert evidence_manifest.main([str(ev), "--verify", str(sibling)]) == 0


# 8 -------------------------------------------------------------------------------------------


def test_reference_rows_and_path_attribution_are_accounted(tmp_path):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    (ev / "copy").mkdir(parents=True)
    (ev / "copy" / "sample.bin").write_bytes(b"abc")
    digest = hashlib.sha256(b"abc").hexdigest()
    ref = tmp_path / "list.csv"
    ref.write_text(f"path,sha256\noriginal/sample.bin,{digest}\nother.bin,unavailable\n", encoding="utf-8")
    assert report_draft.main([str(ev), "-o", str(out), "--hashes", str(ref)]) == 0
    reference = read_json(out / "무결성검증.json")["reference"]
    source, = reference["sources"]
    assert source["sha256"] == hashlib.sha256(ref.read_bytes()).hexdigest()
    assert (source["rows"], source["accepted_rows"], source["rejected_rows"]) == (2, 1, 1)
    assert source["rejected"] == [{"line": 3, "reason": "sha256 값이 해시 형식이 아님"}]
    entry, = reference["entries"]
    assert (entry["result"], entry["file"], entry["path_differs"]) == ("match", "copy/sample.bin", True)
    text = report_text(out)
    assert "| 참조 해시 대조 | 일부 일치·나머지 미확인 |" in text
    assert "list.csv 3행: sha256 값이 해시 형식이 아님" in text
    row = next(line for line in text.splitlines() if line.startswith("| original/sample.bin |"))
    assert "해시 일치·경로 미확인" in row and "copy/sample.bin" in row
    assert read_json(out / "초안검증.json")["verdict"] == "PASS"


def test_reference_with_matching_path_is_attributed(tmp_path):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    (ev / "copy").mkdir(parents=True)
    (ev / "copy" / "sample.bin").write_bytes(b"abc")
    ref = tmp_path / "list.txt"
    ref.write_text(hashlib.sha256(b"abc").hexdigest() + "  copy/sample.bin\n", encoding="utf-8")
    assert report_draft.main([str(ev), "-o", str(out), "--hashes", str(ref)]) == 0
    entry, = read_json(out / "무결성검증.json")["reference"]["entries"]
    assert entry["path_differs"] is False
    assert "| 참조 해시 대조 | 일치 |" in report_text(out)


# 9 -------------------------------------------------------------------------------------------


def test_methods_section_reports_unperformed_ewf_verification(tmp_path):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    (ev / "split.E02").write_bytes(gzip.decompress((EWF / "split.E02.gz").read_bytes()))
    assert report_draft.main([str(ev), "-o", str(out)]) == 1
    text = report_text(out)
    method = next(line for line in text.splitlines() if "EWF 이미지 `split.E02`" in line)
    assert "수행하지 않았다(미수행" in method and "대조했다" not in method
    assert "미디어 전체 해시를 다시 계산해" not in text
    row = next(line for line in text.splitlines() if line.startswith("| EWF 저장 해시 대조 (split.E02) |"))
    assert "미수행" in row
    assert "| split.E02 | - | 기록 없음 | 미수행 |" in text


def test_methods_section_lists_only_compared_algorithms(tmp_path):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    (ev / "synthetic.E01").write_bytes((EWF / "synthetic.E01").read_bytes())
    assert report_draft.main([str(ev), "-o", str(out)]) == 0
    compared = read_json(out / "무결성검증.json")["images"][0]["comparison"]
    assert sorted(compared) == ["md5", "sha1"]
    method = next(line for line in report_text(out).splitlines() if "EWF 이미지 `synthetic.E01`" in line)
    assert "저장된 MD5·SHA-1 값과 대조했다" in method and "SHA-256 값과" not in method


# 10 ------------------------------------------------------------------------------------------


def test_unparseable_candidates_are_counted_separately(tmp_path):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    (ev / "empty.zip").write_bytes(b"")
    (ev / "empty.db").write_bytes(b"")
    assert report_draft.main([str(ev), "-o", str(out)]) == 0
    survey = read_json(out / "선조사.json")
    archives = survey["steps"]["archives"]["result"]
    sqlite = survey["steps"]["sqlite"]["result"]
    assert (archives["archives"], archives["unreadable"]) == (1, 1)
    assert (sqlite["db_files"], sqlite["unreadable"], sqlite["broken"]) == (1, 1, 0)
    assert "integrity" not in sqlite["records"][0]
    warnings = survey["processing_receipt"]["warnings"]
    assert "Incomplete stage: archives" in warnings and "Incomplete stage: sqlite" in warnings
    text = report_text(out)
    assert "| 압축 파일 내부 | 후보 1개(확장자 기준): 판독 0개, 판독 불가 1개, 주의 표시 멤버 0개 |" in text
    assert "| SQLite 데이터베이스 | 후보 1개: 구조 판독 0개, 판독 불가 1개(무결성 검사 미수행), 무결성 이상 0개 |" in text


# 11 ------------------------------------------------------------------------------------------


class Rows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cell = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self.cell = ""

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.rows[-1].append(self.cell)
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data


def test_html_cells_show_filenames_literally(tmp_path):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    (ev / "한`판`.bin").write_bytes(b"abc")
    (ev / "한판.bin").write_bytes(b"xyz")
    assert report_draft.main([str(ev), "-o", str(out)]) == 0
    with (out / "파일해시목록.csv").open(encoding="utf-8-sig", newline="") as stream:
        expected = {r["경로"]: r["SHA-256"] for r in csv.DictReader(stream)}
    assert len(expected) == 2
    parser = Rows()
    parser.feed((out / "보고서초안.html").read_text(encoding="utf-8"))
    # The hash cell lists MD5 / SHA-1 / SHA-256; the SHA-256 must be the CSV value of the same file.
    shown = {r[1]: r[3].rsplit("SHA-256 ", 1)[-1] for r in parser.rows if len(r) >= 4 and r[1] in expected}
    assert shown == expected
    assert "<code>판</code>" not in (out / "보고서초안.html").read_text(encoding="utf-8")


# 12 ------------------------------------------------------------------------------------------


def test_evidence_sheet_prefers_explicit_path_purpose(tmp_path, capsys):
    ev = tmp_path / "증거"
    for sub in ("one", "two"):
        (ev / sub).mkdir(parents=True)
        (ev / sub / "sample.bin").write_bytes(sub.encode())
    manifest = tmp_path / "manifest.json"
    assert evidence_manifest.main([str(ev), "-o", str(manifest)]) == 0
    capsys.readouterr()
    assert court_evidence_sheet.main([str(manifest), "--purpose", "sample.bin=공통",
                                      "--purpose", "two/sample.bin=별도"]) == 0
    rows = {line.split(" | ")[2]: line.rstrip(" |").rsplit(" | ", 1)[1]
            for line in capsys.readouterr().out.splitlines() if "sample.bin" in line and line.startswith("| 갑")}
    assert rows == {"one/sample.bin": "공통", "two/sample.bin": "별도"}


def test_evidence_sheet_flags_name_purpose_shared_by_same_name_files():
    files = [{"path": "one/sample.bin"}, {"path": "two/sample.bin"}]
    rows = court_evidence_sheet.build_rows(files, "갑", 1, {"sample.bin": "공통"})
    assert [r["purpose"] for r in rows] == ["공통", "공통"]
    note, = court_evidence_sheet.purpose_notes(rows, {"sample.bin": "공통"})
    assert "동명 파일 2건" in note and "one/sample.bin" in note and "two/sample.bin" in note


# 13 ------------------------------------------------------------------------------------------


def test_plaso_rows_are_accepted_or_rejected_with_reasons(tmp_path):
    src = tmp_path / "events.jsonl"
    src.write_text('{"timestamp":0,"message":"first"}\n{"message":"undated"}\n{"timestamp":', encoding="utf-8")
    summary = tmp_path / "summary.json"
    assert merge_timeline.main(["--plaso", str(src), "--report", str(summary), "-o", str(tmp_path / "e.json")]) == 0
    stats, = read_json(summary)["inputs"]
    assert (stats["records"], stats["accepted"]) == (3, 1)
    assert stats["rejected"] == [{"line": 2, "reason": "시각 없음 또는 해석 불가"},
                                 {"line": 3, "reason": "JSON 해석 실패(미완성·손상된 줄)"}]


@pytest.mark.parametrize(("available", "truncated"), [(2, False), (3, True)])
def test_record_cap_is_disclosed(tmp_path, capsys, available, truncated):
    spec = importlib.util.spec_from_file_location("parse_ntfs_artifacts", ROOT / "scripts/parse_ntfs_artifacts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    records = [SimpleNamespace(event_id=i) for i in range(available)]
    fake = ModuleType("dissect.target")
    fake.Target = SimpleNamespace(open=lambda path: SimpleNamespace(eventlogs=lambda: iter(records)))
    with patch.dict(sys.modules, {"dissect": ModuleType("dissect"), "dissect.target": fake}):
        assert module.main([str(tmp_path), "-a", "eventlogs", "--limit", "2"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["count"], data["limit"], data["truncated"], data["error"]) == (2, 2, truncated, None)
