"""Opt-in local reference images from the pinned ewfprobe fixture manifest in GAPS.md."""
import hashlib
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ewf_image


@pytest.mark.parametrize("variable", ["LF_EX01_FAST_SAMPLE", "LF_EX01_NONE_SAMPLE"])
def test_published_ex01_media(variable):
    if variable not in os.environ:
        pytest.skip(f"set {variable} to the published Ex01 fixture")
    result = ewf_image.examine(os.environ[variable])
    assert result["status"] == "verified"
    assert result["computed_hashes"]["sha256"] == "770be732aafc4962c6940a2076c35471419621d9d6ca57cda60cbd6e82a8b36f"
    assert result["comparison"] == {"md5": "match", "sha1": "match"}


def test_published_l01_export():
    if "LF_L01_SAMPLE" not in os.environ:
        pytest.skip("set LF_L01_SAMPLE to the published logical fixture")
    path = Path(os.environ["LF_L01_SAMPLE"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "38a8f72561a236f155ee8a395d421310949a1b3e2cc938a0f3ded448e78088a4"
    result = ewf_image.examine(path)
    assert result["status"] == "partial"
    assert result["computed_hashes"]["sha256"] == "fba3344ac80fa70d3d7fd9944c0b6e0e992448d4ce78d9c2e35877eeca4edf80"
    assert len(result["entries"]) == 9951
    assert sum(e["status"] == "verified" for e in result["entries"]) == 108
    lines = [f"{e['path']}\t{e['computed_hashes']['sha256']}\n"
             for e in result["entries"] if e["type"] == "file"]
    assert len(lines) == 9219
    digest = hashlib.sha256("".join(sorted(lines)).encode("utf-8"))
    assert digest.hexdigest() == "5575ef2c4dd6e5652def7db9b0b704b728370019c117efec342df1017c805d50"
