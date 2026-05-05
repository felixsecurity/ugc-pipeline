#!/usr/bin/env python3
"""Orchestrate multiple motion-control jobs in one batch without busy-polling."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any

NANO_EDIT_MODEL = "fal-ai/nano-banana-2/edit"
KLING_MOTION_CONTROL_MODEL = "fal-ai/kling-video/v2.6/standard/motion-control"
CHARACTERS_DIR = Path(__file__).resolve().parents[1] / "characters"
ORCHESTRATE_PATH = Path("orchestrate.json")
EVENTS_PATH = Path("events.log")
REFLECTION_PATH = Path("reflection.md")
WWW_ROOT = Path("/var/www/html")
MIN_VIDEO_SECONDS = 3.0
MAX_VIDEO_SECONDS = 30.05
MAX_FAL_UPLOAD_BYTES = 10 * 1024 * 1024
NANO_POLL_SECONDS = 10
KLING_POLL_SECONDS = 150
NANO_RETRY_LIMIT = 1
GLOBAL_NEGATIVES = ["No text on screen.", "No text overlays."]
STATUS_DONE = "done"
STATUS_FAILED = "failed"

BACKGROUND_TERMS = (
    "background",
    "scene",
    "setting",
    "environment",
    "location",
    "backdrop",
    "room",
    "house",
    "pool",
    "sofa",
    "door",
    "bedsheet",
    "painting",
)
WARDROBE_TERMS = (
    "clothing",
    "outfit",
    "wardrobe",
    "shirt",
    "top",
    "pants",
    "dress",
    "chain",
    "sleeveless",
    "cleavage",
)
MOTION_TERMS = ("motion", "mimic", "exact", "gesture", "face movement", "body movement")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", required=True, type=Path)
    parser.add_argument("--once", action="store_true", help="Process due work once and exit without sleeping.")
    parser.add_argument("--max-sleep-seconds", type=int, default=300)
    return parser.parse_args()


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_utc() -> str:
    return now().isoformat()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def iso_at_offset(seconds: int) -> str:
    return (now() + timedelta(seconds=seconds)).isoformat()


def write_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(mode)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def append_event(batch_dir: Path, payload: dict[str, Any]) -> None:
    event_path = batch_dir / EVENTS_PATH
    event_path.parent.mkdir(parents=True, exist_ok=True)
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def event(
    batch_dir: Path,
    video_id: str,
    phase: str,
    event_name: str,
    *,
    status: str = "ok",
    duration_ms: int | None = None,
    wait_until: str | None = None,
    remote_job_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "ts": now_utc(),
        "video_id": video_id,
        "phase": phase,
        "event": event_name,
        "status": status,
    }
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if wait_until:
        payload["wait_until"] = wait_until
    if remote_job_id:
        payload["remote_job_id"] = remote_job_id
    if details:
        payload["details"] = details
    append_event(batch_dir, payload)


def resolve_command(name: str) -> str:
    candidate = Path(sys.executable).parent / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    resolved = shutil.which(name)
    if resolved:
        return resolved
    raise RuntimeError(f"{name} is required")


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if process.returncode != 0:
        raise RuntimeError(f"{command[0]} failed with exit {process.returncode}: {process.stderr[-2400:]}")
    return process


def download_file(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=300) as response:
        path.write_bytes(response.read())
    path.chmod(0o600)


def extract_first_image_url(result: dict[str, Any]) -> str:
    images = result.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict) and first.get("url"):
            return str(first["url"])
    image = result.get("image")
    if isinstance(image, dict) and image.get("url"):
        return str(image["url"])
    raise RuntimeError("Nano Banana result did not include an image URL")


def is_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def probe_video_seconds(path: Path) -> float:
    result = run_command(
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
    return float(result.stdout.strip())


def has_audio_stream(path: Path) -> bool:
    result = run_command(
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
    return bool(result.stdout.strip())


def extract_second_frame(video_input: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    run_command(
        [
            resolve_command("ffmpeg"),
            "-y",
            "-i",
            str(video_input),
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


def prepare_reference_for_upload(reference_image: Path, output_path: Path) -> Path:
    if reference_image.stat().st_size <= MAX_FAL_UPLOAD_BYTES:
        return reference_image
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            resolve_command("ffmpeg"),
            "-y",
            "-i",
            str(reference_image),
            "-vf",
            "scale='min(1024,iw)':-2",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output_path),
        ]
    )
    if output_path.stat().st_size > MAX_FAL_UPLOAD_BYTES:
        run_command(
            [
                resolve_command("ffmpeg"),
                "-y",
                "-i",
                str(reference_image),
                "-vf",
                "scale='min(768,iw)':-2",
                "-frames:v",
                "1",
                "-q:v",
                "4",
                str(output_path),
            ]
        )
    if output_path.stat().st_size > MAX_FAL_UPLOAD_BYTES:
        raise RuntimeError(f"prepared reference image still exceeds fal upload limit: {output_path}")
    output_path.chmod(0o600)
    return output_path


def canonical_character_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "", value.strip().lower())


def load_character(character_id: str) -> dict[str, Any]:
    character_dir = CHARACTERS_DIR / character_id
    if not character_dir.is_dir():
        raise FileNotFoundError(f"unknown character: {character_id}")
    metadata_path = character_dir / "character.json"
    metadata = load_json(metadata_path) if metadata_path.is_file() else {}
    reference = character_dir / "reference.png"
    if not reference.is_file():
        raise FileNotFoundError(f"missing character reference image: {reference}")
    return {
        "character_id": character_id,
        "character_dir": str(character_dir),
        "reference_image": str(reference),
        "metadata": metadata,
    }


def split_instruction_blocks(text: str) -> tuple[list[list[str]], list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    trailing: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                blocks.append(current)
                current = []
            continue
        if re.fullmatch(r"(?:input|vid)_[A-Za-z0-9_-]+", line):
            if current:
                blocks.append(current)
            current = [line]
            continue
        if current:
            current.append(line)
        else:
            trailing.append(line)
    if current:
        blocks.append(current)
    return blocks, trailing


def normalize_global_rules(lines: list[str]) -> list[str]:
    normalized = [line.strip() for line in lines if line.strip()]
    merged = " ".join(normalized).lower()
    rules: list[str] = []
    if "no text" in merged:
        rules.append("No text on screen.")
        rules.append("No text overlays.")
    for negative in GLOBAL_NEGATIVES:
        if negative not in rules:
            rules.append(negative)
    return rules


def parse_characters_field(value: str) -> tuple[list[dict[str, str]], str]:
    stripped = value.strip()
    if not stripped:
        raise ValueError("characters field is empty")
    if ":" not in stripped:
        character_id = canonical_character_name(stripped)
        return [{"character_id": character_id, "placement": "center", "label": "subject"}], character_id

    placements: list[dict[str, str]] = []
    for segment in [part.strip() for part in stripped.split(".") if part.strip()]:
        if ":" not in segment:
            continue
        role, name = segment.split(":", 1)
        role_lower = role.strip().lower()
        placement = "center"
        if "left" in role_lower:
            placement = "left"
        elif "right" in role_lower:
            placement = "right"
        elif "center" in role_lower or "middle" in role_lower:
            placement = "center"
        placements.append(
            {
                "character_id": canonical_character_name(name),
                "placement": placement,
                "label": role.strip(),
            }
        )
    if not placements:
        raise ValueError(f"could not parse characters field: {value}")
    summary = ", ".join(f"{item['character_id']}:{item['placement']}" for item in placements)
    return placements, summary


def parse_instruction_block(block: list[str]) -> dict[str, Any]:
    if len(block) < 2:
        raise ValueError(f"instruction block is incomplete: {block}")
    video_id = block[0]
    second_line = block[1].strip()
    mode_candidate = second_line.lower().rstrip(".")
    if ":" in second_line or mode_candidate not in {"motion_control", "motion control"}:
        mode = "motion_control"
        field_lines = block[1:]
    else:
        mode = mode_candidate
        field_lines = block[2:]
    fields: dict[str, str] = {}
    freeform: list[str] = []
    for line in field_lines:
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
        else:
            freeform.append(line)
    character_layout: list[dict[str, str]]
    character_summary = ""
    if "characters" in fields:
        character_layout, character_summary = parse_characters_field(fields["characters"])
    elif "character" in fields:
        character_layout, character_summary = parse_characters_field(fields["character"])
    else:
        raise ValueError(f"missing character or characters field for {video_id}")
    return {
        "video_id": video_id,
        "mode": mode,
        "fields": fields,
        "character_layout": character_layout,
        "character_summary": character_summary,
        "freeform": freeform,
    }


def load_batch_instructions(batch_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    instructions_path = batch_dir / "instructions.txt"
    if not instructions_path.is_file():
        raise FileNotFoundError(f"missing instructions.txt in {batch_dir}")
    text = instructions_path.read_text(encoding="utf-8")
    blocks, trailing = split_instruction_blocks(text)
    requests = [parse_instruction_block(block) for block in blocks]
    return requests, normalize_global_rules(trailing)


def validate_batch_inputs(batch_dir: Path, requests: list[dict[str, Any]]) -> None:
    for request in requests:
        video_path = batch_dir / f"{request['video_id']}.mp4"
        if not video_path.is_file():
            raise FileNotFoundError(f"missing source video for {request['video_id']}: {video_path}")


def build_direction_summary(request: dict[str, Any]) -> str:
    fields = request["fields"]
    lines: list[str] = []
    for key in ("clothing", "background", "motion", "other"):
        value = fields.get(key)
        if value:
            lines.append(f"{key}: {value}")
    lines.extend(request["freeform"])
    return " ".join(lines).strip()


def build_prompt_brief(request: dict[str, Any], global_rules: list[str], video_path: Path, duration_s: float) -> dict[str, Any]:
    character_assets = [load_character(item["character_id"]) for item in request["character_layout"]]
    constraints = {
        "clothing": request["fields"].get("clothing", ""),
        "background": request["fields"].get("background", ""),
        "motion": request["fields"].get("motion", ""),
        "other": request["fields"].get("other", ""),
        "freeform": request["freeform"],
    }
    reasoning_steps = [
        "Resolve all named characters and preserve each identity exactly from the reference assets.",
        "Use the second frame from the source video as the spatial and pose anchor for the generated reference image.",
        "Translate clothing, background, and framing requests into explicit visual instructions instead of copying raw user text blindly.",
        "For two-character scenes, preserve left/right placement exactly and keep each character visually distinct.",
        "Treat motion instructions as hard constraints for Kling: preserve timing, gesture rhythm, face movement, body movement, and camera motion.",
        "Apply global negative instructions to both models.",
    ]
    if constraints["other"]:
        reasoning_steps.append("Treat framing extensions such as true 9:16 as composition requirements for the generated reference image.")
    if constraints["background"] and "identical" in constraints["background"].lower():
        reasoning_steps.append("Keep the scene as close as possible to the source frame when the request says identical.")
    if constraints["background"] and any(term in constraints["background"].lower() for term in ("inspired", "change", "replace", "luxurious")):
        reasoning_steps.append("When the request says inspired by the video, preserve recognizability while applying the requested visual change.")
    return {
        "video_id": request["video_id"],
        "mode": request["mode"],
        "source_video": str(video_path),
        "source_video_duration_seconds": round(duration_s, 3),
        "character_layout": [
            {
                "character_id": item["character_id"],
                "placement": item["placement"],
                "label": item["label"],
                "reference_image": asset["reference_image"],
            }
            for item, asset in zip(request["character_layout"], character_assets)
        ],
        "constraints": constraints,
        "global_rules": global_rules,
        "reasoning_steps": reasoning_steps,
    }


def characterize_scene(brief: dict[str, Any]) -> list[str]:
    constraints = brief["constraints"]
    notes: list[str] = []
    clothing = str(constraints.get("clothing") or "").strip()
    background = str(constraints.get("background") or "").strip()
    motion = str(constraints.get("motion") or "").strip()
    other = str(constraints.get("other") or "").strip()

    if clothing:
        notes.append(f"Clothing direction: {clothing}")
    if background:
        notes.append(f"Background direction: {background}")
    if motion:
        notes.append(f"Motion constraint: {motion}")
    if other:
        notes.append(f"Additional constraint: {other}")
    return notes


def build_nano_prompt(brief: dict[str, Any]) -> str:
    characters = brief["character_layout"]
    lines = [
        "Create one clean photorealistic vertical reference image for Kling motion control.",
        "Use the supplied source video frame as the exact pose, body angle, limb placement, framing, and perspective anchor.",
    ]
    if len(characters) == 1:
        character = characters[0]
        lines.append(
            f"Replace the source subject with {character['character_id']} while preserving that character's identity, face, age, hair, proportions, and recognizable appearance from the supplied reference image."
        )
    else:
        lines.append("Replace the source people with the supplied character references while preserving each identity exactly and keeping them visually distinct.")
        placement_notes = ", ".join(f"{item['character_id']} on the {item['placement']}" for item in characters)
        lines.append(f"Maintain character placement exactly as follows: {placement_notes}.")
    lines.append("Copy head angle, torso angle, hand position, stance, facial orientation, and body silhouette as closely as possible from the source frame.")
    lines.extend(characterize_scene(brief))
    lines.append("Keep the result unobstructed and suitable as the starting image for motion control.")
    lines.extend(brief["global_rules"])
    lines.append("Do not add logos, captions, subtitles, stickers, UI, watermarks, or extra people.")
    return " ".join(lines)


def build_nano_retry_prompt(brief: dict[str, Any]) -> str:
    base_prompt = build_nano_prompt(brief)
    retry_addendum = (
        " Retry variant: keep the same pose, identity, and framing, but favor a cleaner, less cluttered composition, "
        "simpler background geometry, and a stronger single-subject silhouette so the generator can return one valid output."
    )
    return base_prompt + retry_addendum


def build_kling_prompt(brief: dict[str, Any]) -> str:
    characters = brief["character_layout"]
    lines = [
        "Transfer the exact body movement, face movement, timing, gesture rhythm, pose progression, camera movement, and action path from the reference video.",
        "Use the supplied generated reference image as the visual identity and scene source for the output video.",
        "Do not reinterpret the motion.",
    ]
    if len(characters) == 1:
        lines.append(f"Keep {characters[0]['character_id']} consistent with the generated reference image throughout the clip.")
    else:
        lines.append("Preserve the left/right identity assignment of all characters from the generated reference image throughout the full motion sequence.")
    lines.extend(characterize_scene(brief))
    lines.append("Keep the original sound from the source video.")
    lines.extend(brief["global_rules"])
    return " ".join(lines)


def save_prompt_artifacts(video_dir: Path, brief: dict[str, Any], nano_prompt: str, kling_prompt: str) -> tuple[Path, Path]:
    brief_path = video_dir / "prompt_brief.json"
    invocation_path = video_dir / "invocation_plan.json"
    write_json(brief_path, brief)
    write_json(
        invocation_path,
        {
            "nano_banana": {
                "model": NANO_EDIT_MODEL,
                "prompt": nano_prompt,
            },
            "kling_motion_control": {
                "model": KLING_MOTION_CONTROL_MODEL,
                "prompt": kling_prompt,
                "character_orientation": "video",
                "keep_original_sound": True,
            },
        },
    )
    return brief_path, invocation_path


def initial_video_state(batch_dir: Path, request: dict[str, Any], global_rules: list[str]) -> dict[str, Any]:
    source_video = batch_dir / f"{request['video_id']}.mp4"
    video_dir = batch_dir / "videos" / request["video_id"]
    return {
        "video_id": request["video_id"],
        "mode": request["mode"],
        "phase": "ready_for_prompting",
        "next_action_at": now_utc(),
        "request": {
            **request,
            "global_rules": global_rules,
        },
        "source_video": str(source_video),
        "video_dir": str(video_dir),
        "artifacts": {
            "request_json": str(video_dir / "request.json"),
            "prompt_brief": str(video_dir / "prompt_brief.json"),
            "invocation_plan": str(video_dir / "invocation_plan.json"),
            "second_frame": str(video_dir / "output_images" / "motion_control_frame_02.png"),
            "pose_reference": str(video_dir / "output_images" / "motion_control_reference.png"),
            "upload_reference": str(video_dir / "output_images" / "motion_control_reference_upload.jpg"),
            "nano_result": str(video_dir / "nano_banana_result.json"),
            "kling_result": str(video_dir / "kling_motion_control_result.json"),
            "raw_video": str(video_dir / "output_videos" / "motion_control.mp4"),
            "final_video": str(video_dir / "output_videos" / "final.mp4"),
            "status": str(video_dir / "status.json"),
            "learning": str(video_dir / "learning.md"),
        },
        "remote": {
            "nano": {
                "attempt": 0,
                "retry_count": 0,
            },
            "kling": {},
        },
        "timings": {
            "created_at": now_utc(),
        },
        "last_error": None,
    }


def initialize_state(batch_dir: Path) -> dict[str, Any]:
    requests, global_rules = load_batch_instructions(batch_dir)
    validate_batch_inputs(batch_dir, requests)
    for request in requests:
        if request["mode"] != "motion_control":
            raise ValueError(f"unsupported mode in this batch implementation: {request['mode']}")
    videos = {request["video_id"]: initial_video_state(batch_dir, request, global_rules) for request in requests}
    return {
        "batch_dir": str(batch_dir),
        "created_at": now_utc(),
        "updated_at": now_utc(),
        "status": "running",
        "global_rules": global_rules,
        "scheduler": {
            "nano_poll_seconds": NANO_POLL_SECONDS,
            "kling_poll_seconds": KLING_POLL_SECONDS,
            "next_action_at": now_utc(),
            "completed_videos": [],
            "failed_videos": [],
        },
        "videos": videos,
    }


def write_state(batch_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_utc()
    completed = [video_id for video_id, video in state["videos"].items() if video["phase"] == STATUS_DONE]
    failed = [video_id for video_id, video in state["videos"].items() if video["phase"] == STATUS_FAILED]
    state["scheduler"]["completed_videos"] = completed
    state["scheduler"]["failed_videos"] = failed
    pending_times = [parse_timestamp(video.get("next_action_at")) for video in state["videos"].values() if video["phase"] not in {STATUS_DONE, STATUS_FAILED}]
    pending_times = [ts for ts in pending_times if ts is not None]
    state["scheduler"]["next_action_at"] = min(pending_times).isoformat() if pending_times else None
    if pending_times:
        state["status"] = "running"
    if len(completed) + len(failed) == len(state["videos"]):
        state["status"] = STATUS_FAILED if failed else STATUS_DONE
    if state["status"] in {STATUS_DONE, STATUS_FAILED} and completed:
        published_ids = state.get("published_video_ids") or []
        if published_ids != completed:
            publish_dir = publish_final_videos(batch_dir, state, completed)
            state["published_www_dir"] = str(publish_dir)
            state["published_video_ids"] = completed
            append_event(
                batch_dir,
                {
                    "ts": now_utc(),
                    "video_id": "batch",
                    "phase": "publish",
                    "event": "published_final_videos",
                    "status": "ok",
                    "details": {
                        "publish_dir": str(publish_dir),
                        "video_count": len(completed),
                    },
                },
            )
    write_json(batch_dir / ORCHESTRATE_PATH, state)


def load_or_init_state(batch_dir: Path) -> dict[str, Any]:
    path = batch_dir / ORCHESTRATE_PATH
    return load_json(path) if path.is_file() else initialize_state(batch_dir)


def render_publish_index(batch_dir: Path, completed_videos: list[str]) -> str:
    items = "\n".join(
        f'      <li><a href="{video_id}.mp4">{video_id}.mp4</a></li>' for video_id in sorted(completed_videos)
    )
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "  <head>",
            '    <meta charset="utf-8" />',
            '    <meta name="viewport" content="width=device-width, initial-scale=1" />',
            f"    <title>{batch_dir.name} outputs</title>",
            "    <style>",
            "      body { font-family: Arial, sans-serif; margin: 2rem; line-height: 1.5; }",
            "      h1 { margin-bottom: 0.25rem; }",
            "      .meta { color: #555; margin-bottom: 1.5rem; }",
            "      ul { padding-left: 1.2rem; }",
            "      li { margin: 0.35rem 0; }",
            "      a { text-decoration: none; }",
            "      a:hover { text-decoration: underline; }",
            "    </style>",
            "  </head>",
            "  <body>",
            f"    <h1>{batch_dir.name} outputs</h1>",
            f"    <div class=\"meta\">{len(completed_videos)} successful video(s) published here.</div>",
            "    <ul>",
            items,
            "    </ul>",
            "  </body>",
            "</html>",
            "",
        ]
    )


def publish_final_videos(batch_dir: Path, state: dict[str, Any], completed_videos: list[str]) -> Path:
    publish_dir = WWW_ROOT / batch_dir.name
    publish_dir.mkdir(parents=True, exist_ok=True)
    publish_dir.chmod(0o755)
    for existing in publish_dir.glob("*.mp4"):
        if existing.is_file():
            existing.unlink()
    index_path = publish_dir / "index.html"
    if index_path.exists():
        index_path.unlink()
    for video_id in sorted(completed_videos):
        video = state["videos"][video_id]
        final_video = Path(video["artifacts"]["final_video"])
        if not final_video.is_file():
            raise FileNotFoundError(f"cannot publish missing final video: {final_video}")
        target = publish_dir / f"{video_id}.mp4"
        shutil.copyfile(final_video, target)
        target.chmod(0o644)
    index_path.write_text(render_publish_index(batch_dir, completed_videos), encoding="utf-8")
    index_path.chmod(0o644)
    return publish_dir


def status_payload(status_obj: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(status_obj):
        return dataclasses.asdict(status_obj)
    if isinstance(status_obj, dict):
        return status_obj
    payload = {}
    for name in dir(status_obj):
        if name.startswith("_"):
            continue
        value = getattr(status_obj, name)
        if callable(value):
            continue
        payload[name] = value
    return payload


def select_due_video(state: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    due: list[tuple[datetime, str, dict[str, Any]]] = []
    current = now()
    for video_id, video in state["videos"].items():
        if video["phase"] in {STATUS_DONE, STATUS_FAILED}:
            continue
        next_action = parse_timestamp(video.get("next_action_at")) or current
        if next_action <= current:
            due.append((next_action, video_id, video))
    if not due:
        return None
    due.sort(key=lambda item: (item[0], item[1]))
    _, video_id, video = due[0]
    return video_id, video


def ensure_video_layout(video: dict[str, Any]) -> Path:
    video_dir = Path(video["video_dir"])
    (video_dir / "output_images").mkdir(parents=True, exist_ok=True)
    (video_dir / "output_videos" / "work").mkdir(parents=True, exist_ok=True)
    return video_dir


def write_video_status(video: dict[str, Any], status: str, stage: str, **extra: Any) -> None:
    write_json(
        Path(video["artifacts"]["status"]),
        {
            "status": status,
            "stage": stage,
            "updated_at": now_utc(),
            **extra,
        },
    )


def transition(video: dict[str, Any], phase: str, next_action_at: str | None = None) -> None:
    video["phase"] = phase
    video["next_action_at"] = next_action_at or now_utc()
    video["timings"]["last_transition_at"] = now_utc()


def phase_build_prompt_package(batch_dir: Path, video_id: str, video: dict[str, Any]) -> None:
    started = monotonic()
    video_dir = ensure_video_layout(video)
    request = video["request"]
    source_video = Path(video["source_video"])
    duration_s = probe_video_seconds(source_video)
    if duration_s < MIN_VIDEO_SECONDS or duration_s > MAX_VIDEO_SECONDS:
        raise ValueError(
            f"input video duration must be between {MIN_VIDEO_SECONDS:.0f}s and {MAX_VIDEO_SECONDS:.2f}s; got {duration_s:.3f}s for {source_video}"
        )
    brief = build_prompt_brief(request, request["global_rules"], source_video, duration_s)
    nano_prompt = build_nano_prompt(brief)
    kling_prompt = build_kling_prompt(brief)
    write_json(Path(video["artifacts"]["request_json"]), request)
    save_prompt_artifacts(video_dir, brief, nano_prompt, kling_prompt)
    second_frame_path = extract_second_frame(source_video, Path(video["artifacts"]["second_frame"]))
    video["timings"]["prompt_built_at"] = now_utc()
    video["prompt_brief"] = brief
    video["nano_prompt"] = nano_prompt
    video["kling_prompt"] = kling_prompt
    write_video_status(video, "running", "prompt_package_built", second_frame_path=str(second_frame_path))
    transition(video, "nano_submit")
    event(
        batch_dir,
        video_id,
        "prompting",
        "prompt_package_built",
        duration_ms=int((monotonic() - started) * 1000),
        details={"source_video_seconds": round(duration_s, 3)},
    )


def submit_nano(fal_client: Any, video: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    brief = video["prompt_brief"]
    video_dir = Path(video["video_dir"])
    characters = brief["character_layout"]
    attempt = int(video["remote"].get("nano", {}).get("attempt", 0))
    reference_inputs: list[str] = []
    for item in characters:
        prepared = prepare_reference_for_upload(Path(item["reference_image"]), Path(video["artifacts"]["upload_reference"]))
        reference_inputs.append(fal_client.upload_file(str(prepared)))
    frame_url = fal_client.upload_file(video["artifacts"]["second_frame"])
    arguments = {
        "prompt": video["nano_prompt"] if attempt == 0 else build_nano_retry_prompt(brief),
        "image_urls": [*reference_inputs, frame_url],
        "num_images": 1,
        "aspect_ratio": "9:16",
        "output_format": "png",
        "safety_tolerance": "1",
        "resolution": "2K",
        "limit_generations": True,
    }
    handle = fal_client.submit(NANO_EDIT_MODEL, arguments=arguments)
    write_json(
        Path(video["artifacts"]["nano_result"]),
        {
            "model": NANO_EDIT_MODEL,
            "arguments": arguments,
            "request_id": handle.request_id,
            "submitted_at": now_utc(),
            "attempt": attempt,
            "prompt_variant": "initial" if attempt == 0 else "retry",
        },
    )
    return handle.request_id, arguments


def phase_submit_nano(batch_dir: Path, fal_client: Any, video_id: str, video: dict[str, Any], state: dict[str, Any]) -> None:
    started = monotonic()
    request_id, arguments = submit_nano(fal_client, video)
    wait_until = iso_at_offset(state["scheduler"]["nano_poll_seconds"])
    video["remote"]["nano"] = {
        "request_id": request_id,
        "model": NANO_EDIT_MODEL,
        "arguments": arguments,
        "submitted_at": now_utc(),
        "attempt": int(video["remote"].get("nano", {}).get("attempt", 0)),
    }
    write_video_status(video, "running", "nano_submitted", request_id=request_id)
    transition(video, "nano_wait", wait_until)
    event(
        batch_dir,
        video_id,
        "nano_wait",
        "remote_submitted",
        duration_ms=int((monotonic() - started) * 1000),
        wait_until=wait_until,
        remote_job_id=request_id,
    )


def phase_poll_nano(batch_dir: Path, fal_client: Any, video_id: str, video: dict[str, Any], state: dict[str, Any]) -> None:
    started = monotonic()
    remote = video["remote"]["nano"]
    try:
        status_obj = fal_client.status(remote["model"], remote["request_id"], with_logs=True)
        payload = status_payload(status_obj)
        status_name = status_obj.__class__.__name__.lower()
        if status_name == "completed":
            result = fal_client.result(remote["model"], remote["request_id"])
            image_url = extract_first_image_url(result)
            download_file(image_url, Path(video["artifacts"]["pose_reference"]))
            existing = load_json(Path(video["artifacts"]["nano_result"]))
            existing["status"] = payload
            existing["result"] = result
            existing["image_url"] = image_url
            write_json(Path(video["artifacts"]["nano_result"]), existing)
            video["remote"]["nano"]["completed_at"] = now_utc()
            video["remote"]["nano"]["image_url"] = image_url
            video["remote"]["nano"]["result_path"] = video["artifacts"]["nano_result"]
            write_video_status(video, "running", "nano_completed", image_url=image_url)
            transition(video, "kling_ready")
            event(
                batch_dir,
                video_id,
                "nano_wait",
                "remote_completed",
                duration_ms=int((monotonic() - started) * 1000),
                remote_job_id=remote["request_id"],
            )
            return
    except Exception as exc:
        retry_count = int(video["remote"].get("nano", {}).get("retry_count", 0))
        if retry_count < NANO_RETRY_LIMIT:
            attempt = retry_count + 1
            started_retry = monotonic()
            video["remote"]["nano"]["retry_count"] = attempt
            video["remote"]["nano"]["attempt"] = attempt
            request_id, arguments = submit_nano(fal_client, video)
            wait_until = iso_at_offset(state["scheduler"]["nano_poll_seconds"])
            video["remote"]["nano"].update(
                {
                    "request_id": request_id,
                    "model": NANO_EDIT_MODEL,
                    "arguments": arguments,
                    "submitted_at": now_utc(),
                    "retries": attempt,
                }
            )
            write_video_status(video, "running", "nano_retry_submitted", request_id=request_id, retry_count=attempt)
            transition(video, "nano_wait", wait_until)
            event(
                batch_dir,
                video_id,
                "nano_wait",
                "retry_submitted",
                duration_ms=int((monotonic() - started_retry) * 1000),
                wait_until=wait_until,
                remote_job_id=request_id,
                details={"retry_count": attempt, "error": str(exc)},
            )
            return
        raise

    wait_until = iso_at_offset(state["scheduler"]["nano_poll_seconds"])
    transition(video, "nano_wait", wait_until)
    event(
        batch_dir,
        video_id,
        "nano_wait",
        "poll_rescheduled",
        status="waiting",
        duration_ms=int((monotonic() - started) * 1000),
        wait_until=wait_until,
        remote_job_id=remote["request_id"],
        details={"status": payload},
    )


def submit_kling(fal_client: Any, video: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    image_url = video["remote"]["nano"]["image_url"]
    source_video = video["source_video"]
    video_url = source_video if is_url(source_video) else fal_client.upload_file(source_video)
    arguments = {
        "prompt": video["kling_prompt"],
        "image_url": image_url,
        "video_url": video_url,
        "character_orientation": "video",
        "keep_original_sound": True,
    }
    handle = fal_client.submit(KLING_MOTION_CONTROL_MODEL, arguments=arguments)
    write_json(
        Path(video["artifacts"]["kling_result"]),
        {
            "model": KLING_MOTION_CONTROL_MODEL,
            "arguments": arguments,
            "request_id": handle.request_id,
            "submitted_at": now_utc(),
        },
    )
    return handle.request_id, arguments


def phase_submit_kling(batch_dir: Path, fal_client: Any, video_id: str, video: dict[str, Any], state: dict[str, Any]) -> None:
    started = monotonic()
    request_id, arguments = submit_kling(fal_client, video)
    wait_until = iso_at_offset(state["scheduler"]["kling_poll_seconds"])
    video["remote"]["kling"] = {
        "request_id": request_id,
        "model": KLING_MOTION_CONTROL_MODEL,
        "arguments": arguments,
        "submitted_at": now_utc(),
    }
    write_video_status(video, "running", "kling_submitted", request_id=request_id)
    transition(video, "kling_wait", wait_until)
    event(
        batch_dir,
        video_id,
        "kling_wait",
        "remote_submitted",
        duration_ms=int((monotonic() - started) * 1000),
        wait_until=wait_until,
        remote_job_id=request_id,
    )


def phase_poll_kling(batch_dir: Path, fal_client: Any, video_id: str, video: dict[str, Any], state: dict[str, Any]) -> None:
    started = monotonic()
    remote = video["remote"]["kling"]
    status_obj = fal_client.status(remote["model"], remote["request_id"], with_logs=True)
    payload = status_payload(status_obj)
    status_name = status_obj.__class__.__name__.lower()
    if status_name == "completed":
        result = fal_client.result(remote["model"], remote["request_id"])
        output_url = result.get("video", {}).get("url")
        if not output_url:
            raise RuntimeError("Kling motion-control result did not include video.url")
        raw_video_path = Path(video["artifacts"]["raw_video"])
        final_video_path = Path(video["artifacts"]["final_video"])
        download_file(str(output_url), raw_video_path)
        final_video_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(raw_video_path, final_video_path)
        raw_video_path.chmod(0o600)
        final_video_path.chmod(0o600)
        existing = load_json(Path(video["artifacts"]["kling_result"]))
        existing["status"] = payload
        existing["result"] = result
        existing["output_url"] = output_url
        write_json(Path(video["artifacts"]["kling_result"]), existing)
        video["remote"]["kling"]["completed_at"] = now_utc()
        write_video_status(video, "running", "kling_completed", output_url=output_url)
        transition(video, "confirm_ready")
        event(
            batch_dir,
            video_id,
            "kling_wait",
            "remote_completed",
            duration_ms=int((monotonic() - started) * 1000),
            remote_job_id=remote["request_id"],
        )
        return
    wait_until = iso_at_offset(state["scheduler"]["kling_poll_seconds"])
    transition(video, "kling_wait", wait_until)
    event(
        batch_dir,
        video_id,
        "kling_wait",
        "poll_rescheduled",
        status="waiting",
        duration_ms=int((monotonic() - started) * 1000),
        wait_until=wait_until,
        remote_job_id=remote["request_id"],
        details={"status": payload},
    )


def phase_confirm(batch_dir: Path, video_id: str, video: dict[str, Any]) -> None:
    started = monotonic()
    final_video = Path(video["artifacts"]["final_video"])
    if not final_video.is_file():
        raise FileNotFoundError(f"missing final video: {final_video}")
    if not has_audio_stream(final_video):
        raise RuntimeError("final video is missing audio despite keep_original_sound=true")
    brief = video["prompt_brief"]
    lines = [
        "# Learning",
        "",
        f"- Video ID: `{video_id}`",
        "- Mode: motion_control",
        f"- Source video: {video['source_video']}",
        f"- Prompt brief: {video['artifacts']['prompt_brief']}",
        f"- Nano Banana result: {video['artifacts']['nano_result']}",
        f"- Kling result: {video['artifacts']['kling_result']}",
        f"- Final video: {video['artifacts']['final_video']}",
        "",
        "## Prompting Notes",
        "",
        "The prompt package was built from normalized request constraints rather than direct string concatenation.",
        "",
        "## Character Layout",
        "",
    ]
    for character in brief["character_layout"]:
        lines.append(f"- {character['character_id']} at {character['placement']}")
    lines.extend(["", "## Global Rules", ""])
    for rule in brief["global_rules"]:
        lines.append(f"- {rule}")
    Path(video["artifacts"]["learning"]).write_text("\n".join(lines) + "\n", encoding="utf-8")
    Path(video["artifacts"]["learning"]).chmod(0o600)
    write_video_status(video, STATUS_DONE, "confirmed", final_video=str(final_video))
    transition(video, STATUS_DONE, None)
    event(
        batch_dir,
        video_id,
        "confirm",
        "result_confirmed",
        duration_ms=int((monotonic() - started) * 1000),
        details={"final_video": str(final_video)},
    )


def mark_failed(batch_dir: Path, video_id: str, video: dict[str, Any], exc: Exception) -> None:
    failed_phase = video.get("phase", "unknown")
    video["last_error"] = str(exc)
    transition(video, STATUS_FAILED, None)
    write_video_status(video, STATUS_FAILED, "failed", error=str(exc))
    event(
        batch_dir,
        video_id,
        failed_phase,
        "failed",
        status="error",
        details={"error": str(exc)},
    )


def process_video(batch_dir: Path, fal_client: Any, state: dict[str, Any], video_id: str, video: dict[str, Any]) -> None:
    try:
        phase = video["phase"]
        if phase == "ready_for_prompting":
            phase_build_prompt_package(batch_dir, video_id, video)
        elif phase == "nano_submit":
            phase_submit_nano(batch_dir, fal_client, video_id, video, state)
        elif phase == "nano_wait":
            phase_poll_nano(batch_dir, fal_client, video_id, video, state)
        elif phase == "kling_ready":
            phase_submit_kling(batch_dir, fal_client, video_id, video, state)
        elif phase == "kling_wait":
            phase_poll_kling(batch_dir, fal_client, video_id, video, state)
        elif phase == "confirm_ready":
            phase_confirm(batch_dir, video_id, video)
        else:
            raise RuntimeError(f"unsupported phase: {phase}")
    except Exception as exc:
        mark_failed(batch_dir, video_id, video, exc)


def sleep_until_next_action(state: dict[str, Any], max_sleep_seconds: int) -> None:
    next_action = parse_timestamp(state["scheduler"].get("next_action_at"))
    if next_action is None:
        return
    delta = max(0.0, min(max_sleep_seconds, (next_action - now()).total_seconds()))
    if delta > 0:
        time.sleep(delta)


def import_fal_client() -> Any:
    if "FAL_KEY" not in os.environ or not os.environ["FAL_KEY"].strip():
        raise RuntimeError("FAL_KEY is not set")
    try:
        import fal_client
    except ImportError as exc:
        raise RuntimeError("fal-client is not installed in the selected Python environment") from exc
    return fal_client


def main() -> int:
    args = parse_args()
    batch_dir = args.batch_dir.resolve()
    if not batch_dir.is_dir():
        raise FileNotFoundError(f"batch directory does not exist: {batch_dir}")

    state = load_or_init_state(batch_dir)
    write_state(batch_dir, state)
    fal_client = import_fal_client()

    while True:
        due = select_due_video(state)
        if due is None:
            write_state(batch_dir, state)
            if state["status"] in {STATUS_DONE, STATUS_FAILED} or args.once:
                break
            sleep_until_next_action(state, args.max_sleep_seconds)
            state = load_json(batch_dir / ORCHESTRATE_PATH)
            continue
        video_id, video = due
        process_video(batch_dir, fal_client, state, video_id, video)
        write_state(batch_dir, state)
        if args.once:
            break

    return 0 if state["status"] != STATUS_FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
