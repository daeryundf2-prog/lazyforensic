"""extract_hwpx.py 의 선택적 kordoc 폴백 — kordoc 없으면 이전 동작과 같아야 한다."""
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HWP = Path(__file__).resolve().parent / "fixtures" / "hwp"

spec = importlib.util.spec_from_file_location("extract_hwpx_kd", ROOT / "scripts" / "extract_hwpx.py")
hwpx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hwpx)

STUB = """#!%s
import os, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1]
assert os.environ.get("KORDOC_OFFLINE") == "1", "offline not forced"
open(out, "w", encoding="utf-8").write("STUB:" + os.path.basename(args[0]) + "\\nline2\\n")
"""


def make_stub(tmp: str) -> str:
    p = Path(tmp) / "kordoc-stub"
    p.write_text(STUB % sys.executable, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


class KordocAbsent(unittest.TestCase):
    def test_hwp_fails_exactly_as_before_without_kordoc(self):
        with mock.patch.dict(os.environ, {"LAZYFORENSIC_KORDOC_BIN": "/nonexistent/kordoc", "PATH": "/nonexistent"}):
            plain = hwpx.process(HWP / "textbox-nested.hwp")
            flagged = hwpx.process(HWP / "textbox-nested.hwp", use_kordoc=True)
        self.assertEqual(plain["status"], "failed")
        self.assertEqual(plain, flagged)
        self.assertNotIn("kordoc_error", flagged)


class KordocStub(unittest.TestCase):
    def test_fallback_only_with_flag_and_only_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = make_stub(tmp)
            with mock.patch.dict(os.environ, {"LAZYFORENSIC_KORDOC_BIN": stub}):
                off = hwpx.process(HWP / "textbox-nested.hwp")
                on = hwpx.process(HWP / "textbox-nested.hwp", use_kordoc=True)
                good = hwpx.process(HWP / "textbox-nested.hwpx", use_kordoc=True)
        self.assertEqual(off["status"], "failed")
        self.assertEqual(on["status"], "ok")
        self.assertEqual(on["engine"], "kordoc")
        self.assertTrue(on["text"].startswith("STUB:textbox-nested.hwp"))
        self.assertIn("fallback_reason", on)
        self.assertNotIn("engine", good)  # stdlib 경로가 성공한 파일은 kordoc로 가지 않는다

    def test_kordoc_failure_keeps_original_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "kordoc-bad"
            bad.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
            bad.chmod(0o755)
            with mock.patch.dict(os.environ, {"LAZYFORENSIC_KORDOC_BIN": str(bad)}):
                r = hwpx.process(HWP / "textbox-nested.hwp", use_kordoc=True)
        self.assertEqual(r["status"], "failed")
        self.assertIn("kordoc", r["kordoc_error"])
        self.assertIn("ZIP", r["error"])


@unittest.skipUnless(hwpx.find_kordoc(), "kordoc CLI absent")
class KordocReal(unittest.TestCase):
    def test_binary_hwp_text_extracted(self):
        r = hwpx.process(HWP / "textbox-nested.hwp", use_kordoc=True)
        self.assertEqual(r["status"], "ok")
        self.assertIn("글상자 첫 문단 안내", r["text"])


if __name__ == "__main__":
    unittest.main()
