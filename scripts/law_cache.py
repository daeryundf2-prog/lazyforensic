"""korean_law MCP 응답 캐시와 보고서 조문 인용의 구조 대조.

캐시는 역직렬화한 객체만 본다. 유니코드 이스케이프·공백·키 순서 같은 JSON 표기는 판정에 영향이 없다.
근거는 구조화된 조문 레코드(법령명·법령 ID·시행일·조·항)뿐이다. 조문 본문 안에서 다른 조문을
언급한 문자열은 그 조문이 캐시에 있다는 근거가 아니다.

보고서 인용 하나는 다음을 모두 만족할 때만 확인된다.
- 같은 조(…조의…) 레코드가 있다.
- 인용 앞에 법령명이 있으면 그 법령의 레코드다. 법령명이 없으면 캐시 안에서 그 조가 한 법령·한 판본에만 있다.
- 인용에 시행일(판본)이 붙어 있으면 같은 시행일의 레코드다. 없으면 후보 판본이 하나뿐이다.
- 항을 인용했으면 레코드에 그 항이 있다. 항 번호 필드·항 목록·조문 식별자가 있으면 그것만 쓰고, 없을 때만
  본문의 항 시작 표지(첫머리나 공백 뒤 ①부터 차례로)를 쓴다. 인용부호 속 ‘②’ 같은 본문 예시는 근거가 아니다.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

LAW_NAME_KEYS = ("statute_name", "statute", "law_name", "lawName", "법령명", "법령명한글")
LAW_ID_KEYS = ("law_id", "lawId", "법령ID", "mst", "MST", "법령일련번호")
DATE_KEYS = ("effective_date", "effectiveDate", "enforcement_date", "시행일자", "시행일")
ARTICLE_KEYS = ("article_number", "articleNumber", "article_no", "article", "조문번호", "조번호")
PARAGRAPH_KEYS = ("paragraph", "paragraph_number", "항", "항번호")
TEXT_KEYS = ("text", "content", "article_text", "조문내용", "본문")
CONTAINER_KEYS = ("articles", "matches", "조문")

ARTICLE_RE = re.compile(r"^\s*(?:제\s*)?(\d+)\s*(?:조)?(?:\s*의\s*(\d+))?"
                        r"(?:\s*(?:제\s*)?(\d+)\s*항)?\s*(?:\([^()]*\))?\s*$")
CITATION_RE = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?(?:\s*제?\s*(\d+)\s*항)?")
DATE_RE = re.compile(r"((?:19|20)\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})|((?:19|20)\d{2})(\d{2})(\d{2})")
CIRCLED = {chr(0x2460 + i): i + 1 for i in range(20)}  # ①…⑳
SAME_LAW_RE = re.compile(r"(?:^|[\s(「『])(?:같은|동|위)\s*법(?:률)?$")
LAW_TOKEN_RE = re.compile(r"(?:^|[\s「『(,·])([^\s「『(,·]+?(?:법률|법|령|규칙))$")


def compact(name: str) -> str:
    return re.sub(r"\s+", "", name)


def article_key(number, sub=None) -> str:
    return f"제{int(number)}조" + (f"의{int(sub)}" if sub else "")


def parse_article(value):
    """'제32조의2', '32', '32의2', '제32조의2 제1항', '제20조(목적)', 32 → (조 표기, 항 또는 None)."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    m = ARTICLE_RE.match(str(value))
    if not m:
        return None
    return article_key(m.group(1), m.group(2)), (int(m.group(3)) if m.group(3) else None)


def normalize_date(value):
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return None
    m = DATE_RE.search(value)
    if not m:
        return None
    year, month, day = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _first(obj: dict, keys):
    for key in keys:
        value = obj.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            return str(value).strip()
    return None


def _text_paragraphs(text: str) -> set:
    """본문에서 항 시작 표지만 항 번호로 복원한다.

    항 표지는 본문 첫머리나 공백 뒤에 오고 뒤에 공백이 따르며 ①부터 차례로 이어진다. 인용부호·괄호에
    붙은 원문자(예: ‘②’)와 순서를 벗어난 원문자는 본문 속 예시·참조라 항의 근거가 아니다."""
    found, expected = set(), 1
    for i, c in enumerate(text):
        if CIRCLED.get(c) != expected:
            continue
        before = text[i - 1] if i else ""
        after = text[i + 1] if i + 1 < len(text) else ""
        if (not before or before.isspace() or before == ")") and (not after or after.isspace()):
            found.add(expected)
            expected += 1
    return found


def _explicit_paragraphs(obj) -> set:
    found = set()
    for key in PARAGRAPH_KEYS:
        value = obj.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            found.add(value)
        elif isinstance(value, str) and (m := re.search(r"\d+", value)):
            found.add(int(m.group()))
        elif isinstance(value, str) and value.strip() in CIRCLED:
            found.add(CIRCLED[value.strip()])
    return found


def _paragraph_numbers(obj, identified=None):
    """레코드가 밝힌 항 번호 집합. 항 정보가 없으면 None.

    항 필드·항 목록·조문 식별자의 항(identified)이 있으면 그것만 쓴다. 구조 정보가 없을 때만 본문의
    항 시작 표지에서 복원한다."""
    found = _explicit_paragraphs(obj) | ({identified} if identified is not None else set())
    for item in obj.get("paragraphs") or ():
        if isinstance(item, int) and not isinstance(item, bool):
            found.add(item)
        elif isinstance(item, dict):
            # 항 목록의 한 항: 항 필드가 없으면 그 항 본문의 첫 표지만 본다.
            own = _explicit_paragraphs(item)
            text = (_first(item, TEXT_KEYS) or "") if not own else ""
            lead = text.lstrip()[:2] if isinstance(text, str) else ""
            if not own and lead[:1] in CIRCLED and (len(lead) == 1 or lead[1].isspace()):
                own = {CIRCLED[lead[0]]}
            found |= own
    if found:
        return found
    text = _first(obj, TEXT_KEYS)
    if isinstance(text, str):
        found = _text_paragraphs(text)
    return found or None


