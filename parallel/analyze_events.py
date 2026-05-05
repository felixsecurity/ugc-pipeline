#!/usr/bin/env python3
"""Summarize batch execution events into reflection.md."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENTS_PATH = Path("events.log")
REFLECTION_PATH = Path("reflection.md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", required=True, type=Path)
    parser.add_argument("--events-path", type=Path, default=None)
    parser.add_argument("--output-path", type=Path, default=None)
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_ts(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def fmt_seconds(value: float) -> str:
    return f"{value:.1f}s"


@dataclass
class EventStats:
    active_seconds: float = 0.0
    wait_seconds: float = 0.0


STAGE_LABELS = {
    "ready_for_prompting": "prompting preflight",
    "prompting": "prompting",
    "nano_submit": "Nano Banana submit",
    "nano_wait": "Nano Banana wait",
    "kling_ready": "Kling submit",
    "kling_wait": "Kling wait",
    "confirm_ready": "final confirmation",
    "confirm": "final confirmation",
    "done": "done",
    "failed": "failed",
}


def stage_label(phase: str) -> str:
    return STAGE_LABELS.get(phase, phase)


def summarize_failure_reason(error_text: str | None) -> str:
    if not error_text:
        return "no provider error payload was recorded"

    normalized = " ".join(error_text.split())
    lower = normalized.lower()

    if "input video duration must be between" in lower:
        return "source video violated the provider duration limits"
    if "video size is too large" in lower:
        return "the source video exceeded the provider file size limit"
    if "content_policy_violation" in lower or "content policy violation" in lower:
        return "the provider rejected the request under content policy"
    if "no_media_generated" in lower or "did not generate the expected output" in lower:
        return "the image model produced no usable output; likely a prompt, attachment, or safety incompatibility"
    if "something went wrong when we tried to get the contents of the file" in lower:
        return "the provider could not read one of the attached files"
    if "unsafe" in lower or "safety" in lower or "policy" in lower:
        return "the provider likely applied a safety or policy filter"

    if len(normalized) > 180:
        normalized = normalized[:177].rstrip() + "..."
    return normalized


def format_video_outcome(video_id: str, phase: str, error_text: str | None = None) -> str:
    if phase == "done":
        return f"- `{video_id}`: succeeded in `{stage_label('confirm')}`"
    reason = summarize_failure_reason(error_text)
    return f"- `{video_id}`: failed in `{stage_label(phase)}`; likely reason: {reason}"


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"events log does not exist: {path}")
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        events.append(json.loads(stripped))
    return events


def main() -> int:
    args = parse_args()
    batch_dir = args.batch_dir.resolve()
    events_path = (args.events_path or (batch_dir / EVENTS_PATH)).resolve()
    output_path = (args.output_path or (batch_dir / REFLECTION_PATH)).resolve()
    events = load_events(events_path)

    if not events:
        raise RuntimeError("events log is empty")

    sorted_events = sorted(events, key=lambda item: item["ts"])
    first_ts = parse_ts(sorted_events[0]["ts"])
    last_ts = parse_ts(sorted_events[-1]["ts"])

    by_phase: defaultdict[str, EventStats] = defaultdict(EventStats)
    by_video: defaultdict[str, EventStats] = defaultdict(EventStats)
    event_counts: Counter[str] = Counter()
    phase_counts: Counter[str] = Counter()
    recommendations: list[str] = []
    terminal_events: dict[str, dict[str, Any]] = {}

    for event in sorted_events:
        phase = str(event.get("phase") or "unknown")
        video_id = str(event.get("video_id") or "batch")
        duration_ms = float(event.get("duration_ms") or 0.0)
        duration_s = duration_ms / 1000.0
        event_name = str(event.get("event") or "unknown")
        event_counts[event_name] += 1
        phase_counts[phase] += 1
        if duration_s > 0:
            by_phase[phase].active_seconds += duration_s
            by_video[video_id].active_seconds += duration_s

        if event_name == "result_confirmed":
            terminal_events[video_id] = {
                "phase": "done",
                "error": None,
            }
        elif event_name == "failed":
            terminal_events[video_id] = {
                "phase": phase,
                "error": str((event.get("details") or {}).get("error") or ""),
            }

        wait_until = event.get("wait_until")
        if isinstance(wait_until, str) and wait_until:
            wait_s = max(0.0, (parse_ts(wait_until) - parse_ts(event["ts"])).total_seconds())
            by_phase[phase].wait_seconds += wait_s
            by_video[video_id].wait_seconds += wait_s

    total_elapsed_s = max(0.0, (last_ts - first_ts).total_seconds())
    total_active_s = sum(item.active_seconds for item in by_phase.values())
    total_wait_s = sum(item.wait_seconds for item in by_phase.values())
    longest_wait_phase = max(by_phase.items(), key=lambda item: item[1].wait_seconds, default=("unknown", EventStats()))
    busiest_video = max(by_video.items(), key=lambda item: item[1].active_seconds + item[1].wait_seconds, default=("n/a", EventStats()))

    if longest_wait_phase[1].wait_seconds > total_active_s and longest_wait_phase[0] == "kling_wait":
        recommendations.append("Kling wait dominated the batch. Consider larger local prompting batches before the first Kling poll and tune the 150s poll interval with observed completion times.")
    if event_counts.get("poll_rescheduled", 0) > event_counts.get("remote_completed", 0):
        recommendations.append("Polling retried more often than completions were observed. Revisit default poll intervals to reduce low-value status checks.")
    if total_wait_s < 1.0:
        recommendations.append("Very little remote wait was recorded. Confirm the scheduler is writing wait_until on every remote submission and reschedule.")
    if not recommendations:
        recommendations.append("The event stream shows balanced overlap. Keep prompt construction ahead of remote completions and refine poll intervals with more real batches.")

    successful_videos = sorted(video_id for video_id, event in terminal_events.items() if event["phase"] == "done")
    failed_videos = sorted(
        (
            video_id,
            event["phase"],
            event["error"],
        )
        for video_id, event in terminal_events.items()
        if event["phase"] != "done"
    )

    lines = [
        "# Reflection",
        "",
        f"- Batch directory: `{batch_dir}`",
        f"- Generated at: {now_utc()}",
        f"- Event source: `{events_path}`",
        f"- Total elapsed wall time: {fmt_seconds(total_elapsed_s)}",
        f"- Total active tracked time: {fmt_seconds(total_active_s)}",
        f"- Total scheduled wait time: {fmt_seconds(total_wait_s)}",
        f"- Longest waiting phase: `{longest_wait_phase[0]}` at {fmt_seconds(longest_wait_phase[1].wait_seconds)}",
        f"- Highest combined load video: `{busiest_video[0]}`",
        "",
        "## Video Outcomes",
        "",
    ]

    if successful_videos:
        lines.append("### Succeeded")
        for video_id in successful_videos:
            lines.append(f"- `{video_id}`: succeeded in `{stage_label('confirm')}`")
        lines.append("")
    if failed_videos:
        lines.append("### Failed")
        for video_id, phase, error_text in failed_videos:
            lines.append(format_video_outcome(video_id, phase, error_text))
        lines.append("")

    lines.extend([
        "## Phase Breakdown",
        "",
    ])

    for phase, stats in sorted(by_phase.items()):
        lines.append(
            f"- `{phase}`: active {fmt_seconds(stats.active_seconds)}, scheduled wait {fmt_seconds(stats.wait_seconds)}, events {phase_counts[phase]}"
        )

    lines.extend(["", "## Per-Video Summary", ""])
    for video_id, stats in sorted(by_video.items()):
        lines.append(f"- `{video_id}`: active {fmt_seconds(stats.active_seconds)}, scheduled wait {fmt_seconds(stats.wait_seconds)}")

    lines.extend(["", "## Recommendations", ""])
    for item in recommendations:
        lines.append(f"- {item}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
