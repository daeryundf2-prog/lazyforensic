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
  저장 레코드가 아니므로 view_rows로 따로 센다. 조회하지 못한 뷰가 있으면(작성 앱 전용 함수 등)
  view_rows는 미확인(None)이고 조회한 뷰 소계만 visible_view_rows로 남긴다.
- 내부 객체는 이름이 'sqlite_'로 시작하는 것만 문자 그대로 비교해 뺀다('sqliteLog'는 사용자 테이블).
- 객체 유형은 SQLite가 밝힌 대로(PRAGMA table_list: table·view·virtual·shadow) 남긴다. FTS 같은 가상 테이블의
  조회 결과는 virtual_rows(논리 문서 수), 그 모듈의 관리용 테이블(색인·설정·원문 저장) 행은 shadow_rows로 따로
  센다 — 둘 다 저장 행 합계(total_rows)에 넣지 않는다. table_list가 없는 SQLite(3.37 미만)에서는 CREATE VIRTUAL
  TABLE 선언과 '<가상 테이블>_<content·data·idx 등>' 이름 규칙으로 구별한다.
- 컬럼 목록을 읽지 못하면(작성 앱 전용 함수를 쓰는 뷰 등) columns는 미확인(None)이고 사유는 columns_error다.
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
        # 이전 WAL 세대의 프레임이면 SQLite도 정상적으로 무시한다. 다만 salt가 어긋난 프레임 뒤에
        # 현 세대 salt가 다시 나오거나, salt-1이 현 세대보다 앞선 값이 아니면 이전 세대라고
        # 판정할 근거가 없다 — 손상된 프레임 뒤의 커밋이 버려졌을 수 있다.
        out["stale_frames"] = rest // frame
        current, later = 0, 0
        for pos in range(off, off + out["stale_frames"] * frame, frame):
            f1, f2 = struct.unpack(">2I", data[pos + 8:pos + 16])
            if (f1, f2) == (salt1, salt2):
                current += 1
            elif not 0 < (salt1 - f1) & 0xFFFFFFFF < 0x80000000:
                later += 1
        if current or later:
            out["complete"] = False
            out["generation_undetermined_frames"] = out["stale_frames"]
            out["limitations"].append(
                f"WAL {out['frames_valid'] + 1}번째 프레임의 salt가 현 세대와 다르지만 그 뒤 "
                f"{out['stale_frames']}개 프레임 중 현 세대 salt {current}개·이전 세대로 볼 수 없는 salt "
                f"{later}개 — 이전 세대 꼬리로 판정할 수 없어 그 뒤 커밋의 반영 여부 미확인")
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


def _approved_alias(path: Path, allowed: set[Path]) -> Path | None:
    """SQLite가 열 부수 파일 이름에 해당하는, 열거기가 승인한 실제 항목.

    대소문자를 구별하지 않는 볼륨(Windows·macOS 기본)에서는 'x.db-WAL'로 열거된 항목을 SQLite가
    'x.db-wal'로 연다. 같은 폴더의 승인된 항목 중 이름이 대소문자만 다르고 실제로 같은 파일(samefile)인
    것만 연결한다 — 이름을 전역 소문자화하지 않으므로 대소문자 구별 볼륨의 다른 파일은 결합하지 않는다.
    링크인 이름은 연결하지 않는다."""
    if path in allowed:
        return path
    if os.path.islink(path):
        return None
    key = path.name.casefold()
    for cand in allowed:
        if cand.parent == path.parent and cand.name.casefold() == key:
            try:
                if os.path.samefile(cand, path):
                    return cand
            except OSError:
                continue
    return None


# 가상 테이블 모듈(FTS3/4/5·R*Tree)이 '<가상 테이블>_<접미사>'로 만드는 관리용(shadow) 테이블.
# PRAGMA table_list(SQLite 3.37+)가 없을 때만 이 이름 규칙으로 구별한다.
SHADOW_SUFFIXES = {"content", "segments", "segdir", "docsize", "stat", "data", "idx", "config",
                   "node", "parent", "rowid"}


