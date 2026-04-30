#!/usr/bin/env python3
"""Run the Process B motion_control flavor."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any


DEFAULT_REQUEST_PATH = Path("request.json")
DEFAULT_CHARACTER_DIR = Path(__file__).resolve().parents[1] / "characters" / "astrid"
NANO_EDIT_MODEL = "fal-ai/nano-banana-2/edit"
KLING_MOTION_CONTROL_MODEL = "fal-ai/kling-video/v2.6/standard/motion-control"
STATUS_PATH = Path("status.json")
PLAN_PATH = Path("motion_control_plan.json")
RESULT_PATH = Path("kling_motion_control_result.json")
LEARNING_PATH = Path("learning.md")
FINAL_VIDEO_PATH = Path("output_videos") / "final.mp4"
RAW_VIDEO_PATH = Path("output_videos") / "motion_control.mp4"
MODIFIED_IMAGE_PATH = Path("output_images") / "motion_control_reference.png"
MIN_VIDEO_SECONDS = 3.0
MAX_VIDEO_SECONDS = 30.05


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


def probe_video_seconds(path: Path) -> float:
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


def has_audio_stream(path: Path) -> bool:
    process = run_command(
        [
            resolve_command("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    return bool(process.stdout.strip())


def resolve_video_input(request: dict[str, Any]) -> str:
    value = str(request.get("video_input") or request.get("video_url") or request.get("input_video") or "").strip()
    if not value:
        raise ValueError("motion_control requests must include video_input")
    if is_url(value):
        return value
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        raise FileNotFoundError(f"input video does not exist: {value}")
    duration = probe_video_seconds(path)
    if duration < MIN_VIDEO_SECONDS or duration > MAX_VIDEO_SECONDS:
        raise ValueError(
            f"input video duration must be between {MIN_VIDEO_SECONDS:.0f}s and {MAX_VIDEO_SECONDS:.2f}s for motion_control; got {duration:.3f}s"
        )
    return str(path)


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


def build_reference_edit_prompt(character_id: str, direction: str) -> str:
    return "\n".join(
        [
            "Create one clean photorealistic vertical character reference image for motion transfer.",
            f"Character: preserve {character_id}'s identity, face, age, hair, body proportions, and recognizable appearance from the input image.",
            f"Requested background/outfit modification: {direction}",
            "Apply only the requested background and outfit changes. Keep the character unobstructed with clear full-body or upper-body proportions suitable for video motion control.",
            "Do not add captions, subtitles, slogans, UI, title cards, watermarks, logos, stickers, generated text, or extra characters.",
        ]
    )


def maybe_edit_reference_image(
    fal_client: Any,
    character_dir: Path,
    character_id: str,
    direction: str,
) -> tuple[str, dict[str, Any] | None, str]:
    reference_image = character_dir / "reference.png"
    if not reference_image.is_file():
        raise FileNotFoundError(f"missing character reference image: {reference_image}")

    source_url = fal_client.upload_file(str(reference_image))
    if not direction:
        return source_url, None, str(reference_image)

    prompt = build_reference_edit_prompt(character_id, direction)
    arguments = {
        "prompt": prompt,
        "image_urls": [source_url],
        "num_images": 1,
        "aspect_ratio": "9:16",
        "output_format": "png",
        "safety_tolerance": "1",
        "resolution": "1K",
        "limit_generations": True,
    }
    result = fal_client.subscribe(NANO_EDIT_MODEL, arguments=arguments, with_logs=True, client_timeout=300)
    image_url = extract_first_image_url(result)
    download_file(image_url, MODIFIED_IMAGE_PATH)
    return image_url, {"model": NANO_EDIT_MODEL, "arguments": arguments, "result": result, "path": str(MODIFIED_IMAGE_PATH)}, str(MODIFIED_IMAGE_PATH)


def build_motion_prompt(character_id: str, direction: str) -> str:
    lines = [
        "Transfer the exact body movement, timing, gesture rhythm, pose progression, camera movement, and action path from the reference video.",
        f"Use {character_id} and the background/outfit shown in the supplied reference image as the generated video's character and visual setting.",
        "Do not reinterpret the motion. Preserve the reference video's movement direction, pace, framing changes, and action beats as closely as possible.",
    ]
    if direction:
        lines.append(f"Respect this requested visual modification in the image reference: {direction}")
    lines.append("Keep the original sound from the reference video.")
    return " ".join(lines)


def run_motion_control(fal_client: Any, image_url: str, video_input: str, prompt: str) -> dict[str, Any]:
    if is_url(video_input):
        video_url = video_input
    else:
        video_url = fal_client.upload_file(video_input)

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
    if not has_audio_stream(RAW_VIDEO_PATH):
        raise RuntimeError("Kling motion-control output did not include an audio stream even though keep_original_sound was true")
    FINAL_VIDEO_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RAW_VIDEO_PATH, FINAL_VIDEO_PATH)
    RAW_VIDEO_PATH.chmod(0o600)
    FINAL_VIDEO_PATH.chmod(0o600)
    return {
        "model": KLING_MOTION_CONTROL_MODEL,
        "arguments": arguments,
        "result": result,
        "downloaded_video": str(RAW_VIDEO_PATH),
        "final_video_path": str(FINAL_VIDEO_PATH),
    }


def write_learning(
    request: dict[str, Any],
    character_id: str,
    direction: str,
    video_input: str,
    reference_path: str,
    final_video_path: Path,
    elapsed_seconds: float,
) -> None:
    lines = [
        "# Learning",
        "",
        f"- Request ID: `{request.get('request_id', Path.cwd().name)}`",
        "- Process B mode: motion_control",
        f"- Character: {character_id}",
        f"- Input video: {video_input}",
        f"- Reference image used: {reference_path}",
        f"- Reference image edited with Nano Banana: {'yes' if direction else 'no'}",
        f"- Motion control model: {KLING_MOTION_CONTROL_MODEL}",
        "- Character orientation: video",
        "- Keep original sound: true",
        f"- Final video: {final_video_path}",
        f"- Elapsed seconds: {elapsed_seconds:.3f}",
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
    write_status("running", "motion_control_setup")

    if "FAL_KEY" not in os.environ or not os.environ["FAL_KEY"].strip():
        raise RuntimeError("FAL_KEY is not set. Put it in /etc/ugc-pipeline/fal.env for supervisor injection.")
    try:
        import fal_client
    except ImportError as exc:
        raise RuntimeError("fal-client is not installed. Install it in /opt/ugc-pipeline-venv.") from exc

    request = load_request(request_path)
    character_id = str(request.get("character_id") or character_dir.name or "astrid").strip().lower()
    direction = str(request.get("direction") or request.get("prompt") or request.get("visual_direction") or "").strip()
    video_input = resolve_video_input(request)

    write_status("running", "reference_image", edit_reference=bool(direction))
    image_url, nano_edit, reference_path = maybe_edit_reference_image(fal_client, character_dir, character_id, direction)

    prompt = build_motion_prompt(character_id, direction)
    write_status("running", "kling_motion_control", model=KLING_MOTION_CONTROL_MODEL)
    kling = run_motion_control(fal_client, image_url, video_input, prompt)

    plan = {
        "mode": "motion_control",
        "character_id": character_id,
        "direction": direction,
        "video_input": str(request.get("video_input") or request.get("video_url") or request.get("input_video")),
        "reference_image_path": reference_path,
        "reference_image_url": image_url,
        "reference_edit_model": NANO_EDIT_MODEL if direction else None,
        "motion_control_model": KLING_MOTION_CONTROL_MODEL,
        "character_orientation": "video",
        "keep_original_sound": True,
        "prompt": prompt,
        "raw_video_path": str(RAW_VIDEO_PATH),
        "final_video_path": str(FINAL_VIDEO_PATH),
    }
    write_json(PLAN_PATH, plan)
    write_json(
        RESULT_PATH,
        {
            "mode": "motion_control",
            "request_id": request.get("request_id", Path.cwd().name),
            "ran_at": now_utc(),
            "nano_edit": nano_edit,
            "kling": kling,
        },
    )

    elapsed_seconds = monotonic() - started
    write_learning(request, character_id, direction, video_input, reference_path, FINAL_VIDEO_PATH, elapsed_seconds)
    write_status(
        "succeeded",
        "process_b",
        mode="motion_control",
        elapsed_seconds=round(elapsed_seconds, 3),
        character_id=character_id,
        input_video=str(request.get("video_input") or request.get("video_url") or request.get("input_video")),
        reference_image_path=reference_path,
        reference_edited=bool(direction),
        plan_path=str(PLAN_PATH),
        result_path=str(RESULT_PATH),
        final_video_path=str(FINAL_VIDEO_PATH),
    )
    print(FINAL_VIDEO_PATH)
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
            write_status("failed", "process_b", mode="motion_control", error=str(exc), error_type=type(exc).__name__)
        except Exception:
            pass
        print(f"motion_control failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
