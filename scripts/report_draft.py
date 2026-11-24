#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report_draft.py — 증거 폴더 하나로 한국어 포렌식 분석 보고서 초안 + 무결성 검증 결과를 만든다

    python scripts/report_draft.py <증거폴더> [-o 출력폴더] [--case-info 사건정보.txt]
                                   [--hashes 참조해시목록 ...] [--keywords 키워드1,키워드2]

하는 일 (모두 로컬, 표준 라이브러리만 필수):
  1. 증거 폴더 전체 파일의 MD5·SHA-1·SHA-256 계산 (분석 전)
  2. EWF 이미지: 획득 기록값 판독 + 저장 해시와 재계산 해시 대조 (scripts/ewf_image.py)
  3. 참조 해시 목록(--hashes)과 대조 (scripts/hash_reference.py)
  4. 선조사 (scripts/case_survey.py): 시그니처 위장·중복·EXIF·압축·PDF·SQLite·개인정보 패턴 등
  5. 타임라인: 파일 수정 시각(KST 환산), EXIF 촬영 시각(시간대 미기록), 이미지 획득 기록
  6. 분석 후 전체 파일 재해시 → 분석 중 증거가 바뀌지 않았는지 대조
  7. 보고서초안.hwpx / 보고서초안.docx / 보고서초안.md / 보고서초안.html / 파일해시목록.csv / 무결성검증.json / 타임라인.html 작성
  8. 작성한 초안을 scripts/verify_report.py로 검증 (근거 없는 해시·금지 문구 차단)

원칙 (fail-closed): 측정하지 않은 값은 "미확인"으로 남긴다. 의견·결론·법적 판단은 쓰지 않는다.
증거 폴더에는 아무것도 쓰지 않는다 (출력 폴더가 증거 폴더 안이면 거부).

종료 코드: 0 = 초안 작성 완료(불일치 없음), 1 = 해시 불일치·판독 오류·조사 범위 미확인(폴더 열거 실패,
           scan·manifest와 같은 코드) 또는 초안 검증 FAIL (초안은 그래도 작성됨), 2 = 사용법 오류
