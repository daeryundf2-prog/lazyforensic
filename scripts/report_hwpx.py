"""Write deterministic, single-section OWPML report drafts (KS X 6101)."""
from __future__ import annotations

from itertools import count
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from report_docx import blocks, display_text, draft_text, row_text, xml_text

HWP = "http://www.hancom.co.kr/hwpml/"
ODF = "urn:oasis:names:tc:opendocument:xmlns:"
OPF = "http://www.idpf.org/2007/opf/"
CONTAINER = ODF + "container"
MANIFEST = ODF + "manifest:1.0"
MIME = b"application/hwp+zip"
HP = "{" + HWP + "2011/paragraph}"
HS = "{" + HWP + "2011/section}"

# Header and section use the namespace declarations of native HWPX documents.
NS = {key: HWP + "2011/" + name for key, name in (
    ("ha", "app"), ("hp", "paragraph"), ("hs", "section"), ("hc", "core"),
    ("hh", "head"), ("hhs", "history"), ("hm", "master-page"))}
NS.update(hp10=HWP + "2016/paragraph", ooxmlchart=HWP + "2016/ooxmlchart",
          hwpunitchar=HWP + "2016/HwpUnitChar", hpf="http://www.hancom.co.kr/schema/2011/hpf",
          dc="http://purl.org/dc/elements/1.1/", opf=OPF, epub="http://www.idpf.org/2007/ops",
          config=ODF + "config:1.0")

PAGE_WIDTH, PAGE_HEIGHT = 59528, 84188  # A4, 1/7200 inch units
MARGIN = 5669  # 20 mm
TEXT_WIDTH = PAGE_WIDTH - 2 * MARGIN
LANGS = ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")
# Style ID doubles as character-property ID.
STYLES = (("본문", 1000), ("제목", 1700), ("큰 제목", 1400), ("작은 제목", 1200),
          ("소제목", 1100), ("표 본문", 900), ("표 머리", 900))


def root(tag, **attrs):
    return ET.Element(tag, {**{"xmlns:" + k: v for k, v in NS.items()}, **attrs})


def add(parent, tag, **attrs):
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})


def serialize(element):
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(element, encoding="utf-8")


def header():
    head = root("hh:head", version="1.5", secCnt="1")
    add(head, "hh:beginNum", page=1, footnote=1, endnote=1, pic=1, tbl=1, equation=1)
    refs = add(head, "hh:refList")
    fonts = add(refs, "hh:fontfaces", itemCnt=len(LANGS))
    for lang in LANGS:
        family = add(fonts, "hh:fontface", lang=lang.upper(), fontCnt=2)
        for i, name in enumerate(("함초롬바탕", "맑은 고딕")):
            add(family, "hh:font", id=i, face=name, type="TTF", isEmbedded=0)
    borders = add(refs, "hh:borderFills", itemCnt=2)
    for i, line in enumerate(("NONE", "SOLID"), 1):
        fill = add(borders, "hh:borderFill", id=i, threeD=0, shadow=0, centerLine="NONE", breakCellSeparateLine=0)
        for diagonal in ("slash", "backSlash"):
            add(fill, "hh:" + diagonal, type="NONE", Crooked=0, isCounter=0)
        for side in ("leftBorder", "rightBorder", "topBorder", "bottomBorder", "diagonal"):
            add(fill, "hh:" + side, type=line if side != "diagonal" else "SOLID", width="0.12 mm", color="#777777")
    chars = add(refs, "hh:charProperties", itemCnt=len(STYLES))
    for i, (_, height) in enumerate(STYLES):
        prop = add(chars, "hh:charPr", id=i, height=height, textColor="#000000", borderFillIDRef=1)
        bold = i not in (0, 5)
        for tag, value in (("fontRef", int(bold)), ("ratio", 100), ("spacing", 0), ("relSz", 100), ("offset", 0)):
            add(prop, "hh:" + tag, **dict.fromkeys(LANGS, value))
        if bold:
            add(prop, "hh:bold")
    add(add(refs, "hh:tabProperties", itemCnt=1), "hh:tabPr", id=0, autoTabLeft=0, autoTabRight=0)
    para = add(add(refs, "hh:paraProperties", itemCnt=1), "hh:paraPr", id=0, tabPrIDRef=0)
    add(para, "hh:align", horizontal="LEFT", vertical="BASELINE")
    add(para, "hh:heading", type="NONE", idRef=0, level=0)
    add(para, "hh:breakSetting", breakLatinWord="BREAK_WORD", breakNonLatinWord="BREAK_WORD",
        widowOrphan=0, keepWithNext=0, keepLines=0, pageBreakBefore=0, lineWrap="BREAK")
    add(para, "hh:autoSpacing", eAsianEng=0, eAsianNum=0)
    margin = add(para, "hh:margin")
    for tag in ("intent", "left", "right", "prev", "next"):
        add(margin, "hc:" + tag, value=160 if tag == "next" else 0, unit="HWPUNIT")
    add(para, "hh:lineSpacing", type="PERCENT", value=140, unit="HWPUNIT")
    add(para, "hh:border", borderFillIDRef=1, offsetLeft=0, offsetRight=0, offsetTop=0,
        offsetBottom=0, connect=0, ignoreMargin=0)
    styles = add(refs, "hh:styles", itemCnt=len(STYLES))
    for i, (name, _) in enumerate(STYLES):
        add(styles, "hh:style", id=i, type="PARA", name=name, engName=name, paraPrIDRef=0,
            charPrIDRef=i, nextStyleIDRef=0, langID=1042, lockForm=0)
    add(add(head, "hh:compatibleDocument", targetProgram="HWP201X"), "hh:layoutCompatibility")
    add(head, "hh:trackchangeConfig", flags=0)
    return head


