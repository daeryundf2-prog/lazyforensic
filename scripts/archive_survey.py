#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
archive_survey.py — 압축 컨테이너 내부 표면 감사 (stdlib 전용)

zip/tar 계열을 추출하지 않고 내부만 조사한다:
- 멤버 목록·크기·SHA-256 (zip만 해시 가능)
- 실행파일·이중 확장자(invoice.pdf.exe) 등 위장 패턴
- 암호화 멤버 (zip flag bit 0x1)
- 압축률 이상(압축폭탄 의심, ratio > 100이고 1MB 초과)
- 중첩 아카이브 (zip 속 zip)

사용:
    python scripts/archive_survey.py evidence/
    python scripts/archive_survey.py package.zip --json

알려진 한계:
- 멤버 해시는 읽기 상한(64MB)까지만 계산한다 — 초과 시 sha256 대신
  '읽기상한' 플래그를 단다. 압축폭탄이 감사기를 OOM으로 죽이지 못하게
  복호를 중간에 끊는 fail-closed 방어다.
- tar 계열은 멤버별 압축 크기를 헤더가 제공하지 않아 컨테이너 파일
  크기를 대리값으로 쓴다 — .tar(비압축)에서는 ratio가 ~1이라
  오탐이 없고, .tar.gz에서만 의미 있는 신호가 된다.
- RAR/7z는 미지원 (외부 도구 영역 — 7z/unar 필요).
- '악성 여부'가 아니라 '수동 확인 필요 신호'만 출력한다.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import re
import sys
import tarfile
import zipfile
from pathlib import Path
from xml.parsers import expat

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_json import print_json  # noqa: E402

