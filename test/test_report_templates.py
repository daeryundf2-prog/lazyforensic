"""Blank-form rendering and independent on-disk verification, with synthetic inputs."""
import hashlib
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import report_docx
import report_draft
import report_templates as forms

W = report_docx.W


def package(path):
    with ZipFile(path) as doc:
        return {part: doc.read(part) for part in doc.namelist()}


def write_parts(path, parts):
    with ZipFile(path, "w") as doc:
        for part, data in parts.items():
            doc.writestr(part, data)


def make_form(path, md):
    report_docx.write_docx(path, md)
    return path


def test_tokens_across_runs_and_story_parts(tmp_path):
    source = make_form(tmp_path / "blank.docx", "앞 {{사건번호}} 뒤 {{사건명}}{{알수없음}}\n"
                       "| 항목 | 내용 |\n| --- | --- |\n| 사건 | {{사건명}} |")
    parts = package(source)
    doc = ET.fromstring(parts["word/document.xml"])
    p = doc.find(".//" + W + "p")
    p.find(".//" + W + "t").text = "앞 {"
    for chunk in ("{사건", "번호}} 뒤 {{사", "건명}}{{알수없음}}"):
        run = report_docx.word(p, "r")
        report_docx.word(report_docx.word(run, "rPr"), "i")
        report_docx.word(run, "t").text = chunk
    parts["word/document.xml"] = ET.tostring(doc, encoding="utf-8")
    header = ET.Element(W + "hdr")
    report_docx.paragraph(header, "머리말 {{사건번호}}")
    parts["word/header1.xml"] = ET.tostring(header, encoding="utf-8")
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>", b'<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-'
        b'officedocument.wordprocessingml.header+xml"/></Types>')
    parts["word/media/image1.bin"] = b"synthetic resource"
    write_parts(source, parts)
    draft = tmp_path / forms.OUTPUT_NAME
    forms.write_form(source, draft, {"사건번호": "합성-01", "사건명": "시료 | <검토> & 기록"})
    assert report_draft.run_verify(draft, [])["verdict"] == "PASS"
    filled = package(draft)
    p = ET.fromstring(filled["word/document.xml"]).find(".//" + W + "p")
    assert [t.text for t in p.iter(W + "t")] == ["앞 합성-01", None, " 뒤 시료 | <검토> & 기록", "미확인"]
    assert len(list(p.iter(W + "i"))) == 3
    assert "머리말 합성-01" in filled["word/header1.xml"].decode()
    assert filled["word/media/image1.bin"] == parts["word/media/image1.bin"]
    assert package(source) == parts
    filled["word/header1.xml"] = filled["word/header1.xml"].replace("합성-01".encode(), "명백히 입증".encode())
    write_parts(draft, filled)
    result = report_draft.run_verify(draft, [])
    assert result["verdict"] == "FAIL" and result["forbidden_hits"] == ["명백히 입증"]


def test_cli_table_values_and_order(tmp_path):
    ev = tmp_path / "증거"
    ev.mkdir()
    data = b"synthetic"
    (ev / "한글 자료.bin").write_bytes(data)
    case = tmp_path / "case.txt"
    case.write_text("사건번호: 합성-02\n", encoding="utf-8")
    source = make_form(tmp_path / "blank.docx", "# 감정서\n{{사건번호}} / {{사건명}}\n{{타임라인표}}\n"
                       "{{증거목록표}}\n{{무결성검증표}}\n{{분석관의견}}")
    out = tmp_path / "결과"
    assert report_draft.main([str(ev), "--case-info", str(case), "--template", str(source), "-o", str(out)]) == 0
    draft = out / forms.OUTPUT_NAME
    assert "합성-02 / 미확인" in report_docx.read_docx(draft)
    assert report_docx.read_docx(draft).endswith("미확인")
    verify = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    assert verify["template"]["verdict"] == verify["docx"]["verdict"] == "PASS"
    tables = ET.fromstring(package(draft)["word/document.xml"]).findall(".//" + W + "tbl")
    timeline, inventory, integrity = [
        [[tc.find(".//" + W + "t").text for tc in row.findall(W + "tc")] for row in table.findall(W + "tr")]
        for table in tables]
    event, = json.loads((out / "events.json").read_text(encoding="utf-8"))
    assert timeline[1:] == [[event[k] for k in ("timestamp", "source", "file", "description")]]
    digest = f"MD5 {hashlib.md5(data).hexdigest()} / SHA-1 {hashlib.sha1(data).hexdigest()} / SHA-256 {hashlib.sha256(data).hexdigest()}"
    assert inventory == [["파일", "크기(바이트)", "해시"], ["한글 자료.bin", "9", digest]]
    assert integrity[0] == ["검증 항목", "결과", "근거"]
    assert integrity[1][:2] == ["분석 전후 재해시", "일치"]
    assert ["참조 해시 대조", "미확인"] == integrity[2][:2]
    assert (ev / "한글 자료.bin").read_bytes() == data


