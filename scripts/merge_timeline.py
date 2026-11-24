#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_timeline.py — 다중 소스 시각을 단일 events.json으로 병합

forensic-timeline(--input events.json)에 넣을 정규화 이벤트 목록을 만든다.
지원 소스:

  --manifest m.json     evidence_manifest.py 출력 → 파일 수정시각(mtime)
  --kakao k.json        parse_kakao.py 출력 → 메시지 수발신 시각
  --exif e.json         exif_audit.py --json 출력 → DateTimeOriginal(+SubSecTimeOriginal·OffsetTimeOriginal)
  --stt t.json          local_stt.py 전사 JSON → 발화 구간
  --plaso p.{jsonl,csv} log2timeline 슈퍼 타임라인 출력 → 통합 이벤트

STT 구간의 시각은 '녹음 파일 내 상대 초'다. 벽시계로 올리려면
기준 시각을 명시해야 한다:

  --stt call.wav.transcript.json --anchor call.wav="2026-09-11T14:03:00+09:00"

--anchor 없이 들어온 STT는 events에 넣지 않고 'unanchored' 목록으로
분리해 보고한다 — 상대 초를 벽시계로 지어내지 않는다.

사용:
    python scripts/merge_timeline.py --manifest m.json --kakao k.json -o events.json
    python scripts/merge_timeline.py ... -o events.json && \\
      python skills/forensic-timeline/scripts/generate_timeline.py \\
        --input events.json --output timeline.html

알려진 한계:
- mtime·EXIF·카톡 시각은 '기록된 값'이지 진실이 아니다 — 타임스탬프
  위조 가능성은 별도 검증 대상.
- 카톡보내기 초 단위 없는 포맷은 초가 00으로 표기된다(파서 한계).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from exif_audit import capture_time, event_fields, zone_note  # noqa: E402
from console_json import print_json  # noqa: E402

KST = timezone(timedelta(hours=9))
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$")


