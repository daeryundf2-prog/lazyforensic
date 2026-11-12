#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scene_cuts.py — 긴 영상 장면 컷 선조사 (PySceneDetect, 로컬 전용)

긴 CCTV/블랙박스/녹화 영상을 통째로 보기 전에 "샷(장면) 목록"을 먼저 만든다.
각 샷의 시작/끝 시각과 프레임 번호를 JSON으로 내며, 컷 지점은 후보이지
편집·조작 여부의 판정이 아니다(점진 전환·조명 변화는 놓치거나 과검출될 수 있다).

PySceneDetect는 선택 의존성이다. 없으면 설치 안내만 출력하고 exit 3 (자동 설치 안 함).
    pip install "scenedetect[opencv-headless]"

사용:
    python scripts/scene_cuts.py video.mp4
    python scripts/scene_cuts.py video.mp4 --out shots.json
    python scripts/scene_cuts.py video.mp4 --detector content --threshold 15 --min-scene-len 15

기본 검출기는 adaptive다. 저대비 컷(비슷한 밝기의 장면 전환)은 content 기본 임계값(27)이 놓친다.

종료 코드: 0 성공 / 2 입력 없음 / 3 PySceneDetect 미설치 / 1 분석 실패
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

INSTALL_HINT = (
    "PySceneDetect가 설치돼 있지 않아 장면 컷을 만들지 않고 종료합니다.\n"
    "설치 예 (시스템 Python이 아닌 venv 권장):\n"
    '  pip install "scenedetect[opencv-headless]"'
)


def detect_shots(path: Path, detector: str = "adaptive", threshold: float | None = None,
                 min_scene_len: int = 15) -> dict:
    from scenedetect import detect, open_video, AdaptiveDetector, ContentDetector  # type: ignore

    if detector == "adaptive":
        det = AdaptiveDetector(min_scene_len=min_scene_len,
                               **({"adaptive_threshold": threshold} if threshold else {}))
    else:
        det = ContentDetector(min_scene_len=min_scene_len,
                              **({"threshold": threshold} if threshold else {}))
    scenes = detect(str(path), det, show_progress=False)
    shots = [
        {
            "index": i + 1,
            "start_sec": round(s.seconds, 3),
            "end_sec": round(e.seconds, 3),
            "start_frame": s.frame_num,
            "end_frame": e.frame_num,
        }
        for i, (s, e) in enumerate(scenes)
    ]
    if not shots:
        total = open_video(str(path)).duration
        shots = [{"index": 1, "start_sec": 0.0, "end_sec": round(total.seconds, 3),
                  "start_frame": 0, "end_frame": total.frame_num}]
    return {
        "file": str(path),
        "detector": detector,
        "threshold": threshold,
        "min_scene_len": min_scene_len,
        "shot_count": len(shots),
        "cut_points_sec": [x["start_sec"] for x in shots[1:]],
        "shots": shots,
        "note": "컷 지점은 후보이며 편집·조작 판정이 아니다.",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PySceneDetect 기반 장면 컷(샷 목록) 선조사")
    ap.add_argument("input", help="영상 파일")
    ap.add_argument("--detector", choices=("content", "adaptive"), default="adaptive")
    ap.add_argument("--threshold", type=float, default=None, help="검출 임계값 (미지정 시 라이브러리 기본)")
    ap.add_argument("--min-scene-len", type=int, default=15, help="최소 샷 길이(프레임)")
    ap.add_argument("--out", default=None, help="JSON 출력 경로 (미지정 시 stdout)")
    args = ap.parse_args(argv)

    if importlib.util.find_spec("scenedetect") is None:
        print(INSTALL_HINT, file=sys.stderr)
        return 3
    path = Path(args.input)
    if not path.is_file():
        print(f"입력을 찾을 수 없습니다: {path}", file=sys.stderr)
        return 2
    try:
        result = detect_shots(path, args.detector, args.threshold, args.min_scene_len)
    except Exception as e:  # noqa: BLE001 — 분석 실패는 빈 결과가 아니라 실패로 보고
        print(f"[FAIL] {path}: {e}", file=sys.stderr)
        return 1

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[OK] {path} — 샷 {result['shot_count']}개 → {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
