#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exif_audit.py — 이미지 EXIF/메타데이터 포렌식 감사

사진 증거의 EXIF를 읽어 촬영일시·기기·GPS·편집 흔적을 표면 조사한다.
Pillow가 없으면 exit 3(fail-closed). 메타데이터 부재 자체가
신호(소셜앱 경유·재저장 가능성)가 될 수 있으므로 결측도 기록한다.

사용:
    python scripts/exif_audit.py photo.jpg
    python scripts/exif_audit.py media/ --json
    python scripts/exif_audit.py media/ -o exif_report.json

보고 항목:
    - DateTimeOriginal + SubSecTimeOriginal + OffsetTimeOriginal(촬영 순간) / Make / Model / Software(편집기 흔적)
    - GPS 여부 / EXIF 완전 부재 여부

JSON(tags)에는 문자열 전체를 그대로 싣는다. 화면 출력만 발췌하며 발췌에는
생략 기호와 원래 글자 수를 붙인다.

알려진 한계:
- EXIF는 자유롭게 편집 가능하다. '기록된 값'이 아니라 '파일에 적힌
  값'으로만 보고할 것. 촬영 시점 확정 수단이 아니다.
- EXIF가 지워진 파일이 '조작됐다'는 뜻이 아니다 — 대부분의 메신저
  경유 이미지는 EXIF가 없다.
- OffsetTimeOriginal이 없으면 촬영 시각의 시간대는 미기록이다. 정렬에
  가정이 필요하면 그 가정을 결과에 표시한다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_json import print_json  # noqa: E402

IMG_EXTS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp", ".heic", ".bmp"}

# Pillow tag id → 이름 (주요 항목만)
TAG_NAMES = {
    0x010F: "Make", 0x0110: "Model", 0x0131: "Software",
    0x0132: "DateTime", 0x9003: "DateTimeOriginal", 0x9004: "DateTimeDigitized",
    0x9010: "OffsetTime", 0x9011: "OffsetTimeOriginal", 0x9012: "OffsetTimeDigitized",
    0x9290: "SubSecTime", 0x9291: "SubSecTimeOriginal", 0x9292: "SubSecTimeDigitized",
    0x8825: "GPSInfo", 0x829A: "ExposureTime", 0x829D: "FNumber",
    0x8827: "ISO", 0xA434: "LensModel",
}
EXIF_DT_RE = re.compile(r"^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})")
OFFSET_RE = re.compile(r"^([+-])(\d{2}):(\d{2})$")
EXCERPT_CHARS = 100


def excerpt(value: str, limit: int = EXCERPT_CHARS) -> str:
    """화면용 발췌. 잘랐으면 생략 기호와 원래 길이를 붙여 발췌임을 드러낸다."""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…(발췌, 원래 {len(value)}자)"


def capture_time(tags: dict) -> dict | None:
    """DateTimeOriginal과 짝 태그(SubSecTimeOriginal·OffsetTimeOriginal)로 촬영 순간을 만든다.

    timestamp: 오프셋이 기록돼 있으면 그 오프셋을 붙인 ISO 8601, 없으면 시간대 없는 ISO 8601.
    소수초는 기록된 자릿수대로(최대 6자리) 싣고 원래 문자열은 subsec에 둔다.
    """
    raw = (tags.get("DateTimeOriginal") or "").strip().rstrip("\x00")
    m = EXIF_DT_RE.match(raw)
    if not m:
        return None
    subsec = (tags.get("SubSecTimeOriginal") or "").strip().rstrip("\x00")
    digits = subsec if subsec.isdigit() else ""
    offset_raw = (tags.get("OffsetTimeOriginal") or "").strip().rstrip("\x00")
    om = OFFSET_RE.match(offset_raw)
    tz = None
    if om and int(om.group(2)) <= 14 and int(om.group(3)) < 60:
        delta = timedelta(hours=int(om.group(2)), minutes=int(om.group(3)))
        tz = timezone(-delta if om.group(1) == "-" else delta)
    try:
        dt = datetime(*[int(g) for g in m.groups()], int((digits + "000000")[:6]), tzinfo=tz)
    except ValueError:
        return None
    spec = "seconds" if not digits else "milliseconds" if len(digits) <= 3 else "microseconds"
    return {"timestamp": dt.isoformat(timespec=spec), "offset": offset_raw if tz else None,
            "subsec": subsec or None, "raw": raw,
            "timezone": "recorded" if tz else "not_recorded"}


