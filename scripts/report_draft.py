#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report_draft.py — 증거 폴더 하나로 한국어 포렌식 분석 보고서 초안 + 무결성 검증 결과를 만든다

    python scripts/report_draft.py <증거폴더> [-o 출력폴더] [--case-info 사건정보.txt]
                                   [--hashes 참조해시목록 ...] [--keywords 키워드1,키워드2]

하는 일 (모두 로컬, 표준 라이브러리만 필수):
  1. 증거 폴더 전체 파일의 MD5·SHA-1·SHA-256 계산 (분석 전)
  2. E01 이미지: 획득 기록값 판독 + 저장 해시와 재계산 해시 대조 (scripts/ewf_image.py)
  3. 참조 해시 목록(--hashes)과 대조 (scripts/hash_reference.py)
  4. 선조사 (scripts/case_survey.py): 시그니처 위장·중복·EXIF·압축·PDF·SQLite·개인정보 패턴 등
  5. 타임라인: 파일 수정 시각(KST 환산), EXIF 촬영 시각(시간대 미기록), 이미지 획득 기록
  6. 분석 후 전체 파일 재해시 → 분석 중 증거가 바뀌지 않았는지 대조
  7. 보고서초안.md / 보고서초안.html / 파일해시목록.csv / 무결성검증.json / 타임라인.html 작성
  8. 작성한 초안을 scripts/verify_report.py로 검증 (근거 없는 해시·금지 문구 차단)

원칙 (fail-closed): 측정하지 않은 값은 "미확인"으로 남긴다. 의견·결론·법적 판단은 쓰지 않는다.
증거 폴더에는 아무것도 쓰지 않는다 (출력 폴더가 증거 폴더 안이면 거부).

종료 코드: 0 = 초안 작성 완료(불일치 없음), 1 = 해시 불일치·판독 오류 또는 초안 검증 FAIL
           (초안은 그래도 작성됨), 2 = 사용법 오류
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import importlib.util
import io
import json
import platform
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import ewf_image  # noqa: E402
import hash_reference  # noqa: E402

UNK = "미확인"
TABLE_LIMIT = 100        # 보고서 본문 표 최대 행 수 (전체는 CSV/JSON)
TIMELINE_FULL_LIMIT = 60

CASE_FIELDS = [  # (키, 보고서 표기, 사건정보 파일에서 받는 이름들)
    ("case_number", "사건번호", ("사건번호", "case_number", "case no", "case")),
    ("case_name", "사건명", ("사건명", "case_name")),
    ("client", "의뢰인", ("의뢰인", "의뢰기관", "client")),
    ("request_date", "의뢰일자", ("의뢰일자", "의뢰일", "request_date")),
    ("purpose", "분석 목적", ("분석목적", "분석 목적", "purpose")),
    ("analyst", "분석 담당", ("분석담당", "분석 담당", "분석관", "analyst", "examiner")),
    ("organization", "작성 기관", ("작성기관", "작성 기관", "organization")),
]

