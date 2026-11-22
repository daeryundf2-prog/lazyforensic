"""Fill and independently project the section and table content of an OWPML form."""
from __future__ import annotations

import json
import posixpath
import re
from itertools import count, groupby
from xml.dom import Node, minidom
from xml.parsers import expat
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED

import report_hwpx as hw
import report_templates as forms
from report_docx import row_text
from report_package import xml_bytes

HP = hw.NS["hp"]
O = "{" + hw.OPF + "}"
C = "{" + hw.CONTAINER + "}"
TEXT_MARKS = {"markpenBegin", "markpenEnd", "titleMark"}
BREAKS = {"tab": "\t", "lineBreak": "\n", "hyphen": "-", "nbSpace": "\u00a0", "fwSpace": "\u3000"}


def layout(package):
    forms.check_package_size(package)
    names = set(package.namelist())
    mime = package.getinfo("mimetype")
    if (package.infolist()[0] != mime or mime.header_offset or mime.compress_type != ZIP_STORED
            or package.read(mime) != hw.MIME):
        raise ValueError("HWPX mimetype 오류")
    container = ET.fromstring(xml_bytes(package, "META-INF/container.xml"))
    roots = container.findall(f"{C}rootfiles/{C}rootfile[@media-type='application/hwpml-package+xml']")
    if container.tag != C + "container" or len(roots) != 1:
        raise ValueError("HWPX 패키지 경로 오류")
    path = roots[0].attrib["full-path"]
    content = ET.fromstring(xml_bytes(package, path))
    items = {}
    for item in content.findall(f"{O}manifest/{O}item"):
        href = item.attrib["href"]
        part = href if href in names else posixpath.normpath(posixpath.join(posixpath.dirname(path), href))
        if item.attrib["id"] in items or part not in names:
            raise ValueError("HWPX manifest 항목 오류")
        items[item.attrib["id"]] = part
    spine = [items[ref.attrib["idref"]] for ref in content.findall(f"{O}spine/{O}itemref")]
    if content.tag != O + "package" or len(spine) != len(set(spine)):
        raise ValueError("HWPX spine 구성 오류")
    sections, masters = [], []
    header = None
    for part in spine:
        root = ET.fromstring(xml_bytes(package, part))
        if root.tag == hw.HS + "sec":
            sections.append(part)
        elif root.tag == "{" + hw.NS["hh"] + "}head" and header is None:
            header = root
        elif root.tag in ("masterPage", "{" + hw.NS["hm"] + "}masterPage"):
            masters.append(part)
        else:
            raise ValueError("HWPX spine 본문 구성 오류")
    named_sections = {n for n in names if re.fullmatch(r"Contents/section\d+\.xml", n)}
    if not sections or named_sections.difference(sections) or header is None:
        raise ValueError("HWPX 섹션 또는 머리 정보 누락")
    masters.extend(sorted(n for n in items.values() if re.fullmatch(r"Contents/masterpage\d*\.xml", n, re.I)
                          and n not in masters))
    border = header.find(".//{" + hw.NS["hh"] + "}borderFill")
    return sections + masters, border.attrib["id"] if border is not None else None


def tag(node):
    return node.localName if node.namespaceURI == HP else None


def children(node, name):
    return [n for n in node.childNodes if tag(n) == name]


def only(node, name):
    result = children(node, name)
    if len(result) != 1:
        raise ValueError(f"HWPX {name} 구성 오류")
    return result[0]


def parts(node):
    """Yield editable text spans and nested blocks in reading order. Controls break spans."""
    def walk(parent):
        for el in parent.childNodes:
            if el.nodeType != Node.ELEMENT_NODE:
                continue
            kind = tag(el)
            if kind == "t":
                for child in el.childNodes:
                    if child.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE):
                        yield child
                    elif child.nodeType == Node.ELEMENT_NODE:
                        if tag(child) in TEXT_MARKS:
                            continue
                        if tag(child) not in BREAKS:
                            raise ValueError("HWPX 텍스트 요소 판독 불가")
                        yield BREAKS[tag(child)]
            elif kind in ("p", "tbl"):
                yield el
            elif kind == "run":
                yield from walk(el)
            else:
                yield ""
                yield from walk(el)
                yield ""
    for is_text, group in groupby(walk(node), lambda n: getattr(n, "nodeType", None) in
                                  (Node.TEXT_NODE, Node.CDATA_SECTION_NODE)):
        if is_text:
            yield list(group)
        else:
            yield from group


