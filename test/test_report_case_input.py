"""Case metadata failures should be diagnosed before evidence processing."""
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.parametrize("content", [b"[]", b"{", b"\xff"])
def test_bad_case_info_stops_before_hashing(tmp_path, content):
    root = tmp_path / "input"
    root.mkdir()
    (root / "item.bin").write_bytes(b"sample")
    case = tmp_path / "case.json"
    case.write_bytes(content)
    out = tmp_path / "output"
    proc = subprocess.run([sys.executable, str(SCRIPTS / "report_draft.py"), str(root),
                           "--case-info", str(case), "-o", str(out)], capture_output=True)
    assert proc.returncode == 2
    assert b"[1/7]" not in proc.stdout
    assert b"case.json" in proc.stderr and b"Traceback" not in proc.stderr
    assert not out.exists()


def test_windows_utf16_case_info_round_trip(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    import report_draft

    case = tmp_path / "case.json"
    case.write_text('{"case_number": "TEST-01"}', encoding="utf-16")
    assert report_draft.load_case_info(case)["case_number"] == "TEST-01"
