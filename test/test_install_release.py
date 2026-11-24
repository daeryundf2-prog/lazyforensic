"""Release install supply chain (D13 review item 6): verified archive, no main-branch fallback, receipts.

A local HTTP server plays the GitHub release API and download host with assets built by
.github/release/build_assets.py from this checkout. The real installer runs in a disposable profile
(install.sh on macOS/Linux, install.ps1 on Windows). No network access is needed.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import threading
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"
TAG = "v0.0.0-synthetic"

pytestmark = pytest.mark.skipif(not (shutil.which("git") and (ROOT / ".git").exists()),
                                reason="release assets are built with git archive from this checkout")


def load_builder():
    spec = importlib.util.spec_from_file_location("build_assets", ROOT / ".github/release/build_assets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def assets(tmp_path_factory):
    out = tmp_path_factory.mktemp("assets")
    load_builder().build(TAG, out, ref="HEAD")
    return out


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_assets_are_listed_and_installers_are_stamped(assets):
    archive = {"zip": assets / f"lazyforensic-{TAG}.zip", "tar.gz": assets / f"lazyforensic-{TAG}.tar.gz"}
    sums = dict(reversed(line.split("  ")) for line in (assets / "SHA256SUMS").read_text().splitlines())
    assert set(sums) == {archive["zip"].name, archive["tar.gz"].name, "install.ps1", "install.sh"}
    for name, digest in sums.items():
        assert sha256((assets / name).read_bytes()) == digest
    ps1, sh = (assets / "install.ps1").read_bytes(), (assets / "install.sh").read_bytes()
    assert ps1.isascii() and sh.isascii() and b"\r\n" in ps1 and b"\r" not in sh
    assert f'$ReleaseTag = "{TAG}"'.encode() in ps1
    assert f'$ReleaseArchiveSha256 = "{sums[archive["zip"].name]}"'.encode() in ps1
    assert f'RELEASE_TAG="{TAG}"'.encode() in sh
    assert f'RELEASE_ARCHIVE_SHA256="{sums[archive["tar.gz"].name]}"'.encode() in sh
    with ZipFile(archive["zip"]) as package:
        names = set(package.namelist())
    prefix = f"lazyforensic-{TAG}/"
    assert {prefix + "scripts/report_draft.py", prefix + "requirements-install.txt"} <= names


def test_pillow_requirement_is_hash_pinned():
    text = (ROOT / "requirements-install.txt").read_text(encoding="ascii")
    requirement = [line for line in text.splitlines() if line and not line.startswith(("#", " "))]
    assert requirement == ["pillow==11.3.0 \\"]
    assert text.count("--hash=sha256:") > 50
    for name in ("install.ps1", "install.sh"):
        source = (ROOT / name).read_text(encoding="ascii")
        assert "--require-hashes" in source and "requirements-install.txt" in source
        assert "pillow>=" not in source.lower()
        # No silent fallback to the mutable main branch.
        assert '"main"' not in source and "archive/$ref" not in source.lower()


class Release:
    """Fake GitHub API + download host. Tests change `files` or `api_up` between installs."""

    def __init__(self, assets: Path):
        self.files = {p.name: p.read_bytes() for p in assets.iterdir()}
        self.digests = {name: "sha256:" + sha256(data) for name, data in self.files.items()}
        self.api_up = True
        release = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                host = f"http://127.0.0.1:{self.server.server_port}"
                if self.path in ("/repos/sample/releases/latest", f"/repos/sample/releases/tags/{TAG}"):
                    if not release.api_up:
                        return self.reply(503, b"{}")
                    body = {"tag_name": TAG, "assets": [
                        {"name": name, "browser_download_url": f"{host}/download/{TAG}/{name}",
                         "digest": release.digests[name]} for name in sorted(release.files)]}
                    return self.reply(200, json.dumps(body, indent=2).encode())
                name = self.path.rsplit("/", 1)[-1]
                if self.path.startswith(f"/download/{TAG}/") and name in release.files:
                    return self.reply(200, release.files[name])
                self.reply(404, b"not found")

            def reply(self, code, data):
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.api = f"http://127.0.0.1:{self.server.server_port}/repos/sample"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@contextmanager
def restore_user_path():
    # install.ps1 edits HKCU\Environment\Path; USERPROFILE does not redirect the registry.
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        def read():
            try:
                return winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return None

        before = read()
        try:
            yield
        finally:
            if read() != before:
                if before is None:
                    winreg.DeleteValue(key, "Path")
                else:
                    winreg.SetValueEx(key, "Path", 0, before[1], before[0])


def tree(folder: Path):
    return {p.relative_to(folder).as_posix(): sha256(p.read_bytes()) for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture
def installer(tmp_path, assets):
    profile = tmp_path / "profile"
    temp = tmp_path / "tmp"
    profile.mkdir()
    temp.mkdir()
    release = Release(assets)
    env = dict(os.environ, HOME=str(profile), TMPDIR=str(temp), TEMP=str(temp), TMP=str(temp),
               LAZYFORENSIC_RELEASE_API=release.api, PIP_NO_INDEX="1", PIP_NO_CACHE_DIR="1",
               NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    for key in ("LAZYFORENSIC_SOURCE", "LAZYFORENSIC_REF", "LAZYFORENSIC_SHA256"):
        env.pop(key, None)
    if WINDOWS:
        local = profile / "Local"
        local.mkdir()
        env.update(USERPROFILE=str(profile), LOCALAPPDATA=str(local), APPDATA=str(profile / "Roaming"))
        root = local / "LazyForensic"
        # The "irm | iex" path: no script file, so the release download branch runs. Print the
        # exception message unwrapped so the assertions do not depend on console width.
        env["LF_STAMPED_INSTALLER"] = str(assets / "install.ps1")
        command = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                   "try { Get-Content -Raw -Encoding UTF8 $env:LF_STAMPED_INSTALLER | Invoke-Expression } "
                   "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"]
        feed = {"stdin": subprocess.DEVNULL}
    else:
        env["SHELL"] = "/bin/sh"
        root = profile / ".lazyforensic"
        command = ["sh"]
        feed = {"input": (assets / "install.sh").read_text(encoding="ascii")}  # the "curl | sh" path

    def run():
        return subprocess.run(command, env=env, cwd=tmp_path, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600, **feed)

    with restore_user_path() if WINDOWS else nullcontext():
        yield release, root, run
    release.close()


def test_release_install_is_verified_reproducible_and_fails_closed(installer, assets):
    release, root, run = installer
    archive = f"lazyforensic-{TAG}.zip" if WINDOWS else f"lazyforensic-{TAG}.tar.gz"
    expected = sha256((assets / archive).read_bytes())

    receipts = []
    for _ in range(2):
        proc = run()
        assert proc.returncode == 0, proc.stdout + proc.stderr
        receipt = json.loads((root / "install-receipt.json").read_text(encoding="utf-8"))
        assert receipt["source"]["kind"] == "release" and receipt["source"]["tag"] == TAG
        assert receipt["source"]["archive"] == archive and receipt["source"]["sha256"] == expected
        assert "SHA256SUMS" in receipt["source"]["verified_against"]
        assert "release installer" in receipt["source"]["verified_against"]
        assert receipt["installer"] == f"release {TAG}"
        assert receipt["requirements_sha256"] == sha256((root / "app/requirements-install.txt").read_bytes())
        receipts.append(receipt)
    # Two installs of the same release record the same input digest.
    assert receipts[0]["source"]["sha256"] == receipts[1]["source"]["sha256"] == expected

    installed = tree(root)
    original = dict(release.files)

    # An altered archive is refused, and the existing installation is left as it was.
    release.files[archive] = original[archive] + b"tampered"
    proc = run()
    assert proc.returncode != 0 and "does not match SHA256SUMS" in proc.stdout + proc.stderr
    assert tree(root) == installed

    # Altering the archive and SHA256SUMS together is still caught by the stamped installer pin.
    tampered = release.files[archive]
    release.files["SHA256SUMS"] = original["SHA256SUMS"].replace(expected.encode(), sha256(tampered).encode())
    release.digests[archive] = "sha256:" + sha256(tampered)
    proc = run()
    assert proc.returncode != 0 and "does not match release installer" in proc.stdout + proc.stderr
    assert tree(root) == installed

    # A failed release lookup never falls back to the main branch.
    release.files, release.digests[archive] = original, "sha256:" + expected
    release.api_up = False
    proc = run()
    assert proc.returncode != 0 and "does not fall back to the main branch" in proc.stdout + proc.stderr
    assert tree(root) == installed