"""
from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import html
import importlib.util
import io
import json
import os
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

import report_templates  # noqa: E402
import ewf_image  # noqa: E402
import evidence_manifest  # noqa: E402
import exif_audit  # noqa: E402
import hash_reference  # noqa: E402
import keyword_report  # noqa: E402
import merge_timeline  # noqa: E402
import report_docx  # noqa: E402
import report_hwpx  # noqa: E402

UNK = "미확인"
TABLE_LIMIT = 100        # 보고서 본문 표 최대 행 수 (전체는 CSV/JSON)
TIMELINE_FULL_LIMIT = 60
RUN_LISTING = "산출물목록.json"  # 실행마다 새 출력 폴더 + 이 목록 (이전 실행 파일이 섞이지 않음)

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
    ("디스크 이미지", {".e01", ".ex01", ".l01", ".lx01", ".s01", ".dd", ".raw", ".img", ".001", ".vmdk", ".vhd", ".vhdx", ".aff4"}),
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


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def kst_from_ns(ns: int, digits: int = 9) -> str:
    """정수 나노초 → KST ISO 8601. 소수는 digits 자리에서 버린다.

    부동소수 초를 거치거나 반올림하면 자정 1 ns 전이 다음 날로 넘어간다.
    """
    secs, frac = divmod(ns, 10**9)
    dt = (EPOCH + timedelta(seconds=secs)).astimezone(KST)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{frac:09d}"[:digits + 1] + "+09:00"


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
    if re.fullmatch(r"\.[el]x?\d\d", ext):
        return "디스크 이미지"
    for name, exts in CATEGORIES:
        if ext in exts:
            return name
    return "기타"


def md_cell(value) -> str:
    s = UNK if value is None or value == "" else str(value)
    return re.sub(r"[\r]\n|[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]", " ", s.replace("|", "\\|"))


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

class CaseInfoError(ValueError):
    """The supplied case metadata could not be read."""


def load_case_info(path: Path | None) -> dict:
    info = {k: "" for k, _, _ in CASE_FIELDS}
    if path is None:
        return info
    try:
        data = path.read_bytes()
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = data.decode(encoding)
        if path.suffix.lower() == ".json":
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError("JSON 객체가 필요합니다")
    except (OSError, ValueError) as exc:
        raise CaseInfoError(f"사건 정보 파일을 읽을 수 없습니다 ({path.name}): {exc}") from exc
    if path.suffix.lower() == ".json":
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


def build_inventory(root: Path, files: list[Path]) -> list[dict]:
    inv = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        try:
            st = p.stat()
            md5, sha1, sha256 = hash_file(p)
            inv.append({"path": rel, "size": st.st_size,
                        "mtime": kst_from_ns(st.st_mtime_ns), "mtime_ns": st.st_mtime_ns,
                        "md5": md5, "sha1": sha1, "sha256": sha256, "category": category(rel)})
        except OSError as e:
            inv.append({"path": rel, "error": f"열기 실패: {e.strerror or type(e).__name__}", "category": category(rel)})
    return inv


def post_check(root: Path, before: list[dict]) -> dict:
    walk = evidence_manifest.walk_evidence(root)
    current = {p.relative_to(root).as_posix(): p for p in walk["files"]}
    recorded = {e["path"]: e for e in before}
    changed, unreadable = [], []
    checked = 0
    for rel in sorted(set(recorded) & set(current)):
        rec = recorded[rel]
        if "sha256" not in rec:
            unreadable.append(rel)
            continue
        try:
            _, _, sha256 = hash_file(current[rel])
        except OSError:
            unreadable.append(rel)
            continue
        checked += 1
        if sha256 != rec["sha256"]:
            changed.append(rel)
    missing = sorted(set(recorded) - set(current))
    new = sorted(set(current) - set(recorded))
    unlisted = [e["path"] for e in walk["errors"]]
    status = "mismatch" if changed or missing or new else ("incomplete" if unreadable or unlisted else "match")
    return {"status": status, "checked": checked, "changed": changed, "missing": missing, "new": new,
            "unreadable": unreadable, "unlisted_folders": unlisted}


def scope_record(walk: dict, inventory: list[dict]) -> dict:
    """조사 범위: 열거한 파일·제외 항목·열지 못한 폴더. 열거 실패가 있으면 전체 파일 수는 미확인이다."""
    complete = not walk["errors"]
    return {"status": "complete" if complete else "partial", "files_found": len(inventory),
            "total_files": len(inventory) if complete else None,
            "unlisted_folders": walk["errors"], "excluded": walk["excluded"]}


def compare_references(entries: list[dict], inventory: list[dict], images: list[dict]) -> list[dict]:
    results = hash_reference.match_reference(entries, inventory)
    physical = [img for img in images if "entries" not in img and Path(img["file"]).suffix.lower() in (".e01", ".ex01")]
    for i, (entry, result) in enumerate(zip(entries, results)):
        name = Path(entry["name"])
        if result["result"] != "not_found" or result["file"] or name.suffix.lower() not in RAW_IMAGE_EXTS:
            continue
        # Resolve the source name across both physical formats, retaining path ambiguity.
        media_files = [{"path": Path(img["file"]).with_suffix(name.suffix).as_posix(),
                        **(img.get("computed_hashes") or {})} for img in physical]
        media, = hash_reference.match_reference([entry], media_files)
        if media["file"]:
            media["file"] = next(img["file"] for img, candidate in zip(physical, media_files)
                                 if candidate["path"] == media["file"])
            if media["result"] == "not_found":
                media.update(result="not_verified", note="이미지 미디어 해시를 재계산하지 못함")
            else:
                media["matched_by"] = "image_media"
        results[i] = {**media, "name": entry["name"]}
    return results


# ---------------------------------------------------------------- 선조사·타임라인

def run_survey(root: Path, inventory: list[dict], scope: dict, keywords: list[str],
               keyword_encoding: str | None = None) -> dict:
    cs = _load_module("case_survey")
    manifest = {
        "manifest_version": 1, "tool": f"report_draft.py v{tool_version()}",
        "file_count": len(inventory), "complete": scope["status"] == "complete",
        "excluded": scope["excluded"], "enumeration_errors": scope["unlisted_folders"],
        "files": [{"path": f["path"], "sha256": f.get("sha256"), "size": f.get("size"),
                   "mtime": f.get("mtime")} for f in inventory],
    }
    return cs.survey(root, keywords, False, manifest=manifest, keyword_encoding=keyword_encoding)


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


def build_events(root: Path, inventory: list[dict], survey: dict, images: list[dict]) -> list[dict]:
    events = []
    for f in inventory:
        if "mtime_ns" in f:
            # timestamp는 ISO 파서가 읽을 수 있게 마이크로초까지(버림), 원값은 mtime·mtime_ns.
            events.append({"timestamp": kst_from_ns(f["mtime_ns"], 6), "category": "file",
                           "source": "파일시스템(mtime)", "description": f"파일 수정 시각(기록값): {f['path']}",
                           "file": f["path"], "mtime": f["mtime"], "mtime_ns": f["mtime_ns"],
                           "details": f"SHA-256 {f['sha256']}"})
    exif, _ = step_result(survey, "exif")
    for r in (exif or {}).get("records", []):
        shot = exif_audit.capture_time(r.get("tags") or {})
        if shot:
            name = rel(root, r["file"])
            events.append({"timestamp": shot["timestamp"], "category": "media", "source": "EXIF",
                           "description": f"촬영 시각(카메라 기록값, {exif_audit.zone_note(shot)}): {name}",
                           "file": name, **exif_audit.event_fields(shot)})
    for img in images:
        acq = (img.get("acquisition") or {}).get("acquisition_date", "")
        m = re.match(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC$", acq)
        if m:
            events.append({"timestamp": f"{m.group(1)}T{m.group(2)}+00:00", "category": "system",
                           "source": "EWF 헤더", "description": f"이미지 획득 시각(이미지 기록값): {img['file']}",
                           "file": img["file"]})

    # 보고서·timeline --manifest·양식이 같은 정수 나노초 기준으로 정렬한다.
    return merge_timeline.order_events(events)


# ---------------------------------------------------------------- 보고서 본문

def image_hash_detail(img: dict) -> str:
    """검증표의 근거 칸 — 실제로 대조한 알고리즘만 적고, 대조하지 못했으면 미수행이라고 적는다."""
    if "entries" in img:
        entry_results = Counter(e["status"] for e in img["entries"] if e["type"] == "file" or e["size"] or e["stored_hashes"])
        return ", ".join(f"{IMAGE_STATUS_KO[k]} {n}건" for k, n in entry_results.items()) or "-"
    compared = [ALGO_KO[k] for k in img.get("comparison") or {}]
    if compared:
        return "대조한 알고리즘: " + ", ".join(compared)
    if img.get("computed_hashes"):
        return "미디어 해시는 재계산했으나 저장 해시가 없어 대조 미수행"
    return "해시 재계산·대조 미수행" + (f" — {img['error']}" if img.get("error") else "")


def image_method(img: dict) -> str:
    """분석 방법 절의 EWF 문장 — 이 실행에서 실제로 판독·계산·대조한 것만 적는다."""
    name = f"EWF 이미지 `{img['file']}`"
    compared = [ALGO_KO[k] for k in img.get("comparison") or {}]
    if compared:
        return (f"{name}: 파일 구조를 직접 해석해 획득 기록값을 읽고, 압축을 풀어 미디어 전체 해시를 다시 계산해 "
                f"저장된 {'·'.join(compared)} 값과 대조했다.")
    if img.get("computed_hashes"):
        return f"{name}: 미디어 전체 해시를 다시 계산했으나 이미지에 저장된 해시가 없어 대조하지 않았다."
    if img["status"] == "not_verified":
        return f"{name}: 획득 기록값과 저장 해시만 읽었다(미디어 해시 재계산은 생략)."
    return f"{name}: 판독하지 못해 미디어 해시 재계산·대조를 수행하지 않았다(미수행 — 3장 참조)."


def reference_counts(refs: list[dict], sources: list) -> tuple[str, str]:
    """(결과, 근거). 목록의 모든 데이터 행을 채택해 모두 일치하고 경로도 확인된 경우에만 '일치'."""
    c = Counter(r["result"] for r in refs)
    path_differs = sum(1 for r in refs if r["result"] == "match" and r.get("path_differs"))
    rejected = sum(s["rejected_rows"] for s in sources if isinstance(s, dict))
    result = UNK
    if c["mismatch"]:
        result = "불일치 있음"
    elif c["match"] and (c["match"] < len(refs) or rejected):
        result = "일부 일치·나머지 미확인"
    elif c["match"]:
        result = f"해시 일치·경로 미확인 {path_differs}건" if path_differs else "일치"
    detail = []
    for s in sources:
        if isinstance(s, dict):  # 보관된 v1.6.2 이전 결과에는 목록별 행 수가 없다
            detail.append(f"{s['name']} 데이터 {s['rows']}행(채택 {s['accepted_rows']}, 거절 {s['rejected_rows']}, "
                          f"해시 없음 {s['ignored_rows']})")
    detail.append(f"참조 {len(refs)}건: 일치 {c.get('match', 0)}"
                  + (f"(그중 경로 미확인 {path_differs})" if path_differs else "")
                  + f", 불일치 {c.get('mismatch', 0)}, 증거에 없음 {c.get('not_found', 0)}, "
                  f"이름 중복으로 대조 보류 {c.get('ambiguous', 0)}, 재계산 불가 {c.get('not_verified', 0)}")
    return result, "; ".join(detail)


def integrity_rows(post, images, refs, ref_sources=(), scope=None):
    rows = []
    if scope and scope["status"] != "complete":
        rows.append(["조사 범위(폴더 열거)", "부분 완료",
                     f"확인 파일 {scope['files_found']}개, 열거 실패 폴더 {len(scope['unlisted_folders'])}개 — "
                     "전체 파일 수 미확인"])
    if post["status"] == "match" and not post["checked"]:
        rows.append(["분석 전후 재해시", UNK, "대조할 파일이 없음"])
    elif post["status"] == "match":
        rows.append(["분석 전후 재해시", "일치", f"분석 전후 SHA-256 {post['checked']}개 모두 같음."])
    else:
        detail = []
        if post["changed"]:
            detail.append(f"변경 {len(post['changed'])}개")
        if post["missing"]:
            detail.append(f"사라짐 {len(post['missing'])}개")
        if post["new"]:
            detail.append(f"새로 생김 {len(post['new'])}개")
        if post["unreadable"] or post.get("unlisted_folders"):
            part = f"대조 완료 {post['checked']}개"
            if post["unreadable"]:
                part += f", 열기 실패 {len(post['unreadable'])}개"
            if post.get("unlisted_folders"):
                part += f", 열거 실패 폴더 {len(post['unlisted_folders'])}개"
            detail.append(part)
        rows.append(["분석 전후 재해시", UNK if post["status"] == "incomplete" else "불일치", ", ".join(detail) + " — 아래 목록 확인"])
    for img in images:
        verdict = IMAGE_STATUS_KO.get(img["status"], img["status"])
        rows.append([f"EWF 저장 해시 대조 ({img['file']})", verdict, image_hash_detail(img)])
    if refs is None:
        rows.append(["참조 해시 대조", UNK, "참조 해시 목록이 제공되지 않음 (--hashes)"])
    else:
        rows.append(["참조 해시 대조", *reference_counts(refs, list(ref_sources))])
    rows.append(["쓰기 방지 장치 사용", UNK, "도구는 읽기만 했으나 쓰기 방지 장치 사용 기록은 제공되지 않음"])
    rows.append(["증거 인계 기록(Chain of Custody)", UNK, "인계 기록이 제공되지 않음 — 분석관이 기재"])
    return rows


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
    scope = ctx.get("scope")  # 보관된 v1.6.2 이전 결과에는 열거 기록이 없다
    partial = bool(scope and scope["status"] != "complete")
    a(f"- 증거 폴더: `{root_name}`")
    if partial:
        a(f"- 파일 수: 확인 {len(inv)}개 (해시 계산 {len(ok_files)}개, 읽기 실패 {len(err_files)}개), "
          f"열거 실패 폴더 {len(scope['unlisted_folders'])}개 — 전체 파일 수 미확인 (조사 범위: 부분 완료)")
    else:
        a(f"- 파일 수: {len(inv)}개 (해시 계산 {len(ok_files)}개, 읽기 실패 {len(err_files)}개)")
    size_label = "읽은 파일의 크기 합계" if err_files or partial else "총 크기"
    a(f"- {size_label}: {fmt_size(total)} ({total:,} 바이트)")
    if partial:
        a("- 열지 못한 폴더 (하위 파일 수 미확인): "
          + ", ".join(f"`{e['path']}` ({e['error']})" for e in scope["unlisted_folders"][:TABLE_LIMIT]))
    excluded = scope["excluded"] if scope else []
    if excluded:
        a(f"- 해시·선조사에서 제외한 항목 {len(excluded)}개 (따라가거나 읽지 않음, 대상 미확인): "
          + ", ".join(f"`{e['path']}` — {e['reason']}" for e in excluded[:10]))
    a("")
    cats = Counter(f["category"] for f in inv)
    sizes = Counter()
    for f in ok_files:
        sizes[f["category"]] += f["size"]
    incomplete_categories = {f["category"] for f in err_files}
    L += table(["유형", "파일 수", "크기"],
               [[c, n, UNK if c in incomplete_categories else fmt_size(sizes[c])] for c, n in cats.most_common()])
    a("")
    if images:
        a("### 나. EWF 이미지")
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
                ["미디어 크기", f"{media['media_size']:,} 바이트 (논리 스트림)" if "entries" in img else f"{fmt_size(media['media_size'])} ({media['media_size']:,} 바이트, "
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
            if "entries" in img:
                entries = img["entries"]
                a(f"논리 항목 {len(entries)}개. 전체 경로·시각·해시는 무결성검증.json에 기록했다.")
                a("")
                L += table(["ID", "경로", "바이트", "저장 해시 대조"],
                           [[e["id"], e["path"], e["size"], IMAGE_STATUS_KO.get(e["status"], UNK)]
                            for e in entries[:TABLE_LIMIT]])
                a("")
    sec = "다" if images else "나"
    a(f"### {sec}. 증거 파일 목록과 해시 (분석 전 계산)")
    a("")
    combined_hashes = ctx.get("docx", False)
    hwpx_only = ctx.get("hwpx", False) and not combined_hashes
    if combined_hashes:
        a("MD5·SHA-1·SHA-256을 싣는다. 수정 시각을 포함한 전체 목록은 `파일해시목록.csv`에 있다.")
    elif hwpx_only:
        a("기본 해시 열에는 SHA-256만 싣는다. 보조 해시 열에는 MD5·SHA-1을 표시한다. 전체 목록은 `파일해시목록.csv`에 있다.")
    else:
        a("SHA-256만 싣는다. MD5·SHA-1·수정 시각을 포함한 전체 목록은 `파일해시목록.csv`에 있다.")
    a("")
    columns = ["번호", "파일", "크기", "해시" if combined_hashes else "SHA-256"]
    if hwpx_only:
        columns.append("보조 해시")
    rows = []
    for i, f in enumerate(ok_files[:TABLE_LIMIT], 1):
        digest = f"MD5 {f['md5']} / SHA-1 {f['sha1']} / SHA-256 {f['sha256']}" if combined_hashes else f["sha256"]
        rows.append([i, report_docx.display_value(f["path"]), fmt_size(f["size"]), digest])
        if hwpx_only:
            rows[-1].append(f"MD5 {f['md5']} / SHA-1 {f['sha1']}")
    L += table(columns, rows)
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
    rows = integrity_rows(post, images, refs, ctx["reference_sources"], scope)
    L += table(["검증 항목", "결과", "근거"], rows)
    a("")
    if post["status"] != "match":
        for label, key in (("변경", "changed"), ("사라짐", "missing"), ("새로 생김", "new"), ("열기 실패", "unreadable"),
                           ("열거 실패 폴더", "unlisted_folders")):
            for p in post.get(key, [])[:TABLE_LIMIT]:
                a(f"- {label}: `{p}`")
        a("")
    if images:
        a("### 가. EWF 저장 해시와 재계산 해시")
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
                    conflicts = img.get("conflicting_stored_hashes", {}).get(algo, [])
                    rows.append([img["file"], label, " / ".join(conflicts) or stored.get(algo) or "기록 없음",
                                 comp.get(algo) or UNK, result])
            if comp.get("sha256"):
                rows.append([img["file"], "SHA-256", "기록 없음", comp["sha256"], "대조 대상 없음(참고값)"])
            if not (stored or comp or "entries" in img):
                rows.append([img["file"], "-", "기록 없음", "미수행", "미수행 (판독 실패로 재계산·대조하지 않음)"])
        L += table(["이미지", "알고리즘", "이미지에 저장된 값", "재계산 값", "결과"], rows)
        a("")
    if refs is not None:
        a(f"### {'나' if images else '가'}. 참조 해시 대조 상세")
        a("")
        sources = ctx["reference_sources"]
        if all(isinstance(s, dict) for s in sources):
            L += table(["참조 목록", "SHA-256", "바이트", "데이터 행", "채택", "거절", "해시 없음"],
                       [[s["name"], s["sha256"], s["size"], s["rows"], s["accepted_rows"], s["rejected_rows"],
                         s["ignored_rows"]] for s in sources])
            rejected = [(s["name"], r) for s in sources for r in s["rejected"]]
            if rejected:
                a("")
                a("대조에 쓰지 못한 행 (해시 미확인):")
                a("")
                for name, r in rejected[:TABLE_LIMIT]:
                    a(f"- {name} {r['line']}행: {r['reason']}")
        else:  # 보관된 v1.6.2 이전 결과: 목록 이름만 기록됨
            a("참조 목록: " + ", ".join(f"`{s}`" for s in sources))
        a("")
        rows = []
        order = {"mismatch": 0, "match": 1, "ambiguous": 2, "not_verified": 3, "not_found": 4}
        for r in sorted(refs, key=lambda r: order.get(r["result"], 9))[:TABLE_LIMIT * 2]:
            label = REF_RESULT_KO.get(r["result"], r["result"])
            target = r.get("file") or "-"
            how = {"image_media": "이미지 미디어 해시와 대조", "basename": "파일명 일치",
                   "path_suffix": "경로 끝부분 일치", "path": "경로 일치",
                   "path_casefold": "경로 일치(대소문자만 다름)"}.get(r.get("matched_by", ""),
                                                                       r.get("note") or "-")
            if r.get("normalization") and r.get("matched_by"):
                how += f" (유니코드 {r['normalization']} 정규화 후)"
            if r.get("path_differs"):
                label = "해시 일치·경로 미확인" if r["result"] == "match" else label
                how = "파일명만 일치 — 참조에 적힌 경로와 증거 경로가 다름 (같은 내용인지만 확인, 같은 파일인지는 미확인)"
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
        a("- 획득 도구·일시·장비: 미확인 (EWF 이미지가 없고 획득 기록이 제공되지 않음)")
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
    if partial:
        steps = ["열 수 있는 폴더에서 찾은 증거 파일만 읽기 전용으로 열어 MD5·SHA-1·SHA-256을 계산했다. "
                 "열지 못한 폴더는 2장에 기록했다(그 안의 파일은 조사하지 않음)."
                 + (" 열기 실패한 파일도 2장에 기록했다." if err_files else "")]
    elif err_files:
        steps = ["열 수 있는 증거 파일의 MD5·SHA-1·SHA-256을 계산했다. 열기 실패한 파일은 2장에 기록했다."]
    else:
        steps = ["증거 폴더의 모든 파일을 읽기 전용으로 열어 MD5·SHA-1·SHA-256을 계산했다."]
    if excluded:
        steps.append("링크와 일반 파일이 아닌 항목은 해시·선조사 모두에서 같은 규칙으로 제외했다(2장).")
    for img in images:
        if "entries" not in img:
            steps.append(image_method(img))
    if any("entry_verification" in img for img in images):
        steps.append("논리 증거는 항목별 바이트에서 계산한 해시를 각 항목의 저장값과 대조했다.")
    if refs is not None:
        steps.append("제공된 참조 해시 목록의 항목을 파일 경로·파일명으로 증거와 짝지어 같은 알고리즘의 해시를 대조했다."
                     + raw_media_method(refs))
    steps.append("확장자와 파일 시그니처 대조, 정확 중복, 이미지 EXIF·유사도, 압축 파일 내부, PDF 구조, "
                 "SQLite 구조, 개인정보 패턴, 영상·오디오 상태를 자동 점검했다(도구가 없는 항목은 미수행으로 표시).")
    steps.append(timeline_method(ctx["events"]))
    if post["unreadable"] or post.get("unlisted_folders"):
        steps.append("분석을 마친 뒤 대조 가능한 파일을 다시 해시했다. 열기·열거에 실패한 항목은 3장에 기록했다.")
    else:
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
    formats = ["md", "html"] + [fmt for fmt in ("docx", "hwpx") if ctx.get(fmt)]
    L += table(["파일", "내용"], [
        [" / ".join("보고서초안." + fmt for fmt in formats), "이 보고서 (HTML은 브라우저로 열어 인쇄·복사)"],
        ["파일해시목록.csv", "전체 파일의 경로·크기·수정 시각·MD5·SHA-1·SHA-256 (엑셀에서 열림)"],
        ["무결성검증.json", "분석 전 해시, 분석 후 재해시 대조, EWF 판독, 참조 해시 대조 원자료"],
        ["타임라인.html / events.json", "전체 타임라인 (브라우저로 열기)"],
        ["선조사.json", "자동 점검 원자료"],
        ["초안검증.json", "이 초안에 대한 verify_report.py 결과"],
        [RUN_LISTING, "이번 실행이 이 폴더에 만든 파일 전체와 SHA-256 (다른 실행의 파일이 섞이지 않았는지 확인)"],
    ])
    a("")
    a("인쇄본(PDF)은 이 도구가 만들지 않는다. DOCX를 PDF로 바꿨다면 `lazyforensic printcheck 인쇄본.pdf --docx 보고서초안.docx`로 "
      "한글이 실제로 그려졌는지 확인한 뒤 쓴다. PDF에서 글자가 복사된다고 인쇄된 모양까지 맞는 것은 아니다.")
    a("")
    a(f"- 작성일: {ctx['environment']['finished'][:10]}")
    a(f"- 작성 기관: {case.get('organization') or UNK}")
    a(f"- 작성자: {case.get('analyst') or UNK}")
    a("")
    return report_docx.xml_text("\n".join(L))


IMAGE_STATUS_KO = {
    "verified": "일치 (저장 해시 = 재계산 해시)",
    "mismatch": "불일치 (저장 해시 ≠ 재계산 해시)",
    "partial": "일부 일치 (저장 해시가 없는 항목 있음)",
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
    if r and "unreadable" in r:
        why = (f"후보 {r['archives']}개(확장자 기준): 판독 {r['archives'] - r['unreadable']}개, "
               f"판독 불가 {r['unreadable']}개, 주의 표시 멤버 {r['flagged_members']}개")
    rows.append(["압축 파일 내부", why or f"압축 파일 {r['archives']}개, 주의 표시 멤버 {r['flagged_members']}개",
                 "zip/tar만 (압축 해제 없이)"])
    r, why = step_result(survey, "pdf")
    rows.append(["PDF 구조", why or f"PDF {r['pdf_files']}개, 암호화·능동 콘텐츠 표시 {r['flagged']}개", "표면 검사"])
    r, why = step_result(survey, "sqlite")
    if r:
        copies = [x for x in r["records"] if x.get("read_from") == "working_copy"]
        unknown = sum(1 for x in copies if x.get("total_rows") is None)
        if "unreadable" in r:
            text = (f"후보 {r['db_files']}개: 구조 판독 {r['db_files'] - r['unreadable']}개, 판독 불가 {r['unreadable']}개"
                    f"(무결성 검사 미수행), 무결성 이상 {r['broken']}개")
        else:  # 보관된 결과에는 판독 불가 수가 없다
            text = f"DB {r['db_files']}개, 무결성 이상 {r['broken']}개"
        if copies:
            text += f", -wal/-journal 반영 {len(copies) - unknown}개" + (f", 행 수 미확인 {unknown}개" if unknown else "")
        if r.get("rows_unconfirmed"):
            text += (f" (그중 {r['rows_unconfirmed']}개는 열 수 있었으나 마지막 커밋까지 취득했는지 확인 불가 — "
                     "보이는 행 수와 사유는 한계 절)")
    rows.append(["SQLite 데이터베이스", why or text,
                 "읽기 전용 구조 조사. -wal/-journal이 있으면 원본 대신 작업 사본에서 커밋 상태를 읽음. "
                 "WAL 프레임 경계·체크섬과 부수 파일의 조사 범위를 따로 확인"])
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
        if r:
            why = (("부분 완료 — " if r["status"] != "complete" else "")
                   + f"{r['total_hits']}건 ({', '.join(r['keywords'])}); 텍스트 파일 {r['files_searched']}개 검색, "
                   f"인코딩 판정 불가 {r['files_undecodable']}개, 읽기 실패 {r['files_read_failed']}개"
                   + (f", 지정 코덱 {r['encoding_requested']}로만 읽음" if r.get("encoding_requested") else "")
                   + keyword_report.scope_note(r))
        rows.append(["키워드 검색", why, "텍스트 파일 리터럴 검색. 판정 불가·읽기 실패·텍스트 아닌 파일은 검색 수에서 제외"])
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
                L.append(f"- `{rel(root, x['file'])}` — {exif_audit.excerpt((x.get('tags') or {}).get('Software', ''))}")
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


TIMELINE_SOURCE_METHOD = {"파일시스템(mtime)": "파일 수정 시각", "EXIF": "EXIF 촬영 시각",
                          "EWF 헤더": "이미지 획득 기록", "E01 헤더": "이미지 획득 기록"}


def raw_media_method(refs: list[dict]) -> str:
    """원시 이미지 참조를 EWF 미디어 해시와 실제로 대조한 경우에만 그 절차와 대상 EWF를 적는다."""
    media = [r for r in refs if r.get("matched_by") == "image_media" or r["result"] == "not_verified"]
    if not media:
        return ""
    names = ", ".join(f"`{n}`" for n in sorted({r["file"] for r in media}))
    return f" 원시 이미지(.raw/.dd 등) 항목 {len(media)}건은 이름이 같은 EWF({names})의 미디어 해시와 대조했다."


def timeline_method(events: list[dict]) -> str:
    """타임라인에 실제로 들어간 시각 출처만 적는다."""
    sources = {e["source"] for e in events}
    used = list(dict.fromkeys([label for src, label in TIMELINE_SOURCE_METHOD.items() if src in sources]
                              + sorted(sources - TIMELINE_SOURCE_METHOD.keys())))
    if not used:
        return "시각 정보를 얻은 항목이 없어 타임라인은 만들지 않았다."
    return f"{'·'.join(used)}을 모아 타임라인을 만들었다."


def exif_limitation(events: list[dict]) -> str:
    """EXIF 시간대 한계. 지역 시간대 이름과 숫자 오프셋을 구별하고, 이 사건 사진의 오프셋 상태를 파일 수로 적는다."""
    text = ("EXIF 값은 편집할 수 있는 기록값이다. EXIF 촬영 시각에는 지역 시간대 이름(예: Asia/Seoul)이 없고, "
            "UTC 오프셋은 OffsetTimeOriginal 태그가 있을 때만 숫자(+09:00 등)로 기록된다.")
    shots = [e for e in events if e["source"] == "EXIF"]
    if shots:
        states = Counter(e.get("offset_status")
                         or ("valid" if datetime.fromisoformat(e["timestamp"]).tzinfo else "absent") for e in shots)
        parts = [f"{label} {states[key]}건" for key, label in (("valid", "유효한 오프셋"), ("empty", "빈 오프셋 태그"),
                                                              ("malformed", "형식 오류 오프셋"), ("absent", "오프셋 태그 없음"))
                 if states[key]]
        text += f" 이 사건의 촬영 시각 {len(shots)}건: {', '.join(parts)}."
        if states["valid"] < len(shots):
            text += " 유효한 오프셋이 없는 촬영 시각은 시간순 정렬에서만 KST로 가정했다."
    return text + " EXIF가 없다고 해서 변조를 뜻하지는 않는다."


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
        "EXIF": "촬영 시각 (EXIF 기록값 — UTC 오프셋이 기록된 경우 그 오프셋, 없으면 카메라 시계 기준)",
        "E01 헤더": "이미지 획득 시각 (E01 기록값, UTC)",  # archived reports before v1.3
        "EWF 헤더": "이미지 획득 시각 (EWF 기록값, UTC)",
    }
    for i, (src, evs) in enumerate(by_src.items()):
        L.append(f"**{chr(ord('1') + i)}) {labels.get(src, src)}**")
        L.append("")

        def show(e):
            if "mtime" in e:  # 운영체제가 돌려준 나노초 그대로
                return e["mtime"].removesuffix("+09:00").replace("T", " ")
            ts = e["timestamp"]
            dt = datetime.fromisoformat(ts)
            if src in ("E01 헤더", "EWF 헤더"):
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + " UTC"
            if src == "EXIF":  # 기록된 벽시계 값·소수초 원문 전체·오프셋 판정을 그대로
                return exif_audit.recorded_time(e) or ts.replace("T", " ")
            if dt.tzinfo is not None:
                return fmt_dt(dt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        if len(evs) <= TIMELINE_FULL_LIMIT:
            L += table(["시각", "파일"], [[show(e), report_docx.display_value(e["file"])] for e in evs])
        else:
            days = Counter(show(e)[:10] for e in evs)
            L.append(f"{len(evs)}건 — 가장 이른 시각 {show(evs[0])} (`{evs[0]['file']}`), "
                     f"가장 늦은 시각 {show(evs[-1])} (`{evs[-1]['file']}`).")
            L.append("")
            L += table(["날짜", "건수"], [[d, n] for d, n in sorted(days.items())][:TABLE_LIMIT])
        L.append("")
    if any("mtime" in e for e in by_src.get("파일시스템(mtime)", [])):
        L.append("파일 수정 시각은 운영체제가 돌려준 정수 나노초(mtime_ns)를 KST로 환산한 값이다. "
                 "`events.json`의 timestamp와 `타임라인.html`은 마이크로초까지만 표시하며(버림), "
                 "원값은 같은 항목의 mtime·mtime_ns에 있다. 파일시스템 자체의 시각 해상도는 확인하지 않았다.")
        L.append("")
    if "EXIF" in by_src:
        naive = sum(1 for e in by_src["EXIF"] if datetime.fromisoformat(e["timestamp"]).tzinfo is None)
        malformed = [e for e in by_src["EXIF"] if e.get("offset_status") == "malformed"]
        empty = [e for e in by_src["EXIF"] if e.get("offset_status") == "empty"]
        if naive - len(malformed) - len(empty):
            L.append(f"EXIF 촬영 시각 {naive - len(malformed) - len(empty)}건은 UTC 오프셋(OffsetTimeOriginal)이 "
                     "기록되지 않아 시간대 미기록이다. 시간순 정렬에서만 KST로 가정했다.")
        if empty:
            L.append(f"EXIF 촬영 시각 {len(empty)}건은 UTC 오프셋 태그(OffsetTimeOriginal)는 있으나 값이 비어 있다"
                     "(빈 문자열·공백). 태그 부재와 구별해 events.json의 offset_status에 empty로 남겼고, "
                     "시간순 정렬에서만 KST로 가정했다.")
        if malformed:
            raws = ", ".join(f"'{e['recorded']['OffsetTimeOriginal']}'" for e in malformed[:5])
            L.append(f"EXIF 촬영 시각 {len(malformed)}건은 UTC 오프셋 기록이 형식 오류다(원문 {raws}). "
                     "미기록과 구별해 events.json의 offset_status에 malformed로 남겼고, 시간순 정렬에서만 KST로 가정했다.")
        if naive < len(by_src["EXIF"]):
            L.append(f"EXIF 촬영 시각 {len(by_src['EXIF']) - naive}건은 기록된 UTC 오프셋으로 시간순을 정했다.")
    ties = sum(1 for e in events if e.get("order_tie"))
    if ties:
        L.append(f"기록된 정밀도로는 선후를 구별할 수 없는 시각 {ties}건은 동률이다(events.json의 order_tie). "
                 "표의 순서는 그 사이의 선후를 뜻하지 않는다.")
    while L and L[-1] == "":
        L.pop()
    return L


def summary_facts(ctx: dict) -> list[str]:
    inv, images, refs, post = ctx["inventory"], ctx["images"], ctx["references"], ctx["post"]
    ok = [f for f in inv if "sha256" in f]
    facts = [f"증거 폴더 `{ctx['root_name']}`의 파일 {len(inv)}개 중 {len(ok)}개의 해시를 계산했다."]
    scope = ctx.get("scope")
    if scope and scope["status"] != "complete":
        facts[0] = (f"증거 폴더 `{ctx['root_name']}`에서 확인한 파일 {len(inv)}개 중 {len(ok)}개의 해시를 계산했다. "
                    f"열지 못한 폴더 {len(scope['unlisted_folders'])}개가 있어 전체 파일 수는 미확인이다.")
    if post["status"] == "match" and not post["checked"]:
        facts.append("분석 전후 재해시 미확인 (대조할 파일이 없음).")
    elif post["status"] == "match":
        facts.append("대조한 파일의 분석 전·후 SHA-256 값이 모두 같았다.")
    elif post["status"] == "incomplete":
        facts.append("열기·열거 실패로 일부 파일의 분석 전후 무결성은 미확인이다.")
    else:
        facts.append("분석 전후 재해시 결과가 다르다 — 3장의 목록을 확인해야 한다.")
    for img in images:
        facts.append(f"EWF `{img['file']}`: {IMAGE_STATUS_KO.get(img['status'], img['status'])}.")
    if refs is not None:
        c = Counter(r["result"] for r in refs)
        path_differs = sum(1 for r in refs if r["result"] == "match" and r.get("path_differs"))
        rejected = sum(s["rejected_rows"] for s in ctx["reference_sources"] if isinstance(s, dict))
        facts.append(f"참조 해시 {len(refs)}건 중 일치 {c.get('match', 0)}건"
                     + (f"(그중 경로 미확인 {path_differs}건)" if path_differs else "")
                     + f", 불일치 {c.get('mismatch', 0)}건, 증거에 없음 {c.get('not_found', 0)}건."
                     + (f" 참조 목록에서 대조에 쓰지 못한 행 {rejected}개." if rejected else ""))
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
        exif_limitation(ctx["events"]),
        "삭제 파일 복구·카빙·파일시스템 해석(NTFS/FAT 등)·메모리·레지스트리·메신저 DB 분석은 하지 않았다. "
        "EWF 판독 범위는 획득 기록, 미디어·논리 파일 해시 대조다.",
        "참조 해시 대조는 파일 경로·파일명으로 짝을 짓는다. 이름이 같은 파일이 여러 개면 대조를 보류한다.",
        "개인정보 패턴·시그니처·중복·유사 이미지 결과는 검토 대상을 고르는 선별 결과이지 판정이 아니다.",
    ]
    for key, name in (("exif", "EXIF"), ("similar_images", "유사 이미지"), ("video_integrity", "영상 무결성"),
                      ("videos", "영상 지문"), ("audio_fp", "오디오 지문")):
        _, why = step_result(ctx["survey"], key)
        if why:
            out.append(f"{name} 점검: {why}")
    db, _ = step_result(ctx["survey"], "sqlite")
    for rec in (db or {}).get("records", []):
        if rec.get("limitations"):
            seen = rec.get("visible_rows")
            out.append(f"SQLite {report_docx.display_value(rel(ctx['root'], rec['file']))}: "
                       + (f"보이는 행 {seen}행, " if seen is not None else "")
                       + "최종 행 수 미확인 — " + "; ".join(rec["limitations"]))
        if rec.get("view_note"):
            out.append(f"SQLite {report_docx.display_value(rel(ctx['root'], rec['file']))}: {rec['view_note']} "
                       "(뷰는 저장 행이 아니므로 저장 행 합계와 무관)")
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

            head = report_docx.cells(rows[0])
            body = [report_docx.cells(r) for r in rows[2:]]
            # 표 셀은 측정값이다 — Word·한글 초안과 같이 문자 그대로 싣고 서식으로 해석하지 않는다.
            out.append("<table><thead><tr>" + "".join(f"<th>{html.escape(c, quote=False)}</th>" for c in head)
                       + "</tr></thead><tbody>")
            for r in body:
                out.append("<tr>" + "".join(f"<td>{html.escape(c, quote=False)}</td>" for c in r) + "</tr>")
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
            w.writerow([i, f["path"], f["category"], f["size"], f["mtime"].removesuffix("+09:00").replace("T", " "),
                        f["md5"], f["sha1"], f["sha256"], ""])
    path.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")


def run_verify(draft: Path, evidence: list[Path], markdown: Path | None = None,
               template_source: Path | None = None) -> dict:
    cmd = [sys.executable, str(SCRIPTS / "verify_report.py"), str(draft), "--evidence",
           *[str(p) for p in evidence], "--json"]
    if markdown is not None:
        cmd.extend(["--markdown", str(markdown)])
    if template_source is not None:
        cmd.extend(["--template-source", str(template_source)])
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
           "--timezone", "mtime=KST 환산(µs까지 표시, 원값 mtime_ns), "
                         "EXIF=기록된 UTC 오프셋(미기록이면 정렬만 KST 가정), EWF=UTC"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return None if proc.returncode == 0 else (proc.stderr.strip()[-300:] or "타임라인 생성 실패")


def write_drafts(out: Path, ctx: dict, integrity: Path, log=print, measured: tuple[Path, ...] = ()) -> tuple[dict, dict]:
    paths = {"md": out / "보고서초안.md", "html": out / "보고서초안.html", "verify": out / "초안검증.json"}
    md = render_report(ctx)
    paths["md"].write_text(md, encoding="utf-8")
    paths["html"].write_text(md_to_html(md, f"디지털포렌식 분석 보고서 (초안) — {ctx['root_name']}"), encoding="utf-8")

    if ctx["docx"]:
        paths["docx"] = out / "보고서초안.docx"
        report_docx.write_docx(paths["docx"], md)

    if ctx.get("hwpx"):
        paths["hwpx"] = out / "보고서초안.hwpx"
        report_hwpx.write_hwpx(paths["hwpx"], md)

    log("[7/7] 초안 검증 (verify_report.py)")
    verify = run_verify(paths["md"], [integrity])
    for fmt in ("docx", "hwpx"):
        if fmt not in paths:
            continue
        # 편집용 형식의 중괄호 판정은 측정 기록(무결성·선조사·사건 목록)의 값과 대조한다 — 같은 본문의 Markdown은 근거가 아니다.
        format_result = run_verify(paths[fmt], [integrity, *measured], markdown=paths["md"])
        verify[fmt] = format_result
        if format_result["exit_code"] or format_result["verdict"] == "ERROR":
            verify["verdict"] = "FAIL"
            verify["exit_code"] = 1
            verify["errors"].extend(fmt.upper() + ": " + msg for msg in format_result["errors"])
        if format_result["warnings"]:
            verify["warnings"].extend(fmt.upper() + ": " + msg for msg in format_result["warnings"])
            if verify["verdict"] == "PASS":
                verify["verdict"] = "WARN"
    paths["verify"].write_text(json.dumps(verify, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths, verify


def is_ewf_continuation(path: Path) -> bool:
    suffix = path.suffix.lower()
    if re.fullmatch(r"\.[el]x?[0-9]{2}", suffix):
        return True
    if re.fullmatch(r"\.[e-z][a-z]{2}", suffix):
        signatures = (ewf_image.EVF_SIGNATURE, *ewf_image.LOGICAL_V1)
    elif re.fullmatch(r"\.[el][xyz][a-z]{2}", suffix):
        signatures = (ewf_image.ewf2.EVF2, ewf_image.ewf2.LEF2)
    else:
        return False
    # Alphabetic segment suffixes overlap ordinary filename extensions.
    try:
        with path.open("rb") as f:
            return f.read(8) in signatures
    except OSError:
        return False  # Read failures are recorded by the file inventory.


def generate(root: Path, out: Path, case_info: Path | None, hash_lists: list[Path], keywords: list[str],
             skip_image_verify: bool, log=print, *, docx: bool = True, hwpx: bool = True,
             keyword_encoding: str | None = None) -> dict:
    started = now_kst()
    root = root.resolve()
    case = load_case_info(case_info)
    out.mkdir(parents=True, exist_ok=True)
    env = environment(started)

    log(f"[1/7] 파일 목록·해시 계산: {root.name}")
    walk = evidence_manifest.walk_evidence(root)
    files = walk["files"]
    inventory = build_inventory(root, files)
    scope = scope_record(walk, inventory)
    log(f"      파일 {len(inventory)}개" + ("" if scope["status"] == "complete" else
                                           f" (열거 실패 폴더 {len(walk['errors'])}개 — 전체 수 미확인)"))

    first_exts = {".e01", ".ex01", ".l01", ".lx01"}
    image_paths = [p for p in files if p.suffix.lower() in first_exts]
    images = []
    seen_segments = set()
    if image_paths:
        log(f"[2/7] EWF 이미지 {len(image_paths)}개 판독{'' if skip_image_verify else '·해시 재계산'}")
    for p in image_paths:
        res = ewf_image.examine(p, verify=not skip_image_verify)
        res["file"] = p.relative_to(root).as_posix()
        images.append(res)
        seen_segments.add(p)
        seen_segments.update(p.with_name(name) for name in res.get("segments", []))
        log(f"      {res['file']}: {IMAGE_STATUS_KO.get(res['status'], res['status'])}")
    for p in files:
        if p in seen_segments or not is_ewf_continuation(p):
            continue
        images.append({"file": p.relative_to(root).as_posix(), "image": p.name, "status": "unsupported",
                       "error": "첫 세그먼트부터 연결된 EWF 세트를 확인할 수 없음"})

    references, sources = None, []
    if hash_lists:
        entries = []
        for h in hash_lists:
            listing = hash_reference.read_reference(h)
            sources.append({k: v for k, v in listing.items() if k != "entries"})
            entries += listing["entries"]
            log(f"[3/7] 참조 해시 목록 {h.name}: {len(listing['entries'])}건 읽음 "
                f"(데이터 {listing['rows']}행 중 채택 {listing['accepted_rows']}, 거절 {listing['rejected_rows']})")
        references = compare_references(entries, inventory, images)

    log("[4/7] 선조사 (시그니처·중복·EXIF·압축·PDF·DB·개인정보 패턴 등)")
    survey = run_survey(root, inventory, scope, keywords, keyword_encoding)
    events = build_events(root, inventory, survey, images)

    log("[5/7] 분석 후 재해시")
    post = post_check(root, inventory)
    env["finished"] = fmt_dt(now_kst())

    ctx = {"docx": docx, "hwpx": hwpx, "root": root, "root_name": root.name, "inventory": inventory, "scope": scope,
           "case": case, "images": images, "references": references, "reference_sources": sources, "post": post,
           "survey": survey, "events": events, "environment": env}

    log("[6/7] 보고서 초안 작성")
    integrity = {
        "tool": f"LazyForensic {env['version']} report_draft.py",
        "evidence_root": root.name, "analysis_started": env["started"], "analysis_finished": env["finished"],
        "files": inventory, "scope": scope, "post_check": post, "images": images,
        "reference": None if references is None else {
            "sources": sources,
            # 해시 값은 알고리즘 이름 키(md5/sha1/sha256)로 둔다 — verify_report.py가 라벨로 근거 해시를 찾는다.
            "entries": [{**{k: v for k, v in r.items() if k != "hash"}, r["algorithm"]: r["hash"]}
                        for r in references]},
    }
    paths = {
        "csv": out / "파일해시목록.csv",
        "integrity": out / "무결성검증.json", "survey": out / "선조사.json", "events": out / "events.json",
        "timeline": out / "타임라인.html",
    }
    paths["integrity"].write_text(json.dumps(integrity, ensure_ascii=False, indent=2), encoding="utf-8")
    survey_out = relativize(json.loads(json.dumps(survey, ensure_ascii=False, default=str)), root)
    paths["survey"].write_text(json.dumps(survey_out, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(paths["csv"], inventory)
    timeline_err = write_timeline_html(events, paths["events"], paths["timeline"], root.name)
    draft_paths, verify = write_drafts(out, ctx, paths["integrity"], log, (paths["survey"], paths["events"]))
    paths.update(draft_paths)

    problems = []
    if scope["status"] != "complete":
        problems.append("폴더 열거 실패로 조사 범위 미확인 (부분 완료)")
    if post["status"] == "incomplete":
        problems.append("열기·열거 실패로 무결성 미확인")
    elif post["status"] != "match":
        problems.append("분석 전후 해시 불일치")
    problems += [f"EWF {i['file']}: {IMAGE_STATUS_KO[i['status']]}" for i in images
                 if i["status"] in ("mismatch", "read_error", "unsupported")]
    if references and any(r["result"] == "mismatch" for r in references):
        problems.append("참조 해시 불일치")
    if verify.get("verdict") in ("FAIL", "ERROR"):
        problems.append(f"초안 검증 {verify.get('verdict')}")
    return {"paths": paths, "post": post, "images": images, "references": references, "reference_sources": sources,
            "scope": scope, "verify": verify, "case": case, "events": events, "problems": problems,
            "timeline_error": timeline_err, "inventory": inventory, "root_name": root.name, "started": env["started"]}


def add_template(source: Path, result: dict):
    paths = result["paths"]
    draft = paths["md"].with_name(report_templates.OUTPUT_NAME).with_suffix(source.suffix.lower())
    case = {label: result["case"].get(key) for key, label, _ in CASE_FIELDS}
    fields = report_templates.form_fields(case, result["inventory"],
                                          integrity_rows(result["post"], result["images"], result["references"],
                                                         result["reference_sources"], result["scope"]),
                                          result["events"], TABLE_LIMIT)
    record = None
    try:
        record = report_templates.input_record(source, source.read_bytes())
        report_templates.write_form(source, draft, fields)
        paths["template"] = draft
        paths["template_reference"] = report_templates.digest_path(draft)
        # 검증기는 빈 양식을 따로 읽어 이름·크기·SHA-256을 싣고, 초안 대조 파일에 묶인 SHA-256과 맞춘다.
        checked = run_verify(draft, [paths["integrity"]], template_source=source)
    except report_templates.PACKAGE_ERRORS as exc:
        checked = {"verdict": "FAIL", "exit_code": 1, "errors": [f"양식을 채울 수 없음: {exc}"], "warnings": []}
    if record is not None:
        checked.setdefault("input_template", record)

    verify = result["verify"]
    verify["template"] = checked
    if checked["exit_code"] or checked["verdict"] in ("FAIL", "ERROR"):
        verify.update(verdict="FAIL", exit_code=1)
        result["problems"].append("양식 초안 검증 FAIL")
    elif checked["warnings"] and verify["verdict"] == "PASS":
        verify["verdict"] = "WARN"
    for key in ("errors", "warnings"):
        verify[key].extend(f"양식: {message}" for message in checked[key])
    paths["verify"].write_text(json.dumps(verify, ensure_ascii=False, indent=2), encoding="utf-8")


IN_PROGRESS = {"status": "in_progress",
               "note": "실행 중이거나 중단된 실행의 폴더다. 완료된 산출물 목록이 아니다.",
               "files": []}


def _staged_listing(folder: Path, record: dict) -> Path:
    """같은 폴더의 임시 파일에 완성된 목록을 쓰고 디스크까지 내린다. 목록 이름으로 게시하는 것은 호출자다."""
    staged = folder / f".{RUN_LISTING}.{os.getpid()}-{os.urandom(4).hex()}.tmp"
    with open(staged, "w", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    return staged


def reserve_output_dir(out: Path) -> Path:
    """이번 실행 전용 출력 폴더를 원자적으로 확보한다.

    산출물 목록 자리를 먼저 만든 실행만 그 폴더를 쓴다. 새 폴더는 exist_ok 없이 만들고,
    이미 있는 폴더는 비어 있을 때만 쓴다. 이전 결과가 있거나 같은 위치를 동시에 요청한 다른
    실행이 먼저 확보했으면 `<이름>_2`, `<이름>_3` … 다음 번호로 넘어간다.

    목록은 처음부터 완성된 `in_progress` 내용으로 나타난다: 임시 파일에 다 쓴 뒤 하드 링크로
    배타적으로 게시한다(이미 있으면 실패). 하드 링크를 지원하지 않는 볼륨(FAT·exFAT 등)에서만
    O_EXCL로 만든 뒤 쓴다 — 그 사이에 종료되면 빈 목록이 남고, 빈·깨진 목록도 미완료 폴더로 본다.
    중단된 폴더는 지우거나 덮어쓰지 않는다. 다시 실행하면 다음 번호 폴더에 쓴다."""
    out.parent.mkdir(parents=True, exist_ok=True)
    candidate, n = out, 1
    while True:
        try:
            candidate.mkdir()
        except FileExistsError:
            pass  # 비어 있는 기존 폴더도 아래 자리 생성에 성공해야 쓴다.
        if candidate.is_dir() and not any(candidate.iterdir()):
            listing = candidate / RUN_LISTING
            staged = _staged_listing(candidate, IN_PROGRESS)
            try:
                os.link(staged, listing)
                linked = True
            except FileExistsError:
                linked = None
            except OSError:
                linked = False
            finally:
                staged.unlink()
            if linked:
                return candidate
            if linked is False:
                try:
                    fd = os.open(listing, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        json.dump(IN_PROGRESS, stream, ensure_ascii=False, indent=2)
                    return candidate
        n += 1
        candidate = out.with_name(f"{out.name}_{n}")


def published_files(res: dict) -> list[Path]:
    return sorted({p for p in res["paths"].values() if p.is_file()})


def write_run_listing(out: Path, res: dict) -> Path:
    """이번 실행이 게시한 파일과 SHA-256 — 폴더를 다시 훑지 않고 생성기가 쓴 경로만 싣는다.

    완성된 목록을 같은 폴더의 임시 파일에 쓴 뒤 os.replace로 한 번에 바꾼다. 어느 순간에 종료돼도
    목록은 이전의 `in_progress`이거나 새 `complete`이며, 반쯤 쓴 목록은 남지 않는다."""
    listing = out / RUN_LISTING
    files = []
    for p in published_files(res):
        _, _, sha256 = hash_file(p)
        files.append({"path": p.relative_to(out).as_posix(), "size": p.stat().st_size, "sha256": sha256})
    listed = {out / f["path"] for f in files} | {listing}
    foreign = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p not in listed)
    record = {
        "status": "complete",
        "tool": f"LazyForensic {tool_version()} report_draft.py", "evidence_root": res["root_name"],
        "analysis_started": res["started"], "output_folder": out.name,
        "note": "이 폴더는 이번 실행 전용이다. 아래 목록은 이번 실행이 게시한 파일이며, 목록에 없는 파일은 이 실행의 산출물이 아니다.",
        "files": files,
    }
    if foreign:
        record["not_from_this_run"] = foreign
        res["problems"].append(f"이 실행이 만들지 않은 파일 {len(foreign)}개가 출력 폴더에 있음 (산출물목록.json not_from_this_run)")
    staged = _staged_listing(out, record)
    try:
        os.replace(staged, listing)
    except OSError:
        staged.unlink(missing_ok=True)
        raise
    return listing


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description="증거 폴더 → 한국어 포렌식 보고서 초안 + 무결성 검증 (로컬 전용)")
    ap.add_argument("root", nargs="?", help="증거 폴더 (EWF 이미지나 내보낸 파일이 든 폴더)")
    ap.add_argument("-o", "--output", help="출력 폴더 (기본: 증거 폴더 옆 '<폴더명>_분석결과')")
    ap.add_argument("--template", type=Path, help="자리표시자를 채울 보고서 양식 (.docx/.hwpx)")
    ap.add_argument("--case-info", help="사건 정보 파일 (templates/case_info_example.txt 형식 또는 JSON)")
    ap.add_argument("--hashes", nargs="+", action="extend", default=[],
                    help="참조 해시 목록 (획득 로그·해시표·sha256sum 출력 등). 여러 개 가능")
    ap.add_argument("--keywords", help="쉼표로 구분한 키워드 (텍스트 파일 검색)")
    ap.add_argument("--keyword-encoding", metavar="코덱",
                    help="키워드 검색에서 텍스트 파일을 이 코덱으로만 읽는다(예: cp949, euc_jp). "
                         "인코딩 불확실로 보고된 파일의 코덱을 확인한 뒤 지정한다")
    ap.add_argument("--hwpx", action=argparse.BooleanOptionalAction, default=True,
                    help="편집용 보고서초안.hwpx 생성 및 검증 (기본: 켜짐)")
    ap.add_argument("--docx", action=argparse.BooleanOptionalAction, default=True,
                    help="편집용 보고서초안.docx 생성 및 검증 (기본: 켜짐)")
    ap.add_argument("--no-image-verify", action="store_true", help="EWF 미디어 해시 재계산 생략 (대용량 시간 절약)")
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
    if evidence_manifest.inside(root_r, out):
        print("출력 폴더가 증거 폴더 안에 있습니다. 증거 폴더에는 아무것도 쓰지 않습니다 — -o 로 다른 곳을 지정하세요.",
              file=sys.stderr)
        return 2
    requested = out.resolve()
    if args.template:
        if args.template.suffix.lower() not in (".docx", ".hwpx") or not args.template.is_file():
            print("읽을 수 있는 DOCX 또는 HWPX 양식을 지정하세요 (--template)", file=sys.stderr)
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
    if args.keyword_encoding:
        try:
            codecs.lookup(args.keyword_encoding)
        except LookupError:
            print(f"알 수 없는 인코딩: {args.keyword_encoding}", file=sys.stderr)
            return 2
    try:
        load_case_info(case_info)  # 형식 오류는 출력 폴더를 확보하기 전에 알린다.
    except CaseInfoError as exc:
        print(exc, file=sys.stderr)
        return 2

    print(f"LazyForensic {tool_version()} — 보고서 초안 생성 (로컬 전용, 증거 폴더는 읽기만 함)")
    out_r = reserve_output_dir(requested)
    if out_r != requested:
        print(f"출력 폴더에 이전 결과가 있거나 다른 실행이 먼저 확보해 덮어쓰지 않고 새 폴더에 씁니다: {out_r}")
    try:
        res = generate(root_r, out_r, case_info, hash_lists, keywords, args.no_image_verify,
                       docx=args.docx, hwpx=args.hwpx, keyword_encoding=args.keyword_encoding)
    except CaseInfoError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.template:
        add_template(args.template, res)
        if "template" in res["paths"]:
            print(f"양식 초안: {res['paths']['template']}")
    listing = write_run_listing(out_r, res)
    print("")
    print(f"완료: {res['paths']['html']}")
    print(f"      (브라우저로 열어 검토. 같은 폴더에 .md·파일해시목록.csv·무결성검증.json·타임라인.html)")
    print(f"산출물 목록: {listing}")
    for fmt in ("docx", "hwpx"):
        if fmt in res["paths"]:
            print(f"편집용 초안: {res['paths'][fmt]}")
    post = res["post"]
    status = {"match": "일치", "incomplete": UNK, "mismatch": "불일치"}[post["status"]]
    print(f"분석 전후 재해시: {status} ({post['checked']}개)")
    for img in res["images"]:
        print(f"EWF {img['file']}: {IMAGE_STATUS_KO.get(img['status'], img['status'])}")
    if res["references"] is not None:
        c = Counter(r["result"] for r in res["references"])
        print(f"참조 해시: 일치 {c.get('match', 0)} / 불일치 {c.get('mismatch', 0)} / 증거에 없음 {c.get('not_found', 0)}")
    v = res["verify"]
    print(f"초안 검증: {v.get('verdict')}" + (f" — {v['errors'][0]}" if v.get("errors") else ""))
    if res["timeline_error"]:
        print(f"타임라인: {res['timeline_error']}")
    if res["scope"]["status"] != "complete":
        print(evidence_manifest.partial_scope_message(res["scope"]["unlisted_folders"]), file=sys.stderr)
    if res["problems"]:
        print("확인 필요: " + "; ".join(res["problems"]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
