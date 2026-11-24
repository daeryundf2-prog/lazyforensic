#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence_manifest.py — 증거 매니페스트 생성기 (chain-of-custody 초안)

증거 디렉터리를 통째로 해시하고, 각 파일의 표면 메타데이터와 함께
도구 버전·생성 시각을 하나의 매니페스트로 고정한다.
이후 감사에서 같은 명령을 다시 돌려 파일 집합·해시가 그대로인지 대조한다.

사용:
    python scripts/evidence_manifest.py evidence/ -o manifest.json
    python scripts/evidence_manifest.py evidence/ --verify manifest.json

출력 필드:
    - file / sha256 / size / mtime (표면값 — $MFT 수준 검증 아님)
    - tool: 스크립트 버전, python 버전, 생성 시각(KST)

알려진 한계:
- os.stat 표면 메타데이터만 기록한다. 타임스탬핑 위조(timestomping)는
  이 도구로 판정할 수 없다.
- '무결성 보장'이 아니라 '변경 탐지'다. 원본 보존은 별도 읽기전용
  조치(scripts/lock_evidence.sh)와 병행할 것.
- 심볼릭 링크·연결 지점과 일반 파일이 아닌 항목은 따라가지 않고 excluded에
  이유와 함께 남긴다. 열지 못한 폴더는 enumeration_errors에 남기며, 이때
  complete는 false다 (그 폴더 안의 파일 수는 알 수 없다).
- 매니페스트(-o)를 증거 폴더 안에 쓰지 않는다 — 대조 대상이 바뀌기 때문이다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_json import print_json  # noqa: E402

KST = timezone(timedelta(hours=9))
TOOL_VERSION = "1.7.2"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# Windows reparse tags for links that point elsewhere (symbolic links and junctions/mount points).
LINK_REPARSE_TAGS = {0xA000000C, 0xA0000003}
LINK_REASON = "링크(심볼릭 링크·연결 지점) — 따라가지 않음"
SPECIAL_REASON = "일반 파일이 아님(장치·파이프 등) — 읽지 않음"


def _is_link(entry: os.DirEntry) -> bool:
    if entry.is_symlink():
        return True
    st = entry.stat(follow_symlinks=False)
    return getattr(st, "st_reparse_tag", 0) in LINK_REPARSE_TAGS


def walk_evidence(root: Path) -> dict:
    """증거 폴더의 일반 파일을 한 가지 규칙으로 열거한다 (보고서·선조사·매니페스트 공통).

    반환: {"files": [Path], "excluded": [{"path", "reason"}], "errors": [{"path", "error"}]}
    errors가 있으면 그 폴더 아래 파일 수는 알 수 없다.
    """
    files: list[Path] = []
    excluded: list[dict] = []
    errors: list[dict] = []

    def relpath(p: Path) -> str:
        return p.relative_to(root).as_posix() or "."

    pending = [root]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError as exc:
            errors.append({"path": relpath(folder), "error": exc.strerror or type(exc).__name__})
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                if _is_link(entry):
                    excluded.append({"path": relpath(path), "reason": LINK_REASON})
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
                else:
                    excluded.append({"path": relpath(path), "reason": SPECIAL_REASON})
            except OSError as exc:
                errors.append({"path": relpath(path), "error": exc.strerror or type(exc).__name__})
    excluded.sort(key=lambda e: e["path"])
    errors.sort(key=lambda e: e["path"])
    return {"files": sorted(files), "excluded": excluded, "errors": errors}


def inside(root: Path, target: Path) -> bool:
    """target이 root 자체이거나 그 아래에 있으면 True (출력 경로 사전 검사용)."""
    root_r, target_r = root.resolve(), target.resolve()
    return target_r == root_r or root_r in target_r.parents


PARTIAL_SCOPE_EXIT = 1  # report·scan·manifest 공통: 열거 실패로 조사 범위가 부분이면 이 종료 코드 (docs/EXIT_CODES.md)


def partial_scope_message(errors: list[dict]) -> str:
    """열거 실패로 범위가 부분일 때 세 명령이 표준 오류에 같은 형식으로 남기는 한 줄."""
    shown = ", ".join(e["path"] for e in errors[:3]) + (f" 외 {len(errors) - 3}개" if len(errors) > 3 else "")
    return (f"조사 범위 미확인(부분 결과): 열거하지 못한 폴더 {len(errors)}개 ({shown}) — "
            f"그 아래 파일 수를 알 수 없습니다. 종료 코드 {PARTIAL_SCOPE_EXIT}")


