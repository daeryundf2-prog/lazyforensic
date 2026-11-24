"""Run bundles, partial-scope exit codes and form provenance (D13 review items 4, 7, 10, 11). Synthetic inputs only."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import case_survey
import evidence_manifest
import report_docx
import report_draft
import report_hwpx as hw
import report_templates as forms

TEMPLATES = ROOT / "templates"
LISTING = report_draft.RUN_LISTING


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def tree_digests(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def deny_listing(monkeypatch, folder):
    """Deterministic access denial for one folder on every OS (no chmod or ACL needed)."""
    denied, real_scandir = folder.resolve(), os.scandir

    def scandir(path="."):
        if isinstance(path, (str, os.PathLike)) and Path(path).resolve() == denied:
            raise PermissionError(13, "Permission denied", str(denied))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)


def verify(draft, *extra):
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/verify_report.py"), str(draft), "--json", *map(str, extra)],
                          capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, json.loads(proc.stdout)


# 4 -------------------------------------------------------------------------------------------


def test_overlapping_runs_reserve_separate_folders_and_list_only_their_own_files(tmp_path, monkeypatch):
    case_a, case_b, out = tmp_path / "사건A", tmp_path / "사건B", tmp_path / "결과"
    for case, name in ((case_a, "only-A.txt"), (case_b, "only-B.txt")):
        case.mkdir()
        (case / name).write_bytes(name.encode())
    real_generate, nested = report_draft.generate, []

    def overlapping(root, out_dir, *args, **kwargs):
        # Run B completely after A has chosen its folder but before A writes anything, then drop a
        # file that no run published into A's folder. This is the worst interleaving of two processes.
        if root.name == "사건A":
            nested.append(report_draft.main([str(case_b), "-o", str(out)]))
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "stray.txt").write_bytes(b"not from this run")
        return real_generate(root, out_dir, *args, **kwargs)

    monkeypatch.setattr(report_draft, "generate", overlapping)
    assert report_draft.main([str(case_a), "-o", str(out)]) == 1  # the stray file is reported
    assert nested == [0]

    a_out, b_out = out, tmp_path / "결과_2"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("결과")) == ["결과", "결과_2"]
    published = {}
    for folder, case, own in ((a_out, "사건A", "only-A.txt"), (b_out, "사건B", "only-B.txt")):
        listing = read_json(folder / LISTING)
        assert listing["status"] == "complete" and listing["evidence_root"] == case
        assert [f["path"] for f in read_json(folder / "무결성검증.json")["files"]] == [own]
        published[folder] = {f["path"]: f["sha256"] for f in listing["files"]}
        on_disk = tree_digests(folder)
        # Independent digests of every listed file match, and nothing of the other run is listed.
        assert published[folder] == {name: on_disk[name] for name in published[folder]}
    assert read_json(a_out / LISTING)["not_from_this_run"] == ["stray.txt"]
    assert set(tree_digests(a_out)) == set(published[a_out]) | {LISTING, "stray.txt"}
    assert "not_from_this_run" not in read_json(b_out / LISTING)
    assert set(tree_digests(b_out)) == set(published[b_out]) | {LISTING}


def test_reservation_is_exclusive_even_for_an_existing_empty_folder(tmp_path):
    out = tmp_path / "결과"
    out.mkdir()  # e.g. created by another run's mkdir an instant before its reservation
    first = report_draft.reserve_output_dir(out)
    second = report_draft.reserve_output_dir(out)
    assert (first, second) == (out, tmp_path / "결과_2")
    assert read_json(first / LISTING)["status"] == read_json(second / LISTING)["status"] == "in_progress"
    (tmp_path / "결과_3").mkdir()
    (tmp_path / "결과_3" / "user.txt").write_bytes(b"x")  # a non-empty folder is never taken
    assert report_draft.reserve_output_dir(out) == tmp_path / "결과_4"


# 7 -------------------------------------------------------------------------------------------


def test_report_scan_and_manifest_share_the_partial_scope_exit_code(tmp_path, monkeypatch, capsys):
    ev = tmp_path / "증거"
    (ev / "closed").mkdir(parents=True)
    (ev / "closed" / "nested.txt").write_bytes(b"sample")
    (ev / "open.txt").write_bytes(b"sample")
    results = {}

    def run_all():
        codes = {"report": report_draft.main([str(ev), "-o", str(tmp_path / "report")]),
                 "scan": case_survey.main([str(ev), "-o", str(tmp_path / "scan.json")]),
                 "manifest": evidence_manifest.main([str(ev), "-o", str(tmp_path / "manifest.json")])}
        return codes, capsys.readouterr().err

    results["complete"] = run_all()
    deny_listing(monkeypatch, ev / "closed")
    results["partial"] = run_all()

    codes, err = results["complete"]
    assert codes == {"report": 0, "scan": 0, "manifest": 0}
    assert "조사 범위 미확인" not in err
    codes, err = results["partial"]
    assert codes == {"report": 1, "scan": 1, "manifest": 1}
    assert err.count("조사 범위 미확인(부분 결과): 열거하지 못한 폴더 1개 (closed)") == 3

    # The machine-readable records agree with the exit code.
    assert read_json(tmp_path / "report_2" / "무결성검증.json")["scope"]["status"] == "partial"
    scan = read_json(tmp_path / "scan.json")
    receipt = scan["processing_receipt"]
    assert scan["scope"]["complete"] is False
    assert receipt["exit_code"] == 1 and receipt["status"] == "partial"
    assert receipt["warnings"][0].startswith("Incomplete scope: 1 folder(s)")
    assert read_json(tmp_path / "manifest.json")["complete"] is False


# 10 ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["docx", "hwpx"])
def test_measured_name_that_looks_like_a_token_is_preserved_and_passes(tmp_path, fmt):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    (ev / "{{year}}.txt").write_bytes(b"a")
    template = TEMPLATES / f"report_form.{fmt}"
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(template)]) == 0
    checks = read_json(out / "초안검증.json")
    for result in (checks, checks["docx"], checks["hwpx"], checks["template"]):
        assert (result["verdict"], result["errors"]) == ("PASS", []), result
    form = out / f"양식초안.{fmt}"
    part = "word/document.xml" if fmt == "docx" else "Contents/section0.xml"
    with ZipFile(form) as package:
        assert "{{year}}.txt" in package.read(part).decode("utf-8")
    # Standalone re-verification: with the blank form the name is a measured value; without it the
    # verifier cannot tell and says how to distinguish.
    code, result = verify(form, "--evidence", out / "무결성검증.json", "--template-source", template)
    assert (code, result["verdict"]) == (0, "PASS"), result
    code, result = verify(form, "--evidence", out / "무결성검증.json")
    assert code == 1 and any("--template-source" in e for e in result["errors"])


def docx_with_alt_text_token(path):
    report_docx.write_docx(path, "감정서 {{사건번호}}\n그림")
    with ZipFile(path) as package:
        parts = {name: package.read(name) for name in package.namelist()}
    doc = ET.fromstring(parts["word/document.xml"])
    run = doc.findall(".//" + report_docx.W + "r")[-1]
    inline = ET.SubElement(report_docx.word(run, "drawing"),
                           "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline")
    ET.SubElement(inline, inline.tag.replace("inline", "docPr"), id="1", name="figure", descr="{{review}}")
    parts["word/document.xml"] = ET.tostring(doc, encoding="utf-8")
    with ZipFile(path, "w") as package:
        for name, data in parts.items():
            package.writestr(name, data)
    return path


def hwpx_with_comment_token(path):
    hw.write_hwpx(path, "감정서 {{사건번호}}")
    with ZipFile(path) as package:
        parts = [(info, package.read(info)) for info in package.infolist()]
    with ZipFile(path, "w") as package:
        for info, data in parts:
            if info.filename == "Contents/section0.xml":
                sec = ET.fromstring(data)
                run = sec.find(".//" + hw.HP + "run")
                ET.SubElement(ET.SubElement(run, hw.HP + "pic"), hw.HP + "shapeComment").text = "{{review}}"
                data = ET.tostring(sec, encoding="utf-8")
            package.writestr(info, data)
    return path


@pytest.mark.parametrize("make", [docx_with_alt_text_token, hwpx_with_comment_token])
def test_token_left_in_the_original_form_still_fails(tmp_path, make):
    ev, out = tmp_path / "증거", tmp_path / "결과"
    ev.mkdir()
    (ev / "{{year}}.txt").write_bytes(b"a")
    blank = make(tmp_path / ("blank.docx" if make is docx_with_alt_text_token else "blank.hwpx"))
    assert forms.template_residue(blank.read_bytes(), blank.suffix) == ["{{review}}"]
    assert report_draft.main([str(ev), "-o", str(out), "--template", str(blank)]) == 1
    template = read_json(out / "초안검증.json")["template"]
    assert template["verdict"] == "FAIL"
    assert any("자리표시자" in e and "{{review}}" in e for e in template["errors"]), template["errors"]


# 11 ------------------------------------------------------------------------------------------


def repackage(source, destination, comment):
    """Same members and visible body, different package bytes."""
    with ZipFile(source) as blank, ZipFile(destination, "w") as copy:
        copy.comment = comment
        for info in blank.infolist():
            copy.writestr(info, blank.read(info))
    return destination


@pytest.mark.parametrize("fmt", ["docx", "hwpx"])
def test_receipt_records_the_input_form_and_binds_it_to_the_draft(tmp_path, fmt):
    ev = tmp_path / "증거"
    ev.mkdir()
    (ev / "sample.bin").write_bytes(b"s")
    blanks = [repackage(TEMPLATES / f"report_form.{fmt}", tmp_path / f"양식{label}.{fmt}", f"synthetic {label}".encode())
              for label in "AB"]
    records = []
    for blank in blanks:
        out = tmp_path / ("결과" + blank.stem)
        assert report_draft.main([str(ev), "-o", str(out), "--template", str(blank)]) == 0
        data = blank.read_bytes()
        expected = {"name": blank.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        template = read_json(out / "초안검증.json")["template"]
        assert template["verdict"] == "PASS" and template["input_template"] == expected
        assert template["recorded_template_sha256"] == expected["sha256"]
        draft = out / f"양식초안.{fmt}"
        # Each finished file is listed with its own digest and independently re-verifies.
        listed = {f["path"]: f["sha256"] for f in read_json(out / LISTING)["files"]}
        assert listed[draft.name] == hashlib.sha256(draft.read_bytes()).hexdigest()
        assert forms.digest_path(draft).name in listed
        code, result = verify(draft, "--evidence", out / "무결성검증.json", "--template-source", blank)
        assert (code, result["input_template"]) == (0, expected)
        records.append((expected, draft))
    (a, draft_a), (b, draft_b) = records
    assert a["sha256"] != b["sha256"]
    # The filled body is identical, so only the recorded input digest tells the two forms apart.
    body_a, bound_a = forms.read_reference(forms.digest_path(draft_a))
    body_b, bound_b = forms.read_reference(forms.digest_path(draft_b))
    assert body_a == body_b and (bound_a, bound_b) == (a["sha256"], b["sha256"])
    code, result = verify(draft_a, "--template-source", blanks[1])
    assert code == 1 and any("다름" in e for e in result["errors"])
