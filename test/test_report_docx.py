"""Word draft, verifier and Windows evidence regressions. All inputs are synthetic."""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import report_docx
import report_draft

W = report_docx.W
FIXTURE = ROOT / "test/fixtures/ewf/synthetic.E01"
DIGEST = re.compile(r"\b(?:[0-9a-f]{64}|[0-9a-f]{40}|[0-9a-f]{32})\b")


def test_word_package(tmp_path):
    md = "# 한글 초안 & <검토>\n\n| 파일 | 상태 |\n| --- | --- |\n| 한글 \\| 표.txt | 미확인 |"
    path = tmp_path / "draft.docx"
    report_docx.write_docx(path, md)
    with ZipFile(path) as package:
        assert package.testzip() is None
        rels = ET.fromstring(package.read("_rels/.rels"))
        rel, = rels.findall("{*}Relationship")
        assert rel.get("Type").endswith("/officeDocument")
        target = rel.attrib["Target"]
        types = ET.fromstring(package.read("[Content_Types].xml"))
        assert types.find("{*}Default[@Extension='rels']").get("ContentType").endswith("relationships+xml")
        assert types.find(f"{{*}}Override[@PartName='/{target}']").get("ContentType") == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")
        doc = ET.fromstring(package.read(target))
    assert doc.find(".//" + W + "rFonts").get(W + "eastAsia") == "맑은 고딕"
    assert doc.find(".//" + W + "pgSz").attrib == {W + "w": "11906", W + "h": "16838"}
    assert doc.find(".//" + W + "pgMar").get(W + "left") == "1134"
    assert doc.find(".//" + W + "t").text == "한글 초안 & <검토>"
    rows = doc.findall(".//" + W + "tbl/" + W + "tr")
    assert [[t.text for t in row.iter(W + "t")] for row in rows] == [
        ["파일", "상태"], ["한글 | 표.txt", "미확인"]]
    first = path.read_bytes()
    report_docx.write_docx(path, md)
    assert path.read_bytes() == first


def test_synthetic_report_with_windows_inputs(tmp_path):
    root = tmp_path / "시험 증거"
    folder = root / ("긴 경로 " * 9 + "가") / ("깊은 경로 " * 9 + "나")
    while len(str(folder)) < 265:
        folder /= "자료 폴더"
    folder.mkdir(parents=True)
    image = folder / "합성 사본.E01"
    shutil.copyfile(FIXTURE, image)
    image.chmod(stat.S_IREAD)
    data = FIXTURE.read_bytes()
    hashes = [digest(data).hexdigest() for digest in (hashlib.md5, hashlib.sha1, hashlib.sha256)]
    case = tmp_path / "사건 정보.txt"
    case.write_bytes("사건명: 합성 문서 검증\r\n".encode("utf-8-sig"))
    refs = tmp_path / "참조.txt"
    refs.write_bytes(f"{hashes[2]}  {image.relative_to(root).as_posix()}\r\n".encode())
    out = tmp_path / "분석 결과"
    try:
        proc = subprocess.run([sys.executable, str(ROOT / "scripts/report_draft.py"), str(root),
                               "-o", str(out), "--case-info", str(case), "--hashes", str(refs)],
                              capture_output=True)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)
        assert image.read_bytes() == data
    finally:
        image.chmod(stat.S_IWRITE | stat.S_IREAD)
    integrity = json.loads((out / "무결성검증.json").read_text(encoding="utf-8"))
    assert [r["result"] for r in integrity["reference"]["entries"]] == ["match"]
    verify = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    assert verify["verdict"] == verify["docx"]["verdict"] == "PASS"
    docx = out / "보고서초안.docx"
    md = docx.with_suffix(".md").read_text(encoding="utf-8")
    with ZipFile(docx) as package:
        body = ET.fromstring(package.read("word/document.xml"))
    text = "\n".join(n.text or "" for n in body.iter(W + "t"))
    assert DIGEST.findall(text) == DIGEST.findall(md)
    assert all(h in text for h in hashes)
    assert text.count("미확인") == md.count("미확인")
    assert "합성 문서 검증" in text
    assert all(heading in text for heading in re.findall(r"^#+ (.+)", md, re.M))
    headers = [[t.text for t in table.find(W + "tr").iter(W + "t")]
               for table in body.iter(W + "tbl")]
    assert ["번호", "파일", "크기", "해시"] in headers
    assert ["검증 항목", "결과", "근거"] in headers
    assert ["시각", "파일"] in headers


def test_split_word_runs_and_companion_markdown(tmp_path):
    path = tmp_path / "draft.docx"
    path.with_suffix(".md").write_text("미확인", encoding="utf-8")
    report_docx.write_docx(path, "명백히 입증")
    with ZipFile(path) as package:
        parts = {name: package.read(name) for name in package.namelist()}
    doc = ET.fromstring(parts["word/document.xml"])
    para = doc.find(".//" + W + "p")
    para.find(".//" + W + "t").text = "명백히 "
    ET.SubElement(ET.SubElement(para, W + "r"), W + "t").text = "입증"
    parts["word/document.xml"] = ET.tostring(doc, encoding="utf-8")
    with ZipFile(path, "w") as package:
        for name, data in parts.items():
            package.writestr(name, data)
    result = report_draft.run_verify(path, [])
    assert result["verdict"] == "FAIL"
    assert result["forbidden_hits"] == ["명백히 입증"]
    assert any("Markdown" in error for error in result["errors"])


