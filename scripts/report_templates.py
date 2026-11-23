"""Measured report fields and the WordprocessingML blank-form adapter."""
from __future__ import annotations

import hashlib
import json
import posixpath
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
import report_hwpx
from report_package import check_package_size

UNKNOWN = "미확인"
OUTPUT_NAME = "양식초안.docx"
DIGEST_SUFFIX = ".본문.sha256"
FORM_MARKER = b"\nLazyForensic filled report form v2"
TOKEN = re.compile(r"\{\{([^{}\r\n\t]*)\}\}")
WORD = report_docx.W[1:-1]
STORY_MIME = {kind: f"application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"
              for kind in ("header", "footer", "footnotes", "endnotes", "comments")}
STORY_ROOTS = {mime: report_docx.W + {"header": "hdr", "footer": "ftr"}.get(kind, kind)
               for kind, mime in STORY_MIME.items()}
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
TOKEN_BOUNDARIES = set(report_docx.RUN_CONTROLS) | {"fldSimple", "fldChar", "drawing", "pict"}
PACKAGE_ERRORS = (OSError, BadZipFile, KeyError, ET.ParseError, ExpatError, ValueError, RuntimeError, zlib.error)


def cell(value):
    if value is None or value == "":
        return UNKNOWN
    return report_docx.display_value(str(value))


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
    # File events carry the exact nanosecond mtime; timestamp is truncated to microseconds for ISO parsers.
    timeline = [[cell(ev.get("mtime") or ev.get("timestamp")), *[cell(ev.get(k)) for k in ("source", "file", "description")]]
                for ev in events[:limit]]
    values["타임라인표"] = limited_table(["시각(기록값)", "출처", "파일", "설명"], timeline, len(events), "events.json")
    return values


def story_parts(package):
    check_package_size(package)
    main, types = report_docx.document_parts(package)
    directory, filename = posixpath.split(main)
    rel_path = posixpath.join(directory, "_rels", filename + ".rels")
    linked = {}
    if rel_path in types:
        rels = report_docx.read_xml(package, rel_path)
        for rel in rels.findall(PACKAGE_REL + "Relationship"):
            kind = rel.get("Type", "").removeprefix(OFFICE_REL + "/")
            if kind in STORY_MIME:
                target = posixpath.normpath(posixpath.join(directory, rel.attrib["Target"])).lstrip("/")
                if rel.get("TargetMode", "Internal") != "Internal" or types.get(target) != STORY_MIME[kind]:
                    raise ValueError("DOCX 양식 본문 관계의 경로 또는 콘텐츠 형식 오류")
                linked[rel.attrib["Id"]] = (kind, target)
    for node in report_docx.read_xml(package, main).iter():
        if node.tag in (report_docx.W + "headerReference", report_docx.W + "footerReference"):
            kind = node.tag.removeprefix(report_docx.W).removesuffix("Reference")
            if linked.get(node.get("{" + OFFICE_REL + "}id"), (None, None))[0] != kind:
                raise ValueError("DOCX 머리말·꼬리말 관계 누락")
    extra = sorted(name for name, mime in types.items() if mime in STORY_MIME.values())
    return [main, *extra], types, linked


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


def has_hidden_format(node):
    w = report_docx.W
    return node.tag in (w + "vanish", w + "webHidden", w + "specVanish") and node.get(w + "val") not in ("0", "false", "off")


def check_styles(styles, used):
    w = report_docx.W
    defaults = styles.find(w + "docDefaults")
    if defaults is not None and any(map(has_hidden_format, defaults.iter())):
        raise ValueError("기본 숨김 텍스트 서식은 지원하지 않습니다")
    definitions = {style.attrib[w + "styleId"]: style for style in styles.findall(w + "style")}
    pending = used | {name for name, style in definitions.items() if style.get(w + "default") in ("1", "true", "on")}
    checked = set()
    while pending:
        name = pending.pop()
        style = definitions.get(name)
        if style is None or name in checked:
            continue
        checked.add(name)
        if any(map(has_hidden_format, style.iter())):
            raise ValueError("사용 중인 스타일에 숨김 텍스트 서식이 있습니다")
        base = style.find(w + "basedOn")
        if base is not None:
            pending.add(base.get(w + "val"))


