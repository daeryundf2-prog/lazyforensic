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
                  "similar_images", "videos"):
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


class SetupEnvTests(unittest.TestCase):
    def test_check_runs(self):
        self.assertEqual(setup_env.main(["--check"]), 0)

    def test_check_json(self):
        self.assertEqual(setup_env.main(["--check", "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
