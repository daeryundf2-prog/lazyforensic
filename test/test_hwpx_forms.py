"""Synthetic Hangul forms, exercised through the same fill and verify paths as the CLI."""
import hashlib
import json
import re
import sys
from itertools import count
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import report_docx
import report_draft
import report_hwpx as hw
import report_hwpx_forms
import report_templates as forms

SAMPLE = ROOT / "templates/report_form.hwpx"
SECTION = "Contents/section0.xml"
PREVIEW = "Preview/PrvText.txt"
HP = hw.HP


def members(path):
    with ZipFile(path) as package:
        return {info.filename: package.read(info) for info in package.infolist()}


def write(path, parts):
    with ZipFile(path, "w") as package:
        for name, data in parts.items():
            package.writestr(name, data)


def rows(table):
    return [["".join(t.text or "" for t in tc.iter(HP + "t")) for tc in tr.findall(HP + "tc")]
            for tr in table.findall(HP + "tr")]


def test_split_runs_stories_order_and_resources(tmp_path):
    source, draft = tmp_path / "blank.hwpx", tmp_path / "양식초안.hwpx"
    parts = members(SAMPLE)
    sec = hw.section("앞 {")
    p = sec[0]
    for style, text in ((2, "{사건"), (3, "번호}} 뒤 {{사건명}}"), (1, "{{없음}}")):
        hw.add(hw.add(p, "hp:run", charPrIDRef=style), "hp:t").text = text
    footer = hw.add(hw.add(p[0], "hp:ctrl"), "hp:footer", id=90, applyPageType="BOTH")
    sub = hw.add(footer, "hp:subList")
    foot_run = hw.add(hw.paragraph(sub, count(91), 0), "hp:run", charPrIDRef=0)
    hw.add(foot_run, "hp:t").text = "꼬리 {{사건명}}"
    pic = hw.add(p[-1], "hp:pic", id=93)
    hw.add(pic, "hc:img", binaryItemIDRef="image1")
    parts["BinData/image1.png"] = (ROOT / "test/fixtures/eval/media/red_square.png").read_bytes()
    parts[SECTION] = hw.serialize(sec).replace(b'left="5669"', b"left = '5669'").replace(
        b'binaryItemIDRef="image1"', b"binaryItemIDRef = 'image1'")
    other = "Contents/section1.xml"
    parts[other] = hw.serialize(hw.section("먼저 읽는 섹션"))
    package = hw.content_package()
    manifest, spine = package[-2:]
    hw.add(manifest, "opf:item", id="image1", href="BinData/image1.png", **{"media-type": "image/png"})
    hw.add(manifest, "opf:item", id="s1", href=other, **{"media-type": "application/xml"})
    spine.insert(1, ET.Element("opf:itemref", idref="s1"))
    parts["Contents/content.hpf"] = hw.serialize(package)
    parts["Contents/header.xml"] = parts["Contents/header.xml"].replace(b'secCnt="1"', b'secCnt="2"')
    write(source, parts)
    fields = {"사건번호": "합성-01", "사건명": "<합성> & 양식"}
    forms.write_form(source, draft, fields)
    filled = members(draft)
    with ZipFile(draft) as z:
        assert (z.infolist()[0].filename, z.infolist()[0].compress_type) == ("mimetype", ZIP_STORED)
        assert z.testzip() is None
    assert filled.keys() == parts.keys()
    assert all(filled[n] == parts[n] for n in parts.keys() - {SECTION, PREVIEW})
    actual = ET.fromstring(filled[SECTION])
    expected = ET.fromstring(parts[SECTION])
    for name in (b"secPr", b"pic"):
        fragment = re.search(b"<hp:" + name + rb"\b.*?</hp:" + name + b">", parts[SECTION], re.S)[0]
        assert fragment in filled[SECTION]
    for path in (".//" + HP + "secPr", ".//" + HP + "pic"):
        assert ET.tostring(actual.find(path)) == ET.tostring(expected.find(path))
    runs = actual[0].findall(HP + "run")[1:]
    assert [(r.get("charPrIDRef"), r.findtext(HP + "t")) for r in runs] == [
        ("0", "앞 합성-01"), ("2", ""), ("3", " 뒤 <합성> & 양식"), ("1", "미확인")]
    errors = []
    text = forms.check_form(draft, forms.digest_path(draft), errors)
    assert not errors and text.startswith("먼저 읽는 섹션\n꼬리 <합성> & 양식")
    assert text == filled[PREVIEW].decode()
    assert "{{" not in text and "}}" not in text
    assert report_draft.run_verify(draft, [])["verdict"] == "PASS"
    before = draft.read_bytes()
    forms.write_form(source, draft, fields)
    assert draft.read_bytes() == before and members(source) == parts
    filled[SECTION] = filled[SECTION].replace("꼬리".encode(), "명백히 입증".encode())
    write(draft, filled)
    result = report_draft.run_verify(draft, [])
    assert result["forbidden_hits"] == ["명백히 입증"] and result["verdict"] == "FAIL"


