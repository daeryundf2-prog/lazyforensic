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

알려진 한계:
- 리터럴 문자열 검색이다. OCR 오류·유사표현·이미지 속 글자는
  잡지 못한다. 유의어 확장 검색은 이 도구의 역할이 아니다.
- 바이너리 파일은 건너뛴다 (읽기 실패 파일은 skipped로 기록).
- 인코딩은 BOM → 파일 안의 선언(encoding=/charset=) → 엄격한 UTF-8 →
  엄격한 CP949 순으로 정한다. 어느 것으로도 오류 없이 해석되지 않으면
  '인코딩 판정 불가'로 따로 세고 검색한 파일 수에 넣지 않는다.
  BOM 없는 UTF-16은 ASCII 글자의 NUL 위치(LE: 글자 뒤, BE: 글자 앞)로만 정한다.
  그 근거가 없으면(한글만 든 UTF-16 등) 정하지 않고 LE·BE 후보로 다시 검색해
  '인코딩 불확실'로 따로 센다.
- CP949로 읽은 줄이 Shift-JIS로도 자연스럽게 읽히면(반각 가타카나·미할당 문자 없음)
  어느 쪽인지 판정할 수 없다. 그 줄은 '인코딩 불확실'로 남기고 Shift-JIS로도 검색한다.
  후보 코덱의 히트는 확정 히트 수에 넣지 않고 candidate_hits로 따로 적는다.
  --encoding으로 코덱을 명시하면 그 코덱으로만 읽는다.
- 선언 없이 고른 코덱(UTF-8·CP949)의 줄이 다른 코덱(UTF-8·CP949·EUC-JP·Big5·Shift-JIS)으로도 자연스럽게
  읽히고 그 해석에서만 키워드가 보이면(예: CP949 '캡처'는 엄격한 UTF-8로도, EUC-JP '東京'은 CP949로도 읽힌다)
  그 줄은 '인코딩 불확실'이다. 불확실한 줄의 히트는 어느 해석이든 확정 히트에 넣지 않고 후보로 적는다.
- 파일 전체가 UTF-8이 아니면 줄마다 UTF-8 → CP949로 다시 판정한다. 줄마다 코덱이 다르면
  'mixed'로 표시하고 코덱 구간을 남긴다. 해석하지 못한 줄은 검색하지 않은 구간으로 적고
  그 파일은 부분 검색으로 센다. 한 줄 안에서 코덱이 섞인 경우는 구별하지 못한다.
- 디코딩에 성공해도 제어 문자 비율이 1%를 넘으면 텍스트가 아닌 기록으로 보고
  텍스트 검색 수에서 빼 따로 센다.
- 줄과 키워드를 NFC로 맞춘 뒤 비교한다 (macOS 등의 NFD 한글 대응).
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import re
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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


BOMS = (  # UTF-32 LE BOM이 UTF-16 LE BOM으로 시작하므로 긴 것부터 본다
    (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"),
)
# 선언은 XML 머리말과 HTML meta에서만 읽는다 (소스 코드 속 'encoding=' 문자열은 선언이 아니다).
DECLARED_RE = re.compile(
    rb"""^\s*<\?xml[^>]*?\sencoding\s*=\s*["']([A-Za-z][A-Za-z0-9_.:-]*)"""
    rb"""|<meta\s[^>]*?charset\s*=\s*["']?([A-Za-z][A-Za-z0-9_.:-]*)""", re.IGNORECASE)

_PRINTABLE_ASCII = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}
_HALFWIDTH_KANA_RE = re.compile(r"[\uff61-\uff9f]")


def _ascii_units(data: bytes, codec: str) -> int:
    """UTF-16 바이트 순서 codec으로 읽었을 때 ASCII 글자(상위 바이트 0)가 되는 2바이트 단위 수."""
    pairs = zip(data[0::2], data[1::2])
    if codec == "utf-16-le":
        return sum(1 for lo, hi in pairs if hi == 0 and lo in _PRINTABLE_ASCII)
    return sum(1 for hi, lo in pairs if hi == 0 and lo in _PRINTABLE_ASCII)


def _plausible(text: str) -> bool:
    """다른 코덱으로 읽은 결과가 텍스트로 자연스러운가: 미할당·사용자 정의·대리 문자,
    반각 가타카나(한국어 CP949 바이트를 Shift-JIS로 읽으면 생기는 글자), 제어 문자 과다가 없다."""
    if not text or _HALFWIDTH_KANA_RE.search(text) or not_text_reason(text):
        return False
    return all(unicodedata.category(ch) not in ("Cn", "Co", "Cs") for ch in text)


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


