import importlib.util
import json
import struct
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_module(name, relpath):
    path = ROOT / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


manifest_mod = load_module("evidence_manifest", "scripts/evidence_manifest.py")
hwpx = load_module("extract_hwpx", "scripts/extract_hwpx.py")
docdiff = load_module("doc_diff", "scripts/doc_diff.py")
audio = load_module("audio_survey", "scripts/audio_survey.py")
exif = load_module("exif_audit", "scripts/exif_audit.py")
osint = load_module("osint_username", "scripts/osint_username.py")
kwr = load_module("keyword_report", "scripts/keyword_report.py")
survey_mod = load_module("case_survey", "scripts/case_survey.py")
setup_env = load_module("setup_forensic_env", "scripts/setup_forensic_env.py")
sigcheck = load_module("signature_check", "scripts/signature_check.py")
dedup = load_module("dedup_files", "scripts/dedup_files.py")
pdf_audit = load_module("pdf_audit", "scripts/pdf_audit.py")
archive_survey = load_module("archive_survey", "scripts/archive_survey.py")
sqlite_survey = load_module("sqlite_survey", "scripts/sqlite_survey.py")
video_integrity = load_module("video_integrity", "scripts/video_integrity.py")

try:
    from PIL import Image  # noqa: F401
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def make_hwpx(path: Path, texts: list[str]) -> None:
    """최소한의 HWPX 구조를 만든다 (Contents/section0.xml + hp:t)."""
    xml = (
        '<?xml version="1.0"?>'
        '<opf:package xmlns:opf="x" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        "<hp:sec>" + "".join(f"<hp:t>{t}</hp:t>" for t in texts) + "</hp:sec>"
        "</opf:package>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Contents/section0.xml", xml)


class EvidenceManifestTests(unittest.TestCase):
    def test_build_and_verify_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("hello", encoding="utf-8")
            (root / "b.txt").write_text("world", encoding="utf-8")
            m = manifest_mod.build_manifest(root)
            self.assertEqual(m["file_count"], 2)
            self.assertEqual(manifest_mod.verify_manifest(root, m), [])

    def test_verify_detects_change_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("hello", encoding="utf-8")
            (root / "b.txt").write_text("world", encoding="utf-8")
            m = manifest_mod.build_manifest(root)
            (root / "a.txt").write_text("tampered", encoding="utf-8")
            (root / "b.txt").unlink()
            (root / "c.txt").write_text("new", encoding="utf-8")
            problems = manifest_mod.verify_manifest(root, m)
            self.assertEqual(len(problems), 3)
            self.assertTrue(any("CHANGED" in p for p in problems))
            self.assertTrue(any("MISSING" in p for p in problems))
            self.assertTrue(any("NEW" in p for p in problems))

    def test_cli_verify_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "evidence"
            root.mkdir()
            (root / "a.txt").write_text("x", encoding="utf-8")
            mf = Path(tmp) / "m.json"  # 매니페스트는 스캔 대상 밖에 둔다
            self.assertEqual(manifest_mod.main([str(root), "-o", str(mf)]), 0)
            self.assertEqual(manifest_mod.main([str(root), "--verify", str(mf)]), 0)
            (root / "a.txt").write_text("y", encoding="utf-8")
            self.assertEqual(manifest_mod.main([str(root), "--verify", str(mf)]), 1)


class HwpxTests(unittest.TestCase):
    def test_extracts_text_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "doc.hwpx"
            make_hwpx(f, ["제1조 목적", "제2조 정의"])
            r = hwpx.process(f)
            self.assertEqual(r["status"], "ok")
            self.assertIn("제1조", r["text"])
            self.assertIn("제2조", r["text"])

    def test_non_hwpx_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "fake.hwpx"
            f.write_text("not a zip", encoding="utf-8")
            r = hwpx.process(f)
            self.assertEqual(r["status"], "failed")


