#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sqlite_survey.py — SQLite 증거 DB 표면 조사 (stdlib 전용, 읽기 전용)

증거 폴더 안의 SQLite 파일(카카오톡·Chrome·각종 앱 DB)을
읽기 전용 + immutable 모드로 열어 구조만 조사한다:
- 테이블 목록과 행 수
- integrity_check 결과
- 페이지 크기/수, 스키마 버전
- 테이블별 컬럼 목록 (내용은 안 읽음)

중요: 원본은 immutable 모드로만 열어 -wal/-shm 부수 파일 생성이나
저널 롤백 등 원본 변경 가능성을 원천 차단한다. 같은 폴더에 -wal 또는
-journal이 있으면 본체와 함께 임시 작업 사본으로 복사해(복사한 바이트의
SHA-256을 기록) 사본에서 커밋된 상태를 읽는다. 원본은 그대로 둔다.
부수 파일은 증거 열거 규칙(evidence_manifest.walk_evidence)이 허용한 일반 파일만
쓴다. 링크 등 범위 밖 부수 파일은 열지 않고 out_of_scope_sidecars로 남기며 그 DB의
행 수는 미확인(visible_rows만 기록)이다.

WAL은 SQLite가 열 수 있는지와 별개로 프레임 경계·salt·체크섬을 직접 검사한다
(wal_audit). 잘린 프레임·체크섬 불일치·커밋 없는 꼬리 프레임이 있으면 마지막
완전한 커밋까지만 보이므로 total_rows는 미확인(None), 보이는 행 수는 visible_rows,
사유는 limitations에 남기고 status를 partial로 둔다.

사용:
    python scripts/sqlite_survey.py evidence/
    python scripts/sqlite_survey.py chats.db --json
    python scripts/sqlite_survey.py chats.db --sample 테이블명 3   # 표본 행 확인

알려진 한계:
- 스키마·행 수만 본다 — 내용 해석(어느 컬럼이 메시지인지)은 수동.
- total_rows는 실제 테이블에 저장된 행 수의 합이다. 뷰가 돌려주는 행 수는
  저장 레코드가 아니므로 view_rows로 따로 센다.
- 손상된 DB는 integrity_check가 실패로 보고한다 — 복구는 별도 도구.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import stat
import struct
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_json import print_json  # noqa: E402
import evidence_manifest  # noqa: E402

SQLITE_EXTS = {".db", ".sqlite", ".sqlite3", ".db3"}
_MAGIC = b"SQLite format 3\x00"  # 정확히 16바이트 헤더
# 커밋 상태에 영향을 주는 부수 파일. -shm은 WAL에서 다시 만들 수 있는 색인이라 읽지 않는다.
SIDECARS = ("-wal", "-journal")
WAL_MAGIC = (0x377F0682, 0x377F0683)  # 끝 비트 1이면 체크섬 단어가 빅엔디언
WAL_HEADER, FRAME_HEADER = 32, 24


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(16) == _MAGIC
    except OSError:
        return False


def connect_ro(path: Path) -> sqlite3.Connection:
    """immutable 읽기 전용 연결 — 원본·-wal·-shm을 절대 건드리지 않는다.
    파일명의 ?·#·공백 등이 URI 파싱을 깨지 못하게 인코딩한다."""
    uri = "file:" + urllib.parse.quote(str(path.resolve())) + "?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def _copy_hashed(src: Path, dst: Path) -> str:
    """src를 dst로 복사하면서 복사한 바이트의 SHA-256을 돌려준다."""
    h = hashlib.sha256()
    with src.open("rb") as fin, dst.open("wb") as fout:
        for chunk in iter(lambda: fin.read(1024 * 1024), b""):
            h.update(chunk)
            fout.write(chunk)
    return h.hexdigest()


def _wal_checksum(data: bytes, big_endian: bool, s0: int, s1: int) -> tuple[int, int]:
    """SQLite WAL 체크섬 (8바이트 단위 누적). data 길이는 8의 배수다."""
    words = struct.unpack((">" if big_endian else "<") + f"{len(data) // 4}I", data)
    for i in range(0, len(words), 2):
        s0 = (s0 + words[i] + s1) & 0xFFFFFFFF
        s1 = (s1 + words[i + 1] + s0) & 0xFFFFFFFF
    return s0, s1


