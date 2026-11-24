#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keyword_report.py — 증거형 키워드 검색 리포트

"해당 키워드가 있는가"를 grep 결과가 아니라 감정서에 붙일 수 있는
형태로 만든다: 파일 경로 + 줄 번호 + 원문 + 검색 대상 파일의 SHA-256.
'없음'도 '검색 안 함'이 아니라 'N개 파일 전수 검색 결과 미검출'로
기록한다 — 부재 증명에 필요한 검색 범위가 보고서에 남는다.

사용:
    python scripts/keyword_report.py evidence/ "보이스피싱" "계좌이체"
    python scripts/keyword_report.py dir/ 키워드 --context 2 --json
    python scripts/keyword_report.py dir/ 키워드 -o report.json
    python scripts/keyword_report.py dir/ 키워드 --encoding cp949   # 운영자가 확인한 코덱
    LAZYFORENSIC_KEYWORD_ENCODING=cp949 python scripts/keyword_report.py dir/ 키워드

알려진 한계:
- 리터럴 문자열 검색이다. OCR 오류·유사표현·이미지 속 글자는
  잡지 못한다. 유의어 확장 검색은 이 도구의 역할이 아니다.
- 바이너리 파일은 건너뛴다 (읽기 실패 파일은 skipped로 기록).
- 코덱이 확실한 파일: BOM, 파일 안의 선언(XML encoding=·HTML charset=), BOM 없는 UTF-16의
  ASCII 글자 NUL 위치(LE: 글자 뒤, BE: 글자 앞). 그 밖의 파일은 바이트 줄마다(CR·LF·CRLF 등
  str.splitlines()와 같은 경계) 엄격한 UTF-8 → CP949 순으로 고른다. 줄마다 코덱이 다르면 'mixed'로
  표시하고, 해석하지 못한 줄은 검색하지 않은 구간으로 적어 부분 검색으로 센다.
- 선언 없는 줄의 해석은 검색어와 무관하게 정한다. 그 줄 바이트를 UTF-8·CP949·EUC-JP·Big5·Shift-JIS로
  엄격히 읽은 결과 중 그 코덱의 문자 체계로 자연스러운 해석(CP949는 KS X 1001 한글 음절,
  EUC-JP·Shift-JIS는 한자·가나(반각 포함), Big5는 한자·주음 부호)이 둘 이상이면 그 줄은 코덱을
  바이트만으로 정할 수 없다. 그런 줄의 히트는 모든 해석의 같은 바이트에서 같은 글자로 읽히는
  부분(예: ASCII 'STATUS')에 키워드가 있을 때만 확정하고, 인용문도 그 부분만 싣는다(다른 부분은
  〔코덱별 해석 다름〕). 해석에 따라 보이거나 안 보이는 키워드는 줄 단위 후보(candidate_hits)로
  해석별 본문·키워드와 함께 남기고 확정 히트에 넣지 않는다. 짧은 CP949 한국어(예: '정상')도 EUC-JP·Big5
  한자로 읽히므로 선언 없이는 후보다 — 작성 코덱을 확인해 --encoding(보고서는 --keyword-encoding)이나
  환경변수 LAZYFORENSIC_KEYWORD_ENCODING으로 지정하면 확정된다.
- 운영자 지정 코덱은 BOM·선언 없는 줄을 그 코덱으로 읽는다. 그 코덱으로 해석되지 않고 엄격한 UTF-8로만
  읽히는 줄은 UTF-8로 읽고, 두 코덱 모두로 읽히고 UTF-8 3바이트 이상 글자가 있는 줄은 두 해석의 후보로
  남긴다. 파일의 BOM·선언·UTF-16 NUL 위치와 다른 코덱을 지정하면 지정 코덱으로 읽되 충돌로 기록하고,
  그 파일의 히트(지정 코덱·표지 코덱 모두)는 후보로만 남긴다.
- 엄격한 CP949 해석이 확장 완성형(UHC 확장 영역)에 기대거나 파일 전체가 UTF-8·CP949로 해석되지 않으면
  BOM 없는 UTF-16 LE·BE 후보로도 검색해 '인코딩 불확실'로 센다.
- 디코딩에 성공해도 제어 문자 비율이 1%를 넘으면 텍스트가 아닌 기록으로 보고
  텍스트 검색 수에서 빼 따로 센다.
- 줄과 키워드를 NFC로 맞춘 뒤 비교한다 (macOS 등의 NFD 한글 대응).
- 히트는 줄 단위로 센다. 한 줄에 키워드가 여러 개면 1건이고 keywords에 줄 안 위치 순으로 모두 남긴다.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_json import print_json  # noqa: E402

TEXT_EXTS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm",
    ".log", ".ini", ".cfg", ".yaml", ".yml", ".py", ".js", ".mjs",
    ".java", ".c", ".cpp", ".h", ".sh", ".ps1", ".sql",
}

