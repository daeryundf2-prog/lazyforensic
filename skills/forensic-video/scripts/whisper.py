#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
whisper.py
Speech-to-Text transcription: local transcribe.cpp, or Groq / OpenAI.

The local backend never sends audio off the machine. Audio upload to Groq /
OpenAI is disabled unless the caller passes allow_upload=True.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


def load_api_key(backend: str | None = None) -> tuple[str | None, str]:
    if backend == "groq" or (backend is None and os.getenv("GROQ_API_KEY")):
        k = os.getenv("GROQ_API_KEY")
        if k:
            return k, "groq"
    if backend == "openai" or (backend is None and os.getenv("OPENAI_API_KEY")):
        k = os.getenv("OPENAI_API_KEY")
        if k:
            return k, "openai"
    return None, "none"


def extract_audio(video_path: str, out_audio: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found for audio extraction")
    cmd = [
        ffmpeg,
        "-y",
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-b:a", "64k",
        str(out_audio),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_audio


def transcribe_video(
    video_path: str,
    out_dir: Path,
    backend: str | None = None,
    allow_upload: bool = False,
) -> list[dict]:
    if not allow_upload:
        print(
            "[forensic-video] external transcription disabled; audio was not uploaded",
            file=sys.stderr,
        )
        return []

    key, provider = load_api_key(backend)
    if not key:
        return []

    audio_path = out_dir / "audio.mp3"
    try:
        extract_audio(video_path, audio_path)
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            return []

        if provider == "groq":
            return _transcribe_groq(audio_path, key)
        elif provider == "openai":
            return _transcribe_openai(audio_path, key)
        return []
    finally:
        # 증거 오디오는 외부 전송 후에도 로컬에 남기지 않는다 (반출 동의 시에도 cleanup)
        try:
            if audio_path.exists():
                audio_path.unlink()
        except Exception:
            pass


def find_local_stt() -> tuple[str | None, str | None]:
    """Return (transcribe-cli path, GGUF model path) from env or PATH."""
    binary = os.getenv("LAZYFORENSIC_TRANSCRIBE_BIN") or shutil.which("transcribe-cli")
    model = os.getenv("LAZYFORENSIC_TRANSCRIBE_MODEL")
    if model and not Path(model).is_file():
        model = None
    return binary, model


def transcribe_local(video_path: str, out_dir: Path, language: str | None = "ko") -> list[dict]:
    """Transcribe on this machine with transcribe.cpp (pin v0.3.x).

    Uses `--batch <list> --batch-jsonl --timestamps segment`, which prints a
    batch_header line and then one JSON line per file whose `segments` rows
    carry t0_ms / t1_ms / text (format read from examples/cli/main.cpp v0.3.1).
    """
    binary, model = find_local_stt()
    if not binary or not model:
        print(
            "[forensic-video] local STT unavailable: set LAZYFORENSIC_TRANSCRIBE_MODEL to a *.gguf "
            "(Qwen3-ASR for Korean) and install transcribe-cli or set LAZYFORENSIC_TRANSCRIBE_BIN",
            file=sys.stderr,
        )
        return []
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("[forensic-video] ffmpeg not found for audio extraction", file=sys.stderr)
        return []
    wav = out_dir / "audio-16k.wav"
    batch_list = out_dir / "stt-batch.txt"
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not wav.exists() or wav.stat().st_size == 0:
            return []
        batch_list.write_text(f"{wav}\n", encoding="utf-8")
        cmd = [binary, "-m", model, "--batch", str(batch_list), "--batch-jsonl", "--timestamps", "segment"]
        if language:
            cmd += ["-l", language]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        return _parse_batch_jsonl(proc.stdout)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[forensic-video] local transcription error: {e}", file=sys.stderr)
        return []
    finally:
        for path in (wav, batch_list):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass


def _parse_batch_jsonl(stdout: str) -> list[dict]:
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("type") == "batch_header" or "file" not in row:
            continue
        if row.get("error"):
            print(f"[forensic-video] local transcription error: {row['error']}", file=sys.stderr)
            return []
        segments = [
            {
                "start": seg.get("t0_ms", 0) / 1000.0,
                "end": seg.get("t1_ms", 0) / 1000.0,
                "text": str(seg.get("text", "")).strip(),
            }
            for seg in row.get("segments", [])
            if str(seg.get("text", "")).strip()
        ]
        if not segments and str(row.get("text", "")).strip():
            segments = [{"start": 0.0, "text": row["text"].strip()}]
        return segments
    return []


def _transcribe_groq(audio_path: Path, api_key: str) -> list[dict]:
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    
    with open(audio_path, "rb") as f:
        file_bytes = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n"
    ).encode("utf-8") + file_bytes + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"whisper-large-v3\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="response_format"\r\n\r\n'
        f"verbose_json\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            segments = []
            for seg in data.get("segments", []):
                segments.append({
                    "start": float(seg.get("start", 0.0)),
                    "text": seg.get("text", "").strip(),
                })
            return segments
    except Exception as e:
        print(f"[forensic-video] Groq transcription error: {e}", file=sys.stderr)
        return []


def _transcribe_openai(audio_path: Path, api_key: str) -> list[dict]:
    url = "https://api.openai.com/v1/audio/transcriptions"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    
    with open(audio_path, "rb") as f:
        file_bytes = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n"
    ).encode("utf-8") + file_bytes + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"whisper-1\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="response_format"\r\n\r\n'
        f"verbose_json\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            segments = []
            for seg in data.get("segments", []):
                segments.append({
                    "start": float(seg.get("start", 0.0)),
                    "text": seg.get("text", "").strip(),
                })
            return segments
    except Exception as e:
        print(f"[forensic-video] OpenAI transcription error: {e}", file=sys.stderr)
        return []