def records(obj) -> list[dict]:
    """캐시 객체에서 조문 레코드를 모은다. 법령명·ID·시행일은 바깥 객체에서 물려받는다."""
    out: list[dict] = []

    def add(ctx, article, paragraphs):
        out.append({**ctx, "article": article, "paragraphs": paragraphs})

    def walk(node, ctx):
        if isinstance(node, list):
            for item in node:
                walk(item, ctx)
            return
        if not isinstance(node, dict):
            return
        ctx = dict(ctx)
        if law := _first(node, LAW_NAME_KEYS):
            ctx["law"] = law
        if law_id := _first(node, LAW_ID_KEYS):
            ctx["law_id"] = law_id
        for key in DATE_KEYS:
            if key in node and (date := normalize_date(node[key])):
                ctx["effective_date"] = date
                break
        if (raw := _first(node, ARTICLE_KEYS)) and (parsed := parse_article(raw)):
            article, paragraph = parsed
            paragraphs = _paragraph_numbers(node, paragraph)
            add(ctx, article, paragraphs)
        for key, value in node.items():
            if key in CONTAINER_KEYS:
                if isinstance(value, dict):  # {"20": "본문", "제32조의2": {...}}
                    for number, body in value.items():
                        parsed = parse_article(number)
                        if parsed is None:
                            walk(body, ctx)
                            continue
                        article, paragraph = parsed
                        detail = body if isinstance(body, dict) else {"text": body}
                        paragraphs = _paragraph_numbers(detail, paragraph)
                        add(ctx, article, paragraphs)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, (str, int)) and (parsed := parse_article(item)):
                            add(ctx, parsed[0], None if parsed[1] is None else {parsed[1]})
                        else:
                            walk(item, ctx)
            elif isinstance(value, (dict, list)):
                walk(value, ctx)

    walk(obj, {"law": None, "law_id": None, "effective_date": None})
    return out


def _edition(record):
    return (compact(record["law"]) if record["law"] else None, record["law_id"], record["effective_date"])


def cited_law(prefix: str, names: list[str], previous):
    """인용 바로 앞의 법령명과 괄호 속 시행일. (법령명 또는 None, 시행일 또는 None)."""
    s = prefix.rstrip()
    date = None
    m = re.search(r"\(([^()]*)\)$", s)
    if m:
        date = normalize_date(m.group(1))
        s = s[:m.start()].rstrip()
    s = s.rstrip("」』").rstrip()
    if SAME_LAW_RE.search(s):
        return previous, date
    for name in sorted(names, key=lambda n: len(compact(n)), reverse=True):
        pattern = r"\s*".join(re.escape(c) for c in compact(name)) + "$"
        hit = re.search(pattern, s)
        if hit and (hit.start() == 0 or not re.match(r"[가-힣A-Za-z0-9]", s[hit.start() - 1])):
            return name, date
    m = LAW_TOKEN_RE.search(s)
    return (m.group(1) if m else None), date


def check(text: str, cache_path: Path, known_names=()) -> dict:
    """보고서의 조문 인용을 캐시 레코드와 대조한다. 결과에는 인용마다 판정과 근거 레코드가 있다."""
    data = Path(cache_path).read_bytes()
    cached = records(json.loads(data))
    names = sorted({r["law"] for r in cached if r["law"]} | set(known_names))
    results, previous = [], None
    for line in text.splitlines():
        for m in CITATION_RE.finditer(line):
            law, date = cited_law(line[:m.start()], names, previous)
            if law:
                previous = law
            article = article_key(m.group(1), m.group(2))
            paragraph = int(m.group(3)) if m.group(3) else None
            entry = {"citation": re.sub(r"\s+", " ", m.group(0)), "law": law, "effective_date": date,
                     "article": article, "paragraph": paragraph}
            candidates = [r for r in cached if r["article"] == article]
            reason = None
            if not candidates:
                reason = "missing"
            else:
                if law:
                    candidates = [r for r in candidates if r["law"] and compact(r["law"]) == compact(law)]
                    reason = None if candidates else "다른 법령의 같은 번호 조문만 있음"
                if not reason and date:
                    candidates = [r for r in candidates if r["effective_date"] == date]
                    reason = None if candidates else "인용한 시행일의 판본이 없음"
                if not reason and len({_edition(r) for r in candidates}) > 1:
                    reason = "같은 번호 조문이 여러 법령·판본에 있어 특정할 수 없음"
                if not reason and paragraph is not None:
                    candidates = [r for r in candidates if r["paragraphs"] and paragraph in r["paragraphs"]]
                    reason = None if candidates else f"제{paragraph}항을 확인할 항 정보가 없음"
            if reason:
                entry.update(status="missing" if reason == "missing" else "unconfirmed",
                             reason=None if reason == "missing" else reason)
            else:
                basis = candidates[0]
                entry.update(status="matched", basis={k: basis[k] for k in ("law", "law_id", "effective_date", "article")}
                             | {"paragraph": paragraph})
            results.append(entry)
    return {"cache": Path(cache_path).name, "cache_sha256": hashlib.sha256(data).hexdigest(),
            "records": len(cached), "citations": results}