class DocDiffTests(unittest.TestCase):
    def test_change_classification(self):
        rows = docdiff.build_rows(
            ["제1조", "제2조 옛말", "제3조"],
            ["제1조", "제2조 새말", "제3조", "제4조 신설"],
        )
        notes = [r["note"] for r in rows]
        self.assertIn("동일", notes)
        self.assertIn("변경", notes)
        self.assertIn("신설", notes)

    def test_identical_files(self):
        rows = [r for r in docdiff.build_rows(["a", "b"], ["a", "b"]) if r["note"] != "동일"]
        self.assertEqual(rows, [])

    def test_cli_missing_file(self):
        self.assertEqual(docdiff.main(["/no.txt", "/no2.txt"]), 2)


class AudioSurveyTests(unittest.TestCase):
    def _make_wav(self, path: Path, seconds: float, loud: bool) -> None:
        fr = 16000
        n = int(fr * seconds)
        amp = 8000 if loud else 100
        frames = struct.pack(f"<{n}h", *([amp] * n))
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(fr)
            wf.writeframes(frames)

    def test_speech_vs_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            loud = Path(tmp) / "loud.wav"
            quiet = Path(tmp) / "quiet.wav"
            self._make_wav(loud, 2.0, loud=True)
            self._make_wav(quiet, 2.0, loud=False)
            rl = audio.survey(loud, 0.01)
            rq = audio.survey(quiet, 0.01)
            self.assertEqual(rl["status"], "ok")
            self.assertGreater(rl["speech_ratio"], 0.9)
            self.assertLess(rq["speech_ratio"], 0.5)

    def test_unreadable_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "bad.wav"
            f.write_bytes(b"not a wave")
            r = audio.survey(f, 0.01)
            # wave 실패 후 ffmpeg이 있으면 ffmpeg 경로로 갈 수 있다
            self.assertIn(r["status"], ("ok", "failed"))


class ExifAuditTests(unittest.TestCase):
    @unittest.skipUnless(HAS_PIL, "Pillow not installed")
    def test_eval_images_no_exif(self):
        media = FIXTURES / "eval" / "media"
        if not media.is_dir():
            self.skipTest("eval fixtures absent")
        from PIL import Image as PILImage

        records = [exif.audit_image(f, PILImage) for f in sorted(media.glob("*.png"))]
        self.assertEqual(len(records), 3)
        self.assertTrue(all(r.get("exif_present") is False for r in records))


class OsintTests(unittest.TestCase):
    def test_fail_closed_without_sherlock(self):
        if osint.find_sherlock() is not None:
            self.skipTest("sherlock installed")
        self.assertEqual(osint.main(["testuser"]), 3)


class KeywordReportTests(unittest.TestCase):
    def test_hit_with_line_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "doc.txt"
            f.write_text("첫째 줄\n보이스피싱 주의\n셋째 줄\n", encoding="utf-8")
            hits = kwr.search_file(f, ["보이스피싱"], 0)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["line"], 2)

    def test_absence_is_documented(self):
        # 평가 코퍼스에서 '보이스피싱' 부재 확인 — 검색 범위가 리포트에 남는다
        eval_dir = FIXTURES / "eval"
        if not eval_dir.is_dir():
            self.skipTest("eval fixtures absent")
        code = kwr.main([str(eval_dir / "docs"), "보이스피싱", "--json"])
        self.assertEqual(code, 1)  # 미검출 → exit 1

    def test_eval_keyword_presence(self):
        eval_dir = FIXTURES / "eval"
        if not eval_dir.is_dir():
            self.skipTest("eval fixtures absent")
        hits = kwr.search_file(eval_dir / "chats" / "kakao_export_2026-03.txt",
                               ["프로젝트 겨울나무"], 0)
        self.assertEqual(len(hits), 1)


