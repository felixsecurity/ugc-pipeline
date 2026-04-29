#!/usr/bin/env python3
"""Basic Process C evaluator for generated image/video request folders."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVALUATION_PATH = "evaluation.md"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def int_at_most(value: Any, limit: int) -> bool:
    try:
        return int(value) <= limit
    except (TypeError, ValueError):
        return False


def evaluate_voice_over(request_dir: Path) -> int:
    checks: list[tuple[str, bool, str]] = []

    request_json = request_dir / "request.json"
    request_check = request_dir / "request_check.json"
    result_json = request_dir / "kling_voice_over_result.json"
    output_dir = request_dir / "output_images"
    video_dir = request_dir / "output_videos"
    work_dir = video_dir / "work"
    plan_json = request_dir / "voice_over_plan.json"
    audio_path = request_dir / "output_audio" / "voiceover.mp3"
    timestamps_json = request_dir / "whisper_timestamps.json"
    subtitles_path = work_dir / "subtitles.ass"
    final_video = video_dir / "final_subtitled.mp4"
    status_json = request_dir / "status.json"
    learning_md = request_dir / "learning.md"
    script_md = request_dir / "script.md"

    checks.append(("request.json exists", request_json.is_file(), "Process A request payload is present."))
    checks.append(("request_check.json exists", request_check.is_file(), "Process A safety check report is present."))
    checks.append(("script.md exists", script_md.is_file(), "Extracted voiceover text is present."))
    checks.append(("voiceover MP3 exists", audio_path.is_file(), "ElevenLabs audio is present."))
    checks.append(("voice_over_plan.json exists", plan_json.is_file(), "Voice-over plan is present."))
    checks.append(("kling_voice_over_result.json exists", result_json.is_file(), "Raw provider results are present."))
    checks.append(("output_images/ exists", output_dir.is_dir(), "Generated keyframes directory is present."))
    checks.append(("output_videos/ exists", video_dir.is_dir(), "Process B output video directory is present."))
    checks.append(("whisper_timestamps.json exists", timestamps_json.is_file(), "Whisper timestamps are present."))
    checks.append(("subtitles.ass exists", subtitles_path.is_file(), "ASS subtitle file is present."))
    checks.append(("final_subtitled.mp4 exists", final_video.is_file(), "Final subtitle-burned video is present."))
    checks.append(("status.json exists", status_json.is_file(), "Process B status metadata is present."))
    checks.append(("learning.md exists", learning_md.is_file(), "Process B learning note is present."))

    request: dict[str, Any] = load_json(request_json) if request_json.is_file() else {}
    status: dict[str, Any] = load_json(status_json) if status_json.is_file() else {}
    check_report: dict[str, Any] = load_json(request_check) if request_check.is_file() else {}
    plan: dict[str, Any] = load_json(plan_json) if plan_json.is_file() else {}
    result: dict[str, Any] = load_json(result_json) if result_json.is_file() else {}

    pngs = sorted(output_dir.glob("*")) if output_dir.is_dir() else []
    segment_mp4s = sorted(work_dir.glob("segment_*.mp4")) if work_dir.is_dir() else []
    segment_count = plan.get("segment_count")
    keyframe_count = plan.get("keyframe_count")

    checks.append(("Process A accepted request", bool(check_report.get("accepted")), "Request checker accepted the request."))
    checks.append(("Process B succeeded", status.get("status") == "succeeded", "Process B status is succeeded."))
    checks.append(("mode is voice_over", status.get("mode") == "voice_over" or request.get("process_b_mode") == "voice_over", "Request/status records voice_over mode."))
    checks.append(("keyframe count matches plan", len(pngs) == keyframe_count and len(pngs) > 1, "Generated keyframes match the planned count."))
    checks.append(("segment count matches plan", len(segment_mp4s) == segment_count and len(segment_mp4s) > 0, "Generated 5-second segment MP4s match the planned count."))
    checks.append(("final path matches plan", plan.get("final_video_path") == "output_videos/final_subtitled.mp4", "Plan points to the final subtitled video."))

    passed = all(item[1] for item in checks)
    models = ", ".join(str(result.get(key, "")) for key in ("nano_model", "kling_model") if result.get(key))

    lines = [
        "# Evaluation",
        "",
        f"- Evaluated at: {datetime.now(timezone.utc).isoformat()}",
        f"- Request ID: `{request.get('request_id', request_dir.name)}`",
        "- Mode: voice_over",
        f"- Models: `{models or 'unknown'}`",
        f"- Prompt: {request.get('prompt', '')}",
        f"- Character: {plan.get('character_id', request.get('character_id', 'unknown'))}",
        f"- Stage direction: {plan.get('stage_direction', '')}",
        f"- Audio duration seconds: {plan.get('audio_duration_seconds', 'unknown')}",
        f"- Rounded video duration seconds: {plan.get('rounded_video_duration_seconds', 'unknown')}",
        f"- Local keyframe count: {len(pngs)}",
        f"- Local segment count: {len(segment_mp4s)}",
        f"- Video path: output_videos/final_subtitled.mp4",
        f"- Overall status: {'passed' if passed else 'needs_review'}",
        "",
        "## Checks",
        "",
    ]

    for name, ok, description in checks:
        lines.append(f"- {'PASS' if ok else 'FAIL'}: {name} - {description}")

    lines.extend(
        [
            "",
            "## Human Review Notes",
            "",
            "- Verify the keyframe chain preserves character identity and follows the stage direction.",
            "- Verify generated images and video contain no disallowed nudity.",
            "- Verify the voiceover, subtitles, and final video timing are aligned.",
            "- Verify stitched segment transitions are acceptable before publishing.",
            "",
        ]
    )

    output_path = request_dir / EVALUATION_PATH
    output_path.write_text("\n".join(lines), encoding="utf-8")
    output_path.chmod(0o600)
    print(output_path)
    return 0 if passed else 1


def main(argv: list[str]) -> int:
    request_dir = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    checks: list[tuple[str, bool, str]] = []

    request_json = request_dir / "request.json"
    request_check = request_dir / "request_check.json"
    fal_result = request_dir / "fal_result.json"
    output_dir = request_dir / "output_images"
    video_dir = request_dir / "output_videos"
    video_plan = request_dir / "video_plan.json"
    status_json = request_dir / "status.json"
    learning_md = request_dir / "learning.md"

    request: dict[str, Any] = load_json(request_json) if request_json.is_file() else {}
    if request.get("process_b_mode") == "voice_over":
        return evaluate_voice_over(request_dir)

    checks.append(("request.json exists", request_json.is_file(), "Process A request payload is present."))
    checks.append(("request_check.json exists", request_check.is_file(), "Process A safety check report is present."))
    checks.append(("fal_result.json exists", fal_result.is_file(), "Process B fal result is present."))
    checks.append(("output_images/ exists", output_dir.is_dir(), "Process B output image directory is present."))
    checks.append(("output_videos/ exists", video_dir.is_dir(), "Process B output video directory is present."))
    checks.append(("video_plan.json exists", video_plan.is_file(), "Process B video edit plan is present."))
    checks.append(("status.json exists", status_json.is_file(), "Process B status metadata is present."))
    checks.append(("learning.md exists", learning_md.is_file(), "Process B learning note is present."))

    result: dict[str, Any] = load_json(fal_result) if fal_result.is_file() else {}
    status: dict[str, Any] = load_json(status_json) if status_json.is_file() else {}
    check_report: dict[str, Any] = load_json(request_check) if request_check.is_file() else {}
    plan: dict[str, Any] = load_json(video_plan) if video_plan.is_file() else {}
    downloaded = result.get("downloaded_images", []) if isinstance(result.get("downloaded_images", []), list) else []
    pngs = sorted(output_dir.glob("*")) if output_dir.is_dir() else []
    mp4s = sorted(video_dir.glob("*.mp4")) if video_dir.is_dir() else []
    video = result.get("video", {}) if isinstance(result.get("video", {}), dict) else {}
    request_details = result.get("request", {}) if isinstance(result.get("request", {}), dict) else {}

    checks.append(("Process A accepted request", bool(check_report.get("accepted")), "Request checker accepted the request."))
    checks.append(("Process B succeeded", status.get("status") == "succeeded", "Process B status is succeeded."))
    checks.append(("at least one image downloaded", len(downloaded) > 0 and len(pngs) > 0, "At least one local output image exists."))
    checks.append(("silent video rendered", bool(video.get("path")) and len(mp4s) > 0, "At least one local MP4 video exists."))
    checks.append(("video has no audio", plan.get("audio") == "none", "Video plan records silent-first output with no audio."))
    checks.append(("Nano Banana image cap respected", int_at_most(request_details.get("num_images"), 4), "Process B requested no more than four static images."))

    passed = all(item[1] for item in checks)
    mode = result.get("mode", "unknown")
    model = result.get("model", "unknown")
    prompt = request.get("prompt", "")
    effective_prompt = request_details.get("effective_prompt", "")
    prompt_strategy = request_details.get("prompt_strategy", "unknown")
    video_path = video.get("path", "none")

    lines = [
        "# Evaluation",
        "",
        f"- Evaluated at: {datetime.now(timezone.utc).isoformat()}",
        f"- Request ID: `{request.get('request_id', request_dir.name)}`",
        f"- Mode: {mode}",
        f"- Model: `{model}`",
        f"- Prompt strategy: {prompt_strategy}",
        f"- Prompt: {prompt}",
        f"- Effective prompt: {effective_prompt}",
        f"- Local image count: {len(pngs)}",
        f"- Local MP4 count: {len(mp4s)}",
        f"- Video path: {video_path}",
        f"- Overall status: {'passed' if passed else 'needs_review'}",
        "",
        "## Checks",
        "",
    ]

    for name, ok, description in checks:
        lines.append(f"- {'PASS' if ok else 'FAIL'}: {name} - {description}")

    lines.extend(
        [
            "",
            "## Human Review Notes",
            "",
            "- Verify prompt match visually.",
            "- Verify generated or edited images and the silent video contain no disallowed nudity.",
            "- Verify text overlays are readable and do not hide the product or main subject.",
            "- Verify motion, pacing, and CTA are acceptable before publishing outside the debug workflow.",
            "",
        ]
    )

    output_path = request_dir / EVALUATION_PATH
    output_path.write_text("\n".join(lines), encoding="utf-8")
    output_path.chmod(0o600)
    print(output_path)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