def test_nested_repeat_preserves_header_styles_and_following_row(tmp_path):
    source, draft = tmp_path / "blank.hwpx", tmp_path / "양식초안.hwpx"
    parts = members(SAMPLE)
    sec = hw.section("| 고정 | 값 |\n| --- | --- |\n| 안쪽 | {{사건번호}} |")
    outer = next(next(sec.iter("hp:tc")).iter("hp:run"))
    hw.table(outer, [["경로", "크기", "해시"], ["", "{{증거목록", ""], ["고정", "끝", "행"]], count(50))
    inner = outer.find("hp:tbl")
    marker = next(inner.findall("hp:tr")[1].findall("hp:tc")[1].iter("hp:p"))
    hw.add(hw.add(marker, "hp:run", charPrIDRef=3), "hp:t").text = "표}}"
    hw.add(hw.add(marker, "hp:linesegarray"), "hp:lineseg", textpos=0)
    parts[SECTION] = hw.serialize(sec)
    write(source, parts)
    data = [["파일", "크기", "해시"], ["가.txt", "11", "첫 값"], ["나.txt", "22", "다음 값"]]
    forms.write_form(source, draft, {"증거목록표": data, "사건번호": "합성"})
    filled = members(draft)
    root = ET.fromstring(filled[SECTION])
    outside, inside = list(root.iter(HP + "tbl"))
    assert outside.get("rowCnt") == "2"
    assert inside.get("rowCnt") == "4"
    assert rows(inside) == [["경로", "크기", "해시"], *data[1:], ["고정", "끝", "행"]]
    trs = inside.findall(HP + "tr")
    for i, tr in enumerate(trs):
        for j, addr in enumerate(tr.iter(HP + "cellAddr")):
            assert (addr.get("rowAddr"), addr.get("colAddr")) == (str(i), str(j))
    for tr in trs[1:3]:
        assert tr.findall(HP + "tc")[1].find(".//" + HP + "run[@charPrIDRef='3']") is not None
        assert tr.find(".//" + HP + "linesegarray") is None
    assert report_draft.run_verify(draft, [])["verdict"] == "PASS"
    # Drop one item, while leaving all remaining row/column coordinates valid.
    inside.remove(trs[1])
    inside.set("rowCnt", "3")
    for i, tr in enumerate(inside.findall(HP + "tr")):
        for addr in tr.iter(HP + "cellAddr"):
            addr.set("rowAddr", str(i))
    filled[SECTION] = ET.tostring(root, encoding="utf-8")
    write(draft, filled)
    assert any("SHA-256 불일치" in e for e in report_draft.run_verify(draft, [])["errors"])


def test_shipped_form_cli_and_independent_recheck(tmp_path):
    assert hw.read_hwpx(SAMPLE) == report_docx.read_docx(SAMPLE.with_suffix(".docx"))
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    data = b"synthetic"
    (ev / "자료.bin").write_bytes(data)
    for source in (SAMPLE.with_suffix(".docx"), SAMPLE):
        assert report_draft.main([str(ev), "-o", str(out), "--no-hwpx", "--template", str(source)]) == 0
    draft = out / "양식초안.hwpx"
    assert not (out / "보고서초안.hwpx").exists()
    result = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    assert result["template"]["verdict"] == result["verdict"] == "PASS"
    root = ET.fromstring(members(draft)[SECTION])
    _, inventory, integrity, timeline = [rows(t) for t in root.iter(HP + "tbl")]
    assert inventory[1] == ["자료.bin", "9", " / ".join(
        label + " " + h(data).hexdigest() for label, h in (("MD5", hashlib.md5), ("SHA-1", hashlib.sha1), ("SHA-256", hashlib.sha256)))]
    assert integrity[1][:2] == ["분석 전후 재해시", "일치"]
    event, = json.loads((out / "events.json").read_text(encoding="utf-8"))
    assert timeline[1] == [event[k] for k in ("timestamp", "source", "file", "description")]
    for form in (draft.with_suffix(".docx"), draft):
        assert report_draft.run_verify(form, [out / "무결성검증.json"])["verdict"] == "PASS"
    before = draft.read_bytes()
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(draft)]) == 2
    assert draft.read_bytes() == before
    forms.digest_path(draft).unlink()
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