def object_types(cur: sqlite3.Cursor) -> dict[str, str]:
    """main 스키마 객체 이름 → SQLite가 밝힌 유형('table'·'view'·'virtual'·'shadow')."""
    try:
        listed = {r[1]: r[2] for r in cur.execute("PRAGMA table_list").fetchall() if r[0] == "main"}
    except sqlite3.Error:
        listed = {}
    if listed:
        return listed
    master = cur.execute("SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','view')").fetchall()
    virtual = {n for n, t, sql in master
               if t == "table" and (sql or "").lstrip().upper().startswith("CREATE VIRTUAL TABLE")}
    out = {}
    for name, ttype, _ in master:
        if name in virtual:
            out[name] = "virtual"
        elif ttype == "table" and any(name.startswith(v + "_") and name[len(v) + 1:] in SHADOW_SUFFIXES
                                      for v in virtual):
            out[name] = "shadow"
        else:
            out[name] = ttype
    return out


def _group_total(rec: dict, tables: list[dict], kind: str, key: str, label: str) -> list[dict]:
    """kind 객체의 행 수 합계를 rec[key]에 둔다. 하나라도 세지 못했으면 None, 센 소계는 visible_<key>."""
    group = [t for t in tables if t["type"] == kind]
    failed = [t["name"] for t in group if t["row_count"] is None]
    subtotal = sum(t["row_count"] for t in group if t["row_count"] is not None)
    if failed:
        rec[key] = None
        rec["visible_" + key] = subtotal
        rec[key.replace("_rows", "_note")] = (
            f"{label} {len(failed)}개 조회 실패({', '.join(failed)}) — 조회한 {len(group) - len(failed)}개 "
            f"{label} 소계 {subtotal}행, {label} 조회 결과 미확인")
    else:
        rec[key] = subtotal
    return group