def paragraph(parent, ids, style):
    return add(parent, "hp:p", id=next(ids), paraPrIDRef=0, styleIDRef=style, pageBreak=0, columnBreak=0, merged=0)


def page_settings(para):
    run = add(para, "hp:run", charPrIDRef=0)
    props = add(run, "hp:secPr", id="", textDirection="HORIZONTAL", tabStopVal=4000, tabStopUnit="HWPUNIT")
    add(props, "hp:startNum", pageStartsOn="BOTH", page=0, pic=0, tbl=0, equation=0)
    add(props, "hp:visibility", hideFirstHeader=0, hideFirstFooter=0, hideFirstMasterPage=0,
        border="SHOW_ALL", fill="SHOW_ALL", hideFirstPageNum=0, hideFirstEmptyLine=0, showLineNumber=0)
    page = add(props, "hp:pagePr", landscape="WIDELY", width=PAGE_WIDTH, height=PAGE_HEIGHT, gutterType="LEFT_ONLY")
    add(page, "hp:margin", left=MARGIN, right=MARGIN, top=MARGIN, bottom=MARGIN, header=0, footer=0, gutter=0)
    add(add(run, "hp:ctrl"), "hp:colPr", id="", type="NEWSPAPER", layout="LEFT", colCount=1, sameSz=1, sameGap=0)


