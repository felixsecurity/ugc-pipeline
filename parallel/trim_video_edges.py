#!/usr/bin/env python3
"""Trim black lead-in and TikTok outro frames from a source video."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BLACK_LUMA_THRESHOLD = 20
WHITE_LUMA_THRESHOLD = 180
SAMPLE_FPS = 10
SAMPLE_SIZE = 32
INTRO_SCAN_SECONDS = 8.0
OUTRO_SCAN_SECONDS = 8.0
MIN_INTRO_RUN_SECONDS = 0.5
MIN_OUTRO_RUN_SECONDS = 0.5
INTRO_BLACK_RATIO = 0.85
INTRO_BORDER_BLACK_RATIO = 0.95
OUTRO_BLACK_RATIO = 0.55
OUTRO_BORDER_BLACK_RATIO = 0.90
OUTRO_CENTER_WHITE_RATIO = 0.005
OUTRO_CENTER_MEAN_MAX = 120.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args()


def resolve_command(name: str) -> str:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    raise RuntimeError(f"{name} is required")


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if process.returncode != 0:
        raise RuntimeError(f"{command[0]} failed with exit {process.returncode}: {process.stderr[-2400:]}")
    return process


def run_binary_command(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if process.returncode != 0:
        stderr = process.stderr.decode("utf-8", errors="replace") if process.stderr else ""
        raise RuntimeError(f"{command[0]} failed with exit {process.returncode}: {stderr[-2400:]}")
    return process


def probe_duration(path: Path) -> float:
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


@dataclass(frozen=True)
class FrameMetrics:
    black_ratio: float
    border_black_ratio: float
    center_mean: float
    center_white_ratio: float


def compute_frame_metrics(frame: bytes, size: int = SAMPLE_SIZE) -> FrameMetrics:
    black = 0
    border_black = 0
    border_pixels = 0
    center_luma_sum = 0
    center_pixels = 0
    center_white = 0

    for y in range(size):
        for x in range(size):
            offset = (y * size + x) * 3
            r = frame[offset]
            g = frame[offset + 1]
            b = frame[offset + 2]
            luma = (r * 299 + g * 587 + b * 114) // 1000
            is_black = luma < BLACK_LUMA_THRESHOLD
            if is_black:
                black += 1
            if x < 4 or x >= size - 4 or y < 4 or y >= size - 4:
                border_pixels += 1
                if is_black:
                    border_black += 1
            else:
                center_pixels += 1
                center_luma_sum += luma
                if luma >= WHITE_LUMA_THRESHOLD:
                    center_white += 1

    total_pixels = size * size
    return FrameMetrics(
        black_ratio=black / total_pixels,
        border_black_ratio=border_black / border_pixels if border_pixels else 0.0,
        center_mean=center_luma_sum / center_pixels if center_pixels else 0.0,
        center_white_ratio=center_white / center_pixels if center_pixels else 0.0,
    )


def sample_metrics(path: Path, start_seconds: float, duration_seconds: float, fps: int = SAMPLE_FPS) -> list[FrameMetrics]:
    if duration_seconds <= 0:
        return []
    result = run_binary_command(
        [
            resolve_command("ffmpeg"),
            "-v",
            "error",
            "-ss",
            f"{start_seconds:.6f}",
            "-t",
            f"{duration_seconds:.6f}",
            "-i",
            str(path),
            "-vf",
            f"fps={fps},scale={SAMPLE_SIZE}:{SAMPLE_SIZE}:flags=neighbor",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
    )
    frame_size = SAMPLE_SIZE * SAMPLE_SIZE * 3
    raw = result.stdout
    if not raw:
        return []
    if len(raw) % frame_size != 0:
        raise RuntimeError(f"unexpected raw frame payload size for {path}: {len(raw)}")
    return [compute_frame_metrics(raw[i : i + frame_size]) for i in range(0, len(raw), frame_size)]


def run_blackdetect(path: Path, duration_seconds: float) -> list[dict[str, float]]:
    scan_seconds = min(duration_seconds, INTRO_SCAN_SECONDS)
    if scan_seconds <= 0:
        return []
    process = subprocess.run(
        [
            resolve_command("ffmpeg"),
            "-hide_banner",
            "-v",
            "info",
            "-ss",
            "0",
            "-t",
            f"{scan_seconds:.6f}",
            "-i",
            str(path),
            "-vf",
            "blackdetect=d=0.10:pix_th=0.08",
            "-an",
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        return []
    events: list[dict[str, float]] = []
    for line in process.stderr.splitlines():
        if "black_start:" not in line:
            continue
        fields: dict[str, float] = {}
        for token in line.split():
            if ":" not in token:
                continue
            key, value = token.split(":", 1)
            if key in {"black_start", "black_end", "black_duration"}:
                try:
                    fields[key] = float(value)
                except ValueError:
                    pass
        if fields:
            events.append(fields)
    return events


def detect_intro_trim(duration_seconds: float, path: Path) -> dict[str, Any]:
    intro_limit = min(duration_seconds, INTRO_SCAN_SECONDS)
    intro_metrics = sample_metrics(path, 0.0, intro_limit)
    blackdetect_events = run_blackdetect(path, duration_seconds)
    intro_trim_end = 0.0
    intro_reason: dict[str, Any] = {"method": "none"}

    if blackdetect_events:
        first = blackdetect_events[0]
        if first.get("black_start") == 0.0 and first.get("black_duration", 0.0) >= MIN_INTRO_RUN_SECONDS:
            intro_trim_end = float(first.get("black_end", 0.0))
            intro_reason = {"method": "blackdetect", **first}

    if intro_trim_end <= 0.0 and intro_metrics:
        run_start = None
        run_length = 0
        min_run_frames = max(1, int(MIN_INTRO_RUN_SECONDS * SAMPLE_FPS))
        for index, metrics in enumerate(intro_metrics):
            if metrics.black_ratio >= INTRO_BLACK_RATIO and metrics.border_black_ratio >= INTRO_BORDER_BLACK_RATIO:
                if run_start is None:
                    run_start = index
                run_length += 1
            else:
                if run_start is not None and run_length >= min_run_frames:
                    intro_trim_end = (run_start + run_length) / SAMPLE_FPS
                    intro_reason = {
                        "method": "sampled_black_run",
                        "start_frame": run_start,
                        "end_frame": run_start + run_length,
                        "frames": run_length,
                    }
                    break
                run_start = None
                run_length = 0
        else:
            if run_start is not None and run_length >= min_run_frames:
                intro_trim_end = (run_start + run_length) / SAMPLE_FPS
                intro_reason = {
                    "method": "sampled_black_run",
                    "start_frame": run_start,
                    "end_frame": run_start + run_length,
                    "frames": run_length,
                }

    return {
        "trim_start_seconds": max(0.0, intro_trim_end),
        "detected": intro_trim_end > 0.0,
        "reason": intro_reason,
    }


def detect_outro_trim(duration_seconds: float, path: Path) -> dict[str, Any]:
    scan_seconds = min(duration_seconds, OUTRO_SCAN_SECONDS)
    start_seconds = max(0.0, duration_seconds - scan_seconds)
    outro_metrics = sample_metrics(path, start_seconds, scan_seconds)
    outro_trim_start = duration_seconds
    outro_reason: dict[str, Any] = {"method": "none"}
    min_run_frames = max(1, int(MIN_OUTRO_RUN_SECONDS * SAMPLE_FPS))
    run_start = None
    run_length = 0

    for index, metrics in enumerate(outro_metrics):
        matches = (
            metrics.border_black_ratio >= OUTRO_BORDER_BLACK_RATIO
            and metrics.black_ratio >= OUTRO_BLACK_RATIO
            and metrics.center_white_ratio >= OUTRO_CENTER_WHITE_RATIO
            and metrics.center_mean <= OUTRO_CENTER_MEAN_MAX
        )
        if matches:
            if run_start is None:
                run_start = index
            run_length += 1
        else:
            if run_start is not None and run_length >= min_run_frames:
                outro_trim_start = start_seconds + (run_start / SAMPLE_FPS)
                outro_reason = {
                    "method": "sampled_outro_run",
                    "start_frame": run_start,
                    "end_frame": run_start + run_length,
                    "frames": run_length,
                }
                break
            run_start = None
            run_length = 0
    else:
        if run_start is not None and run_length >= min_run_frames:
            outro_trim_start = start_seconds + (run_start / SAMPLE_FPS)
            outro_reason = {
                "method": "sampled_outro_run",
                "start_frame": run_start,
                "end_frame": run_start + run_length,
                "frames": run_length,
            }

    return {
        "trim_end_seconds": min(duration_seconds, outro_trim_start),
        "detected": outro_trim_start < duration_seconds,
        "reason": outro_reason,
    }


def output_duration(path: Path) -> float:
    return probe_duration(path)


def trim_video(input_path: Path, output_path: Path, start_seconds: float, end_seconds: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.0, end_seconds - start_seconds)
    has_audio = True
    try:
        probe = run_command(
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
                str(input_path),
            ]
        )
        has_audio = bool(probe.stdout.strip())
    except Exception:
        has_audio = False

    command = [
        resolve_command("ffmpeg"),
        "-y",
        "-ss",
        f"{start_seconds:.6f}",
        "-i",
        str(input_path),
        "-t",
        f"{duration:.6f}",
        "-map",
        "0:v:0",
    ]
    if has_audio:
        command.extend(["-map", "0:a?"])
    command.extend(
        [
            "-map_metadata",
            "0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if has_audio:
        command.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        command.append("-an")
    command.extend(["-movflags", "+faststart", str(output_path)])
    run_command(command)
    output_path.chmod(0o644)


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    report_path = args.report.resolve()

    if not input_path.is_file():
        raise FileNotFoundError(f"input video does not exist: {input_path}")

    duration_seconds = probe_duration(input_path)
    intro = detect_intro_trim(duration_seconds, input_path)
    outro = detect_outro_trim(duration_seconds, input_path)
    trim_start = min(max(0.0, intro["trim_start_seconds"]), duration_seconds)
    trim_end = max(trim_start, min(duration_seconds, outro["trim_end_seconds"]))

    if trim_end - trim_start <= 0.05:
        trim_start = 0.0
        trim_end = duration_seconds

    trimmed = trim_start > 0.0 or trim_end < duration_seconds - 0.01
    if trimmed:
        trim_video(input_path, output_path, trim_start, trim_end)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(input_path, output_path)
        output_path.chmod(0o644)

    output_seconds = output_duration(output_path)
    report = {
        "input_video": str(input_path),
        "output_video": str(output_path),
        "input_duration_seconds": round(duration_seconds, 6),
        "output_duration_seconds": round(output_seconds, 6),
        "trimmed": trimmed,
        "trim_start_seconds": round(trim_start, 6),
        "trim_end_seconds": round(trim_end, 6),
        "trimmed_intro": intro["detected"],
        "trimmed_outro": outro["detected"],
        "intro_detection": intro,
        "outro_detection": outro,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.chmod(0o644)
    sys.stdout.write(json.dumps(report, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