def validate_story(root):
    w = report_docx.W
    unsupported = {w + name for name in ("altChunk", "subDoc", "object", "del", "moveFrom",
                                          "delText", "ins", "moveTo", "sym", "dataBinding", "txbxContent")}
    unsupported.update({"{http://schemas.openxmlformats.org/drawingml/2006/main}t",
                        "{http://schemas.openxmlformats.org/drawingml/2006/chart}chart",
                        "{urn:schemas-microsoft-com:vml}textpath",
                        "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"})
    # Page numbering is layout metadata. Other fields can replace measured
    # values with a different result when the recipient opens the document.
    page_field = re.compile(r"(?:PAGE|NUMPAGES)(?:\s+\\\*\s+(?:MERGEFORMAT|Arabic))*", re.I)
    field = None
    for node in root.iter():
        if node.tag == w + "tc":
            merges = (node.find(w + "tcPr/" + w + name) for name in ("hMerge", "vMerge"))
            continued = any(m is not None and m.get(w + "val", "continue") == "continue" for m in merges)
            if continued and any((t.text or "").strip() for t in node.iter(w + "t")):
                raise ValueError("병합 연속 셀의 텍스트를 확인할 수 없습니다")
        if node.tag in unsupported:
            raise ValueError("도형·변경 내용·외부 또는 동적 본문을 검증할 수 없습니다")
        if has_hidden_format(node):
            raise ValueError("숨김 텍스트 서식은 양식에서 지원하지 않습니다")
        if node.tag == w + "fldSimple":
            cached = "".join(t.text or "" for t in node.iter(w + "t")).strip()
            if cached and not cached.isdecimal():
                raise ValueError("쪽 번호 필드 결과에는 숫자만 사용할 수 있습니다")
            if not page_field.fullmatch(node.get(w + "instr", "").strip()):
                raise ValueError("쪽 번호 외의 동적 필드는 검증할 수 없습니다")
        elif node.tag == w + "t" and field is not None:
            if (node.text or "").strip() and not node.text.strip().isdecimal():
                raise ValueError("쪽 번호 필드 결과에는 숫자만 사용할 수 있습니다")
        elif node.tag == w + "instrText":
            if field is None:
                raise ValueError("DOCX 필드 시작 누락")
            field.append(node.text or "")
        elif node.tag == w + "fldChar":
            kind = node.get(w + "fldCharType")
            if kind == "begin" and field is None:
                field = []
            elif kind in ("separate", "end") and field is not None:
                if not page_field.fullmatch("".join(field).strip()):
                    raise ValueError("쪽 번호 외의 동적 필드는 검증할 수 없습니다")
                if kind == "end":
                    field = None
            else:
                raise ValueError("DOCX 필드 구성 오류")
    if field is not None:
        raise ValueError("DOCX 필드 끝 누락")


def alternative_text(root):
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] in ("docPr", "cNvPr", "shape", "imagedata"):
            for key, value in node.attrib.items():
                if key.rsplit("}", 1)[-1] in ("descr", "title", "alt") and value:
                    yield value


def word_structure(node, fields):
    """Yield paragraph/cell boundaries in addition to the text used for policy scans."""
    w = report_docx.W
    name = node.tag.removeprefix(w)
    if name == "p":
        value, = report_docx.lines(node)
        if fields is not None:
            marker = TOKEN.fullmatch(value.strip())
            rows = fields.get(marker[1].strip()) if marker else None
            if isinstance(rows, list):
                yield "tbl"
                for row in rows:
                    yield "tr"
                    for cell_value in row:
                        yield from ("tc", ["p", cell_value], "/tc")
                    yield "/tr"
                yield "/tbl"
                value = ""
            else:
                value, = projected_lines(value, fields)
        yield ["p", value]
        # Section references and page fields can occur inside paragraphs.
    boundary = name in ("tbl", "tr", "tc", "footnote", "endnote", "comment", "sectPr")
    if boundary:
        yield name
    if name in ("footnote", "endnote", "comment", "headerReference", "footerReference",
                "footnoteReference", "endnoteReference", "commentReference", "gridSpan",
                "vMerge", "hMerge", "gridBefore", "gridAfter", "fldSimple", "fldChar", "instrText"):
        yield [name, sorted(node.attrib.items()), node.text or ""]
    for child in node:
        yield from word_structure(child, fields)
    if boundary:
        yield "/" + name


def word_content(package, fields=None):
    parts, types, linked = story_parts(package)
    structure = [["relationships", sorted(linked.items())]]
    used_styles = set()
    expand = None if fields is None else lambda value: projected_lines(value, fields)
    lines = []
    for part in parts:
        root = report_docx.read_xml(package, part)
        if part == parts[0]:
            root = report_docx.body(root)
        elif root.tag != STORY_ROOTS[types[part]]:
            raise ValueError("DOCX 양식 본문 종류와 콘텐츠 형식이 다릅니다")
        validate_story(root)
        used_styles.update(n.get(report_docx.W + "val") for n in root.iter()
                          if n.tag in (report_docx.W + "rStyle", report_docx.W + "pStyle", report_docx.W + "tblStyle"))
        lines.extend(report_docx.lines(root, expand))
        alt = list(alternative_text(root))
        lines.extend(alt)
        structure.append([part, list(word_structure(root, fields)), alt])
    for part, mime in types.items():
        if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml":
            check_styles(report_docx.read_xml(package, part), used_styles)
    encoded = json.dumps(structure, ensure_ascii=False, separators=(",", ":"))
    return text_digest("DOCX form v2\n" + encoded), "\n".join(lines), parts


