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
    # File events are shown with the exact nanosecond mtime recorded in events.json.
    assert timeline[1:] == [[event[k] for k in ("mtime", "source", "file", "description")]]
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
    # A previous run's folder is never reused, so the filled draft cannot be overwritten by the next run.
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(draft)]) == 0
    assert draft.read_bytes() == before
    assert (tmp_path / "결과_2" / forms.OUTPUT_NAME).is_file()


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


def test_catalogue_and_sample_cover_every_field(tmp_path):
    sample = ROOT / "templates/report_form.docx"
    catalogue = (ROOT / "docs/REPORT_TEMPLATES.md").read_text(encoding="utf-8")
    case = {label: f"합성 {label}" for _, label, _ in report_draft.CASE_FIELDS}
    fields = forms.form_fields(case, [], [], [], 100)
    assert set(forms.TOKEN.findall(catalogue)) == set(forms.TOKEN.findall(report_docx.read_docx(sample))) == set(fields)
    draft = tmp_path / forms.OUTPUT_NAME
    forms.write_form(sample, draft, fields)
    text = report_docx.read_docx(draft)
    assert all(value in text for value in case.values())
    assert report_draft.run_verify(draft, [])["verdict"] == "PASS"


@pytest.mark.parametrize("unresolved", ["not_found", "not_verified", "ambiguous"])
def test_form_integrity_result_accounts_for_every_reference(unresolved):
    rows = report_draft.integrity_rows({"status": "match", "checked": 1}, [],
                                       [{"result": "match"}, {"result": unresolved}])
    fields = forms.form_fields({}, [], rows, [], 100)
    reference, = [r for r in fields["무결성검증표"] if r[0] == "참조 해시 대조"]
    assert reference[1] == "일부 일치·나머지 미확인"
    assert "참조 2건: 일치 1" in reference[2]


def test_integrity_describes_only_the_two_measurements(tmp_path):
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "sample.bin").write_bytes(b"test")
    out = tmp_path / "out"
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(ROOT / "templates/report_form.docx")]) == 0
    for path in (out / "보고서초안.docx", out / forms.OUTPUT_NAME):
        text = report_docx.read_docx(path)
        assert "분석 전후 SHA-256 1개" in text
        assert "증거가 바뀌지 않았다" not in text


def test_empty_summary_has_no_rehash_match():
    ctx = {"inventory": [], "images": [], "references": None, "survey": {}, "root_name": "empty",
           "post": {"status": "match", "checked": 0}}
    assert any("대조할 파일이 없음" in fact for fact in report_draft.summary_facts(ctx))


@pytest.mark.parametrize(("kind", "root_name"), [("header", "hdr"), ("footer", "ftr"),
                                                ("footnotes", "footnotes"), ("endnotes", "endnotes"),
                                                ("comments", "comments")])
