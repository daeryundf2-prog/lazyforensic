---
name: forensic-video
description: CCTV/블랙박스 프레임 추출·ffprobe 태그. ffmpeg 필요. 촬영일시 조작의 법정 증명에는 쓰지 말 것.
---

# forensic-video

프레임을 뽑고 컨테이너 태그를 읽는다. 촬영 시점을 확정하지 않는다.

## 실행

```bash
python skills/forensic-video/scripts/setup.py --check
python skills/forensic-video/scripts/watch.py "<동영상경로>" --resolution 720
python skills/forensic-video/scripts/forensic_video_audit.py "<동영상경로>"
```

전사가 필요하면 먼저 로컬 전사를 쓴다. 오디오가 기기 밖으로 나가지 않는다.

```bash
LAZYFORENSIC_TRANSCRIBE_MODEL=/path/qwen3-asr-1.7b.gguf python skills/forensic-video/scripts/watch.py "<동영상경로>" --local-stt
```

`transcribe-cli`(transcribe.cpp v0.3.x, 0.4에서 CLI 변경)가 PATH에 있거나 `LAZYFORENSIC_TRANSCRIBE_BIN`으로 지정돼 있어야 한다. 한국어는 Qwen3-ASR GGUF(Apache-2.0)를 쓴다. `--stt-lang`의 기본값은 `ko`이다. 추출한 WAV는 전사 후 지운다. 전사는 수사 보조 자료이고 원 음성과 대조하기 전에는 인용하지 않는다.

기본 실행은 오디오를 외부로 보내지 않는다. Groq/OpenAI 전사가 꼭 필요하고 의뢰인 동의·반출 승인이 있는 경우에만 다음처럼 명시한다.

```bash
python skills/forensic-video/scripts/watch.py "<동영상경로>" --upload-audio --whisper groq
```

`--upload-audio`는 추출 오디오를 제3자 API로 전송한다. URL 입력은 편의 다운로드일 뿐 포렌식 이미징이나 Chain of Custody가 아니다. 프레임은 최대 100개다.

## Antigravity

추출된 프레임은 호스트 `Read`로 본다. `view_file`을 만들지 말 것.
해석은 `invoke_subagent` `Model: "flash"`, 태그 vs `os.stat` 대조는 `Model: "pro"`(세션 모델과 무관, Claude 할당량 절약).
번호판·신원 단정, 편집 여부 확정을 쓰지 않는다.
