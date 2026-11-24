#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report_core.py — verify_report 검증 함수 모음.

verify_report.py 에서 분리(C2). 스캔/추출/로딩/바인딩/헬스체크 함수만 모은다.
CLI 오케스트레이션은 verify_report.py 가 유지한다.
"""
from __future__ import annotations

import difflib
import json
import re
from datetime import datetime
from pathlib import Path

try:
    from scripts.report_patterns import (
        CERTAINTY_ASSERTION_RES,
        DOT_DATE_RE,
        EVIDENCE_TAG_RE,
        FORBIDDEN_PHRASES,
        HASH_VALUE_RE,
        ISO_DATE_RE,
        ISO_DT_RE,
        KOR_DT_RE,
        KST,
        NEGATION_RE,
    )
except ImportError:  # scripts/ 가 sys.path 에 직접 있는 경우
    from report_patterns import (
        CERTAINTY_ASSERTION_RES,
        DOT_DATE_RE,
        EVIDENCE_TAG_RE,
        FORBIDDEN_PHRASES,
        HASH_VALUE_RE,
        ISO_DATE_RE,
        ISO_DT_RE,
        KOR_DT_RE,
        KST,
        NEGATION_RE,
    )


def scan_certainty_variants(text: str) -> list[str]:
    hits = []
    for pat, label in CERTAINTY_ASSERTION_RES:
        for m in pat.finditer(text):
            after = text[m.end(): m.end() + 20]
            if not NEGATION_RE.match(after):
                hits.append(f"{label}: '{m.group(0)}'")
                break
    return hits


def scan_forbidden(text: str) -> list[str]:
    hits = []
    for phrase in FORBIDDEN_PHRASES:
        start = 0
        while True:
            idx = text.find(phrase, start)
            if idx == -1:
                break
            after = text[idx + len(phrase): idx + len(phrase) + 20]
            if not NEGATION_RE.match(after):
                hits.append(phrase)
                break
            start = idx + len(phrase)
    return hits


def extract_hashes(text: str) -> list[str]:
    """SHA-256 (64hex 전체) + 문맥 라벨이 붙은 MD5(32hex)/SHA-1(40hex).

    32/40 hex 는 무라벨 오탐이 많아 MD5/SHA-1 라벨이 같은 줄에 있을 때만 수집한다.

    줄바꿈·공백으로 분할된 64hex 도 잡는다(표 정렬·하드랩). 개행 결합은 두
    32hex 해시를 하나로 합쳐 환영 해시를 만들 수 있지만, 그 결과는 '근거 없는
    해시' 판정(실패폐쇄 방향)으로 귀결되므로 안전한 쪽의 오류다.
    """
    found = re.findall(r"\b[a-fA-F0-9]{64}\b(?!\.\w)", text)
    have = {h.lower() for h in found}
    for line in text.splitlines():
        if re.search(r"\bMD5\b|\bSHA-?1\b|md5_legacy", line, re.IGNORECASE):
            for pat in (r"\b[a-fA-F0-9]{40}\b(?!\.\w)", r"\b[a-fA-F0-9]{32}\b(?!\.\w)"):
                for h in re.findall(pat, line):
                    if h.lower() not in have:
                        found.append(h)
                        have.add(h.lower())
    # 분할 해시: 개행/공백이 hex 문자 사이에 낀 경우만 결합해 다시 찾는다.
    compact = re.sub(r"(?<=[a-fA-F0-9])[ \t]*\r?\n[ \t]*(?=[a-fA-F0-9])", "", text)
    if compact != text:
        for h in re.findall(r"\b[a-fA-F0-9]{64}\b(?!\.\w)", compact):
            if h.lower() not in have and h not in text:
                found.append(h)
                have.add(h.lower())
    return found


def _h12_to_24(ampm: str | None, hour: int | None) -> int | None:
    if hour is None:
        return None
    if ampm is None:
        return hour
    if ampm == "오후" and hour != 12:
        return hour + 12
    if ampm == "오전" and hour == 12:
        return 0
    return hour


def extract_timestamps(text: str) -> list[str]:
    """정규형 집합: 'YYYY-MM-DD HH:MM(:SS)' 와 'YYYY-MM-DD'."""
    out: set[str] = set()
    zoned_starts = set()
    for m in ISO_DT_RE.finditer(text):
        y, mo, d, h, mi, sec, fraction, zone = m.groups()
        stamp = f"{y}-{int(mo):02d}-{int(d):02d} {int(h):02d}:{mi}:{sec or '00'}"
        if zone:
            zoned_starts.add(m.start())
            offset = {"Z": "+00:00", " UTC": "+00:00", " KST": "+09:00"}.get(zone, zone)
            if ":" not in offset:
                offset = offset[:3] + ":" + offset[3:]
            try:
                dt = datetime.fromisoformat(stamp + offset).astimezone(KST)
            except (ValueError, OverflowError):
                # Preserve invalid input for the verifier's calendar/clock check.
                out.add(stamp + offset)
                continue
            stamp = dt.replace(tzinfo=None).isoformat(sep=" ")
        out.add(stamp + (fraction or "").rstrip("0").rstrip("."))
    for m in KOR_DT_RE.finditer(text):
        date = f"{int(m.group('y')):04d}-{int(m.group('mo')):02d}-{int(m.group('d')):02d}"
        if m.group("hkor"):
            hour = int(m.group("hkor"))
            minute = int(m.group("mikor") or 0)
        elif m.group("hiso"):
            hour = int(m.group("hiso"))
            minute = int(m.group("miiso"))
        else:
            out.add(date)
            continue
        hour = _h12_to_24(m.group("ampm"), hour)
        out.add(f"{date} {hour:02d}:{minute:02d}:{m.group('seiso') or '00'}")
    for m in DOT_DATE_RE.finditer(text):
        y, mo, d = m.groups()
        out.add(f"{int(y):04d}-{int(mo):02d}-{int(d):02d}")
    for m in ISO_DATE_RE.finditer(text):
        if m.start() in zoned_starts:
            continue
        y, mo, d = m.groups()
        out.add(f"{int(y):04d}-{int(mo):02d}-{int(d):02d}")
    return sorted(out)


def read_text_smart(path: Path, max_bytes: int = 8 * 1024 * 1024) -> tuple[str, str, bool]:
    """BOM/UTF-16/CP949를 순서대로 시도한다. 모두 실패하면 치환 문자로 읽고 lossy=True.

    구버전의 errors="ignore" 는 UTF-16 (Windows PowerShell Out-File 기본값) 입력을
    그대로 mojibake 로 만들어 금지 문구·해시 검출을 모두 놓쳤다.
    대용량 파일은 앞 max_bytes까지만 읽고 lossy=True로 표시한다 (Lazy Read:
    전체 로딩 OOM 방지, 잘림은 호출자가 경고한다).
    """
    with path.open("rb") as f:
        raw = f.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    encodings = ("utf-16",) if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "cp949")
    for enc in encodings:
        try:
            return raw.decode(enc), enc + ("(truncated)" if truncated else ""), truncated
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(lossy)" + ("(truncated)" if truncated else ""), True


def load_evidence_hashes(evidence_paths: list[str]) -> set[str]:
    hashes: set[str] = set()

    def collect(value, field=""):
        if isinstance(value, dict):
            for key, item in value.items():
                collect(item, key.lower().replace("-", ""))
        elif isinstance(value, list):
            for item in value:
                collect(item, field)
        elif isinstance(value, str) and HASH_VALUE_RE.fullmatch(value):
            algorithm = {32: "md5", 40: "sha1", 64: "sha256"}[len(value)]
            if algorithm in field.split("_") or field in ("hash", "digest", "prev_hash", "entry_hash"):
                hashes.add(value.lower())

    for p in evidence_paths:
        try:
            text = Path(p).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, (dict, list)):
            collect(data)
            continue
        if not text.lstrip().startswith(("{", "[")):
            hashes.update(h.lower() for h in extract_hashes(text))
            continue
        for line in text.splitlines():
            if line.lstrip().startswith(("{", "[")):
                try:
                    collect(json.loads(line))
                except ValueError:
                    pass
            else:
                hashes.update(h.lower() for h in extract_hashes(line))
    return hashes


def _normalize_file_token(tok: str) -> str:
    """파일 토큰 정규화: 경로는 basename으로, 비교는 소문자(Windows/macOS
    파일시스템은 대소문자 무관)로 통일한다."""
    return tok.strip().replace("\\", "/").rstrip("/").split("/")[-1].casefold()


def load_evidence_hash_file_pairs(evidence_paths: list[str]) -> dict[str, list[str]]:
    """evidence JSON에서 {해시: [파일명...]} 매핑을 추출한다.

    지원 스키마: dict의 (path|file|filename|name)↔(sha256|sha1|md5|hash) 필드 쌍,
    부모 dict의 파일명 + 자식 dict의 해시(중첩 스키마), 그리고 "파일명 ... 64hex"
    형태의 텍스트 표.
    """
    pairs: dict[str, list[str]] = {}

    def _add(h: str, fname: str):
        h = h.strip().lower()
        fname = fname.strip().replace("\\", "/")
        if not HASH_VALUE_RE.match(h) or not fname:
            return
        pairs.setdefault(h, [])
        if fname not in pairs[h]:
            pairs[h].append(fname)

    # 파일명 후보 키 우선순위 — dict 순서가 아닌 명시적 우선순위로 고정.
    # 'name'은 확장자가 있는(=실제 파일명인) 값만 인정해 도구명/기관명 오염 방지.
    def _fname_candidates(obj: dict) -> list[str]:
        found: list[str] = []
        for key in ("filename", "file_name", "file", "path", "target"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                found.append(v)
                break
        name_v = obj.get("name")
        if isinstance(name_v, str) and re.search(r"\.[A-Za-z0-9]{1,8}$", name_v.strip()):
            found.append(name_v)
        return found

    def _walk(obj, parent_fname: str | None = None):
        if isinstance(obj, list):
            for item in obj:
                _walk(item, parent_fname)
            return
        if not isinstance(obj, dict):
            return
        # 이 객체 안의 해시들: 같은 객체의 파일명 후보, 없으면 부모의 파일명
        fnames = _fname_candidates(obj)
        inherited = fnames or ([parent_fname] if parent_fname else [])
        for key, val in obj.items():
            if not isinstance(val, str):
                continue
            val_clean = val.strip()
            if HASH_VALUE_RE.match(val_clean) and re.search(
                r"sha|md5|hash|digest", key, re.IGNORECASE
            ):
                for fname in inherited:
                    _add(val_clean, fname)
        # 자식 순회: 이 객체의 파일명을 물려준다 (file/meta 중첩 스키마 결합)
        child_fname = fnames[0] if fnames else parent_fname
        for val in obj.values():
            if isinstance(val, (dict, list)):
                _walk(val, child_fname)

    for p in evidence_paths:
        path = Path(p)
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        try:
            data = json.loads(text)
            _walk(data)
        except Exception:
            for line in text.splitlines():
                parsed = False
                try:
                    _walk(json.loads(line))
                    parsed = True
                except Exception:
                    pass
                if parsed:
                    continue  # JSON 라인은 표 regex 이중 처리 생략
                # 텍스트 표 형태: "파일명 <경로/확장자> ... 64hex"
                for m in re.finditer(r"([^\s|]+\.[A-Za-z0-9]{1,8})\s*\|?\s*([a-fA-F0-9]{64})\b", line):
                    _add(m.group(2), m.group(1))
                for m in re.finditer(r"\b([a-fA-F0-9]{64})\b\s*\|?\s*([^\s|]+\.[A-Za-z0-9]{1,8})", line):
                    _add(m.group(1), m.group(2))
    return pairs


def check_hash_file_binding(report_text: str, evidence_pairs: dict[str, list[str]], *, display_name=None) -> list[str]:
    """해시-파일명 인접성 검증 (Proximity Binding).

    보고서에서 해시가 나온 줄의 ±2줄 윈도우에 적힌 파일명이, evidence의 해당
    해시 소유 파일명과 일치하는지 대조한다. 정상 해시를 악의적 서술(다른
    파일명)에 재사용하면 FAIL. 파일명 후보는 basename+casefold로 정규화하고,
    소수점 버전(2.3)·IP·URL 등 확장자에 영문자가 없는 유사 토큰은 제외한다.

    스키마 강제 2종 추가:
    - 표 행 엄격: 해시가 마크다운 표 행(`|`) 안에 있으면 같은 행에 소유
      파일명이 있어야 한다. 같은 행에 다른 파일명만 있으면 즉시 위반.
    - 미결합 경고: ±2줄 윈도우에 파일명이 전혀 없으면 스키마 위반(WARN)으로
      기록한다 (3줄 이상 떨어진 변칙 서술 방지).
    """
    violations: list[str] = []
    seen: set[tuple[str, int]] = set()
    if not evidence_pairs:
        return violations
    lines = report_text.splitlines()
    # 태그 귀속 해시 집합: 본문에 <evidence>H</evidence>로 한 번이라도
    # 인용된 해시는 파일명 동행 없이도 그라운딩된 것으로 본다 (재언급 오탐 방지).
    tagged_hashes: set[str] = set()
    for tm in EVIDENCE_TAG_RE.finditer(report_text):
        for hm in re.finditer(r"\b([a-fA-F0-9]{64})\b", tm.group(1)):
            tagged_hashes.add(hm.group(1).lower())
    # 확장자에 최소 1개 영문자를 요구해 2.3 / 192.168.1.10 / example.com 배제.
    # 확장자 뒤 한국어 조사(의/은/는/이/가/을/를/도/에/에서/와/과/로)를 허용해
    # "secret.zip의 해시" 형태의 파일명 토큰도 잡는다.
    file_token_re = re.compile(
        r"([^\s|]+\.[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]{0,7})(?:의|은|는|이|가|을|를|도|에|에서|와|과|로|으로|랑|이랑)?(?=[\s,.:;)\]|가-힣]|$)"
    )
    for li, line in enumerate(lines):
        row_hashes = extract_hashes(line) if "|" in line else re.findall(r"\b[a-fA-F0-9]{64}\b", line)
        for value in row_hashes:
            h = value.lower()
            owners = evidence_pairs.get(h)
            if not owners:
                continue  # 미등록 해시는 기존 '근거 없는 해시' 검사가 처리
            # 해시와 파일명이 인접(같은 줄 또는 ±2줄)한 파일명 수집
            window_files: set[str] = set()
            for wli in range(max(0, li - 2), min(len(lines), li + 3)):
                for fm in file_token_re.finditer(lines[wli]):
                    window_files.add(_normalize_file_token(fm.group(1)))
            # 생성기가 쓰는 불일치 상세 행은 알고리즘 앞까지가 파일 경로다.
            detail = re.match(r"^- (.+) (?:MD5|SHA-1|SHA-256) 실제 [a-fA-F0-9]+ / 참조 [a-fA-F0-9]+$", line)
            if detail:
                window_files.add(_normalize_file_token(detail[1]))
            owners_norm = [_normalize_file_token(o) for o in owners]
            # 표에서 공백이 있는 파일명은 토큰으로 나누지 않고 셀 전체로 확인한다.
            if "|" in line:
                raw_cells = [c.strip().replace(r"\|", "|") for c in re.split(r"(?<!\\)\|", line)]
                # A quoted report label must equal the encoded full evidence path.
                if display_name and any(display_name(owner) in raw_cells for owner in owners):
                    continue
                cells = [c.replace("\\", "/") for c in raw_cells]
                matching = [c for c in cells if _normalize_file_token(c) in owners_norm]
                if matching:
                    if any("/" not in c or "/" not in owner or c == owner
                           or c.endswith("/" + owner) or owner.endswith("/" + c)
                           for c in matching for owner in owners
                           if _normalize_file_token(c) == _normalize_file_token(owner)):
                        continue
                    violations.append(f"해시-파일 결합 불일치(표 행): {matching} / 근거 경로 {owners}")
                    continue
            if not window_files:
                if h in tagged_hashes:
                    continue  # 태그 귀속 해시는 파일명 동행 불필요
                key = (h, li)
                if key not in seen:
                    seen.add(key)
                    violations.append(
                        f"[SCHEMA-WARN] 해시-파일 결합 스키마 위반: 해시 {h[:16]}... 주변 ±2줄에 파일명이 없어 "
                        f"어느 파일의 해시인지 검증할 수 없음 — 해시와 파일명을 같은 표 행 또는 인접 줄에 기재하십시오"
                    )
                continue
            # 표 행 엄격: '|' 포함 행에서는 같은 행의 파일명을 우선 대조한다.
            if "|" in line:
                same_row = {_normalize_file_token(fm.group(1)) for fm in file_token_re.finditer(line)}
                if same_row and not any(o in same_row for o in owners_norm):
                    key = (h, li)
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            f"해시-파일 결합 불일치(표 행): 해시 {h[:16]}... 와 같은 행에 "
                            f"{sorted(same_row)[:2]} 가 있으나 evidence 소유 파일은 "
                            f"{owners[:2]} 임 — 표 행의 파일명-해시 쌍을 확인하십시오"
                        )
                    continue
            if not any(o in window_files for o in owners_norm):
                key = (h, li)
                if key in seen:
                    continue
                seen.add(key)
                violations.append(
                    f"해시-파일 결합 불일치: 해시 {h[:16]}... 가 인접 줄에서 "
                    f"{sorted(window_files)[:2]} 와 함께 서술되었으나 evidence에서 이 해시의 소유 파일은 "
                    f"{owners[:2]} 임 — 정상 해시를 다른 파일 서술에 재사용했는지 확인하십시오"
                )
    return violations


def _evidence_documents(evidence_paths) -> list:
    """--evidence 파일의 내용: JSON 문서(파일 전체 또는 JSONL 한 줄)는 객체로, 그 밖의 비어 있지 않은 줄은 문자열로."""
    docs: list = []
    for p in evidence_paths:
        try:
            text = Path(p).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        try:
            docs.append(json.loads(text))
            continue
        except ValueError:
            pass
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(("{", "[")):
                try:
                    docs.append(json.loads(line))
                    continue
                except ValueError:
                    pass
            if line:
                docs.append(line)
    return docs


PLACEHOLDER_RE = re.compile(r"\{\{[^{}\r\n\t]*\}\}")


def unmeasured_placeholders(text: str, evidence_paths, *, display_name=None) -> list[str]:
    """본문에 남은 중괄호 토큰 중 근거 파일의 측정값(JSON 문자열 값) 안에 있지 않은 것.

    `{{year}}.txt` 같은 실제 파일명은 근거 파일에 그 값이 있으므로 면제된다. 같은 본문의 Markdown이
    있다는 사실은 면제 근거가 아니다 — 그 Markdown도 같은 초안이다."""
    values: set[str] = set()

    def collect(node):
        if isinstance(node, dict):
            for item in node.values():
                collect(item)
        elif isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, str) and ("{{" in node or "}}" in node):
            values.add(node)

    for doc in _evidence_documents(evidence_paths):
        if not isinstance(doc, str):
            collect(doc)
    forms = set()
    for value in values:
        for form in (value, value.replace("\\", "/").rsplit("/", 1)[-1]):
            forms.update((form, form.replace("|", r"\|")))
            if display_name is not None:
                forms.add(display_name(form))
    covered = [False] * len(text)
    for form in forms:
        start = text.find(form)
        while form and start != -1:
            covered[start:start + len(form)] = [True] * len(form)
            start = text.find(form, start + 1)
    left = "".join(" " if hit else ch for ch, hit in zip(text, covered))
    if "{{" not in left and "}}" not in left:
        return []
    return sorted(set(PLACEHOLDER_RE.findall(left))) or [t for t in ("{{", "}}") if t in left]


CLAIM_ID_RE = re.compile(r"[A-Za-z0-9]+(?:[._:/-][A-Za-z0-9]+)*")
CLAIM_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+")
# 표시용 크기(743 B, 21.8 KB, 1,024 바이트)는 단위 글자가 식별자처럼 보이므로 주장 용어에서 뺀다.
SIZE_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(?:[KMGT]i?B|B|bytes?|바이트)(?![A-Za-z0-9])", re.IGNORECASE)


def _evidence_records(doc) -> list:
    """근거 문서를 측정 레코드로 나눈다.

    JSON 객체는 ("slots", [(키 경로, 값)…]) 레코드 하나다. 스칼라 목록은 같은 키 경로의 값들이고, 바깥 객체의
    값을 같은 키 경로로 물려받는다. 문장(공백이 든 문자열)은 ("words", 문장) 레코드다 — 문장 레코드끼리는
    단어를 정렬해 대응 위치를 맞춘다. JSON이 아닌 근거 줄은 호출자가 문장 레코드로 만든다."""
    out: list = []

    def scalar(value):
        return isinstance(value, (str, int, float)) and not isinstance(value, bool)

    def sentence(value):
        return isinstance(value, str) and re.search(r"\s", value.strip()) is not None

    def walk(node, path, context):
        if isinstance(node, dict):
            own, nested = [], []
            for key, value in node.items():
                slot = f"{path}.{key}" if path else str(key)
                if scalar(value):
                    own.append((slot, str(value)))
                elif isinstance(value, list) and value and all(scalar(v) and not sentence(v) for v in value):
                    own += [(slot, str(v)) for v in value]
                elif isinstance(value, (dict, list)):
                    nested.append((slot, value))
            if own:
                out.append(("slots", context + own))
            for slot, value in nested:
                walk(value, slot, context + own)
        elif isinstance(node, list):
            loose = [(path, str(v)) for v in node if scalar(v) and not sentence(v)]
            if loose:
                out.append(("slots", context + loose))
            for value in node:
                if sentence(value):
                    out.append(("words", value))
                elif isinstance(value, (dict, list)):
                    walk(value, path, context)
        elif sentence(node):
            out.append(("words", node))
        elif scalar(node):
            out.append(("slots", context + [(path, str(node))]))

    walk(doc, "", [])
    return out


def check_claim_binding(report_text: str, evidence_paths, extract_terms, procedural=()) -> dict:
    """한 측정 레코드의 대상에 다른 레코드의 값을 붙인 주장을 찾는다.

    인용 태그(<evidence>)가 원문 그대로여도 결론이 두 레코드의 값을 엇갈려 쓰면 그 주장은 'unbound'다.
    예: '청색 표시는 상자 A에 있다'·'백색 표시는 상자 B에 있다'에서 '청색 표시는 상자 B에 있다'는 첫 레코드의
    대상(청색)과 둘째 레코드의 값(B)을 같은 문장에 썼고, 첫 레코드에는 그 자리에 다른 값(A)이 있다.
    대응 위치는 JSON 객체에서는 같은 키 경로, 문장 레코드끼리는 단어 정렬로 정한다. 여러 대상을 나열한 문장
    (같은 위치의 값들)은 엇갈림이 아니다. 근거에 있는 식별 용어가 둘 이상인 문장만 판정하고, 과반 레코드에
    나오는 용어·한두 자리 정수(순번)·표시용 크기(743 B)는 쓰지 않는다. 헤딩·굵은 소제목 줄과 '미확인'·'미측정'
    문장은 판정하지 않는다. 이 검사는 용어의 위치만 본다 — 문장의 의미나 결론의 타당성을 판정하지 않는다."""
    proc = {p.casefold() for p in procedural}
    cache: dict[str, frozenset] = {}  # 같은 값(분류명·도구명 등)이 레코드마다 반복된다

    def terms(text):
        if text not in cache:
            found = {t.casefold() for t in extract_terms(text)}
            found |= {m.group(0).casefold() for m in CLAIM_ID_RE.finditer(text)}
            cache[text] = frozenset(t for t in found - proc if not (t.isdigit() and len(t) <= 2))
        return cache[text]

    records = []  # (슬롯 → 값 용어) 또는 단어별 용어 목록, 그리고 전체 용어
    for doc in _evidence_documents(evidence_paths):
        for kind, value in ([("words", doc)] if isinstance(doc, str) else _evidence_records(doc)):
            if kind == "words":
                words = [frozenset(terms(w)) for w in value.split()]
                records.append((None, words, set().union(*words)))
            else:
                slots: dict[str, set] = {}
                for slot, text in value:
                    slots.setdefault(slot, set()).update(terms(text))
                records.append((slots, None, set().union(*slots.values())))
    postings: dict[str, set[int]] = {}
    for index, record in enumerate(records):
        for term in record[2]:
            postings.setdefault(term, set()).add(index)
    # 레코드의 과반에 나오는 용어는 어느 대상인지 가르지 못한다.
    limit = len(records) / 2
    distinct = {t for t, ids in postings.items() if len(records) < 2 or len(ids) <= limit}

    def aligned(a, b):
        """두 레코드의 대응 위치마다 (a의 값 용어, b의 값 용어)."""
        if a[0] is not None and b[0] is not None:
            return [(a[0][k], b[0][k]) for k in a[0].keys() & b[0].keys()]
        if a[1] is not None and b[1] is not None:
            pairs = []
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a[1], b=b[1], autojunk=False).get_opcodes():
                if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
                    pairs += zip(a[1][i1:i2], b[1][j1:j2])
            return pairs
        return []

    def crossing(used):
        """(a 레코드의 대상 용어 t1, b 레코드의 값 용어 t2) 또는 None.

        엇갈림: 두 용어가 한 레코드에 함께 나온 적이 없고, 주장이 t1 자리는 a에서, t2 자리는 b에서 가져왔으며
        a의 t2 자리 값과 b의 t1 자리 값은 둘 다 따로 있는데 주장에 없다."""
        if any(used <= record[2] for record in records):
            return None
        candidates = sorted({i for t in used for i in postings[t]})[:64]
        for i in candidates:
            a = records[i]
            for j in candidates:
                b = records[j]
                if i == j or not (used - b[2]) or not (used - a[2]):
                    continue
                pairs = aligned(a, b)
                for p1, (va1, vb1) in enumerate(pairs):
                    if not vb1 or vb1 & used:
                        continue
                    for t1 in sorted(used & va1 - b[2]):
                        for p2, (va2, vb2) in enumerate(pairs):
                            if p2 == p1 or not va2 or va2 & used:
                                continue
                            for t2 in sorted(used & vb2 - a[2]):
                                if not postings[t1] & postings[t2]:
                                    return t1, t2
        return None

    body = EVIDENCE_TAG_RE.sub(" ", report_text)
    claims = []
    for line in body.splitlines():
        line = re.sub(r"<[^>]+>", " ", line).strip()
        if (not line or line.startswith("#") or re.fullmatch(r"\*\*[^*]+\*\*:?", line)
                or "미확인" in line or "미측정" in line):
            continue
        for sentence in CLAIM_SPLIT_RE.split(line):
            used = terms(SIZE_RE.sub(" ", sentence)) & distinct
            if len(used) < 2:
                continue
            crossed = crossing(used)
            claim = {"claim": sentence.strip()[:160], "terms": sorted(used), "status": "unbound" if crossed else "bound"}
            if crossed:
                claim["crossed"] = list(crossed)
            claims.append(claim)
    return {"records": len(records), "claims": claims}


def run_forensic_health_check() -> dict:
    """Run comprehensive Section 5.1 & Section 6 forensic factuality health check suite.
    Scores 12 categories (10 points each = 120 points total; pass mark 100%).
    Returns health check report with score and test results.
    """
    import io
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    try:
        from scripts.verify_report import main as _main
    except ImportError:
        from verify_report import main as _main

    tests = [
        # 1. Forbidden phrases: Must block forbidden certainty words
        (
            "forbidden_phrases_blocked",
            "이 증거는 법원에 유효하며 피의자의 유출이 명백히 입증되었습니다.",
            lambda out, code: code != 0 and any("금지 문구" in e for e in out.get("errors", [])),
        ),
        # 2. Hash grounding: Must block ungrounded 64-char hex hash
        (
            "orphan_hash_blocked",
            "분석 결과 악성코드의 해시는 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 입니다.",
            lambda out, code: code != 0 and any("근거 없는 해시" in e for e in out.get("errors", [])),
        ),
        # 3. Statutory bounds: Must block out-of-bounds statute article
        (
            "statutory_bounds_blocked",
            "정보통신망법 제100조에 따라 처벌을 의뢰합니다.",
            lambda out, code: code != 0 and any("허위 조문 날조" in e for e in out.get("errors", [])),
        ),
        # 4. Precedents sanity: Must block future year precedent
        (
            "precedents_future_blocked",
            "대법원 2099도12345 판결 취지를 원용합니다.",
            lambda out, code: code != 0 and any("미래 연도 판결" in e for e in out.get("errors", [])),
        ),
        # 5. Agency & Academic citations: Must block fabricated agencies & fake academic journals/future citations (Section 5.1 #2 & #3)
        (
            "agency_and_academic_blocked",
            "사이버수사처 및 대한인공지능법학회지, 홍길동 교수의 2099년 학술지 논문 인용.",
            lambda out, code: code != 0
            and any("공공기관 명칭 날조" in e for e in out.get("errors", []))
            and any("학술논문/학술지 날조" in e for e in out.get("errors", [])),
        ),
        # 6. Historical events: Valid historical events pass
        (
            "historical_events_valid",
            "제1차 갑오개혁 및 동학농민운동 1차 봉기 당시의 기록 분석.",
            lambda out, code: code == 0 and len(out.get("errors", [])) == 0,
        ),
        # 7. Historical events: Fabricated historical rounds, Hanja numerals & single treaties blocked (Section 5.1 #3)
        (
            "historical_events_invalid",
            "제四차 갑오개혁 및 第4次 갑오개혁, 제2차 을사조약, 제2차 을미개혁, 3차 동학농민운동 참조.",
            lambda out, code: code != 0 and any("한국사 사건/조약 날조" in e for e in out.get("errors", [])),
        ),
        # 8. Judicial procedures: Valid procedures pass (including supervisory guidance)
        (
            "judicial_procedures_valid",
            "서울중앙지방검찰청 검사의 약식명령 청구 및 경찰의 구속영장 신청, 대검찰청의 지휘 사실을 기재합니다.",
            lambda out, code: code == 0 and len(out.get("errors", [])) == 0,
        ),
        # 9. Judicial procedures: Impossible procedures with long clauses blocked (Section 5.1 #4)
        (
            "judicial_procedures_invalid",
            "대검찰청 특별수사본부는 이번 사건과 관련하여 피의자들에 대해 약식명령을 청구하였고 경찰은 관할 법원에 직접 영장을 청구하였다.",
            lambda out, code: code != 0 and any("불가능한 사법절차 날조" in e for e in out.get("errors", [])),
        ),
        # 10. Evidence-First & Abstention protocol
        (
            "evidence_first_protocol",
            "<evidence>2026-08-30T10:00:00Z</evidence> [INSUFFICIENT_DATA] 추가 분석 필요.",
            lambda out, code: code == 0 and len(out.get("errors", [])) == 0,
        ),
        # 11. Benign regression: legitimate artifacts must PASS (review-found
        #     false positives — 제44조의2, "재판에 필요", 버전 2.3, 시행예정일)
        (
            "benign_real_articles_pass",
            "정보통신망법 제44조의2 위반 혐의로 기재한다. 이 자료는 재판에 필요하다. "
            "분석 도구 버전 2.3으로 산출하였다. 개정법은 2027-01-01 시행 예정이다. "
            "민법 제2조의2에 따른 신의성실 원칙도 언급한다.",
            lambda out, code: code == 0 and len(out.get("errors", [])) == 0,
        ),
        # 12. Benign regression: checksum-style notes with negated/softened claims
        (
            "benign_negation_pass",
            "해당 행위의 완전한 입증은 어렵다. 유출이 확실시되지 않아 미확인으로 둔다. "
            "100% 확인 불가한 부분은 미측정으로 기록한다. 조작 가능성을 배제할 수 없다.",
            lambda out, code: code == 0 and len(out.get("errors", [])) == 0,
        ),
    ]

    passed_tests = 0
    total_tests = len(tests)
    test_results = {}

    with tempfile.TemporaryDirectory() as td:
        for name, text, checker in tests:
            f = Path(td) / f"{name}.md"
            f.write_text(text, encoding="utf-8")
            s_out = io.StringIO()
            s_err = io.StringIO()
            with redirect_stdout(s_out), redirect_stderr(s_err):
                argv = [str(f), "--json"]
                if name == "evidence_first_protocol":
                    ev_f = Path(td) / f"{name}_ev.json"
                    ev_f.write_text('{"timestamp": "2026-08-30T10:00:00Z"}', encoding="utf-8")
                    argv.extend(["--evidence", str(ev_f)])
                code = _main(argv)
            try:
                out = json.loads(s_out.getvalue())
            except Exception:
                out = {"errors": [s_err.getvalue()]}
            ok = checker(out, code)
            if ok:
                passed_tests += 1
                test_results[name] = {"status": "PASS", "code": code, "errors": out.get("errors", [])}
            else:
                test_results[name] = {"status": "FAIL", "code": code, "errors": out.get("errors", [])}

    score = int((passed_tests / total_tests) * 100)
    return {
        "suite": "Forensic Report Factuality Health Check Suite (Section 5.1 & 7-8)",
        "score": score,
        "max_score": 100,
        "passed": passed_tests,
        "total": total_tests,
        "status": "PASS" if score == 100 else "FAIL",
        "details": test_results,
    }