def shift_jis_lines(data: bytes) -> dict[int, str]:
    """CP949로 읽히는 줄 중 Shift-JIS로도 자연스럽게 읽히는 줄 {줄 번호: Shift-JIS 본문}.

    엄격한 UTF-8로 읽히는 줄은 제외한다(UTF-8 다바이트 구조는 우연히 맞기 어렵다)."""
    out = {}
    for n, raw in enumerate(data.split(b"\n"), start=1):
        if raw.isascii():
            continue
        try:
            raw.decode("utf-8")
            continue
        except UnicodeDecodeError:
            pass
        try:
            korean = raw.decode("cp949")
            japanese = raw.decode("shift_jis")
        except UnicodeDecodeError:
            continue
        if japanese != korean and _plausible(japanese.rstrip("\r")):
            out[n] = japanese
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


def decode_text(data: bytes) -> tuple[str | None, str | None, str]:
    """(본문, 코덱, 판정 근거)를 돌려준다. 본문이 None이면 근거는 판정 불가 사유다.

    치환 문자로 읽어 넘기지 않는다 — 잘못 읽은 파일의 '미검출'은 거짓 음성이 된다.
    """
    for bom, codec in BOMS:
        if data.startswith(bom):
            try:
                return data.decode(codec), codec, "bom"
            except UnicodeDecodeError:
                return None, None, f"{codec} BOM이 있으나 내용이 {codec}로 해석되지 않음"
    if b"\x00" in data:
        # NUL 위치만으로는 '한글' 같은 비ASCII UTF-16을 판정할 수 없다(LE와 BE 모두 NUL이 생긴다).
        # ASCII 글자(LE: 글자+NUL, BE: NUL+글자)가 한쪽 바이트 순서로만 보일 때만 정한다.
        if len(data) % 2 == 0:
            le, be = _ascii_units(data, "utf-16-le"), _ascii_units(data, "utf-16-be")
            codec = "utf-16-le" if le and not be else "utf-16-be" if be and not le else None
            if codec:
                try:
                    return data.decode(codec), codec, "nul_pattern"
                except UnicodeDecodeError:
                    pass
        return None, None, "NUL 바이트가 있어 인코딩을 판정할 수 없음"
    declared = DECLARED_RE.search(data[:1024])
    if declared:
        label = (declared.group(1) or declared.group(2)).decode("ascii").lower()
        try:
            codec = codecs.lookup(label).name
        except LookupError:
            return None, None, f"선언된 인코딩 '{label}'을 알 수 없음"
        # 웹 표준처럼 EUC-KR 표기는 그 상위 집합인 CP949로 읽는다.
        codec = "cp949" if codec == "euc_kr" else codec
        try:
            return data.decode(codec), codec, "declared"
        except UnicodeDecodeError:
            return None, None, f"선언된 인코딩 '{label}'로 해석되지 않음"
    for codec in ("utf-8", "cp949"):
        try:
            return data.decode(codec), codec, "strict"
        except UnicodeDecodeError:
            continue
    return None, None, "UTF-8·CP949 어느 쪽으로도 해석되지 않음"


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


def decode_lines(data: bytes) -> dict | None:
    """줄마다 엄격한 UTF-8 → CP949 순으로 해석한다 (BOM·NUL·선언이 없는 파일 전용).

    한 파일에 UTF-8 줄과 CP949 줄이 섞이면 파일 전체 CP949 해석은 UTF-8 한글을 다른
    글자로 바꿔 '미검출'을 만든다. 줄 단위 판정 결과 코덱이 섞였거나 해석하지 못한 줄이
    있으면 {'lines', 'encoding', 'segments', 'unsearched_lines'}를 돌려준다.
    모든 줄이 한 코덱으로 해석되면 None (파일 단위 판정을 그대로 쓴다).
    """
    lines: list[str] = []
    kinds: list[str] = []
    for raw in data.split(b"\n"):
        if raw.isascii():
            lines.append(raw.decode("ascii"))
            kinds.append("ascii")
            continue
        for codec in ("utf-8", "cp949"):
            try:
                lines.append(raw.decode(codec))
                kinds.append(codec)
                break
            except UnicodeDecodeError:
                continue
        else:
            lines.append("")
            kinds.append("undecodable")
    used = {k for k in kinds if k not in ("ascii", "undecodable")}
    bad = [i + 1 for i, k in enumerate(kinds) if k == "undecodable"]
    if not used or (not bad and len(used) == 1):
        return None
    segments: list[dict] = []
    for i, k in enumerate(kinds):
        if k == "ascii":
            continue
        if segments and segments[-1]["encoding"] == k and segments[-1]["last_line"] == i:
            segments[-1]["last_line"] = i + 1
        else:
            segments.append({"first_line": i + 1, "last_line": i + 1, "encoding": k})
    return {"lines": lines, "encoding": "mixed" if len(used) > 1 else used.pop(),
            "segments": segments, "unsearched_lines": _runs(bad), "kinds": kinds}


