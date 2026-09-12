#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
case_survey.py — 증거 디렉터리 통합 선조사 파이프라인

의뢰인 데이터 폴더 하나를 던지면 이 레포의 결정적 도구들을 순서대로
돌려 단일 리포트를 만든다. 각 단계는 독립적이라 하나가 실패해도
나머지는 계속되고, 실패는 '생략'이 아니라 '실패'로 기록된다.

실행 순서:
    1. evidence_manifest  — 전체 파일 해시 매니페스트 (변경 탐지 기준)
    2. pii_mask (탐지만)  — 외부 공유 전 알아야 할 개인정보 분포
    3. keyword_report     --keywords 지정 시
    4. audio_survey       — 발화 구간 후보 지도
    5. exif_audit         — 이미지 메타데이터 (Pillow 필요)
    6. image_similarity   — 유사 이미지 쌍 (Pillow 필요)
    7. video_fingerprint  — 영상 지문 목록 (ffmpeg 필요)
    8. local_stt          --stt 지정 시 (로컬 엔진 필요)

사용:
    python scripts/case_survey.py case/ -o survey.json
    python scripts/case_survey.py case/ --keywords "계좌,송금" --stt -o survey.json
    python scripts/case_survey.py case/ --markdown survey.md

알려진 한계:
- 각 단계의 한계는 개별 스크립트와 docs/GAPS.md를 따른다.
- 이 리포트는 '표면 조사'다 — 카빙·MFT·메모리 분석은 BYO 도구 영역.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
SCRIPTS = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_step(name: str, fn) -> dict:
    """각 단계를 감싸 예외를 '실패' 기록으로 바꾼다."""
    buf_out, buf_err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            result = fn()
        return {"status": "ok", "result": result}
    except SystemExit as e:
        return {"status": "failed", "exit": e.code,
                "stderr": buf_err.getvalue()[-500:]}
    except Exception as e:  # noqa: BLE001 — 단계 실패가 전체를 멈추지 않게
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


def survey(root: Path, keywords: list[str], run_stt: bool) -> dict:
    report: dict = {
        "survey_version": 1,
        "generated_at": datetime.now(KST).isoformat(),
        "root": str(root.resolve()),
        "steps": {},
    }

    def step_manifest():
        mod = _load("evidence_manifest")
        m = mod.build_manifest(root)
        return {"file_count": m["file_count"], "files": m["files"]}
    report["steps"]["manifest"] = _run_step("manifest", step_manifest)

    def step_pii():
        mod = _load("pii_mask")
        text_exts = mod.TEXT_EXTS
        files = [p for p in root.rglob("*")
                 if p.is_file() and p.suffix.lower() in text_exts]
        findings = [mod.process_file(f, mask=False, out_path=None, in_place=False)
                    for f in sorted(files)]
        total = sum(r.get("pii_count", 0) for r in findings)
        return {"files_scanned": len(files), "total_pii": total,
                "hits": [r for r in findings if r.get("pii_count")]}
    report["steps"]["pii"] = _run_step("pii", step_pii)

    if keywords:
        def step_keywords():
            mod = _load("keyword_report")
            searched, results = [], []
            for f in sorted(root.rglob("*")):
                if not f.is_file() or f.suffix.lower() not in mod.TEXT_EXTS:
                    continue
                searched.append(str(f))
                hits = mod.search_file(f, keywords, context=0)
                if hits:
                    results.append({"file": str(f), "sha256": mod.sha256_file(f),
                                    "hit_count": len(hits), "hits": hits})
            return {"keywords": keywords, "files_searched": len(searched),
                    "total_hits": sum(r["hit_count"] for r in results),
                    "results": results}
        report["steps"]["keywords"] = _run_step("keywords", step_keywords)

    def step_audio():
        mod = _load("audio_survey")
        files = [p for p in root.rglob("*")
                 if p.is_file() and p.suffix.lower() in mod.AUDIO_EXTS]
        return {"audio_files": len(files),
                "surveys": [mod.survey(f, mod.DEFAULT_THRESHOLD) for f in sorted(files)]}
    report["steps"]["audio"] = _run_step("audio", step_audio)

    def step_exif():
        mod = _load("exif_audit")
        Image = mod._require_pillow()
        if Image is None:
            raise RuntimeError("Pillow 없음 — exif_audit 생략")
        files = [p for p in root.rglob("*")
                 if p.is_file() and p.suffix.lower() in mod.IMG_EXTS]
        return {"image_files": len(files),
                "records": [mod.audit_image(f, Image) for f in sorted(files)]}
    report["steps"]["exif"] = _run_step("exif", step_exif)

    def step_images():
        mod = _load("image_similarity")
        Image = mod._require_pillow()
        if Image is None:
            raise RuntimeError("Pillow 없음 — image_similarity 생략")
        records = mod.scan_dir(root, Image)
        return {"image_files": len(records),
                "similar_pairs": mod.find_pairs(records, 10)}
    report["steps"]["similar_images"] = _run_step("similar_images", step_images)

    def step_videos():
        mod = _load("video_fingerprint")
        if not mod._require_ffmpeg():
            raise RuntimeError("ffmpeg 없음 — video_fingerprint 생략")
        files = [p for p in root.rglob("*")
                 if p.is_file() and p.suffix.lower() in mod.VIDEO_EXTS]
        fps = []
        for f in sorted(files):
            try:
                fp = mod.fingerprint(f)
                fp["frame_hashes"] = [format(h, "016x") for h in fp["frame_hashes"]]
                fps.append(fp)
            except RuntimeError as e:
                fps.append({"file": str(f), "error": str(e)})
        return {"video_files": len(files), "fingerprints": fps}
    report["steps"]["videos"] = _run_step("videos", step_videos)

    if run_stt:
        def step_stt():
            mod = _load("local_stt")
            engine = mod.detect_engine()
            if engine is None:
                raise RuntimeError("로컬 STT 엔진 없음 — 전사 생략")
            files = [p for p in root.rglob("*")
                     if p.is_file() and p.suffix.lower() in mod.AUDIO_EXTS]
            out = []
            for f in sorted(files):
                try:
                    r = mod.transcribe_file(f, engine, "ko", verbatim=False, model_size="turbo")
                    hits = mod.keyword_hits(r["segments"], keywords) if keywords else []
                    out.append({"file": str(f), "engine": r["engine"],
                                "segment_count": len(r["segments"]),
                                "keyword_hits": hits})
                except Exception as e:  # noqa: BLE001
                    out.append({"file": str(f), "error": str(e)[:300]})
            return {"engine": engine, "files": out}
        report["steps"]["stt"] = _run_step("stt", step_stt)

    return report


