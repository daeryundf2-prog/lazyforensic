#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_report.py — 보고서 초안 할루시네이션 검증기
측정값 없이 채워진 결론, 금지 문구, 근거 없는 해시/시각을 차단한다.
Antigravity hard grounding: 부모가 Model pro로 재실행해야 하는 검증.

알려진 한계 (docs/GAPS.md):
- 해시 grounding 은 파일 단위 집합 비교다. 해시 A를 파일 B 서술에 붙이는
  '해시-파일 결합 오류'는 잡지 못한다.
- 법령 조문은 korean_law MCP 응답과의 자동 대조가 불가능하다. 출처 표기 없는
  조문 인용을 경고(WARN)로 알릴 뿐이다. 조문 텍스트 자체의 진위는 검증하지 않는다.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 보고서 기준 타임존: 대한민국 실무 문서는 KST다. 실행 머신의 로컬 TZ과
# 무관하게 미래 시각 판정 경계를 고정한다.
KST = timezone(timedelta(hours=9))
CURRENT_YEAR = datetime.now(KST).year

# 정확히 표준 길이인 해시만 인정한다 (MD5 32 / SHA-1 40 / SHA-256 64).
# {32,64} 구간은 33·41자리 등 잘린 해시가 정상으로 둔갑하는 것을 허용했다.
HASH_VALUE_RE = re.compile(r"^(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

FORBIDDEN_PHRASES = [
    "명백히 입증",
    "법원에 유효",
    "법원에 제출 적격",
    "유출 확정",
    "유출의심",
    "확정적으로 유출",
    "조작 가능성",
    "Timestomping으로 단정",
    "court-admissible",
]

# 과단정 변형 패턴 클래스: 고정 문자열을 우회하는 어휘 변형("완벽히 입증",
# "확실시", "의심의 여지 없이" 등)을 구조로 잡는다. 부정 문맥은 NEGATION_RE
# 와 동일하게 면제한다. '재판에 필요' 같은 정상 서술은 포함하지 않는다.
CERTAINTY_ASSERTION_RES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"(?:완벽히|완전히|전적으로|절대적으로|명백히|분명히|확실히|명확히|결정적으로|의심의\s*여지\s*없이|100\s*퍼센트)\s*"
            r"(?:증명|입증|확인|밝혀)"
        ),
        "과단정 표현 (부사+증명/입증류)",
    ),
    (
        re.compile(r"(?:확실시|확정적|틀림없)\s*(?:된|이다|으로|임|함|하다)?"),
        "과단정 표현 (확실시/확정적/틀림없)",
    ),
    (
        re.compile(r"(?:타임스탬프|타임.?스탬프|시각\s*정보)\s*(?:조작|변조|위조)\s*(?:행위가?\s*)?(?:확실|확정|명백|단정)"),
        "타임스탬프 조작 단정",
    ),
]


def scan_certainty_variants(text: str) -> list[str]:
    hits = []
    for pat, label in CERTAINTY_ASSERTION_RES:
        for m in pat.finditer(text):
            after = text[m.end(): m.end() + 20]
            if not NEGATION_RE.match(after):
                hits.append(f"{label}: '{m.group(0)}'")
                break
    return hits

# 금지 문구 직후에 이어지면 과단정이 아닌 부정/한정/완화 문맥으로 보고 건너뛴다.
# 예: "유출의심 아님", "조작 가능성을 배제할 수 없다", "확실시되지 않았다",
# "입증되었다고 보기는 어렵다"
NEGATION_RE = re.compile(
    r"^\s*(?:을|를|이|가|은|는|되|됨|다|의|보기는|보기|할|이라고|으로|로)?\s*"
    r"(?:아님|아니|않|없|불가|금지|못|불가능|배제|어렵|단정할 수 없|확인할 수 없|알 수 없"
    r"|되지\s*않|안\s*되|되지\s*못|여부는\s*확인|여부를\s*확인|볼\s*수\s*없)"
)


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
    found = re.findall(r"\b[a-fA-F0-9]{64}\b", text)
    have = {h.lower() for h in found}
    for line in text.splitlines():
        if re.search(r"\bMD5\b|\bSHA-?1\b|md5_legacy", line, re.IGNORECASE):
            for pat in (r"\b[a-fA-F0-9]{40}\b", r"\b[a-fA-F0-9]{32}\b"):
                for h in re.findall(pat, line):
                    if h.lower() not in have:
                        found.append(h)
                        have.add(h.lower())
    # 분할 해시: 개행/공백이 hex 문자 사이에 낀 경우만 결합해 다시 찾는다.
    compact = re.sub(r"(?<=[a-fA-F0-9])[ \t]*\r?\n[ \t]*(?=[a-fA-F0-9])", "", text)
    if compact != text:
        for h in re.findall(r"\b[a-fA-F0-9]{64}\b", compact):
            if h.lower() not in have and h not in text:
                found.append(h)
                have.add(h.lower())
    return found


# 보고서/타임라인 양쪽에서 쓰는 날짜-시각 추출. 비교를 위해 정규형으로 통일한다.
ISO_DT_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?\b")
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
# 한국식 표기: "2024년 5월 1일", "2024년 5월 1일 오전 9시 30분", "…오후 3시",
# "…9:30" — '시/분' 접미사와 오전/오후, 콜론식 시각을 모두 받는다.
# '시'만 있고 '분'이 없으면 정분(整分) 근거가 없으므로 00분으로 정규화한다.
KOR_DT_RE = re.compile(
    r"\b(?P<y>\d{4})년\s*(?P<mo>\d{1,2})월\s*(?P<d>\d{1,2})일\s*"
    r"(?:(?P<ampm>오전|오후)\s*)?"
    r"(?:"
    r"(?:(?P<hkor>\d{1,2})시)(?:\s*(?P<mikor>\d{1,2})분)?"
    r"|(?P<hiso>\d{1,2}):(?P<miiso>\d{2})(?::(?P<seiso>\d{2}))?"
    r")?"
)
DOT_DATE_RE = re.compile(r"\b(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.(?!\d)")


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
    for m in ISO_DT_RE.finditer(text):
        y, mo, d, h, mi, s = m.groups()
        out.add(f"{int(y):04d}-{int(mo):02d}-{int(d):02d} {int(h):02d}:{mi}:{int(s) if s else 0:02d}")
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
        out.add(f"{date} {hour:02d}:{minute:02d}:{0:02d}")
    for m in DOT_DATE_RE.finditer(text):
        y, mo, d = m.groups()
        out.add(f"{int(y):04d}-{int(mo):02d}-{int(d):02d}")
    for m in ISO_DATE_RE.finditer(text):
        y, mo, d = m.groups()
        out.add(f"{int(y):04d}-{int(mo):02d}-{int(d):02d}")
    return sorted(out)


# 법령 조문 인용: 제1016조, 제3조의2, 제3조 제2항 등. "제출/조치" 같은 일반 어휘와 구별.
LAW_CITATION_RE = re.compile(r"제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s*제?\s*\d+\s*항)?")
LAW_SOURCE_MARKER_RE = re.compile(r"korean_law|LawSearch|법제처|국가법령정보센터")