def span_text(span):
    return "".join(n.data for n in span)


def matrix(value, fields):
    match = forms.TOKEN.fullmatch(value.strip())
    data = fields.get(match[1].strip()) if match and fields is not None else None
    return data if isinstance(data, list) else None


def cell_paras(tc):
    return children(only(tc, "subList"), "p")


def row_plan(row, fields):
    """Locate the marker and blank column slots in this row, excluding nested tables."""
    slots = []
    data = None
    for col, tc in enumerate(children(row, "tc")):
        for p in cell_paras(tc):
            spans = [part for part in parts(p) if part != ""]
            if len(spans) == 1 and isinstance(spans[0], list):
                values = matrix(span_text(spans[0]), fields)
                if values is not None:
                    if data is not None:
                        raise ValueError("한 양식 행에는 표 자리표시자를 하나만 넣으세요")
                    data = values
                    slots.append((col, p))
                    continue
            if not spans or all(isinstance(s, list) and not span_text(s).strip() for s in spans):
                slots.append((col, p))
    if data is None:
        return [], []
    if len({col for col, _ in slots}) != len(slots) or len(slots) not in (1, len(data[0])):
        raise ValueError("표 양식 행에는 데이터 열마다 한 개의 빈 문단을 배치하세요")
    values = data[1:] or [[forms.UNKNOWN] + [""] * (len(data[0]) - 1)]
    if len(slots) == 1:
        values = [[" / ".join(v for v in row if v)] for row in values]
    return [p for _, p in slots], values


def grid(table):
    """Read physical rows and check the logical address of every cell, including merges."""
    rows = children(table, "tr")
    width = int(table.getAttribute("colCnt"))
    if len(rows) != int(table.getAttribute("rowCnt")) or not (0 < width <= 65535):
        raise ValueError("HWPX 표 크기 오류")
    occupied_until = [0] * width
    for y, row in enumerate(rows):
        x = 0
        for tc in children(row, "tc"):
            while x < width and occupied_until[x] > y:
                x += 1
            addr, span = only(tc, "cellAddr"), only(tc, "cellSpan")
            cols, height = int(span.getAttribute("colSpan")), int(span.getAttribute("rowSpan"))
            if (int(addr.getAttribute("colAddr")) != x or int(addr.getAttribute("rowAddr")) != y
                    or cols < 1 or x + cols > width or height < 1 or y + height > len(rows)
                    or any(end > y for end in occupied_until[x:x + cols])):
                raise ValueError("HWPX 표 셀 위치 오류")
            occupied_until[x:x + cols] = [y + height] * cols
            x += cols
        if any(end <= y for end in occupied_until):
            raise ValueError("HWPX 표 셀 누락")
    return rows


def text_paragraph(value):
    return ["p", [value] if value else []]


