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
import sqlite3
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_json import print_json  # noqa: E402

SQLITE_EXTS = {".db", ".sqlite", ".sqlite3", ".db3"}
_MAGIC = b"SQLite format 3\x00"  # 정확히 16바이트 헤더
# 커밋 상태에 영향을 주는 부수 파일. -shm은 WAL에서 다시 만들 수 있는 색인이라 복사하지 않는다.
SIDECARS = ("-wal", "-journal")


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
    """본체와 -wal/-journal을 임시 사본으로 복사해 커밋된 상태를 읽는다. 원본은 열지 않는다."""
    with tempfile.TemporaryDirectory(prefix="lf-sqlite-") as tmp:
        copy = Path(tmp) / path.name
        body_sha = _copy_hashed(path, copy)
        copied = {s.name: _copy_hashed(s, Path(tmp) / s.name) for s in sidecars}
        changed = [path.name] if body_sha != rec["sha256"] else []
        for entry in rec["sidecars"]:
            if copied[entry["file"]] != entry["sha256"]:
                changed.append(entry["file"])
        if changed:
            raise OSError("조사 중 원본 바이트가 바뀜: " + ", ".join(changed))
        conn = sqlite3.connect(str(copy))
        try:
            if any(s.name.endswith("-wal") for s in sidecars):
                # 작업 사본의 WAL 색인 복구 결과 = 실제로 반영된 커밋 프레임 범위
                wal = next(e for e in rec["sidecars"] if e["file"].endswith("-wal"))
                page_size = conn.execute("PRAGMA page_size").fetchone()[0]
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                busy, log, _ = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                wal["frames_in_file"] = max(wal["size_bytes"] - 32, 0) // (page_size + 24)
                wal["frames_committed"] = log if mode == "wal" and not busy else None
            _survey_conn(conn, rec, sample)
        finally:
            conn.close()


def survey_db(path: Path, sample: tuple[str, int] | None = None) -> dict:
    rec: dict = {"file": str(path), "sha256": sha256_file(path),
                 "size_bytes": path.stat().st_size, "is_sqlite": is_sqlite(path)}
    if not rec["is_sqlite"]:
        rec["error"] = "SQLite 매직 없음 — 일반 파일이거나 다른 DB 형식"
        return rec
    sidecars = [s for s in (path.with_name(path.name + suffix) for suffix in SIDECARS) if s.is_file()]
    if sidecars:
        rec["sidecars"] = [{"file": s.name, "size_bytes": s.stat().st_size, "sha256": sha256_file(s)}
                           for s in sidecars]
        rec["read_from"] = "working_copy"
        try:
            _survey_working_copy(path, rec, sidecars, sample)
        except (OSError, sqlite3.Error) as e:
            # 커밋 상태를 재생하지 못했으면 본체만 본 행 수를 확정값처럼 남기지 않는다.
            rec.pop("tables", None)
            rec["total_rows"] = None
            rec["view_rows"] = None
            rec["error"] = f"부수 파일({', '.join(s.name for s in sidecars)}) 반영 실패 — 행 수 미확인: {e}"
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


def scan(root: Path) -> list[dict]:
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix.lower() in SQLITE_EXTS or is_sqlite(p):
            out.append(survey_db(p))
    return out


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
            line = f"{r['file']}: 테이블 {n_tables}개 총 {r['total_rows']}행"
            if n_views:
                line += f", 뷰 {n_views}개 조회 결과 {r['view_rows']}행(저장 행 아님)"
            for s in r.get("sidecars", []):
                line += f", {s['file']} 반영(작업 사본)"
            print(f"{line}, 무결성={mark}")
            for t in r["tables"]:
                print(f"  {t['name']} ({t['type']}): {t.get('row_count')}행, "
                      f"컬럼 {len(t.get('columns', []))}개")
                for row in t.get("sample_rows", []):
                    print(f"    표본: {row}")
    broken = [r for r in records if r.get("is_sqlite") and not r.get("integrity_ok")]
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