def read_text_smart(path: Path, max_bytes: int = 8 * 1024 * 1024) -> tuple[str, str, bool]:
    """BOM/UTF-16/CP949를 순서대로 시도한다. 모두 실패하면 치환 문자로 읽고 lossy=True.

    구버전의 errors="ignore" 는 UTF-16 (Windows PowerShell Out-File 기본값) 입력을
    그대로 mojibake 로 만들어 금지 문구·해시 검출을 모두 놓쳤다.
    대용량 파일은 앞 max_bytes까지만 읽고 lossy=True로 표시한다 (Lazy Read:
    전체 로딩 OOM 방지, 잘림은 호출자가 경고한다).
    """
    size = path.stat().st_size if path.exists() else 0
    truncated = size > max_bytes
    with open(path, "rb") as f:
        raw = f.read(max_bytes + 1 if truncated else max_bytes)
    if truncated:
        raw = raw[:max_bytes]
    for enc in ("utf-8-sig", "utf-16", "cp949"):
        try:
            return raw.decode(enc), enc + ("(truncated)" if truncated else ""), truncated
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(lossy)" + ("(truncated)" if truncated else ""), True


def load_evidence_hashes(evidence_paths: list[str]) -> set[str]:
    hashes: set[str] = set()
    for p in evidence_paths:
        path = Path(p)
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        hashes.update(h.lower() for h in extract_hashes(text))
        # json / jsonl 구조 필드 (sha256, md5, sha1)
        objs: list = []
        try:
            data = json.loads(text)
            objs.append(data)
        except Exception:
            for line in text.splitlines():
                try:
                    objs.append(json.loads(line))
                except Exception:
                    pass
        for obj in objs:
            items = obj if isinstance(obj, list) else [obj]
            for item in items:
                if isinstance(item, dict):
                    for key, val in item.items():
                        if isinstance(val, str) and HASH_VALUE_RE.match(val):
                            hashes.add(val.lower())
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
        fname = _normalize_file_token(fname)
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
            text = path.read_text(encoding="utf-8", errors="ignore")
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


def check_hash_file_binding(report_text: str, evidence_pairs: dict[str, list[str]]) -> list[str]:
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
    # <evidence> 태그 안의 해시는 태그 귀속으로 이미 그라운딩되므로
    # 미결합 스키마 경고에서 제외한다 (태그 밖 서술만 검사).
    tag_spans: list[tuple[int, int]] = [
        (m.start(), m.end()) for m in EVIDENCE_TAG_RE.finditer(report_text)
    ]
    line_offsets: list[int] = []
    _off = 0
    for ln in lines:
        line_offsets.append(_off)
        _off += len(ln) + 1

    def _in_evidence_tag(abs_pos: int) -> bool:
        return any(s <= abs_pos < e for s, e in tag_spans)

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
        for m in re.finditer(r"\b([a-fA-F0-9]{64})\b", line):
            h = m.group(1).lower()
            owners = evidence_pairs.get(h)
            if not owners:
                continue  # 미등록 해시는 기존 '근거 없는 해시' 검사가 처리
            abs_pos = line_offsets[li] + m.start()
            in_tag = _in_evidence_tag(abs_pos)
            # 해시와 파일명이 인접(같은 줄 또는 ±2줄)한 파일명 수집
            window_files: set[str] = set()
            for wli in range(max(0, li - 2), min(len(lines), li + 3)):
                for fm in file_token_re.finditer(lines[wli]):
                    window_files.add(_normalize_file_token(fm.group(1)))
            owners_norm = [_normalize_file_token(o) for o in owners]
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


EVIDENCE_TAG_RE = re.compile(r"<evidence(?:\s+[^>]*)?>(.*?)</evidence>", re.DOTALL | re.IGNORECASE)

KOREAN_PARTICLE_SUFFIX = (
    r"(?:장관|차관|처장|청장|국장|위원장|부장|령|고시|지침|규정)?"
    r"(?:[이가은는을를의에]|에서|에게|서|과|와|도|만|부터|까지|로|으로|란|이란|라|라는|이라|이라는|며|이며|고|이고|통해|대해|관해)?"
    r"(?![가-힣\w])"
)

# Standard Korean verbal predicate suffix lookahead (handling 하였다, 했고, 했다, 한다, 함, 등에 따라, 등)
KOREAN_VERB_SUFFIX = (
    r"(?:하여(?:다|도|는|며|서)?|하였다|하였고|하였으며|하였으나|했(?:다|고|으며|으나|음)?|한다|하는|하며|하고|하기로|함|된)?"
    r"(?:[이가은는을를의에]|에서|에게|서|과|와|도|만|부터|까지|로|으로|란|이란|라|라는|이라|이라는|며|이며|고|이고|통해|대해|관해)?"
    r"(?![가-힣\w])"
)

# Number patterns supporting Arabic digits, Hangul Korean numbers, and Hanja/Sino-Korean numerals
HIST_NUM_4_PLUS = (
    r"(?:[4-9]|\d{2,}|[사오육칠팔구]|십[일이삼사오육칠팔구]?|[이삼사오육칠팔구]십[일이삼사오육칠팔구]?|"
    r"[四五六七八九]|[十百]|\d{2,}|[二三四五六七八九]十[一二三四五六七八九]?|十[一二三四五六七八九]?)"
)
HIST_NUM_3_PLUS = (
    r"(?:[3-9]|\d{2,}|[삼사오육칠팔구]|십[일이삼사오육칠팔구]?|[이삼사오육칠팔구]십[일이삼사오육칠팔구]?|"
    r"[三四五六七八九]|[十百]|\d{2,}|[二三四五六七八九]十[一二三四五六七八九]?|十[一二三四五六七八九]?)"
)
HIST_NUM_2_PLUS = (
    r"(?:[2-9]|\d{2,}|[이삼사오육칠팔구]|십[일이삼사오육칠팔구]?|[이삼사오육칠팔구]십[일이삼사오육칠팔구]?|"
    r"[二三四五六七八九]|[十百]|\d{2,}|[二三四五六七八九]十[一二三四五六七八九]?|十[一二三四五六七八九]?)"
)

HIST_ORD_PREFIX = r"(?:제|第)?\s*"
HIST_ORD_SUFFIX = r"(?:\s*(?:차|차례|次))"

FABRICATED_AGENCY_RE = re.compile(
    rf"(?<![가-힣])(?P<agency>디지털포렌식청|사이버수사처|국가포렌식연구원|사이버범죄특별수사처|경찰청사이버보안국|사이버보안청|인공지능윤리청|국가데이터청|개인정보보호청|사이버테러수사본부|정보보호조사위원회|디지털윤리위원회|한국연방검찰청|대검찰청사이버수사청){KOREAN_PARTICLE_SUFFIX}"
)