def project(node, fields, literals, seen, legacy):
    """Compute a content tree from the blank and measured values, or read the saved tree."""
    seen.add(node)
    if tag(node) == "tbl":
        rows = []
        positions = []
        for row in grid(node):
            slots, values = row_plan(row, fields) if fields else ([], [])
            for item in values or [()]:
                replacements = dict(zip(slots, item))
                cells = []
                for index, tc in enumerate(children(row, "tc")):
                    column = int(only(tc, "cellAddr").getAttribute("colAddr"))
                    span = int(only(tc, "cellSpan").getAttribute("colSpan"))
                    if not legacy and (column != index or span != 1):
                        positions.append([len(rows), index, column, span])
                    cells.append([project(p, fields, replacements, seen, legacy) for p in cell_paras(tc)])
                rows.append(cells)
        table = ["table", rows]
        if positions:
            table.append(positions)
        captions = children(node, "caption")
        if captions:
            caption = only(node, "caption")
            label = project(caption, fields, {}, seen, legacy)
            below = caption.getAttribute("side") in ("BOTTOM", "RIGHT")
            return ["captioned", [table, label] if below else [label, table]]
        return table
    content = []
    if node in literals:
        seen.update(node.getElementsByTagNameNS(HP, "t"))
        return text_paragraph(literals[node])
    for part in parts(node):
        if isinstance(part, list):
            seen.update(n.parentNode for n in part)
            value = span_text(part)
            rows = matrix(value, fields)
            if rows is not None:
                cells = [[[text_paragraph(value)] for value in row] for row in rows]
                content.append(["table", cells])
                continue
            if fields is not None:
                value, = forms.projected_lines(value, fields)
        elif isinstance(part, str):
            value = part
        else:
            content.append(project(part, fields, literals, seen, legacy))
            continue
        if value:
            if content and isinstance(content[-1], str):
                content[-1] += value
            else:
                content.append(value)
    return ["p", content]


def lines(block):
    kind, content = block[:2]
    if kind == "table":
        for row in content:
            yield row_text([" ".join(line for p in cell for line in lines(p)) for cell in row])
    else:
        for item in content:
            if isinstance(item, str):
                yield item
            else:
                yield from lines(item)


def form_content(package, fields=None, *, legacy=False):
    stories, _ = layout(package)
    sections = []
    for name in stories:
        with minidom.parseString(xml_bytes(package, name)) as doc:
            root = doc.documentElement
            if (root.namespaceURI, root.localName) not in ((hw.NS["hs"], "sec"), (None, "masterPage"),
                                                           (hw.NS["hm"], "masterPage")):
                raise ValueError("HWPX 본문 구성 오류")
            if root.getElementsByTagNameNS(HP, "fieldBegin") or root.getElementsByTagNameNS(HP, "fieldEnd"):
                raise ValueError("동적 HWPX 필드는 양식에서 검증할 수 없습니다")
            seen = set()
            blocks = [project(p, fields, {}, seen, legacy) for p in parts(root)
                      if getattr(p, "nodeType", None) == Node.ELEMENT_NODE]
            for el in root.getElementsByTagNameNS(HP, "*"):
                if el not in seen and (tag(el) in ("p", "tbl") or (tag(el) == "t" and forms.node_text(el))):
                    raise ValueError("HWPX 본문에서 판독되지 않은 문단·표·텍스트가 있습니다")
            if not blocks and root.localName == "sec":
                raise ValueError("HWPX 본문 없음")
            for comment in root.getElementsByTagNameNS(HP, "shapeComment"):
                if value := forms.node_text(comment):
                    blocks.append(text_paragraph(value))
            sections.append(blocks)
    reference = json.dumps(sections, ensure_ascii=False, separators=(",", ":"))
    text = "\n".join(line for section in sections for p in section for line in lines(p))
    return forms.text_digest(reference if legacy else "HWPX form v2\n" + reference), text


def clear_layout(p):
    for cache in children(p, "linesegarray"):
        p.removeChild(cache)


def set_text(node, value):
    # CDATA is only an XML serialization choice. Insert measured values as
    # ordinary character data so the serializer can escape every legal string.
    if node.nodeType == Node.CDATA_SECTION_NODE:
        node.parentNode.replaceChild(node.ownerDocument.createTextNode(value), node)
    else:
        node.data = value


def put_literal(p, value):
    nodes = [n for span in parts(p) if isinstance(span, list) for n in span]
    if nodes:
        set_text(nodes[0], value)
        for node in nodes[1:]:
            node.data = ""
    else:
        run = children(p, "run")
        if not run:
            prefix = (p.prefix + ":") if p.prefix else ""
            run = [p.appendChild(p.ownerDocument.createElementNS(HP, prefix + "run"))]
            run[0].setAttribute("charPrIDRef", "0")
        texts = children(run[0], "t")
        prefix = (run[0].prefix + ":") if run[0].prefix else ""
        t = texts[0] if texts else run[0].appendChild(p.ownerDocument.createElementNS(HP, prefix + "t"))
        t.appendChild(p.ownerDocument.createTextNode(value))
    clear_layout(p)


