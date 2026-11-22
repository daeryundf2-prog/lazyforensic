"""Logical evidence entry trees and byte ranges, using the libewf EWF specification."""
import hashlib
import re
import struct
import zlib

if __package__:
    from .ewf2 import EwfError, MAX_METADATA, read_exact
else:
    from ewf2 import EwfError, MAX_METADATA, read_exact


def read_ltree(f, length):
    if not 48 <= length <= MAX_METADATA + 48:
        raise EwfError("unsupported ltree size")
    header = read_exact(f, 48)
    size, checksum = struct.unpack_from("<QI", header, 16)
    if size != length - 48 or zlib.adler32(header[:24] + bytes(4) + header[28:]) != checksum:
        raise EwfError("invalid ltree length or header checksum")
    tree = read_exact(f, size)
    if hashlib.md5(tree).digest() != header[:16]:
        raise EwfError("ltree MD5 mismatch")
    return tree


def number(value, field, base=10):
    pattern = r"[0-9]{1,20}" if base == 10 else r"[0-9a-fA-F]{1,16}"
    if not re.fullmatch(pattern, value):
        raise EwfError(f"invalid logical {field}")
    return int(value, base)


def extents(row, size, total):
    words = row["be"].split()
    if not words:
        raise EwfError("missing logical binary extents")
    count = number(words[0], "extent count")
    if len(words) != 1 + 2 * count:
        raise EwfError("unsupported typed or incomplete logical extents")
    runs = []
    for i in range(1, len(words), 2):
        offset = number(words[i], "extent offset", 16)
        length = number(words[i + 1], "extent length", 16)
        if offset + length > total:
            raise EwfError("logical extent exceeds the media stream")
        runs.append((offset, length))
    flags = number(row.get("opr") or "0", "entry flags")
    duplicate = row.get("du")
    if flags & 0x04000000:
        if len(runs) != 1 or runs[0][1] != 1 or duplicate in (None, "", "-1"):
            raise EwfError("unsupported sparse logical file without a duplicate extent")
        offset = number(duplicate, "duplicate offset")
        if offset + size > total:
            raise EwfError("logical duplicate exceeds the media stream")
        return [(offset, size)]
    if duplicate not in (None, "", "-1"):
        raise EwfError("unsupported logical duplicate layout")
    length = sum(n for _, n in runs)
    if size == 0 and length in (0, 1):
        return []
    if length != size:
        raise EwfError("file size disagrees with logical extents")
    return runs


def parse_tree(raw):
    if raw is None:
        raise EwfError("missing logical single files / ltree section")
    text = raw.decode("utf-16-le").lstrip("\ufeff").rstrip("\x00\n").replace("\r\n", "\n")
    first, _, body = text.partition("\n")
    parts = body.split("\n\n")
    groups = {}
    for part in parts:
        name, _, content = part.partition("\n")
        if name not in ("rec", "entry", "perm", "srce", "sub"):
            raise EwfError(f"unsupported logical metadata category {name!r}")
        if name in groups:
            raise EwfError("duplicate logical metadata category")
        groups[name] = content.split("\n")
    if number(first, "category count") != len(groups) or not {"rec", "entry"} <= groups.keys():
        raise EwfError("incomplete logical categories")
    rec = groups["rec"]
    if len(rec) != 2 or "tb" not in rec[0].split("\t"):
        raise EwfError("missing logical stream length")
    total = number(dict(zip(rec[0].split("\t"), rec[1].split("\t"))).get("tb", ""), "stream length")
    lines = groups["entry"]
    if len(lines) < 4 or len(lines) % 2 or not re.fullmatch(r"[0-9]+\t1", lines[0]):
        raise EwfError("incomplete logical entry tree")
    keys = lines[1].split("\t")
    if not {"p", "n", "ls", "be"} <= set(keys) or len(keys) != len(set(keys)):
        raise EwfError("unsupported logical entry columns")
    entries = []
    path_chars = 0
    ranges = []
    # Each stack item is a directory path and its number of unvisited children.
    parents = [("", 1)]
    for index in range(2, len(lines), 2):
        while parents and parents[-1][1] == 0:
            parents.pop()
        children = lines[index].split("\t")
        values = lines[index + 1].split("\t")
        if not parents or len(children) != 2 or children[0] not in ("0", "26") or len(values) != len(keys):
            raise EwfError("invalid logical tree structure")
        row = dict(zip(keys, values))
        child_count = number(children[1], "child count")
        if row["p"] not in ("", "0", "1") or (child_count and row["p"] != "1"):
            raise EwfError("unsupported logical entry type")
        parent, left = parents[-1]
        parents[-1] = parent, left - 1
        name = row["n"].translate({1: "\n", 2: "\r", 3: "\t"})
        path_chars += len(parent) + len(name) + 1
        if path_chars > MAX_METADATA:
            raise EwfError("unsupported expanded logical path size")
        path = f"{parent}/{name}" if parent else name
        size = number(row["ls"] or "0", "file size")
        if not row["ls"] and row["p"] != "1":
            raise EwfError("missing logical file size")
        runs = extents(row, size, total)
        if index == 2:
            if row["p"] != "1" or size:
                raise EwfError("unsupported logical root")
            path = ""
        else:
            stored = {}
            for key, algo, length in (("ha", "md5", 32), ("sha", "sha1", 40)):
                value = row.get(key, "")
                if value and not re.fullmatch(f"[0-9a-fA-F]{{{length}}}", value):
                    raise EwfError(f"invalid logical {algo}")
                if value.strip("0"):
                    stored[algo] = value.lower()
            dates = {"cr": "created", "ac": "accessed", "wr": "written", "mo": "entry_modified",
                     "dl": "deleted", "aq": "acquired"}
            times = {label: row[k] for k, label in dates.items() if row.get(k)}
            entries.append(dict(id=len(entries), path=path, name=name, size=size,
                                type="directory" if row["p"] == "1" else "file",
                                timestamps=times, stored_hashes=stored))
            ranges.append(runs)
        if child_count:
            parents.append((path, child_count))
    if any(left for _, left in parents):
        raise EwfError("missing logical child entries")
    return total, entries, ranges
