"""Manifest file identity and validation on copied evidence trees."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evidence_manifest


def test_legacy_windows_manifest_for_moved_directory(tmp_path):
    folder = tmp_path / "nested"
    folder.mkdir()
    (folder / "item.bin").write_bytes(b"test")
    manifest = evidence_manifest.build_manifest(tmp_path)
    manifest["root"] = r"C:\original"
    manifest["files"][0]["path"] = r"nested\item.bin"
    assert evidence_manifest.verify_manifest(tmp_path, manifest) == []


def test_duplicate_manifest_entry_cannot_hide_a_conflict(tmp_path):
    (tmp_path / "item.bin").write_bytes(b"test")
    manifest = evidence_manifest.build_manifest(tmp_path)
    entry, = manifest["files"]
    manifest["files"].insert(0, {**entry, "sha256": "0" * 64})
    manifest["file_count"] = 2
    assert evidence_manifest.verify_manifest(tmp_path, manifest)


@pytest.mark.parametrize("manifest", [{}, {"files": [], "file_count": 1}, ["invalid"], {"files": [{}]}])
def test_invalid_manifest_cli_reports_input_error(tmp_path, manifest):
    root = tmp_path / "empty"
    root.mkdir()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert evidence_manifest.main([str(root), "--verify", str(path)]) != 0


def test_manifest_includes_files_in_cache_directories(tmp_path):
    folder = tmp_path / "__pycache__"
    folder.mkdir()
    item = folder / "artifact.bin"
    item.write_bytes(b"first")
    manifest = evidence_manifest.build_manifest(tmp_path)
    item.write_bytes(b"second")
    assert evidence_manifest.verify_manifest(tmp_path, manifest) == ["CHANGED: __pycache__/artifact.bin"]
