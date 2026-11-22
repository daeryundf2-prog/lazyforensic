"""Run the offline POSIX installer against a synthetic source and user profile."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX shell installer")


def installer_env(tmp_path):
    profile = tmp_path / "user space"
    profile.mkdir()
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "bin").mkdir()
    shutil.copyfile(ROOT / "bin/lazyforensic", source / "bin/lazyforensic")
    (source / "scripts/report_draft.py").write_text("import argparse\nargparse.ArgumentParser().parse_args()\n")
    commands = tmp_path / "commands"
    commands.mkdir()
    uname = commands / "uname"
    uname.write_text("#!/bin/sh\necho Linux\n")
    uname.chmod(0o755)
    return {**os.environ, "HOME": str(profile), "LAZYFORENSIC_SOURCE": str(source), "SHELL": "/bin/bash",
            "PATH": str(commands) + os.pathsep + os.environ["PATH"], "PIP_NO_INDEX": "1",
            "PIP_CONFIG_FILE": os.devnull, "PIP_NO_CACHE_DIR": "1", "TMPDIR": str(tmp_path)}


def test_fresh_shell_finds_offline_install(tmp_path):
    env = installer_env(tmp_path)
    subprocess.run(["sh", str(ROOT / "install.sh")], env=env, check=True, capture_output=True)
    launch = subprocess.run(["bash", "--noprofile", "--norc", "-c",
                             'source "$HOME/.bashrc" && lazyforensic report --help'],
                            env=env, capture_output=True)
    assert launch.returncode == 0, launch.stderr


def test_source_archive_error_stops_replacement(tmp_path):
    env = installer_env(tmp_path)
    previous = Path(env["HOME"]) / ".lazyforensic/app/previous"
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"installed")
    tar = tmp_path / "commands/tar"
    tar.write_text('#!/bin/sh\ncase "$*" in\n *-cf*) "' + shutil.which("tar") +
                   '" "$@"; exit 1 ;;\nesac\nexec "' + shutil.which("tar") + '" "$@"\n')
    tar.chmod(0o755)
    proc = subprocess.run(["sh", str(ROOT / "install.sh")], env=env, capture_output=True)
    assert proc.returncode != 0
    assert previous.read_bytes() == b"installed"
