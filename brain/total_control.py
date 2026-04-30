#!/usr/bin/env python3
"""Run the Process B total_control flavor.

This mode uses a source video as the full performance driver. It first converts
the source video's speech into the standard Process B destination voice with
ElevenLabs speech-to-speech, muxes that converted audio back onto the source
video, then sends the character reference and dubbed driver video through Kling
motion control. Body motion, camera motion, timing, lip motion, and the changed
voice are inherited from that dubbed driver video.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import elevenlabs_tts


DEFAULT_REQUEST_PATH = Path("request.json")
DEFAULT_CHARACTER_DIR = Path(__file__).resolve().parents[1] / "characters" / "astrid"
NANO_EDIT_MODEL = "fal-ai/nano-banana-2/edit"
KLING_MOTION_CONTROL_MODEL = "fal-ai/kling-video/v2.6/standard/motion-control"
ELEVENLABS_STS_MODEL_ID = "eleven_multilingual_sts_v2"
WHISPER_MODEL_DIR = Path(os.environ.get("UGC_WHISPER_MODEL_DIR", "/opt/ugc-pipeline-whisper"))

PLAN_PATH = Path("total_control_plan.json")
RESULT_PATH = Path("kling_total_control_result.json")
STATUS_PATH = Path("status.json")
LEARNING_PATH = Path("learning.md")
SOURCE_AUDIO_PATH = Path("output_audio") / "source_audio.wav"
VOICE_CHANGED_AUDIO_PATH = Path("output_audio") / "voice_changed.mp3"
DUBBED_DRIVER_VIDEO_PATH = Path("output_videos") / "work" / "total_control_driver.mp4"
RAW_VIDEO_PATH = Path("output_videos") / "total_control_motion.mp4"
FINAL_WITH_AUDIO_PATH = Path("output_videos") / "work" / "total_control_with_audio.mp4"
FINAL_VIDEO_PATH = Path("output_videos") / "final_subtitled.mp4"
TIMESTAMPS_PATH = Path("whisper_timestamps.json")
SUBTITLES_PATH = Path("output_videos") / "work" / "subtitles.ass"
UPLOAD_REFERENCE_PATH = Path("output_images") / "total_control_reference.jpg"
SECOND_FRAME_PATH = Path("output_images") / "total_control_frame_02.png"
POSE_REFERENCE_PATH = Path("output_images") / "total_control_pose_reference.png"

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
SUBTITLE_CENTER_X = VIDEO_WIDTH // 2
SUBTITLE_CENTER_Y = int(VIDEO_HEIGHT * 0.64)
MIN_VIDEO_SECONDS = 3.0
MAX_VIDEO_SECONDS = 30.05
MAX_FAL_UPLOAD_BYTES = 10 * 1024 * 1024


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


def is_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


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


def probe_media_seconds(path_or_url: str) -> float:
    process = run_command(
        [
            resolve_command("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path_or_url,
        ]
    )
    return float(process.stdout.strip())


def resolve_video_input(request: dict[str, Any]) -> str:
    value = str(
        request.get("video_input")
        or request.get("video_url")
        or request.get("input_video")
        or request.get("source_video")
        or ""
    ).strip()
    if not value:
        raise ValueError("total_control requests must include video_input")
    if is_url(value):
        duration = probe_media_seconds(value)
        if duration < MIN_VIDEO_SECONDS or duration > MAX_VIDEO_SECONDS:
            raise ValueError(
                f"input video duration must be between {MIN_VIDEO_SECONDS:.0f}s and {MAX_VIDEO_SECONDS:.2f}s for total_control; got {duration:.3f}s"
            )
        return value
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        raise FileNotFoundError(f"input video does not exist: {value}")
    duration = probe_media_seconds(str(path))
    if duration < MIN_VIDEO_SECONDS or duration > MAX_VIDEO_SECONDS:
        raise ValueError(
            f"input video duration must be between {MIN_VIDEO_SECONDS:.0f}s and {MAX_VIDEO_SECONDS:.2f}s for total_control; got {duration:.3f}s"
        )
    return str(path)


def resolve_character_id(request: dict[str, Any], character_dir: Path) -> str:
    explicit = str(request.get("character_id") or "").strip().lower()
    if explicit:
        return explicit
    prompt = str(request.get("prompt") or request.get("client_request") or "").strip()
    match = re.search(r"\b(?:use|with)\s+([A-Za-z][A-Za-z0-9_-]*)\b", prompt, flags=re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return (character_dir.name or "astrid").lower()


def extract_direction(request: dict[str, Any]) -> str:
    return str(request.get("direction") or request.get("visual_direction") or request.get("scene_direction") or "").strip()


def extract_audio(video_input: str, output_path: Path = SOURCE_AUDIO_PATH) -> Path:
    ffmpeg = resolve_command("ffmpeg")
    output_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            video_input,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    output_path.chmod(0o600)
    return output_path


def encode_multipart_form(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"----ugc-pipeline-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    for name, (filename, data, content_type) in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                data,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def voice_change_speech(api_key: str, source_audio_path: Path, output_path: Path = VOICE_CHANGED_AUDIO_PATH) -> dict[str, Any]:
    query = urllib.parse.urlencode({"output_format": elevenlabs_tts.OUTPUT_FORMAT})
    url = f"{elevenlabs_tts.API_BASE_URL}/speech-to-speech/{elevenlabs_tts.VOICE_ID}?{query}"
    body, boundary = encode_multipart_form(
        fields={
            "model_id": ELEVENLABS_STS_MODEL_ID,
            "voice_settings": json.dumps(elevenlabs_tts.VOICE_SETTINGS),
        },
        files={
            "audio": (source_audio_path.name, source_audio_path.read_bytes(), "audio/wav"),
        },
    )
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "audio/mpeg",
            "xi-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            audio = response.read()
            status_code = response.status
            content_type = response.headers.get("Content-Type", "")
            request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ElevenLabs speech-to-speech returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ElevenLabs speech-to-speech request failed: {exc.reason}") from exc

    if not audio:
        raise RuntimeError("ElevenLabs speech-to-speech returned an empty audio response")
    output_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    output_path.write_bytes(audio)
    output_path.chmod(0o600)
    return {
        "http_status": status_code,
        "content_type": content_type,
        "request_id": request_id,
        "bytes_written": len(audio),
        "voice_name": elevenlabs_tts.VOICE_NAME,
        "voice_id": elevenlabs_tts.VOICE_ID,
        "model_id": ELEVENLABS_STS_MODEL_ID,
        "output_format": elevenlabs_tts.OUTPUT_FORMAT,
    }


def mux_changed_audio(video_input: str, audio_path: Path, output_path: Path = DUBBED_DRIVER_VIDEO_PATH) -> Path:
    ffmpeg = resolve_command("ffmpeg")
    output_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            video_input,
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    output_path.chmod(0o600)
    return output_path


def download_file(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
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


def prepare_reference_for_upload(reference_image: Path) -> Path:
    if reference_image.stat().st_size <= MAX_FAL_UPLOAD_BYTES:
        return reference_image

    ffmpeg = resolve_command("ffmpeg")
    UPLOAD_REFERENCE_PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            str(reference_image),
            "-vf",
            "scale='min(1024,iw)':-2",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(UPLOAD_REFERENCE_PATH),
        ]
    )
    if UPLOAD_REFERENCE_PATH.stat().st_size > MAX_FAL_UPLOAD_BYTES:
        run_command(
            [
                ffmpeg,
                "-y",
                "-i",
                str(reference_image),
                "-vf",
                "scale='min(768,iw)':-2",
                "-frames:v",
                "1",
                "-q:v",
                "4",
                str(UPLOAD_REFERENCE_PATH),
            ]
        )
    if UPLOAD_REFERENCE_PATH.stat().st_size > MAX_FAL_UPLOAD_BYTES:
        raise RuntimeError(f"prepared reference image still exceeds fal upload limit: {UPLOAD_REFERENCE_PATH}")
    UPLOAD_REFERENCE_PATH.chmod(0o600)
    return UPLOAD_REFERENCE_PATH


def extract_second_frame(video_input: str, output_path: Path = SECOND_FRAME_PATH) -> Path:
    ffmpeg = resolve_command("ffmpeg")
    output_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    run_command(
        [
            ffmpeg,
            "-y",
            "-i",
            video_input,
            "-vf",
            "select=eq(n\\,1)",
            "-vsync",
            "vfr",
            "-update",
            "1",
            "-frames:v",
            "1",
            str(output_path),
        ]
    )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg did not extract the second frame to {output_path}")
    output_path.chmod(0o600)
    return output_path


def build_pose_reference_prompt(character_id: str, direction: str) -> str:
    background_rule = (
        f"Apply this requested background or scene change: {direction}"
        if direction
        else "Keep the background, camera angle, lighting, framing, and scene layout from the video frame."
    )
    return "\n".join(
        [
            "Create one clean photorealistic vertical character reference image for Kling motion control.",
            "Use the video frame as the pose, composition, body angle, limb placement, camera framing, and scene reference.",
            f"Replace the person or subject in that video frame with {character_id} from the character reference image.",
            f"Preserve {character_id}'s identity, face, age, hair, body proportions, and recognizable appearance from the character reference image.",
            "Copy the pose from the video frame as exactly as possible, including head angle, torso angle, hands, arms, legs, balance, body silhouette, and mouth position.",
            background_rule,
            "Keep the character unobstructed and suitable as the starting image for video motion control.",
            "Do not add captions, subtitles, slogans, UI, title cards, watermarks, logos, stickers, generated text, or extra characters.",
        ]
    )


def generate_pose_reference_image(
    fal_client: Any,
    character_dir: Path,
    character_id: str,
    direction: str,
    second_frame_path: Path,
) -> dict[str, Any]:
    reference_image = character_dir / "reference.png"
    if not reference_image.is_file():
        raise FileNotFoundError(f"missing character reference image: {reference_image}")

    upload_reference = prepare_reference_for_upload(reference_image)
    reference_url = fal_client.upload_file(str(upload_reference))
    frame_url = fal_client.upload_file(str(second_frame_path))
    prompt = build_pose_reference_prompt(character_id, direction)
    arguments = {
        "prompt": prompt,
        "image_urls": [reference_url, frame_url],
        "num_images": 1,
        "aspect_ratio": "9:16",
        "output_format": "png",
        "safety_tolerance": "1",
        "resolution": "2K",
        "limit_generations": True,
    }
    result = fal_client.subscribe(NANO_EDIT_MODEL, arguments=arguments, with_logs=True, client_timeout=300)
    image_url = extract_first_image_url(result)
    download_file(image_url, POSE_REFERENCE_PATH)
    return {
        "model": NANO_EDIT_MODEL,
        "arguments": arguments,
        "result": result,
        "image_url": image_url,
        "path": str(POSE_REFERENCE_PATH),
        "source_reference_path": str(reference_image),
        "uploaded_reference_path": str(upload_reference),
        "reference_url": reference_url,
        "second_frame_path": str(second_frame_path),
        "second_frame_url": frame_url,
    }


def build_motion_prompt(character_id: str, direction: str) -> str:
    lines = [
        "Transfer the exact body movement, lip movement, timing, gesture rhythm, pose progression, camera movement, action path, and framing from the driver video.",
        f"Use {character_id} from the supplied image as the generated video's character and place that character into the driver video's scene.",
        "Do not reinterpret or smooth over the performance. Copy the driver's body motion and mouth shapes exactly.",
        "Keep the original sound from the driver video; it already contains the ElevenLabs voice-changed audio.",
    ]
    if direction:
        lines.append(f"Respect this requested scene or styling direction without changing the copied performance: {direction}")
    return " ".join(lines)


def run_kling_motion_control(
    fal_client: Any,
    pose_reference: dict[str, Any],
    video_path: Path,
    character_id: str,
    direction: str,
) -> dict[str, Any]:
    image_url = str(pose_reference["image_url"])
    video_url = fal_client.upload_file(str(video_path))
    prompt = build_motion_prompt(character_id, direction)
    arguments = {
        "prompt": prompt,
        "image_url": image_url,
        "video_url": video_url,
        "character_orientation": "video",
        "keep_original_sound": True,
    }
    result = fal_client.subscribe(KLING_MOTION_CONTROL_MODEL, arguments=arguments, with_logs=True, client_timeout=1800)
    output_url = result.get("video", {}).get("url")
    if not output_url:
        raise RuntimeError("Kling motion-control result did not include video.url")
    download_file(str(output_url), RAW_VIDEO_PATH)
    return {
        "model": KLING_MOTION_CONTROL_MODEL,
        "arguments": arguments,
        "result": result,
        "downloaded_video": str(RAW_VIDEO_PATH),
        "reference_image_path": str(pose_reference["path"]),
        "source_reference_image_path": str(pose_reference["source_reference_path"]),
        "uploaded_reference_image_path": str(pose_reference["uploaded_reference_path"]),
        "reference_image_url": image_url,
        "driver_video_path": str(video_path),
        "driver_video_url": video_url,
        "prompt": prompt,
    }


def replace_audio(video_path: Path, audio_path: Path, output_path: Path = FINAL_WITH_AUDIO_PATH) -> Path:
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
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
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
    direction: str,
    video_input: str,
    source_duration: float,
    voice_changed_duration: float,
    second_frame_path: Path,
    pose_reference: dict[str, Any],
    final_video_path: Path,
    elapsed_seconds: float,
) -> None:
    lines = [
        "# Learning",
        "",
        f"- Request ID: `{request.get('request_id', Path.cwd().name)}`",
        "- Process B mode: total_control",
        f"- Character: {character_id}",
        f"- Input video: {video_input}",
        f"- Source video duration seconds: {source_duration:.3f}",
        f"- Voice changed audio: {VOICE_CHANGED_AUDIO_PATH}",
        f"- Voice changed duration seconds: {voice_changed_duration:.3f}",
        f"- Destination voice: {elevenlabs_tts.VOICE_NAME} ({elevenlabs_tts.VOICE_ID})",
        f"- Voice conversion model: {ELEVENLABS_STS_MODEL_ID}",
        f"- Pose source frame: {second_frame_path}",
        f"- Generated motion reference image: {pose_reference['path']}",
        f"- Nano Banana model: {NANO_EDIT_MODEL}",
        "- Nano Banana resolution: 2K",
        f"- Motion control model: {KLING_MOTION_CONTROL_MODEL}",
        "- Character orientation: video",
        "- Keep original sound: true, using the dubbed driver video",
        "- Whisper model: base",
        f"- Final subtitled video: {final_video_path}",
        f"- Elapsed seconds: {elapsed_seconds:.3f}",
        "- Subtitle style: social media words, large white text, black outline, baked into the video with ffmpeg.",
        "",
        "## Direction",
        "",
        direction or "none",
        "",
    ]
    LEARNING_PATH.write_text("\n".join(lines), encoding="utf-8")
    LEARNING_PATH.chmod(0o600)


def run(request_path: Path, character_dir: Path) -> int:
    started = monotonic()
    write_status("running", "total_control_setup")

    request = load_request(request_path)
    character_id = resolve_character_id(request, character_dir)
    direction = extract_direction(request)
    video_input = resolve_video_input(request)
    source_duration = probe_media_seconds(video_input)

    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set. Put it in /etc/ugc-pipeline/fal.env for supervisor injection.")
    if "FAL_KEY" not in os.environ or not os.environ["FAL_KEY"].strip():
        raise RuntimeError("FAL_KEY is not set. Put it in /etc/ugc-pipeline/fal.env for supervisor injection.")
    try:
        import fal_client
    except ImportError as exc:
        raise RuntimeError("fal-client is not installed. Install it in /opt/ugc-pipeline-venv.") from exc

    write_status("running", "extract_second_frame")
    second_frame_path = extract_second_frame(video_input)

    write_status("running", "nano_banana_pose_reference", model=NANO_EDIT_MODEL)
    pose_reference = generate_pose_reference_image(
        fal_client,
        character_dir,
        character_id,
        direction,
        second_frame_path,
    )

    write_status("running", "extract_source_audio")
    source_audio_path = extract_audio(video_input)

    write_status("running", "elevenlabs_voice_change", voice_id=elevenlabs_tts.VOICE_ID)
    voice_change = voice_change_speech(api_key, source_audio_path)
    voice_changed_duration = probe_media_seconds(str(VOICE_CHANGED_AUDIO_PATH))

    write_status("running", "mux_voice_changed_driver")
    dubbed_driver_path = mux_changed_audio(video_input, VOICE_CHANGED_AUDIO_PATH)

    write_status("running", "kling_motion_control", model=KLING_MOTION_CONTROL_MODEL)
    kling = run_kling_motion_control(fal_client, pose_reference, dubbed_driver_path, character_id, direction)

    write_status("running", "replace_final_audio")
    with_audio_path = replace_audio(RAW_VIDEO_PATH, VOICE_CHANGED_AUDIO_PATH)

    write_status("running", "whisper_timestamps", model="base")
    whisper_data = run_whisper(VOICE_CHANGED_AUDIO_PATH)
    subtitles_path = write_ass_subtitles(whisper_data)

    write_status("running", "subtitle_burn", subtitles_path=str(subtitles_path))
    final_video_path = burn_subtitles(with_audio_path, subtitles_path)

    plan = {
        "mode": "total_control",
        "character_id": character_id,
        "direction": direction,
        "video_input": video_input,
        "source_duration_seconds": source_duration,
        "source_audio_path": str(source_audio_path),
        "voice_changed_audio_path": str(VOICE_CHANGED_AUDIO_PATH),
        "voice_changed_audio_duration_seconds": voice_changed_duration,
        "second_frame_path": str(second_frame_path),
        "pose_reference_path": str(pose_reference["path"]),
        "pose_reference_url": str(pose_reference["image_url"]),
        "uploaded_character_reference_path": str(pose_reference["uploaded_reference_path"]),
        "pose_reference_model": NANO_EDIT_MODEL,
        "pose_reference_resolution": "2K",
        "dubbed_driver_video_path": str(dubbed_driver_path),
        "motion_control_model": KLING_MOTION_CONTROL_MODEL,
        "character_orientation": "video",
        "keep_original_sound": True,
        "raw_motion_video_path": str(RAW_VIDEO_PATH),
        "with_audio_path": str(with_audio_path),
        "subtitles_path": str(subtitles_path),
        "final_video_path": str(final_video_path),
    }
    write_json(PLAN_PATH, plan)
    write_json(
        RESULT_PATH,
        {
            "mode": "total_control",
        "request_id": request.get("request_id", Path.cwd().name),
        "ran_at": now_utc(),
        "nano_pose_reference": pose_reference,
        "elevenlabs_voice_change": voice_change,
        "kling": kling,
        },
    )

    elapsed_seconds = monotonic() - started
    write_learning(
        request,
        character_id,
        direction,
        video_input,
        source_duration,
        voice_changed_duration,
        second_frame_path,
        pose_reference,
        final_video_path,
        elapsed_seconds,
    )
    write_status(
        "succeeded",
        "process_b",
        mode="total_control",
        elapsed_seconds=round(elapsed_seconds, 3),
        character_id=character_id,
        input_video=video_input,
        second_frame_path=str(second_frame_path),
        pose_reference_path=str(pose_reference["path"]),
        uploaded_character_reference_path=str(pose_reference["uploaded_reference_path"]),
        source_audio_path=str(source_audio_path),
        voice_changed_audio_path=str(VOICE_CHANGED_AUDIO_PATH),
        dubbed_driver_video_path=str(dubbed_driver_path),
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
            write_status("failed", "process_b", mode="total_control", error=str(exc), error_type=type(exc).__name__)
        except Exception:
            pass
        print(f"total_control failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