@pytest.mark.parametrize(("actual", "error"), [
    ("합성-99", "SHA-256 불일치"), ("{{남음}}", "자리표시자"), ("SHA-256 " + "a" * 64, "근거 없는 해시"),
    ("명백히 입증", "금지 문구"),
])
def test_gate_catches_writer_corruption(tmp_path, monkeypatch, actual, error):
    ev = tmp_path / "증거"
    ev.mkdir()
    (ev / "시료.txt").write_bytes(b"synthetic")
    out = tmp_path / "결과"
    form = make_form(tmp_path / "blank.docx", "감정서\n{{분석관의견}}")
    writer = forms.fill_story

    def altered(data, fields):
        return writer(data, fields).replace("미확인".encode(), actual.encode())

    monkeypatch.setattr(forms, "fill_story", altered)
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(form)]) == 1
    result = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    assert result["verdict"] == result["template"]["verdict"] == "FAIL"
    assert any(error in message for message in result["template"]["errors"])


def test_empty_tables_missing_reference_and_input_collision(tmp_path):
    ev = tmp_path / "증거"
    ev.mkdir()
    form = make_form(tmp_path / "blank.docx", "{{증거목록표}}\n{{타임라인표}}")
    out = tmp_path / "결과"
    assert report_draft.main([str(ev), "-o", str(out), "--no-docx", "--template", str(form)]) == 0
    draft = out / forms.OUTPUT_NAME
    assert report_docx.read_docx(draft) == "미확인\n미확인"
    draft.with_suffix(forms.DIGEST_SUFFIX).unlink()
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"
    before = draft.read_bytes()
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(draft)]) == 2
    assert draft.read_bytes() == before


def test_unsupported_form_and_mixed_table_marker(tmp_path):
    ev = tmp_path / "증거"
    ev.mkdir()
    (ev / "a.txt").write_bytes(b"sample")
    form = make_form(tmp_path / "form.docx", "목록 {{증거목록표}}")
    out = tmp_path / "결과"
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(form.with_suffix(".hwpx"))]) == 2
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(form)]) == 1
    assert not (out / forms.OUTPUT_NAME).exists()
    assert "단독" in (out / "초안검증.json").read_text(encoding="utf-8")


def test_missing_table_row_fails_even_if_remaining_digests_are_grounded(tmp_path):
    form = make_form(tmp_path / "blank.docx", "{{증거목록표}}")
    draft = tmp_path / forms.OUTPUT_NAME
    files = [{"path": name, "size": 1, "sha256": hashlib.sha256(name.encode()).hexdigest()}
             for name in ("a.bin", "b.bin")]
    evidence = tmp_path / "integrity.json"
    evidence.write_text(json.dumps({"files": files}), encoding="utf-8")
    fields = forms.form_fields({}, files, [], [], 100)
    forms.write_form(form, draft, fields)
    assert report_draft.run_verify(draft, [evidence])["verdict"] == "PASS"
    parts = package(draft)
    doc = ET.fromstring(parts["word/document.xml"])
    table = doc.find(".//" + W + "tbl")
    table.remove(table.findall(W + "tr")[1])
    parts["word/document.xml"] = ET.tostring(doc, encoding="utf-8")
    write_parts(draft, parts)
    result = report_draft.run_verify(draft, [evidence])
    assert result["verdict"] == "FAIL"
    assert any("SHA-256 불일치" in message for message in result["errors"])


def test_static_forbidden_claim_in_blank_form(tmp_path):
    form = make_form(tmp_path / "blank.docx", "감정서\n명백히 입증\n{{사건번호}}")
    draft = tmp_path / forms.OUTPUT_NAME
    forms.write_form(form, draft, {})
    result = report_draft.run_verify(draft, [])
    assert result["verdict"] == "FAIL" and result["forbidden_hits"] == ["명백히 입증"]
    assert not any("SHA-256 불일치" in message for message in result["errors"])
