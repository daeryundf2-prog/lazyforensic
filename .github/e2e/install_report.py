"""Exercise the installed CLI on a disposable profile. Uses only the standard library."""
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "test/fixtures/ewf/synthetic.E01"
WINDOWS = os.name == "nt"
PS = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"]
# Actions steps inherit the runner's original PATH. Import the user PATH as a
# newly opened desktop terminal would, retaining setup-python's process PATH.
PS_LAUNCH = r"""
$ErrorActionPreference = 'Stop'
$env:Path += ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
$cli = (Get-Command lazyforensic).Source
if ($cli -ne (Join-Path $env:LOCALAPPDATA 'LazyForensic\bin\lazyforensic.cmd')) {
    throw "Unexpected launcher: $cli"
}
Write-Host "Launcher: $cli"
[string[]]$cliArgs = ConvertFrom-Json $env:LF_E2E_ARGS
& lazyforensic @cliArgs
exit $LASTEXITCODE
"""
SH_LAUNCH = """
set -eu
. "$HOME/.bashrc"
cli=$(command -v lazyforensic)
echo "Launcher: $cli"
test "$cli" = "$HOME/.local/bin/lazyforensic"
exec lazyforensic "$@"
"""


@contextmanager
def restore_user_path():
    # USERPROFILE does not redirect HKCU. Save the raw (possibly expandable)
    # value and its type, and restore it even if installation or a check fails.
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
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
                    value, kind = before
                    winreg.SetValueEx(key, "Path", 0, kind, value)
            assert read() == before, "User PATH restoration failed"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_report(out, evidence, fmt):
    checks = read_json(out / "초안검증.json")
    reports = {"md": checks, "docx": checks["docx"], "hwpx": checks["hwpx"]}
    if fmt:
        reports["template"] = checks["template"]
    for kind, result in reports.items():
        name = f"양식초안.{fmt}" if kind == "template" else f"보고서초안.{kind}"
        assert (out / name).stat().st_size and result["report"] == name, name
        assert (result["verdict"], result["exit_code"]) == ("PASS", 0), (name, result)
    assert (out / "보고서초안.html").stat().st_size > 0
    integrity = read_json(out / "무결성검증.json")
    assert integrity["evidence_root"] == evidence.name
    assert (integrity["post_check"]["status"], integrity["post_check"]["checked"]) == ("match", 1)
    image, = integrity["images"]
    assert image["status"] == "verified" and image["comparison"] == {"md5": "match", "sha1": "match"}
    source = FIXTURE.read_bytes()
    entry, = integrity["files"]
    with (out / "파일해시목록.csv").open(encoding="utf-8-sig", newline="") as stream:
        row, = csv.DictReader(stream)
    assert entry["path"] == row["경로"] == FIXTURE.name
    for algo, column in (("md5", "MD5"), ("sha1", "SHA-1"), ("sha256", "SHA-256")):
        assert entry[algo] == row[column] == hashlib.new(algo, source).hexdigest(), column
    survey = read_json(out / "선조사.json")
    receipt = survey["processing_receipt"]
    assert receipt["status"] == "partial" and receipt["source"] == {"path": evidence.name, "sha256": None}
    record, = survey["steps"]["manifest"]["result"]["files"]
    assert (record["path"], record["sha256"]) == (entry["path"], entry["sha256"])


def verify_export(export, survey, item_path):
    payload = read_json(export)
    item, = payload["evidence_list"]
    for obj, source in ((payload, survey), (item, item_path)):
        receipt = obj["processing_receipt"]
        expected = hashlib.sha256(source.read_bytes()).hexdigest()
        assert receipt["source"] == {"path": str(source.resolve()), "sha256": expected}, source
        assert (receipt["status"], receipt["exit_code"]) == ("complete", 0), receipt
    assert item["provenance"]["status"] == "verified"
    assert item["claimed_sha256"] == item["verified_sha256"] == item["processing_receipt"]["source"]["sha256"]


