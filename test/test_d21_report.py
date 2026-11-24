# -*- coding: utf-8 -*-
"""Report regressions from the D21 review (synthetic inputs only).

#3 printed PDFs must draw the Korean text they carry, #5 negated or comparative claims go to human
review, #7 the methods section follows what actually ran and each photo's offset state, #8 the shipped
HWPX template's package members are not signature warnings. (#4 is in test_law_cache_binding.py.)

The print tests need Poppler (and the end-to-end one LibreOffice and Pillow). They skip where those tools
are missing; the CI print job sets LF_REQUIRE_PRINT_TOOLS=1 so a missing tool fails there instead.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import archive_survey  # noqa: E402
import korean_morph_forensic  # noqa: E402
import print_check  # noqa: E402
import report_docx  # noqa: E402
import report_draft  # noqa: E402
import report_hwpx  # noqa: E402

GATE = ["--morph-grounding", "--high-fidelity", "--strict"]
TITLE = "디지털포렌식 분석 보고서 (초안)"


def run_report(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = report_draft.main([str(a) for a in args])
    return code, buf.getvalue()


def need(*tools):
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        if os.environ.get("LF_REQUIRE_PRINT_TOOLS"):
            pytest.fail(f"print tools required but missing: {missing}")
        pytest.skip(f"needs {', '.join(missing)}")


def documents(out: Path) -> dict[str, str]:
    """The plain draft and the two office drafts as text."""
    return {"md": (out / "보고서초안.md").read_text(encoding="utf-8"),
            "docx": report_docx.read_docx(out / "보고서초안.docx"),
            "hwpx": report_hwpx.read_hwpx(out / "보고서초안.hwpx")}


# #3 ------------------------------------------------------------------------------------------

def synthetic_pdf(path: Path, text: str, procs: dict[str, str]) -> None:
    """One line of Hangul drawn with a Type 3 font. procs maps each character to its glyph drawing
    (glyph space 0..1000); the ToUnicode map keeps the text layer correct whatever the glyph draws."""
    chars = sorted(set(text))
    codes = {c: 65 + i for i, c in enumerate(chars)}

    def stream(data: str) -> str:
        return f"<< /Length {len(data.encode())} >>\nstream\n{data}\nendstream"

    objs = {1: "<< /Type /Catalog /Pages 2 0 R >>", 2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 120] /Resources << /Font << /F1 5 0 R >> >> "
               "/Contents 4 0 R >>",
            4: stream("BT /F1 24 Tf 40 60 Td <" + "".join(f"{codes[c]:02X}" for c in text) + "> Tj ET")}
    procs_ref = {}
    for i, c in enumerate(chars):
        objs[6 + i] = stream("1000 0 0 0 1000 1000 d1 " + procs[c])
        procs_ref[c] = 6 + i
    cmap = 6 + len(chars)
    objs[cmap] = stream("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /S def "
                        "1 begincodespacerange <00> <FF> endcodespacerange "
                        f"{len(chars)} beginbfchar " + " ".join(f"<{codes[c]:02X}> <{ord(c):04X}>" for c in chars)
                        + " endbfchar endcmap CMapName currentdict /CMap defineresource pop end end")
    objs[5] = ("<< /Type /Font /Subtype /Type3 /FontBBox [0 0 1000 1000] /FontMatrix [0.001 0 0 0.001 0 0] "
               "/CharProcs << " + " ".join(f"/g{codes[c]} {procs_ref[c]} 0 R" for c in chars) + " >> "
               "/Encoding << /Type /Encoding /Differences [65 " + " ".join(f"/g{codes[c]}" for c in chars) + "] >> "
               f"/FirstChar 65 /LastChar {64 + len(chars)} /Widths [{' '.join('1000' for _ in chars)}] "
               f"/ToUnicode {cmap} 0 R /Resources << >> >>")
    out, offsets = b"%PDF-1.4\n", {}
    for number in sorted(objs):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n{objs[number]}\nendobj\n".encode()
    start, size = len(out), max(objs) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offsets[n]:010d} 00000 n \n".encode() for n in range(1, size))
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    path.write_bytes(out)


SHAPES = ["100 100 800 800 re f", "100 100 m 900 100 l 500 900 l f", "100 100 200 800 re f 700 100 200 800 re f",
          "100 100 200 800 re f 100 100 800 200 re f", "450 100 100 800 re f 100 450 800 100 re f"]
EMPTY_BOX = "100 100 800 60 re f 100 840 800 60 re f 100 100 60 800 re f 840 100 60 800 re f"
WORD = "가나다라마"


def test_print_check_without_poppler_is_unverified_not_a_pass(tmp_path, monkeypatch):
    pdf = tmp_path / "print.pdf"
    synthetic_pdf(pdf, WORD, dict(zip(WORD, SHAPES)))
    monkeypatch.setattr(print_check.shutil, "which", lambda name: None)
    result = print_check.check(pdf)
    assert result["status"] == "unverified" and "Poppler" in result["reason"]
    with contextlib.redirect_stdout(io.StringIO()):
        assert print_check.main([str(pdf)]) == 3


@pytest.mark.parametrize(("label", "procs", "status"), [
    ("every glyph drawn", dict(zip(WORD, SHAPES)), "pass"),
    ("one blank glyph", {**dict(zip(WORD, SHAPES)), "다": ""}, "fail"),
    ("one substitute box for every character", {c: EMPTY_BOX for c in WORD}, "fail"),
])
def test_print_check_looks_at_the_rendered_glyphs_not_the_text_layer(tmp_path, label, procs, status):
    need("pdftotext", "pdftoppm", "pdffonts")
    pdf = tmp_path / "print.pdf"
    synthetic_pdf(pdf, WORD, procs)
    result = print_check.check(pdf, expect=[WORD])
    # The text layer is the same in all three files; only the drawing differs.
    assert result["missing_text"] == [] and result["words"] == 1 and result["hangul_chars"] == 5
    assert result["status"] == status, (label, result)
    if label == "one blank glyph":
        assert result["blank"] == [{"page": 1, "word": WORD, "char": "다"}]
    if label.startswith("one substitute"):
        assert result["substitute_glyphs"] == sorted(WORD)


def soffice():
    found = os.environ.get("LF_SOFFICE") or shutil.which("soffice") or shutil.which("libreoffice")
    if not found:
        if os.environ.get("LF_REQUIRE_PRINT_TOOLS"):
            pytest.fail("LibreOffice required but missing")
        pytest.skip("needs LibreOffice (soffice)")
    return found


def convert(office: str, docx: Path, out: Path, fontconfig: Path | None) -> Path:
    out.mkdir()
    env = dict(os.environ)
    if fontconfig is not None:
        env["FONTCONFIG_FILE"] = str(fontconfig)
    proc = subprocess.run([office, f"-env:UserInstallation={(out / 'profile').as_uri()}", "--headless",
                           "--convert-to", "pdf", "--outdir", str(out), str(docx)],
                          capture_output=True, text=True, env=env, timeout=300)
    pdf = out / (docx.stem + ".pdf")
    assert pdf.is_file(), proc.stdout + proc.stderr
    return pdf


def photo_case(tmp_path: Path) -> tuple[Path, Path]:
    """A log and two photos (+09:00 and a blank offset tag), plus a reference list naming the log only."""
    if os.environ.get("LF_REQUIRE_PRINT_TOOLS"):
        import PIL.Image as image_mod  # the print job installs Pillow; a missing one must fail there
    else:
        image_mod = pytest.importorskip("PIL.Image")
    evidence = tmp_path / "합성자료"
    (evidence / "사진").mkdir(parents=True)
    (evidence / "기록").mkdir()
    for name, offset in (("파랑_01.jpg", "+09:00"), ("하양_02.jpg", "   ")):
        tags = image_mod.Exif()
        tags[0x8769] = {0x9003: "2024:01:01 09:00:00", 0x9011: offset, 0x9291: "000000200"}
        image_mod.new("RGB", (8, 8), "blue").save(evidence / "사진" / name, exif=tags)
    log = evidence / "기록" / "인수기록.log"
    log.write_bytes("09:00 인수 기록\r\n09:05 봉인 상태 확인\r\n".encode("cp949"))
    refs = tmp_path / "refs.txt"
    refs.write_text(f"{hashlib.sha256(log.read_bytes()).hexdigest()}  기록/인수기록.log\n", encoding="utf-8")
    return evidence, refs


def test_printed_report_draws_the_korean_title_file_name_and_hash(tmp_path):
    # D21 #3: the same DOCX printed with and without a Korean font. Only the glyph check tells them apart;
    # both PDFs carry the Korean text in their text layer.
    need("pdftotext", "pdftoppm", "pdffonts")
    office = soffice()
    evidence, refs = photo_case(tmp_path)
    out = tmp_path / "out"
    code, log = run_report(evidence, "-o", out, "--hashes", refs)
    assert code == 0, log
    docx = out / "보고서초안.docx"
    digest = hashlib.sha256((evidence / "기록/인수기록.log").read_bytes()).hexdigest()
    expect = ["인수기록.log", digest]

    configured = os.environ.get("LF_PRINT_FONTCONFIG")  # where the converter's default lacks a Korean font
    printed = convert(office, docx, tmp_path / "with-font", Path(configured) if configured else None)
    result = print_check.check(printed, docx, expect)
    assert result["status"] == "pass", {k: result[k] for k in ("blank", "missing_text", "substitute_glyphs", "fonts")}
    assert result["title"] == TITLE and result["hangul_chars"] > 100
    text = subprocess.run(["pdftotext", str(printed), "-"], capture_output=True, text=True, encoding="utf-8").stdout
    flat = "".join(text.split())
    # D21 #7 in the printed copy too: no EWF procedure, one valid offset and one blank tag.
    assert "미디어해시와대조" not in flat and "이미지획득기록" not in flat
    assert "유효한오프셋1건,빈오프셋태그1건" in flat

    latin = tmp_path / "latin-fonts"
    latin.mkdir()
    dejavu = Path("/usr/share/fonts/truetype/dejavu")
    dirs = "".join(f"<dir>{d}</dir>" for d in (latin, dejavu) if d.is_dir())
    conf = tmp_path / "latin-only.conf"
    conf.write_text(f'<?xml version="1.0"?><fontconfig>{dirs}<cachedir>{tmp_path / "fc-cache"}</cachedir></fontconfig>',
                    encoding="utf-8")
    blank = print_check.check(convert(office, docx, tmp_path / "without-font", conf), docx, expect)
    assert blank["missing_text"] == [], "the text layer still carries every string"
    assert blank["status"] == "fail" and (blank["blank"] or blank["substitute_glyphs"]), blank
    with contextlib.redirect_stdout(io.StringIO()) as shown:
        assert print_check.main([str(tmp_path / "without-font" / "보고서초안.pdf"), "--docx", str(docx)]) == 1
    assert "한글 글꼴" in shown.getvalue()


# #5 ------------------------------------------------------------------------------------------

EVIDENCE = {"범위": "두 상자의 전체 목록이다. 각 색의 표시는 정확히 한 곳에만 있다.",
            "observations": ["청색 표시는 상자 A에 있다.", "백색 표시는 상자 B에 있다."]}


def verify_claim(tmp_path, claim):
    evidence = tmp_path / "observed.json"
    evidence.write_text(json.dumps(EVIDENCE, ensure_ascii=False), encoding="utf-8")
    draft = tmp_path / "draft.md"
    draft.write_text(f"# 감정 결과\n<evidence>청색 표시는 상자 A에 있다.</evidence>\n{claim}\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/verify_report.py"), str(draft), "--json",
                           "--evidence", str(evidence), *GATE], capture_output=True, text=True, encoding="utf-8")
    result = json.loads(proc.stdout)
    claim_result, = result["claim_binding"]["claims"]
    return proc.returncode, result, claim_result


@pytest.mark.parametrize(("claim", "verdict", "status", "position"), [
    ("청색 표시는 상자 A에 있다.", "PASS", "bound", None),            # true positive
    ("청색 표시는 상자 B에 있다.", "FAIL", "unbound", None),          # clearly wrong positive
    ("청색 표시는 상자 B에 있지 않다.", "WARN", "review", "crossed"),  # true negation
    ("청색 표시는 상자 B에서 관찰되지 않았다.", "WARN", "review", "crossed"),
    ("청색 표시는 상자 A에 있지 않다.", "WARN", "review", "same-record"),  # false negation, same record
])
def test_negated_claims_go_to_human_review_instead_of_pass_or_fail(tmp_path, claim, verdict, status, position):
    code, result, item = verify_claim(tmp_path, claim)
    assert (result["verdict"], item["status"]) == (verdict, status), result
    assert code == (0 if verdict == "PASS" else 1)
    if status == "review":
        assert item["relation"] == ["부정"] and item["position"] == position
        assert result["errors"] == [], result["errors"]  # neither a binding error nor a lexical-score error
        assert any("판정 불가(사람 검토)" in w and "부정" in w for w in result["warnings"]), result["warnings"]
    if status == "unbound":
        assert item["crossed"] == ["청색", "b"]
        assert any("주장-근거 결합 불일치" in e for e in result["errors"])


def test_comparison_claims_go_to_human_review(tmp_path):
    _, result, item = verify_claim(tmp_path, "청색 표시는 백색 표시보다 상자 A에 있다.")
    assert (result["verdict"], item["status"], item["relation"]) == ("WARN", "review", ["비교"]), result
    assert result["errors"] == []


def test_fallback_morphemes_skip_existence_and_negation_predicates(monkeypatch):
    # Kiwi tags 있다·않다 as verbs, not content; the regex fallback used without Kiwi must agree.
    monkeypatch.setattr(korean_morph_forensic, "get_kiwi_instance", lambda: None)
    terms = korean_morph_forensic.extract_forensic_morphemes("청색 표시는 상자 B에 있지 않다. 흔적이 없었다. 원본이 아니다.")
    assert {"있지", "않다", "없었다", "아니다"}.isdisjoint(terms) and {"청색", "상자", "흔적"} <= set(terms), terms


# #7 ------------------------------------------------------------------------------------------

def test_methods_and_exif_limits_follow_what_ran_and_each_photo(tmp_path):
    evidence, refs = photo_case(tmp_path)
    out = tmp_path / "out"
    code, log = run_report(evidence, "-o", out, "--hashes", refs)
    assert code == 0, log
    for kind, text in documents(out).items():
        assert "미디어 해시와 대조" not in text and "이미지 획득 기록" not in text, kind
        assert "시간대가 기록되지 않는다" not in text, kind
        assert "파일 수정 시각·EXIF 촬영 시각을 모아 타임라인을 만들었다." in text, kind
        assert "이 사건의 촬영 시각 2건: 유효한 오프셋 1건, 빈 오프셋 태그 1건." in text, kind
        assert "지역 시간대 이름(예: Asia/Seoul)이 없고" in text, kind


def test_methods_name_the_ewf_actually_compared_with_a_raw_reference(tmp_path):
    # The positive case: a raw-image reference really was compared with the EWF media hash.
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    shutil.copy(ROOT / "test/fixtures/ewf/synthetic.E01", evidence)
    out = tmp_path / "out"
    code, _ = run_report(evidence, "-o", out)
    assert code == 0
    media = json.loads((out / "무결성검증.json").read_text(encoding="utf-8"))["images"][0]["computed_hashes"]["md5"]
    refs = tmp_path / "refs.txt"
    refs.write_text(f"{media}  synthetic.raw\n", encoding="utf-8")
    out = tmp_path / "out2"
    code, log = run_report(evidence, "-o", out, "--hashes", refs)
    assert code == 0, log
    events = json.loads((out / "events.json").read_text(encoding="utf-8"))
    for kind, text in documents(out).items():
        assert "원시 이미지(.raw/.dd 등) 항목 1건은 이름이 같은 EWF(synthetic.E01)의 미디어 해시와 대조했다." in \
            text.replace("`", ""), kind
        assert ("이미지 획득 기록" in text) == any(e["source"] == "EWF 헤더" for e in events), kind


def test_method_and_limit_sentences_from_events():
    assert report_draft.timeline_method([]) == "시각 정보를 얻은 항목이 없어 타임라인은 만들지 않았다."
    events = [{"source": "EWF 헤더", "timestamp": "2024-01-01T00:00:00+00:00"},
              {"source": "EXIF", "timestamp": "2024-01-01T09:00:00", "offset_status": "malformed"},
              {"source": "EXIF", "timestamp": "2024-01-01T09:00:00"},
              {"source": "파일시스템(mtime)", "timestamp": "2024-01-01T09:00:00+09:00"}]
    assert report_draft.timeline_method(events) == "파일 수정 시각·EXIF 촬영 시각·이미지 획득 기록을 모아 타임라인을 만들었다."
    text = report_draft.exif_limitation(events)
    assert "촬영 시각 2건: 형식 오류 오프셋 1건, 오프셋 태그 없음 1건." in text and "KST로 가정" in text
    valid = report_draft.exif_limitation([{"source": "EXIF", "timestamp": "2024-01-01T09:00:00+09:00",
                                           "offset_status": "valid"}])
    assert "유효한 오프셋 1건." in valid and "KST로 가정" not in valid
    assert report_draft.raw_media_method([{"result": "match", "matched_by": "path", "file": "a.log"}]) == ""


# #8 ------------------------------------------------------------------------------------------

def rewrite(source: Path, target: Path, member: str, data: bytes) -> Path:
    with ZipFile(source) as src, ZipFile(target, "w") as out:
        for info in src.infolist():
            out.writestr(info, data if info.filename == member else src.read(info))
    return target


def flagged(path: Path) -> dict:
    return {m["name"]: m["flags"] for m in archive_survey.audit_archive(path)["members"] if m["flags"]}


def test_shipped_hwpx_template_members_are_not_signature_warnings(tmp_path):
    template = ROOT / "templates/report_form.hwpx"
    assert flagged(template) == {}
    member = next(m for m in archive_survey.audit_archive(template)["members"] if m["name"] == "Contents/content.hpf")
    assert (member["signature"], member["package_member"]["root"]) == ("MATCH", "{http://www.idpf.org/2007/opf/}package")

    # The same path holding different XML is recorded as a structural difference.
    other = rewrite(template, tmp_path / "other.hwpx", "Contents/content.hpf",
                    b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"/>')
    assert flagged(other) == {"Contents/content.hpf": [
        "구조불일치(XML 루트 {http://www.w3.org/1999/xhtml}html, 기대 {http://www.idpf.org/2007/opf/}package)"]}
    broken = rewrite(template, tmp_path / "broken.hwpx", "Contents/content.hpf", b"<?xml version=")
    assert flagged(broken)["Contents/content.hpf"][0].startswith("구조불일치(XML 아님")

    # OOXML relationship parts with an XML declaration are package members too.
    rels = (b'<?xml version="1.0" encoding="UTF-8"?><Relationships '
            b'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
    docx = rewrite(ROOT / "templates/report_form.docx", tmp_path / "form.docx", "_rels/.rels", rels)
    assert flagged(docx) == {}
    swapped = rewrite(ROOT / "templates/report_form.docx", tmp_path / "swapped.docx", "_rels/.rels",
                      b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
    assert list(flagged(swapped)) == ["_rels/.rels"]

    # End to end: the template copied into the evidence (as colleagues do) gives no caution in the draft.
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    shutil.copy(template, evidence / "대조양식.hwpx")
    out = tmp_path / "out"
    code, log = run_report(evidence, "-o", out, "--template", evidence / "대조양식.hwpx")
    assert code == 0, log
    for kind, text in documents(out).items():
        assert "시그니처불일치" not in text and "content.hpf" not in text, kind
    assert flagged(out / "보고서초안.hwpx") == {} and flagged(out / "보고서초안.docx") == {}