def test_default_story_content_types_are_filled_and_checked(tmp_path, kind, root_name):
    source = make_form(tmp_path / "blank.docx", "Form")
    parts = package(source)
    name = "word/notes.story"
    mime = f"application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>", f'<Default Extension="story" ContentType="{mime}"/></Types>'.encode())
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    parts["word/_rels/document.xml.rels"] = (
        f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="r1" Type="{rel}{kind}" Target="notes.story"/></Relationships>').encode()
    root = ET.Element(W + root_name)
    if kind in ("header", "footer"):
        doc = ET.fromstring(parts["word/document.xml"])
        ref = report_docx.word(doc.find(".//" + W + "sectPr"), kind + "Reference", type="default")
        ref.set("{" + rel.rstrip("/") + "}id", "r1")
        parts["word/document.xml"] = ET.tostring(doc)
    para_parent = root if kind in ("header", "footer") else report_docx.word(root, kind[:-1], id=1)
    report_docx.paragraph(para_parent, "{{사건번호}} / {{사건번호}}")
    parts[name] = ET.tostring(root, encoding="utf-8")
    write_parts(source, parts)
    draft = tmp_path / forms.OUTPUT_NAME
    forms.write_form(source, draft, {"사건번호": "A-01"})
    filled = package(draft)
    assert "A-01 / A-01" in filled[name].decode()
    filled[name] = filled[name].replace(b"A-01", b"{{review}}")
    write_parts(draft, filled)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"
    renamed = draft.rename(tmp_path / "report.docx")
    assert report_draft.run_verify(renamed, [])["verdict"] == "FAIL"


@pytest.mark.parametrize("feature", ["simple_field", "complex_field", "hidden", "alt_text", "deleted_text",
                                     "data_binding", "nested_text_box"])
def test_gate_checks_content_outside_plain_runs(tmp_path, feature):
    draft = tmp_path / forms.OUTPUT_NAME
    forms.write_form(make_form(tmp_path / "blank.docx", "Sample"), draft, {})
    parts = package(draft)
    doc = ET.fromstring(parts["word/document.xml"])
    p = doc.find(".//" + W + "p")
    run = p.find(W + "r")
    if feature == "simple_field":
        field = report_docx.word(p, "fldSimple", instr="DATE")
        p.remove(run)
        field.append(run)
    elif feature == "complex_field":
        field = report_docx.word(p, "r")
        report_docx.word(field, "fldChar", fldCharType="begin")
        report_docx.word(field, "instrText").text = " DATE "
        report_docx.word(field, "fldChar", fldCharType="separate")
        report_docx.word(field, "fldChar", fldCharType="end")
    elif feature == "hidden":
        report_docx.word(run.find(W + "rPr"), "vanish")
    elif feature == "alt_text":
        inline = ET.SubElement(report_docx.word(run, "drawing"),
                               "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline")
        ET.SubElement(inline, inline.tag.replace("inline", "docPr"), id="1", name="figure", descr="{{review}}")
    elif feature == "deleted_text":
        report_docx.word(report_docx.word(report_docx.word(p, "del"), "r"), "delText").text = "{{review}}"
    elif feature == "data_binding":
        sdt = report_docx.word(p, "sdt")
        report_docx.word(report_docx.word(sdt, "sdtPr"), "dataBinding", xpath="/sample")
        p.remove(run)
        report_docx.word(sdt, "sdtContent").append(run)
    else:
        # The nested paragraph would merge with its host in the flat reader.
        inner = report_docx.word(report_docx.word(run, "txbxContent"), "p")
        report_docx.word(inner, "r")
    parts["word/document.xml"] = ET.tostring(doc, encoding="utf-8")
    write_parts(draft, parts)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


def test_content_control_cell_is_in_verified_table(tmp_path):
    source = make_form(tmp_path / "blank.docx", "| A | B |\n| --- | --- |\n| first | {{사건번호}} |")
    parts = package(source)
    doc = ET.fromstring(parts["word/document.xml"])
    tr = doc.findall(".//" + W + "tr")[-1]
    cell = tr.findall(W + "tc")[-1]
    tr.remove(cell)
    control = report_docx.word(tr, "sdt")
    report_docx.word(control, "sdtContent").append(cell)
    parts["word/document.xml"] = ET.tostring(doc)
    write_parts(source, parts)
    draft = tmp_path / forms.OUTPUT_NAME
    forms.write_form(source, draft, {"사건번호": "A-01"})
    errors = []
    assert "| first | A-01 |" in forms.check_form(draft, forms.digest_path(draft), errors)
    assert not errors


@pytest.mark.parametrize("damage", ["missing_relationship", "missing_part", "wrong_type"])
def test_header_reference_must_resolve_to_a_header(tmp_path, damage):
    draft = make_form(tmp_path / "draft.docx", "Sample")
    parts = package(draft)
    doc = ET.fromstring(parts["word/document.xml"])
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ref = report_docx.word(doc.find(".//" + W + "sectPr"), "headerReference", type="default")
    ref.set("{" + rel + "}id", "r1")
    parts["word/document.xml"] = ET.tostring(doc)
    if damage != "missing_relationship":
        parts["word/_rels/document.xml.rels"] = (
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="r1" Type="{rel}/header" Target="header.xml"/></Relationships>').encode()
    if damage == "wrong_type":
        parts["word/header.xml"] = ET.tostring(ET.Element(W + "hdr"))
    write_parts(draft, parts)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


def test_hidden_character_style_cannot_hide_a_measured_value(tmp_path):
    draft = tmp_path / forms.OUTPUT_NAME
    forms.write_form(make_form(tmp_path / "blank.docx", "A-01"), draft, {})
    parts = package(draft)
    styles = ET.Element(W + "styles")
    style = report_docx.word(styles, "style", type="character", styleId="base")
    report_docx.word(report_docx.word(style, "rPr"), "vanish")
    style = report_docx.word(styles, "style", type="character", styleId="sample")
    report_docx.word(style, "basedOn", val="base")
    parts["word/styles.xml"] = ET.tostring(styles)
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>", b'<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
        b'officedocument.wordprocessingml.styles+xml"/></Types>')
    doc = ET.fromstring(parts["word/document.xml"])
    report_docx.word(doc.find(".//" + W + "rPr"), "rStyle", val="sample")
    parts["word/document.xml"] = ET.tostring(doc)
    write_parts(draft, parts)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


def test_malformed_content_type_is_a_verification_failure(tmp_path):
    draft = make_form(tmp_path / "draft.docx", "Sample")
    parts = package(draft)
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(b"</Types>", b"<Override/></Types>")
    write_parts(draft, parts)
    result = report_draft.run_verify(draft, [])
    assert result["verdict"] == "FAIL"


def test_docx_form_digest_keeps_cell_paragraph_boundaries(tmp_path):
    source = make_form(tmp_path / "blank.docx", "| A | B |\n| --- | --- |\n| first second | x |")
    draft = tmp_path / forms.OUTPUT_NAME
    forms.write_form(source, draft, {})
    parts = package(draft)
    doc = ET.fromstring(parts["word/document.xml"])
    tc = doc.findall(".//" + W + "tr")[-1].find(W + "tc")
    tc.find(".//" + W + "t").text = "first"
    report_docx.paragraph(tc, "second")
    parts["word/document.xml"] = ET.tostring(doc)
    write_parts(draft, parts)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


def test_table_row_cannot_be_replaced_by_literal_text(tmp_path):
    source = make_form(tmp_path / "blank.docx", "{{증거목록표}}")
    draft = tmp_path / forms.OUTPUT_NAME
    matrix = [["Name", "Bytes"], ["item.bin", "0"]]
    forms.write_form(source, draft, {"증거목록표": matrix})
    parts = package(draft)
    doc = ET.fromstring(parts["word/document.xml"])
    body = doc.find(W + "body")
    body.remove(body.find(W + "tbl"))
    for index, row in enumerate(matrix):
        holder = ET.Element("holder")
        report_docx.paragraph(holder, report_docx.row_text(row))
        body.insert(index, holder[0])
    parts["word/document.xml"] = ET.tostring(doc)
    write_parts(draft, parts)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


@pytest.mark.parametrize("suffix", [".docx", ".hwpx"])
def test_renamed_filled_form_requires_its_reference(tmp_path, suffix):
    source = tmp_path / ("blank" + suffix)
    if suffix == ".docx":
        make_form(source, "{{사건번호}}")
    else:
        import report_hwpx
        report_hwpx.write_hwpx(source, "{{사건번호}}")
    draft = tmp_path / ("양식초안" + suffix)
    forms.write_form(source, draft, {"사건번호": "A-01"})
    assert report_draft.run_verify(draft, [])["verdict"] == "PASS"
    moved = draft.rename(tmp_path / ("delivery" + suffix))
    assert report_draft.run_verify(moved, [])["verdict"] == "FAIL"


def test_page_field_cannot_replace_a_measured_scalar(tmp_path):
    source = make_form(tmp_path / "blank.docx", "Page")
    doc_parts = package(source)
    doc = ET.fromstring(doc_parts["word/document.xml"])
    p = doc.find(".//" + W + "p")
    field = report_docx.word(p, "fldSimple", instr="PAGE")
    report_docx.word(report_docx.word(field, "r"), "t").text = "{{사건번호}}"
    doc_parts["word/document.xml"] = ET.tostring(doc)
    write_parts(source, doc_parts)
    with pytest.raises(ValueError):
        forms.write_form(source, tmp_path / forms.OUTPUT_NAME, {"사건번호": "A-01"})


def test_legacy_docx_receipt_can_still_check_static_text(tmp_path):
    draft = make_form(tmp_path / forms.OUTPUT_NAME, "Sample")
    forms.digest_path(draft).write_text(hashlib.sha256(b"Sample").hexdigest() + "\n", encoding="ascii")
    assert report_draft.run_verify(draft, [])["exit_code"] == 0


@pytest.mark.parametrize("merge", ["hMerge", "vMerge"])
def test_word_continuation_cell_cannot_hide_measured_text(tmp_path, merge):
    source = make_form(tmp_path / "blank.docx", "| A | B |\n| --- | --- |\n| Sample | {{사건번호}} |")
    parts = package(source)
    doc = ET.fromstring(parts["word/document.xml"])
    rows = doc.findall(".//" + W + "tr")
    start = rows[0 if merge == "vMerge" else 1].findall(W + "tc")[1 if merge == "vMerge" else 0]
    report_docx.word(start.find(W + "tcPr"), merge, val="restart")
    report_docx.word(rows[1].findall(W + "tc")[1].find(W + "tcPr"), merge)
    parts["word/document.xml"] = ET.tostring(doc)
    write_parts(source, parts)
    with pytest.raises(ValueError):
        forms.write_form(source, tmp_path / forms.OUTPUT_NAME, {"사건번호": "A-01"})


@pytest.mark.parametrize("ext", ["docx", "hwpx"])
def test_direction_controls_are_visible_and_hashes_stay_bound(tmp_path, ext):
    root, out = tmp_path / "evidence", tmp_path / "out"
    root.mkdir()
    name = "sample\u202eitem.bin"
    (root / name).write_bytes(b"sample")
    source = ROOT / ("templates/report_form." + ext)
    assert report_draft.main([str(root), "-o", str(out), "--template", str(source)]) == 0
    checked = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    assert checked["template"]["verdict"] == "PASS"
    raw = json.loads((out / "무결성검증.json").read_text(encoding="utf-8"))
    assert raw["files"][0]["path"] == name
    for path in [out / ("양식초안." + ext), out / ("보고서초안." + ext)]:
        if ext == "docx":
            body = report_docx.read_docx(path)
        else:
            body = package(path)["Preview/PrvText.txt"].decode()
        assert "\u202e" not in body
        assert r"sample\u202eitem.bin" in body


def test_form_file_labels_keep_control_characters_distinct():
    paths = ["a\nb", "a b", r"a\nb", "a\x0bb", "a\x00b", r"a\u0000b"]
    fields = forms.form_fields({}, [{"path": p} for p in paths], [], [], 100)
    labels = [r[0] for r in fields["증거목록표"][1:]]
    assert len(set(labels)) == len(paths)
    assert all(not any(c in value for c in "\x00\n\x0b") for value in labels)


@pytest.mark.parametrize("feature", ["symbol", "textpath", "tracked_insert", "wrong_story_root"])
def test_word_gate_rejects_unreadable_story_content(tmp_path, feature):
    draft = make_form(tmp_path / "draft.docx", "Sample")
    parts = package(draft)
    doc = ET.fromstring(parts["word/document.xml"])
    run = doc.find(".//" + W + "r")
    if feature == "symbol":
        report_docx.word(run, "sym", font="Symbol", char="F061")
    elif feature == "textpath":
        shape = ET.SubElement(report_docx.word(run, "pict"), "{urn:schemas-microsoft-com:vml}shape")
        ET.SubElement(shape, "{urn:schemas-microsoft-com:vml}textpath", string="{{review}}")
    elif feature == "tracked_insert":
        p = doc.find(".//" + W + "p")
        p.remove(run)
        report_docx.word(p, "ins", id="1").append(run)
    else:
        parts["word/header1.xml"] = b"<sample/>"
        parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
            b"</Types>", b'<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-'
            b'officedocument.wordprocessingml.header+xml"/></Types>')
    parts["word/document.xml"] = ET.tostring(doc)
    write_parts(draft, parts)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