@pytest.mark.parametrize(("replacement", "message"), [
    ("합성-99", "SHA-256 불일치"), ("{{남음}}", "자리표시자"), ("MD5 " + "a" * 32, "근거 없는 해시"),
    ("명백히 입증", "금지 문구"),
])
def test_gate_checks_saved_xml_independently(tmp_path, monkeypatch, replacement, message):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    (ev / "시료.bin").write_bytes(b"sample")
    fill = report_hwpx_forms.fill_story

    def corrupt(*args):
        return fill(*args).replace("미확인".encode(), replacement.encode())

    monkeypatch.setattr(report_hwpx_forms, "fill_story", corrupt)
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(SAMPLE)]) == 1
    result = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    assert result["verdict"] == result["template"]["verdict"] == "FAIL"
    assert any(message in error for error in result["template"]["errors"])


def test_caption_merged_rows_and_single_slot(tmp_path):
    source, draft = tmp_path / "blank.hwpx", tmp_path / "양식초안.hwpx"
    parts = members(SAMPLE)
    sec = hw.section("| 구분 | 항목 |\n| --- | --- |\n| 고정 | 시작 |\n|  | {{무결성검증표}} |\n|  | 끝 |")
    table = next(sec.iter("hp:tbl"))
    header, start, repeat, end = table.findall("hp:tr")
    start[0].find("hp:cellSpan").set("rowSpan", "3")
    for tr in (repeat, end):
        tr.remove(tr[0])
    caption = ET.Element("hp:caption", side="TOP")
    table.insert(3, caption)
    cp = hw.paragraph(hw.add(caption, "hp:subList"), count(100), 0)
    hw.add(hw.add(cp, "hp:run", charPrIDRef=0), "hp:t").text = "표 제목 {{사건명}}"
    zones = ET.Element("hp:cellzoneList")
    table.insert(list(table).index(header), zones)
    hw.add(zones, "hp:cellzone", startRowAddr=1, endRowAddr=2, startColAddr=1, endColAddr=1, borderFillIDRef=1)
    parts[SECTION] = hw.serialize(sec)
    write(source, parts)
    fields = {"무결성검증표": [["검증", "근거"], ["첫째", "자료"], ["둘째", "기록"]], "사건명": "합성"}
    forms.write_form(source, draft, fields)
    filled = members(draft)
    table = ET.fromstring(filled[SECTION]).find(".//" + HP + "tbl")
    assert rows(table) == [["구분", "항목"], ["고정", "시작"], ["첫째 / 자료"], ["둘째 / 기록"], ["끝"]]
    assert table.findall(HP + "tr")[1].find(".//" + HP + "cellSpan").get("rowSpan") == "4"
    assert table.find(".//" + HP + "cellzone").get("endRowAddr") == "3"
    assert report_draft.run_verify(draft, [])["verdict"] == "PASS"
    filled[SECTION] = filled[SECTION].replace("표 제목 합성".encode(), "명백히 입증".encode())
    write(draft, filled)
    assert report_draft.run_verify(draft, [])["forbidden_hits"] == ["명백히 입증"]


def test_empty_inventory_and_timeline(tmp_path):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(SAMPLE)]) == 0
    draft = out / "양식초안.hwpx"
    text = members(draft)[PREVIEW].decode()
    assert "분석 대상 증거\n미확인" in text and "타임라인\n미확인" in text
    assert "{{" not in text


def test_native_master_page_and_unreadable_table_text(tmp_path):
    source, draft = tmp_path / "blank.hwpx", tmp_path / "양식초안.hwpx"
    parts = members(SAMPLE)
    master = hw.root("masterPage")
    sub = hw.add(master, "hp:subList")
    p = hw.paragraph(sub, count(100), 0)
    hw.add(hw.add(p, "hp:run", charPrIDRef=0), "hp:t").text = "바탕쪽 {{사건번호}}"
    name = "Contents/masterpage0.xml"
    parts[name] = hw.serialize(master)
    content = hw.content_package()
    hw.add(content[-2], "opf:item", id="masterpage0", href=name, **{"media-type": "application/xml"})
    parts["Contents/content.hpf"] = hw.serialize(content)
    write(source, parts)
    forms.write_form(source, draft, {"사건번호": "합성"})
    assert report_draft.run_verify(draft, [])["verdict"] == "PASS"
    filled = members(draft)
    assert "바탕쪽 합성" in filled[name].decode()
    # Text hidden outside a table cell's paragraph list cannot be silently omitted by the gate.
    filled[SECTION] = filled[SECTION].replace(b"</hp:tc>", b"<hp:t>extra</hp:t></hp:tc>", 1)
    write(draft, filled)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


