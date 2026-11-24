"""Build the verified release assets for one tag. Standard library and git only.

    python .github/release/build_assets.py --tag v1.7.0 --out dist [--ref <commit>]

Writes to --out:
  lazyforensic-<tag>.zip / .tar.gz   git archive of <ref> (default: the tag) under lazyforensic-<tag>/
  install.ps1 / install.sh           the installers of <ref>, stamped with the tag and their archive's SHA-256
  SHA256SUMS                         sha256sum format, one line per file above

The stamped installer installs exactly its own release and refuses an archive whose SHA-256 differs from
the stamped value, the SHA256SUMS line or GitHub's asset digest.
"""
import argparse
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
STAMPS = {
    "install.ps1": ("zip", '$ReleaseTag = ""', '$ReleaseTag = "{tag}"',
                    '$ReleaseArchiveSha256 = ""', '$ReleaseArchiveSha256 = "{sha}"'),
    "install.sh": ("tar.gz", 'RELEASE_TAG=""', 'RELEASE_TAG="{tag}"',
                   'RELEASE_ARCHIVE_SHA256=""', 'RELEASE_ARCHIVE_SHA256="{sha}"'),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True).stdout


def build(tag: str, out: Path, ref: str | None = None) -> dict:
    if not TAG_RE.fullmatch(tag):
        raise SystemExit(f"invalid tag: {tag!r}")
    ref = ref or tag
    out.mkdir(parents=True, exist_ok=True)
    archives = {}
    for fmt in ("zip", "tar.gz"):
        target = out / f"lazyforensic-{tag}.{fmt}"
        git("archive", f"--format={fmt}", f"--prefix=lazyforensic-{tag}/", "-o", str(target), ref)
        archives[fmt] = target
    for name, (fmt, tag_from, tag_to, sha_from, sha_to) in STAMPS.items():
        # Byte-level replacement keeps each installer's line endings (install.ps1 is CRLF in archives).
        text = git("show", f"{ref}:{name}").decode("ascii")
        if name == "install.ps1":
            text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        for old, new in ((tag_from, tag_to.format(tag=tag)), (sha_from, sha_to.format(sha=sha256(archives[fmt])))):
            if text.count(old) != 1:
                raise SystemExit(f"{name}: stamp anchor {old!r} must occur exactly once")
            text = text.replace(old, new)
        (out / name).write_bytes(text.encode("ascii"))
    files = [*archives.values(), out / "install.ps1", out / "install.sh"]
    sums = "".join(f"{sha256(p)}  {p.name}\n" for p in files)
    (out / "SHA256SUMS").write_text(sums, encoding="ascii", newline="\n")
    return {p.name: sha256(p) for p in files}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ref", help="commit to archive (default: the tag)")
    args = ap.parse_args()
    for name, digest in build(args.tag, args.out, args.ref).items():
        print(f"{digest}  {name}")


if __name__ == "__main__":
    main()