def _survey_conn(conn: sqlite3.Connection, rec: dict, sample: tuple[str, int] | None) -> None:
    cur = conn.cursor()
    rec["page_size"] = cur.execute("PRAGMA page_size").fetchone()[0]
    rec["page_count"] = cur.execute("PRAGMA page_count").fetchone()[0]
    integ = cur.execute("PRAGMA integrity_check").fetchone()[0]
    rec["integrity"] = integ
    rec["integrity_ok"] = (integ == "ok")

    tables = []
    # 내부 객체(sqlite_master·sqlite_sequence·sqlite_stat1 등)만 뺀다. LIKE의 '_'는 임의의 한 글자이므로
    # 'sqliteLog' 같은 사용자 테이블까지 빠진다 — 접두사 7글자를 문자 그대로 비교한다.
    # SQLite는 대소문자와 관계없이 'sqlite_'로 시작하는 이름을 내부용으로 예약한다.
    rows = cur.execute(
        "SELECT name, type FROM sqlite_master "
        "WHERE type IN ('table','view') AND lower(substr(name, 1, 7)) <> 'sqlite_' "
        "ORDER BY name").fetchall()
    # FTS 같은 가상 테이블의 조회 결과(논리 문서)와 그 모듈의 관리용 테이블은 사용자 저장 행이 아니다.
    kinds = object_types(cur)
    for name, ttype in rows:
        entry: dict = {"name": name, "type": kinds.get(name, ttype)}
        ident = '"' + name.replace('"', '""') + '"'  # SQLite 식별자 인용 규칙
        try:
            entry["row_count"] = cur.execute(
                f'SELECT COUNT(*) FROM {ident}').fetchone()[0]
        except sqlite3.Error as e:
            entry["row_count"] = None
            entry["count_error"] = str(e)
        try:
            entry["columns"] = [r[1] for r in cur.execute(
                f'PRAGMA table_info({ident})').fetchall()]
        except sqlite3.Error as e:
            # 조회 실패는 '컬럼 0개'가 아니다 — 미확인과 사유를 남긴다.
            entry["columns"] = None
            entry["columns_error"] = str(e)
        if sample and sample[0] == name:
            try:
                entry["sample_rows"] = [
                    list(r) for r in cur.execute(
                        f'SELECT * FROM {ident} LIMIT ?', (sample[1],)).fetchall()]
            except sqlite3.Error as e:
                entry["sample_error"] = str(e)
        tables.append(entry)
    rec["tables"] = tables
    # 뷰는 테이블 행을 다시 보여 줄 뿐이므로 저장 레코드 합계에 넣지 않는다.
    # 집계하지 못한 저장 테이블이 하나라도 있으면 최종 합계는 미확인이고, 센 테이블의 소계만 남긴다.
    # 조회하지 못한 가상 테이블(모듈 없음 등)도 같다 — 그 모듈이 사용자 레코드를 어디에 두는지 알 수 없다.
    stored = [t for t in tables if t["type"] == "table"]
    failed = [t["name"] for t in tables if t["type"] in ("table", "virtual") and t["row_count"] is None]
    subtotal = sum(t["row_count"] for t in stored if t["row_count"] is not None)
    if failed:
        rec["total_rows"] = None
        rec["visible_rows"] = subtotal
        counted = sum(1 for t in stored if t["row_count"] is not None)
        rec.setdefault("limitations", []).append(
            f"테이블 {len(failed)}개 행 수 집계 실패({', '.join(failed)}) — 집계된 {counted}개 "
            f"테이블 소계 {subtotal}행, 최종 합계 미확인")
    else:
        rec["total_rows"] = subtotal
    # 조회하지 못한 뷰를 0행으로 더하지 않는다 — 조회한 뷰의 소계만 남기고 뷰 결과는 미확인이다.
    # 저장 행 합계(total_rows)와는 무관하므로 limitations(취득·집계 제한)에 넣지 않는다.
    _group_total(rec, tables, "view", "view_rows", "뷰")
    if _group_total(rec, tables, "virtual", "virtual_rows", "가상 테이블"):
        shadow = [t for t in tables if t["type"] == "shadow"]
        rec["shadow_tables"] = len(shadow)
        rec["shadow_rows"] = sum(t["row_count"] for t in shadow if t["row_count"] is not None)
    else:
        rec.pop("virtual_rows")
    unread = [f"{t['name']}({t['columns_error']})" for t in tables if t["columns"] is None]
    if unread:
        rec["columns_note"] = f"컬럼 미확인 {len(unread)}개 객체: " + ", ".join(unread)


def _survey_working_copy(path: Path, rec: dict, sidecars: list[tuple[Path, Path]],
                         sample: tuple[str, int] | None) -> None:
    """본체와 범위 안 -wal/-journal을 임시 사본으로 복사해 커밋된 상태를 읽는다. 원본은 열지 않는다.

    sidecars: (열거기가 승인한 실제 항목, SQLite가 찾는 이름) 쌍. 사본은 SQLite가 찾는 이름으로 둔다."""
    with tempfile.TemporaryDirectory(prefix="lf-sqlite-") as tmp:
        copy = Path(tmp) / path.name
        body_sha = _copy_hashed(path, copy)
        changed = [path.name] if body_sha != rec["sha256"] else []
        wal = None
        for entry, (src, expected) in zip(rec.get("sidecars", []), sidecars):
            if _copy_hashed(src, Path(tmp) / expected.name) != entry["sha256"]:
                changed.append(entry["file"])
            if expected.name.endswith("-wal"):
                wal, wal_copy = entry, Path(tmp) / expected.name
        if changed:
            raise OSError("조사 중 원본 바이트가 바뀜: " + ", ".join(changed))
        if wal is not None:
            # SQLite가 연 결과와 별개로, 복사한 바이트 그대로 프레임 경계·salt·체크섬을 검사한다.
            wal["audit"] = wal_audit(wal_copy)
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
            src = _approved_alias(s, allowed)
            reason = "증거 열거 범위 밖(링크·특수 파일 등으로 제외됨) — 읽지 않음"
        else:
            src = s if _in_scope(s) else None
            reason = evidence_manifest.LINK_REASON if os.path.islink(s) else evidence_manifest.SPECIAL_REASON
        if src is not None:
            sidecars.append((src, s))
        else:
            outside.append({"file": s.name, "reason": reason})
    if sidecars or outside:
        rec["sidecars"] = [{"file": src.name, "size_bytes": src.stat().st_size, "sha256": sha256_file(src),
                            "in_inventory": True} for src, _ in sidecars]
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
            rec["error"] = f"부수 파일({', '.join(src.name for src, _ in sidecars)}) 반영 실패 — 행 수 미확인: {e}"
            return rec
        if rec["limitations"]:
            # 열 수 있었던 상태의 행 수일 뿐, 마지막 커밋까지 취득했는지는 확인되지 않았다.
            if rec.get("total_rows") is not None:
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
    if rec.get("limitations"):
        rec["status"] = "partial"
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