def wal_audit(path: Path) -> dict:
    """WAL 파일을 프레임 단위로 검사해 마지막 완전한 커밋과 남은 바이트를 돌려준다.

    SQLite는 salt·체크섬이 맞는 프레임을 커밋 프레임까지만 반영하고 나머지는 조용히
    버린다. 그래서 '열 수 있음'은 '마지막 커밋까지 취득함'을 뜻하지 않는다.
    complete가 False면 limitations에 사유가 있다.
    """
    data = path.read_bytes()
    out: dict = {"size_bytes": len(data), "frames_valid": 0, "last_commit_frame": 0,
                 "uncommitted_frames": 0, "stale_frames": 0, "trailing_bytes": 0,
                 "complete": True, "limitations": []}
    if not data:
        return out
    if len(data) < WAL_HEADER:
        out.update(complete=False, trailing_bytes=len(data))
        out["limitations"].append(f"WAL 헤더가 잘림({len(data)}바이트 < 32) — 반영 가능 프레임 미확인")
        return out
    magic, _version, page_size, _seq, salt1, salt2, ck1, ck2 = struct.unpack(">8I", data[:WAL_HEADER])
    if magic not in WAL_MAGIC or page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
        out.update(complete=False, trailing_bytes=len(data) - WAL_HEADER)
        out["limitations"].append("WAL 헤더 형식이 맞지 않음 — SQLite가 WAL 전체를 무시함")
        return out
    big = bool(magic & 1)
    s0, s1 = _wal_checksum(data[:24], big, 0, 0)
    if (s0, s1) != (ck1, ck2):
        out.update(complete=False, trailing_bytes=len(data) - WAL_HEADER)
        out["limitations"].append("WAL 헤더 체크섬 불일치 — SQLite가 WAL 전체를 무시함")
        return out
    out["page_size"] = page_size
    frame = FRAME_HEADER + page_size
    off, stop = WAL_HEADER, "end"
    while off + frame <= len(data):
        pgno, commit, f1, f2, c1, c2 = struct.unpack(">6I", data[off:off + FRAME_HEADER])
        if (f1, f2) != (salt1, salt2):
            stop = "stale"
            break
        n0, n1 = _wal_checksum(data[off:off + 8], big, s0, s1)
        n0, n1 = _wal_checksum(data[off + FRAME_HEADER:off + frame], big, n0, n1)
        if (n0, n1) != (c1, c2) or pgno == 0:
            stop = "checksum"
            break
        s0, s1 = n0, n1
        out["frames_valid"] += 1
        if commit:
            out["last_commit_frame"] = out["frames_valid"]
        off += frame
    out["uncommitted_frames"] = out["frames_valid"] - out["last_commit_frame"]
    rest = len(data) - off
    if stop == "stale":
        out["stale_frames"] = rest // frame  # 이전 WAL 세대의 프레임 — SQLite도 정상적으로 무시한다
    elif stop == "checksum":
        out["complete"] = False
        out["trailing_bytes"] = rest
        out["limitations"].append(f"WAL {out['frames_valid'] + 1}번째 프레임의 체크섬 불일치 — "
                                  f"그 뒤 {rest}바이트 미반영")
    elif rest:
        out["trailing_bytes"] = rest
        tail = data[off:]
        salts = struct.unpack(">2I", tail[8:16]) if len(tail) >= 16 else None
        if salts is None or salts == (salt1, salt2):
            out["complete"] = False
            out["limitations"].append(f"WAL 끝 {rest}바이트가 프레임 크기({frame}바이트)보다 작음 — "
                                      "마지막 프레임이 잘린 취득본")
    if out["uncommitted_frames"] and stop != "checksum":
        out["complete"] = False
        out["limitations"].append(f"마지막 커밋 뒤 커밋 표시 없는 프레임 {out['uncommitted_frames']}개 — "
                                  "진행 중이던 트랜잭션이거나 잘린 취득본")
    return out


def _in_scope(path: Path) -> bool:
    """열거 규칙과 같은 판정: 링크·연결 지점이 아닌 일반 파일만 범위 안이다."""
    st = os.lstat(path)
    return stat.S_ISREG(st.st_mode) and getattr(st, "st_reparse_tag", 0) not in evidence_manifest.LINK_REPARSE_TAGS