def insert_table(p, span, rows, ids, width, border):
    # Import a self-contained fragment. The form can use any namespace prefix.
    if border is None:
        raise ValueError("새 표에 사용할 HWPX 테두리 서식 없음")
    holder = hw.root("hp:run")
    hw.table(holder, rows, ids, width=width, border=border)
    run = span[0].parentNode.parentNode
    for cell_p in holder.iter("hp:p"):
        for attr in ("styleIDRef", "paraPrIDRef"):
            cell_p.set(attr, p.getAttribute(attr) or "0")
    for cell_run in holder.iter("hp:run"):
        cell_run.set("charPrIDRef", run.getAttribute("charPrIDRef") or "0")
    with minidom.parseString(hw.serialize(holder)) as doc:
        table = p.ownerDocument.importNode(only(doc.documentElement, "tbl"), True)
        table.setAttribute("xmlns:hp", HP)
        run.insertBefore(table, span[0].parentNode)
    for n in span:
        n.data = ""


def fill_table(table, fields, ids, width, border):
    rows = grid(table)
    copies = []
    changed = False
    for caption in children(table, "caption"):
        changed |= fill_paragraph(caption, fields, ids, width, border)
    for row in rows:
        slots, values = row_plan(row, fields)
        copies.append(len(values) or 1)
        prototype = row.cloneNode(True) if len(values) > 1 else None
        following = row.nextSibling
        for index, item in enumerate(values or [()]):
            current = row
            if index:
                current = table.insertBefore(prototype.cloneNode(True), following)
                for el in current.getElementsByTagName("*"):
                    if el.getAttribute("id").isdigit():
                        old_id = el.getAttribute("id")
                        el.setAttribute("id", str(next(ids)))
                        if el.getAttribute("instid") == old_id:
                            el.setAttribute("instid", el.getAttribute("id"))
                        elif el.getAttribute("instid").isdigit():
                            el.setAttribute("instid", str(next(ids)))
            targets = slots if current is row else row_plan(current, fields)[0]
            literal = dict(zip(targets, item))
            for tc in children(current, "tc"):
                for p in cell_paras(tc):
                    if p in literal:
                        put_literal(p, literal[p])
                        changed = True
                    else:
                        changed |= fill_paragraph(p, fields, ids, width, border)
    if len(rows) != sum(copies):
        offsets = [0]
        for n in copies:
            offsets.append(offsets[-1] + n)
        for y, row in enumerate(children(table, "tr")):
            for tc in children(row, "tc"):
                addr, span = only(tc, "cellAddr"), only(tc, "cellSpan")
                old = int(addr.getAttribute("rowAddr"))
                end = old + int(span.getAttribute("rowSpan"))
                if copies[old] > 1 and end > old + 1:
                    raise ValueError("반복할 행에서 시작하는 세로 병합은 지원하지 않습니다")
                if end > old + 1:
                    span.setAttribute("rowSpan", str(offsets[end] - offsets[old]))
                addr.setAttribute("rowAddr", str(y))
        for zone_list in children(table, "cellzoneList"):
            for zone in children(zone_list, "cellzone"):
                first, last = int(zone.getAttribute("startRowAddr")), int(zone.getAttribute("endRowAddr"))
                if not (0 <= first <= last < len(rows)):
                    raise ValueError("HWPX 셀 영역 오류")
                zone.setAttribute("startRowAddr", str(offsets[first]))
                zone.setAttribute("endRowAddr", str(offsets[last + 1] - 1))
        size = only(table, "sz")
        if size.getAttribute("heightRelTo") == "ABSOLUTE":
            extra = sum((n - 1) * max(int(only(tc, "cellSz").getAttribute("height")) for tc in children(row, "tc"))
                        for row, n in zip(rows, copies) if n > 1)
            size.setAttribute("height", str(int(size.getAttribute("height")) + extra))
        table.setAttribute("rowCnt", str(sum(copies)))
    return changed