def form_text(package, fields=None):
    return word_content(package, fields)[1]


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def node_text(node):
    return "".join(n.data for n in node.childNodes if n.nodeType in (n.TEXT_NODE, n.CDATA_SECTION_NODE))


def replaced_texts(texts, fields):
    """Edit matches backwards so offsets and formatting outside each match survive."""
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
    return texts if matches else None


def replace_runs(nodes, fields):
    texts = replaced_texts([node_text(node) for node in nodes], fields)
    if texts is not None:
        for node, value in zip(nodes, texts):
            while node.firstChild:
                node.removeChild(node.firstChild)
            node.appendChild(node.ownerDocument.createTextNode(value))
            node.setAttribute("xml:space", "preserve")


def editable_runs(elements):
    nodes, texts, boundaries = [], [], []
    offset = 0
    for el in elements:
        if el.namespaceURI != WORD:
            continue
        if el.localName == "t":
            value = node_text(el)
            nodes.append(el)
            texts.append(value)
            offset += len(value)
        elif el.localName in TOKEN_BOUNDARIES:
            boundaries.append(offset)
    value = "".join(texts)
    for match in TOKEN.finditer(value):
        index = bisect_right(boundaries, match.start())
        if index < len(boundaries) and boundaries[index] < match.end():
            raise ValueError("자리표시자 중간에 필드·탭·줄바꿈·도형을 넣을 수 없습니다")
    return nodes, value


def fill_story(data, fields):
    # DOM preserves the prefix declarations referenced by mc:Ignorable and the
    # unedited layout/relationship attributes in office-authored forms.
    with minidom.parseString(data) as doc:
        for p in doc.getElementsByTagNameNS(WORD, "p"):
            elements = p.getElementsByTagName("*")
            if any(el.namespaceURI == WORD and el.localName == "p" for el in elements):
                raise ValueError("텍스트 상자 안의 중첩 문단은 지원하지 않습니다")
            nodes, value = editable_runs(elements)
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


def digest_path(path):
    if path.suffix.lower() == ".hwpx":
        return path.with_name(path.name + DIGEST_SUFFIX)
    return path.with_suffix(DIGEST_SUFFIX)


def write_form(source, destination, fields):
    """Publish only complete packages. Derive the parity reference from the input form."""
    with ZipFile(source) as blank:
        with TemporaryDirectory(dir=destination.parent) as temp:
            staged = Path(temp) / "draft"
            digest = Path(temp) / "expected"
            with ZipFile(staged, "w") as filled:
                comment = blank.comment
                if not comment.endswith(FORM_MARKER):
                    comment += FORM_MARKER
                if len(comment) > 65535:
                    raise ValueError("양식 표시를 추가할 ZIP 주석 공간이 없습니다")
                filled.comment = comment
                if source.suffix.lower() == ".hwpx":
                    import report_hwpx_forms
                    expected = report_hwpx_forms.write_parts(blank, filled, fields)
                else:
                    expected, _, parts = word_content(blank, fields)
                    stories = set(parts)
                    for part in blank.infolist():
                        data = blank.read(part)
                        if part.filename in stories:
                            data = fill_story(data, fields)
                        filled.writestr(part, data)
            digest.write_text(expected + "\n", encoding="ascii")
            digest.replace(digest_path(destination))
            staged.replace(destination)


def check_form(path, reference, errors, warnings=None):
    stored = reference.read_text(encoding="ascii").strip()
    with ZipFile(path) as package:
        legacy = not package.comment.endswith(FORM_MARKER)
        if path.suffix.lower() == ".hwpx":
            import report_hwpx_forms
            digest, text = report_hwpx_forms.form_content(package)
            report_hwpx.check_preview(package, text, errors)
            if legacy and digest != stored:
                legacy = report_hwpx_forms.form_content(package, legacy=True)[0] == stored
        else:
            digest, text, _ = word_content(package)
            legacy = legacy and text_digest(text) == stored
    if stored != digest:
        if not legacy:
            errors.append("양식 원본과 측정값으로 계산한 본문 SHA-256 불일치 (표·해시·본문 대조)")
        elif warnings is not None:
            warnings.append("이전 양식 대조 파일 형식으로 확인했습니다. 강화된 표 구조 검증에는 새 초안이 필요합니다.")
    return text
