#!/usr/bin/env python3
"""Run the Process B slide_show flavor.

Input is a local folder containing script.txt and numbered images. Each
non-empty script line maps to the image with the same 1-based index. The runner
creates ElevenLabs narration, uses Whisper base for timestamps, renders a 9:16
slideshow with black top padding, image content below it, subtitles below the
image, and burns subtitles into the final MP4.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import elevenlabs_tts


DEFAULT_INPUT_DIR = Path(".")
WHISPER_MODEL_DIR = Path(os.environ.get("UGC_WHISPER_MODEL_DIR", "/opt/ugc-pipeline-whisper"))

PLAN_PATH = Path("slide_show_plan.json")
STATUS_PATH = Path("status.json")
LEARNING_PATH = Path("learning.md")
SCRIPT_COPY_PATH = Path("script.md")
AUDIO_PATH = Path("output_audio") / "voiceover.mp3"
TIMESTAMPS_PATH = Path("whisper_timestamps.json")
SUBTITLES_PATH = Path("output_videos") / "work" / "subtitles.ass"
SEGMENT_LIST_PATH = Path("output_videos") / "work" / "segments.txt"
FINAL_VIDEO_PATH = Path("output_videos") / "final_subtitled.mp4"

VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 30
TOP_PADDING_HEIGHT = int(VIDEO_HEIGHT * 0.20)
SUBTITLE_AREA_HEIGHT = int(VIDEO_HEIGHT * 0.20)
IMAGE_AREA_HEIGHT = VIDEO_HEIGHT - TOP_PADDING_HEIGHT - SUBTITLE_AREA_HEIGHT
SUBTITLE_CENTER_X = VIDEO_WIDTH // 2
SUBTITLE_CENTER_Y = 780
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


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


def find_script_path(input_dir: Path) -> Path:
    candidates = [input_dir / "script.txt", input_dir / "script.txt.txt"]
    candidates.extend(sorted(input_dir.glob("script*.txt")))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"missing script.txt in {input_dir}")


def read_script_lines(script_path: Path) -> list[str]:
    lines = [line.strip() for line in script_path.read_text(encoding="utf-8").splitlines()]
    non_empty = [line for line in lines if line]
    if not non_empty:
        raise ValueError(f"script has no non-empty lines: {script_path}")
    return non_empty


def find_numbered_images(input_dir: Path) -> list[Path]:
    numbered: dict[int, Path] = {}
    duplicates: dict[int, list[Path]] = {}
    for path in sorted(input_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        match = re.fullmatch(r"0*([1-9][0-9]*)", path.stem)
        if not match:
            continue
        index = int(match.group(1))
        if index in numbered:
            duplicates.setdefault(index, [numbered[index]]).append(path)
        else:
            numbered[index] = path
    if duplicates:
        detail = ", ".join(f"{index}: {', '.join(str(path.name) for path in paths)}" for index, paths in duplicates.items())
        raise ValueError(f"duplicate numbered images found: {detail}")
    if not numbered:
        raise FileNotFoundError(f"no numbered images found in {input_dir}")
    expected = list(range(1, max(numbered) + 1))
    missing = [index for index in expected if index not in numbered]
    if missing:
        raise ValueError(f"numbered images must be contiguous from 1; missing: {missing}")
    return [numbered[index] for index in expected]


def validate_inputs(input_dir: Path) -> tuple[Path, list[str], list[Path]]:
    if not input_dir.is_dir():
        raise FileNotFoundError(f"input folder does not exist: {input_dir}")
    script_path = find_script_path(input_dir)
    lines = read_script_lines(script_path)
    images = find_numbered_images(input_dir)
    if len(lines) != len(images):
        raise ValueError(f"script line count ({len(lines)}) must match numbered image count ({len(images)})")
    return script_path, lines, images


def save_script(lines: list[str]) -> Path:
    SCRIPT_COPY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    SCRIPT_COPY_PATH.chmod(0o600)
    return SCRIPT_COPY_PATH


def synthesize_audio(lines: list[str]) -> dict[str, Any]:
    if AUDIO_PATH.is_file() and AUDIO_PATH.stat().st_size > 0:
        return {
            "voice_name": elevenlabs_tts.VOICE_NAME,
            "voice_id": elevenlabs_tts.VOICE_ID,
            "model_id": elevenlabs_tts.MODEL_ID,
            "output_format": elevenlabs_tts.OUTPUT_FORMAT,
            "api_result": None,
            "reused_existing_audio": True,
        }

    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set. Put it in /etc/ugc-pipeline/fal.env for supervisor injection.")
    text = "\n".join(lines)
    result = elevenlabs_tts.synthesize_speech(api_key, text, AUDIO_PATH)
    return {
        "voice_name": elevenlabs_tts.VOICE_NAME,
        "voice_id": elevenlabs_tts.VOICE_ID,
        "model_id": elevenlabs_tts.MODEL_ID,
        "output_format": elevenlabs_tts.OUTPUT_FORMAT,
        "api_result": result,
        "reused_existing_audio": False,
    }


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


def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text)


def normalize_token(text: str) -> str:
    value = re.sub(r"[^a-z0-9]", "", text.lower())
    number_words = {
        "zero": "0",
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
        "ten": "10",
    }
    return number_words.get(value, value)


def tokens_similar(left: str, right: str) -> bool:
    left_norm = normalize_token(left)
    right_norm = normalize_token(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    if left_norm in right_norm or right_norm in left_norm:
        return min(len(left_norm), len(right_norm)) >= 4
    return SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.72


def script_word_entries(line: str) -> list[dict[str, Any]]:
    return [{"text": match.group(0), "start_char": match.start(), "end_char": match.end()} for match in re.finditer(r"[A-Za-z0-9']+", line)]


def collect_words(whisper_data: dict[str, Any]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in whisper_data.get("segments", []):
        for word in segment.get("words", []) or []:
            text = str(word.get("word", "")).strip()
            if text:
                words.append({"word": text, "norm": normalize_token(text), "start": float(word["start"]), "end": float(word["end"])})
    if words:
        return words
    for segment in whisper_data.get("segments", []):
        text = str(segment.get("text", "")).strip()
        if text:
            words.append({"word": text, "norm": normalize_token(text), "start": float(segment["start"]), "end": float(segment["end"])})
    return words


def proportional_line_timings(lines: list[str], audio_duration: float) -> list[dict[str, Any]]:
    weights = [max(1, len(tokenize(line))) for line in lines]
    total = sum(weights)
    timings: list[dict[str, Any]] = []
    cursor = 0.0
    for index, (line, weight) in enumerate(zip(lines, weights), start=1):
        end = audio_duration if index == len(lines) else cursor + (audio_duration * weight / total)
        timings.append({"index": index, "text": line, "start": cursor, "end": end, "method": "proportional"})
        cursor = end
    return timings


def interpolate_word_timings(entries: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    if not entries:
        return []
    duration = max(0.25, end - start)
    step = duration / len(entries)
    word_timings: list[dict[str, Any]] = []
    for offset, entry in enumerate(entries):
        word_start = start + (offset * step)
        word_end = start + ((offset + 1) * step)
        word_timings.append({"word": entry["text"], "start": word_start, "end": word_end, "source": "interpolated"})
    return word_timings


def match_line_words(
    entries: list[dict[str, Any]],
    whisper_words: list[dict[str, Any]],
    cursor: int,
    search_window: int = 8,
) -> tuple[list[dict[str, Any]], int, int]:
    matched: list[dict[str, Any]] = []
    search_from = cursor
    for entry in entries:
        best_index: int | None = None
        best_score = 0.0
        for index in range(search_from, min(len(whisper_words), search_from + search_window)):
            candidate = whisper_words[index]
            if tokens_similar(entry["text"], str(candidate["word"])):
                score = SequenceMatcher(None, normalize_token(entry["text"]), str(candidate.get("norm", ""))).ratio()
                if score > best_score:
                    best_index = index
                    best_score = score
        if best_index is not None:
            matched.append(
                {
                    "word": entry["text"],
                    "start": float(whisper_words[best_index]["start"]),
                    "end": float(whisper_words[best_index]["end"]),
                    "source": "whisper_fuzzy",
                    "whisper_word": whisper_words[best_index]["word"],
                    "whisper_index": best_index,
                }
            )
            search_from = best_index + 1
    if not matched:
        return [], cursor, cursor
    return matched, int(matched[0]["whisper_index"]), int(matched[-1]["whisper_index"]) + 1


def line_timings_from_whisper(lines: list[str], whisper_data: dict[str, Any], audio_duration: float) -> list[dict[str, Any]]:
    words = collect_words(whisper_data)
    line_entries = [script_word_entries(line) for line in lines]
    if not words or any(not entries for entries in line_entries):
        return proportional_line_timings(lines, audio_duration)

    timings: list[dict[str, Any]] = []
    cursor = 0
    for index, (line, entries) in enumerate(zip(lines, line_entries), start=1):
        matched_words, first_index, next_cursor = match_line_words(entries, words, cursor)
        match_ratio = len(matched_words) / len(entries)
        if match_ratio < 0.45:
            proportional = proportional_line_timings(lines, audio_duration)
            for timing in proportional:
                timing["word_timings"] = interpolate_word_timings(script_word_entries(str(timing["text"])), float(timing["start"]), float(timing["end"]))
            return proportional

        start = float(matched_words[0]["start"])
        end = float(matched_words[-1]["end"])
        if index == 1:
            start = 0.0
        if index == len(lines):
            end = audio_duration
        timings.append(
            {
                "index": index,
                "text": line,
                "start": start,
                "end": max(end, start + 0.25),
                "method": "whisper_fuzzy",
                "match_ratio": round(match_ratio, 3),
                "first_whisper_index": first_index,
                "next_whisper_index": next_cursor,
                "matched_words": matched_words,
            }
        )
        cursor = max(next_cursor, cursor + 1)

    for previous, current in zip(timings, timings[1:]):
        midpoint = (previous["end"] + current["start"]) / 2
        previous["end"] = midpoint
        current["start"] = midpoint

    for timing in timings:
        entries = script_word_entries(str(timing["text"]))
        matched = list(timing.get("matched_words", []))
        if len(matched) == len(entries):
            timing["word_timings"] = [{"word": word["word"], "start": word["start"], "end": word["end"], "source": word["source"]} for word in matched]
        else:
            timing["word_timings"] = interpolate_word_timings(entries, float(timing["start"]), float(timing["end"]))
    return timings


def ass_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def ass_escape(text: str) -> str:
    return text.replace("{", "\\{").replace("}", "\\}")


def wrap_subtitle_text(text: str) -> str:
    words = text.upper().split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and len(candidate) > 24:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\\N".join(lines[:3])


def subtitle_groups(timings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for timing in timings:
        current: list[dict[str, Any]] = []
        for word in timing.get("word_timings", []):
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


def write_ass_subtitles(timings: list[dict[str, Any]], path: Path = SUBTITLES_PATH) -> Path:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {VIDEO_WIDTH}",
        f"PlayResY: {VIDEO_HEIGHT}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Social,Arial,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,1,5,40,40,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for group in subtitle_groups(timings):
        positioned_text = f"{{\\an5\\pos({SUBTITLE_CENTER_X},{SUBTITLE_CENTER_Y})}}{ass_escape(wrap_subtitle_text(str(group['text'])))}"
        lines.append(
            f"Dialogue: 0,{ass_time(float(group['start']))},{ass_time(float(group['end']))},Social,,0,0,0,,{positioned_text}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def render_segment(image_path: Path, timing: dict[str, Any], output_path: Path) -> Path:
    duration = max(0.25, float(timing["end"]) - float(timing["start"]))
    output_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    filter_graph = (
        f"scale={VIDEO_WIDTH}:{IMAGE_AREA_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={VIDEO_WIDTH}:{IMAGE_AREA_HEIGHT}:(ow-iw)/2:0:black,"
        f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:0:{TOP_PADDING_HEIGHT}:black,"
        f"setsar=1,fps={VIDEO_FPS},format=yuv420p"
    )
    run_command(
        [
            resolve_command("ffmpeg"),
            "-y",
            "-loop",
            "1",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(image_path),
            "-vf",
            filter_graph,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            str(output_path),
        ]
    )
    output_path.chmod(0o600)
    return output_path


def render_segments(images: list[Path], timings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    work_dir = Path("output_videos") / "work"
    for image, timing in zip(images, timings):
        segment_path = work_dir / f"segment_{int(timing['index']):02d}.mp4"
        render_segment(image, timing, segment_path)
        segments.append({"index": timing["index"], "image_path": str(image), "path": str(segment_path), **timing})
    return segments


def concat_and_burn(segments: list[dict[str, Any]], audio_path: Path, subtitles_path: Path, output_path: Path = FINAL_VIDEO_PATH) -> Path:
    work_dir = Path("output_videos") / "work"
    joined_path = work_dir / "joined.mp4"
    SEGMENT_LIST_PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    SEGMENT_LIST_PATH.write_text("".join(f"file '{Path(segment['path']).name}'\n" for segment in segments), encoding="utf-8")
    SEGMENT_LIST_PATH.chmod(0o600)
    run_command(
        [
            resolve_command("ffmpeg"),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(SEGMENT_LIST_PATH),
            "-i",
            str(audio_path),
            "-vf",
            f"subtitles={subtitles_path}:force_style='Fontname=Arial,Fontsize=48,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=6,Shadow=1,Alignment=5'",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            f"{probe_audio_seconds(audio_path):.3f}",
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
    joined_path.unlink(missing_ok=True)
    return output_path


def write_learning(input_dir: Path, script_path: Path, line_count: int, final_video_path: Path, elapsed_seconds: float) -> None:
    lines = [
        "# Learning",
        "",
        "- Process B mode: slide_show",
        f"- Input folder: {input_dir}",
        f"- Script path: {script_path}",
        f"- Slide count: {line_count}",
        f"- Voice: {elevenlabs_tts.VOICE_NAME} ({elevenlabs_tts.VOICE_ID})",
        f"- TTS model: {elevenlabs_tts.MODEL_ID}",
        "- Whisper model: base",
        f"- Final subtitled video: {final_video_path}",
        f"- Elapsed seconds: {elapsed_seconds:.3f}",
        "- Layout: 720x1280, 20% black top padding, image below it, subtitles below the image.",
        "",
    ]
    LEARNING_PATH.write_text("\n".join(lines), encoding="utf-8")
    LEARNING_PATH.chmod(0o600)


def run(input_dir: Path) -> int:
    started = monotonic()
    input_dir = input_dir.resolve()
    write_status("running", "slide_show_setup", input_dir=str(input_dir))

    script_path, lines, images = validate_inputs(input_dir)
    local_script_path = save_script(lines)

    write_status("running", "elevenlabs_tts", line_count=len(lines))
    tts_result = synthesize_audio(lines)
    audio_duration = probe_audio_seconds(AUDIO_PATH)

    write_status("running", "whisper_timestamps", model="base")
    whisper_data = run_whisper(AUDIO_PATH)
    timings = line_timings_from_whisper(lines, whisper_data, audio_duration)
    subtitles_path = write_ass_subtitles(timings)

    write_status("running", "render_slides", slide_count=len(images))
    segments = render_segments(images, timings)

    write_status("running", "subtitle_burn", subtitles_path=str(subtitles_path))
    final_video_path = concat_and_burn(segments, AUDIO_PATH, subtitles_path)

    plan = {
        "mode": "slide_show",
        "input_dir": str(input_dir),
        "script_path": str(script_path),
        "local_script_path": str(local_script_path),
        "line_count": len(lines),
        "image_count": len(images),
        "images": [str(path) for path in images],
        "audio_path": str(AUDIO_PATH),
        "audio_duration_seconds": audio_duration,
        "timings": timings,
        "segments": segments,
        "subtitles_path": str(subtitles_path),
        "final_video_path": str(final_video_path),
        "tts": tts_result,
    }
    write_json(PLAN_PATH, plan)

    elapsed_seconds = monotonic() - started
    write_learning(input_dir, script_path, len(lines), final_video_path, elapsed_seconds)
    write_status(
        "succeeded",
        "process_b",
        mode="slide_show",
        elapsed_seconds=round(elapsed_seconds, 3),
        input_dir=str(input_dir),
        script_path=str(script_path),
        line_count=len(lines),
        image_count=len(images),
        audio_path=str(AUDIO_PATH),
        timestamps_path=str(TIMESTAMPS_PATH),
        subtitles_path=str(subtitles_path),
        final_video_path=str(final_video_path),
    )
    print(final_video_path)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR, help="Folder containing script.txt and numbered images.")
    args = parser.parse_args(argv[1:])
    try:
        return run(args.input_dir)
    except Exception as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        try:
            write_status("failed", "process_b", mode="slide_show", error=str(exc), error_type=type(exc).__name__)
        except Exception:
            pass
        print(f"slide_show failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