CATEGORIES = [
    ("디스크 이미지", {".e01", ".ex01", ".l01", ".s01", ".dd", ".raw", ".img", ".001", ".vmdk", ".vhd", ".vhdx", ".aff4"}),
    ("이미지(사진)", {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".webp"}),
    ("영상", {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".3gp"}),
    ("오디오", {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".amr", ".wma"}),
    ("문서", {".pdf", ".doc", ".docx", ".hwp", ".hwpx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".odt"}),
    ("텍스트·로그", {".txt", ".log", ".csv", ".json", ".xml", ".html", ".htm", ".md", ".eml"}),
    ("압축 파일", {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz"}),
    ("데이터베이스", {".db", ".sqlite", ".sqlite3", ".sqlitedb"}),
    ("실행 파일", {".exe", ".dll", ".sys", ".msi", ".bat", ".ps1", ".sh", ".app", ".apk"}),
]
RAW_IMAGE_EXTS = {".raw", ".dd", ".img", ".001", ".bin", ""}


# ---------------------------------------------------------------- 공통

def tool_version() -> str:
    try:
        return json.loads((REPO / "plugin.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return UNK


def now_kst() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}"
    return f"{n} B"


def category(path: str) -> str:
    ext = Path(path).suffix.lower()
    if re.fullmatch(r"\.e\d\d", ext):
        return "디스크 이미지"
    for name, exts in CATEGORIES:
        if ext in exts:
            return name
    return "기타"


def md_cell(value) -> str:
    s = UNK if value is None or value == "" else str(value)
    return s.replace("|", "\\|").replace("\n", " ")


def table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(md_cell(c) for c in r) + " |")
    return out


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------- 입력

def load_case_info(path: Path | None) -> dict:
    info = {k: "" for k, _, _ in CASE_FIELDS}
    if path is None:
        return info
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        raw = json.loads(text)
        pairs = {str(k).strip(): str(v).strip() for k, v in raw.items() if v is not None}
    else:
        pairs = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([^:：=]+)[:：=]\s*(.*)$", line)
            if m:
                pairs[m.group(1).strip()] = m.group(2).strip()
    lowered = {k.lower().replace(" ", ""): v for k, v in pairs.items()}
    for key, _, names in CASE_FIELDS:
        for n in names:
            v = lowered.get(n.lower().replace(" ", ""))
            if v:
                info[key] = v
                break
    return info


def hash_file(path: Path) -> tuple[str, str, str]:
    md5, sha1, sha256 = hashlib.md5(), hashlib.sha1(), hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha1.hexdigest(), sha256.hexdigest()


def list_files(root: Path) -> tuple[list[Path], list[str]]:
    files, links = [], []
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            links.append(p.relative_to(root).as_posix())
        elif p.is_file():
            files.append(p)
    return files, links


def build_inventory(root: Path, files: list[Path]) -> list[dict]:
    inv = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        try:
            st = p.stat()
            md5, sha1, sha256 = hash_file(p)
            inv.append({"path": rel, "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime, KST).replace(microsecond=0).isoformat(),
                        "md5": md5, "sha1": sha1, "sha256": sha256, "category": category(rel)})
        except OSError as e:
            inv.append({"path": rel, "error": f"읽기 실패: {e.strerror or e}", "category": category(rel)})
    return inv


def post_check(root: Path, before: list[dict]) -> dict:
    files, _ = list_files(root)
    current = {p.relative_to(root).as_posix(): p for p in files}
    recorded = {e["path"]: e for e in before}
    changed, unreadable = [], []
    for rel in sorted(set(recorded) & set(current)):
        rec = recorded[rel]
        if "sha256" not in rec:
            continue
        try:
            _, _, sha256 = hash_file(current[rel])
        except OSError:
            unreadable.append(rel)
            continue
        if sha256 != rec["sha256"]:
            changed.append(rel)
    missing = sorted(set(recorded) - set(current))
    new = sorted(set(current) - set(recorded))
    ok = not (changed or missing or new or unreadable)
    return {"status": "match" if ok else "mismatch", "checked": len(set(recorded) & set(current)),
            "changed": changed, "missing": missing, "new": new, "unreadable": unreadable}


def compare_references(entries: list[dict], inventory: list[dict], images: list[dict]) -> list[dict]:
    image_by_stem = {}
    for img in images:
        stem = Path(img["file"]).name
        stem = re.sub(r"\.[Ee]01$", "", stem).lower()
        image_by_stem[stem] = img
    generic, results = [], []
    for e in entries:
        p = Path(e["name"])
        img = image_by_stem.get(p.stem.lower()) if p.suffix.lower() in RAW_IMAGE_EXTS else None
        if img is None:
            generic.append(e)
            continue
        computed = img.get("computed_hashes") or {}
        actual = computed.get(e["algorithm"])
        if not actual:
            results.append({**e, "result": "not_verified", "file": img["file"],
                            "note": "이미지 미디어 해시를 재계산하지 못함"})
        else:
            results.append({**e, "result": "match" if actual == e["hash"] else "mismatch",
                            "file": img["file"], "matched_by": "image_media", "actual": actual})
    ok_files = [f for f in inventory if "sha256" in f]
    return results + hash_reference.match_reference(generic, ok_files)


# ---------------------------------------------------------------- 선조사·타임라인

def run_survey(root: Path, inventory: list[dict], keywords: list[str]) -> dict:
    cs = _load_module("case_survey")
    manifest = {
        "manifest_version": 1, "tool": f"report_draft.py v{tool_version()}",
        "file_count": len(inventory),
        "files": [{"path": f["path"], "sha256": f.get("sha256"), "size": f.get("size"),
                   "mtime": f.get("mtime")} for f in inventory],
    }
    return cs.survey(root, keywords, False, manifest=manifest)


def step_result(survey: dict, key: str):
    step = (survey.get("steps") or {}).get(key)
    if not step:
        return None, "미수행"
    if step.get("status") != "ok":
        reason = step.get("error") or (step.get("stderr") or "").strip()[-160:] or "실패"
        reason = re.sub(r"^RuntimeError:\s*", "", reason)
        return None, f"미수행 — {reason}"
    return step.get("result"), None


def rel(root: Path, p: str) -> str:
    try:
        return Path(p).resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return Path(p).name


def relativize(obj, root: Path):
    """JSON 구조 안의 증거 폴더 절대 경로를 '폴더명/상대경로'로 바꾼다 (출력물에 분석 PC 경로를 남기지 않음)."""
    prefix = str(root)
    if isinstance(obj, dict):
        return {k: relativize(v, root) for k, v in obj.items()}
    if isinstance(obj, list):
        return [relativize(v, root) for v in obj]
    if isinstance(obj, str) and prefix in obj:
        return obj.replace(prefix, root.name)  # 오류 메시지 안에 섞인 경로까지
    return obj


def exif_naive(raw: str) -> str | None:
    m = re.match(r"^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})", raw.strip())
    if not m:
        return None
    try:
        dt = datetime(*[int(g) for g in m.groups()])
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def build_events(root: Path, inventory: list[dict], survey: dict, images: list[dict]) -> list[dict]:
    events = []
    for f in inventory:
        if "mtime" in f:
            events.append({"timestamp": f["mtime"], "category": "file", "source": "파일시스템(mtime)",
                           "description": f"파일 수정 시각(기록값): {f['path']}", "file": f["path"],
                           "details": f"SHA-256 {f['sha256']}"})
    exif, _ = step_result(survey, "exif")
    for r in (exif or {}).get("records", []):
        dto = (r.get("tags") or {}).get("DateTimeOriginal")
        ts = exif_naive(dto) if dto else None
        if ts:
            name = rel(root, r["file"])
            events.append({"timestamp": ts.replace(" ", "T"), "category": "media", "source": "EXIF",
                           "description": f"촬영 시각(카메라 기록값, 시간대 미기록): {name}", "file": name})
    for img in images:
        acq = (img.get("acquisition") or {}).get("acquisition_date", "")
        m = re.match(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC$", acq)
        if m:
            events.append({"timestamp": f"{m.group(1)}T{m.group(2)}+00:00", "category": "system",
                           "source": "E01 헤더", "description": f"이미지 획득 시각(이미지 기록값): {img['file']}",
                           "file": img["file"]})

    def key(ev):
        dt = datetime.fromisoformat(ev["timestamp"])
        if dt.tzinfo is None:  # EXIF: 시간대 미기록 → 정렬에서만 KST로 가정 (보고서에 명시)
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(timezone.utc)
    events.sort(key=key)
    return events


# ---------------------------------------------------------------- 보고서 본문

def render_report(ctx: dict) -> str:
    root_name = ctx["root_name"]
    inv = ctx["inventory"]
    ok_files = [f for f in inv if "sha256" in f]
    err_files = [f for f in inv if "error" in f]
    case = ctx["case"]
    images = ctx["images"]
    refs = ctx["references"]
    post = ctx["post"]
    survey = ctx["survey"]
    L: list[str] = []
    a = L.append

    a("# 디지털포렌식 분석 보고서 (초안)")
    a("")
    a("> 이 문서는 LazyForensic이 자동으로 만든 **초안**이다. 도구가 직접 측정한 값만 채웠고, "
      "측정하지 못했거나 기록이 없는 항목은 \"미확인\"으로 남겼다. 의견·결론·법적 판단은 쓰지 않았다. "
      "분석관이 원자료와 대조해 검토·보완한 뒤에만 사용한다.")
    a("")

    # 1
    a("## 1. 사건 개요")
    a("")
    L += table(["항목", "내용"], [[label, case.get(k) or UNK] for k, label, _ in CASE_FIELDS
                                   if k != "organization"])
    a("")

    # 2
    a("## 2. 분석 대상 증거")
    a("")
    a("### 가. 증거 요약")
    a("")
    total = sum(f["size"] for f in ok_files)
    a(f"- 증거 폴더: `{root_name}`")
    a(f"- 파일 수: {len(inv)}개 (해시 계산 {len(ok_files)}개, 읽기 실패 {len(err_files)}개)")
    a(f"- 총 크기: {fmt_size(total)} ({total:,} 바이트)")
    if ctx["links"]:
        a(f"- 심볼릭 링크 {len(ctx['links'])}개는 따라가지 않았다(대상 미확인): "
          + ", ".join(f"`{x}`" for x in ctx["links"][:10]))
    a("")
    cats = Counter(f["category"] for f in inv)
    sizes = Counter()
    for f in ok_files:
        sizes[f["category"]] += f["size"]
    L += table(["유형", "파일 수", "크기"], [[c, n, fmt_size(sizes[c])] for c, n in cats.most_common()])
    a("")
    if images:
        a("### 나. 디스크 이미지 (E01)")
        a("")
        a("아래 획득 정보는 이미지 파일 안에 획득 도구가 기록한 값이다. 이 도구가 획득 과정을 확인한 것은 아니다.")
        a("")
        for img in images:
            media = img.get("media") or {}
            acq = img.get("acquisition") or {}
            a(f"**{img['file']}**")
            a("")
            rows = [
                ["세그먼트 파일", ", ".join(img.get("segments") or []) or UNK],
                ["형식", img.get("format") or UNK],
                ["미디어 유형", media.get("media_type") or UNK],
                ["미디어 크기", f"{fmt_size(media['media_size'])} ({media['media_size']:,} 바이트, "
                              f"{media['sector_count']:,} 섹터 × {media['bytes_per_sector']} 바이트)"
                 if media else UNK],
                ["사건번호(기록값)", acq.get("case_number") or UNK],
                ["증거번호(기록값)", acq.get("evidence_number") or UNK],
                ["설명(기록값)", acq.get("description") or UNK],
                ["검사자(기록값)", acq.get("examiner") or UNK],
                ["메모(기록값)", acq.get("notes") or UNK],
                ["판독 상태", IMAGE_STATUS_KO.get(img["status"], img["status"])],
            ]
            if img.get("error"):
                rows.append(["오류", img["error"]])
            L += table(["항목", "내용"], rows)
            a("")
    sec = "다" if images else "나"
    a(f"### {sec}. 증거 파일 목록과 해시 (분석 전 계산)")
    a("")
    a("SHA-256만 싣는다. MD5·SHA-1·수정 시각을 포함한 전체 목록은 `파일해시목록.csv`에 있다.")
    a("")
    rows = [[i, f["path"], fmt_size(f["size"]), f["sha256"]] for i, f in enumerate(ok_files[:TABLE_LIMIT], 1)]
    L += table(["번호", "파일", "크기", "SHA-256"], rows)
    if len(ok_files) > TABLE_LIMIT:
        a("")
        a(f"(앞 {TABLE_LIMIT}개만 표시. 나머지 {len(ok_files) - TABLE_LIMIT}개는 `파일해시목록.csv` 참조)")
    if err_files:
        a("")
        a("읽지 못한 파일 (해시 미확인):")
        a("")
        for f in err_files[:TABLE_LIMIT]:
            a(f"- `{f['path']}` — {f['error']}")
    a("")

    # 3
    a("## 3. 무결성 검증 결과")
    a("")
    rows = []
    if post["status"] == "match":
        rows.append(["분석 전후 재해시", "일치", f"분석 전후 SHA-256 {post['checked']}개 모두 같음. 분석 중 증거가 바뀌지 않았다."])
    else:
        detail = []
        if post["changed"]:
            detail.append(f"변경 {len(post['changed'])}개")
        if post["missing"]:
            detail.append(f"사라짐 {len(post['missing'])}개")
        if post["new"]:
            detail.append(f"새로 생김 {len(post['new'])}개")
        if post["unreadable"]:
            detail.append(f"재해시 실패 {len(post['unreadable'])}개")
        rows.append(["분석 전후 재해시", "불일치", ", ".join(detail) + " — 아래 목록 확인"])
    for img in images:
        cmp_ = img.get("comparison") or {}
        verdict = IMAGE_STATUS_KO.get(img["status"], img["status"])
        algos = ", ".join(k.upper() for k in cmp_) or "-"
        rows.append([f"E01 저장 해시 대조 ({img['file']})", verdict, f"대조 알고리즘: {algos}"])
    if refs is None:
        rows.append(["참조 해시 대조", UNK, "참조 해시 목록이 제공되지 않음 (--hashes)"])
    else:
        c = Counter(r["result"] for r in refs)
        rows.append(["참조 해시 대조", "불일치 있음" if c.get("mismatch") else ("일치" if c.get("match") else UNK),
                     f"참조 {len(refs)}건: 일치 {c.get('match', 0)}, 불일치 {c.get('mismatch', 0)}, "
                     f"증거에 없음 {c.get('not_found', 0)}, 이름 중복으로 대조 보류 {c.get('ambiguous', 0)}, "
                     f"재계산 불가 {c.get('not_verified', 0)}"])
    rows.append(["쓰기 방지 장치 사용", UNK, "도구는 읽기만 했으나 쓰기 방지 장치 사용 기록은 제공되지 않음"])
    rows.append(["증거 인계 기록(Chain of Custody)", UNK, "인계 기록이 제공되지 않음 — 분석관이 기재"])
    L += table(["검증 항목", "결과", "근거"], rows)
    a("")
    if post["status"] != "match":
        for label, key in (("변경", "changed"), ("사라짐", "missing"), ("새로 생김", "new"), ("재해시 실패", "unreadable")):
            for p in post[key][:TABLE_LIMIT]:
                a(f"- {label}: `{p}`")
        a("")
    if images:
        a("### 가. E01 저장 해시와 재계산 해시")
        a("")
        rows = []
        for img in images:
            stored = img.get("stored_hashes") or {}
            comp = img.get("computed_hashes") or {}
            cmp_ = img.get("comparison") or {}
            for algo, label in (("md5", "MD5"), ("sha1", "SHA-1")):
                if stored.get(algo) or comp.get(algo):
                    if not stored.get(algo):
                        result = "대조 대상 없음(참고값)"
                    else:
                        result = {"match": "일치", "mismatch": "불일치"}.get(cmp_.get(algo), UNK)
                    rows.append([img["file"], label, stored.get(algo) or "기록 없음",
                                 comp.get(algo) or UNK, result])
            if comp.get("sha256"):
                rows.append([img["file"], "SHA-256", "기록 없음", comp["sha256"], "대조 대상 없음(참고값)"])
        L += table(["이미지", "알고리즘", "이미지에 저장된 값", "재계산 값", "결과"], rows)
        a("")
    if refs:
        a(f"### {'나' if images else '가'}. 참조 해시 대조 상세")
        a("")
        a("참조 목록: " + ", ".join(f"`{s}`" for s in ctx["reference_sources"]))
        a("")
        rows = []
        order = {"mismatch": 0, "match": 1, "ambiguous": 2, "not_verified": 3, "not_found": 4}
        for r in sorted(refs, key=lambda r: order.get(r["result"], 9))[:TABLE_LIMIT * 2]:
            label = REF_RESULT_KO.get(r["result"], r["result"])
            target = r.get("file") or "-"
            how = {"image_media": "이미지 미디어 해시와 대조", "basename": "파일명 일치",
                   "path_suffix": "경로 끝부분 일치", "path": "경로 일치"}.get(r.get("matched_by", ""),
                                                                       r.get("note") or "-")
            rows.append([r["name"], ALGO_KO[r["algorithm"]], r["hash"], target, label, how])
        L += table(["참조 항목", "알고리즘", "참조 해시", "대조한 증거", "결과", "대조 방법"], rows)
        if len(refs) > TABLE_LIMIT * 2:
            a("")
            a(f"(앞 {TABLE_LIMIT * 2}건만 표시. 전체는 `무결성검증.json`)")
        mism = [r for r in refs if r["result"] == "mismatch"]
        if mism:
            a("")
            a("불일치 항목의 실제 계산값:")
            a("")
            for r in mism[:TABLE_LIMIT]:
                # 파일명에 백틱을 붙이지 않는다 — verify_report.py의 해시-파일 결합 검사가 파일명을 읽어야 한다.
                a(f"- {r['file']} {ALGO_KO[r['algorithm']]} 실제 {r['actual']} / 참조 {r['hash']}")
        a("")

    # 4
    a("## 4. 획득 및 분석 환경")
    a("")
    a("### 가. 획득 환경")
    a("")
    if images:
        rows = []
        for img in images:
            acq = img.get("acquisition") or {}
            rows.append([img["file"], acq.get("acquiry_software_version") or UNK, acq.get("acquiry_os") or UNK,
                         acq.get("acquisition_date") or UNK])
        L += table(["이미지", "획득 도구 버전(기록값)", "획득 OS(기록값)", "획득 일시(기록값)"], rows)
        a("")
        a("- 획득 장비·쓰기 방지 장치·획득 장소: 미확인 (이미지에 기록되지 않음)")
    else:
        a("- 획득 도구·일시·장비: 미확인 (E01 이미지가 없고 획득 기록이 제공되지 않음)")
    a("")
    a("### 나. 분석 환경")
    a("")
    env = ctx["environment"]
    rows = [
        ["분석 일시 (KST)", f"{env['started']} ~ {env['finished']}"],
        ["분석 도구", f"LazyForensic {env['version']} (report_draft.py)"],
        ["운영체제", env["os"]],
        ["Python", env["python"]],
        ["선택 도구", env["optional"]],
    ]
    L += table(["항목", "내용"], rows)
    a("")

    # 5
    a("## 5. 분석 방법")
    a("")
    steps = ["증거 폴더의 모든 파일을 읽기 전용으로 열어 MD5·SHA-1·SHA-256을 계산했다."]
    if images:
        steps.append("E01 이미지는 파일 구조를 직접 해석해 획득 기록값을 읽고, 압축을 풀어 미디어 전체 해시를 "
                     "다시 계산해 이미지에 저장된 해시와 대조했다."
                     if not ctx["skip_image_verify"] else
                     "E01 이미지는 획득 기록값과 저장 해시만 읽었다(미디어 해시 재계산은 생략).")
    if refs is not None:
        steps.append("제공된 참조 해시 목록의 항목을 파일 경로·파일명으로 증거와 짝지어 같은 알고리즘의 해시를 대조했다. "
                     "원시 이미지(.raw/.dd 등) 항목은 이름이 같은 E01의 미디어 해시와 대조했다.")
    steps.append("확장자와 파일 시그니처 대조, 정확 중복, 이미지 EXIF·유사도, 압축 파일 내부, PDF 구조, "
                 "SQLite 구조, 개인정보 패턴, 영상·오디오 상태를 자동 점검했다(도구가 없는 항목은 미수행으로 표시).")
    steps.append("파일 수정 시각·EXIF 촬영 시각·이미지 획득 기록을 모아 타임라인을 만들었다.")
    steps.append("분석을 마친 뒤 모든 파일을 다시 해시해 분석 전 값과 대조했다.")
    steps.append("이 초안을 `verify_report.py`로 검사해 근거 없는 해시와 과단정 표현이 없는지 확인했다.")
    for i, s in enumerate(steps, 1):
        a(f"{i}. {s}")
    a("")

    # 6
    a("## 6. 분석 결과")
    a("")
    a("### 가. 자동 점검 결과")
    a("")
    L += findings_table(ctx)
    a("")
    details = findings_details(ctx)
    if details:
        a("### 나. 세부 관찰")
        a("")
        L += details
        a("")
    a(f"### {'다' if details else '나'}. 타임라인")
    a("")
    L += timeline_section(ctx)
    a("")

    # 7
    a("## 7. 종합")
    a("")
    a("### 가. 자동 집계된 사실")
    a("")
    for fact in summary_facts(ctx):
        a(f"- {fact}")
    a("")
    a("### 나. 분석관 의견")
    a("")
    a("미확인 — 분석관이 위 결과를 원자료와 대조한 뒤 직접 작성한다. 이 도구는 의견과 결론을 쓰지 않는다.")
    a("")

    # 8
    a("## 8. 한계 및 유의사항")
    a("")
    for lim in limitations(ctx):
        a(f"- {lim}")
    a("")

    a("## 부록. 산출물")
    a("")
    L += table(["파일", "내용"], [
        ["보고서초안.md / 보고서초안.html", "이 보고서 (HTML은 브라우저로 열어 인쇄·복사)"],
        ["파일해시목록.csv", "전체 파일의 경로·크기·수정 시각·MD5·SHA-1·SHA-256 (엑셀에서 열림)"],
        ["무결성검증.json", "분석 전 해시, 분석 후 재해시 대조, E01 판독, 참조 해시 대조 원자료"],
        ["타임라인.html / events.json", "전체 타임라인 (브라우저로 열기)"],
        ["선조사.json", "자동 점검 원자료"],
        ["초안검증.json", "이 초안에 대한 verify_report.py 결과"],
    ])
    a("")
    a(f"- 작성일: {ctx['environment']['finished'][:10]}")
    a(f"- 작성 기관: {case.get('organization') or UNK}")
    a(f"- 작성자: {case.get('analyst') or UNK}")
    a("")
    return "\n".join(L)


IMAGE_STATUS_KO = {
    "verified": "일치 (저장 해시 = 재계산 해시)",
    "mismatch": "불일치 (저장 해시 ≠ 재계산 해시)",
    "no_stored_hash": "미확인 (이미지에 저장된 해시 없음)",
    "not_verified": "미확인 (재계산 생략)",
    "read_error": "판독 오류 (이미지 손상 가능 — 미확인)",
    "unsupported": "미지원 형식 (미확인)",
}
REF_RESULT_KO = {"match": "일치", "mismatch": "불일치", "not_found": "증거에 없음",
                 "ambiguous": "대조 보류(같은 이름 여러 개)", "not_verified": "미확인"}
ALGO_KO = {"md5": "MD5", "sha1": "SHA-1", "sha256": "SHA-256"}


def findings_table(ctx: dict) -> list[str]:
    survey, root = ctx["survey"], ctx["root"]
    rows = []

    r, why = step_result(survey, "signatures")
    rows.append(["확장자-시그니처 대조", why or f"불일치 {len(r['mismatches'])}개 / 일치 {r['match']}개 (전체 {r['total']}개)",
                 "불일치는 확장자 변경 또는 시그니처 없는 형식일 수 있어 수동 확인 대상"])
    r, why = step_result(survey, "dedup")
    rows.append(["정확 중복 파일 (SHA-256 동일)", why or f"{len(r['exact_groups'])}개 그룹", "같은 내용의 사본"])
    r, why = step_result(survey, "exif")
    if r:
        recs = r["records"]
        present = sum(1 for x in recs if x.get("exif_present"))
        sw = sum(1 for x in recs if x.get("has_software"))
        gps = sum(1 for x in recs if x.get("has_gps"))
        errs = sum(1 for x in recs if x.get("error"))
        rows.append(["이미지 EXIF", f"이미지 {r['image_files']}개 중 EXIF 있음 {present}개, 편집 SW 기록 {sw}개, "
                                   f"GPS 기록 {gps}개, 판독 실패 {errs}개", "EXIF는 편집 가능한 기록값"])
    else:
        rows.append(["이미지 EXIF", why, "Pillow 설치 시 수행"])
    r, why = step_result(survey, "similar_images")
    rows.append(["유사 이미지", why or f"{len(r['similar_pairs'])}쌍 (이미지 {r['image_files']}개)", "지각 해시 근접 쌍"])
    r, why = step_result(survey, "archives")
    rows.append(["압축 파일 내부", why or f"압축 파일 {r['archives']}개, 주의 표시 멤버 {r['flagged_members']}개",
                 "zip/tar만 (압축 해제 없이)"])
    r, why = step_result(survey, "pdf")
    rows.append(["PDF 구조", why or f"PDF {r['pdf_files']}개, 암호화·능동 콘텐츠 표시 {r['flagged']}개", "표면 검사"])
    r, why = step_result(survey, "sqlite")
    rows.append(["SQLite 데이터베이스", why or f"DB {r['db_files']}개, 무결성 이상 {r['broken']}개", "읽기 전용 구조 조사"])
    r, why = step_result(survey, "pii")
    rows.append(["개인정보 패턴", why or f"{r['total_pii']}건 (텍스트 파일 {r['files_scanned']}개 검사)",
                 "외부 전달 전 마스킹 검토 대상. 정규식 기반"])
    r, why = step_result(survey, "audio")
    rows.append(["오디오", why or f"{r['audio_files']}개", "발화 구간 후보만 (전사 아님)"])
    r, why = step_result(survey, "video_integrity")
    if r:
        bad = sum(1 for x in r["records"] if x.get("status") != "OK")
        rows.append(["영상 무결성", f"영상 {r['video_files']}개, 이상 {bad}개", "디코드 오류·잘림 진단"])
    else:
        rows.append(["영상 무결성", why, "ffmpeg 설치 시 수행"])
    if "keywords" in (survey.get("steps") or {}):
        r, why = step_result(survey, "keywords")
        rows.append(["키워드 검색", why or f"{r['total_hits']}건 ({', '.join(r['keywords'])})",
                     "텍스트 파일 리터럴 검색"])
    return table(["점검 항목", "결과", "비고"], rows)


def findings_details(ctx: dict) -> list[str]:
    survey, root = ctx["survey"], ctx["root"]
    L: list[str] = []
    r, _ = step_result(survey, "signatures")
    if r and r["mismatches"]:
        L.append("확장자와 시그니처가 다른 파일:")
        L.append("")
        for m in r["mismatches"][:TABLE_LIMIT]:
            L.append(f"- `{rel(root, m['file'])}` — 확장자 {m['ext']}, 시그니처 {m['detected']}")
        L.append("")
    r, _ = step_result(survey, "dedup")
    if r and r["exact_groups"]:
        L.append("내용이 같은 파일 그룹:")
        L.append("")
        for g in r["exact_groups"][:TABLE_LIMIT]:
            files = g.get("files") or g.get("paths") or []
            L.append("- " + ", ".join(f"`{rel(root, x)}`" for x in files))
        L.append("")
    r, _ = step_result(survey, "exif")
    if r:
        models = Counter()
        for x in r["records"]:
            t = x.get("tags") or {}
            if t.get("Make") or t.get("Model"):
                make, model = t.get("Make", "").strip(), t.get("Model", "").strip()
                models[model if make and model.startswith(make) else f"{make} {model}".strip()] += 1
        if models:
            L.append("EXIF에 기록된 촬영 기기 (기록값):")
            L.append("")
            for name, n in models.most_common(20):
                L.append(f"- {name}: {n}개")
            L.append("")
        sw = [x for x in r["records"] if x.get("has_software")]
        if sw:
            L.append("EXIF에 소프트웨어 기록이 있는 이미지 (원본성 검토 대상):")
            L.append("")
            for x in sw[:TABLE_LIMIT]:
                L.append(f"- `{rel(root, x['file'])}` — {(x.get('tags') or {}).get('Software', '')}")
            L.append("")
    r, _ = step_result(survey, "archives")
    if r:
        flagged = [(rec["file"], m) for rec in r["records"] for m in rec.get("members", []) if m.get("flags")]
        if flagged:
            L.append("주의 표시가 붙은 압축 파일 멤버:")
            L.append("")
            for f, m in flagged[:TABLE_LIMIT]:
                L.append(f"- `{rel(root, f)}` 안의 `{m['name']}` — {', '.join(m['flags'])}")
            L.append("")
    r, _ = step_result(survey, "pii")
    if r and r["hits"]:
        L.append("개인정보 패턴이 나온 파일 (건수만 표시, 내용은 싣지 않음):")
        L.append("")
        for h in r["hits"][:TABLE_LIMIT]:
            L.append(f"- `{rel(root, h['file'])}`: {h['pii_count']}건")
        L.append("")
    while L and L[-1] == "":
        L.pop()
    return L


def timeline_section(ctx: dict) -> list[str]:
    events = ctx["events"]
    L: list[str] = []
    by_src: dict[str, list[dict]] = {}
    for ev in events:
        by_src.setdefault(ev["source"], []).append(ev)
    if not events:
        return ["시각 정보를 얻은 항목이 없다."]
    L.append(f"시각 정보 {len(events)}건. 출처별로 나눠 싣는다. 전체 시간순 보기는 `타임라인.html`.")
    L.append("")
    labels = {
        "파일시스템(mtime)": "파일 수정 시각 (파일시스템 기록값, KST 환산)",
        "EXIF": "촬영 시각 (EXIF 기록값, 시간대 미기록 — 카메라 시계 기준)",
        "E01 헤더": "이미지 획득 시각 (E01 기록값, UTC)",
    }
    for i, (src, evs) in enumerate(by_src.items()):
        L.append(f"**{chr(ord('1') + i)}) {labels.get(src, src)}**")
        L.append("")

        def show(e):
            ts = e["timestamp"]
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is not None and src != "E01 헤더":
                return fmt_dt(dt)
            if src == "E01 헤더":
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + " UTC"
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        if len(evs) <= TIMELINE_FULL_LIMIT:
            L += table(["시각", "파일"], [[show(e), e["file"]] for e in evs])
        else:
            days = Counter(show(e)[:10] for e in evs)
            L.append(f"{len(evs)}건 — 가장 이른 시각 {show(evs[0])} (`{evs[0]['file']}`), "
                     f"가장 늦은 시각 {show(evs[-1])} (`{evs[-1]['file']}`).")
            L.append("")
            L += table(["날짜", "건수"], [[d, n] for d, n in sorted(days.items())][:TABLE_LIMIT])
        L.append("")
    if "EXIF" in by_src:
        L.append("EXIF 시각에는 시간대가 기록되지 않는다. `타임라인.html`에서는 정렬을 위해서만 KST로 가정했다.")
    while L and L[-1] == "":
        L.pop()
    return L


def summary_facts(ctx: dict) -> list[str]:
    inv, images, refs, post = ctx["inventory"], ctx["images"], ctx["references"], ctx["post"]
    ok = [f for f in inv if "sha256" in f]
    facts = [f"증거 폴더 `{ctx['root_name']}`의 파일 {len(inv)}개 중 {len(ok)}개의 해시를 계산했다."]
    if post["status"] == "match":
        facts.append("분석 전후 재해시 결과가 모두 같아, 이 도구의 분석 과정에서 증거가 바뀌지 않았다.")
    else:
        facts.append("분석 전후 재해시 결과가 다르다 — 3장의 목록을 확인해야 한다.")
    for img in images:
        facts.append(f"E01 `{img['file']}`: {IMAGE_STATUS_KO.get(img['status'], img['status'])}.")
    if refs is not None:
        c = Counter(r["result"] for r in refs)
        facts.append(f"참조 해시 {len(refs)}건 중 일치 {c.get('match', 0)}건, 불일치 {c.get('mismatch', 0)}건, "
                     f"증거에 없음 {c.get('not_found', 0)}건.")
    r, _ = step_result(ctx["survey"], "signatures")
    if r:
        facts.append(f"확장자와 시그니처가 다른 파일 {len(r['mismatches'])}개.")
    r, _ = step_result(ctx["survey"], "dedup")
    if r:
        facts.append(f"내용이 같은 파일 그룹 {len(r['exact_groups'])}개.")
    return facts


def limitations(ctx: dict) -> list[str]:
    out = [
        "이 보고서는 자동 생성 초안이다. 법원·수사기관 제출 적격을 보증하지 않으며, 분석관 검토 없이 쓰지 않는다.",
        "파일 수정 시각은 운영체제가 기록한 값이다. 복사·내보내기·압축 해제 과정에서 바뀔 수 있고, "
        "$MFT·$SI/$FN 수준의 시각 검증은 하지 않았다.",
        "EXIF 값은 편집할 수 있는 기록값이며, 시간대가 기록되지 않는다. EXIF가 없다고 해서 변조를 뜻하지는 않는다.",
        "삭제 파일 복구·카빙·파일시스템 해석(NTFS/FAT 등)·메모리·레지스트리·메신저 DB 분석은 하지 않았다. "
        "E01은 해시 대조와 기록값 판독까지만 한다.",
        "참조 해시 대조는 파일 경로·파일명으로 짝을 짓는다. 이름이 같은 파일이 여러 개면 대조를 보류한다.",
        "개인정보 패턴·시그니처·중복·유사 이미지 결과는 검토 대상을 고르는 선별 결과이지 판정이 아니다.",
    ]
    for key, name in (("exif", "EXIF"), ("similar_images", "유사 이미지"), ("video_integrity", "영상 무결성"),
                      ("videos", "영상 지문"), ("audio_fp", "오디오 지문")):
        _, why = step_result(ctx["survey"], key)
        if why:
            out.append(f"{name} 점검: {why}")
    return out


# ---------------------------------------------------------------- HTML

def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    return s


def md_to_html(md: str, title: str) -> str:
    out, i = [], 0
    lines = md.splitlines()
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        m = re.match(r"^(#{1,4}) (.*)$", ln)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
            i += 1
            continue
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1

            def cells(r):
                parts = re.split(r"(?<!\\)\|", r.strip())[1:-1]
                return [c.strip().replace("\\|", "|") for c in parts]
            head = cells(rows[0])
            body = [cells(r) for r in rows[2:]]
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>")
            for r in body:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
            continue
        if ln.startswith("> "):
            out.append(f"<blockquote>{_inline(ln[2:])}</blockquote>")
            i += 1
            continue
        if re.match(r"^- ", ln):
            out.append("<ul>")
            while i < len(lines) and lines[i].startswith("- "):
                out.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            out.append("</ul>")
            continue
        if re.match(r"^\d+\. ", ln):
            out.append("<ol>")
            while i < len(lines) and re.match(r"^\d+\. ", lines[i]):
                item = re.sub(r"^\d+\. ", "", lines[i])  # f-string 안 역슬래시는 3.12 미만에서 문법 오류
                out.append(f"<li>{_inline(item)}</li>")
                i += 1
            out.append("</ol>")
            continue
        out.append(f"<p>{_inline(ln)}</p>")
        i += 1
    css = ("body{font-family:'Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif;max-width:1100px;"
           "margin:24px auto;padding:0 16px;line-height:1.6;color:#111}"
           "h1{border-bottom:2px solid #333;padding-bottom:6px}h2{border-bottom:1px solid #999;margin-top:32px}"
           "table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}"
           "th,td{border:1px solid #888;padding:4px 6px;vertical-align:top;word-break:break-all}"
           "th{background:#eee}blockquote{border-left:4px solid #c00;margin:8px 0;padding:6px 12px;background:#fff4f4}"
           "code{font-family:Consolas,Menlo,monospace;font-size:12px}"
           "@media print{body{margin:0;max-width:none}h2{page-break-after:avoid}tr{page-break-inside:avoid}}")
    return ("<!DOCTYPE html>\n<html lang=\"ko\"><head><meta charset=\"utf-8\">"
            f"<title>{html.escape(title)}</title><style>{css}</style></head><body>\n"
            + "\n".join(out) + "\n</body></html>\n")


# ---------------------------------------------------------------- 실행

def environment(started: datetime) -> dict:
    optional = []
    try:
        import PIL  # noqa: F401
        optional.append("Pillow 있음")
    except ImportError:
        optional.append("Pillow 없음(EXIF·유사 이미지 미수행)")
    for b in ("ffmpeg", "ffprobe", "fpcalc"):
        optional.append(f"{b} {'있음' if shutil.which(b) else '없음'}")
    return {
        "started": fmt_dt(started), "finished": "", "version": tool_version(),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(), "optional": ", ".join(optional),
    }


def write_csv(path: Path, inventory: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["번호", "경로", "유형", "크기(바이트)", "수정시각(KST)", "MD5", "SHA-1", "SHA-256", "비고"])
    for i, f in enumerate(inventory, 1):
        if "error" in f:
            w.writerow([i, f["path"], f["category"], "", "", "", "", "", f["error"]])
        else:
            w.writerow([i, f["path"], f["category"], f["size"], f["mtime"].replace("T", " ")[:19],
                        f["md5"], f["sha1"], f["sha256"], ""])
    path.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")


def run_verify(draft: Path, evidence: list[Path]) -> dict:
    cmd = [sys.executable, str(SCRIPTS / "verify_report.py"), str(draft), "--evidence",
           *[str(p) for p in evidence], "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        res = json.loads(proc.stdout)
    except ValueError:
        res = {"verdict": "ERROR", "errors": [proc.stderr.strip()[-500:] or "verify_report 출력 없음"], "warnings": []}
    res["report"] = draft.name
    res["exit_code"] = proc.returncode
    return res


def write_timeline_html(events: list[dict], out_json: Path, out_html: Path, root_name: str) -> str | None:
    out_json.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    if not events:
        return "시각 정보 없음"
    cmd = [sys.executable, str(REPO / "skills/forensic-timeline/scripts/generate_timeline.py"),
           "--input", str(out_json), "--output", str(out_html),
           "--title", f"타임라인 — {root_name}", "--target", root_name,
           "--max-events", str(max(len(events), 1)),
           "--timezone", "mtime=KST 환산, EXIF=시간대 미기록(정렬만 KST 가정), E01=UTC"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return None if proc.returncode == 0 else (proc.stderr.strip()[-300:] or "타임라인 생성 실패")


def generate(root: Path, out: Path, case_info: Path | None, hash_lists: list[Path], keywords: list[str],
             skip_image_verify: bool, log=print) -> dict:
    started = now_kst()
    root = root.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = environment(started)

    log(f"[1/7] 파일 목록·해시 계산: {root.name}")
    files, links = list_files(root)
    inventory = build_inventory(root, files)
    log(f"      파일 {len(inventory)}개")

    image_paths = [p for p in files if p.suffix.lower() == ".e01"]
    images = []
    if image_paths:
        log(f"[2/7] E01 이미지 {len(image_paths)}개 판독{'' if skip_image_verify else '·해시 재계산'}")
    for p in image_paths:
        res = ewf_image.examine(p, verify=not skip_image_verify)
        res["file"] = p.relative_to(root).as_posix()
        images.append(res)
        log(f"      {res['file']}: {IMAGE_STATUS_KO.get(res['status'], res['status'])}")

    references, sources = None, []
    if hash_lists:
        entries = []
        for h in hash_lists:
            got = hash_reference.load_reference(h)
            sources.append(h.name)
            entries += got
            log(f"[3/7] 참조 해시 목록 {h.name}: {len(got)}건 읽음")
        references = compare_references(entries, inventory, images)

    log("[4/7] 선조사 (시그니처·중복·EXIF·압축·PDF·DB·개인정보 패턴 등)")
    survey = run_survey(root, inventory, keywords)
    events = build_events(root, inventory, survey, images)

    log("[5/7] 분석 후 재해시")
    post = post_check(root, inventory)
    env["finished"] = fmt_dt(now_kst())

    ctx = {"root": root, "root_name": root.name, "inventory": inventory, "links": links, "case": load_case_info(case_info),
           "images": images, "references": references, "reference_sources": sources, "post": post,
           "survey": survey, "events": events, "environment": env, "skip_image_verify": skip_image_verify}

    log("[6/7] 보고서 초안 작성")
    integrity = {
        "tool": f"LazyForensic {env['version']} report_draft.py",
        "evidence_root": root.name, "analysis_started": env["started"], "analysis_finished": env["finished"],
        "files": inventory, "post_check": post, "images": images,
        "reference": None if references is None else {
            "sources": sources,
            # 해시 값은 알고리즘 이름 키(md5/sha1/sha256)로 둔다 — verify_report.py가 라벨로 근거 해시를 찾는다.
            "entries": [{**{k: v for k, v in r.items() if k != "hash"}, r["algorithm"]: r["hash"]}
                        for r in references]},
    }
    paths = {
        "md": out / "보고서초안.md", "html": out / "보고서초안.html", "csv": out / "파일해시목록.csv",
        "integrity": out / "무결성검증.json", "survey": out / "선조사.json", "events": out / "events.json",
        "timeline": out / "타임라인.html", "verify": out / "초안검증.json",
    }
    paths["integrity"].write_text(json.dumps(integrity, ensure_ascii=False, indent=2), encoding="utf-8")
    survey_out = relativize(json.loads(json.dumps(survey, ensure_ascii=False, default=str)), root)
    paths["survey"].write_text(json.dumps(survey_out, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(paths["csv"], inventory)
    timeline_err = write_timeline_html(events, paths["events"], paths["timeline"], root.name)
    md = render_report(ctx)
    paths["md"].write_text(md, encoding="utf-8")
    paths["html"].write_text(md_to_html(md, f"디지털포렌식 분석 보고서 (초안) — {root.name}"), encoding="utf-8")

    log("[7/7] 초안 검증 (verify_report.py)")
    verify = run_verify(paths["md"], [paths["integrity"]])
    paths["verify"].write_text(json.dumps(verify, ensure_ascii=False, indent=2), encoding="utf-8")

    problems = []
    if post["status"] != "match":
        problems.append("분석 전후 해시 불일치")
    problems += [f"E01 {i['file']}: {IMAGE_STATUS_KO[i['status']]}" for i in images
                 if i["status"] in ("mismatch", "read_error")]
    if references and any(r["result"] == "mismatch" for r in references):
        problems.append("참조 해시 불일치")
    if verify.get("verdict") in ("FAIL", "ERROR"):
        problems.append(f"초안 검증 {verify.get('verdict')}")
    return {"paths": paths, "post": post, "images": images, "references": references, "verify": verify,
            "problems": problems, "timeline_error": timeline_err, "inventory": inventory}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description="증거 폴더 → 한국어 포렌식 보고서 초안 + 무결성 검증 (로컬 전용)")
    ap.add_argument("root", nargs="?", help="증거 폴더 (E01 이미지나 내보낸 파일이 든 폴더)")
    ap.add_argument("-o", "--output", help="출력 폴더 (기본: 증거 폴더 옆 '<폴더명>_분석결과')")
    ap.add_argument("--case-info", help="사건 정보 파일 (templates/case_info_example.txt 형식 또는 JSON)")
    ap.add_argument("--hashes", nargs="+", action="extend", default=[],
                    help="참조 해시 목록 (획득 로그·해시표·sha256sum 출력 등). 여러 개 가능")
    ap.add_argument("--keywords", help="쉼표로 구분한 키워드 (텍스트 파일 검색)")
    ap.add_argument("--no-image-verify", action="store_true", help="E01 미디어 해시 재계산 생략 (대용량 시간 절약)")
    ap.add_argument("--case-template", metavar="파일", help="사건 정보 빈 양식을 이 경로에 쓰고 끝낸다")
    args = ap.parse_args(argv)

    if args.case_template:
        dst = Path(args.case_template)
        if dst.exists():
            print(f"이미 있는 파일입니다: {dst}", file=sys.stderr)
            return 2
        shutil.copyfile(REPO / "templates" / "case_info_example.txt", dst)
        print(f"사건 정보 양식 → {dst}  (값을 채운 뒤 --case-info {dst} 로 넘기세요)")
        return 0
    if not args.root:
        ap.print_help()
        return 2
    root = Path(args.root)
    if not root.is_dir():
        print(f"증거 폴더를 찾을 수 없습니다: {root}", file=sys.stderr)
        return 2
    root_r = root.resolve()
    out = Path(args.output) if args.output else root_r.parent / f"{root_r.name}_분석결과"
    out_r = out.resolve()
    if out_r == root_r or root_r in out_r.parents:
        print("출력 폴더가 증거 폴더 안에 있습니다. 증거 폴더에는 아무것도 쓰지 않습니다 — -o 로 다른 곳을 지정하세요.",
              file=sys.stderr)
        return 2
    case_info = Path(args.case_info) if args.case_info else None
    if case_info and not case_info.is_file():
        print(f"사건 정보 파일을 찾을 수 없습니다: {case_info}", file=sys.stderr)
        return 2
    hash_lists = [Path(h) for h in args.hashes]
    for h in hash_lists:
        if not h.is_file():
            print(f"참조 해시 목록을 찾을 수 없습니다: {h}", file=sys.stderr)
            return 2
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else []

    print(f"LazyForensic {tool_version()} — 보고서 초안 생성 (로컬 전용, 증거 폴더는 읽기만 함)")
    res = generate(root_r, out_r, case_info, hash_lists, keywords, args.no_image_verify)

    print("")
    print(f"완료: {res['paths']['html']}")
    print(f"      (브라우저로 열어 검토. 같은 폴더에 .md·파일해시목록.csv·무결성검증.json·타임라인.html)")
    post = res["post"]
    print(f"분석 전후 재해시: {'일치' if post['status'] == 'match' else '불일치'} ({post['checked']}개)")
    for img in res["images"]:
        print(f"E01 {img['file']}: {IMAGE_STATUS_KO.get(img['status'], img['status'])}")
    if res["references"] is not None:
        c = Counter(r["result"] for r in res["references"])
        print(f"참조 해시: 일치 {c.get('match', 0)} / 불일치 {c.get('mismatch', 0)} / 증거에 없음 {c.get('not_found', 0)}")
    v = res["verify"]
    print(f"초안 검증: {v.get('verdict')}" + (f" — {v['errors'][0]}" if v.get("errors") else ""))
    if res["timeline_error"]:
        print(f"타임라인: {res['timeline_error']}")
    if res["problems"]:
        print("확인 필요: " + "; ".join(res["problems"]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
