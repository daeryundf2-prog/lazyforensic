"""HWPX package, report generation and verifier regression tests using synthetic inputs."""
import hashlib
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import report_draft
import report_hwpx

NS = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
      "hh": "http://www.hancom.co.kr/hwpml/2011/head",
      "opf": "http://www.idpf.org/2007/opf/"}
SECTION = "Contents/section0.xml"
MD = "# 한글 & <초안>\n\n| 파일 | 상태 |\n| --- | --- |\n| 한글 \\| 표.txt | 미확인 |"
DIGEST = re.compile(r"\b(?:[0-9a-f]{64}|[0-9a-f]{40}|[0-9a-f]{32})\b")


def test_package_and_cells(tmp_path):
    path = tmp_path / "draft.hwpx"
    report_hwpx.write_hwpx(path, MD)
    with ZipFile(path) as archive:
        assert archive.testzip() is None
        mime = archive.infolist()[0]
        assert (mime.filename, mime.header_offset, mime.compress_type) == ("mimetype", 0, ZIP_STORED)
        assert archive.read(mime) == b"application/hwp+zip"
        required = {"version.xml", "META-INF/container.xml", "META-INF/manifest.xml", "settings.xml",
                    "Contents/content.hpf", "Contents/header.xml", SECTION, "Preview/PrvText.txt", "mimetype"}
        assert required <= set(archive.namelist())
        parts = {n: ET.fromstring(archive.read(n)) for n in archive.namelist() if n.endswith((".hpf", ".xml"))}
        roots = parts["META-INF/container.xml"].findall("{*}rootfiles/{*}rootfile")
        package, = [r.attrib["full-path"] for r in roots if r.get("media-type") == "application/hwpml-package+xml"]
        assert all(r.attrib["full-path"] in required for r in roots)
        entries = parts["META-INF/manifest.xml"].findall("{*}file-entry")
        odf = "{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}"
        assert {e.attrib[odf + "full-path"] for e in entries} == (required - {"META-INF/manifest.xml", "mimetype"}) | {"/"}
        items = {i.attrib["id"]: i.attrib["href"] for i in parts[package].findall("opf:manifest/opf:item", NS)}
        assert set(items.values()) == {"Contents/header.xml", SECTION, "settings.xml"}
        assert [items[r.attrib["idref"]] for r in parts[package].findall("opf:spine/opf:itemref", NS)] == [
            "Contents/header.xml", SECTION]
        header = parts["Contents/header.xml"]
        fonts = header.findall(".//hh:fontface[@lang='HANGUL']/hh:font", NS)
        assert {f.get("face") for f in fonts} == {"함초롬바탕", "맑은 고딕"}
        sec = parts[SECTION]
        styles = {s.get("id") for s in header.findall(".//hh:styles/hh:style", NS)}
        assert {p.attrib["styleIDRef"] for p in sec.findall(".//hp:p", NS)} <= styles
        page = sec.find("hp:p/hp:run/hp:secPr/hp:pagePr", NS)
        assert (int(page.attrib["width"]), int(page.attrib["height"])) == (59528, 84188)
        assert page.find("hp:margin", NS).get("left") == "5669"
        rows = sec.findall(".//hp:tbl/hp:tr", NS)
        for y, row in enumerate(rows):
            for x, cell in enumerate(row.findall("hp:tc", NS)):
                assert cell.find("hp:cellAddr", NS).attrib == {"colAddr": str(x), "rowAddr": str(y)}
        assert [[c.findtext("hp:subList/hp:p/hp:run/hp:t", namespaces=NS) for c in r.findall("hp:tc", NS)]
                for r in rows] == [["파일", "상태"], ["한글 | 표.txt", "미확인"]]
        expected = "한글 & <초안>\n| 파일 | 상태 |\n| 한글 \\| 표.txt | 미확인 |"
        assert archive.read("Preview/PrvText.txt").decode("utf-8") == expected
    assert report_hwpx.read_hwpx(path) == expected
    first = path.read_bytes()
    report_hwpx.write_hwpx(path, MD)
    assert path.read_bytes() == first