ENCODING_ENV = "LAZYFORENSIC_KEYWORD_ENCODING"
SOURCE_LABELS = {"cli": "명령 인자", "env": f"환경설정 {ENCODING_ENV}"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def encoding_error(name: str) -> str | None:
    """키워드 검색에 쓸 수 없는 코덱 이름이면 사유. 없는 이름과 바이트 변환 코덱(base64_codec 등)을 거른다."""
    try:
        info = codecs.lookup(name)
    except LookupError:
        return f"알 수 없는 인코딩: {name}"
    if not getattr(info, "_is_text_encoding", True):
        return f"텍스트 인코딩이 아닌 코덱: {name} (바이트 변환 코덱은 키워드 검색에 쓸 수 없음)"
    return None


def resolve_encoding(cli_value: str | None, environ: dict | None = None) -> tuple[str | None, str | None]:
    """(코덱, 출처). 명령 인자가 환경변수보다 우선한다. 쓸 수 없는 코덱이면 ValueError."""
    env = os.environ if environ is None else environ
    if cli_value:
        value, source = cli_value, "cli"
    else:
        value, source = (env.get(ENCODING_ENV) or "").strip(), "env"
    if not value:
        return None, None
    err = encoding_error(value)
    if err:
        raise ValueError(err + (f" (환경변수 {ENCODING_ENV})" if source == "env" else ""))
    return value, source


def operator_codec_note(encoding: str, source: str | None) -> str:
    """보고서·선조사에 싣는 운영자 지정 코덱 표기. 자동 판정과 구별한다."""
    return f"운영자 지정 코덱: {encoding} ({SOURCE_LABELS.get(source or 'cli', source)})"


# (BOM, 디코딩 코덱, 이 표지와 같은 뜻으로 보는 지정 코덱 이름). UTF-32 LE BOM이 UTF-16 LE BOM으로
# 시작하므로 긴 것부터 본다.
BOM_MARKERS = (
    (codecs.BOM_UTF32_LE, "utf-32", {"utf-32", "utf-32-le"}),
    (codecs.BOM_UTF32_BE, "utf-32", {"utf-32", "utf-32-be"}),
    (codecs.BOM_UTF8, "utf-8-sig", {"utf-8", "utf-8-sig"}),
    (codecs.BOM_UTF16_LE, "utf-16", {"utf-16", "utf-16-le"}),
    (codecs.BOM_UTF16_BE, "utf-16", {"utf-16", "utf-16-be"}),
)
BOMS = tuple((bom, codec) for bom, codec, _ in BOM_MARKERS)
# 선언은 XML 머리말과 HTML meta에서만 읽는다 (소스 코드 속 'encoding=' 문자열은 선언이 아니다).
DECLARED_RE = re.compile(
    rb"""^\s*<\?xml[^>]*?\sencoding\s*=\s*["']([A-Za-z][A-Za-z0-9_.:-]*)"""
    rb"""|<meta\s[^>]*?charset\s*=\s*["']?([A-Za-z][A-Za-z0-9_.:-]*)""", re.IGNORECASE)
_KOREAN_CODECS = {"cp949", "euc_kr"}  # 웹 표준처럼 EUC-KR 표기는 상위 집합 CP949로 읽는다

_PRINTABLE_ASCII = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}
_HALFWIDTH_KANA_RE = re.compile(r"[\uff61-\uff9f]")


def _ascii_units(data: bytes, codec: str) -> int:
    """UTF-16 바이트 순서 codec으로 읽었을 때 ASCII 글자(상위 바이트 0)가 되는 2바이트 단위 수."""
    pairs = zip(data[0::2], data[1::2])
    if codec == "utf-16-le":
        return sum(1 for lo, hi in pairs if hi == 0 and lo in _PRINTABLE_ASCII)
    return sum(1 for hi, lo in pairs if hi == 0 and lo in _PRINTABLE_ASCII)


def _plausible(text: str) -> bool:
    """BOM 없는 UTF-16 후보가 텍스트로 자연스러운가: 미할당·사용자 정의·대리 문자,
    반각 가타카나, 제어 문자 과다가 없다."""
    if not text or _HALFWIDTH_KANA_RE.search(text) or not_text_reason(text):
        return False
    return all(unicodedata.category(ch) not in ("Cn", "Co", "Cs") for ch in text)


# 코덱마다 그 언어의 글자로 자연스러운 문자 체계 (유니코드 문자 이름 접두사). 문자(L*)만 본다.
_SCRIPTS = {
    "cp949": ("HANGUL SYLLABLE",),
    "euc_jp": ("CJK", "HIRAGANA", "KATAKANA", "HALFWIDTH KATAKANA"),
    "shift_jis": ("CJK", "HIRAGANA", "KATAKANA", "HALFWIDTH KATAKANA"),
    "big5": ("CJK", "BOPOMOFO"),
}


def coherent(codec: str, text: str) -> bool:
    """codec으로 읽은 text가 그 코덱의 문자 체계로 자연스러운가 (검색어와 무관한 판정).

    미할당·사용자 정의·대리·제어 문자가 있거나 제어 문자가 많으면 아니다. CP949 해석은 KS X 1001 한글 음절만,
    EUC-JP·Shift-JIS는 한자·가나(반각 포함), Big5는 한자·주음 부호만 문자로 허용한다 — UTF-8 한국어를
    CP949로 읽은 '湲곕줉'(한자+확장 음절)이나 CP949 한국어를 Big5로 읽은 '蕾熱ц'(키릴 문자)는 자연스럽지 않다.
    UTF-8 해석은 문자 체계를 제한하지 않는다."""
    if not text or not_text_reason(text):
        return False
    codec = "cp949" if codec in _KOREAN_CODECS else codec
    allowed = _SCRIPTS.get(codec)
    for ch in text:
        if ch.isascii():
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cn", "Co", "Cs", "Cc", "Cf"):
            return False
        if cat[0] != "L" or allowed is None:
            continue
        if not unicodedata.name(ch, "").startswith(allowed):
            return False
        if codec == "cp949":
            try:
                ch.encode("euc_kr")
            except UnicodeEncodeError:
                return False
    return True


def utf16_candidates(data: bytes) -> list[tuple[str, str]]:
    """BOM 없는 UTF-16으로 볼 수 있는 (코덱, 본문) 후보. 엄격히 해석되고 자연스러운 것만."""
    if len(data) % 2:
        return []
    out = []
    for codec in ("utf-16-le", "utf-16-be"):
        try:
            text = data.decode(codec)
        except UnicodeDecodeError:
            continue
        if _plausible(text):
            out.append((codec, text))
    return out