def _require_pillow():
    try:
        from PIL import Image  # type: ignore
        return Image
    except ImportError:
        print("Pillow가 없습니다: pip install 'pillow>=10.0,<12'", file=sys.stderr)
        return None


def audit_image(path: Path, Image) -> dict:
    rec: dict = {"file": str(path)}
    try:
        with Image.open(path) as img:
            rec["format"] = img.format
            rec["size"] = f"{img.width}x{img.height}"
            exif = img.getexif()
            if not exif:
                rec["exif_present"] = False
                rec["note"] = "EXIF 없음 — 메신저 경유·재저장 가능성"
                return rec
            rec["exif_present"] = True
            tags = {}
            # 원자료에는 값 전체를 싣는다 (앞부분이 같고 꼬리만 다른 값을 구별해야 한다).
            for tag_id, value in exif.items():
                name = TAG_NAMES.get(tag_id)
                if name:
                    tags[name] = str(value)
            # IFD 안의 DateTimeOriginal 등은 get_ifd로
            try:
                ifd = exif.get_ifd(0x8769)  # ExifIFD
                for tag_id, value in ifd.items():
                    name = TAG_NAMES.get(tag_id)
                    if name:
                        tags[name] = str(value)
            except Exception:  # noqa: BLE001
                pass
            rec["tags"] = tags
            rec["capture_time"] = capture_time(tags)
            rec["has_gps"] = "GPSInfo" in tags or 0x8825 in exif
            rec["has_software"] = "Software" in tags
            if rec["has_software"]:
                rec["note"] = "편집 소프트웨어 흔적 있음 — 원본성 검토 필요"
    except Exception as e:  # noqa: BLE001 — 손상 파일은 실패로 기록
        rec["error"] = str(e)[:200]
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="이미지 EXIF/메타데이터 포렌식 감사")
    ap.add_argument("input", help="이미지 파일 또는 디렉터리")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("-o", "--output", default=None, help="JSON 리포트 출력 경로")
    args = ap.parse_args(argv)

    Image = _require_pillow()
    if Image is None:
        return 3

    target = Path(args.input)
    if target.is_dir():
        files = sorted(p for p in target.rglob("*") if p.suffix.lower() in IMG_EXTS)
    elif target.is_file():
        files = [target]
    else:
        print(f"대상을 찾을 수 없습니다: {target}", file=sys.stderr)
        return 2
    if not files:
        print(f"이미지 파일이 없습니다: {target}", file=sys.stderr)
        return 2

    records = [audit_image(f, Image) for f in files]
    summary = {
        "total": len(records),
        "exif_present": sum(1 for r in records if r.get("exif_present")),
        "exif_absent": sum(1 for r in records if r.get("exif_present") is False),
        "with_gps": sum(1 for r in records if r.get("has_gps")),
        "with_software": sum(1 for r in records if r.get("has_software")),
        "errors": sum(1 for r in records if "error" in r),
        "records": records,
    }

    if args.output:
        Path(args.output).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
        print(f"→ {args.output}")
    elif args.json:
        print_json(summary, indent=2)
    else:
        print(f"{summary['total']}개 이미지: EXIF 있음 {summary['exif_present']}, "
              f"없음 {summary['exif_absent']}, GPS {summary['with_gps']}, "
              f"편집흔적 {summary['with_software']}")
        for r in records:
            if r.get("error"):
                print(f"  [ERR] {r['file']}: {r['error']}")
            elif r.get("exif_present"):
                t = r["tags"]
                shot = r.get("capture_time")
                when = (shot["timestamp"] + ("" if shot["offset"] else " (시간대 미기록)")) if shot else "기록없음"
                print(f"  {r['file']}: {excerpt(t.get('Make', '?'))} {excerpt(t.get('Model', '?'))} "
                      f"촬영 {when}"
                      + (" [GPS]" if r["has_gps"] else "")
                      + (f" [SW:{excerpt(t['Software'])}]" if r.get("has_software") else ""))
            else:
                print(f"  {r['file']}: EXIF 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