@pytest.mark.parametrize("has_marker", [True, False])
def test_fill_always_refreshes_existing_preview(tmp_path, has_marker):
    source, draft = tmp_path / "blank.hwpx", tmp_path / "양식초안.hwpx"
    parts = members(SAMPLE)
    parts[SECTION] = hw.serialize(hw.section("{{사건번호}}" if has_marker else "Sample"))
    parts[PREVIEW] = b"obsolete sample"
    write(source, parts)
    forms.write_form(source, draft, {"사건번호": "Sample"})
    assert members(draft)[PREVIEW] == b"Sample"
    assert report_draft.run_verify(draft, [])["verdict"] == "PASS"
    changed = members(draft)
    changed[PREVIEW] = b"obsolete sample"
    write(draft, changed)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


@pytest.mark.parametrize("marker", ["{{사건번호}}", "{{증거목록표}}"])
def test_hwpx_cdata_accepts_literal_cdata_terminator(tmp_path, marker):
    source, draft = tmp_path / "blank.hwpx", tmp_path / "양식초안.hwpx"
    parts = members(SAMPLE)
    if marker == "{{사건번호}}":
        sec = hw.section(marker)
    else:
        sec = hw.section("| A | B | C |\n| --- | --- | --- |\n| {{증거목록표}} |  |  |")
    value = "sample <&]]>" + "가" * 8192
    parts[SECTION] = hw.serialize(sec).replace(marker.encode(), f"<![CDATA[{marker}]]>".encode())
    write(source, parts)
    fields = {"사건번호": value, "증거목록표": [["A", "B", "C"], [value, "0", "미확인"]]}
    forms.write_form(source, draft, fields)
    assert value in members(draft)[PREVIEW].decode()
    assert report_draft.run_verify(draft, [])["verdict"] == "PASS"


@pytest.mark.parametrize("element", ["shapeComment", "fieldBegin"])
def test_hwpx_unprojected_control_text_fails_verification(tmp_path, element):
    draft = tmp_path / "양식초안.hwpx"
    forms.write_form(SAMPLE, draft, {"사건번호": "Sample"})
    parts = members(draft)
    sec = ET.fromstring(parts[SECTION])
    run = sec.find(".//" + HP + "run")
    if element == "shapeComment":
        ET.SubElement(ET.SubElement(run, HP + "pic"), HP + element).text = "{{review}}"
    else:
        ET.SubElement(ET.SubElement(run, HP + "ctrl"), HP + element, type="DATE")
    parts[SECTION] = ET.tostring(sec, encoding="utf-8")
    write(draft, parts)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


def test_preview_at_declared_path_is_refreshed(tmp_path):
    source, draft = tmp_path / "blank.hwpx", tmp_path / "양식초안.hwpx"
    parts = members(SAMPLE)
    other = "Preview/sample.txt"
    parts[other] = parts.pop(PREVIEW)
    for part in ("META-INF/container.xml", "META-INF/manifest.xml"):
        parts[part] = parts[part].replace(PREVIEW.encode(), other.encode())
    write(source, parts)
    forms.write_form(source, draft, {"사건번호": "A-01"})
    with ZipFile(draft) as z:
        assert z.read(other).decode() == report_hwpx_forms.form_content(z)[1]


def test_unregistered_marker_without_case_data_has_matching_reference(tmp_path):
    source, draft = tmp_path / "blank.hwpx", tmp_path / "양식초안.hwpx"
    hw.write_hwpx(source, "{{unknown}}")
    forms.write_form(source, draft, {})
    assert members(draft)[PREVIEW].decode() == forms.UNKNOWN
    assert report_draft.run_verify(draft, [])["verdict"] == "PASS"


def test_verification_binds_values_to_logical_merged_columns(tmp_path):
    source, draft = tmp_path / "blank.hwpx", tmp_path / "양식초안.hwpx"
    parts = members(SAMPLE)
    sec = hw.section("| A | B |\n| --- | --- |\n| left | right |\n|  | result |")
    table = next(sec.iter("hp:tbl"))
    start, end = table.findall("hp:tr")[1:]
    start[0].find("hp:cellSpan").set("rowSpan", "2")
    end.remove(end[0])
    parts[SECTION] = hw.serialize(sec)
    write(source, parts)
    forms.write_form(source, draft, {})
    assert report_draft.run_verify(draft, [])["verdict"] == "PASS"
    start[0].find("hp:cellSpan").set("rowSpan", "1")
    start[1].find("hp:cellSpan").set("rowSpan", "2")
    end[0].find("hp:cellAddr").set("colAddr", "0")
    changed = members(draft)
    changed[SECTION] = hw.serialize(sec)
    write(draft, changed)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


def test_legacy_hwpx_receipt_can_still_check_static_text(tmp_path):
    draft = tmp_path / "양식초안.hwpx"
    hw.write_hwpx(draft, "Sample")
    legacy = hashlib.sha256(b'[[["p",["Sample"]]]]').hexdigest()
    forms.digest_path(draft).write_text(legacy + "\n", encoding="ascii")
    assert report_draft.run_verify(draft, [])["exit_code"] == 0
