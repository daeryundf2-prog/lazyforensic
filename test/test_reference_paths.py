"""Synthetic reference attribution across folders and acquisition formats."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hash_reference
import report_draft


def reference(name, value):
    return {"name": name, "algorithm": "sha256", "hash": value}


def test_windows_absolute_reference_selects_matching_path():
    files = [{"path": "a/disk.bin", "sha256": "1" * 64},
             {"path": "b/disk.bin", "sha256": "2" * 64}]
    entries = hash_reference.parse_text("2" * 64 + r"  C:\source\B\disk.bin")
    result, = hash_reference.match_reference(entries, files)
    assert (result["result"], result["file"]) == ("match", "b/disk.bin")


def test_raw_file_reference_uses_existing_file():
    files = [{"path": "disk.raw", "sha256": "1" * 64}]
    images = [{"file": "disk.E01", "computed_hashes": {"sha256": "2" * 64}}]
    result, = report_draft.compare_references([reference("disk.raw", "1" * 64)], files, images)
    assert (result["result"], result["file"]) == ("match", "disk.raw")


def test_media_reference_preserves_directory_or_reports_ambiguity():
    images = [{"file": "a/disk.E01", "computed_hashes": {"sha256": "1" * 64}},
              {"file": "b/disk.E01", "computed_hashes": {"sha256": "2" * 64}}]
    qualified, bare = report_draft.compare_references(
        [reference("a/disk.raw", "1" * 64), reference("disk.raw", "2" * 64)], [], images)
    assert (qualified["result"], qualified["file"]) == ("match", "a/disk.E01")
    assert (bare["result"], bare["file"]) == ("ambiguous", None)


def test_digest_shaped_filename_starts_new_reference_block():
    name = "a" * 64 + ".bin"
    entries = hash_reference.parse_text(f"Filename: first.bin\nFilename: {name}\nMD5: " + "1" * 32)
    assert [(e["name"], e["algorithm"]) for e in entries] == [(name, "md5")]