def objects_text(rec: dict) -> str:
    """저장 행 합계 밖의 객체 표기: 뷰 조회 결과, 가상 테이블(FTS 등) 조회 결과, 그 관리용(shadow) 테이블."""
    tables = rec.get("tables") or []
    n_views = sum(1 for t in tables if t["type"] == "view")
    n_virtual = sum(1 for t in tables if t["type"] == "virtual")
    n_shadow = sum(1 for t in tables if t["type"] == "shadow")
    parts = []
    if n_views and rec.get("view_rows") is None:
        parts.append(f"뷰 {n_views}개 조회 결과 미확인(조회한 뷰 소계 {rec.get('visible_view_rows')}행, 저장 행 아님)")
    elif n_views:
        parts.append(f"뷰 {n_views}개 조회 결과 {rec['view_rows']}행(저장 행 아님)")
    if n_virtual and rec.get("virtual_rows") is None:
        parts.append(f"가상 테이블 {n_virtual}개 조회 결과 미확인(조회한 소계 {rec.get('visible_virtual_rows')}행, "
                     "저장 행 합계와 별도)")
    elif n_virtual:
        parts.append(f"가상 테이블 {n_virtual}개 조회 결과 {rec['virtual_rows']}행(전문검색 문서 등 논리 레코드, "
                     "저장 행 합계와 별도)")
    if n_shadow:
        parts.append(f"가상 테이블 관리용(shadow) 테이블 {n_shadow}개 {rec.get('shadow_rows')}행(색인·설정·원문 저장 — "
                     "사용자 레코드 아님, 합계 제외)")
    return "".join(", " + p for p in parts)


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
            line = f"{r['file']}: 테이블 {n_tables}개 {rows_text(r)}{objects_text(r)}"
            for s in r.get("sidecars", []):
                line += f", {s['file']} 반영(작업 사본)"
            print(f"{line}, 무결성={mark}")
            for text in r.get("limitations", []):
                print(f"  제한: {text}")
            for key, label in (("view_note", "뷰"), ("virtual_note", "가상 테이블"), ("columns_note", "컬럼")):
                if r.get(key):
                    print(f"  {label}: {r[key]}")
            for t in r["tables"]:
                count = t.get("row_count")
                shown = f"{count}행" if count is not None else f"미확인({t.get('count_error', '')})"
                cols = (f"컬럼 {len(t['columns'])}개" if t.get("columns") is not None
                        else f"컬럼 미확인({t.get('columns_error', '')})")
                print(f"  {t['name']} ({t['type']}): {shown}, {cols}")
                for row in t.get("sample_rows", []):
                    print(f"    표본: {row}")
    broken = [r for r in records if r.get("is_sqlite") and not r.get("integrity_ok")]
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