def fill_paragraph(p, fields, ids, width, border):
    changed = False
    for part in list(parts(p)):
        if isinstance(part, list):
            rows = matrix(span_text(part), fields)
            if rows is not None:
                insert_table(p, part, rows, ids, width, border)
                changed = True
            else:
                values = forms.replaced_texts([n.data for n in part], fields)
                if values is not None:
                    for n, value in zip(part, values):
                        set_text(n, value)
                    changed = True
        elif not isinstance(part, str):
            fill = fill_table if tag(part) == "tbl" else fill_paragraph
            changed |= fill(part, fields, ids, width, border)
    if changed:
        clear_layout(p)
    return changed


def signature(el):
    # DOM keeps identities stable across in-place edits. Include direct text and attributes.
    return (tuple(el.attributes.items()), tuple((n, n.nodeValue) for n in el.childNodes))


def byte_ranges(data, elements, wanted):
    """Locate intact subtrees without guessing where quoted attributes or nested tags end."""
    parser = expat.ParserCreate()
    ordered = iter(elements)
    stack, ranges = [], {}

    def start(name, attrs):
        stack.append((next(ordered), parser.CurrentByteIndex))

    def end(name):
        el, first = stack.pop()
        if el in wanted:
            last = parser.CurrentByteIndex
            # Expat reports the next byte for <x/>, and the opening '<' for </x>.
            if el.hasChildNodes() or data[last - 2:last] != b"/>":
                last = data.index(b">", last) + 1
            ranges[el] = (first, last)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.Parse(data, True)
    return ranges


def serialize_edits(doc, data, before):
    rendered = doc.toxml(encoding="utf-8", standalone=True)
    # Only splice bytes between XML documents with compatible encodings.
    if (doc.encoding or "utf-8").lower() not in ("utf-8", "utf8") or b"\0" in data[:4]:
        return rendered
    elements = doc.getElementsByTagName("*")
    intact = set()
    for el in reversed(elements):
        if (el in before and signature(el) == before[el]
                and all(child in intact for child in el.childNodes if child.nodeType == Node.ELEMENT_NODE)):
            intact.add(el)
    keep = {el for el in intact if el.parentNode not in intact}
    # An ordered snapshot of the input elements also includes deleted lineseg caches.
    source = byte_ranges(data, before, keep)
    target = byte_ranges(rendered, elements, keep)
    chunks = []
    cursor = 0
    for el, (first, last) in sorted(target.items(), key=lambda item: item[1][0]):
        a, b = source[el]
        chunks.extend((rendered[cursor:first], data[a:b]))
        cursor = last
    chunks.append(rendered[cursor:])
    return b"".join(chunks)


def fill_story(data, fields, ids, border):
    with minidom.parseString(data) as doc:
        width = hw.TEXT_WIDTH
        for page in doc.getElementsByTagNameNS(HP, "pagePr")[:1]:
            margin = only(page, "margin")
            width = int(page.getAttribute("width")) - sum(int(margin.getAttribute(k) or 0) for k in ("left", "right", "gutter"))
            if width <= 0:
                raise ValueError("HWPX 쪽 너비 오류")
        before = {el: signature(el) for el in doc.getElementsByTagName("*")}
        changed = fill_paragraph(doc.documentElement, fields, ids, width, border)
        return serialize_edits(doc, data, before) if changed else data


def write_parts(blank, filled, fields):
    digest, preview = form_content(blank, fields)
    stories, border = layout(blank)
    used = set()
    for name in stories:
        for el in ET.fromstring(xml_bytes(blank, name)).iter():
            for attr in ("id", "instid"):
                value = el.get(attr, "")
                if value.isascii() and value.isdigit():
                    used.add(int(value))
    ids = (i for i in count(1) if i not in used)
    updated = {}
    for name in stories:
        original = xml_bytes(blank, name)
        data = fill_story(original, fields, ids, border)
        if data != original:
            updated[name] = data
    for name in hw.preview_names(blank):
        updated[name] = preview.encode("utf-8")
    for part in blank.infolist():
        data = updated.pop(part.filename, None)
        filled.writestr(part, blank.read(part) if data is None else data)
    return digest
