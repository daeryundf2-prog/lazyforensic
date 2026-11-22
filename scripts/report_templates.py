"""Measured report fields and the WordprocessingML blank-form adapter."""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from bisect import bisect_right
from itertools import accumulate
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.dom import minidom
from xml.parsers.expat import ExpatError
from zipfile import BadZipFile, ZipFile
import zlib

import report_docx

UNKNOWN = "미확인"
OUTPUT_NAME = "양식초안.docx"
DIGEST_SUFFIX = ".본문.sha256"
TOKEN = re.compile(r"\{\{([^{}\r\n\t]*)\}\}")
WORD = report_docx.W[1:-1]
STORY_TYPES = {"application/vnd.openxmlformats-officedocument.wordprocessingml." + kind + "+xml"
               for kind in ("header", "footer", "footnotes", "endnotes", "comments")}
PACKAGE_ERRORS = (OSError, BadZipFile, KeyError, ET.ParseError, ExpatError, ValueError, RuntimeError, zlib.error)


def cell(value):
    if value is None or value == "":
        return UNKNOWN
    return report_docx.xml_text(" ".join(str(value).splitlines()))


def limited_table(header, rows, total, companion):
    if not rows:
        return UNKNOWN
    if len(rows) < total:
        rows.append([f"나머지 {total - len(rows)}건", companion, *([""] * (len(header) - 2))])
    return [header, *rows]


def form_fields(case, inventory, integrity_rows, events, limit):
    """Format-neutral mapping. A value is either literal text or a matrix of cells."""
    values = {label.replace(" ", ""): cell(value) for label, value in case.items()}
    values["분석관의견"] = UNKNOWN
    evidence = []
    for item in inventory[:limit]:
        hashes = " / ".join(f"{label} {cell(item.get(key))}"
                            for label, key in (("MD5", "md5"), ("SHA-1", "sha1"), ("SHA-256", "sha256")))
        evidence.append([cell(item["path"]), cell(item.get("size")), hashes])
    values["증거목록표"] = limited_table(["파일", "크기(바이트)", "해시"], evidence, len(inventory), "파일해시목록.csv")
    values["무결성검증표"] = [["검증 항목", "결과", "근거"], *[[cell(v) for v in row] for row in integrity_rows]]
    timeline = [[cell(ev.get(k)) for k in ("timestamp", "source", "file", "description")] for ev in events[:limit]]
    values["타임라인표"] = limited_table(["시각(기록값)", "출처", "파일", "설명"], timeline, len(events), "events.json")
    return values


def story_parts(package):
    names = package.namelist()
    if len(names) != len(set(names)) or len(names) > 10000:
        raise ValueError("양식 패키지 항목이 중복되거나 너무 많습니다")
    if sum(entry.file_size for entry in package.infolist()) > 64 * 1024 * 1024:
        raise ValueError("압축 해제 기준 양식 크기 제한(64 MiB) 초과")
    main, overrides = report_docx.document_parts(package)
    extra = sorted(name.lstrip("/") for name, mime in overrides.items() if mime in STORY_TYPES)
    return [main, *extra]


def projected_lines(value, fields):
    """Expand the blank form as plain text, independently of the XML writer."""
    match = TOKEN.fullmatch(value.strip())
    if match and isinstance(rows := fields.get(match[1].strip()), list):
        yield from (report_docx.row_text(row) for row in rows)
        yield ""  # anchor paragraph following the new table
        return

    def scalar(match):
        result = fields.get(match[1].strip(), UNKNOWN)
        if isinstance(result, list):
            raise ValueError("표 자리표시자는 한 문단에 단독으로 배치하세요")
        return result

    yield TOKEN.sub(scalar, value)


def validate_story(root):
    # These Word features obtain their visible text outside ordinary w:t runs.
    # Page-number fields commonly occur in headers and have a cached w:t result.
    for node in root.iter():
        if node.tag in {report_docx.W + "altChunk", report_docx.W + "subDoc", report_docx.W + "object",
                        "{http://schemas.openxmlformats.org/drawingml/2006/main}t",
                        "{http://schemas.openxmlformats.org/drawingml/2006/chart}chart"}:
            raise ValueError("외부 본문·포함 개체·도형 텍스트는 양식 검증에서 지원하지 않습니다")
    instructions = "".join(n.text or "" for n in root.iter(report_docx.W + "instrText"))
    instructions += "".join(n.get(report_docx.W + "instr", "") for n in root.iter(report_docx.W + "fldSimple"))
    if "{{" in instructions or "}}" in instructions:
        raise ValueError("필드 코드에 치환되지 않은 자리표시자가 있습니다")


