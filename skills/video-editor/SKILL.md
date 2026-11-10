---
name: video-editor
description: 동영상 구간 자르기·필름스트립/파형. 원본은 수정하지 않는다. 법원 제출본·Manim 교육영상에는 쓰지 말 것.
---

# video-editor

원본은 그대로 두고 `_extracted` / `_enhanced` 사본만 만든다.
그레이딩·오버레이·자막 적용 구간은 재인코딩된다. 중간 산출물 결합의 stream copy를 전체 파이프라인의 무손실 처리로 표현하지 않는다.

```bash
python skills/video-editor/helpers/timeline_view.py "<동영상경로>" <시작초> <종료초> -o timeline_strip.png --n-frames 12
python skills/video-editor/helpers/render.py edl.json -o extracted_clip.mp4 --build-subtitles
```

Manim 참고는 사용자가 교육용 애니메이션을 명시한 경우에만 `manim-video/REFERENCE.md`다. 스킬 피커에 올리지 않는다.

가공 이력(구간, 필터)을 보고서에 적는다. `invoke_subagent` `Model: "flash"`로 클립을 만들고 부모는 원본 경로가 그대로인지 확인한다.

## 계약

| 항목 | 내용 |
|---|---|
| Intent | 동영상 구간 자르기와 필름스트립/파형을 만든다. |
| Scope | 원본은 수정하지 않고 사본에 출력한다. 법원 제출본이나 교육용 Manim 영상은 만들지 않는다. |
| Evidence | 원본 해시와 산출물 경로. |
| Stop | 산출물을 전달하면 끝. |

### 활성화 예시

- 긍정: "이 영상 10초~20초 구간 잘라줘"
- 부정: "법원 제출용 영상으로 편집해줘"
- 경계: "원본을 덮어써도 돼" → 거절하고 사본으로 출력

<!-- 계약 틀 출처: hypercore hyper-loop SKILL.md (MIT, NOTICE 참조) -->