def uhc_extension(data: bytes) -> bool:
    """CP949 2바이트 글자 중 KS X 1001(EUC-KR) 밖의 확장 영역(선행 0x81–0xA0 또는 후행 < 0xA1)이 있는가.

    일반 한국어 문서는 대부분 KS X 1001 안에서 끝난다. 확장 영역에 기대어서만 CP949로 읽히는 파일은
    다른 코덱(BOM 없는 UTF-16 등)의 바이트일 수 있다."""
    i, n = 0, len(data)
    while i < n:
        lead = data[i]
        if lead < 0x80:
            i += 1
            continue
        if i + 1 < n and (lead < 0xA1 or data[i + 1] < 0xA1):
            return True
        i += 2
    return False


def file_marker(data: bytes) -> dict | None:
    """파일이 스스로 밝힌 코덱 표지: BOM, BOM 없는 UTF-16의 ASCII NUL 위치, XML·HTML 선언.

    {'kind': 'bom'|'nul_pattern'|'declared', 'codec': 디코딩 코덱 또는 None(알 수 없는 선언),
     'accepts': 같은 뜻의 코덱 이름 집합, 'label': 표기}. 표지가 없으면 None."""
    for bom, codec, accepts in BOM_MARKERS:
        if data.startswith(bom):
            return {"kind": "bom", "codec": codec, "accepts": accepts, "label": f"{codec} BOM"}
    if b"\x00" in data:
        # NUL 위치만으로는 '한글' 같은 비ASCII UTF-16을 판정할 수 없다(LE와 BE 모두 NUL이 생긴다).
        # ASCII 글자(LE: 글자+NUL, BE: NUL+글자)가 한쪽 바이트 순서로만 보일 때만 정한다.
        if len(data) % 2 == 0:
            le, be = _ascii_units(data, "utf-16-le"), _ascii_units(data, "utf-16-be")
            codec = "utf-16-le" if le and not be else "utf-16-be" if be and not le else None
            if codec:
                return {"kind": "nul_pattern", "codec": codec, "accepts": {codec},
                        "label": f"{codec} NUL 위치"}
        return None
    declared = DECLARED_RE.search(data[:1024])
    if declared:
        label = (declared.group(1) or declared.group(2)).decode("ascii").lower()
        try:
            codec = codecs.lookup(label).name
        except LookupError:
            return {"kind": "declared", "codec": None, "accepts": set(), "label": label}
        accepts = {codec} | (_KOREAN_CODECS if codec in _KOREAN_CODECS else set())
        codec = "cp949" if codec == "euc_kr" else codec
        return {"kind": "declared", "codec": codec, "accepts": accepts, "label": f"선언 {label}"}
    return None


def decode_text(data: bytes) -> tuple[str | None, str | None, str]:
    """(본문, 코덱, 판정 근거)를 돌려준다. 본문이 None이면 근거는 판정 불가 사유다.

    치환 문자로 읽어 넘기지 않는다 — 잘못 읽은 파일의 '미검출'은 거짓 음성이 된다.
    """
    marker = file_marker(data)
    if marker is not None:
        codec = marker["codec"]
        if codec is None:
            return None, None, f"선언된 인코딩 '{marker['label']}'을 알 수 없음"
        try:
            return data.decode(codec), codec, marker["kind"]
        except UnicodeDecodeError:
            if marker["kind"] == "bom":
                return None, None, f"{codec} BOM이 있으나 내용이 {codec}로 해석되지 않음"
            if marker["kind"] == "declared":
                return None, None, f"{marker['label']}로 해석되지 않음"
    if b"\x00" in data:
        return None, None, "NUL 바이트가 있어 인코딩을 판정할 수 없음"
    for codec in ("utf-8", "cp949"):
        try:
            return data.decode(codec), codec, "strict"
        except UnicodeDecodeError:
            continue
    return None, None, UNDECODABLE_FALLBACK


# 텍스트에 나오지 않는 C0 제어 문자 (탭·줄바꿈·폼피드·ESC 제외). 디코딩에 성공해도 이 비율이 높으면 텍스트가 아니다.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0e-\x1a\x1c-\x1f\x7f]")
NOT_TEXT_RATIO = 0.01


def _runs(numbers: list[int]) -> list[list[int]]:
    """오름차순 줄 번호를 [처음, 끝] 구간으로 묶는다."""
    runs: list[list[int]] = []
    for n in numbers:
        if runs and runs[-1][1] == n - 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return runs


# 선언 없는 바이트를 다른 코덱으로 읽은 해석. 짧은 한국어 CP949('캡처')는 엄격한 UTF-8로도,
# EUC-JP·Big5의 한자 몇 글자는 CP949로도, CP949 한국어는 EUC-JP·Big5 한자로도 오류 없이 읽힌다.
ALT_CODECS = ("utf-8", "cp949", "euc_jp", "big5", "shift_jis")
UNDECODABLE_FALLBACK = "UTF-8·CP949 어느 쪽으로도 해석되지 않음"
MASK = "〔코덱별 해석 다름〕"
# str.splitlines()의 경계 중 ASCII 바이트인 것. 이 바이트는 ASCII 호환 다바이트 코덱(CP949·EUC-JP·Big5·
# Shift-JIS)에서 글자의 둘째 바이트가 될 수 없으므로 코덱과 무관한 줄 경계다.
_RECORD_SEP = re.compile(rb"\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e]")


def split_records(data: bytes) -> list[bytes]:
    """바이트를 코덱과 무관한 줄 경계로 나눈다. 끝의 줄바꿈 뒤 빈 줄은 str.splitlines()처럼 세지 않는다."""
    parts = _RECORD_SEP.split(data)
    if len(parts) > 1 and parts[-1] == b"":
        parts.pop()
    return parts


def _try(raw: bytes, codec: str) -> str | None:
    try:
        return raw.decode(codec)
    except UnicodeDecodeError:
        return None