def form_text(package, fields=None):
    parts = story_parts(package)
    expand = None if fields is None else lambda value: projected_lines(value, fields)
    lines = []
    for part in parts:
        root = report_docx.read_xml(package, part)
        if part == parts[0]:
            root = report_docx.body(root)
        validate_story(root)
        lines.extend(report_docx.lines(root, expand))
    return "\n".join(lines)


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def node_text(node):
    return "".join(n.data for n in node.childNodes if n.nodeType in (n.TEXT_NODE, n.CDATA_SECTION_NODE))


def replace_runs(nodes, fields):
    """Edit matches backwards so offsets and formatting outside each match survive."""
    texts = [node_text(node) for node in nodes]
    ends = list(accumulate(map(len, texts)))
    starts = [0, *ends[:-1]]
    matches = list(TOKEN.finditer("".join(texts)))
    for match in reversed(matches):
        first = bisect_right(ends, match.start())
        last = bisect_right(ends, match.end() - 1)
        value = fields.get(match[1].strip(), UNKNOWN)
        if isinstance(value, list):
            raise ValueError("표 자리표시자는 한 문단에 단독으로 배치하세요")
        prefix = texts[first][:match.start() - starts[first]]
        suffix = texts[last][match.end() - starts[last]:]
        texts[first:last + 1] = [prefix + value, *([""] * (last - first))]
        texts[last] += suffix
    if matches:
        for node, value in zip(nodes, texts):
            while node.firstChild:
                node.removeChild(node.firstChild)
            node.appendChild(node.ownerDocument.createTextNode(value))
            node.setAttribute("xml:space", "preserve")


def fill_story(data, fields):
    # DOM preserves the prefix declarations referenced by mc:Ignorable and the
    # unedited layout/relationship attributes in office-authored forms.
    with minidom.parseString(data) as doc:
        for p in doc.getElementsByTagNameNS(WORD, "p"):
            elements = p.getElementsByTagName("*")
            if any(el.namespaceURI == WORD and el.localName == "p" for el in elements):
                raise ValueError("텍스트 상자 안의 중첩 문단은 지원하지 않습니다")
            nodes = [el for el in elements if el.namespaceURI == WORD and el.localName == "t"]
            value = "".join(map(node_text, nodes))
            match = TOKEN.fullmatch(value.strip())
            if match and isinstance(rows := fields.get(match[1].strip()), list):
                if p.parentNode.namespaceURI != WORD or p.parentNode.localName != "body":
                    raise ValueError("표 자리표시자는 본문에 독립된 문단으로 배치하세요")
                holder = ET.Element("holder")
                report_docx.table(holder, rows)
                with minidom.parseString(ET.tostring(holder[0], encoding="utf-8")) as fragment:
                    p.parentNode.insertBefore(doc.importNode(fragment.documentElement, True), p)
                # Keep the anchor's section breaks, bookmarks and paragraph properties.
                for node in nodes:
                    while node.firstChild:
                        node.removeChild(node.firstChild)
            else:
                replace_runs(nodes, fields)
        return doc.toxml(encoding="utf-8")


def write_form(source, destination, fields):
    """Publish only complete packages. Derive the parity reference from the input form."""
    with ZipFile(source) as blank:
        expected = form_text(blank, fields)
        stories = set(story_parts(blank))
        with TemporaryDirectory(dir=destination.parent) as temp:
            staged = Path(temp) / "draft.docx"
            digest = Path(temp) / "expected"
            with ZipFile(staged, "w") as filled:
                for part in blank.infolist():
                    data = blank.read(part)
                    if part.filename in stories:
                        data = fill_story(data, fields)
                    filled.writestr(part, data)
            digest.write_text(text_digest(expected) + "\n", encoding="ascii")
            digest.replace(destination.with_suffix(DIGEST_SUFFIX))
            staged.replace(destination)


def check_form(path, reference, errors):
    with ZipFile(path) as package:
        text = form_text(package)
    if reference.read_text(encoding="ascii").strip() != text_digest(text):
        errors.append("양식 원본과 측정값으로 계산한 본문 SHA-256 불일치 (표·해시·본문 대조)")
    return text