def _survey_conn(conn: sqlite3.Connection, rec: dict, sample: tuple[str, int] | None) -> None:
    cur = conn.cursor()
    rec["page_size"] = cur.execute("PRAGMA page_size").fetchone()[0]
    rec["page_count"] = cur.execute("PRAGMA page_count").fetchone()[0]
    integ = cur.execute("PRAGMA integrity_check").fetchone()[0]
    rec["integrity"] = integ
    rec["integrity_ok"] = (integ == "ok")

    tables = []
    rows = cur.execute(
        "SELECT name, type FROM sqlite_master "
        "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name").fetchall()
    for name, ttype in rows:
        entry: dict = {"name": name, "type": ttype}
        try:
            entry["row_count"] = cur.execute(
                f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.Error as e:
            entry["row_count"] = None
            entry["count_error"] = str(e)
        try:
            entry["columns"] = [r[1] for r in cur.execute(
                f'PRAGMA table_info("{name}")').fetchall()]
        except sqlite3.Error:
            entry["columns"] = []
        if sample and sample[0] == name:
            try:
                entry["sample_rows"] = [
                    list(r) for r in cur.execute(
                        f'SELECT * FROM "{name}" LIMIT ?', (sample[1],)).fetchall()]
            except sqlite3.Error as e:
                entry["sample_error"] = str(e)
        tables.append(entry)
    rec["tables"] = tables
    # 뷰는 테이블 행을 다시 보여 줄 뿐이므로 저장 레코드 합계에 넣지 않는다.
    rec["total_rows"] = sum(t["row_count"] or 0 for t in tables if t["type"] == "table")
    rec["view_rows"] = sum(t["row_count"] or 0 for t in tables if t["type"] == "view")


def _survey_working_copy(path: Path, rec: dict, sidecars: list[Path],
                         sample: tuple[str, int] | None) -> None:
    """본체와 범위 안 -wal/-journal을 임시 사본으로 복사해 커밋된 상태를 읽는다. 원본은 열지 않는다."""
    with tempfile.TemporaryDirectory(prefix="lf-sqlite-") as tmp:
        copy = Path(tmp) / path.name
        body_sha = _copy_hashed(path, copy)
        copied = {s.name: _copy_hashed(s, Path(tmp) / s.name) for s in sidecars}
        changed = [path.name] if body_sha != rec["sha256"] else []
        for entry in rec.get("sidecars", []):
            if copied[entry["file"]] != entry["sha256"]:
                changed.append(entry["file"])
        if changed:
            raise OSError("조사 중 원본 바이트가 바뀜: " + ", ".join(changed))
        wal = next((e for e in rec.get("sidecars", []) if e["file"].endswith("-wal")), None)
        if wal is not None:
            # SQLite가 연 결과와 별개로, 복사한 바이트 그대로 프레임 경계·salt·체크섬을 검사한다.
            wal["audit"] = wal_audit(Path(tmp) / wal["file"])
            if not wal["audit"]["complete"]:
                rec["limitations"] += [f"{wal['file']}: {t}" for t in wal["audit"]["limitations"]]
        conn = sqlite3.connect(str(copy))
        try:
            if wal is not None:
                # 작업 사본의 WAL 색인 복구 결과 = 실제로 반영된 커밋 프레임 범위
                page_size = conn.execute("PRAGMA page_size").fetchone()[0]
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                busy, log, _ = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                wal["frames_in_file"] = max(wal["size_bytes"] - 32, 0) // (page_size + 24)
                wal["frames_committed"] = log if mode == "wal" and not busy else None
            _survey_conn(conn, rec, sample)
        finally:
            conn.close()


def survey_db(path: Path, sample: tuple[str, int] | None = None,
              allowed: set[Path] | None = None) -> dict:
    """allowed: 증거 열거기가 승인한 파일 집합. None이면 같은 규칙(링크가 아닌 일반 파일)으로 판정한다.

    범위 밖 부수 파일은 열지도 해시하지도 않는다. 그 DB와 WAL이 불완전한 DB의 행 수는
    total_rows=None(미확인)이고 보이는 행 수는 visible_rows, 사유는 limitations다.
    """
    rec: dict = {"file": str(path), "sha256": sha256_file(path),
                 "size_bytes": path.stat().st_size, "is_sqlite": is_sqlite(path)}
    if not rec["is_sqlite"]:
        rec["error"] = "SQLite 매직 없음 — 일반 파일이거나 다른 DB 형식"
        return rec
    present = [s for s in (path.with_name(path.name + suffix) for suffix in SIDECARS) if os.path.lexists(s)]
    sidecars, outside = [], []
    for s in present:
        if allowed is not None:
            ok = s in allowed
            reason = "증거 열거 범위 밖(링크·특수 파일 등으로 제외됨) — 읽지 않음"
        else:
            ok = _in_scope(s)
            reason = evidence_manifest.LINK_REASON if os.path.islink(s) else evidence_manifest.SPECIAL_REASON
        if ok:
            sidecars.append(s)
        else:
            outside.append({"file": s.name, "reason": reason})
    if sidecars or outside:
        rec["sidecars"] = [{"file": s.name, "size_bytes": s.stat().st_size, "sha256": sha256_file(s),
                            "in_inventory": True} for s in sidecars]
        rec["read_from"] = "working_copy"
        rec["limitations"] = []
        if outside:
            rec["out_of_scope_sidecars"] = outside
            rec["limitations"] += [f"{o['file']}: {o['reason']} — 커밋 상태 반영 여부 미확인" for o in outside]
        try:
            _survey_working_copy(path, rec, sidecars, sample)
        except (OSError, sqlite3.Error) as e:
            # 커밋 상태를 재생하지 못했으면 본체만 본 행 수를 확정값처럼 남기지 않는다.
            rec.pop("tables", None)
            rec["total_rows"] = None
            rec["view_rows"] = None
            rec["error"] = f"부수 파일({', '.join(s.name for s in sidecars)}) 반영 실패 — 행 수 미확인: {e}"
            return rec
        if rec["limitations"]:
            # 열 수 있었던 상태의 행 수일 뿐, 마지막 커밋까지 취득했는지는 확인되지 않았다.
            rec["visible_rows"] = rec["total_rows"]
            rec["total_rows"] = None
            rec["status"] = "partial"
        else:
            del rec["limitations"]
        return rec
    rec["read_from"] = "source_immutable"
    try:
        conn = connect_ro(path)
    except sqlite3.Error as e:
        rec["error"] = f"열기 실패: {e}"
        return rec
    try:
        _survey_conn(conn, rec, sample)
    except sqlite3.Error as e:
        rec["error"] = f"조사 실패: {e}"
    finally:
        conn.close()
    return rec


def unreadable(rec: dict) -> bool:
    """무결성 검사까지 가지 못한 후보 (매직 없음·열기 실패) — 무결성 결과는 미수행이다."""
    return "integrity" not in rec


def scan(root: Path, files: list[Path] | None = None) -> list[dict]:
    """files: 증거 열거기(walk_evidence)가 승인한 파일. 부수 파일도 이 집합 안의 것만 읽는다."""
    if files is None:
        files = evidence_manifest.walk_evidence(root)["files"]
    allowed = set(files)
    out = []
    for p in files:
        if "__pycache__" in p.parts:
            continue
        if p.suffix.lower() in SQLITE_EXTS or is_sqlite(p):
            out.append(survey_db(p, allowed=allowed))
    return out


def rows_text(rec: dict) -> str:
    """행 수 표기. 취득 완전성을 확인하지 못했으면 보이는 행 수와 최종 행 수 미확인을 함께 적는다."""
    if rec.get("total_rows") is None and rec.get("visible_rows") is not None:
        return f"보이는 행 {rec['visible_rows']}행(최종 행 수 미확인)"
    return f"총 {rec.get('total_rows')}행"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SQLite 증거 DB 표면 조사 (읽기 전용)")
    ap.add_argument("target", help="DB 파일 또는 디렉터리")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--sample", nargs=2, metavar=("테이블", "행수"),
                    help="특정 테이블의 표본 행 N개 확인")
    args = ap.parse_args(argv)

    sample = (args.sample[0], int(args.sample[1])) if args.sample else None
    target = Path(args.target)
    if target.is_dir():
        records = scan(target)
    elif target.is_file():
        records = [survey_db(target, sample)]
    else:
        print(f"대상을 찾을 수 없습니다: {target}", file=sys.stderr)
        return 2

    if args.json:
        print_json({"db_files": len(records), "records": records}, indent=2, default=str)
    else:
        for r in records:
            if not r.get("is_sqlite") or "error" in r and "tables" not in r:
                print(f"[실패] {r['file']}: {r.get('error', '')}")
                continue
            mark = "ok" if r["integrity_ok"] else "손상!"
            n_tables = sum(1 for t in r["tables"] if t["type"] == "table")
            n_views = len(r["tables"]) - n_tables
            line = f"{r['file']}: 테이블 {n_tables}개 {rows_text(r)}"
            if n_views:
                line += f", 뷰 {n_views}개 조회 결과 {r['view_rows']}행(저장 행 아님)"
            for s in r.get("sidecars", []):
                line += f", {s['file']} 반영(작업 사본)"
            print(f"{line}, 무결성={mark}")
            for text in r.get("limitations", []):
                print(f"  제한: {text}")
            for t in r["tables"]:
                print(f"  {t['name']} ({t['type']}): {t.get('row_count')}행, "
                      f"컬럼 {len(t.get('columns', []))}개")
                for row in t.get("sample_rows", []):
                    print(f"    표본: {row}")
    broken = [r for r in records if r.get("is_sqlite") and not r.get("integrity_ok")]
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