# Fabricated academic journals (Section 5.1 #3)
FABRICATED_ACADEMIC_JOURNALS_RE = re.compile(
    rf"(?<![가-힣])(?P<journal>대한인공지능법학회지|한국사이버포렌식학회논문집|한국디지털증거법학회지|국제사이버수사학술지|대한디지털포렌식학회논문지|한국인공지능윤리학회지|대한사이버보안학회지){KOREAN_PARTICLE_SUFFIX}"
)

# Future academic citations (Section 5.1 #3)
FUTURE_ACADEMIC_CITATION_RE = re.compile(
    r"(?<![가-힣])(?P<citation>(?:「[^」]{2,60}」\s*,\s*[^,\n]{2,30}?(?:학회지|논문집|학술지|저널|리뷰)\s*,\s*(?P<year>\d{4})\s*년?)|(?:(?P<author>[가-힣]{2,4})\s*교수?(?:의|가)?\s*[^.!?\n]{0,60}?(?P<year2>\d{4})\s*년\s*(?:발표한|게재한|발간한|출간한|수록된)?\s*(?:논문|저널|학술지)))"
)

ABOLISHED_GOV_AGENCIES: dict[str, tuple[str, str]] = {
    "정보통신부": ("2008년 폐지", "과학기술정보통신부 또는 방송통신위원회"),
    "문화공보부": ("1990년 폐지", "문화체육관광부"),
    "재정경제원": ("1998년 폐지", "기획재정부"),
    "재정경제부": ("2008년 개편", "기획재정부"),
    "미래창조과학부": ("2017년 개편", "과학기술정보통신부"),
    "과학기술처": ("1998년 개편", "과학기술정보통신부"),
    "과학기술부": ("2008년 개편", "과학기술정보통신부"),
    "교육인적자원부": ("2008년 개편", "교육부"),
    "교육과학기술부": ("2013년 개편", "교육부"),
    "건설교통부": ("2008년 개편", "국토교통부"),
    "국토해양부": ("2013년 개편", "국토교통부"),
    "행정자치부": ("2017년 개편", "행정안전부"),
    "안전행정부": ("2014년 개편", "행정안전부"),
    "국민안전처": ("2017년 개편", "행정안전부/소방청/해양경찰청"),
    "산업자원부": ("2008년 개편", "산업통상자원부"),
    "지식경제부": ("2013년 개편", "산업통상자원부"),
    "상공자원부": ("1994년 개편", "산업통상자원부"),
    "동력자원부": ("1993년 개편", "산업통상자원부"),
    "보건사회부": ("1994년 개편", "보건복지부"),
    "노동부": ("2010년 개편", "고용노동부"),
    "총무처": ("1998년 폐지", "행정안전부"),
    "내무부": ("1998년 폐지", "행정안전부"),
    "공보처": ("1998년 폐지", "문화체육관광부"),
    "기획예산처": ("2008년 개편", "기획재정부"),
    "철도청": ("2005년 개편", "한국철도공사/국가철도공단"),
}

# Fabricated Korean historical events & treaties bounds (Section 5.1 #3)
FABRICATED_HISTORICAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 갑오개혁 (1차, 2차, 3차(을미개혁)만 존재 -> 4차 이상 날조)
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:(?:갑오\s*개혁|甲午\s*改革)\s*{HIST_ORD_PREFIX}{HIST_NUM_4_PLUS}{HIST_ORD_SUFFIX})|(?:{HIST_ORD_PREFIX}{HIST_NUM_4_PLUS}{HIST_ORD_SUFFIX}\s*(?:갑오\s*개혁|甲午\s*改革)))"
            + KOREAN_PARTICLE_SUFFIX
        ),
        "갑오개혁은 제1차(1894), 제2차(1894~1895), 제3차(을미개혁, 1895)까지만 존재하며 4차 이상은 존재하지 않는 역사 날조입니다.",
    ),
    # 동학농민운동 / 동학농민혁명 (1차, 2차 봉기만 존재 -> 3차 이상 날조)
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:(?:동학\s*농민\s*(?:운동|혁명)|東學\s*農民\s*(?:運動|革命))\s*{HIST_ORD_PREFIX}{HIST_NUM_3_PLUS}{HIST_ORD_SUFFIX})|(?:{HIST_ORD_PREFIX}{HIST_NUM_3_PLUS}{HIST_ORD_SUFFIX}\s*(?:동학\s*농민\s*(?:운동|혁명)|東學\s*農民\s*(?:運動|革命))))"
            + KOREAN_PARTICLE_SUFFIX
        ),
        "동학농민운동은 제1차 봉기(백산), 제2차 봉기(삼례)까지만 존재하며 3차 이상은 날조입니다.",
    ),
    # 임진왜란 (1차 임진왜란 1592, 2차 정유재란 1597 -> 3차 이상 날조)
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:(?:임진\s*왜란|壬辰\s*倭亂)\s*{HIST_ORD_PREFIX}{HIST_NUM_3_PLUS}{HIST_ORD_SUFFIX})|(?:{HIST_ORD_PREFIX}{HIST_NUM_3_PLUS}{HIST_ORD_SUFFIX}\s*(?:임진\s*왜란|壬辰\s*倭亂)))"
            + KOREAN_PARTICLE_SUFFIX
        ),
        "임진왜란은 임진왜란(1592)과 정유재란(1597) 2차례 교전이며 3차 이상은 날조입니다.",
    ),
    # 단일 체결 조약/늑약의 차수 날조 (을사조약, 을사늑약, 정미7조약, 정미칠조약, 한일신협약, 한일의정서, 강화도조약, 조일수호조규, 한일병합조약, 한일합방조약, 조미수호통상조약, 한일기본조약, 남북기본합의서 등 2차 이상 불가)
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:(?:을사\s*조약|을사\s*늑약|정미\s*7\s*조약|정미\s*칠\s*조약|한일\s*신\s*협약|한일\s*의정서|강화도\s*조약|조일\s*수호\s*조규|한일\s*(?:병합|합방)\s*조약|조미\s*수호\s*통상\s*조약|한일\s*기본\s*조약|남북\s*기본\s*합의서|乙巳條約|乙巳勒約|丁未七條約|韓日新協約|韓日議政書|江華島條約|朝日修好條規|韓日倂合條約|朝美修好通商條約|韓日基本條約|南北基本合意書)\s*{HIST_ORD_PREFIX}{HIST_NUM_2_PLUS}{HIST_ORD_SUFFIX})|(?:{HIST_ORD_PREFIX}{HIST_NUM_2_PLUS}{HIST_ORD_SUFFIX}\s*(?:을사\s*조약|을사\s*늑약|정미\s*7\s*조약|정미\s*칠\s*조약|한일\s*신\s*협약|한일\s*의정서|강화도\s*조약|조일\s*수호\s*조규|한일\s*(?:병합|합방)\s*조약|조미\s*수호\s*통상\s*조약|한일\s*기본\s*조약|남북\s*기본\s*합의서|乙巳條約|乙巳勒約|丁未七條약|韓日新協約|韓日議政書|江華島條約|朝日修好條規|韓日倂合條約|朝美修好通商條約|韓日基本條約|南北基本合意書)))"
            + KOREAN_PARTICLE_SUFFIX
        ),
        "해당 조약/의정서는 1회 단일 체결 사건으로 제2차 이상의 조약은 존재하지 않는 역사 날조입니다.",
    ),
    # 단일 역사적 사건/운동/개혁 차수 날조 (을미개혁, 을미사변, 3·1운동, 삼일운동, 신미양요, 병인양요, 갑신정변, 임오군란, 사화, 4·19혁명, 5·18민주화운동, 6월민주항쟁 등 2차 이상 불가)
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:(?:을미\s*개혁|을미\s*사변|3\s*[·.]\s*1\s*운동|삼일\s*운동|신미\s*양요|병인\s*양요|갑신\s*정변|임오\s*군란|무오\s*사화|갑자\s*사화|기묘\s*사화|을사\s*사화|4\s*[·.]\s*19\s*혁명|사일구\s*혁명|5\s*[·.]\s*18\s*민주화\s*운동|오일팔\s*민주화\s*운동|6\s*월\s*민주\s*항쟁|육월\s*민주\s*항쟁|乙未改革|乙未事變|三\s*[·.]\s*一\s*運動|辛未洋擾|丙寅洋擾|甲申政變|壬午軍亂|四\s*[·.]\s*一九\s*革命|五\s*[·.]\s*一八\s*民主化\s*運動|六月\s*民主\s*抗爭)\s*{HIST_ORD_PREFIX}{HIST_NUM_2_PLUS}{HIST_ORD_SUFFIX})|(?:{HIST_ORD_PREFIX}{HIST_NUM_2_PLUS}{HIST_ORD_SUFFIX}\s*(?:을미\s*개혁|을미\s*사변|3\s*[·.]\s*1\s*운동|삼일\s*운동|신미\s*양요|병인\s*양요|갑신\s*정변|임오\s*군란|무오\s*사화|갑자\s*사화|기묘\s*사화|을사\s*사화|4\s*[·.]\s*19\s*혁명|사일구\s*혁명|5\s*[·.]\s*18\s*민주화\s*운동|오일팔\s*민주화\s*운동|6\s*월\s*민주\s*항쟁|육월\s*민주\s*항쟁|乙未改革|乙未事變|三\s*[·.]\s*一\s*運動|辛未洋擾|丙寅洋擾|甲申政變|壬午軍亂|四\s*[·.]\s*一九\s*革命|五\s*[·.]\s*一八\s*民主化\s*運動|六月\s*民主\s*抗爭)))"
            + KOREAN_PARTICLE_SUFFIX
        ),
        "해당 역사적 사건/개혁은 단일 1회성 사건으로 제2차 이상의 사건은 존재하지 않는 역사 날조입니다.",
    ),
]