# 선언 없는 바이트를 다른 코덱으로 읽은 해석. 짧은 한국어 CP949('캡처')는 엄격한 UTF-8로도,
# EUC-JP·Big5의 한자 몇 글자는 CP949로도 오류 없이 읽힌다 — 바이트만으로 원래 코덱을 정할 수 없다.
ALT_CODECS = ("utf-8", "cp949", "euc_jp", "big5", "shift_jis")
UNDECODABLE_FALLBACK = "UTF-8·CP949 어느 쪽으로도 해석되지 않음"


def _keywords_in(text: str, keywords: list[str]) -> set[str]:
    normalized = unicodedata.normalize("NFC", text)
    return {kw for kw in keywords if unicodedata.normalize("NFC", kw) in normalized}


def alternative_readings(raw_lines: list[bytes], chosen: list[tuple[str | None, str | None]],
                         keywords: list[str], skip: dict[int, str]) -> dict[str, dict[int, str]]:
    """{코덱: {줄 번호: 본문}} — 선택한 해석에 없는 키워드가 다른 코덱의 자연스러운 해석에서만 보이는 줄.

    chosen은 줄마다 (선택 코덱, 본문)이고 해석하지 못한 줄은 (None, None)이다. 그런 줄은 어느 해석의
    히트도 확정할 수 없다. skip은 이미 Shift-JIS 후보로 잡은 줄이다(중복 후보를 만들지 않는다)."""
    out: dict[str, dict[int, str]] = {}
    for n, (raw, (codec, text)) in enumerate(zip(raw_lines, chosen), start=1):
        if raw.isascii():
            continue
        seen = _keywords_in(text, keywords) if text is not None else set()
        for alt in ALT_CODECS:
            if alt == codec or (alt == "shift_jis" and n in skip):
                continue
            try:
                reading = raw.decode(alt).rstrip("\r")
            except UnicodeDecodeError:
                continue
            if reading == (text or "").rstrip("\r") or not _plausible(reading):
                continue
            if _keywords_in(reading, keywords) - seen:
                out.setdefault(alt, {})[n] = reading
    return out


def not_text_reason(text: str) -> str | None:
    """디코딩된 문자열을 텍스트로 볼 수 없는 근거 (제어 문자 비율). 텍스트면 None."""
    controls = len(_CONTROL_RE.findall(text))
    if text and controls > len(text) * NOT_TEXT_RATIO:
        return f"제어 문자 {controls}개 / {len(text)}자 — 텍스트가 아닌 바이너리 기록으로 판단"
    return None


def _find_hits(lines: list[str], keywords: list[str], context: int,
                only: set[int] | None = None) -> list[dict]:
    """줄 목록에서 키워드 히트. only가 있으면 그 줄 번호만 본다."""
    wanted = [(kw, unicodedata.normalize("NFC", kw)) for kw in keywords]
    hits = []
    for i, line in enumerate(lines):
        if only is not None and i + 1 not in only:
            continue
        normalized = unicodedata.normalize("NFC", line)
        for kw, nfc in wanted:
            if nfc in normalized:
                hit = {
                    "line": i + 1,
                    "keyword": kw,
                    "text": line.strip(),
                }
                if context:
                    lo = max(0, i - context)
                    hi = min(len(lines), i + context + 1)
                    hit["context"] = [
                        {"line": j + 1, "text": lines[j].strip()}
                        for j in range(lo, hi) if j != i
                    ]
                hits.append(hit)
                break  # 같은 줄에서 키워드 여러 개면 한 번만
    return hits