def _parse_ns(raw) -> tuple[int, int] | None:
    """시간대가 붙은 ISO 8601 → (정수 나노초, 정밀도 ns). 시간대가 없거나 형식이 다르면 None.

    부동소수 초나 datetime(마이크로초)을 거치지 않으므로 소수 9자리까지 그대로 비교한다.
    """
    m = _ISO_RE.match(str(raw or "").strip())
    if not m:
        try:  # 다른 ISO 표기 (마이크로초까지)
            dt = datetime.fromisoformat(str(raw or "").strip().replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        if dt.tzinfo is None:
            return None
        return (dt - EPOCH) // timedelta(microseconds=1) * 1000, 1000
    if not m.group(4):
        return None
    day, clock, frac, zone = m.groups()
    try:
        whole = datetime.fromisoformat(f"{day}T{clock}{'+00:00' if zone == 'Z' else zone}")
    except ValueError:
        return None
    frac = frac or ""
    return ((whole - EPOCH) // timedelta(seconds=1) * 10**9 + int((frac + "0" * 9)[:9]),
            10 ** (9 - min(len(frac), 9)))


def event_instant(ev: dict) -> tuple[int, int] | None:
    """이벤트의 정렬 기준 (정수 나노초, 정밀도 ns). 보고서·timeline·양식이 같은 값을 쓴다.

    우선순위: instant_ns(EXIF 원문 소수부로 계산) → mtime_ns(운영체제 원값) → timestamp.
    시간대가 없는 timestamp는 순간을 정할 수 없으므로 None (맨 뒤에 둔다).
    """
    if isinstance(ev.get("instant_ns"), int):
        return ev["instant_ns"], ev.get("precision_ns") or 1
    if isinstance(ev.get("mtime_ns"), int):
        return ev["mtime_ns"], 1
    return _parse_ns(ev.get("timestamp"))


def order_events(events: list[dict]) -> list[dict]:
    """정수 나노초로 시간순 정렬(같은 값은 입력 순서 유지)하고 구별할 수 없는 이웃을 동률로 표시한다.

    두 이벤트를 더 거친 쪽 정밀도로 버림한 값이 같으면 선후를 알 수 없으므로 order_tie를 단다.
    """
    keyed = [(event_instant(ev), ev) for ev in events]
    keyed.sort(key=lambda kv: (kv[0] is None, kv[0][0] if kv[0] else 0))
    for (a, ev_a), (b, ev_b) in zip(keyed, keyed[1:]):
        if a and b:
            step = max(a[1], b[1])
            if a[0] // step == b[0] // step:
                ev_a["order_tie"] = ev_b["order_tie"] = True
    return [ev for _, ev in keyed]


def _load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _norm_iso(raw: str) -> str | None:
    """'YYYY-MM-DD HH:MM:SS' 또는 ISO → ISO(+09:00). 실패 시 None."""
    s = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.isoformat()
    except ValueError:
        pass
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$", s)
    if m:
        y, mo, d, h, mi, sec = m.groups()
        return datetime(int(y), int(mo), int(d), int(h), int(mi),
                        int(sec or 0), tzinfo=KST).isoformat()
    return None


def events_from_manifest(path: str) -> list[dict]:
    data = _load_json(path)
    files = data.get("files", [])
    out = []
    for f in files:
        ts = _norm_iso(f.get("mtime", ""))
        ev = {"timestamp": ts or f.get("mtime", ""), "source": "파일시스템",
              "description": f"파일 수정(기록값): {f['path']}",
              "file": f["path"], "sha256": f.get("sha256", "")[:16]}
        if isinstance(f.get("mtime_ns"), int):
            # timestamp는 마이크로초까지다. 정렬과 원값은 운영체제가 돌려준 정수 나노초로 한다.
            ev["mtime_ns"] = f["mtime_ns"]
        out.append(ev)
    return out


def events_from_kakao(path: str) -> list[dict]:
    data = _load_json(path)
    records = data.get("records", data) if isinstance(data, dict) else data
    out = []
    for r in records:
        if r.get("type") != "message":
            continue
        ts = _norm_iso(r.get("timestamp") or "")
        if ts is None:
            continue  # 시각 불명 메시지는 타임라인에 올리지 않는다
        out.append({"timestamp": ts, "source": "카카오톡",
                    "description": f"{r.get('sender', '?')}: "
                                   f"{str(r.get('message', ''))[:80]}",
                    "file": path})
    return out


def events_from_exif(path: str) -> list[dict]:
    data = _load_json(path)
    records = data.get("records", []) if isinstance(data, dict) else data
    out = []
    for r in records:
        shot = capture_time(r.get("tags") or {})
        if shot is None:
            continue
        ev = {"timestamp": shot["timestamp"], "source": "EXIF",
              "description": f"촬영(기록값, {zone_note(shot)}): {Path(r['file']).name}",
              "file": r["file"], **event_fields(shot)}
        if not shot["offset"]:
            # 오프셋이 없거나 형식이 맞지 않으면 KST를 가정하고, 가정했다고 남긴다.
            ev["timestamp"] = shot["timestamp"] + "+09:00"
            ev["note"] = ("OffsetTimeOriginal 형식 오류 — KST로 가정" if shot["offset_status"] == "malformed"
                          else "OffsetTimeOriginal 미기록 — KST로 가정")
        out.append(ev)
    return out


def events_from_stt(path: str, anchors: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """(anchored 이벤트, unanchored 기록)를 반환한다."""
    data = _load_json(path)
    segs = data.get("segments", [])
    # 전사 파일명에서 원본 오디오명 추정: x.wav.transcript.json → x.wav
    stem = Path(path).name
    audio_name = stem[:-len(".transcript.json")] if stem.endswith(".transcript.json") else stem
    anchor_iso = anchors.get(audio_name) or anchors.get(stem)
    anchored, unanchored = [], []
    for s in segs:
        if s.get("start") is None:
            unanchored.append({"file": audio_name, "text": s.get("text", "")[:80],
                               "reason": "엔진이 타임스탬프 미제공"})
            continue
        if anchor_iso is None:
            unanchored.append({"file": audio_name,
                               "offset_sec": s["start"],
                               "text": s.get("text", "")[:80],
                               "reason": "녹음 기준시각 미지정"})
            continue
        base = datetime.fromisoformat(anchor_iso)
        ts = base + timedelta(seconds=float(s["start"]))
        anchored.append({"timestamp": ts.isoformat(), "source": "STT(앵커 추정)",
                         "description": f"발화: {s.get('text', '')[:80]}",
                         "file": audio_name,
                         "note": f"기준 {anchor_iso} + {s['start']}s"})
    return anchored, unanchored


def _plaso_record_event(rec: dict, path: str) -> dict | None:
    """Plaso(json_line/CSV) 레코드 한 건을 통합 이벤트로 변환. 시각 없으면 None."""
    ts = None
    # plaso json_line: timestamp는 epoch microseconds, datetime은 ISO 문자열
    raw_ts = rec.get("timestamp")
    if raw_ts is not None:
        try:
            ts = datetime.fromtimestamp(int(raw_ts) / 1_000_000, tz=timezone.utc).isoformat()
        except (ValueError, TypeError, OSError):
            ts = None
    if ts is None:
        for key in ("datetime", "date_time"):
            if rec.get(key):
                ts = _norm_iso(str(rec[key]))
                if ts:
                    break
    if ts is None:
        return None
    desc = rec.get("message") or rec.get("display_name") or rec.get("filename") or "(항목)"
    return {
        "timestamp": ts,
        "source": f"plaso:{rec.get('parser') or rec.get('source') or 'unknown'}",
        "description": f"[{rec.get('timestamp_desc', 'event')}] {str(desc)[:80]}",
        "file": path,
    }


def events_from_plaso(path: str) -> tuple[list[dict], dict]:
    """log2timeline 출력(JSONL 또는 CSV) → (통합 이벤트 목록, 입력·채택·탈락 집계).

    탈락한 레코드는 줄 번호와 이유를 남긴다 — 결과 건수만으로 입력 전체를 반영했다고 오인하지 않게."""
    import csv
    import io

    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    records: list[tuple[int, dict]] = []
    rejected: list[dict] = []
    if raw.lstrip().startswith("{"):
        for n, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                rejected.append({"line": n, "reason": "JSON 해석 실패(미완성·손상된 줄)"})
                continue
            if not isinstance(rec, dict):
                rejected.append({"line": n, "reason": "JSON 객체가 아님"})
                continue
            records.append((n, rec))
    else:
        reader = csv.DictReader(io.StringIO(raw))
        records = [(reader.line_num, row) for row in reader]
    out = []
    for n, r in records:
        ev = _plaso_record_event(r, path)
        if ev:
            out.append(ev)
        else:
            rejected.append({"line": n, "reason": "시각 없음 또는 해석 불가"})
    rejected.sort(key=lambda r: r["line"])
    # 입력 레코드 하나는 채택 또는 탈락 중 정확히 하나로 집계된다.
    stats = {"source": "plaso", "file": path, "records": len(out) + len(rejected),
             "accepted": len(out), "rejected": rejected}
    return out, stats


def merge(manifests, kakaos, exifs, stts, anchors, plasos=None) -> dict:
    events, unanchored, inputs = [], [], []
    for p in manifests:
        events += events_from_manifest(p)
    for p in kakaos:
        events += events_from_kakao(p)
    for p in exifs:
        events += events_from_exif(p)
    for p in plasos or []:
        plaso_events, stats = events_from_plaso(p)
        events += plaso_events
        inputs.append(stats)
    for p in stts:
        a, u = events_from_stt(p, anchors)
        events += a
        unanchored += u
    return {"events": order_events(events), "unanchored_stt": unanchored, "inputs": inputs}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="다중 소스 시각 → events.json 병합")
    ap.add_argument("--manifest", action="append", default=[])
    ap.add_argument("--kakao", action="append", default=[])
    ap.add_argument("--exif", action="append", default=[])
    ap.add_argument("--stt", action="append", default=[])
    ap.add_argument("--plaso", action="append", default=[],
                    help="log2timeline 출력(JSONL/CSV) — 슈퍼 타임라인 병합")
    ap.add_argument("--anchor", action="append", default=[],
                    help='오디오파일명="ISO시각" — STT 상대시각의 벽시계 기준')
    ap.add_argument("-o", "--output", default=None, help="events.json 경로")
    ap.add_argument("--report", default=None, help="병합 요약 JSON 경로")
    args = ap.parse_args(argv)

    if not (args.manifest or args.kakao or args.exif or args.stt or args.plaso):
        print("입력 소스가 없습니다. --manifest/--kakao/--exif/--stt/--plaso 중 하나 이상 지정.",
              file=sys.stderr)
        return 2

    anchors = {}
    for a in args.anchor:
        if "=" not in a:
            print(f"--anchor 형식 오류(파일=시각 필요): {a}", file=sys.stderr)
            return 2
        k, v = a.split("=", 1)
        anchors[k.strip()] = v.strip().strip('"')

    merged = merge(args.manifest, args.kakao, args.exif, args.stt, anchors, plasos=args.plaso)

    out = json.dumps(merged["events"], ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        print_json(merged["events"], indent=2)
    if args.report:
        Path(args.report).write_text(json.dumps({
            "event_count": len(merged["events"]),
            "inputs": merged["inputs"],
            "unanchored_stt": merged["unanchored_stt"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    for stats in merged["inputs"]:
        print(f"{stats['source']} {stats['file']}: 입력 {stats['records']}건 중 채택 {stats['accepted']}건, "
              f"탈락 {len(stats['rejected'])}건", file=sys.stderr)
        for r in stats["rejected"]:
            print(f"  탈락 {r['line']}행: {r['reason']}", file=sys.stderr)
    n_un = len(merged["unanchored_stt"])
    print(f"이벤트 {len(merged['events'])}건 병합"
          + (f", STT 상대시각 {n_un}건은 미배치(--anchor 필요)" if n_un else ""),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
