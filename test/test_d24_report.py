# -*- coding: utf-8 -*-
"""D24 #10: two reference tables in a row must print as two tables.

Word and LibreOffice merge directly adjacent tables. The reference-list table and the reference-entry
table then became one table, and the entry rows that continued on the next page carried the list's header
("참조 목록 / SHA-256 / 바이트 …") above the tail of a split hash. The DOCX now separates adjacent tables
and keeps every row (a wrapped 64-character hash) on one page. Synthetic inputs only.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import report_docx  # noqa: E402
import report_draft  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def run_report(*args):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        code = report_draft.main([str(a) for a in args])
    return code, buf.getvalue()


def reference_case(tmp_path: Path, count: int = 36) -> tuple[Path, Path, list[str]]:
    evidence = tmp_path / "합성자료"
    evidence.mkdir()
    lines, digests = [], []
    for i in range(count):
        item = evidence / f"교대기록_{i:02d}.log"
        item.write_bytes(f"합성 교대 기록 {i}\n".encode())
        digest = hashlib.sha256(item.read_bytes()).hexdigest()
        digests.append(digest)
        lines.append(f"{digest}  {item.name}")
    refs = tmp_path / "참조목록.txt"
    refs.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return evidence, refs, digests


def body_children(docx: Path) -> list[ET.Element]:
    with ZipFile(docx) as package:
        return list(ET.fromstring(package.read("word/document.xml")).find(W + "body"))


def test_adjacent_tables_are_separated_and_rows_do_not_split(tmp_path):
    evidence, refs, _ = reference_case(tmp_path, 3)
    out = tmp_path / "out"
    code, log = run_report(evidence, "-o", out, "--hashes", refs)
    assert code == 0, log
    children = body_children(out / "보고서초안.docx")
    tables = [i for i, node in enumerate(children) if node.tag == W + "tbl"]
    adjacent = [(a, b) for a, b in zip(tables, tables[1:]) if b == a + 1]
    assert adjacent == [], "two w:tbl elements in a row merge into one table when printed"
    first_cells = {i: "".join(children[i].find(W + "tr").itertext()) for i in tables}
    lists = [i for i, text in first_cells.items() if text.startswith("참조 목록")]
    entries = [i for i, text in first_cells.items() if text.startswith("참조 항목")]
    assert len(lists) == len(entries) == 1 and entries[0] == lists[0] + 2
    assert children[lists[0] + 1].tag == W + "p" and "".join(children[lists[0] + 1].itertext()) == ""
    for i in tables:
        for row in children[i].findall(W + "tr"):
            assert row.find(W + "trPr/" + W + "cantSplit") is not None
    # The verifier compares the DOCX body with the Markdown draft, separator included.
    assert "초안 검증: PASS" in log
    md = (out / "보고서초안.md").read_text(encoding="utf-8")
    assert report_docx.read_docx(out / "보고서초안.docx") == report_docx.draft_text(md, docx=True)


def _tools() -> str:
    found = os.environ.get("LF_SOFFICE") or shutil.which("soffice") or shutil.which("libreoffice")
    missing = [t for t in ("pdftotext",) if not shutil.which(t)]
    if not found or missing:
        if os.environ.get("LF_REQUIRE_PRINT_TOOLS"):
            pytest.fail(f"print tools required but missing: {missing or 'soffice'}")
        pytest.skip("needs LibreOffice (soffice) and Poppler (pdftotext)")
    return found


def test_printed_entry_rows_keep_their_own_header_and_whole_hashes(tmp_path):
    office = _tools()
    evidence, refs, digests = reference_case(tmp_path)
    out = tmp_path / "out"
    code, log = run_report(evidence, "-o", out, "--hashes", refs)
    assert code == 0, log
    printed = tmp_path / "printed"
    printed.mkdir()
    env = dict(os.environ)
    if os.environ.get("LF_PRINT_FONTCONFIG"):
        env["FONTCONFIG_FILE"] = os.environ["LF_PRINT_FONTCONFIG"]
    proc = subprocess.run([office, f"-env:UserInstallation={(printed / 'profile').as_uri()}", "--headless",
                           "--convert-to", "pdf", "--outdir", str(printed), str(out / "보고서초안.docx")],
                          capture_output=True, text=True, env=env, timeout=300)
    pdf = printed / "보고서초안.pdf"
    assert pdf.is_file(), proc.stdout + proc.stderr
    text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True, encoding="utf-8").stdout
    pages = ["".join(page.split()) for page in text.split("\f")]
    # Entry rows say how they matched; the file-hash table earlier in the report lists the same digests.
    entry_pages = [n for n, page in enumerate(pages) if "경로일치" in page]
    assert len(entry_pages) > 1, "the entry table must continue on another page for this check"
    for n in entry_pages:
        assert "참조항목" in pages[n], f"page {n + 1} continues the entry table without its own header"
    list_pages = [n for n, page in enumerate(pages) if "참조목록SHA-256" in page]
    assert len(list_pages) == 1, "the reference-list header repeats on the entry table's pages"
    for digest in digests:  # once in the file-hash table, once in the entry table — each whole on one page
        assert sum(page.count(digest) for page in pages) >= 2, f"hash split across pages: {digest}"
