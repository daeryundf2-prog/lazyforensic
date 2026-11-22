"""Package admission tests use only synthetic directory metadata and empty XML declarations."""
import sys
from pathlib import Path
from stat import S_IFLNK
from zipfile import ZipFile, ZipInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import report_draft
import report_templates as forms


@pytest.mark.parametrize("suffix", [".docx", ".hwpx"])
@pytest.mark.parametrize("case", ["parent", "windows", "symlink", "dtd", "deep_xml"])
def test_writer_and_verifier_reject_unsafe_package_members(tmp_path, suffix, case):
    sample = ROOT / "templates" / ("report_form" + suffix)
    draft = tmp_path / ("양식초안" + suffix)
    forms.write_form(sample, draft, {"사건번호": "A-01"})
    name = {"parent": "../sample.txt", "windows": "notes\\sample.txt",
            "symlink": "sample.txt", "dtd": "sample.xml", "deep_xml": "sample.xml"}[case]
    info = ZipInfo(name)
    content = b"sample"
    if case == "symlink":
        info.create_system = 3
        info.external_attr = (S_IFLNK | 0o777) << 16
    elif case == "dtd":
        content = '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE sample><sample/>'.encode("utf-16")
    elif case == "deep_xml":
        content = b"<s>" * 200 + b"</s>" * 200
    with ZipFile(draft, "a") as package:
        package.writestr(info, content)
    assert report_draft.run_verify(draft, [])["verdict"] == "FAIL"
    destination = tmp_path / ("copy" + suffix)
    with pytest.raises(ValueError):
        forms.write_form(draft, destination, {})
    assert not destination.exists()


def test_unused_xml_limit_applies_before_inflation(tmp_path):
    template = ROOT / "templates/report_form.docx"
    with ZipFile(template) as package:
        # Exercise declared sizes without allocating or compressing a large part.
        unused = ZipInfo("extra.xml")
        unused.file_size = 32 * 1024 * 1024 + 1
        package.filelist.append(unused)
        with pytest.raises(ValueError, match="32 MiB"):
            forms.check_package_size(package)