# Impossible judicial procedures under Korean Law (Section 5.1 #4)
IMPOSSIBLE_JUDICIAL_PROCEDURE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:대검찰청|대검|고등검찰청|고검)(?:의|에서|이|은|는)?\s*[^.!?\n]{{0,100}}?(?:약식명령\s*(?:을\s*)?청구|약식기소|약식명령\s*기소))"
            r"(?!\s*(?:를\s*)?(?:검토|지시|지휘|지도|권고|시달|보고|요청|명령|하도록|하라는))"
            + KOREAN_VERB_SUFFIX
        ),
        "약식명령 청구권자는 1심 관할 지방검찰청(또는 지청) 검사이며, 상급 검찰청(대검찰청/고등검찰청)은 약식명령을 청구할 수 없습니다 (형사소송법 제448조 위반).",
    ),
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:대법원|고등법원)(?:에|에의|을\s*상대로)?\s*[^.!?\n]{{0,100}}?(?:약식명령\s*(?:을\s*)?청구|약식명령\s*신청|약식명령))"
            r"(?!\s*(?:를\s*)?(?:검토|지시|지휘|지도|권고|시달|보고|요청|명령|하도록|하라는))"
            + KOREAN_VERB_SUFFIX
        ),
        "약식명령은 지방법원 단독판사 관할이며 상급법원(대법원/고등법원)에 청구할 수 없습니다 (형사소송법 제448조 위반).",
    ),
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:경찰(?:청|관|서)?|사법경찰관)(?:이|의|은|는|에서)?\s*[^.!?\n]{{0,100}}?"
            r"(?:"
            r"(?:법원에\s*[^.!?\n]{0,50}?(?:구속영장|체포영장|압수수색영장|영장)(?:을|를)?\s*(?:직접\s*)?(?:청구|신청))"
            r"|(?:(?:직접\s*)?(?:구속영장|체포영장|압수수색영장|영장)(?:을|를)?\s*직접\s*청구)"
            r"|(?:(?:구속영장|체포영장|압수수색영장|영장)(?:을|를)?\s*청구)"
            r"))"
            r"(?!\s*(?:를\s*)?(?:신청|지시|지휘|지도|시달|보고|요청|기각|기각한))"
            + KOREAN_VERB_SUFFIX
        ),
        "영장 청구권은 검사에게만 전속되어 있으며, 사법경찰관은 검사에게 신청만 가능할 뿐 법원에 영장을 직접 청구하거나 신청할 수 없습니다 (헌법 제12조 제3항, 형사소송법 제200조의2, 제201조 위반).",
    ),
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:경찰(?:청|관|서)?|사법경찰관)(?:이|의|은|는)?\s*[^.!?\n]{{0,100}}?"
            r"(?:법원에\s*)?(?:직접\s*)?(?:공소제기|공소\s*제기|기소(?:\s*청구|\s*권|\s*결정|\s*강행|\s*(?:를\s*)?결정|하여|하였다|했다|함|한다)?))"
            r'(?!(?:\s*(?:의견|유예|중지|송치|지시|지휘|보고|요청)))'
            + KOREAN_VERB_SUFFIX
        ),
        "국가소추주의 및 기소독점주의에 따라 공소제기(기소)는 검사만 가능하며, 경찰은 송치/불송치 결정만 가능하고 직접 기소할 수 없습니다 (형사소송법 제246조 위반).",
    ),
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:대법원|대검찰청|대검)(?:의|에서|이)?\s*[^.!?\n]{{0,100}}?(?:구속영장|체포영장)(?:을|를)?\s*(?:청구|발부))"
            + KOREAN_VERB_SUFFIX
        ),
        "대법원은 상고심 법률심 법원으로 수사단계 구속영장을 발부하지 않으며 대검찰청은 영장청구 관할이 아닙니다.",
    ),
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:헌법재판소|헌재)(?:의|에서|이|은|는)?\s*[^.!?\n]{{0,100}}?(?:징역|금고|벌금|형벌|유죄|무죄)[^.!?\n]{{0,30}}?(?:선고|판결))"
            + KOREAN_VERB_SUFFIX
        ),
        "헌법재판소는 위헌법률심판, 탄핵, 헌법소원 등을 관할하며, 일반 형사사건의 징역형이나 유죄 판결을 선고할 수 없습니다 (헌법 제111조 위반).",
    ),
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>민사(?:소송|재판)(?:에서|으로|부)?\s*[^.!?\n]{{0,100}}?(?:징역|금고|벌금)[^.!?\n]{{0,30}}?(?:선고|부과))"
            + KOREAN_VERB_SUFFIX
        ),
        "민사소송은 사법상 권리분쟁 해결 절차로, 형벌인 징역형, 금고, 벌금형을 선고할 수 없습니다.",
    ),
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>형사(?:소송|재판)(?:의|에서)?\s*(?:원고|원고측))"
            + KOREAN_PARTICLE_SUFFIX
        ),
        "형사소송의 당사자는 검사와 피고인이며, '원고'는 민사/행정소송의 당사자 명칭으로 형사소송에는 존재하지 않습니다.",
    ),
    (
        re.compile(
            rf"(?<![가-힣\w])(?P<target>(?:지방법원|고등법원|대법원)(?:에|에의)?\s*[^.!?\n]{{0,50}}?헌법소원(?:\s*심판)?\s*청구)"
            + KOREAN_VERB_SUFFIX
        ),
        "헌법소원 심판 청구는 헌법재판소의 전속 관할이며 일반 법원에 청구할 수 없습니다 (헌법재판소법 제68조 위반).",
    ),
]


