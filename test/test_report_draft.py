# -*- coding: utf-8 -*-
"""lazyforensic report: E01 판독, 참조 해시 대조, 보고서 초안 생성의 소비자 관점 테스트.

E01 픽스처(test/fixtures/ewf)는 libewf ewfacquire 20140816으로 합성 원시 데이터
(_synthetic_media)를 획득한 것이다 — 실제 사건 데이터 없음.
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ewf_image  # noqa: E402
import hash_reference  # noqa: E402
import report_draft  # noqa: E402

FIX = ROOT / "test" / "fixtures" / "ewf"


def _synthetic_media() -> bytes:
    """픽스처 E01을 만든 원시 데이터 1 MiB를 재현한다."""
    out = bytearray(("LazyForensic synthetic test media 합성 시험 매체\n" * 4000).encode("utf-8")[:64 * 1024])
    x = b"seed"
    while len(out) < 80 * 1024:
        x = hashlib.sha256(x).digest()
        out += x
    out += b"\x00" * (1024 * 1024 - len(out))
    return bytes(out)


MEDIA = _synthetic_media()
MEDIA_MD5 = hashlib.md5(MEDIA).hexdigest()
MEDIA_SHA1 = hashlib.sha1(MEDIA).hexdigest()
MEDIA_SHA256 = hashlib.sha256(MEDIA).hexdigest()


class EwfImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_segment_compressed_image_verifies_and_reads_case_fields(self):
        res = ewf_image.examine(FIX / "synthetic.E01")
        self.assertEqual(res["status"], "verified")
        self.assertEqual(res["computed_hashes"]["md5"], MEDIA_MD5)
        self.assertEqual(res["computed_hashes"]["sha1"], MEDIA_SHA1)
        self.assertEqual(res["computed_hashes"]["sha256"], MEDIA_SHA256)
        self.assertEqual(res["comparison"], {"md5": "match", "sha1": "match"})
        self.assertEqual(res["media"]["media_size"], len(MEDIA))
        acq = res["acquisition"]
        self.assertEqual(acq["case_number"], "TEST-2026-001")
        self.assertEqual(acq["evidence_number"], "EV-01")
        self.assertEqual(acq["examiner"], "test examiner")

    def test_multi_segment_uncompressed_image_is_read_across_segments(self):
        for name in ("split.E01", "split.E02"):
            (self.tmp / name).write_bytes(gzip.decompress((FIX / f"{name}.gz").read_bytes()))
        res = ewf_image.examine(self.tmp / "split.E01")
        self.assertEqual(res["segments"], ["split.E01", "split.E02"])
        self.assertEqual(res["status"], "verified")
        self.assertEqual(res["computed_hashes"]["sha256"], MEDIA_SHA256)

    def test_missing_last_segment_is_not_reported_as_verified(self):
        (self.tmp / "split.E01").write_bytes(gzip.decompress((FIX / "split.E01.gz").read_bytes()))
        res = ewf_image.examine(self.tmp / "split.E01")
        self.assertEqual(res["status"], "unsupported")

    def test_corrupted_chunk_is_a_read_error(self):
        data = bytearray((FIX / "synthetic.E01").read_bytes())
        data[12000:12004] = b"\x00\x00\x00\x00"
        p = self.tmp / "bad.E01"
        p.write_bytes(bytes(data))
        self.assertEqual(ewf_image.examine(p)["status"], "read_error")

    def test_tampered_stored_hash_is_a_mismatch(self):
        data = bytearray((FIX / "synthetic.E01").read_bytes())
        stored = bytes.fromhex(MEDIA_MD5)
        idx = data.rfind(stored)
        self.assertGreater(idx, 0)
        data[idx] ^= 0xFF
        p = self.tmp / "tampered.E01"
        p.write_bytes(bytes(data))
        res = ewf_image.examine(p)
        self.assertEqual(res["status"], "mismatch")
        self.assertEqual(res["comparison"]["md5"], "mismatch")

    def test_non_ewf_file_is_unsupported(self):
        p = self.tmp / "fake.E01"
        p.write_bytes(b"not an image" * 10)
        self.assertEqual(ewf_image.examine(p)["status"], "unsupported")


class HashReferenceTest(unittest.TestCase):
    def test_reads_common_hash_list_formats(self):
        sha = "a" * 64
        text = "\n".join([
            f"{sha}  docs/report.pdf",                                   # sha256sum
            f"SHA1 (photo.jpg) = {'b' * 40}",                            # BSD
            "nps-image.raw  31129600  " + "c" * 40,                      # 이름 크기 해시 표
            "",
            "Original Filename: DCIM/100CANON/IMG_0001.JPG  in: card.raw",  # 블록
            "SHA 1: " + "d" * 40,
            "",
            f"{'e' * 32} {'f' * 32} two-hashes.bin",                     # 해시 2개 → 버림
        ])
        got = {(e["name"], e["algorithm"], e["hash"]) for e in hash_reference.parse_text(text)}
        self.assertEqual(got, {
            ("docs/report.pdf", "sha256", sha),
            ("photo.jpg", "sha1", "b" * 40),
            ("nps-image.raw", "sha1", "c" * 40),
            ("DCIM/100CANON/IMG_0001.JPG", "sha1", "d" * 40),
        })

    def test_reads_csv_with_named_columns(self):
        text = "Filename,MD5,SHA1\nC:\\cases\\a.txt," + "1" * 32 + "," + "2" * 40 + "\n"
        got = hash_reference.parse_text(text)
        self.assertEqual({(e["name"], e["algorithm"]) for e in got}, {("C:/cases/a.txt", "md5"), ("C:/cases/a.txt", "sha1")})

    def test_duplicate_basenames_are_not_guessed(self):
        files = [{"path": "a/x.txt", "sha256": "1" * 64}, {"path": "b/x.txt", "sha256": "2" * 64}]
        entries = [{"name": "x.txt", "algorithm": "sha256", "hash": "1" * 64}]
        self.assertEqual(hash_reference.match_reference(entries, files)[0]["result"], "ambiguous")
        entries = [{"name": "b/x.txt", "algorithm": "sha256", "hash": "1" * 64}]
        self.assertEqual(hash_reference.match_reference(entries, files)[0]["result"], "mismatch")


class ReportDraftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ev = self.tmp / "증거"
        (self.ev / "docs").mkdir(parents=True)
        (self.ev / "docs" / "memo.txt").write_text("회의 메모: 연락처 010-1234-5678\n", encoding="utf-8")
        (self.ev / "docs" / "memo_copy.txt").write_text("회의 메모: 연락처 010-1234-5678\n", encoding="utf-8")
        (self.ev / "photo.jpg").write_bytes(b"MZ\x90\x00" + b"\x00" * 200)   # 실행 파일 시그니처
        shutil.copyfile(FIX / "synthetic.E01", self.ev / "synthetic.E01")
        self.before = {p.relative_to(self.ev).as_posix(): p.read_bytes() for p in self.ev.rglob("*") if p.is_file()}
        memo_sha = hashlib.sha256((self.ev / "docs" / "memo.txt").read_bytes()).hexdigest()
        self.refs = self.tmp / "hashes.txt"
        self.refs.write_text("\n".join([
            f"{memo_sha}  docs/memo.txt",
            f"{'0' * 64}  photo.jpg",
            f"{'9' * 64}  missing.doc",
            f"synthetic.raw  {len(MEDIA)}  {MEDIA_SHA1}",
        ]) + "\n", encoding="utf-8")
        self.case = self.tmp / "case.txt"
        self.case.write_text("사건번호: 2026-테스트-7\n분석담당: 시험 분석관\n", encoding="utf-8")
        self.out = self.tmp / "결과"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = report_draft.main([str(a) for a in args])
        return code, buf.getvalue()

    def test_report_draft_end_to_end(self):
        code, out = self.run_main(self.ev, "-o", self.out, "--case-info", self.case, "--hashes", self.refs)
        # 참조 해시 불일치(photo.jpg)가 있으므로 확인 필요(1)로 끝나되 초안은 작성된다
        self.assertEqual(code, 1, out)
        for name in ("보고서초안.md", "보고서초안.html", "파일해시목록.csv", "무결성검증.json",
                     "타임라인.html", "events.json", "선조사.json", "초안검증.json"):
            self.assertTrue((self.out / name).is_file(), name)

        md = (self.out / "보고서초안.md").read_text(encoding="utf-8")
        self.assertIn("2026-테스트-7", md)
        self.assertIn("시험 분석관", md)
        self.assertIn("| 의뢰인 | 미확인 |", md)
        self.assertIn("TEST-2026-001", md)                       # E01 헤더 기록값
        self.assertIn("일치 (저장 해시 = 재계산 해시)", md)
        self.assertIn(MEDIA_MD5, md)
        self.assertIn("| synthetic.raw | SHA-1 |", md)              # 원시 이미지 참조 ↔ E01 미디어 해시
        self.assertIn("확장자 .jpg, 시그니처", md)                 # 시그니처 위장
        for name in ("보고서초안.md", "무결성검증.json", "선조사.json", "events.json"):
            self.assertNotIn(str(self.tmp), (self.out / name).read_text(encoding="utf-8"), name)  # 분석 PC 경로 미노출
        self.assertIn("`docs/memo.txt`, `docs/memo_copy.txt`", md)  # 정확 중복

        integ = json.loads((self.out / "무결성검증.json").read_text(encoding="utf-8"))
        self.assertEqual(integ["post_check"]["status"], "match")
        results = {e["name"]: e["result"] for e in integ["reference"]["entries"]}
        self.assertEqual(results, {"docs/memo.txt": "match", "photo.jpg": "mismatch",
                                   "missing.doc": "not_found", "synthetic.raw": "match"})

        verify = json.loads((self.out / "초안검증.json").read_text(encoding="utf-8"))
        self.assertEqual(verify["verdict"], "PASS", verify)

        csv_text = (self.out / "파일해시목록.csv").read_bytes()
        self.assertTrue(csv_text.startswith(b"\xef\xbb\xbf"))      # 엑셀 한글용 BOM
        memo_md5 = hashlib.md5((self.ev / "docs" / "memo.txt").read_bytes()).hexdigest()
        self.assertIn(f"docs/memo.txt".encode(), csv_text)
        self.assertIn(memo_md5.encode(), csv_text)

        after = {p.relative_to(self.ev).as_posix(): p.read_bytes() for p in self.ev.rglob("*") if p.is_file()}
        self.assertEqual(after, self.before)                      # 증거 폴더 무변경

    def test_clean_run_exits_zero(self):
        code, out = self.run_main(self.ev, "-o", self.out)
        self.assertEqual(code, 0, out)
        self.assertIn("초안 검증: PASS", out)

    def test_refuses_output_inside_evidence(self):
        code, _ = self.run_main(self.ev, "-o", self.ev / "out")
        self.assertEqual(code, 2)
        self.assertFalse((self.ev / "out").exists())

    def test_case_template_round_trip(self):
        tpl = self.tmp / "사건정보.txt"
        code, _ = self.run_main("--case-template", tpl)
        self.assertEqual(code, 0)
        filled = tpl.read_text(encoding="utf-8").replace("사건명:", "사건명: 시험 사건")
        tpl.write_text(filled, encoding="utf-8")
        self.assertEqual(report_draft.load_case_info(tpl)["case_name"], "시험 사건")

    def test_html_renders_tables(self):
        html = report_draft.md_to_html("# 제목\n\n| a | b |\n| --- | --- |\n| 1 \\| 2 | `x` |\n", "t")
        self.assertIn("<th>a</th>", html)
        self.assertIn("<td>1 | 2</td>", html)
        # Table cells are measured values: backticks stay literal, as in the Word and Hangul drafts.
        self.assertIn("<td>`x`</td>", html)
        self.assertNotIn("<code>x</code>", html)


if __name__ == "__main__":
    unittest.main()