def table(run, rows, ids, *, width=TEXT_WIDTH, border=2):
    weights = {"번호": 1, "파일": 5, "크기": 2, "해시": 13, "SHA-256": 8, "보조 해시": 9}
    cols = [weights.get(label, 4) for label in rows[0]]
    total_weight = sum(cols)
    widths = [width * weight // total_weight for weight in cols]
    widths[-1] += width - sum(widths)
    tbl = add(run, "hp:tbl", id=next(ids), zOrder=0, numberingType="TABLE", textWrap="TOP_AND_BOTTOM",
              textFlow="BOTH_SIDES", lock=0, pageBreak="CELL", repeatHeader=1, rowCnt=len(rows),
              colCnt=len(cols), cellSpacing=0, borderFillIDRef=border, noAdjust=0)
    add(tbl, "hp:sz", width=width, widthRelTo="ABSOLUTE", height=1800 * len(rows), heightRelTo="ABSOLUTE", protect=0)
    add(tbl, "hp:pos", treatAsChar=1, affectLSpacing=0, flowWithText=1, allowOverlap=0, holdAnchorAndSO=0,
        vertRelTo="PARA", horzRelTo="COLUMN", vertAlign="TOP", horzAlign="LEFT", vertOffset=0, horzOffset=0)
    add(tbl, "hp:outMargin", left=0, right=0, top=0, bottom=0)
    padding = dict(left=220, right=220, top=120, bottom=120)
    add(tbl, "hp:inMargin", **padding)
    for y, values in enumerate(rows):
        tr = add(tbl, "hp:tr")
        style = 6 if y == 0 else 5
        for x, value in enumerate(values):
            tc = add(tr, "hp:tc", name="", header=int(y == 0), hasMargin=0, protect=0, editable=0, dirty=0, borderFillIDRef=border)
            sub = add(tc, "hp:subList", id="", textDirection="HORIZONTAL", lineWrap="BREAK", vertAlign="CENTER",
                      linkListIDRef=0, linkListNextIDRef=0, textWidth=0, textHeight=0, hasTextRef=0, hasNumRef=0)
            cell_run = add(paragraph(sub, ids, style), "hp:run", charPrIDRef=style)
            add(cell_run, "hp:t").text = xml_text(value)
            add(tc, "hp:cellAddr", colAddr=x, rowAddr=y)
            add(tc, "hp:cellSpan", colSpan=1, rowSpan=1)
            add(tc, "hp:cellSz", width=widths[x], height=1800)
            add(tc, "hp:cellMargin", **padding)


def section(md):
    sec = root("hs:sec")
    ids = count(1)
    for kind, value in blocks(md):
        style = kind.count("#")
        p = paragraph(sec, ids, style)
        if len(sec) == 1:
            page_settings(p)
        run = add(p, "hp:run", charPrIDRef=style)
        if kind == "table":
            table(run, value, ids)
            add(run, "hp:t")
        else:
            add(run, "hp:t").text = xml_text(display_text(value))
    return sec


def content_package():
    package = ET.Element("opf:package", {"xmlns:opf": OPF, "version": "1.0", "unique-identifier": "", "id": ""})
    meta = add(package, "opf:metadata")
    add(meta, "opf:title").text = "디지털포렌식 분석 보고서 (초안)"
    add(meta, "opf:language").text = "ko"
    manifest = add(package, "opf:manifest")
    for name, path in (("header", "Contents/header.xml"), ("section0", "Contents/section0.xml"), ("settings", "settings.xml")):
        add(manifest, "opf:item", id=name, href=path, **{"media-type": "application/xml"})
    spine = add(package, "opf:spine")
    for name in ("header", "section0"):
        add(spine, "opf:itemref", idref=name, linear="yes")
    return package


def write_hwpx(path: Path, md: str) -> None:
    version = ET.Element("hv:HCFVersion", {"xmlns:hv": HWP + "2011/version", "tagetApplication": "WORDPROCESSOR",
                         "major": "5", "minor": "1", "micro": "1", "buildNumber": "0", "os": "1",
                         "xmlVersion": "1.5", "application": "LazyForensic", "appVersion": "1.6.1"})
    container = ET.Element("container", xmlns=CONTAINER)
    roots = add(container, "rootfiles")
    for name, mime in (("Contents/content.hpf", "application/hwpml-package+xml"), ("Preview/PrvText.txt", "text/plain")):
        add(roots, "rootfile", **{"full-path": name, "media-type": mime})
    settings = ET.Element("ha:HWPApplicationSetting", {"xmlns:ha": NS["ha"]})
    add(settings, "ha:CaretPosition", listIDRef=0, paraIDRef=0, pos=0)
    parts = {"version.xml": serialize(version), "META-INF/container.xml": serialize(container),
             "Contents/content.hpf": serialize(content_package()), "Contents/header.xml": serialize(header()),
             "Contents/section0.xml": serialize(section(md)), "settings.xml": serialize(settings),
             "Preview/PrvText.txt": draft_text(md).encode("utf-8")}
    manifest = ET.Element("manifest:manifest", {"xmlns:manifest": MANIFEST})
    media_types = {"Contents/content.hpf": "application/hwpml-package+xml", "Preview/PrvText.txt": "text/plain"}
    add(manifest, "manifest:file-entry", **{"manifest:full-path": "/", "manifest:media-type": MIME.decode()})
    for name in parts:
        mime = media_types.get(name, "application/xml")
        add(manifest, "manifest:file-entry", **{"manifest:full-path": name, "manifest:media-type": mime})
    parts["META-INF/manifest.xml"] = serialize(manifest)
    with ZipFile(path, "w") as package:
        for name, data in {"mimetype": MIME, **parts}.items():
            entry = ZipInfo(name)  # fixed timestamp and platform across hosts
            entry.create_system = 3
            package.writestr(entry, data, compress_type=ZIP_STORED if name == "mimetype" else ZIP_DEFLATED)


def preview_names(package):
    container = package.getinfo("META-INF/container.xml")
    if container.file_size > 32 * 1024 * 1024:
        raise ValueError("HWPX container XML 크기 제한 초과")
    root = ET.fromstring(package.read(container))
    names = {entry.attrib["full-path"] for entry in root.findall(
        "c:rootfiles/c:rootfile[@media-type='text/plain']", {"c": CONTAINER})}
    if "Preview/PrvText.txt" in package.namelist():
        names.add("Preview/PrvText.txt")
    for name in sorted(names):
        if not name.lower().endswith(".txt") or name not in package.namelist():
            raise ValueError("HWPX 텍스트 미리보기 경로 오류")
        yield name


def check_preview(package, text, errors):
    for name in preview_names(package):
        saved = package.read(name).decode("utf-8-sig").replace("\r\n", "\n")
        if saved != text:
            errors.append("HWPX 텍스트 미리보기와 본문이 다름")


def read_hwpx(path: Path) -> str:
    """Read the generated single-section draft in spine order, preserving cell boundaries."""
    def read_xml(name):
        part = package.getinfo(name)
        if part.file_size > 32 * 1024 * 1024:
            raise ValueError("HWPX XML 크기 제한 초과")
        return ET.fromstring(package.read(part))

    with ZipFile(path) as package:
        names = package.namelist()
        if not names or len(set(names)) != len(names) or names[0] != "mimetype":
            raise ValueError("HWPX 패키지 구성 오류")
        mime = package.getinfo("mimetype")
        if (mime.header_offset != 0 or mime.compress_type != ZIP_STORED
                or mime.file_size != len(MIME) or package.read(mime) != MIME):
            raise ValueError("HWPX mimetype 오류")
        container = read_xml("META-INF/container.xml")
        roots = container.findall("c:rootfiles/c:rootfile[@media-type='application/hwpml-package+xml']", {"c": CONTAINER})
        if len(roots) != 1 or container.tag != "{" + CONTAINER + "}container":
            raise ValueError("HWPX 패키지 경로 오류")
        content = read_xml(roots[0].attrib["full-path"])
        ns = {"opf": OPF}
        items = content.findall("opf:manifest/opf:item", ns)
        by_id = {item.attrib["id"]: item.attrib["href"] for item in items}
        spine = [by_id[ref.attrib["idref"]] for ref in content.findall("opf:spine/opf:itemref", ns)]
        if (content.tag != "{" + OPF + "}package" or len(items) != len(by_id)
                or not set(by_id.values()).issubset(names) or spine != ["Contents/header.xml", "Contents/section0.xml"]
                or any(n.startswith("Contents/section") and n != spine[1] for n in names)):
            raise ValueError("단일 섹션 HWPX 초안의 본문 구성이 다름")
        sec = read_xml(spine[1])
    if sec.tag != HS + "sec" or not len(sec):
        raise ValueError("HWPX 본문 없음")

    lines = []
    for para in sec:
        if para.tag != HP + "p":
            raise ValueError("HWPX 문단 구조 오류")
        # The writer anchors each table in its own paragraph. Inspect every text
        # node, including split runs, and reject ambiguous mixed/nested tables.
        tables = list(para.iter(HP + "tbl"))
        if not tables:
            lines.append("".join(paragraph_text(para)))
            continue
        if len(tables) != 1 or tables[0] not in para.findall(HP + "run/" + HP + "tbl"):
            raise ValueError("HWPX 중첩 표 구조 오류")
        table_node = tables[0]
        cells = set()
        rows = table_node.findall(HP + "tr")
        if not rows or len(rows) != int(table_node.attrib["rowCnt"]):
            raise ValueError("HWPX 표 행 수 오류")
        for y, tr in enumerate(rows):
            values = []
            row_cells = tr.findall(HP + "tc")
            if not row_cells or len(row_cells) != int(table_node.attrib["colCnt"]):
                raise ValueError("HWPX 표 열 수 오류")
            for x, tc in enumerate(row_cells):
                addr = tc.find(HP + "cellAddr")
                if addr is None or (addr.get("rowAddr"), addr.get("colAddr")) != (str(y), str(x)):
                    raise ValueError("HWPX 표 셀 위치 오류")
                paragraphs = tc.findall(HP + "subList/" + HP + "p")
                values.append(" ".join("".join(paragraph_text(p)) for p in paragraphs))
                for p in paragraphs:
                    cells.update(p.iter(HP + "t"))
            lines.append(row_text(values))
        if any("".join(t.itertext()) for t in para.iter(HP + "t") if t not in cells):
            raise ValueError("HWPX 표 밖에 추가 본문이 있음")
    return "\n".join(lines)


def paragraph_text(para):
    for t in para.iter(HP + "t"):
        yield t.text or ""
        for child in t:
            if child.tag == HP + "tab":
                yield "\t"
            elif child.tag == HP + "lineBreak":
                yield "\n"
            else:
                raise ValueError("HWPX 텍스트 요소 판독 불가")
            yield child.tail or ""
