"""Admission checks shared by the report readers and blank-form writers."""
from __future__ import annotations

from io import BytesIO
import stat
import unicodedata
from xml.parsers import expat

XML_LIMIT = 32 * 1024 * 1024
XML_SUFFIXES = (".xml", ".rels", ".hpf")


def validate_xml(stream):
    # Check declarations and depth before allocating an ElementTree or DOM.
    # Expat reads the XML encoding declaration, including UTF-16 documents.
    depth = 0
    elements = 0

    def start(name, attrs):
        nonlocal depth, elements
        depth += 1
        elements += 1
        if depth > 128 or elements > 250_000:
            raise ValueError("양식 XML 중첩 또는 요소 수 제한 초과")

    def end(name):
        nonlocal depth
        depth -= 1

    def reject_dtd(*args):
        raise ValueError("양식 XML에 DTD를 사용할 수 없습니다")

    parser = expat.ParserCreate()
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = reject_dtd
    parser.ParseFile(stream)


def check_package_size(package):
    entries = package.infolist()
    if len(entries) > 10000 or sum(entry.file_size for entry in entries) > 64 * 1024 * 1024:
        raise ValueError("양식 크기(64 MiB) 또는 항목 수 제한 초과")
    seen = set()
    xml_entries = []
    for entry in entries:
        name = entry.orig_filename.removesuffix("/")
        key = unicodedata.normalize("NFC", name).casefold()
        if (key in seen or any(c in entry.orig_filename for c in "\\\x00:")
                or any(c in ("", ".", "..") for c in name.split("/"))
                or stat.S_ISLNK(entry.external_attr >> 16)):
            raise ValueError("양식 패키지에 중복·비정상 경로 또는 심볼릭 링크가 있습니다")
        seen.add(key)
        if not entry.is_dir() and name.lower().endswith(XML_SUFFIXES):
            if entry.file_size > XML_LIMIT:
                raise ValueError("양식 XML 크기 제한(32 MiB) 초과")
            xml_entries.append(entry)
    # Opaque parts are copied to the recipient too. Inspect XML that is outside
    # the projected stories without materializing a second document tree.
    for entry in xml_entries:
        with package.open(entry) as stream:
            validate_xml(stream)


def xml_bytes(package, name):
    part = package.getinfo(name)
    if part.file_size > XML_LIMIT:
        raise ValueError("양식 XML 크기 제한(32 MiB) 초과")
    data = package.read(part)
    # Content types and manifests may designate XML with a custom extension.
    if not name.lower().endswith(XML_SUFFIXES):
        validate_xml(BytesIO(data))
    return data