def exercise(work):
    profile = work / "사용자 폴더"
    temp = work / "tmp"
    profile.mkdir()
    temp.mkdir()
    # pip may probe rustc for its user agent. Isolate runner tool caches too.
    tool_cache = work / "tool-cache"
    tool_cache.mkdir()
    env = dict(os.environ, HOME=str(profile), TMPDIR=str(temp), TEMP=str(temp), TMP=str(temp),
               RUSTUP_HOME=str(tool_cache / "rustup"), LAZYFORENSIC_SOURCE=str(ROOT),
               PIP_NO_CACHE_DIR="1", PIP_NO_INDEX="1")
    app_data = profile / "Local"
    if WINDOWS:
        app_data.mkdir()
        env.update(USERPROFILE=str(profile), LOCALAPPDATA=str(app_data), APPDATA=str(profile / "Roaming"),
                   PSModuleAnalysisCachePath=str(tool_cache / "ps-module-cache"))
        install = app_data / "LazyForensic"
        venv_python = profile / ".lfenv/Scripts/python.exe"
    else:
        env["SHELL"] = "/bin/bash"
        install = profile / ".lazyforensic"
        venv_python = profile / ".lfenv/bin/python"
    app = install / "app"
    evidence = work / "합성 증거 폴더"
    evidence.mkdir()
    specimen = evidence / FIXTURE.name
    shutil.copyfile(FIXTURE, specimen)
    print(f"{platform.platform()} | Python {sys.version} | {ROOT=} {profile=} {evidence=}", flush=True)
    log = work / "commands.log"

    def run(cmd, *, stdin=None, process_env=None):
        with log.open("ab") as stream:
            subprocess.run(cmd, cwd=work, env=process_env or env, stdout=stream,
                           stderr=subprocess.STDOUT, check=True, stdin=stdin)

    def cli(*args):
        args = list(map(str, args))
        print("lazyforensic", *args, flush=True)
        if WINDOWS:
            run([*PS, PS_LAUNCH], process_env=dict(env, LF_E2E_ARGS=json.dumps(args)))
        else:
            run(["bash", "--noprofile", "--norc", "-c", SH_LAUNCH, "bash", *args])

    try:
        if WINDOWS:
            run([*PS, r"""
                $ErrorActionPreference = 'Stop'
                $PSVersionTable.PSVersion
                Write-Host "Console output code page: $([Console]::OutputEncoding.CodePage)"
                Write-Host "ANSI code page: $([Text.Encoding]::Default.CodePage)"
                Get-Content -Raw -Encoding UTF8 (Join-Path $env:LAZYFORENSIC_SOURCE 'install.ps1') | Invoke-Expression
            """])
            # Also catch code-page-dependent shims on a runner using UTF-8.
            assert (install / "bin/lazyforensic.cmd").read_bytes().isascii()
        else:
            run(["bash", "--version"])
            run(["locale", "charmap"])
            with (ROOT / "install.sh").open("rb") as installer:
                run(["sh"], stdin=installer)
        run([str(venv_python), "-c", "import sys; print(sys.executable, sys.version)"])
        # Print diagnostics before reports so a late failure cannot trim them from the log tail.
        print(log.read_bytes().decode("utf-8", errors="replace"), flush=True)
        help_start = log.stat().st_size
        cli("--help")
        assert b"lazyforensic report" in log.read_bytes()[help_start:], "Missing report usage"
        for fmt in (None, "docx", "hwpx"):
            if fmt:
                out = work / f"{fmt} 결과"
                cli("report", evidence, "-o", out, "--template", app / f"templates/report_form.{fmt}")
            else:
                out = work / (evidence.name + "_분석결과")
                cli("report", evidence)
            verify_report(out, evidence, fmt)
            if fmt:
                form = out / f"양식초안.{fmt}"
                sidecar = out / ("양식초안.본문.sha256" if fmt == "docx" else "양식초안.hwpx.본문.sha256")
                run([str(venv_python), str(app / "scripts/verify_report.py"), str(form),
                     "--template-digest", str(sidecar), "--evidence", str(out / "무결성검증.json"), "--strict"])
            else:
                # The survey records its evidence root relative to its parent.
                survey = work / "survey.json"
                shutil.copyfile(out / "선조사.json", survey)
                export = work / "export.json"
                cli("export", survey, "-o", export)
                verify_export(export, survey, specimen)
        assert list(evidence.iterdir()) == [specimen] and specimen.read_bytes() == FIXTURE.read_bytes()
        # There is no shipped uninstaller. Remove only documented destinations,
        # then require the disposable profile and installer temp to be empty.
        shutil.rmtree(install)
        shutil.rmtree(profile / ".lfenv")
        if WINDOWS:
            app_data.rmdir()
        else:
            (profile / ".local/bin/lazyforensic").unlink()
            for name in (".local/bin", ".local"):
                (profile / name).rmdir()
            for name in (".bashrc", ".profile"):
                rc = profile / name
                assert rc.read_text().strip() == '# LazyForensic PATH\nexport PATH="$HOME/.local/bin:$PATH"'
                rc.unlink()
        assert not list(profile.iterdir()), list(profile.iterdir())
        assert not list(temp.iterdir()), list(temp.iterdir())
        expected = {profile, temp, tool_cache, evidence, log, export, survey, work / (evidence.name + "_분석결과"),
                    work / "docx 결과", work / "hwpx 결과"}
        assert set(work.iterdir()) == expected, set(work.iterdir()) ^ expected
    finally:
        if sys.exc_info()[0] is not None and log.exists():
            lines = log.read_bytes().decode("utf-8", errors="replace").splitlines()
            print("\n".join(lines[-100:]), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with TemporaryDirectory(dir=os.environ.get("RUNNER_TEMP"), prefix="lf-") as directory:
        with restore_user_path() if WINDOWS else nullcontext():
            exercise(Path(directory).resolve())
    print("PASS: three reports, both form digests, survey/export receipts and install footprint.")