def _ascii_compatible(codec: str) -> bool:
    try:
        return "\n a\r".encode(codec) == b"\n a\r"
    except (UnicodeError, LookupError):
        return False


def _keywords_in(text: str, wanted: list[tuple[str, str]]) -> list[str]:
    """text에 든 키워드를 줄 안 첫 위치 순(같으면 문자열 순)으로. 검색어 순서와 무관하다."""
    normalized = unicodedata.normalize("NFC", text)
    found = [(normalized.find(nfc), kw) for kw, nfc in wanted if nfc and nfc in normalized]
    return [kw for _, kw in sorted(found)]


def _char_spans(raw: bytes, codec: str) -> list[tuple[int, int, str]]:
    """(시작 바이트, 끝 바이트, 글자) — 각 글자가 어느 바이트에서 나왔는지."""
    dec = codecs.getincrementaldecoder(codec)()
    out, start = [], 0
    for i in range(len(raw)):
        piece = dec.decode(raw[i:i + 1])
        for ch in piece:
            out.append((start, i + 1, ch))
        if piece:
            start = i + 1
    return out


def masked_quote(raw: bytes, readings: dict[str, str], chosen: str) -> str:
    """chosen 해석의 글자 중 모든 해석에서 같은 바이트가 같은 글자로 읽히는 것만 남긴 인용문.
    해석마다 다른 부분은 MASK 한 번으로 줄인다."""
    others = [set(_char_spans(raw, c)) for c in readings if c != chosen]
    out: list[str] = []
    for item in _char_spans(raw, chosen):
        if all(item in o for o in others):
            out.append(item[2])
        elif not out or out[-1] != MASK:
            out.append(MASK)
    return "".join(out)


def _choose_auto(raw: bytes) -> tuple[str | None, str | None, dict[str, str]]:
    if raw.isascii():
        return "ascii", raw.decode("ascii"), {}
    for codec in ("utf-8", "cp949"):
        text = _try(raw, codec)
        if text is not None:
            return codec, text, {}
    return None, None, {}


def _explicit_chooser(name: str):
    """운영자 지정 코덱의 줄 해석: 지정 코덱 → (지정 코덱으로 안 되면) 엄격한 UTF-8.
    둘 다 읽히고 UTF-8 3바이트 이상 글자가 있으면 UTF-8 해석도 남겨 두 해석의 후보로 본다."""
    def choose(raw: bytes) -> tuple[str | None, str | None, dict[str, str]]:
        if raw.isascii():
            return "ascii", raw.decode("ascii"), {}
        mine = _try(raw, name)
        utf8 = None if name == "utf-8" else _try(raw, "utf-8")
        if mine is not None and (utf8 is None or utf8 == mine):
            return name, mine, {}
        if mine is None and utf8 is not None:
            return "utf-8", utf8, {}
        if mine is None:
            return None, None, {}
        if not any(b >= 0xE0 for b in raw):
            return name, mine, {}
        # UTF-8 3바이트 이상 글자가 있고 지정 코덱 해석이 그 코덱의 문자 체계로 자연스럽지 않으면
        # (UTF-8 '기록'을 CP949로 읽은 '湲곕줉') UTF-8 줄이다. 둘 다 자연스러우면 두 해석의 후보다.
        if ("cp949" if name in _KOREAN_CODECS else name) in _SCRIPTS and not coherent(name, mine):
            return "utf-8", utf8, {}
        return name, mine, {"utf-8": utf8}
    return choose


def _hit(lines: list[str], i: int, keywords: list[str], context: int, text: str | None = None) -> dict:
    hit = {"line": i + 1, "keyword": keywords[0], "keywords": keywords,
           "text": (lines[i] if text is None else text).strip()}
    if context:
        lo, hi = max(0, i - context), min(len(lines), i + context + 1)
        hit["context"] = [{"line": j + 1, "text": lines[j].strip()} for j in range(lo, hi) if j != i]
    return hit


def _find_hits(lines: list[str], wanted: list[tuple[str, str]], context: int) -> list[dict]:
    """줄 목록에서 줄 단위 히트 (한 줄에 키워드가 여러 개면 1건)."""
    hits = []
    for i, line in enumerate(lines):
        found = _keywords_in(line, wanted)
        if found:
            hits.append(_hit(lines, i, found, context))
    return hits


def _add_candidate(cands: dict[int, dict], line: int, readings: list[dict], lines: list[str] | None,
                   context: int) -> None:
    """줄 단위 후보. 같은 줄의 해석은 한 항목으로 모은다 (해석별 본문·키워드 보존)."""
    entry = cands.setdefault(line, {"line": line, "readings": []})
    seen = {r["encoding"] for r in entry["readings"]}
    entry["readings"] += [r for r in readings if r["encoding"] not in seen]
    if context and lines is not None and "context" not in entry and line <= len(lines):
        i = line - 1
        lo, hi = max(0, i - context), min(len(lines), i + context + 1)
        entry["context"] = [{"line": j + 1, "text": lines[j].strip()} for j in range(lo, hi) if j != i]


def _finish_candidates(cands: dict[int, dict]) -> list[dict]:
    out = []
    for line in sorted(cands):
        entry = cands[line]
        if not any(r["keywords"] for r in entry["readings"]):
            continue
        first = next(r for r in entry["readings"] if r["keywords"])
        union: list[str] = []
        for r in entry["readings"]:
            union += [k for k in r["keywords"] if k not in union and k not in entry.get("confirmed", [])]
        entry.update({"encoding": first["encoding"], "text": first["text"], "keywords": union,
                      "keyword": union[0] if union else first["keywords"][0]})
        out.append(entry)
    return out


