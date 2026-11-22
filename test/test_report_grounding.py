"""Report verifier regressions on synthetic report and timeline files."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def verify(path, *options):
    proc = subprocess.run([sys.executable, str(SCRIPTS / "verify_report.py"), str(path), "--json",
                           *options], capture_output=True, encoding="utf-8")
    return proc.returncode, json.loads(proc.stdout)


def test_cp949_report_is_grounded(tmp_path):
    report = tmp_path / "draft.md"
    report.write_bytes(("측정 " + "a" * 64 + "\n").encode("cp949"))
    code, result = verify(report)
    assert (code, result["verdict"], result["encoding"]) == (1, "FAIL", "cp949")


def test_explicit_unreadable_timeline_fails(tmp_path):
    report = tmp_path / "draft.md"
    report.write_text("2001-01-02 12:34:56", encoding="utf-8")
    code, result = verify(report, "--timeline", str(tmp_path / "missing.json"))
    assert (code, result["verdict"]) == (1, "FAIL")


@pytest.mark.parametrize(("reported", "grounded"), [
    ("2001-01-01T15:30:59Z", True),
    ("2001-01-02T00:30:59Z", False),
    ("2001년 1월 2일 00:30:58", False),
])
def test_timeline_checks_seconds_and_offsets(tmp_path, reported, grounded):
    report = tmp_path / "draft.md"
    timeline = tmp_path / "timeline.json"
    actual = "2001-01-02T00:30:59+09:00" if "T" in reported else "2001년 1월 2일 00:30:59"
    timeline.write_text(json.dumps([{"timestamp": actual}], ensure_ascii=False), encoding="utf-8")
    report.write_text(reported, encoding="utf-8")
    code, result = verify(report, "--strict", "--timeline", str(timeline))
    assert (code, result["verdict"]) == ((0, "PASS") if grounded else (1, "WARN"))


def test_impossible_clock_time_fails(tmp_path):
    report = tmp_path / "draft.md"
    report.write_text("2001-01-02 12:77:01", encoding="utf-8")
    assert verify(report)[0] == 1


def test_truncated_report_cannot_be_certified(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(SCRIPTS))
    import verify_report

    read = verify_report.read_text_smart
    monkeypatch.setattr(verify_report, "read_text_smart", lambda path: read(path, max_bytes=64))
    report = tmp_path / "draft.md"
    report.write_bytes(b"." * 64 + b"SHA-256 " + b"b" * 64)
    code = verify_report.main([str(report), "--json"])
    result = json.loads(capsys.readouterr().out)
    assert (code, result["verdict"]) == (1, "FAIL")


@pytest.mark.parametrize(("label", "key", "length"), [("SHA-256", "sha256", 64),
                                                     ("SHA-1", "sha1", 40), ("MD5", "md5", 32)])
def test_hash_table_checks_qualified_owner(tmp_path, label, key, length):
    evidence = tmp_path / "integrity.json"
    evidence.write_text(json.dumps({"files": [{"path": "first/item.bin", key: "a" * length},
                                              {"path": "second/item.bin", key: "b" * length}]}))
    report = tmp_path / "draft.md"
    report.write_text(f"| first/item.bin | {label} {'b' * length} |\n")
    assert verify(report, "--evidence", str(evidence))[0] == 1


def test_digest_shaped_metadata_is_not_a_measurement(tmp_path):
    evidence = tmp_path / "integrity.json"
    evidence.write_text(json.dumps({"path": "disk.bin", "sha256": "b" * 64, "notes": "a" * 64}))
    report = tmp_path / "draft.md"
    report.write_text("| disk.bin | " + "a" * 64 + " |\n")
    assert verify(report, "--evidence", str(evidence))[0] == 1


def test_digest_filename_in_report_is_not_a_hash_claim(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    name = "d" * 64 + ".bin"
    (root / name).write_bytes(b"sample")
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, str(SCRIPTS / "report_draft.py"), str(root), "-o", str(out)],
                          capture_output=True)
    assert proc.returncode == 0
    result = json.loads((out / "초안검증.json").read_text(encoding="utf-8"))
    assert result["verdict"] == result["docx"]["verdict"] == "PASS"
