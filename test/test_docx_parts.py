"""Verify the document part selected by the package relationships."""
from pathlib import Path
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import report_docx
import report_draft


@pytest.mark.parametrize("broken_part", ["_rels/.rels", "[Content_Types].xml"])
def test_invalid_docx_package_metadata_fails(tmp_path, broken_part):
    path = tmp_path / "draft.docx"
    report_docx.write_docx(path, "Draft")
    with ZipFile(path) as z:
        parts = {name: z.read(name) for name in z.namelist()}
    parts[broken_part] = b"<empty/>"
    with ZipFile(path, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)
    assert report_draft.run_verify(path, [])["verdict"] == "FAIL"


def test_read_declared_main_document(tmp_path):
    path = tmp_path / "draft.docx"
    report_docx.write_docx(path, "Draft")
    with ZipFile(path) as z:
        parts = {name: z.read(name) for name in z.namelist()}
    old = "word/document.xml"
    main = "parts/main.xml"
    for name in ("[Content_Types].xml", "_rels/.rels"):
        parts[name] = parts[name].replace(old.encode(), main.encode())
    parts[main] = parts[old].replace(b">Draft<", b">Revised<")
    with ZipFile(path, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)
    assert report_docx.read_docx(path) == "Revised"


def test_case_field_with_carriage_return_stays_in_docx_cell(tmp_path):
    md = "\n".join(report_draft.table(["Field", "Value"], [["description", "first\rsecond"]]))
    path = tmp_path / "draft.docx"
    report_docx.write_docx(path, md)
    with ZipFile(path) as z:
        row = ET.fromstring(z.read("word/document.xml")).findall(".//" + report_docx.W + "tr")[1]
    assert [t.text for t in row.iter(report_docx.W + "t")] == ["description", "first second"]