def _scan_records(data: bytes, wanted: list[tuple[str, str]], context: int, choose, alt_codecs: tuple) -> dict:
    """바이트 줄마다 해석을 정하고 확정 히트와 줄 단위 후보를 나눈다."""
    lines: list[str] = []
    kinds: list[str] = []
    records = []
    for raw in split_records(data):
        codec, text, extra = choose(raw)
        sub = (text.splitlines() or [""]) if text is not None else [""]
        records.append((raw, codec, text, extra, len(lines), len(sub)))
        lines += sub
        kinds += [codec or "undecodable"] * len(sub)
    hits: list[dict] = []
    cands: dict[int, dict] = {}
    plausible: set[str] = set()
    for raw, codec, text, extra, first, count in records:
        alts = dict(extra)
        if alt_codecs and not raw.isascii():
            for alt in alt_codecs:
                if alt != codec and alt not in alts:
                    reading = _try(raw, alt)
                    if reading is not None:
                        alts[alt] = reading
        if not any(_keywords_in(t, wanted) for t in [text or "", *alts.values()]):
            continue
        readings = {} if text is None else {codec: text}
        for alt, reading in alts.items():
            if reading not in readings.values() and (alt in extra or coherent(alt, reading)):
                readings[alt] = reading
        own = lines[first:first + count]
        if text is not None and len(readings) == 1:
            for j, line in enumerate(own):
                found = _keywords_in(line, wanted)
                if found:
                    hits.append(_hit(lines, first + j, found, context))
            continue
        if not readings:
            continue
        plausible.update(readings)
        confirmed: list[str] = []
        if text is not None:
            quote = masked_quote(raw, readings, codec)
            confirmed = _keywords_in(quote, wanted)
            if confirmed:
                j = next((j for j, line in enumerate(own) if _keywords_in(line, wanted)), 0)
                hit = _hit(lines, first + j, confirmed, context, quote)
                hit["quote_basis"] = "codec_independent"
                hit["readings"] = [{"encoding": c, "text": t.strip()} for c, t in readings.items()]
                hits.append(hit)
        per = [{"encoding": c, "text": t.strip(), "keywords": [k for k in _keywords_in(t, wanted)
                                                                if k not in confirmed]}
               for c, t in readings.items()]
        if any(r["keywords"] for r in per):
            _add_candidate(cands, first + 1, per, lines, context)
            cands[first + 1]["confirmed"] = confirmed
    return {"lines": lines, "kinds": kinds, "hits": hits, "cands": cands, "plausible": plausible}


def _label(kinds: list[str], basis: str) -> tuple[str | None, str, list[dict] | None, list[list[int]]]:
    """줄별 코덱에서 파일 코덱 표기·근거·구간·검색하지 못한 줄 구간."""
    used = []
    for k in kinds:
        if k not in ("ascii", "undecodable") and k not in used:
            used.append(k)
    bad = [i + 1 for i, k in enumerate(kinds) if k == "undecodable"]
    if not used and bad:
        return None, basis, None, _runs(bad)
    if not used:
        return "utf-8", basis, None, []
    if len(used) == 1 and not bad:
        return used[0], basis, None, []
    segments: list[dict] = []
    for i, k in enumerate(kinds):
        if k == "ascii":
            continue
        if segments and segments[-1]["encoding"] == k and segments[-1]["last_line"] == i:
            segments[-1]["last_line"] = i + 1
        else:
            segments.append({"first_line": i + 1, "last_line": i + 1, "encoding": k})
    return ("mixed" if len(used) > 1 else used[0]), ("per_line" if basis == "strict" else basis), segments, _runs(bad)


def not_text_reason(text: str) -> str | None:
    """디코딩된 문자열을 텍스트로 볼 수 없는 근거 (제어 문자 비율). 텍스트면 None."""
    controls = len(_CONTROL_RE.findall(text))
    if text and controls > len(text) * NOT_TEXT_RATIO:
        return f"제어 문자 {controls}개 / {len(text)}자 — 텍스트가 아닌 바이너리 기록으로 판단"
    return None