def test_no_docx(tmp_path):
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "memo.txt").write_text("synthetic", encoding="utf-8")
    out = tmp_path / "out"
    assert report_draft.main([str(ev), "--no-docx", "-o", str(out)]) == 0
    assert not (out / "보고서초안.docx").exists()
    assert "SHA-256만 싣는다" in (out / "보고서초안.md").read_text(encoding="utf-8")
    assert "docx" not in json.loads((out / "초안검증.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("tamper, expected", [
    ("명백히 입증", "금지 문구"), ("2099-01-01", "미래 시각"),
    ("MD5 " + "a" * 32, "근거 없는 해시"), ("SHA-1 " + "c" * 40, "근거 없는 해시"),
    ("SHA-256 " + "b" * 64, "근거 없는 해시"), ("", "Markdown"),
])
def test_docx_gate_reads_the_written_document(tmp_path, monkeypatch, tamper, expected):
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "memo.txt").write_bytes(b"synthetic")
    out = tmp_path / "out"
    write = report_docx.write_docx

    def altered(path, markdown):
        if tamper:
            write(path, markdown + "\n" + tamper)
        else:
            write(path, DIGEST.sub("", markdown, count=1))
    monkeypatch.setattr(report_docx, "write_docx", altered)
    assert report_draft.main([str(ev), "--docx", "-o", str(out)]) == 1
    result = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    assert result["verdict"] == result["docx"]["verdict"] == "FAIL"
    assert any(expected in error for error in result["docx"]["errors"])


def test_spaced_filename_with_mismatching_reference(tmp_path):
    ev = tmp_path / "evidence"
    ev.mkdir()
    name = "자료 사본.bin"
    (ev / name).write_bytes(b"synthetic")
    refs = tmp_path / "refs.txt"
    refs.write_text(f"{'1' * 64}  {name}\n", encoding="utf-8")
    out = tmp_path / "out"
    assert report_draft.main([str(ev), "-o", str(out), "--hashes", str(refs)]) == 1
    verify = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    # The reference mismatches. Both drafts correctly describe that mismatch.
    assert verify["verdict"] == verify["docx"]["verdict"] == "PASS"


def test_malformed_docx_fails_closed(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a ZIP")
    assert report_draft.run_verify(path, [])["verdict"] == "FAIL"


def check_locked_run(root, out):
    assert report_draft.main([str(root), "-o", str(out)]) == 1
    integ = json.loads((out / "무결성검증.json").read_text(encoding="utf-8"))
    assert integ["post_check"]["checked"] == 1
    assert integ["post_check"]["status"] == "incomplete"
    assert integ["post_check"]["unreadable"] == ["잠긴 파일.bin"]
    assert "열기 실패" in (out / "보고서초안.md").read_text(encoding="utf-8")
    text = report_docx.read_docx(out / "보고서초안.docx")
    assert "열기 실패" in text and hashlib.sha256(b"available").hexdigest() in text


def test_locked_file_does_not_abort_or_count_as_verified(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "ok.bin").write_bytes(b"available")
    locked = root / "잠긴 파일.bin"
    locked.write_bytes(b"locked")
    original = Path.open

    def open_file(path, *args, **kwargs):
        if path == locked:
            raise PermissionError(13, "Permission denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    check_locked_run(root, tmp_path / "out")


@pytest.mark.skipif(os.name != "nt", reason="Windows mandatory byte-range lock")
def test_live_windows_lock(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "ok.bin").write_bytes(b"available")
    locked = root / "잠긴 파일.bin"
    locked.write_bytes(b"locked")
    code = ("import msvcrt, sys\nwith open(sys.argv[1], 'r+b') as f:\n"
            "    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\n"
            "    print('locked', flush=True)\n    sys.stdin.readline()")
    with subprocess.Popen([sys.executable, "-c", code, str(locked)], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True) as child:
        try:
            assert child.stdout.readline().strip() == "locked"
            check_locked_run(root, tmp_path / "out")
        finally:
            child.communicate("\n", timeout=10)


def find_soffice():
    executable = shutil.which("soffice")
    if executable:
        return executable
    candidates = [Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")]
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "LibreOffice/program/soffice.exe")
    return next((str(path) for path in candidates if path.is_file()), None)


SOFFICE = find_soffice()


@pytest.mark.skipif(not SOFFICE, reason="soffice is unavailable")
def test_libreoffice_pdf(tmp_path):
    example = ROOT / "examples/nps-2009-canon2/output/보고서초안.docx"
    result = subprocess.run([SOFFICE, f"-env:UserInstallation={(tmp_path / 'profile').as_uri()}",
                             "--headless", "--convert-to", "pdf", "--outdir", str(tmp_path), str(example)],
                            capture_output=True, timeout=90)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (tmp_path / example.with_suffix(".pdf").name).read_bytes().startswith(b"%PDF-")