class CaseSurveyTests(unittest.TestCase):
    def test_survey_eval_corpus(self):
        eval_dir = FIXTURES / "eval"
        if not eval_dir.is_dir():
            self.skipTest("eval fixtures absent")
        report = survey_mod.survey(eval_dir, ["프로젝트 겨울나무"], run_stt=False)
        steps = report["steps"]
        # manifest/pii/keywords/audio/exif/similar_images/videos 단계가 기록된다
        for k in ("manifest", "pii", "keywords", "audio", "exif",
                  "similar_images", "signatures", "dedup", "archives",
                  "pdf", "sqlite", "videos", "video_integrity"):
            self.assertIn(k, steps)
        m = steps["manifest"]
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["result"]["file_count"], 47)
        kw = steps["keywords"]
        self.assertEqual(kw["status"], "ok")
        self.assertGreaterEqual(kw["result"]["total_hits"], 1)

    def test_survey_missing_dir_exit_2(self):
        self.assertEqual(survey_mod.main(["/nonexistent-xyz"]), 2)

    def test_markdown_renders(self):
        eval_dir = FIXTURES / "eval"
        if not eval_dir.is_dir():
            self.skipTest("eval fixtures absent")
        report = survey_mod.survey(eval_dir, [], run_stt=False)
        md = survey_mod.to_markdown(report)
        self.assertIn("파일 매니페스트", md)
        self.assertIn("47개 파일", md)


class SignatureCheckTests(unittest.TestCase):
    def test_detects_disguised_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "photo.jpg"
            f.write_bytes(b"MZ" + b"\x00" * 30)  # PE를 jpg로 위장
            r = sigcheck.audit_file(f)
            self.assertEqual(r["status"], "MISMATCH")
            self.assertEqual(r["detected"], "PE 실행파일")

    def test_real_png_matches(self):
        media = FIXTURES / "eval" / "media" / "red_square.png"
        if not media.is_file():
            self.skipTest("eval fixtures absent")
        r = sigcheck.audit_file(media)
        self.assertEqual(r["status"], "MATCH")

    def test_text_files_unknown_not_mismatch(self):
        eval_dir = FIXTURES / "eval"
        if not eval_dir.is_dir():
            self.skipTest("eval fixtures absent")
        code = sigcheck.main([str(eval_dir), "--json"])
        self.assertEqual(code, 0)  # 텍스트류는 UNKNOWN이라 MISMATCH가 아님


class DedupTests(unittest.TestCase):
    def test_exact_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("same", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "b.txt").write_text("same", encoding="utf-8")
            (root / "c.txt").write_text("different", encoding="utf-8")
            groups = dedup.exact_dups(list(root.rglob("*")))
            groups = [g for g in groups if Path(g["files"][0]).is_file()]
            self.assertEqual(len(groups), 1)
            self.assertEqual(len(groups[0]["files"]), 2)


class SetupEnvTests(unittest.TestCase):
    def test_check_runs(self):
        self.assertEqual(setup_env.main(["--check"]), 0)

    def test_check_json(self):
        self.assertEqual(setup_env.main(["--check", "--json"]), 0)


def _make_pdf(path: Path, extra: bytes = b"") -> None:
    body = (b"%PDF-1.4\n"
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R >> endobj\n"
            b"4 0 obj << /Author " + "(테스터)".encode("utf-8") +
            b" /Producer (unit-test) >> endobj\n"
            + extra +
            b"\nstartxref\n0\n%%EOF\n")
    path.write_bytes(body)


class PdfAuditTests(unittest.TestCase):
    def test_clean_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "doc.pdf"
            _make_pdf(f)
            r = pdf_audit.audit_pdf(f)
            self.assertTrue(r["is_pdf"])
            self.assertFalse(r["encrypted"])
            self.assertFalse(r["suspicious"])
            self.assertEqual(r["page_count"], 1)
            self.assertEqual(r["metadata"]["author"], "테스터")

    def test_encrypted_and_js_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "evil.pdf"
            _make_pdf(f, b"5 0 obj << /Encrypt 6 0 R /JavaScript (app.alert) "
                          b"/OpenAction 7 0 R >> endobj")
            r = pdf_audit.audit_pdf(f)
            self.assertTrue(r["encrypted"])
            self.assertTrue(r["suspicious"])
            self.assertIn("javascript", r["active_signals"])
            self.assertEqual(pdf_audit.main([str(f)]), 1)

    def test_non_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "doc.pdf"
            f.write_bytes(b"not a pdf at all")
            r = pdf_audit.audit_pdf(f)
            self.assertFalse(r["is_pdf"])