def search_file_result(path: Path, keywords: list[str], context: int,
                       encoding: str | None = None) -> dict:
    """{'hits', 'error', 'encoding', 'basis', 'undecodable', 'not_text', 'segments', 'unsearched_lines',
    'uncertain', 'conflict'}.

    읽기 실패(error)·인코딩 판정 불가(undecodable)·텍스트 아님(not_text)은 '히트 없음'이 아니라
    검색 불가다. unsearched_lines는 해석하지 못해 검색하지 않은 줄 구간, segments는 줄 단위로 판정한
    코덱 구간이다. hits는 확정 히트(줄마다 1건, keywords에 그 줄의 키워드 전부)다.
    uncertain은 코덱을 정할 수 없는 근거다: {'reason', 'candidates', 'lines', 'candidate_hits'}.
    lines가 None이면 파일 전체가 불확실하다. candidate_hits는 줄마다 1건이고 readings에 해석별
    본문과 그 해석에서 보이는 키워드를 남긴다. 확정 히트 수에 넣지 않는다.
    encoding은 운영자 지정 코덱이다(basis='explicit'). 파일 표지와 다르면 conflict에 기록한다.
    """
    empty = {"hits": [], "error": None, "encoding": None, "basis": None, "undecodable": None,
             "not_text": None, "segments": None, "unsearched_lines": [], "uncertain": None, "conflict": None}
    try:
        data = path.read_bytes()
    except OSError as e:
        return {**empty, "error": str(e)}
    wanted = [(kw, unicodedata.normalize("NFC", kw)) for kw in keywords]
    marker = file_marker(data)
    reasons: list[str] = []
    candidates: list[str] = []
    cands: dict[int, dict] = {}
    whole_file = False  # 파일 전체의 코덱이 불확실하면 선택한 해석의 히트도 후보다
    conflict = None
    segments, unsearched, kinds = None, [], None
    if encoding:
        name = codecs.lookup(encoding).name
        if marker is not None or not _ascii_compatible(name):
            if marker is not None and name not in marker["accepts"]:
                conflict = {"requested": encoding, "marker": marker["kind"], "marker_label": marker["label"],
                            "marker_encoding": marker["codec"]}
            # 지정 코덱이 표지와 같은 뜻이면 표지 코덱으로 읽는다(UTF-8 BOM을 본문 글자로 남기지 않는다).
            text = _try(data, marker["codec"] if marker is not None and not conflict else name)
            if text is None:
                why = f"지정한 인코딩 '{encoding}'로 해석되지 않음"
                if conflict:
                    why += f" (파일 표지 {marker['label']}와 다름)"
                return {**empty, "undecodable": why, "conflict": conflict}
            codec, basis, lines = name, "explicit", text.splitlines()
            hits = _find_hits(lines, wanted, context)
            if conflict:
                whole_file = True
                reasons.append(f"지정 코덱 {encoding}이 파일 표지({marker['label']})와 다름 — 지정 코덱으로 읽었으나 "
                               "어느 해석이 맞는지 확정하지 않음")
                other = _try(data, marker["codec"]) if marker["codec"] else None
                if other is not None:
                    candidates.append(marker["codec"])
                    other_lines = other.splitlines()
                    for h in _find_hits(other_lines, wanted, 0):
                        _add_candidate(cands, h["line"], [{"encoding": marker["codec"], "text": h["text"],
                                                          "keywords": h["keywords"]}], lines, context)
        else:
            scan = _scan_records(data, wanted, context, _explicit_chooser(name), ())
            lines, kinds, hits, cands = scan["lines"], scan["kinds"], scan["hits"], scan["cands"]
            codec, basis, segments, unsearched = _label(kinds, "explicit")
            if codec is None:
                return {**empty, "undecodable": f"지정한 인코딩 '{encoding}'로 해석되지 않음"}
            if cands:
                candidates += [c for c in sorted(scan["plausible"]) if c != codec]
                reasons.append(f"지정 코덱 {encoding}과 엄격한 UTF-8로 모두 읽히는 줄(UTF-8 다바이트 글자 포함) — "
                               "어느 해석인지 바이트만으로 판정 불가")
    elif marker is not None or b"\x00" in data:
        text, codec, basis = decode_text(data)
        lines = text.splitlines() if text is not None else []
        hits = _find_hits(lines, wanted, context) if text is not None else []
    else:
        scan = _scan_records(data, wanted, context, _choose_auto, ALT_CODECS)
        lines, kinds, hits, cands = scan["lines"], scan["kinds"], scan["hits"], scan["cands"]
        codec, basis, segments, unsearched = _label(kinds, "strict")
        text = "\n".join(lines) if codec is not None else None
        if codec is None:
            basis = UNDECODABLE_FALLBACK
        if cands:
            candidates += [c for c in sorted(scan["plausible"]) if c != codec]
            reasons.append("선언 없는 바이트가 여러 코덱으로 자연스럽게 읽히고 해석에 따라 키워드가 보이거나 "
                           "보이지 않음 — 어느 코덱인지 바이트만으로 판정 불가")
    if not encoding:
        # 파일 전체가 한 코덱으로 엄격히 읽히지 않거나, 엄격한 CP949 해석이 확장 완성형(UHC 확장 영역)에
        # 기대면 원래 한국어 텍스트라는 근거가 약하다 — BOM 없는 UTF-16 후보도 본다.
        whole_failed = codec is None or basis == "per_line"
        weak_cp949 = codec == "cp949" and basis == "strict" and uhc_extension(data)
        if (whole_failed or weak_cp949) and not any(data.startswith(b) for b, _ in BOMS):
            found = utf16_candidates(data)
            if found:
                whole_file = True
                reasons.append("BOM 없는 UTF-16으로도 읽히나 바이트 순서(LE/BE)·코덱을 정할 근거가 없음")
                candidates += [c for c, _ in found]
                for c, t in found:
                    for h in _find_hits(t.splitlines(), wanted, 0):
                        _add_candidate(cands, h["line"], [{"encoding": c, "text": h["text"],
                                                          "keywords": h["keywords"]}], None, 0)
    lines_text = "\n".join(lines)
    reason = not_text_reason(lines_text) if codec is not None else None
    confirmed: list[dict] = []
    if codec is not None and not reason:
        for h in hits:
            if whole_file:
                line_codec = kinds[h["line"] - 1] if kinds is not None else codec
                _add_candidate(cands, h["line"], [{"encoding": line_codec, "text": h["text"],
                                                  "keywords": h["keywords"]}], None, 0)
                cands[h["line"]].setdefault("context", h.get("context", []))
            else:
                confirmed.append(h)
    candidate_hits = _finish_candidates(cands)
    for c in candidate_hits:
        c.pop("confirmed", None)
        if not c.get("context"):
            c.pop("context", None)
    uncertain = None
    if reasons and (candidate_hits or whole_file):
        uncertain = {"reason": "; ".join(reasons),
                     "candidates": ([codec] if codec is not None else []) + [c for c in dict.fromkeys(candidates)
                                                                            if c != codec],
                     "lines": None if whole_file else _runs([c["line"] for c in candidate_hits]),
                     "candidate_hits": candidate_hits}
    if codec is None:
        return {**empty, "undecodable": basis, "uncertain": uncertain}
    if reason:
        return {**empty, "encoding": codec, "basis": basis, "not_text": reason, "uncertain": uncertain,
                "conflict": conflict}
    return {**empty, "hits": confirmed, "encoding": codec, "basis": basis, "segments": segments,
            "unsearched_lines": unsearched, "uncertain": uncertain, "conflict": conflict}


