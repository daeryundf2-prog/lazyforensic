"""Deterministic WordprocessingML for the Markdown subset emitted by report_draft."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from itertools import groupby
from pathlib import Path
from zipfile import ZipFile, ZipInfo

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ET.register_namespace("w", W[1:-1])
CONTENT_TYPES = b'''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''
RELATIONSHIPS = b'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''


def cells(line: str) -> list[str]:
    return [c.strip().replace(r"\|", "|") for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def blocks(md: str):
    """Yield headings, paragraphs and complete table rows in source order."""
    for is_table, group in groupby(md.splitlines(), key=lambda line: line.startswith("|")):
        if is_table:
            header = next(group)
            next(group, None)  # separator written by report_draft.table
            yield "table", [cells(header), *(cells(row) for row in group)]
            continue
        for line in group:
            if not line.strip():
                continue
            heading = re.match(r"^(#{1,4}) (.*)", line)
            if heading:
                yield heading[1], heading[2]
            else:
                yield "p", line.removeprefix("> ")


def display_text(s: str) -> str:
    # Render the two inline styles used in prose. Table cells contain literal evidence.
    return re.sub(r"`([^`]+)`|\*\*([^*]+)\*\*", lambda m: m[1] or m[2], s)


def xml_text(s: str) -> str:
    # XML 1.0 cannot represent these characters. Keep their code points visible.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]",
                  lambda m: f"\\u{ord(m[0]):04x}", s)


def row_text(row):
    return "| " + " | ".join(s.replace("|", r"\|") for s in row) + " |"


def draft_text(md: str) -> str:
    lines = []
    for kind, value in blocks(md):
        if kind == "table":
            lines.extend(row_text(row) for row in value)
        else:
            lines.append(display_text(value))
    return xml_text("\n".join(lines))


def word(parent, tag, **attrs):
    return ET.SubElement(parent, W + tag, {W + k: str(v) for k, v in attrs.items()})


def paragraph(parent, value, *, level=0, cell=False, header=False):
    p = word(parent, "p")
    props = word(p, "pPr")
    if level:
        word(props, "keepNext")
    word(props, "spacing", before=160 if level else 0, after=40 if cell else 100)
    if level:
        word(props, "outlineLvl", val=level - 1)
    run = word(p, "r")
    fmt = word(run, "rPr")
    word(fmt, "rFonts", ascii="맑은 고딕", hAnsi="맑은 고딕", eastAsia="맑은 고딕")
    if level or header:
        word(fmt, "b")
    size = {1: 34, 2: 28, 3: 24}.get(level, 18 if cell else 21)
    word(fmt, "sz", val=size)
    word(fmt, "lang", val="ko-KR", eastAsia="ko-KR")
    t = word(run, "t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = xml_text(value)


def table(body, rows):
    tbl = word(body, "tbl")
    props = word(tbl, "tblPr")
    width = 11906 - 2 * 1134  # portrait A4, 20 mm margins, in twips
    word(props, "tblW", w=width, type="dxa")
    borders = word(props, "tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        word(borders, side, val="single", sz=4, color="AAAAAA")
    word(props, "tblLayout", type="fixed")
    weights = {"번호": 1, "파일": 5, "크기": 2, "해시": 13}
    column_weights = [weights.get(label, 4) for label in rows[0]]
    total_weight = sum(column_weights)
    widths = [width * weight // total_weight for weight in column_weights]
    widths[-1] += width - sum(widths)
    grid = word(tbl, "tblGrid")
    for w in widths:
        word(grid, "gridCol", w=w)
    for i, row in enumerate(rows):
        tr = word(tbl, "tr")
        if i == 0:
            word(word(tr, "trPr"), "tblHeader")
        for j, value in enumerate(row):
            tc = word(tr, "tc")
            props = word(tc, "tcPr")
            word(props, "tcW", w=widths[j], type="dxa")
            if i == 0:
                word(props, "shd", val="clear", fill="E8EDF2")
            paragraph(tc, value, cell=True, header=i == 0)


def write_docx(path: Path, md: str) -> None:
    doc = ET.Element(W + "document")
    body = word(doc, "body")
    for kind, value in blocks(md):
        if kind == "table":
            table(body, value)
        else:
            paragraph(body, display_text(value), level=kind.count("#"))
    sect = word(body, "sectPr")
    word(sect, "pgSz", w=11906, h=16838)
    word(sect, "pgMar", top=1134, right=1134, bottom=1134, left=1134, header=567, footer=567, gutter=0)
    parts = {"[Content_Types].xml": CONTENT_TYPES, "_rels/.rels": RELATIONSHIPS,
             "word/document.xml": ET.tostring(doc, encoding="utf-8", xml_declaration=True)}
    with ZipFile(path, "w") as package:
        for name, data in parts.items():
            info = ZipInfo(name)  # fixed 1980 timestamp
            info.create_system = 3
            package.writestr(info, data)  # identical bytes across zlib versions


def read_docx(path: Path) -> str:
    """Keep runs contiguous, table cells delimited, and each table row on one line."""
    with ZipFile(path) as package:
        def read_xml(name):
            part = package.getinfo(name)
            if part.file_size > 32 * 1024 * 1024:
                raise ValueError("DOCX XML 크기 제한 초과")
            return ET.fromstring(package.read(part))

        rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
        type_ns = "{http://schemas.openxmlformats.org/package/2006/content-types}"
        office_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
        rels = read_xml("_rels/.rels")
        types = read_xml("[Content_Types].xml")
        if rels.tag != rel_ns + "Relationships" or types.tag != type_ns + "Types":
            raise ValueError("DOCX 패키지 메타데이터 오류")
        main = [r for r in rels.findall(rel_ns + "Relationship") if r.get("Type") == office_rel]
        if len(main) != 1 or main[0].get("TargetMode", "Internal") != "Internal":
            raise ValueError("DOCX 본문 관계 오류")
        target = main[0].attrib["Target"].removeprefix("/")
        overrides = {t.get("PartName"): t.get("ContentType") for t in types.findall(type_ns + "Override")}
        defaults = {t.get("Extension", "").lower(): t.get("ContentType") for t in types.findall(type_ns + "Default")}
        mime = overrides.get("/" + target, defaults.get(Path(target).suffix[1:].lower()))
        if mime != "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml":
            raise ValueError("DOCX 본문 콘텐츠 형식 오류")
        doc = read_xml(target)
    body = doc.find(W + "body")
    if doc.tag != W + "document" or body is None:
        raise ValueError("DOCX 본문 없음")

    def lines(node):
        if node.tag == W + "p":
            tokens = {W + "tab": "\t", W + "br": "\n", W + "cr": "\n"}
            yield "".join((n.text or "") if n.tag == W + "t" else tokens.get(n.tag, "")
                          for n in node.iter())
        elif node.tag == W + "tr":
            row = [" ".join(lines(tc)) for tc in node.findall(W + "tc")]
            yield row_text(row)
        else:
            for child in node:
                yield from lines(child)
    return "\n".join(lines(body))