def run_forensic_health_check() -> dict:
    """Run comprehensive Section 5.1 & Section 6 forensic factuality health check suite.
    Scores 12 categories (10 points each = 120 points total; pass mark 100%).
    Returns health check report with score and test results.
    """
    import io
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

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
                code = main(argv)
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report hallucination guard — hard grounding verifier")
    parser.add_argument("report", nargs="?", default=None, help="Report draft path (.md/.html)")
    parser.add_argument("--health-check", action="store_true", help="Run Section 5.1 & 7-8 forensic factuality health check suite")
    parser.add_argument("--evidence", nargs="*", default=[], help="Evidence files (audit json, audit_trail.jsonl, timeline json, etc.) for hash grounding")
    parser.add_argument("--timeline", help="Timeline events.json for timestamp grounding (optional)")
    parser.add_argument("--claim-ledger", help="Optional path to claim-ledger.md for Section 6 verification")
    parser.add_argument("--law-cache", help="korean_law MCP 응답 캐시 JSON (법령대조 근거파일, 스켈레톤 — 자동대조 미완성)")
    parser.add_argument("--allow-historical", action="store_true", help="역사적 부처명 인용 허용 (오류 대신 경고 처리)")
    parser.add_argument("--morph-grounding", action="store_true", help="Kiwi 형태소 기반 증거-보고서 용어 일치도 검증 (Section 5.2)")
    parser.add_argument("--high-fidelity", action="store_true", help="Local High-Fidelity gate: require evidence files and <evidence> tags plus morpheme overlap (no Vertex API)")
    parser.add_argument("--json", action="store_true", help="Output JSON result")
    parser.add_argument("--strict", action="store_true", help="Fail with exit 1 if warnings are detected")
    args = parser.parse_args(argv)

    if args.health_check:
        health_report = run_forensic_health_check()
        if args.json:
            print(json.dumps(health_report, ensure_ascii=False, indent=2))
        else:
            print(f"[{health_report['status']}] {health_report['suite']}: {health_report['score']}/{health_report['max_score']} points ({health_report['passed']}/{health_report['total']} passed)")
            for name, d in health_report["details"].items():
                print(f"  - {name}: {d['status']}")
        return 0 if health_report["status"] == "PASS" else 1

    if not args.report:
        parser.print_help()
        return 2

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"[-] Report not found: {report_path}", file=sys.stderr)
        return 2

    text, encoding_used, lossy = read_text_smart(report_path)

    errors = []
    warnings = []
    if lossy:
        warnings.append(f"보고서 인코딩 판별 실패({encoding_used}) — 치환 문자가 있어 검증 누락 가능")

    # 1) 금지 문구 (직후 부정 문맥은 제외)
    forbidden = scan_forbidden(text)
    if forbidden:
        errors.append(f"금지 문구 발견: {sorted(set(forbidden))} — GEMINI 실패폐쇄 위반")

    # 1-1) 과단정 변형 (동의어 우회 방어)
    certainty_variants = scan_certainty_variants(text)
    if certainty_variants:
        errors.append(f"과단정 변형 표현 발견: {certainty_variants[:3]} — 부정/한정 표기로 바꿀 것")

    # 2) 해시 grounding (SHA-256 전체 + MD5/SHA-1 라벨 붙은 값)
    report_hashes = {h.lower() for h in extract_hashes(text)}
    if report_hashes:
        evidence_hashes = load_evidence_hashes(args.evidence)
        if evidence_hashes:
            orphan = report_hashes - evidence_hashes
            if orphan:
                errors.append(f"근거 없는 해시 {len(orphan)}개: {sorted(list(orphan))[:3]} — evidence 파일에 없는 해시를 보고서에 쓰지 말 것")
        elif args.evidence:
            ev_names = ", ".join(Path(p).name for p in args.evidence)
            errors.append(f"해시 {len(report_hashes)}개가 보고서에 있으나 evidence({ev_names})에 해당 해시 없음 — audit_timestamps.py --json 결과 또는 .lazyforensic/audit_trail.jsonl 을 --evidence 로 전달해야 함")
        else:
            # --evidence 가 아예 없으면 grounding 근거 자체가 없다. 과거에는
            # WARN(통과)으로 두어 '감사 생략 + 조작 해시'라는 핵심 위협이
            # 게이트를 통과했다. 실패폐쇄: 근거 없는 해시는 어느 경로로
            # 들어왔든 FAIL이다.
            errors.append(
                f"근거 없는 해시 {len(report_hashes)}개: {sorted(list(report_hashes))[:3]} — "
                "--evidence 미제시로 grounding 불가. 감사 없이 쓴 해시는 조작과 구별할 수 없다. "
                "audit_timestamps.py <원본> --json > audit.json 후 --evidence 로 재검증할 것"
            )

    # 2-1) 해시-파일명 인접성 검증 (Proximity Binding): 정상 해시를 다른
    #      파일 서술에 재사용하는 증거 조작을 차단한다. report_hashes 는
    #      위에서 1회 계산한 것을 재사용한다.
    if report_hashes and args.evidence:
        evidence_pairs = load_evidence_hash_file_pairs(args.evidence)
        for v in check_hash_file_binding(text, evidence_pairs):
            if v.startswith("[SCHEMA-WARN]"):
                warnings.append(v[len("[SCHEMA-WARN] "):])
            else:
                errors.append(v)

    # 3) Chain of Custody 빈칸 검증: 해시 없음 + 결론이 '일치'이면 경고
    if "미측정" not in text and "미확인" not in text:
        if re.search(r"비교 결과[^\n]*일치", text) and not report_hashes:
            warnings.append("해시 없이 '일치' 결론 — '미측정'으로 표기해야 함")

    # 4-0) 타임라인 grounding 없이도 성립하는 절대 검증: 현재 연도(그리고 month/day
    #    정합성)를 초과하는 시각 인용은 그 자체로 날조다. --timeline 유무와 무관하게 FAIL.
    #    단, '시행 예정/계획/예상' 등 미래 문맥은 정상 서술이므로 WARN으로 강등한다.
    #    타임존은 KST로 고정해 실행 머신 TZ과 무관하게 판정한다.
    now = datetime.now(KST)
    FUTURE_CONTEXT_RE = re.compile(r"예정|시행|계획|예상|예고|발효\s*예|until|scheduled|planned", re.IGNORECASE)
    future_hits: set[str] = set()
    for line in text.splitlines():
        for ts in extract_timestamps(line):
            ts_date = ts.split(" ")[0]
            try:
                is_future = datetime.strptime(ts_date, "%Y-%m-%d").replace(tzinfo=KST) > now
            except ValueError:
                # 월/일 오기(예: 2024-13-45)도 정합성 오류로 본다
                if ts not in future_hits:
                    future_hits.add(ts)
                    errors.append(f"불가능한 날짜 형식: '{ts}' — 달력에 존재하지 않는 날짜입니다")
                continue
            if not is_future:
                continue
            if FUTURE_CONTEXT_RE.search(line) and ts not in future_hits:
                future_hits.add(ts)
                warnings.append(
                    f"미래 시각({ts})이 예정/시행/계획 문맥에 사용됨 — 사건 서술이 아니라면 문제없으나, 사건 시각으로는 근거 없음"
                )
            elif ts not in future_hits:
                future_hits.add(ts)
                errors.append(
                    f"미래 시각 날조: '{ts}' — 현재({now.strftime('%Y-%m-%d %H:%M')})보다 미래 시점의 사건 서술은 존재할 수 없습니다"
                )

    # 4) 타임라인 grounding (optional): 보고서 내 시각이 timeline에 있는지.
    #    미확인/미측정 표기가 '있는 줄'의 시각은 건너뛴다 (전역 치환 아닌 per-item 판정).
    if args.timeline:
        try:
            tl_text, _, _ = read_text_smart(Path(args.timeline))
            tl_times = set(extract_timestamps(tl_text))
            for line in text.splitlines():
                if "미확인" in line or "미측정" in line:
                    continue
                orphan_times = [t for t in extract_timestamps(line) if t not in tl_times]
                if orphan_times:
                    warnings.append(f"타임라인에 없는 시각 {orphan_times[:3]} — 근거 시각만 사용해야 함")
        except Exception:
            pass

    # 5) 법령 환각: 조문 인용이 있으면 korean_law MCP 출처 표기 요구 (자동 대조 불가 → WARN)
    citations = sorted(set(LAW_CITATION_RE.findall(text)))
    if citations and not LAW_SOURCE_MARKER_RE.search(text):
        preview = ", ".join(citations[:3]) + (f" 외 {len(citations) - 3}" if len(citations) > 3 else "")
        warnings.append(f"조문 인용({preview})이 있으나 korean_law MCP 출처 표기 없음 — MCP 응답과 대조 전까지 미확인으로 둘 것")

    # 5-0) --law-cache 실대조: korean_law MCP 응답캐시(JSON)를 기반으로 조문 실존 여부를 대조한다.
    if args.law_cache:
        try:
            cache_path = Path(args.law_cache)
            cache_text = cache_path.read_text(encoding="utf-8", errors="replace")
            json.loads(cache_text)  # 유효한 JSON 형식 검증
            clean_cache = re.sub(r"\s+", "", cache_text)

            missing_citations = []
            matched_citations = []

            for cit in citations:
                clean_cit = re.sub(r"\s+", "", cit)
                if clean_cit in clean_cache:
                    matched_citations.append(cit)
                    continue

                m = re.search(r"제\s*(\d+)\s*(?:조(?:의\s*(\d+))?)", cit)
                found = False
                if m:
                    art_token = f"제{m.group(1)}조"
                    if m.group(2):
                        art_token += f"의{m.group(2)}"
                    if art_token in clean_cache:
                        found = True
                if found:
                    matched_citations.append(cit)
                else:
                    missing_citations.append(cit)

            if missing_citations:
                missing_str = ", ".join(missing_citations)
                msg = (
                    f"조문 인용({missing_str})이 법령 캐시({cache_path.name})에 존재하지 않음 — "
                    f"원문 대조 전까지 미확인 유지"
                )
                # B2-1: 조건부 FAIL 승격 — strict와 함께일 때만 errors, 기본은 WARN 유지(오탐 방지).
                if getattr(args, "strict", False):
                    errors.append(msg + " — 허위 조문 날조로 차단 (FAIL)")
                else:
                    warnings.append(msg + " (WARN)")
            elif matched_citations:
                # 모든 조문이 캐시와 일치하면 출처 미표기 경고(WARN)를 정상 그라운딩으로 해제
                # 단, --strict에서는 인간 대조를 위해 WARN 유지(오탐 방지).
                if not getattr(args, "strict", False):
                    warnings = [w for w in warnings if not ("조문 인용" in w and "korean_law MCP 출처 표기 없음" in w)]
        except Exception as exc:
            warnings.append(f"법령대조용 MCP 응답캐시 읽기 실패({args.law_cache}): {exc} — 원문 대조 전까지 미확인 유지")

    # 5-1) 법령 조문 상한 경계 검사 (허위 조문 날조 FAIL 차단, 공백 유연성 및 중복 스팬 제거)
    STATUTE_BOUNDS = {
        "민법": 1118,
        "형법": 372,
        "개인정보보호법": 76,
        "정보통신망법": 76,
        "정보통신망 이용촉진 및 정보보호 등에 관한 법률": 76,
        "부정경쟁방지법": 18,
        "부정경쟁방지 및 영업비밀보호에 관한 법률": 18,
        "전자문서법": 37,
        "전자문서 및 전자거래 기본법": 37,
        "형사소송법": 493,
        "민사소송법": 502,
        "상법": 935,
        "행정소송법": 46,
        "근로기준법": 116,
        "특정금융정보법": 22,
        "특정 금융거래정보의 보고 및 이용 등에 관한 법률": 22,
        "전자상거래법": 45,
        "전자상거래 등에서의 소비자보호에 관한 법률": 45,
        "자본시장법": 449,
        "자본시장과 금융투자업에 관한 법률": 449,
        "신용정보법": 53,
        "신용정보의 이용 및 보호에 관한 법률": 53,
        "소비자기본법": 86,
        "가사소송법": 72,
        "특허법": 232,
        "저작권법": 142,
        "도로교통법": 205,
        "의료법": 95,
    }

    # 가지번호(제N조의M) 허용 상한 — 초과 인용은 날조 (예: 민법 제1118조의99)
    # 주의: 상한은 법 개정마다 변한다. 정보통신망법 제44조의7(도청) 등 실존
    # 조문을 차단하지 않도록 실측 상한을 유지할 것.
    STATUTE_SUBARTICLES: dict[str, int] = {
        "민법": 2,
        "형법": 2,
        "형사소송법": 6,
        "개인정보보호법": 5,
        "정보통신망법": 7,  # 제44조의2~제44조의7 실존
        "정보통신망 이용촉진 및 정보보호 등에 관한 법률": 7,
        "부정경쟁방지법": 0,
        "전자문서법": 0,
        "민사소송법": 6,
        "상법": 5,
        "행정소송법": 0,
        "근로기준법": 2,
        "특정금융정보법": 0,
        "전자상거래법": 0,
        "자본시장법": 4,
        "신용정보법": 0,
        "소비자기본법": 0,
        "가사소송법": 0,
        "특허법": 3,
        "저작권법": 0,
        "도로교통법": 24,
        "의료법": 0,
    }

    def _make_statute_pattern(statute_name: str) -> re.Pattern:
        clean_name = re.sub(r"\s+", "", statute_name)
        escaped_chars = [re.escape(c) for c in clean_name]
        pattern_str = r"\s*".join(escaped_chars) + r"\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?"
        return re.compile(pattern_str)

    matched_spans: list[tuple[int, int]] = []
    sorted_statutes = sorted(STATUTE_BOUNDS.items(), key=lambda x: len(x[0]), reverse=True)

    for statute, max_art in sorted_statutes:
        pat = _make_statute_pattern(statute)
        for m in pat.finditer(text):
            span = m.span()
            if any(s <= span[0] and span[1] <= e for s, e in matched_spans):
                continue
            matched_spans.append(span)
            art_num = int(m.group(1))
            sub_num = m.group(2)
            full_ref = m.group(0)
            if art_num > max_art or art_num < 1:
                errors.append(
                    f"{statute} 허위 조문 날조 발견: {full_ref} (현행 {statute}은 제1조~제{max_art}조까지만 존재함)"
                )
            elif sub_num is not None:
                max_sub = STATUTE_SUBARTICLES.get(statute, 0)
                if int(sub_num) > max_sub or int(sub_num) < 1:
                    errors.append(
                        f"{statute} 허위 가지번호 날조 발견: {full_ref} "
                        f"(현행 {statute}의 가지번호(제N조의M)는 최대 '의{max_sub}'까지만 존재함)"
                    )

    # 5-2) 판례 연도 검사 (미래 연도 판결 날조 FAIL 차단)
    #    사건부호 판정은 문맥 요건을 충족한 후보만 검사한다: 임의 숫자+한글
    #    조합(예: "2024자연 12345")을 판례로 오탐하는 것을 방지하기 위해
    #    법원명 선행 또는 선고/판결/결정/사건/호 후행 어미를 요구한다.
    PRECEDENT_RE = re.compile(
        r"(?:대법원|서울고등법원|서울중앙지방법원|[가-힣]{2,6}지방법원|[가-힣]{2,6}고등법원|헌법재판소)?\s*"
        r"(?P<year>\d{4})\s*(?P<code>[가-힣]{1,4})\s*(?P<num>\d+)\b"
        r"(?=\s*(?:선고|판결|결정|사건|호|판시|대법원|지방법원|고등법원))"
    )
    VALID_CASE_CODES = {
        # 민사
        "가단", "가합", "가소", "나", "다", "라", "마", "그", "바", "자", "차",
        # 보전처분 / 민사신청
        "카", "카단", "카합", "카기", "카담", "카조", "카열", "카경",
        # 형사
        "고단", "고합", "고약", "노", "도", "로", "모", "오", "보", "코",
        # 가사소송 및 가사비송
        "드", "드단", "드합", "르", "르단", "르합", "므", "스", "으",
        "느", "느단", "느합", "즈", "즈단", "즈합",
        # 도산 / 회생 / 파산
        "회단", "회합", "회개", "개회", "개단", "개합", "하단", "하합", "하면", "개확",
        # 행정 / 특허
        "구", "구합", "구단", "누", "두", "루", "무", "허",
        # 헌법재판소
        "헌가", "헌나", "헌다", "헌라", "헌마", "헌바", "헌사", "헌아",
        # 소년보호
        "푸", "버",
        # 재심
        "재가단", "재가합", "재다", "재나", "재도", "재노", "재고단", "재고합",
    }
    for m in PRECEDENT_RE.finditer(text):
        year = int(m.group("year"))
        code = m.group("code")
        num = m.group("num")
        case_str = f"{year}{code}{num}"
        if year > CURRENT_YEAR:
            errors.append(
                f"판례 허위 날조 발견: 미래 연도 판결 인용 {case_str} (현재 {CURRENT_YEAR}년 이후 판결은 존재할 수 없음)"
            )
        elif year < 1948:
            errors.append(
                f"판례 허위 날조 발견: 대한민국 사법부 수립 이전 판결 {case_str} (1948년 이전)"
            )
        if code not in VALID_CASE_CODES:
            warnings.append(
                f"판례 부호 의심: 비표준 사건부호 인용 '{code}' in {case_str} — 대법원 규격 사건부호 여부를 확인하십시오."
            )

    # 5-3) 공공기관 및 수사기관 명칭 날조 검사 (Section 5.1 #2)
    for m in FABRICATED_AGENCY_RE.finditer(text):
        errors.append(f"공공기관 명칭 날조 발견: {m.group('agency')} (Section 5.1 #2 실존하지 않는 수사/포렌식 기관)")

    for agency, (abolish_info, successor) in ABOLISHED_GOV_AGENCIES.items():
        pat = re.compile(rf"(?<![가-힣]){re.escape(agency)}{KOREAN_PARTICLE_SUFFIX}")
        if pat.search(text):
            successor_candidates = [s.strip() for s in re.split(r"또는|/|,", successor) if s.strip()]
            has_successor_annotation = any(cand in text for cand in successor_candidates)
            has_historical_marker = bool(
                re.search(rf"\((?:구|과거)\s*{re.escape(agency)}\)", text)
                or re.search(rf"{re.escape(agency)}\s*\((?:현|현행)", text)
            )

            if has_successor_annotation or has_historical_marker or getattr(args, "allow_historical", False):
                warnings.append(
                    f"정부기관 역사적 구 부처명 인용: '{agency}' ({abolish_info}, 현행 '{successor}' 병기됨/역사적 검토 허용)"
                )
            else:
                errors.append(
                    f"정부기관 명칭 오류/날조 발견: 폐지된 구 부처명 인용 '{agency}' ({abolish_info}, 현행 '{successor}' 명칭 사용 필수) (Section 5.1 위반)"
                )

    # 5-3-2) 한국사 사건 및 조약 날조 검사 (Section 5.1 #3)
    for pat, desc in FABRICATED_HISTORICAL_PATTERNS:
        for m in pat.finditer(text):
            matched_text = m.group("target") if "target" in m.groupdict() else m.group(0)
            errors.append(
                f"한국사 사건/조약 날조 발견: '{matched_text}' — {desc} (Section 5.1 #3 위반)"
            )

    # 5-3-3) 불가능한 사법 절차 날조 검사 (Section 5.1 #4)
    for pat, desc in IMPOSSIBLE_JUDICIAL_PROCEDURE_PATTERNS:
        for m in pat.finditer(text):
            matched_text = m.group("target") if "target" in m.groupdict() else m.group(0)
            errors.append(
                f"불가능한 사법절차 날조 발견: '{matched_text}' — {desc} (Section 5.1 #4 위반)"
            )

    # 5-3-4) 학술 논문 및 저널 날조 검사 (Section 5.1 #3)
    for m in FABRICATED_ACADEMIC_JOURNALS_RE.finditer(text):
        fake_journal = m.group("journal")
        errors.append(
            f"학술논문/학술지 날조 발견: 실존하지 않는 가짜 학술지 '{fake_journal}' (Section 5.1 #3 위반)"
        )
    for m in FUTURE_ACADEMIC_CITATION_RE.finditer(text):
        cite_str = m.group("citation")
        pub_year = int(m.group("year") or m.group("year2"))
        if pub_year > CURRENT_YEAR:
            errors.append(
                f"학술논문/학술지 날조 발견: 미래 연도 학술 논문 '{cite_str}' ({pub_year}년) - 현재 연도({CURRENT_YEAR}년)보다 미래의 학술 출판물은 날조된 환각입니다 (Section 5.1 #3 위반)"
            )

    # 5-4) <evidence> 태그 인용 검증 (Section 3.2 #1 Evidence-First)
    evidence_matches = EVIDENCE_TAG_RE.findall(text)
    if evidence_matches:
        combined_ev_text = ""
        for ep in args.evidence:
            p = Path(ep)
            if p.is_file():
                combined_ev_text += "\n" + p.read_text(encoding="utf-8", errors="replace")

        for quote in evidence_matches:
            q_strip = quote.strip()
            if not q_strip:
                errors.append("<evidence> 태그가 비어 있습니다. 증거 측정값을 채우십시오.")
            elif combined_ev_text:
                clean_q = re.sub(r"\s+", " ", q_strip)
                clean_ev = re.sub(r"\s+", " ", combined_ev_text)
                if clean_q not in clean_ev:
                    errors.append(
                        f"근거 인용 불일치: <evidence> 구절('{q_strip[:25]}...')이 제공된 증거 파일에 존재하지 않습니다."
                    )
            else:
                errors.append(
                    f"근거 인용 검증 불가: <evidence> 구절('{q_strip[:25]}...')이 있으나 "
                    "--evidence 파일이 제공되지 않았거나 비어 있습니다. 증거 파일을 지정하십시오."
                )

    # 5-5) Local High-Fidelity 비파라메트릭 게이트 (Section 4.2; Vertex API 호출 없음)
    if args.high_fidelity:
        if not args.evidence:
            errors.append("[High-Fidelity Grounding 위반] High-Fidelity 검증을 위한 원문/증거(--evidence)가 지정되지 않았습니다.")
        elif not evidence_matches:
            errors.append("[High-Fidelity Grounding 위반] 증거 기반 사실관계를 뒷받침하는 <evidence> 원문 인용 태그가 없습니다.")

    # 5-6) Kiwi 형태소 기반 포렌식 그라운딩 검증 (Section 5.2 & 4.2)
    if (args.morph_grounding or args.high_fidelity) and args.evidence:
        try:
            from scripts.korean_morph_forensic import calculate_forensic_grounding
        except ImportError:
            try:
                from korean_morph_forensic import calculate_forensic_grounding
            except ImportError:
                calculate_forensic_grounding = None

        if calculate_forensic_grounding is not None:
            combined_ev = "\n".join(
                Path(ep).read_text(encoding="utf-8", errors="replace")
                for ep in args.evidence if Path(ep).is_file()
            )
            if combined_ev.strip():
                finding_section_match = re.search(
                    r"(?:^|\n)\s*(?:#{1,4}\s*|\d+[\.\)]\s*|제\s*\d+\s*조?\s*|\b|【|\[)?\s*(?:\d+[\.\)]\s*)?"
                    r"(?:감정\s*결과|감정\s*대상물|분석\s*내용|타임라인\s*분석|상세\s*분석|해시\s*검증|증거\s*분석)\b[^\n]*\n?"
                    r"(.*?)"
                    r"(?=\n#{1,2}\s*(?:종합\s*의견|의견|서명|결론|비고|첨부)|\Z)",
                    text,
                    re.DOTALL,
                )
                eval_target = finding_section_match.group(1) if finding_section_match else text
                clean_rep = re.sub(r"<[^>]+>", " ", eval_target)
                clean_ev = re.sub(r"<[^>]+>", " ", combined_ev)
                thresh = 0.70 if args.high_fidelity else 0.55
                grounding_res = calculate_forensic_grounding(clean_ev, clean_rep, threshold=thresh, filter_procedural=True)
                if not grounding_res["is_grounded"]:
                    msg = (
                        f"포렌식 형태소 그라운딩 미달 ({grounding_res['grounding_score']*100:.1f}% < {int(thresh*100)}%): "
                        f"증거에 없는 추정 용어 다수 {grounding_res['unsupported_terms'][:5]}"
                    )
                    if args.high_fidelity:
                        errors.append(f"[High-Fidelity Grounding 위반] {msg}")
                    else:
                        warnings.append(msg)

    # 5-7) Section 6 Claim Ledger 검증
    if args.claim_ledger:
        try:
            from scripts.verify_claim_ledger import verify_claim_ledger_file
        except ImportError:
            try:
                from verify_claim_ledger import verify_claim_ledger_file
            except ImportError:
                verify_claim_ledger_file = None

        if verify_claim_ledger_file is not None:
            ledger_report = verify_claim_ledger_file(args.claim_ledger, synthesis_path=args.report)
            if not ledger_report["ok"]:
                for v in ledger_report["violations"]:
                    errors.append(f"[Claim Ledger 위반] [{v['claimId']}] {v['violation']}")
        else:
            warnings.append("verify_claim_ledger 모듈을 찾을 수 없어 원장 검증을 건너뛰었습니다.")

    result = {
        "report": str(report_path),
        "encoding": encoding_used,
        "forbidden_hits": sorted(set(forbidden)),
        "report_hashes": sorted(list(report_hashes)),
        "law_citations": citations[:10],
        "errors": errors,
        "warnings": warnings,
        "verdict": "FAIL" if errors else ("WARN" if warnings else "PASS"),
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if errors:
            print(f"[FAIL] 할루시네이션 검증 실패 ({len(errors)} errors, {len(warnings)} warnings)", file=sys.stderr)
            for e in errors:
                print(f"  - ERROR: {e}", file=sys.stderr)
            for w in warnings:
                print(f"  - WARN: {w}", file=sys.stderr)
            print(f"[HINT] 수정 후 재실행: python scripts/verify_report.py {report_path} --evidence audit.json --json", file=sys.stderr)
        elif warnings:
            print(f"[WARN] {len(warnings)} warnings (통과하나 부모가 Model pro로 대조 필요)")
            for w in warnings:
                print(f"  - {w}")
            if args.strict:
                print(f"[FAIL] --strict 모드: 경고가 감지되어 차단합니다.", file=sys.stderr)
        else:
            print("[PASS] 할루시네이션 검증 통과 — 근거 없는 금지 문구/해시 없음")

    if errors or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