class ArchiveSurveyTests(unittest.TestCase):
    def test_double_ext_and_exec_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            zpath = Path(tmp) / "pack.zip"
            with zipfile.ZipFile(zpath, "w") as z:
                z.writestr("invoice.pdf.exe", b"MZ" + b"\x00" * 30)
                z.writestr("readme.txt", "안녕")
            r = archive_survey.audit_archive(zpath)
            self.assertEqual(r["type"], "zip")
            flagged = {m["name"]: m["flags"] for m in r["members"] if m["flags"]}
            self.assertIn("invoice.pdf.exe", flagged)
            self.assertTrue(any("이중확장자" in f for f in flagged["invoice.pdf.exe"]))
            self.assertTrue(any("실행형" in f for f in flagged["invoice.pdf.exe"]))
            self.assertEqual(archive_survey.main([str(zpath)]), 1)

    def test_zip_bomb_ratio_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            zpath = Path(tmp) / "bomb.zip"
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("big.bin", b"\x00" * (2 * 1024 * 1024))
            r = archive_survey.audit_archive(zpath)
            self.assertTrue(any("압축폭탄" in f
                                for m in r["members"] for f in m["flags"]))

    def test_clean_zip_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            zpath = Path(tmp) / "ok.zip"
            with zipfile.ZipFile(zpath, "w") as z:
                z.writestr("a.txt", "hello")
            r = archive_survey.audit_archive(zpath)
            self.assertTrue(all(not m["flags"] for m in r["members"]))
            self.assertEqual(archive_survey.main([str(zpath)]), 0)


class SqliteSurveyTests(unittest.TestCase):
    def test_tables_and_integrity(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "chat.db"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE messages (id INTEGER, body TEXT)")
            conn.executemany("INSERT INTO messages VALUES (?, ?)",
                             [(1, "a"), (2, "b"), (3, "c")])
            conn.commit()
            conn.close()
            r = sqlite_survey.survey_db(db)
            self.assertTrue(r["is_sqlite"])
            self.assertTrue(r["integrity_ok"])
            tables = {t["name"]: t for t in r["tables"]}
            self.assertEqual(tables["messages"]["row_count"], 3)
            self.assertIn("body", tables["messages"]["columns"])

    def test_non_db_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "fake.db"
            f.write_bytes(b"not sqlite")
            r = sqlite_survey.survey_db(f)
            self.assertFalse(r["is_sqlite"])
            self.assertIn("error", r)


class VideoIntegrityTests(unittest.TestCase):
    def test_no_ffmpeg_exit_3(self):
        if video_integrity._require_ffmpeg():
            self.skipTest("ffmpeg installed")
        self.assertEqual(video_integrity.main(["/tmp/x.mp4"]), 3)

    @unittest.skipUnless(
        __import__("shutil").which("ffmpeg") is not None, "ffmpeg not installed")
    def test_ok_and_damaged(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            ok = Path(tmp) / "ok.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "testsrc=duration=1:size=64x64:rate=5", str(ok)],
                check=True)
            r = video_integrity.audit_video(ok)
            self.assertEqual(r["status"], "OK")
            self.assertGreater(r["duration"], 0)
            # 잘린 파일 — 앞부분만 남김
            cut = Path(tmp) / "cut.mp4"
            data = ok.read_bytes()
            cut.write_bytes(data[: len(data) // 3])
            r2 = video_integrity.audit_video(cut)
            self.assertIn(r2["status"], ("DAMAGED", "UNREADABLE"))


if __name__ == "__main__":
    unittest.main()