def to_markdown(report: dict) -> str:
    lines = [f"# 증거 선조사 리포트 — {report['root']}",
             f"생성: {report['generated_at']}", ""]
    names = {
        "manifest": "파일 매니페스트", "pii": "개인정보 탐지",
        "keywords": "키워드 검색", "audio": "오디오 발화 구간",
        "exif": "이미지 EXIF", "similar_images": "유사 이미지",
        "videos": "영상 지문", "stt": "로컬 STT",
    }
    for key, step in report["steps"].items():
        title = names.get(key, key)
        if step["status"] != "ok":
            lines.append(f"## {title}: 실패 — {step.get('error') or step.get('stderr', '')[:120]}")
            continue
        r = step["result"]
        if key == "manifest":
            lines.append(f"## {title}: {r['file_count']}개 파일")
        elif key == "pii":
            lines.append(f"## {title}: {r['total_pii']}건 탐지 ({r['files_scanned']}개 파일)")
            for h in r["hits"]:
                lines.append(f"- {h['file']}: {h['pii_count']}건")
        elif key == "keywords":
            lines.append(f"## {title}: {r['total_hits']}히트 ({r['files_searched']}개 파일 검색)")
            for res in r["results"]:
                lines.append(f"- {res['file']}: {res['hit_count']}히트")
        elif key == "audio":
            for s in r["surveys"]:
                if s["status"] == "ok":
                    lines.append(f"## {title}: {s['file']} — 발화 {s['speech_ratio']*100:.0f}%")
        elif key == "exif":
            present = sum(1 for x in r["records"] if x.get("exif_present"))
            lines.append(f"## {title}: {r['image_files']}개 이미지, EXIF 있음 {present}")
        elif key == "similar_images":
            lines.append(f"## {title}: {r['image_files']}개 이미지, 유사 쌍 {len(r['similar_pairs'])}건")
        elif key == "videos":
            lines.append(f"## {title}: {r['video_files']}개 영상 지문 생성")
        elif key == "stt":
            for f in r["files"]:
                if "error" in f:
                    lines.append(f"## {title}: {f['file']} 실패")
                else:
                    lines.append(f"## {title}: {f['file']} — {f['segment_count']}세그먼트, "
                                 f"키워드 히트 {len(f.get('keyword_hits', []))}건")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="증거 디렉터리 통합 선조사 (로컬 전용)")
    ap.add_argument("root", help="증거 디렉터리")
    ap.add_argument("--keywords", default=None, help="쉼표 구분 키워드")
    ap.add_argument("--stt", action="store_true", help="로컬 STT 전사까지 실행")
    ap.add_argument("-o", "--output", default=None, help="JSON 리포트 경로")
    ap.add_argument("--markdown", default=None, help="마크다운 리포트 경로")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"디렉터리를 찾을 수 없습니다: {root}", file=sys.stderr)
        return 2

    keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else []
    report = survey(root, keywords, args.stt)

    failed = [k for k, v in report["steps"].items() if v["status"] != "ok"]
    out = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(to_markdown(report), encoding="utf-8")
    if not args.output and not args.markdown:
        print(out)

    print(f"선조사 완료 — 단계 {len(report['steps'])}개 중 실패 {len(failed)}개"
          + (f" ({', '.join(failed)})" if failed else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