@pytest.mark.parametrize(("options", "formats"), [
    ([], {"hwpx", "docx"}), (["--hwpx", "--no-docx"], {"hwpx"}),
    (["--no-hwpx"], {"docx"}), (["--no-hwpx", "--no-docx"], set()),
])
def test_report_format_selection(tmp_path, options, formats):
    evidence = tmp_path / "증거"
    evidence.mkdir()
    data = b"synthetic"
    (evidence / "자료.txt").write_bytes(data)
    output = tmp_path / "out"
    assert report_draft.main([str(evidence), "-o", str(output), *options]) == 0
    result = json.loads((output / "초안검증.json").read_text(encoding="utf-8"))
    assert {"hwpx", "docx"} & result.keys() == formats
    for fmt in ("hwpx", "docx"):
        assert (output / f"보고서초안.{fmt}").exists() == (fmt in formats)
    if "hwpx" in formats:
        draft = output / "보고서초안.hwpx"
        assert result["hwpx"]["verdict"] == result["verdict"] == "PASS"
        md = draft.with_suffix(".md").read_text(encoding="utf-8")
        text = report_hwpx.read_hwpx(draft)
        assert DIGEST.findall(text) == DIGEST.findall(md)
        assert all(h(data).hexdigest() in text for h in (hashlib.md5, hashlib.sha1, hashlib.sha256))
        assert text.count("미확인") == md.count("미확인") > 0
        assert all(title in text for title in re.findall(r"^#+ (.+)", md, re.M))
        assert "| 검증 항목 | 결과 | 근거 |" in text and "| 시각 | 파일 |" in text


def change_member(path, name, change):
    with ZipFile(path) as archive:
        parts = [(entry, archive.read(entry)) for entry in archive.infolist()]
    with ZipFile(path, "w") as archive:
        for entry, payload in parts:
            archive.writestr(entry, change(payload) if entry.filename == name else payload)


def test_gate_checks_saved_section_even_if_preview_matches(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "합성.bin").write_bytes(b"sample")
    out = tmp_path / "out"
    write = report_hwpx.write_hwpx

    def replace_section(path, md):
        write(path, md)

        def tamper(payload):
            sec = ET.fromstring(payload)
            digest = next(t for t in sec.findall(".//hp:tc//hp:t", NS) if (t.text or "").startswith("MD5 "))
            digest.text = "MD5 " + "a" * 32
            para = sec.find("hp:p", NS)
            for value in ("명백히 ", "입증"):
                run = ET.SubElement(para, f"{{{NS['hp']}}}run")
                ET.SubElement(run, f"{{{NS['hp']}}}t").text = value
            return ET.tostring(sec, encoding="utf-8")
        change_member(path, SECTION, tamper)

    monkeypatch.setattr(report_hwpx, "write_hwpx", replace_section)
    assert report_draft.main([str(evidence), "-o", str(out)]) == 1
    result = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    assert result["verdict"] == result["hwpx"]["verdict"] == "FAIL"
    assert result["hwpx"]["forbidden_hits"] == ["명백히 입증"]
    for message in ("Markdown", "근거 없는 해시", "금지 문구"):
        assert any(message in error for error in result["hwpx"]["errors"])


@pytest.mark.parametrize(("name", "change"), [
    (SECTION, lambda s: b"<broken"), ("Contents/content.hpf", lambda s: s.replace(b'idref="section0"', b'idref="missing"')),
    ("mimetype", lambda s: b"application/zip"),
    (SECTION, lambda s: s.replace(b'colAddr="1"', b'colAddr="0"')),
])
def test_unreadable_draft_fails_closed(tmp_path, name, change):
    draft = tmp_path / "draft.hwpx"
    report_hwpx.write_hwpx(draft, MD)
    change_member(draft, name, change)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"


def test_public_example_preserves_archived_acquisition_time():
    output = Path(__file__).resolve().parents[1] / "examples/nps-2009-canon2/output"
    events = json.loads((output / "events.json").read_text(encoding="utf-8"))
    event, = [e for e in events if e["source"] == "E01 헤더"]
    utc = event["timestamp"].removesuffix("+00:00").replace("T", " ") + " UTC"
    hwpx = output / "보고서초안.hwpx"
    text = report_hwpx.read_hwpx(hwpx)
    assert f"| {utc} | {event['file']} |" in text
    assert DIGEST.findall(text) == DIGEST.findall(hwpx.with_suffix(".md").read_text(encoding="utf-8"))
