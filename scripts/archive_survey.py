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
import json
import sys
import tarfile
import zipfile
from pathlib import Path

ARCHIVE_EXTS = {".zip", ".jar", ".hwpx", ".docx", ".xlsx", ".pptx"}
TAR_EXTS = {".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz"}
EXEC_EXTS = {".exe", ".dll", ".scr", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jar", ".com", ".msi"}
DOC_LIKE = {".pdf", ".doc", ".docx", ".hwp", ".hwpx", ".jpg", ".png", ".txt", ".xlsx"}
ZIP_BOMB_RATIO = 100
ZIP_BOMB_MIN = 1024 * 1024  # 원본이 1MB 초과인데 압축률 100배 넘으면 의심
HASH_CAP = 64 * 1024 * 1024  # 멤버 해시 읽기 상한 — 초과 시 해시 생략


def _hash_member(fobj, cap: int = HASH_CAP) -> tuple[bytes, str | None, bool]:
    """멤버를 1MB 청크로 증분 해시. cap 초과 시 즉시 읽기를 중단한다.

    반환: (head 32B, sha256 또는 None, 상한 초과 여부)
    f.read() 전체 복호는 압축폭탄이 감사기 자체를 OOM으로 죽이게 하므로
    메모리·CPU를 상한 내로 묶는다.
    """
    h = hashlib.sha256()
    head = fobj.read(32)
    h.update(head)
    total = len(head)
    while True:
        chunk = fobj.read(1024 * 1024)
        if not chunk:
            return head, h.hexdigest(), False
        total += len(chunk)
        if total > cap:
            return head, None, True
        h.update(chunk)


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


def audit_zip(path: Path, sig) -> dict:
    rec: dict = {"file": str(path), "type": "zip", "members": []}
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                encrypted = bool(info.flag_bits & 0x1)
                head = b""
                sha = None
                capped = False
                if not encrypted:
                    try:
                        with z.open(info) as f:
                            head, sha, capped = _hash_member(f)
                    except (OSError, RuntimeError, zipfile.BadZipFile):
                        pass
                m = _audit_member(sig, info.filename, Path(info.filename).suffix.lower(),
                                  head, info.file_size, info.compress_size, encrypted)
                if capped:
                    m["flags"].append("읽기상한(해시생략)")
                elif sha:
                    m["sha256"] = sha
                rec["members"].append(m)
    except (OSError, zipfile.BadZipFile) as e:
        rec["error"] = str(e)
    return rec


def audit_tar(path: Path, sig) -> dict:
    rec: dict = {"file": str(path), "type": "tar", "members": []}
    # tar 헤더에는 멤버별 압축 크기가 없다 — 컨테이너 파일 크기를 대리값으로
    # 쓴다. .tar는 비압축이라 ratio ~1(오탐 없음), .tar.gz에서 폭탄 신호가 됨.
    container_size = path.stat().st_size or 0
    try:
        with tarfile.open(path) as t:
            for info in t.getmembers():
                if not info.isfile():
                    continue
                head = b""
                sha = None
                capped = False
                try:
                    f = t.extractfile(info)
                    if f:
                        head, sha, capped = _hash_member(f)
                except (OSError, tarfile.TarError):
                    pass
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


def scan(root: Path) -> list[dict]:
    sig = _sig_mod()
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        suf, name = p.suffix.lower(), p.name.lower()
        if suf in ARCHIVE_EXTS or suf in TAR_EXTS or name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
            if zipfile.is_zipfile(p) or suf in TAR_EXTS or ".tar." in name:
                out.append(audit_archive(p, sig))
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
        print(json.dumps({"archives": len(records), "flagged_members": len(flagged),
                          "records": records}, ensure_ascii=False, indent=2))
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