ARCHIVE_EXTS = {".zip", ".jar", ".hwpx", ".docx", ".xlsx", ".pptx"}
TAR_EXTS = {".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz"}
EXEC_EXTS = {".exe", ".dll", ".scr", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jar", ".com", ".msi"}
DOC_LIKE = {".pdf", ".doc", ".docx", ".hwp", ".hwpx", ".jpg", ".png", ".txt", ".xlsx"}
ZIP_BOMB_RATIO = 100
ZIP_BOMB_MIN = 1024 * 1024  # 원본이 1MB 초과인데 압축률 100배 넘으면 의심
HASH_CAP = 64 * 1024 * 1024  # 멤버 해시 읽기 상한 — 초과 시 해시 생략
MEMBER_CAP = 10000           # 단일 아카이브 멤버 수 상한 — 초과 시 나머지 생략
READ_TOTAL_CAP = 512 * 1024 * 1024  # 아카이브당 누적 읽기 상한 — 초과 시 남은 멤버 해시 생략


def _hash_member(fobj, cap: int = HASH_CAP) -> tuple[bytes, str | None, bool]:
    if cap < 0:
        raise ValueError("Negative read limit")
    digest = hashlib.sha256()
    head = b""
    total = 0
    while total <= cap:
        chunk = fobj.read(min(1024 * 1024, cap - total + 1))
        if not chunk:
            return head, digest.hexdigest(), False
        head = (head + chunk[:32])[:32]
        total += len(chunk)
        if total > cap:
            return head, None, True
        digest.update(chunk)
    return head, None, True


def _sig_mod():
    spec = importlib.util.spec_from_file_location(
        "signature_check", Path(__file__).resolve().parent / "signature_check.py")
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sniff_head(sig, head: bytes, ext: str) -> tuple[str, str]:
    """멤버 선두 바이트로 시그니처 판정 — ('형식', 'MATCH|MISMATCH|UNKNOWN')."""
    if sig is None or not head:
        return "?", "UNKNOWN"
    for sg, off, name, exts in sig.SIGNATURES:
        if name == "ISOBMFF":
            if len(head) >= 8 and head[4:8] == b"ftyp":
                return name, ("MATCH" if ext in exts else "MISMATCH")
            continue
        if head[off:off + len(sg)] == sg:
            if ext in exts or not exts:
                return name, "MATCH"
            return name, "MISMATCH"
    return "UNKNOWN", "UNKNOWN"


def _is_double_ext(name: str) -> bool:
    parts = Path(name).name.lower().split(".")
    return len(parts) >= 3 and f".{parts[-2]}" in DOC_LIKE and f".{parts[-1]}" in EXEC_EXTS


def _audit_member(sig, name: str, ext: str, head: bytes, size: int, comp_size: int,
                  encrypted: bool) -> dict:
    detected, status = _sniff_head(sig, head, ext)
    flags = []
    if _is_double_ext(name):
        flags.append("이중확장자")
    if ext in EXEC_EXTS:
        flags.append("실행형")
    if status == "MISMATCH":
        flags.append(f"시그니처불일치({detected})")
    if encrypted:
        flags.append("암호화멤버")
    if size > ZIP_BOMB_MIN and comp_size > 0 and size / comp_size > ZIP_BOMB_RATIO:
        flags.append("압축폭탄의심")
    if ext in ARCHIVE_EXTS or ext in TAR_EXTS:
        flags.append("중첩아카이브")
    return {"name": name, "size": size, "detected": detected,
            "signature": status, "flags": flags}


# 문서 패키지(HWPX·OOXML)의 알려진 XML 구성원은 확장자가 .xml이 아니다. 선두 '<?xml'만 보고 확장자와
# 대조하면 정상 양식(Contents/content.hpf 등)이 시그니처 불일치로 오른다. 이 경로의 구성원은 XML 루트
# 요소를 읽어 패키지 규격의 루트와 대조한다. 같으면 정상, 다르면 그 차이를 '구조불일치'로 남긴다.
KNOWN_PACKAGE_XML = (
    ("hwpx", re.compile(r"Contents/content\.hpf"),
     {"{http://www.idpf.org/2007/opf/}package", "{http://www.idpf.org/2007/opf}package"}),
    ("hwpx", re.compile(r"META-INF/container\.rdf"), {"{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF"}),
    ("ooxml", re.compile(r"(?:.*/)?_rels/[^/]*\.rels"),
     {"{http://schemas.openxmlformats.org/package/2006/relationships}Relationships"}),
)
ROOT_READ_CAP = 1024 * 1024  # 루트 요소는 문서 첫머리에 있다 — 이만큼 읽어 못 찾으면 판정 불가


def _package_kind(z: zipfile.ZipFile, names: set) -> str | None:
    """mimetype 구성원(HWPX)이나 [Content_Types].xml(OOXML)로 패키지 종류를 정한다."""
    if "mimetype" in names:
        info = z.getinfo("mimetype")
        if info.file_size <= 64 and not info.flag_bits & 1:
            if z.read(info).strip().startswith(b"application/hwp+zip"):
                return "hwpx"
    if "[Content_Types].xml" in names:
        return "ooxml"
    return None


def _xml_root(stream) -> tuple[str | None, int]:
    """첫 요소의 '{네임스페이스}이름'과 읽은 바이트 수. XML이 아니거나 DTD가 있으면 (None, 읽은 바이트)."""
    parser = expat.ParserCreate(namespace_separator="}")
    found: list[str] = []

    class Done(Exception):
        pass

    def start(name, attrs):
        found.append("{" + name if "}" in name else name)
        raise Done

    def reject(*args):
        raise ValueError("DTD")

    parser.StartElementHandler = start
    parser.StartDoctypeDeclHandler = reject
    read = 0
    try:
        while read < ROOT_READ_CAP:
            chunk = stream.read(min(64 * 1024, ROOT_READ_CAP - read))
            read += len(chunk)
            parser.Parse(chunk, not chunk)
            if not chunk:
                break
    except Done:
        return found[0], read
    except (expat.ExpatError, ValueError):
        pass
    return None, read


def _check_package_member(member: dict, kind: str | None, z: zipfile.ZipFile, info) -> int:
    """알려진 패키지 XML 구성원이면 루트 요소로 판정을 고친다. 읽은 바이트 수를 돌려준다."""
    if kind is None or member["detected"] not in ("XML", "UNKNOWN") or "error" in member:
        return 0
    for owner, pattern, roots in KNOWN_PACKAGE_XML:
        if owner != kind or not pattern.fullmatch(info.filename):
            continue
        try:
            with z.open(info) as stream:
                root, read = _xml_root(stream)
        except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
            root, read = None, 0
        member["flags"] = [f for f in member["flags"] if not f.startswith("시그니처불일치(")]
        member["package_member"] = {"package": kind, "expected_root": sorted(roots), "root": root}
        if root in roots:
            member.update(detected="XML", signature="MATCH")
        else:
            member["signature"] = "MISMATCH"
            member["flags"].append(f"구조불일치({'XML 루트 ' + root if root else 'XML 아님'}, "
                                   f"기대 {sorted(roots)[0]})")
        return read
    return 0


def audit_zip(path: Path, sig) -> dict:
    rec = {"file": str(path), "type": "zip", "members": [], "status": "complete", "warnings": []}
    budget = READ_TOTAL_CAP
    try:
        if path.stat().st_size > READ_TOTAL_CAP:
            raise ValueError("Container size limit exceeded")
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            kind = _package_kind(z, {i.filename for i in infos})
            if len(infos) > MEMBER_CAP:
                rec["status"] = "partial"
                rec["warnings"].append("Member count limit exceeded")
            for info in infos[:MEMBER_CAP]:
                if info.is_dir():
                    continue
                encrypted = bool(info.flag_bits & 1)
                head, sha, capped = b"", None, False
                error = None
                charge = min(info.file_size, HASH_CAP) + 1
                if encrypted:
                    error = "Encrypted member not measured"
                elif charge > budget or info.file_size > HASH_CAP:
                    capped = True
                else:
                    budget -= charge
                    try:
                        with z.open(info) as stream:
                            head, sha, capped = _hash_member(stream, min(info.file_size, HASH_CAP))
                    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
                        error = str(exc)
                member = _audit_member(sig, info.filename, Path(info.filename).suffix.lower(),
                                       head, info.file_size, info.compress_size, encrypted)
                if sha and not capped:
                    budget -= _check_package_member(member, kind, z, info)
                if capped:
                    member["flags"].append("읽기상한(해시생략)")
                if error:
                    member["error"] = error
                if sha:
                    member["sha256"] = sha
                else:
                    rec["status"] = "partial"
                rec["members"].append(member)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        rec.update(status="failed", error=str(exc))
    rec["read_budget_charged"] = READ_TOTAL_CAP - budget
    return rec


def audit_tar(path: Path, sig) -> dict:
    rec: dict = {"file": str(path), "type": "tar", "members": []}
    # tar 헤더에는 멤버별 압축 크기가 없다 — 컨테이너 파일 크기를 대리값으로
    # 쓴다. .tar는 비압축이라 ratio ~1(오탐 없음), .tar.gz에서 폭탄 신호가 됨.
    container_size = path.stat().st_size or 0
    total_read = 0
    try:
        with tarfile.open(path) as t:
            members = t.getmembers()
            if len(members) > MEMBER_CAP:
                rec["flags"] = [f"멤버수상한초과({len(members)}>{MEMBER_CAP} — 나머지 생략)"]
                members = members[:MEMBER_CAP]
            for info in members:
                if not info.isfile():
                    continue
                head = b""
                sha = None
                capped = False
                if total_read < READ_TOTAL_CAP:
                    try:
                        f = t.extractfile(info)
                        if f:
                            head, sha, capped = _hash_member(f)
                            total_read += min(info.size, HASH_CAP)
                    except (OSError, tarfile.TarError):
                        pass
                else:
                    capped = True
                    note = f"누적읽기상한초과({READ_TOTAL_CAP}B — {info.name} 해시 생략)"
                    if note not in rec.get("flags", []):
                        rec.setdefault("flags", []).append(note)
                m = _audit_member(sig, info.name, Path(info.name).suffix.lower(),
                                  head, info.size, container_size, False)
                if capped:
                    m["flags"].append("읽기상한(해시생략)")
                elif sha:
                    m["sha256"] = sha
                rec["members"].append(m)
    except (OSError, tarfile.TarError) as e:
        rec["error"] = str(e)
    return rec


def audit_archive(path: Path, sig=None) -> dict:
    if sig is None:
        sig = _sig_mod()
    suf = path.suffix.lower()
    name = path.name.lower()
    if zipfile.is_zipfile(path):
        return audit_zip(path, sig)
    if suf in TAR_EXTS or name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        return audit_tar(path, sig)
    return {"file": str(path), "error": "지원하지 않는 아카이브 형식 (RAR/7z는 외부 도구 필요)"}


def is_candidate(p: Path) -> bool:
    suf, name = p.suffix.lower(), p.name.lower()
    return suf in ARCHIVE_EXTS or suf in TAR_EXTS or name.endswith((".tar.gz", ".tar.bz2", ".tar.xz"))


def unreadable(rec: dict) -> bool:
    """구조를 읽지 못한 후보 (판독 불가) — 멤버 0개를 '내용 없음'으로 오인하지 않게 따로 센다."""
    return bool(rec.get("error")) or rec.get("status") == "failed"


def scan(root: Path, files: list[Path] | None = None) -> list[dict]:
    """확장자로 고른 후보마다 기록을 하나씩 남긴다. 읽지 못한 후보도 error와 함께 남긴다."""
    sig = _sig_mod()
    if files is None:
        files = sorted(p for p in root.rglob("*") if p.is_file())
    out = []
    for p in files:
        if "__pycache__" in p.parts or not is_candidate(p):
            continue
        is_zip = zipfile.is_zipfile(p) or p.suffix.lower() in ARCHIVE_EXTS
        out.append(audit_zip(p, sig) if is_zip else audit_tar(p, sig))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="압축 컨테이너 내부 표면 감사 (추출 없음)")
    ap.add_argument("target", help="아카이브 파일 또는 디렉터리")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if target.is_dir():
        records = scan(target)
    elif target.is_file():
        records = [audit_archive(target)]
    else:
        print(f"대상을 찾을 수 없습니다: {target}", file=sys.stderr)
        return 2

    flagged = [m for r in records for m in r.get("members", []) if m["flags"]]
    if args.json:
        print_json({"archives": len(records), "flagged_members": len(flagged),
                          "records": records}, indent=2)
    else:
        for r in records:
            if "error" in r:
                print(f"[실패] {r['file']}: {r['error']}")
                continue
            n_flag = sum(1 for m in r["members"] if m["flags"])
            print(f"{r['file']}: {r['type']} 멤버 {len(r['members'])}개, 플래그 {n_flag}개")
            for m in r["members"]:
                if m["flags"]:
                    print(f"  ⚠ {m['name']} — {', '.join(m['flags'])}")
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
