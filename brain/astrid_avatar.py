#!/usr/bin/env python3
"""Run the Astrid scripted avatar Process B flavor.

This script is prepared for Process B but is not called by the current default
supervisor path. It turns a request such as:

    Use Astrid and let her say: "..."

into script text, ElevenLabs MP3, Kling avatar video, Whisper timestamps, and a
final subtitle-burned MP4.
"""

from __future__ import annotations

import argparse
import json
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
KLING_MODEL = "fal-ai/kling-video/ai-avatar/v2/standard"
MAX_AUDIO_SECONDS = 60.0
WHISPER_MODEL_DIR = Path(os.environ.get("UGC_WHISPER_MODEL_DIR", "/opt/ugc-pipeline-whisper"))
SCRIPT_PATH = Path("script.md")
AUDIO_PATH = Path("output_audio") / "voiceover.mp3"
AVATAR_REFERENCE_PATH = Path("output_images") / "avatar_reference.png"
AVATAR_REFERENCE_RESULT_PATH = Path("avatar_reference_result.json")
AVATAR_RESULT_PATH = Path("kling_avatar_result.json")
TIMESTAMPS_PATH = Path("whisper_timestamps.json")
SUBTITLES_PATH = Path("output_videos") / "work" / "subtitles.ass"
FINAL_VIDEO_PATH = Path("output_videos") / "final_subtitled.mp4"
STATUS_PATH = Path("status.json")
LEARNING_PATH = Path("learning.md")
SUBTITLE_PLAY_RES_X = 720
SUBTITLE_PLAY_RES_Y = 1280
SUBTITLE_CENTER_X = SUBTITLE_PLAY_RES_X // 2
SUBTITLE_CENTER_Y = int(SUBTITLE_PLAY_RES_Y * 0.64)


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
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def extract_script_from_request(request: dict[str, Any]) -> str:
    explicit = request.get("script")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    prompt = str(request.get("prompt") or request.get("client_request") or "").strip()
    if not prompt:
        raise ValueError("request must include prompt/client_request or script")

    patterns = [
        r"\buse\s+astrid\b.*?\blet\s+her\s+say\s*:\s*[\"'“‘](.*?)[\"'”’]\s*$",
        r"\buse\s+astrid\b.*?\bwith\s+the\s+script\s*:\s*[\"'“‘](.*?)[\"'”’]\s*$",
        r"\blet\s+astrid\s+say\s*:\s*[\"'“‘](.*?)[\"'”’]\s*$",
        r"\bastrid\s+says?\s*:\s*[\"'“‘](.*?)[\"'”’]\s*$",
        r"\bshe\s+says?\s*:\s*[\"'“‘](.*?)[\"'”’]\s*$",
        r"\bastrid\s+says?\s*[\"'“‘](.*?)[\"'”’]\s*$",
        r"\bshe\s+says?\s*[\"'“‘](.*?)[\"'”’]\s*$",
        r"\bsay\s*:\s*[\"'“‘](.*?)[\"'”’]\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.IGNORECASE | re.DOTALL)
        if match:
            script = match.group(1).strip()
            if script:
                return script

    raise ValueError('could not extract script. Expected a request like: Use Astrid and let her say: "..."')


def extract_background_direction(request: dict[str, Any]) -> str:
    for key in ("background_direction", "background", "scene_direction", "visual_direction"):
        value = str(request.get(key) or "").strip()
        if value:
            return value

    prompt = str(request.get("prompt") or request.get("client_request") or "").strip()
    if not prompt:
        return ""

    labels = (
        "background direction",
        "background",
        "scene direction",
        "visual direction",
        "scene",
    )
    label_pattern = "|".join(re.escape(label) for label in labels)
    label_match = re.search(rf"(?:{label_pattern})\s*:\s*(.+?)(?:\b(?:she|astrid)\s+says?\b|$)", prompt, flags=re.IGNORECASE | re.DOTALL)
    if label_match:
        return label_match.group(1).strip(" .:-")

    change_match = re.search(
        r"\bchange\s+(?:her\s+)?background\s+(?:into|to)\s+(.+?)(?:\b(?:she|astrid)\s+says?\b|$)",
        prompt,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if change_match:
        return change_match.group(1).strip(" .:-")

    return ""


def save_script(script: str, path: Path = SCRIPT_PATH) -> Path:
    path.write_text(script.strip() + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def resolve_command(name: str) -> str:
    candidate = Path(sys.executable).parent / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    resolved = shutil.which(name)
    if resolved:
        return resolved
    raise RuntimeError(f"{name} is required for this Process B flavor")


def require_command(name: str) -> str:
    try:
        return resolve_command(name)
    except RuntimeError:
        raise RuntimeError(f"{name} is required for this Process B flavor")


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if process.returncode != 0:
        raise RuntimeError(f"{command[0]} failed with exit {process.returncode}: {process.stderr[-2400:]}")
    return process


def probe_audio_seconds(path: Path) -> float:
    ffprobe = require_command("ffprobe")
    process = run_command(
        [
            ffprobe,
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


def validate_audio_length(path: Path, max_seconds: float = MAX_AUDIO_SECONDS) -> float:
    duration = probe_audio_seconds(path)
    if duration >= max_seconds:
        raise ValueError(f"audio duration must be less than {max_seconds:.0f} seconds; got {duration:.3f} seconds")
    return duration


def download_file(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as response:
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


def build_avatar_reference_prompt(background_direction: str) -> str:
    return "\n".join(
        [
            "Create one photorealistic vertical talking-avatar reference image.",
            "Preserve Astrid's identity, face, age, blonde hair, body proportions, outfit continuity, and recognizable appearance from the input reference image.",
            "Keep Astrid framed as a clean UGC presenter, facing camera, upper body visible, natural room lighting, realistic hands, stable anatomy.",
            f"Replace only the background with this requested scene: {background_direction}",
            "Keep Astrid unobstructed and suitable for an AI talking-head avatar video.",
            "Do not add captions, subtitles, slogans, UI, title cards, watermarks, logos, stickers, generated text, or extra people.",
        ]
    )


def generate_avatar_reference_image(character_dir: Path, background_direction: str) -> dict[str, Any] | None:
    if not background_direction:
        return None
    if "FAL_KEY" not in os.environ or not os.environ["FAL_KEY"].strip():
        raise RuntimeError("FAL_KEY is not set. Put it in /etc/ugc-pipeline/fal.env for supervisor injection.")

    try:
        import fal_client
    except ImportError as exc:
        raise RuntimeError("fal-client is not installed. Install it in /opt/ugc-pipeline-venv.") from exc

    reference_image = character_dir / "reference.png"
    if not reference_image.is_file():
        raise FileNotFoundError(f"missing Astrid reference image: {reference_image}")

    reference_url = fal_client.upload_file(str(reference_image))
    prompt = build_avatar_reference_prompt(background_direction)
    arguments = {
        "prompt": prompt,
        "image_urls": [reference_url],
        "num_images": 1,
        "aspect_ratio": "9:16",
        "output_format": "png",
        "safety_tolerance": "1",
        "resolution": "2K",
        "limit_generations": True,
    }
    result = fal_client.subscribe(NANO_EDIT_MODEL, arguments=arguments, with_logs=True, client_timeout=300)
    image_url = extract_first_image_url(result)
    download_file(image_url, AVATAR_REFERENCE_PATH)
    output = {
        "model": NANO_EDIT_MODEL,
        "arguments": arguments,
        "result": result,
        "source_reference_path": str(reference_image),
        "reference_url": reference_url,
        "image_url": image_url,
        "path": str(AVATAR_REFERENCE_PATH),
        "background_direction": background_direction,
    }
    write_json(AVATAR_REFERENCE_RESULT_PATH, output)
    return output


def run_kling_avatar(character_dir: Path, audio_path: Path, avatar_reference: dict[str, Any] | None = None) -> dict[str, Any]:
    if "FAL_KEY" not in os.environ or not os.environ["FAL_KEY"].strip():
        raise RuntimeError("FAL_KEY is not set. Put it in /etc/ugc-pipeline/fal.env for supervisor injection.")

    try:
        import fal_client
    except ImportError as exc:
        raise RuntimeError("fal-client is not installed. Install it in /opt/ugc-pipeline-venv.") from exc

    reference_image = Path(str(avatar_reference["path"])) if avatar_reference else character_dir / "reference.png"
    if not reference_image.is_file():
        raise FileNotFoundError(f"missing Astrid avatar reference image: {reference_image}")
    if not audio_path.is_file():
        raise FileNotFoundError(f"missing voiceover audio: {audio_path}")

    image_url = fal_client.upload_file(str(reference_image))
    audio_url = fal_client.upload_file(str(audio_path))
    arguments = {
        "image_url": image_url,
        "audio_url": audio_url,
        "prompt": "Natural UGC talking-head delivery. Preserve Astrid's appearance and background from the reference image and synchronize lip movement to the supplied voiceover.",
    }
    result = fal_client.subscribe(KLING_MODEL, arguments=arguments, with_logs=True, client_timeout=900)
    video_url = result.get("video", {}).get("url")
    if not video_url:
        raise RuntimeError("Kling avatar result did not include video.url")

    raw_video_path = Path("output_videos") / "kling_avatar.mp4"
    download_file(video_url, raw_video_path)
    output = {
        "model": KLING_MODEL,
        "arguments": {
            "image_url": image_url,
            "audio_url": audio_url,
            "prompt": arguments["prompt"],
        },
        "result": result,
        "downloaded_video": str(raw_video_path),
        "avatar_reference": avatar_reference,
    }
    write_json(AVATAR_RESULT_PATH, output)
    return output


def run_whisper(audio_path: Path) -> dict[str, Any]:
    whisper_bin = require_command("whisper")
    work_dir = Path("output_audio") / "whisper"
    work_dir.mkdir(parents=True, exist_ok=True)
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

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {SUBTITLE_PLAY_RES_X}",
        f"PlayResY: {SUBTITLE_PLAY_RES_Y}",
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
    ffmpeg = require_command("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
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


def append_learning(
    script: str,
    audio_duration: float,
    final_video_path: Path,
    background_direction: str,
    avatar_reference: dict[str, Any] | None,
) -> None:
    lines = [
        "# Learning",
        "",
        "- Process B mode: astrid_scripted_avatar",
        f"- Character: astrid",
        f"- Script path: {SCRIPT_PATH}",
        f"- Voiceover path: {AUDIO_PATH}",
        f"- Voiceover duration seconds: {audio_duration:.3f}",
        f"- Avatar background edit: {'yes' if avatar_reference else 'no'}",
    ]
    if avatar_reference:
        lines.extend(
            [
                f"- Background direction: {background_direction}",
                f"- Edited avatar reference: {avatar_reference['path']}",
                f"- Avatar reference model: {NANO_EDIT_MODEL}",
            ]
        )
    lines.extend(
        [
        f"- Avatar model: {KLING_MODEL}",
        f"- Whisper model: base",
        f"- Final subtitled video: {final_video_path}",
        "- Subtitle style: social media words, large white text, black outline, baked into the video with ffmpeg.",
        "",
        "## Extracted Script",
        "",
        script,
        "",
        ]
    )
    LEARNING_PATH.write_text("\n".join(lines), encoding="utf-8")
    LEARNING_PATH.chmod(0o600)


def run(request_path: Path, character_dir: Path) -> int:
    started = monotonic()
    write_status("running", "astrid_script_extraction")

    request = load_request(request_path)
    script = extract_script_from_request(request)
    background_direction = extract_background_direction(request)
    script_path = save_script(script)

    write_status("running", "elevenlabs_tts", script_path=str(script_path))
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set. Put it in /etc/ugc-pipeline/fal.env for supervisor injection.")
    elevenlabs_tts.synthesize_speech(api_key, script, AUDIO_PATH)

    write_status("running", "audio_prevalidation", audio_path=str(AUDIO_PATH))
    audio_duration = validate_audio_length(AUDIO_PATH)

    avatar_reference = None
    if background_direction:
        write_status("running", "nano_banana_avatar_reference", model=NANO_EDIT_MODEL, background_direction=background_direction)
        avatar_reference = generate_avatar_reference_image(character_dir, background_direction)

    write_status("running", "kling_avatar", model=KLING_MODEL)
    avatar = run_kling_avatar(character_dir, AUDIO_PATH, avatar_reference)
    raw_video_path = Path(str(avatar["downloaded_video"]))

    write_status("running", "whisper_timestamps", model="base")
    whisper_data = run_whisper(AUDIO_PATH)
    subtitles_path = write_ass_subtitles(whisper_data)

    write_status("running", "subtitle_burn", subtitles_path=str(subtitles_path))
    final_video_path = burn_subtitles(raw_video_path, subtitles_path)
    append_learning(script, audio_duration, final_video_path, background_direction, avatar_reference)

    write_status(
        "succeeded",
        "process_b",
        mode="astrid_scripted_avatar",
        elapsed_seconds=round(monotonic() - started, 3),
        script_path=str(script_path),
        audio_path=str(AUDIO_PATH),
        audio_duration_seconds=round(audio_duration, 3),
        raw_video_path=str(raw_video_path),
        background_direction=background_direction,
        avatar_reference_path=str(avatar_reference["path"]) if avatar_reference else "",
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
            write_status("failed", "process_b", mode="astrid_scripted_avatar", error=str(exc), error_type=type(exc).__name__)
        except Exception:
            pass
        print(f"astrid_avatar failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