def search_file(path: Path, keywords: list[str], context: int) -> list[dict]:
    result = search_file_result(path, keywords, context)
    if result["error"] is not None:
        raise OSError(result["error"])
    if result["undecodable"] is not None:
        raise ValueError(result["undecodable"])
    if result.get("not_text") is not None:
        raise ValueError(result["not_text"])
    return result["hits"]


def search_files(files: list[Path], keywords: list[str], context: int,
                 encoding: str | None = None) -> dict:
    """텍스트 확장자 파일을 검색하고 검색 범위를 분류한다 (CLI·선조사 공통).

    searched만 검색 분모다. read_errors·undecodable·not_text는 검색 불가이고,
    partial은 일부 줄을 해석하지 못해 그 구간을 검색하지 못한 파일이다.
    uncertain은 코덱을 바이트만으로 정하지 못한 파일(검색했어도 결과가 확정되지 않음)이고,
    줄 단위 후보를 candidate_hits로 남긴다 — 확정하려면 encoding을 지정해 다시 검색한다.
    conflicts는 지정 코덱이 파일 표지(BOM·선언)와 다른 파일이다.
    """
    out: dict = {"searched": [], "read_errors": [], "undecodable": [], "not_text": [],
                 "partial": [], "mixed": [], "uncertain": [], "conflicts": [], "encodings": {}, "results": []}
    for f in files:
        res = search_file_result(f, keywords, context, encoding)
        if res.get("uncertain"):
            out["uncertain"].append({"file": str(f), "encoding": res.get("encoding"), **res["uncertain"]})
        if res.get("conflict"):
            out["conflicts"].append({"file": str(f), **res["conflict"]})
        if res["error"] is not None:
            out["read_errors"].append({"file": str(f), "error": res["error"][:200]})
            continue
        if res["undecodable"] is not None:
            out["undecodable"].append({"file": str(f), "reason": res["undecodable"]})
            continue
        if res.get("not_text") is not None:
            out["not_text"].append({"file": str(f), "encoding": res["encoding"], "reason": res["not_text"]})
            continue
        out["searched"].append(str(f))
        out["encodings"][res["encoding"]] = out["encodings"].get(res["encoding"], 0) + 1
        if res.get("unsearched_lines"):
            out["partial"].append({"file": str(f), "unsearched_lines": res["unsearched_lines"],
                                   "segments": res["segments"]})
        if res["encoding"] == "mixed":
            out["mixed"].append({"file": str(f), "segments": res["segments"]})
        if res["hits"]:
            row = {"file": str(f), "sha256": sha256_file(f), "encoding": res["encoding"],
                   "encoding_basis": res["basis"], "hit_count": len(res["hits"]), "hits": res["hits"]}
            if res.get("segments"):
                row["encoding_segments"] = res["segments"]
            if res.get("unsearched_lines"):
                row["unsearched_lines"] = res["unsearched_lines"]
            out["results"].append(row)
    out["status"] = ("partial" if out["read_errors"] or out["undecodable"] or out["not_text"] or out["partial"]
                     or out["uncertain"] or out["conflicts"] else "complete")
    return out


def scope_note(result: dict) -> str:
    """검색 결과 요약에 붙일 검색 범위 제한 문구 (보고서·선조사 공통). 없으면 빈 문자열."""
    parts = []
    if result.get("files_not_text"):
        parts.append(f"텍스트 아님(바이너리 기록) {result['files_not_text']}개")
    if result.get("files_partially_searched"):
        parts.append(f"일부 줄 해석 불가(그 구간 검색 안 함) {result['files_partially_searched']}개")
    if result.get("files_mixed_encoding"):
        parts.append(f"줄마다 인코딩이 다른 파일 {result['files_mixed_encoding']}개(줄 단위 해석)")
    if result.get("files_encoding_conflict"):
        parts.append(f"지정 코덱과 파일 표지(BOM·선언) 충돌 {result['files_encoding_conflict']}개(지정 코덱으로 "
                     "읽었으나 그 파일의 히트·미검출은 미확정)")
    if result.get("files_encoding_uncertain"):
        codecs_ = ", ".join(result.get("plausible_codecs") or [])
        parts.append(f"인코딩 불확실 {result['files_encoding_uncertain']}개(후보 줄 "
                     f"{result.get('candidate_hits', 0)}개는 미확정"
                     + (f", 가능한 코덱 {codecs_}" if codecs_ else "")
                     + " — 작성 코덱을 확인해 지정하고 재검색 필요)")
    return "".join(", " + p for p in parts)


def uncertain_summary(found: dict) -> dict:
    """search_files 결과에서 보고서·선조사가 함께 쓰는 인코딩 불확실·충돌 필드."""
    plausible: list[str] = []
    for u in found["uncertain"]:
        plausible += [c for c in u["candidates"] if c not in plausible]
    return {"files_encoding_uncertain": len(found["uncertain"]),
            "candidate_hits": sum(len(u["candidate_hits"]) for u in found["uncertain"]),
            "plausible_codecs": plausible,
            "encoding_uncertain": found["uncertain"][:50],
            "files_encoding_conflict": len(found.get("conflicts", [])),
            "encoding_conflicts": found.get("conflicts", [])[:50]}


