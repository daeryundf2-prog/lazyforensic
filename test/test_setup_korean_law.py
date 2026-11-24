"""setup_korean_law.mjs — 기존 korean-law-mcp 체크아웃은 고정 커밋·잠금 파일이 같을 때만 재사용한다.

합성 저장소(의존성 없음, 오프라인 npm ci)로 설치 분기를 검사한다. 실제 korean-law-mcp를 받지 않는다.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup_korean_law.mjs"
WINDOWS = os.name == "nt"

pytestmark = pytest.mark.skipif(not (shutil.which("node") and shutil.which("npm") and shutil.which("git")),
                                reason="node, npm and git are required")

BUILD = "require('fs').mkdirSync('build',{recursive:true});require('fs').writeFileSync('build/index.js','// synthetic\\n')"


def git(cwd, *args):
    # Fixed identity and dates: the same synthetic tree always yields the same commit.
    stamp = "2026-01-01T00:00:00+00:00"
    env = dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    return subprocess.run(["git", "-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                           "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


def make_checkout(root: Path, version="1.0.0") -> tuple[Path, str]:
    """Copy the setup script into a disposable root and commit a dependency-free package beside it."""
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(SCRIPT, root / "scripts" / SCRIPT.name)
    pkg = root / "korean-law-mcp"
    pkg.mkdir()
    (pkg / "build.js").write_text(BUILD + "\n", encoding="utf-8")
    (pkg / ".gitignore").write_text("node_modules/\nbuild/\n", encoding="utf-8")
    manifest = {"name": "synthetic-law", "version": version, "private": True, "scripts": {"build": "node build.js"}}
    (pkg / "package.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    lock = {"name": "synthetic-law", "version": version, "lockfileVersion": 3, "requires": True,
            "packages": {"": {"name": "synthetic-law", "version": version}}}
    (pkg / "package-lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    git(pkg, "init", "-q")
    git(pkg, "add", "-A")
    git(pkg, "commit", "-q", "-m", "synthetic")
    return pkg, git(pkg, "rev-parse", "HEAD")


def setup(root: Path, ref: str, tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), KOREAN_LAW_MCP_REF=ref,
               npm_config_cache=str(tmp_path / "npm-cache"), npm_config_offline="true",
               npm_config_update_notifier="false", npm_config_fund="false", npm_config_audit="false")
    return subprocess.run(["node", str(root / "scripts" / SCRIPT.name)], cwd=root, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)


def test_same_pinned_commit_builds_with_the_same_lockfile(tmp_path):
    records = []
    for name in ("one", "two"):
        pkg, commit = make_checkout(tmp_path / name)
        proc = setup(tmp_path / name, commit, tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert (pkg / "build" / "index.js").is_file()
        record = json.loads((pkg / ".lazyforensic-build.json").read_text(encoding="utf-8"))
        assert record["commit"] == commit and record["ref"] == commit
        assert record["package_lock_sha256"] == hashlib.sha256((pkg / "package-lock.json").read_bytes()).hexdigest()
        assert git(pkg, "status", "--porcelain", "--untracked-files=no") == ""
        records.append(record)
    # Two installs of the same pinned commit record the same build input.
    assert records[0]["commit"] == records[1]["commit"]
    assert records[0]["package_lock_sha256"] == records[1]["package_lock_sha256"]


def test_other_commit_is_reported_and_not_reused(tmp_path):
    pkg, pinned = make_checkout(tmp_path)
    (pkg / "package.json").write_text((pkg / "package.json").read_text(encoding="utf-8").replace("1.0.0", "1.0.1"),
                                      encoding="utf-8")
    git(pkg, "commit", "-q", "-am", "other")
    other = git(pkg, "rev-parse", "HEAD")
    proc = setup(tmp_path, pinned, tmp_path)
    assert proc.returncode == 1
    assert other in proc.stderr and pinned in proc.stderr
    assert not (pkg / "build").exists() and not (pkg / "node_modules").exists()
    assert not (pkg / ".lazyforensic-build.json").exists()


def test_modified_lockfile_is_reported_and_not_reused(tmp_path):
    pkg, pinned = make_checkout(tmp_path)
    lock = pkg / "package-lock.json"
    lock.write_text(lock.read_text(encoding="utf-8").replace('"requires": true', '"requires": false'), encoding="utf-8")
    proc = setup(tmp_path, pinned, tmp_path)
    assert proc.returncode == 1
    assert "package-lock.json" in proc.stderr
    assert not (pkg / "build").exists() and not (pkg / ".lazyforensic-build.json").exists()


def test_checkout_without_git_history_is_not_reused(tmp_path):
    pkg, pinned = make_checkout(tmp_path)
    # Git marks object files read-only on Windows.
    def writable(func, path, _):
        os.chmod(path, 0o700)
        func(path)
    shutil.rmtree(pkg / ".git", **({"onexc": writable} if sys.version_info >= (3, 12) else {"onerror": writable}))
    proc = setup(tmp_path, pinned, tmp_path)
    assert proc.returncode == 1
    assert "git" in proc.stderr
    assert not (pkg / "build").exists()


def test_check_reports_recorded_build_input(tmp_path):
    pkg, commit = make_checkout(tmp_path)
    assert setup(tmp_path, commit, tmp_path).returncode == 0
    env = dict(os.environ, KOREAN_LAW_MCP_REF=commit, LAW_OC="synthetic")
    proc = subprocess.run(["node", str(tmp_path / "scripts" / SCRIPT.name), "--check"], cwd=tmp_path, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"commit={commit}" in proc.stdout
