# -*- coding: utf-8 -*-
"""Report verification regressions from the D17 review (synthetic inputs only).

F4 claims bound to one measured record, F9 placeholders in ordinary office drafts, and the
remaining D13 #10 cases (tokens left in a form's alt text/title/description).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "test"))

import report_docx  # noqa: E402
import report_draft  # noqa: E402
import report_hwpx  # noqa: E402
import report_templates as forms  # noqa: E402
from test_run_provenance import docx_with_alt_text_token, hwpx_with_comment_token  # noqa: E402

GATE = ["--morph-grounding", "--high-fidelity", "--strict"]


def verify(path, *extra):
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/verify_report.py"), str(path), "--json", *map(str, extra)],
                          capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, json.loads(proc.stdout)


# F4 ------------------------------------------------------------------------------------------


BOXES = {
    "observation sentences": {"observations": ["청색 표시는 상자 A에 있다.", "백색 표시는 상자 B에 있다."]},
    "objects": {"marks": [{"표시": "청색", "상자": "A", "note": "청색 표시는 상자 A에 있다."},
                          {"표시": "백색", "상자": "B", "note": "백색 표시는 상자 B에 있다."}]},
}


def evidence_file(tmp_path, shape):
    evidence = tmp_path / "observed.json"
    evidence.write_text(json.dumps(BOXES[shape], ensure_ascii=False), encoding="utf-8")
    return evidence


def draft(tmp_path, conclusion, quote="청색 표시는 상자 A에 있다."):
    path = tmp_path / "draft.md"
    path.write_text(f"# 감정 결과\n<evidence>{quote}</evidence>\n{conclusion}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("shape", sorted(BOXES))
def test_exact_quote_does_not_carry_a_conclusion_about_another_measured_object(tmp_path, shape):
    evidence = evidence_file(tmp_path, shape)
    code, consistent = verify(draft(tmp_path, "청색 표시는 상자 A에 있다."), "--evidence", evidence, *GATE)
    assert (code, consistent["verdict"], consistent["errors"]) == (0, "PASS", []), consistent
    assert [(c["status"], c["terms"]) for c in consistent["claim_binding"]["claims"]] == [("bound", ["a", "청색"])]

    code, moved = verify(draft(tmp_path, "청색 표시는 상자 B에 있다."), "--evidence", evidence, *GATE)
    assert (code, moved["verdict"]) == (1, "FAIL"), moved
    assert any("주장-근거 결합 불일치" in e and "상자 B" in e for e in moved["errors"]), moved["errors"]
    claim, = moved["claim_binding"]["claims"]
    assert (claim["status"], claim["crossed"]) == ("unbound", ["청색", "b"])

    # Naming both measured objects (same attribute of each) is not a crossing.
    code, listed = verify(draft(tmp_path, "청색 표시와 백색 표시를 상자 A와 상자 B에서 각각 확인했다."),
                          "--evidence", evidence, *GATE)
    assert not any("주장-근거 결합 불일치" in e for e in listed["errors"]), listed

    # Without --high-fidelity the same finding is a review warning; --strict still refuses it.
    code, warned = verify(draft(tmp_path, "청색 표시는 상자 B에 있다."), "--evidence", evidence, "--morph-grounding")
    assert (code, warned["verdict"]) == (0, "WARN") and any("주장-근거 결합 불일치" in w for w in warned["warnings"])
    code, _ = verify(draft(tmp_path, "청색 표시는 상자 B에 있다."), "--evidence", evidence, "--morph-grounding", "--strict")
    assert code == 1


def test_object_attributes_bind_within_one_record_and_shared_context_is_inherited(tmp_path):
    evidence = tmp_path / "survey.json"
    evidence.write_text(json.dumps({"tool": "SynthTool", "summary": "기록상 촬영 장치 관계", "files": [
        {"path": "a.jpg", "camera": "CAM-01"}, {"path": "b.jpg", "camera": "CAM-02"}]}, ensure_ascii=False),
        encoding="utf-8")
    quote = "a.jpg"
    code, result = verify(draft(tmp_path, "SynthTool 기록상 a.jpg 촬영 장치는 CAM-01", quote), "--evidence", evidence, *GATE)
    assert (code, result["verdict"]) == (0, "PASS"), result
    code, result = verify(draft(tmp_path, "SynthTool 기록상 a.jpg 촬영 장치는 CAM-02", quote), "--evidence", evidence, *GATE)
    assert code == 1 and any("주장-근거 결합 불일치" in e for e in result["errors"]), result
    # An explicit unknown is not a claim about either object.
    code, result = verify(draft(tmp_path, "a.jpg 촬영 장치와 CAM-02 관계는 미확인", quote), "--evidence", evidence, *GATE)
    assert (code, result["verdict"]) == (0, "PASS"), result


# F9 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("suffix", "writer"), [(".docx", report_docx.write_docx), (".hwpx", report_hwpx.write_hwpx)])
def test_same_name_markdown_does_not_excuse_an_unfilled_slot(tmp_path, suffix, writer):
    prose = "측정표 검토란 {{검토자}}\n"
    md = tmp_path / "standalone.md"
    md.write_text(prose, encoding="utf-8")
    office = md.with_suffix(suffix)
    writer(office, prose)
    for companion in (True, False):
        if not companion:
            md.rename(tmp_path / "elsewhere.md")
        code, result = verify(office)
        assert (code, result["verdict"]) == (1, "FAIL"), (companion, result)
        assert any("자리표시자" in e and "{{검토자}}" in e for e in result["errors"]), result["errors"]


@pytest.mark.parametrize("fmt", ["docx", "hwpx"])
def test_ordinary_draft_exempts_only_braces_inside_measured_values(tmp_path, fmt):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    (ev / "{{year}}.txt").write_bytes(b"a")
    # The searched keyword is recorded in 선조사.json, so it is a measured value of this run too.
    assert report_draft.main([str(ev), "-o", str(out), "--keywords", "{{kw}}"]) == 0
    checks = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    assert (checks[fmt]["verdict"], checks[fmt]["errors"]) == ("PASS", []), checks[fmt]
    office = out / f"보고서초안.{fmt}"
    integrity = out / "무결성검증.json"
    records = [integrity, out / "선조사.json", out / "events.json"]
    assert "{{kw}}" in (out / "보고서초안.md").read_text(encoding="utf-8")
    code, result = verify(office, "--evidence", *records)
    assert (code, result["verdict"]) == (0, "PASS"), result
    # Without the measured record the verifier cannot tell the file name from a blank and says how.
    code, result = verify(office)
    assert code == 1 and any("{{year}}" in e and "--evidence" in e for e in result["errors"]), result
    # A real blank next to the measured name is still reported, alone.
    md = (out / "보고서초안.md").read_text(encoding="utf-8") + "\n검토자: {{검토자}}\n"
    (out / "보고서초안.md").write_text(md, encoding="utf-8")
    (report_docx.write_docx if fmt == "docx" else report_hwpx.write_hwpx)(office, md)
    code, result = verify(office, "--evidence", *records)
    assert code == 1, result
    assert [e for e in result["errors"] if "자리표시자" in e] == ["치환되지 않은 자리표시자가 있습니다 (['{{검토자}}'])"]


# D13 #10 -------------------------------------------------------------------------------------


def docx_with_title_token(path):
    docx_with_alt_text_token(path)
    with ZipFile(path) as package:
        parts = {name: package.read(name) for name in package.namelist()}
    doc = ET.fromstring(parts["word/document.xml"])
    pr = doc.find(".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr")
    pr.set("descr", "그림")
    pr.set("title", "{{review}}")
    parts["word/document.xml"] = ET.tostring(doc, encoding="utf-8")
    with ZipFile(path, "w") as package:
        for name, data in parts.items():
            package.writestr(name, data)
    return path


@pytest.mark.parametrize("make", [docx_with_alt_text_token, docx_with_title_token, hwpx_with_comment_token])
def test_token_left_in_a_forms_alt_text_fails_in_the_run_and_on_reverification(tmp_path, make):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    (ev / "{{year}}.txt").write_bytes(b"a")
    blank = make(tmp_path / ("blank.hwpx" if make is hwpx_with_comment_token else "blank.docx"))
    assert forms.template_residue(blank.read_bytes(), blank.suffix) == ["{{review}}"]
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(blank)]) == 1
    template = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))["template"]
    assert template["verdict"] == "FAIL" and any("{{review}}" in e for e in template["errors"])
    filled = out / f"양식초안{blank.suffix}"
    for extra in ([], ["--template-source", blank]):
        code, result = verify(filled, "--evidence", out / "무결성검증.json", *extra)
        assert (code, result["verdict"]) == (1, "FAIL"), (extra, result)