def _command_line(args: list[str]) -> str:
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def rerun_commands(result: dict, target: str, keywords: list[str], command: str = "kw") -> list[str]:
    """인코딩 불확실로 끝난 검색을 코덱을 지정해 다시 돌리는 정확한 명령 (가능한 코덱마다 하나)."""
    codecs_ = [c for c in result.get("plausible_codecs") or [] if c not in ("ascii", "mixed")]
    if not codecs_ or not result.get("files_encoding_uncertain"):
        return []
    if command == "report":
        base = ["lazyforensic", "report", target, "--keywords", ",".join(keywords), "--keyword-encoding"]
    else:
        base = ["lazyforensic", "kw", target, *keywords, "--encoding"]
    return [_command_line(base + [c]) for c in codecs_]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="증거형 키워드 검색 리포트 (로컬 전용)")
    ap.add_argument("target", help="검색 대상 파일 또는 디렉터리")
    ap.add_argument("keywords", nargs="+", help="검색할 키워드(리터럴)")
    ap.add_argument("--context", type=int, default=0, help="히트 전후 맥락 줄 수")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("-o", "--output", default=None, help="리포트 출력 경로")
    ap.add_argument("--encoding", "--keyword-encoding", dest="encoding", default=None,
                    help="운영자가 확인한 코덱(예: cp949, shift_jis). BOM·선언 없는 줄을 이 코덱으로 읽는다. "
                         f"지정하지 않으면 환경변수 {ENCODING_ENV}를 쓴다")
    args = ap.parse_args(argv)
    try:
        encoding, source = resolve_encoding(args.encoding)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    target = Path(args.target)
    if target.is_dir():
        files = sorted(p for p in target.rglob("*") if p.is_file())
    elif target.is_file():
        files = [target]
    else:
        print(f"대상을 찾을 수 없습니다: {target}", file=sys.stderr)
        return 2

    text_files = []
    skipped = []
    for f in files:
        (text_files if f.suffix.lower() in TEXT_EXTS else skipped).append(f)
    found = search_files(text_files, args.keywords, args.context, encoding)
    skipped = [str(f) for f in skipped] + [e["file"] for e in found["read_errors"]]
    results = found["results"]
    report = {
        "keywords": args.keywords,
        "scope": str(target),
        "encoding_requested": encoding,
        "encoding_source": source,
        "status": found["status"],
        "files_searched": len(found["searched"]),
        "files_skipped_binary": len(skipped),
        "files_read_failed": len(found["read_errors"]),
        "read_errors": found["read_errors"][:50],
        "files_undecodable": len(found["undecodable"]),
        "undecodable": found["undecodable"][:50],
        "files_not_text": len(found["not_text"]),
        "not_text": found["not_text"][:50],
        "files_partially_searched": len(found["partial"]),
        "partially_searched": found["partial"][:50],
        "files_mixed_encoding": len(found["mixed"]),
        "encodings": found["encodings"],
        **uncertain_summary(found),
        "files_with_hits": len(results),
        "total_hits": sum(r["hit_count"] for r in results),
        "results": results,
        "skipped": skipped[:50],
    }
    if encoding:
        report["operator_codec"] = operator_codec_note(encoding, source)
    report["rerun_commands"] = rerun_commands(report, str(target), args.keywords)

    if args.json or args.output:
        out = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
            print(f"→ {args.output}")
        if args.json:
            print_json(report, indent=2)
    else:
        if encoding:
            print(report["operator_codec"])
        if results:
            for r in results:
                print(f"{r['file']}  (sha256:{r['sha256'][:16]}…)  {r['hit_count']}히트")
                for h in r["hits"]:
                    print(f"    L{h['line']}  [{', '.join(h['keywords'])}]  {h['text'][:120]}")
        print(f"\n검색 {report['files_searched']}개 파일(바이너리/읽기실패 {report['files_skipped_binary']}개 제외), "
              f"히트 {report['total_hits']}건 / {report['files_with_hits']}개 파일")
        if report["files_read_failed"]:
            print(f"주의: 읽기 실패 {report['files_read_failed']}개 파일 — '미검출'이 아니라 '검색 불가'다", file=sys.stderr)
        if report["files_undecodable"]:
            print(f"주의: 인코딩 판정 불가 {report['files_undecodable']}개 파일 — '미검출'이 아니라 '검색 불가'다",
                  file=sys.stderr)
        if report["files_not_text"]:
            print(f"주의: 텍스트 확장자지만 텍스트가 아닌 파일 {report['files_not_text']}개 — 텍스트 검색 수에서 제외",
                  file=sys.stderr)
        if report["files_partially_searched"]:
            print(f"주의: 일부 줄을 해석하지 못한 파일 {report['files_partially_searched']}개 — 그 구간은 검색 불가",
                  file=sys.stderr)
        if report["files_mixed_encoding"]:
            print(f"참고: 줄마다 인코딩이 다른 파일 {report['files_mixed_encoding']}개 — 줄 단위로 해석해 검색",
                  file=sys.stderr)
        for c in report["encoding_conflicts"]:
            print(f"주의: 지정 코덱 {c['requested']}이 파일 표지({c['marker_label']})와 다름 {c['file']} — "
                  "지정 코덱으로 읽었으나 히트·미검출 미확정", file=sys.stderr)
        for u in report["encoding_uncertain"]:
            print(f"주의: 인코딩 불확실 {u['file']} — {u['reason']} (가능한 코덱 {', '.join(u['candidates'])})",
                  file=sys.stderr)
            for h in u["candidate_hits"]:
                for r in h["readings"]:
                    print(f"    후보 L{h['line']} {r['encoding']}  [{', '.join(r['keywords']) or '-'}]  "
                          f"{r['text'][:120]} (미확정)", file=sys.stderr)
        if report["files_encoding_uncertain"]:
            lines = report["candidate_hits"]
            print(f"인코딩 불확실 파일 {report['files_encoding_uncertain']}개·후보 줄 {lines}개 "
                  f"(가능한 코덱: {', '.join(report['plausible_codecs'])}). 작성 코덱을 확인한 뒤 다시 검색:",
                  file=sys.stderr)
            for cmd in report["rerun_commands"]:
                print(f"    {cmd}", file=sys.stderr)
        if report["total_hits"] == 0:
            print("결과: 전수 검색 범위 내 미검출" if report["status"] == "complete"
                  else "결과: 검색한 파일에서 미검출 (검색 불가·미확정 파일이 있어 전수 검색 결과 아님)")

    return 1 if report["total_hits"] == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