def search_file_result(path: Path, keywords: list[str], context: int,
                       encoding: str | None = None) -> dict:
    """{'hits', 'error', 'encoding', 'basis', 'undecodable', 'not_text', 'segments', 'unsearched_lines',
    'uncertain'}.

    읽기 실패(error)·인코딩 판정 불가(undecodable)·텍스트 아님(not_text)은 '히트 없음'이 아니라
    검색 불가다. 디코딩 성공은 텍스트라는 뜻이 아니다. unsearched_lines는 해석하지 못해
    검색하지 않은 줄 구간, segments는 줄 단위로 판정한 코덱 구간이다.
    uncertain은 바이트만으로 코덱을 정할 수 없는 근거다: {'reason', 'candidates', 'lines', 'candidate_hits'}.
    lines가 None이면 파일 전체가 불확실하다. candidate_hits는 후보 코덱으로 읽었을 때의 히트이고
    확정 히트(hits)에 넣지 않는다. encoding을 주면 그 코덱으로만 엄격히 읽는다(basis='explicit').
    """
    empty = {"hits": [], "error": None, "encoding": None, "basis": None, "undecodable": None,
             "not_text": None, "segments": None, "unsearched_lines": [], "uncertain": None}
    try:
        data = path.read_bytes()
    except OSError as e:
        return {**empty, "error": str(e)}
    if encoding:
        try:
            text, codec, basis = data.decode(encoding), codecs.lookup(encoding).name, "explicit"
        except UnicodeDecodeError as e:
            return {**empty, "undecodable": f"지정한 인코딩 '{encoding}'로 해석되지 않음: {e.reason}"}
    else:
        text, codec, basis = decode_text(data)
    segments, unsearched, uncertain, kinds = None, [], None, None
    if codec in ("cp949", None) and basis in ("strict", UNDECODABLE_FALLBACK):
        per_line = decode_lines(data)
        if per_line is not None:
            text = "\n".join(per_line["lines"])
            codec, basis, kinds = per_line["encoding"], "per_line", per_line["kinds"]
            segments, unsearched = per_line["segments"], per_line["unsearched_lines"]
    reasons, candidates, lines, candidate_hits = [], [], [], []
    # 엄격한 CP949 해석이 확장 완성형(UHC 확장 영역)에 기대면 원래 한국어 텍스트라는 근거가 약하다.
    weak_cp949 = codec == "cp949" and basis == "strict" and uhc_extension(data)
    if not encoding and (text is None or weak_cp949) and not any(data.startswith(bom) for bom, _ in BOMS):
        found = utf16_candidates(data)
        if found:
            reasons.append("BOM 없는 UTF-16으로도 읽히나 바이트 순서(LE/BE)·코덱을 정할 근거가 없음")
            candidates += [c for c, _ in found]
            lines = None
            candidate_hits += [{**h, "encoding": c} for c, t in found
                               for h in _find_hits(t.splitlines(), keywords, context)]
    japanese: dict[int, str] = {}
    if text is not None and basis in ("strict", "per_line") and codec in ("cp949", "mixed"):
        japanese = shift_jis_lines(data)
        if japanese:
            numbers = sorted(japanese)
            alt = [japanese.get(n, "") for n in range(1, numbers[-1] + 1)]
            reasons.append("CP949로 읽은 줄이 Shift-JIS로도 해석됨 — 어느 코덱인지 바이트만으로 판정 불가")
            candidates.append("shift_jis")
            candidate_hits += [{**h, "encoding": "shift_jis"} for h in
                               _find_hits(alt, keywords, context, set(numbers))]
    # 선언 없이 고른 코덱(파일 전체 엄격 해석 또는 줄 단위 해석)은 줄마다 다른 코덱 해석과 대조한다.
    # 다른 해석에서만 키워드가 보이는 줄은 코덱을 정할 수 없는 줄이다 — '미검출'로 확정하지 않는다.
    doubtful = set(japanese)
    fallback = not encoding and basis in ("strict", "per_line", UNDECODABLE_FALLBACK)
    if fallback and codec not in ("utf-16-le", "utf-16-be"):
        raw_lines = data.split(b"\n")
        if kinds is not None:
            chosen = [(None, None) if k == "undecodable" else (k, t) for k, t in zip(kinds, text.split("\n"))]
        elif text is not None:
            chosen = [(codec, t) for t in text.split("\n")]
        else:
            chosen = [(None, None)] * len(raw_lines)
        for alt, readings in alternative_readings(raw_lines, chosen, keywords, japanese).items():
            numbers = sorted(readings)
            doubtful.update(numbers)
            candidates.append(alt)
            sparse = [readings.get(n, "") for n in range(1, numbers[-1] + 1)]
            candidate_hits += [{**h, "encoding": alt} for h in _find_hits(sparse, keywords, context, set(numbers))]
        if len(doubtful) > len(japanese):
            reasons.append("선언 없는 바이트가 다른 코덱으로도 자연스럽게 읽히고 그 해석에서만 키워드가 보임 — "
                           "어느 코덱인지 바이트만으로 판정 불가")
    if lines is not None:
        lines = _runs(sorted(doubtful))
    hits: list[dict] = []
    if text is not None and not not_text_reason(text):
        # 줄 번호는 바이트의 줄바꿈(\n) 기준 — 다른 코덱 해석과 같은 줄을 가리켜야 한다.
        split = text.split("\n") if fallback else text.splitlines()
        for h in _find_hits(split, keywords, context):
            if lines is None or h["line"] in doubtful:
                # 코덱을 정할 수 없는 줄은 선택한 코덱의 히트도 확정 히트가 아니다.
                line_codec = kinds[h["line"] - 1] if kinds is not None else codec
                candidate_hits.append({**h, "encoding": line_codec})
            else:
                hits.append(h)
    uncertain = None
    if reasons:
        uncertain = {"reason": "; ".join(reasons),
                     "candidates": ([codec] if text is not None else []) + candidates,
                     "lines": lines, "candidate_hits": candidate_hits}
    if text is None:
        return {**empty, "undecodable": basis, "uncertain": uncertain}
    reason = not_text_reason(text)
    if reason:
        return {**empty, "encoding": codec, "basis": basis, "not_text": reason, "uncertain": uncertain}
    return {**empty, "hits": hits, "encoding": codec,
            "basis": basis, "segments": segments, "unsearched_lines": unsearched, "uncertain": uncertain}


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
    후보 코덱으로 읽었을 때의 히트를 candidate_hits로 남긴다 — 확정하려면 encoding을 지정해 다시 검색한다.
    """
    out: dict = {"searched": [], "read_errors": [], "undecodable": [], "not_text": [],
                 "partial": [], "mixed": [], "uncertain": [], "encodings": {}, "results": []}
    for f in files:
        res = search_file_result(f, keywords, context, encoding)
        if res.get("uncertain"):
            out["uncertain"].append({"file": str(f), "encoding": res.get("encoding"), **res["uncertain"]})
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
                     or out["uncertain"] else "complete")
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
    if result.get("files_encoding_uncertain"):
        parts.append(f"인코딩 불확실 {result['files_encoding_uncertain']}개(후보 코덱 히트 "
                     f"{result.get('candidate_hits', 0)}건은 미확정 — 코덱을 지정해 재검색 필요)")
    return "".join(", " + p for p in parts)


def uncertain_summary(found: dict) -> dict:
    """search_files 결과에서 보고서·선조사가 함께 쓰는 인코딩 불확실 필드."""
    return {"files_encoding_uncertain": len(found["uncertain"]),
            "candidate_hits": sum(len(u["candidate_hits"]) for u in found["uncertain"]),
            "encoding_uncertain": found["uncertain"][:50]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="증거형 키워드 검색 리포트 (로컬 전용)")
    ap.add_argument("target", help="검색 대상 파일 또는 디렉터리")
    ap.add_argument("keywords", nargs="+", help="검색할 키워드(리터럴)")
    ap.add_argument("--context", type=int, default=0, help="히트 전후 맥락 줄 수")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("-o", "--output", default=None, help="리포트 출력 경로")
    ap.add_argument("--encoding", default=None,
                    help="모든 파일을 이 코덱으로만 읽는다(예: shift_jis, utf-16-le) — 인코딩 불확실 파일 재검색용")
    args = ap.parse_args(argv)
    if args.encoding:
        try:
            codecs.lookup(args.encoding)
        except LookupError:
            print(f"알 수 없는 인코딩: {args.encoding}", file=sys.stderr)
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
    found = search_files(text_files, args.keywords, args.context, args.encoding)
    skipped = [str(f) for f in skipped] + [e["file"] for e in found["read_errors"]]
    results = found["results"]
    report = {
        "keywords": args.keywords,
        "scope": str(target),
        "encoding_requested": args.encoding,
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

    if args.json or args.output:
        out = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
            print(f"→ {args.output}")
        if args.json:
            print_json(report, indent=2)
    else:
        if results:
            for r in results:
                print(f"{r['file']}  (sha256:{r['sha256'][:16]}…)  {r['hit_count']}히트")
                for h in r["hits"]:
                    print(f"    L{h['line']}  [{h['keyword']}]  {h['text'][:120]}")
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
        for u in report["encoding_uncertain"]:
            print(f"주의: 인코딩 불확실 {u['file']} — {u['reason']} (후보 {', '.join(u['candidates'])})",
                  file=sys.stderr)
            for h in u["candidate_hits"]:
                print(f"    후보 {h['encoding']} L{h['line']}  [{h['keyword']}]  {h['text'][:120]} (미확정)",
                      file=sys.stderr)
        if report["total_hits"] == 0:
            print("결과: 전수 검색 범위 내 미검출" if report["status"] == "complete"
                  else "결과: 검색한 파일에서 미검출 (검색 불가 파일 제외 — 전수 검색 아님)")

    return 1 if report["total_hits"] == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
