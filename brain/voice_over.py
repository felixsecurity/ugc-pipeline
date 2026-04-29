#!/usr/bin/env python3
"""Run the Process B voice_over flavor.

This turns a request with a named character, stage direction, and exact
voiceover text into ElevenLabs audio, chained Nano Banana keyframes, Kling
image-to-video segments, and a subtitle-burned final MP4.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import elevenlabs_tts


DEFAULT_REQUEST_PATH = Path("request.json")
DEFAULT_CHARACTER_DIR = Path(__file__).resolve().parents[1] / "characters" / "astrid"
NANO_EDIT_MODEL = "fal-ai/nano-banana-2/edit"
KLING_IMAGE_TO_VIDEO_MODEL = "fal-ai/kling-video/v2.6/pro/image-to-video"
WHISPER_MODEL_DIR = Path(os.environ.get("UGC_WHISPER_MODEL_DIR", "/opt/ugc-pipeline-whisper"))

SCRIPT_PATH = Path("script.md")
AUDIO_PATH = Path("output_audio") / "voiceover.mp3"
PLAN_PATH = Path("voice_over_plan.json")
RESULT_PATH = Path("kling_voice_over_result.json")
TIMESTAMPS_PATH = Path("whisper_timestamps.json")
SUBTITLES_PATH = Path("output_videos") / "work" / "subtitles.ass"
JOINED_VIDEO_PATH = Path("output_videos") / "work" / "joined.mp4"
FINAL_WITH_AUDIO_PATH = Path("output_videos") / "work" / "final_with_audio.mp4"
FINAL_VIDEO_PATH = Path("output_videos") / "final_subtitled.mp4"
STATUS_PATH = Path("status.json")
LEARNING_PATH = Path("learning.md")

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 30
SEGMENT_SECONDS = 5
SUBTITLE_CENTER_X = VIDEO_WIDTH // 2
SUBTITLE_CENTER_Y = int(VIDEO_HEIGHT * 0.64)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(mode)


def write_status(status: str, stage: str, **extra: Any) -> None:
    write_json(
        STATUS_PATH,
        {
            "status": status,
            "stage": stage,
            "updated_at": now_utc(),
            **extra,
        },
    )


def load_request(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"request file does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_character_id(request: dict[str, Any], prompt: str, character_dir: Path) -> str:
    explicit = str(request.get("character_id") or "").strip().lower()
    if explicit:
        return explicit
    if character_dir.name:
        return character_dir.name.lower()
    match = re.search(r"\buse\s+([A-Za-z][A-Za-z0-9_-]*)\b", prompt, flags=re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return "astrid"


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'`" and value[-1] == value[0]:
        return value[1:-1].strip()
    if len(value) >= 2 and value[0] in "“‘" and value[-1] in "”’":
        return value[1:-1].strip()
    return value


def extract_after_label(text: str, labels: tuple[str, ...]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = rf"(?:{label_pattern})\s*:\s*(.+)$"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    value = match.group(1).strip()
    next_label = re.search(
        r"(?:^|\s)(?:stage directions?|directions?|stage|visuals?|scene|voiceover|voice over|script|text to read|spoken text)\s*:",
        value,
        flags=re.IGNORECASE,
    )
    if next_label:
        value = value[: next_label.start()].strip()
    return strip_quotes(value)


def extract_voiceover_text(request: dict[str, Any], prompt: str) -> str:
    for key in ("voiceover_text", "voice_over_text", "script", "spoken_text", "text_to_read"):
        value = str(request.get(key) or "").strip()
        if value:
            return value

    labels = ("voiceover", "voice over", "script", "text to read", "spoken text", "read out")
    value = extract_after_label(prompt, labels)
    if value:
        return value

    quoted_patterns = [
        r"\bvoice\s*over\s*(?:says?|is)?\s*[\"'“‘](.*?)[\"'”’]\s*$",
        r"\bread\s+out\s*[\"'“‘](.*?)[\"'”’]\s*$",
        r"\bsay\s*[\"'“‘](.*?)[\"'”’]\s*$",
    ]
    for pattern in quoted_patterns:
        match = re.search(pattern, prompt, flags=re.IGNORECASE | re.DOTALL)
        if match and match.group(1).strip():
            return match.group(1).strip()

    raise ValueError("voice_over requests must include exact spoken text, for example Voiceover: \"...\"")


def extract_stage_direction(request: dict[str, Any], prompt: str, voiceover_text: str) -> str:
    for key in ("stage_direction", "stage", "direction", "visual_direction", "scene_direction"):
        value = str(request.get(key) or "").strip()
        if value:
            return value

    labels = ("stage direction", "stage directions", "directions", "direction", "stage", "visuals", "visual", "scene")
    value = extract_after_label(prompt, labels)
    if value:
        return value

    cleaned = prompt.replace(voiceover_text, "")
    cleaned = re.sub(
        r"\b(?:voiceover|voice over|script|text to read|spoken text|read out)\s*:\s*[\"'“‘]?\s*[\"'”’]?",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\buse\s+[A-Za-z][A-Za-z0-9_-]*\b\.?", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
    if cleaned:
        return cleaned
    raise ValueError("voice_over requests must include stage direction")


def save_script(script: str) -> Path:
    SCRIPT_PATH.write_text(script.strip() + "\n", encoding="utf-8")
    SCRIPT_PATH.chmod(0o600)
    return SCRIPT_PATH


def resolve_command(name: str) -> str:
    candidate = Path(sys.executable).parent / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    resolved = shutil.which(name)
    if resolved:
        return resolved
    raise RuntimeError(f"{name} is required for this Process B flavor")


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if process.returncode != 0:
        raise RuntimeError(f"{command[0]} failed with exit {process.returncode}: {process.stderr[-2400:]}")
    return process


def probe_audio_seconds(path: Path) -> float:
    process = run_command(
        [
            resolve_command("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float(process.stdout.strip())


def rounded_video_seconds(audio_seconds: float) -> int:
    return max(SEGMENT_SECONDS, int(math.ceil(audio_seconds / SEGMENT_SECONDS) * SEGMENT_SECONDS))


def download_file(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=240) as response:
        path.write_bytes(response.read())
    path.chmod(0o600)


def extract_first_image_url(result: dict[str, Any]) -> str:
    images = result.get("images")
    if isinstance(images, list) and images:
        url = images[0].get("url")
        if url:
            return str(url)
    image = result.get("image")
    if isinstance(image, dict) and image.get("url"):
        return str(image["url"])
    raise RuntimeError("Nano Banana edit result did not include an image URL")


def build_keyframe_prompt(
    character_id: str,
    stage_direction: str,
    frame_index: int,
    frame_count: int,
) -> str:
    if frame_count <= 1:
        progress = 0.0
    else:
        progress = frame_index / (frame_count - 1)

    if progress <= 0.05:
        beat = "opening keyframe, establish the character and setting before the action starts"
    elif progress >= 0.95:
        beat = "final keyframe, complete the visual action with a clean stable ending"
    else:
        beat = f"intermediate keyframe at {int(round(progress * 100))}% of the action, progress the stage direction naturally"

    return "\n".join(
        [
            "Create one photorealistic vertical 9:16 video keyframe for a UGC advertisement.",
            f"Character: preserve {character_id}'s identity, face, age, hair, proportions, and wardrobe continuity from the input image.",
            f"Original stage direction: {stage_direction}",
            f"Keyframe role: {beat}.",
            "Use natural commercial lighting, credible body pose, realistic hands, stable anatomy, and a composition suitable for short-form social video.",
            "Do not add captions, subtitles, slogans, UI, title cards, watermarks, logos, stickers, or generated text.",
        ]
    )


def build_motion_prompt(stage_direction: str, segment_index: int, segment_count: int) -> str:
    return (
        f"Five-second photorealistic UGC motion segment {segment_index + 1} of {segment_count}. "
        f"Animate only the visual action implied by this stage direction: {stage_direction}. "
        "Keep character identity, clothing, lighting, and setting continuous between the supplied start and end frames. "
        "Use natural camera movement and realistic body motion. No captions, no text overlays, no logos, no watermarks."
    )


def run_nano_keyframes(
    fal_client: Any,
    character_dir: Path,
    character_id: str,
    stage_direction: str,
    segment_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reference_image = character_dir / "reference.png"
    if not reference_image.is_file():
        raise FileNotFoundError(f"missing character reference image: {reference_image}")

    output_dir = Path("output_images")
    output_dir.mkdir(mode=0o700, exist_ok=True)
    frame_count = segment_count + 1
    input_url = fal_client.upload_file(str(reference_image))
    frames: list[dict[str, Any]] = []
    raw_results: list[dict[str, Any]] = []

    for frame_index in range(frame_count):
        prompt = build_keyframe_prompt(character_id, stage_direction, frame_index, frame_count)
        arguments = {
            "prompt": prompt,
            "image_urls": [input_url],
            "num_images": 1,
            "aspect_ratio": "9:16",
            "output_format": "png",
            "safety_tolerance": "1",
            "resolution": "1K",
            "limit_generations": True,
        }
        result = fal_client.subscribe(NANO_EDIT_MODEL, arguments=arguments, with_logs=True, client_timeout=300)
        image_url = extract_first_image_url(result)
        local_path = output_dir / f"{frame_index:02d}.png"
        download_file(image_url, local_path)
        frames.append(
            {
                "index": frame_index,
                "time_seconds": frame_index * SEGMENT_SECONDS,
                "prompt": prompt,
                "input_url": input_url,
                "image_url": image_url,
                "path": str(local_path),
            }
        )
        raw_results.append({"index": frame_index, "model": NANO_EDIT_MODEL, "arguments": arguments, "result": result})
        input_url = image_url

    return frames, raw_results


def run_kling_segments(
    fal_client: Any,
    frames: list[dict[str, Any]],
    stage_direction: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    work_dir = Path("output_videos") / "work"
    work_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    segment_count = len(frames) - 1
    segments: list[dict[str, Any]] = []
    raw_results: list[dict[str, Any]] = []

    for index in range(segment_count):
        prompt = build_motion_prompt(stage_direction, index, segment_count)
        arguments = {
            "prompt": prompt,
            "negative_prompt": (
                "blur, distortion, low quality, identity drift, face drift, warped hands, bad anatomy, "
                "text overlays, captions, subtitles, logos, watermarks"
            ),
            "start_image_url": frames[index]["image_url"],
            "end_image_url": frames[index + 1]["image_url"],
            "duration": "5",
            "generate_audio": False,
        }
        result = fal_client.subscribe(KLING_IMAGE_TO_VIDEO_MODEL, arguments=arguments, with_logs=True, client_timeout=1200)
        video_url = result.get("video", {}).get("url")
        if not video_url:
            raise RuntimeError("Kling image-to-video result did not include video.url")
        local_path = work_dir / f"segment_{index + 1:02d}.mp4"
        download_file(str(video_url), local_path)
        segments.append(
            {
                "index": index + 1,
                "start_frame": frames[index]["path"],
                "end_frame": frames[index + 1]["path"],
                "prompt": prompt,
                "video_url": video_url,
                "path": str(local_path),
            }
        )
        raw_results.append({"index": index + 1, "model": KLING_IMAGE_TO_VIDEO_MODEL, "arguments": arguments, "result": result})

    return segments, raw_results


def concat_segments(segments: list[dict[str, Any]], output_path: Path = JOINED_VIDEO_PATH) -> Path:
    ffmpeg = resolve_command("ffmpeg")
    output_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    concat_path = output_path.parent / "segments.txt"
    concat_path.write_text("".join(f"file '{Path(segment['path']).name}'\n" for segment in segments), encoding="utf-8")
    concat_path.chmod(0o600)
    run_command(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-vf",
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},fps={VIDEO_FPS},format=yuv420p",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    output_path.chmod(0o600)
    return output_path


def attach_audio(video_path: Path, audio_path: Path, duration_seconds: int, output_path: Path = FINAL_WITH_AUDIO_PATH) -> Path:
    ffmpeg = resolve_command("ffmpeg")
    output_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            str(duration_seconds),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    output_path.chmod(0o600)
    return output_path


def run_whisper(audio_path: Path) -> dict[str, Any]:
    whisper_bin = resolve_command("whisper")
    work_dir = Path("output_audio") / "whisper"
    work_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    command = [
        whisper_bin,
        str(audio_path),
        "--model",
        "base",
        "--output_dir",
        str(work_dir),
        "--output_format",
        "json",
        "--word_timestamps",
        "True",
    ]
    if WHISPER_MODEL_DIR.is_dir():
        command.extend(["--model_dir", str(WHISPER_MODEL_DIR)])
    run_command(command)
    whisper_json = work_dir / f"{audio_path.stem}.json"
    if not whisper_json.is_file():
        raise FileNotFoundError(f"Whisper did not write expected JSON: {whisper_json}")
    data = json.loads(whisper_json.read_text(encoding="utf-8"))
    write_json(TIMESTAMPS_PATH, data)
    return data


def collect_words(whisper_data: dict[str, Any]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in whisper_data.get("segments", []):
        for word in segment.get("words", []) or []:
            text = str(word.get("word", "")).strip()
            if text:
                words.append({"word": text, "start": float(word["start"]), "end": float(word["end"])})
    if words:
        return words

    for segment in whisper_data.get("segments", []):
        text = str(segment.get("text", "")).strip()
        if text:
            words.append({"word": text, "start": float(segment["start"]), "end": float(segment["end"])})
    return words


def ass_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def subtitle_groups(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        current.append(word)
        duration = float(current[-1]["end"]) - float(current[0]["start"])
        if len(current) >= 3 or duration >= 1.45 or re.search(r"[.!?]$", str(word["word"])):
            groups.append(
                {
                    "start": float(current[0]["start"]),
                    "end": max(float(current[-1]["end"]), float(current[0]["start"]) + 0.35),
                    "text": " ".join(str(item["word"]).strip() for item in current).upper(),
                }
            )
            current = []
    if current:
        groups.append(
            {
                "start": float(current[0]["start"]),
                "end": max(float(current[-1]["end"]), float(current[0]["start"]) + 0.35),
                "text": " ".join(str(item["word"]).strip() for item in current).upper(),
            }
        )
    return groups


def write_ass_subtitles(whisper_data: dict[str, Any], path: Path = SUBTITLES_PATH) -> Path:
    words = collect_words(whisper_data)
    if not words:
        raise ValueError("Whisper output did not contain words or transcript segments")

    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {VIDEO_WIDTH}",
        f"PlayResY: {VIDEO_HEIGHT}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Social,Arial,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,1,5,40,40,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for group in subtitle_groups(words):
        positioned_text = f"{{\\an5\\pos({SUBTITLE_CENTER_X},{SUBTITLE_CENTER_Y})}}{ass_escape(group['text'])}"
        lines.append(
            f"Dialogue: 0,{ass_time(group['start'])},{ass_time(group['end'])},Social,,0,0,0,,{positioned_text}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def burn_subtitles(video_path: Path, subtitles_path: Path, output_path: Path = FINAL_VIDEO_PATH) -> Path:
    ffmpeg = resolve_command("ffmpeg")
    output_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"subtitles={subtitles_path}:force_style='Fontname=Arial,Fontsize=58,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=6,Shadow=1,Alignment=5'",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    output_path.chmod(0o600)
    return output_path


def write_learning(
    request: dict[str, Any],
    character_id: str,
    stage_direction: str,
    script: str,
    audio_duration: float,
    rounded_duration: int,
    segment_count: int,
    final_video_path: Path,
    elapsed_seconds: float,
) -> None:
    lines = [
        "# Learning",
        "",
        f"- Request ID: `{request.get('request_id', Path.cwd().name)}`",
        "- Process B mode: voice_over",
        f"- Character: {character_id}",
        f"- Script path: {SCRIPT_PATH}",
        f"- Voiceover path: {AUDIO_PATH}",
        f"- Voiceover duration seconds: {audio_duration:.3f}",
        f"- Rounded video duration seconds: {rounded_duration}",
        f"- Segment count: {segment_count}",
        f"- Keyframe count: {segment_count + 1}",
        f"- Keyframe model: {NANO_EDIT_MODEL}",
        f"- Video segment model: {KLING_IMAGE_TO_VIDEO_MODEL}",
        "- Kling native audio: disabled",
        "- Whisper model: base",
        f"- Final subtitled video: {final_video_path}",
        f"- Elapsed seconds: {elapsed_seconds:.3f}",
        "- Subtitle style: social media words, large white text, black outline, baked into the video with ffmpeg.",
        "",
        "## Stage Direction",
        "",
        stage_direction,
        "",
        "## Extracted Voiceover",
        "",
        script,
        "",
    ]
    LEARNING_PATH.write_text("\n".join(lines), encoding="utf-8")
    LEARNING_PATH.chmod(0o600)


def run(request_path: Path, character_dir: Path) -> int:
    started = monotonic()
    write_status("running", "voice_over_extraction")

    request = load_request(request_path)
    prompt = str(request.get("prompt") or request.get("client_request") or "").strip()
    voiceover_text = extract_voiceover_text(request, prompt)
    stage_direction = extract_stage_direction(request, prompt, voiceover_text)
    character_id = resolve_character_id(request, prompt, character_dir)
    script_path = save_script(voiceover_text)

    write_status("running", "elevenlabs_tts", script_path=str(script_path))
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set. Put it in /etc/ugc-pipeline/fal.env for supervisor injection.")
    elevenlabs_tts.synthesize_speech(api_key, voiceover_text, AUDIO_PATH)

    write_status("running", "audio_prevalidation", audio_path=str(AUDIO_PATH))
    audio_duration = probe_audio_seconds(AUDIO_PATH)
    rounded_duration = rounded_video_seconds(audio_duration)
    segment_count = rounded_duration // SEGMENT_SECONDS

    if "FAL_KEY" not in os.environ or not os.environ["FAL_KEY"].strip():
        raise RuntimeError("FAL_KEY is not set. Put it in /etc/ugc-pipeline/fal.env for supervisor injection.")
    try:
        import fal_client
    except ImportError as exc:
        raise RuntimeError("fal-client is not installed. Install it in /opt/ugc-pipeline-venv.") from exc

    write_status("running", "nano_banana_keyframes", model=NANO_EDIT_MODEL, keyframe_count=segment_count + 1)
    frames, nano_results = run_nano_keyframes(fal_client, character_dir, character_id, stage_direction, segment_count)

    write_status("running", "kling_image_to_video", model=KLING_IMAGE_TO_VIDEO_MODEL, segment_count=segment_count)
    segments, kling_results = run_kling_segments(fal_client, frames, stage_direction)

    write_status("running", "ffmpeg_concat")
    joined_path = concat_segments(segments)
    with_audio_path = attach_audio(joined_path, AUDIO_PATH, rounded_duration)

    write_status("running", "whisper_timestamps", model="base")
    whisper_data = run_whisper(AUDIO_PATH)
    subtitles_path = write_ass_subtitles(whisper_data)

    write_status("running", "subtitle_burn", subtitles_path=str(subtitles_path))
    final_video_path = burn_subtitles(with_audio_path, subtitles_path)

    plan = {
        "mode": "voice_over",
        "character_id": character_id,
        "stage_direction": stage_direction,
        "script_path": str(SCRIPT_PATH),
        "audio_path": str(AUDIO_PATH),
        "audio_duration_seconds": audio_duration,
        "rounded_video_duration_seconds": rounded_duration,
        "segment_seconds": SEGMENT_SECONDS,
        "segment_count": segment_count,
        "keyframe_count": len(frames),
        "frames": frames,
        "segments": segments,
        "joined_video_path": str(joined_path),
        "with_audio_path": str(with_audio_path),
        "subtitles_path": str(subtitles_path),
        "final_video_path": str(final_video_path),
    }
    write_json(PLAN_PATH, plan)
    write_json(
        RESULT_PATH,
        {
            "mode": "voice_over",
            "request_id": request.get("request_id", Path.cwd().name),
            "ran_at": now_utc(),
            "nano_model": NANO_EDIT_MODEL,
            "kling_model": KLING_IMAGE_TO_VIDEO_MODEL,
            "nano_results": nano_results,
            "kling_results": kling_results,
        },
    )

    elapsed_seconds = monotonic() - started
    write_learning(
        request,
        character_id,
        stage_direction,
        voiceover_text,
        audio_duration,
        rounded_duration,
        segment_count,
        final_video_path,
        elapsed_seconds,
    )
    write_status(
        "succeeded",
        "process_b",
        mode="voice_over",
        elapsed_seconds=round(elapsed_seconds, 3),
        character_id=character_id,
        script_path=str(script_path),
        audio_path=str(AUDIO_PATH),
        audio_duration_seconds=round(audio_duration, 3),
        rounded_video_duration_seconds=rounded_duration,
        segment_count=segment_count,
        keyframe_count=len(frames),
        plan_path=str(PLAN_PATH),
        result_path=str(RESULT_PATH),
        timestamps_path=str(TIMESTAMPS_PATH),
        subtitles_path=str(subtitles_path),
        final_video_path=str(final_video_path),
    )
    print(final_video_path)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, default=DEFAULT_REQUEST_PATH)
    parser.add_argument("--character-dir", type=Path, default=DEFAULT_CHARACTER_DIR)
    args = parser.parse_args(argv[1:])

    try:
        return run(args.request, args.character_dir)
    except Exception as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        try:
            write_status("failed", "process_b", mode="voice_over", error=str(exc), error_type=type(exc).__name__)
        except Exception:
            pass
        print(f"voice_over failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