@pytest.mark.parametrize("feature", ["simple_page", "complex_page", "nonbreaking_hyphen"])
def test_scalar_marker_cannot_cross_a_word_control(tmp_path, feature):
    source = make_form(tmp_path / "blank.docx", "{{사건")
    parts = package(source)
    doc = ET.fromstring(parts["word/document.xml"])
    p = doc.find(".//" + W + "p")
    if feature == "simple_page":
        report_docx.word(p, "fldSimple", instr=" PAGE ")
    elif feature == "complex_page":
        for kind in ("begin", "separate", "end"):
            report_docx.word(report_docx.word(p, "r"), "fldChar", fldCharType=kind)
            if kind == "begin":
                report_docx.word(report_docx.word(p, "r"), "instrText").text = " PAGE "
    else:
        report_docx.word(report_docx.word(p, "r"), "noBreakHyphen")
    report_docx.word(report_docx.word(p, "r"), "t").text = "번호}}"
    parts["word/document.xml"] = ET.tostring(doc)
    write_parts(source, parts)
    with pytest.raises(ValueError):
        forms.write_form(source, tmp_path / forms.OUTPUT_NAME, {"사건번호": "A-01"})


def test_word_nonbreaking_hyphen_is_part_of_verified_text(tmp_path):
    source = make_form(tmp_path / "blank.docx", "A")
    parts = package(source)
    doc = ET.fromstring(parts["word/document.xml"])
    r = doc.find(".//" + W + "r")
    report_docx.word(r, "noBreakHyphen")
    report_docx.word(r, "t").text = "01"
    parts["word/document.xml"] = ET.tostring(doc)
    write_parts(source, parts)
    with ZipFile(source) as template:
        assert forms.form_text(template) == "A\u201101"