def build_manifest(root: Path) -> dict:
    walk = walk_evidence(root)
    files = []
    for f in walk["files"]:
        st = f.stat()
        files.append({
            "path": f.relative_to(root).as_posix(),
            "sha256": sha256_file(f),
            "size": st.st_size,
            # 부동소수 st_mtime은 자정 1 ns 전을 다음 날로 올린다. 정수 ns를 마이크로초로 버림한다.
            "mtime": (datetime(1970, 1, 1, tzinfo=timezone.utc)
                      + timedelta(microseconds=st.st_mtime_ns // 1000)).astimezone(KST).isoformat(),
            "mtime_ns": st.st_mtime_ns,
        })
    return {
        "manifest_version": 1,
        "tool": f"evidence_manifest.py v{TOOL_VERSION}",
        "python": platform.python_version(),
        "generated_at": datetime.now(KST).isoformat(),
        "root": str(root.resolve()),
        "file_count": len(files),
        "complete": not walk["errors"],
        "excluded": walk["excluded"],
        "enumeration_errors": walk["errors"],
        "files": files,
    }


def verify_manifest(root: Path, manifest: dict) -> list[str]:
    problems = []
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        return ["INVALID: files 목록 없음"]
    entries = manifest["files"]
    if manifest.get("file_count", len(entries)) != len(entries):
        return ["INVALID: file_count 불일치"]
    recorded_root = manifest.get("root")
    if recorded_root is not None and not isinstance(recorded_root, str):
        return ["INVALID: root 경로 오류"]
    if recorded_root and str(Path(recorded_root)) != str(root.resolve()):
        # 불일치로 세지 않고 경고만 — 다른 경로로 옮긴 사본을 대조하는 정상 사용도 있다.
        print(f"경고: 매니페스트 기록 루트({recorded_root})가 검증 대상({root.resolve()})과 "
              "다릅니다 — 다른 위치로 옮겨졌거나 다른 디렉터리의 매니페스트일 수 있습니다",
              file=sys.stderr)
    windows_paths = bool(recorded_root and PureWindowsPath(recorded_root).drive)
    recorded = {}
    for entry in entries:
        if not isinstance(entry, dict):
            return ["INVALID: 파일 항목 오류"]
        path, digest = entry.get("path"), entry.get("sha256")
        if not isinstance(path, str) or not path or not isinstance(digest, str) or not re.fullmatch("[a-fA-F0-9]{64}", digest):
            return ["INVALID: 파일 경로 또는 SHA-256 오류"]
        if windows_paths:
            path = path.replace("\\", "/")
        if path in recorded:
            return [f"DUPLICATE: {path}"]
        recorded[path] = digest.lower()
    walk = walk_evidence(root)
    current = {f.relative_to(root).as_posix(): f for f in walk["files"]}
    problems += [f"UNLISTED: {e['path']} ({e['error']}) — 폴더를 열지 못해 하위 파일 미확인" for e in walk["errors"]]

    for path in sorted(set(recorded) - set(current)):
        problems.append(f"MISSING: {path}")
    for path in sorted(set(current) - set(recorded)):
        problems.append(f"NEW: {path}")
    for path in sorted(set(recorded) & set(current)):
        digest = sha256_file(current[path])
        if digest != recorded[path]:
            problems.append(f"CHANGED: {path}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="증거 매니페스트 생성·대조 (로컬 전용)")
    ap.add_argument("root", help="증거 디렉터리")
    ap.add_argument("-o", "--output", default=None, help="매니페스트 출력 경로")
    ap.add_argument("--verify", metavar="MANIFEST", default=None,
                    help="기존 매니페스트와 대조")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"디렉터리를 찾을 수 없습니다: {root}", file=sys.stderr)
        return 2

    if args.verify:
        try:
            manifest = json.loads(Path(args.verify).read_text(encoding="utf-8-sig"))
            problems = verify_manifest(root, manifest)
        except (OSError, ValueError) as e:
            print(f"매니페스트를 읽을 수 없습니다: {e}", file=sys.stderr)
            return 2
        if problems:
            print(f"불일치 {len(problems)}건:")
            for p in problems:
                print(f"  {p}")
            return 1
        print(f"일치 — {manifest.get('file_count', '?')}개 파일 모두 해시 동일")
        return 0

    if args.output and inside(root, Path(args.output)):
        print("매니페스트 출력 경로가 증거 폴더 안에 있습니다. 증거 폴더에는 아무것도 쓰지 않습니다 — "
              "-o 로 증거 폴더 밖을 지정하세요.", file=sys.stderr)
        return 2
    manifest = build_manifest(root)
    out = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"매니페스트 → {args.output} ({manifest['file_count']}개 파일)")
    else:
        print_json(manifest, indent=2)
    for e in manifest["excluded"]:
        print(f"제외: {e['path']} — {e['reason']}", file=sys.stderr)
    if manifest["enumeration_errors"]:
        for e in manifest["enumeration_errors"]:
            print(f"열거 실패: {e['path']} ({e['error']})", file=sys.stderr)
        print(partial_scope_message(manifest["enumeration_errors"]), file=sys.stderr)
        return PARTIAL_SCOPE_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
