#!/usr/bin/env python3
"""Generate Process B voiceover audio with ElevenLabs text-to-speech."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


API_BASE_URL = "https://api.elevenlabs.io/v1"
DEFAULT_CHARACTER_DIR = Path(__file__).resolve().parents[1] / "characters" / "astrid"
DEFAULT_SCRIPT_PATHS = ("script.md", "script.txt")
DEFAULT_OUTPUT_PATH = Path("output_audio") / "voiceover.mp3"
RESULT_PATH = Path("elevenlabs_tts_result.json")
STATUS_PATH = Path("status.json")
LEARNING_PATH = Path("learning.md")

VOICE_NAME = "Riley"
VOICE_ID = "hA4zGnmTwX2NQiTRMt7o"
MODEL_ID = "eleven_multilingual_v2"
OUTPUT_FORMAT = "mp3_44100_128"
VOICE_SETTINGS = {
    "speed": 0.92,
    "stability": 0.78,
    "similarity_boost": 0.85,
    "style": 0.23,
    "use_speaker_boost": True,
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
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


def find_default_script() -> Path:
    for candidate in DEFAULT_SCRIPT_PATHS:
        path = Path(candidate)
        if path.is_file():
            return path
    raise FileNotFoundError("No script file found. Expected script.md or script.txt, or pass --script.")


def read_script(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"script is empty: {path}")
    return text


def load_character(character_dir: Path) -> dict[str, Any]:
    metadata_path = character_dir / "character.json"
    reference_path = character_dir / "reference.png"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"missing character metadata: {metadata_path}")
    if not reference_path.is_file():
        raise FileNotFoundError(f"missing character reference image: {reference_path}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["character_dir"] = str(character_dir)
    metadata["reference_image_path"] = str(reference_path)
    return metadata


def elevenlabs_payload(text: str) -> dict[str, Any]:
    return {
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": VOICE_SETTINGS,
    }


def synthesize_speech(api_key: str, text: str, output_path: Path) -> dict[str, Any]:
    query = urllib.parse.urlencode({"output_format": OUTPUT_FORMAT})
    url = f"{API_BASE_URL}/text-to-speech/{VOICE_ID}?{query}"
    body = json.dumps(elevenlabs_payload(text)).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
            "xi-api-key": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            audio = response.read()
            status_code = response.status
            content_type = response.headers.get("Content-Type", "")
            request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ElevenLabs API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ElevenLabs API request failed: {exc.reason}") from exc

    if not audio:
        raise RuntimeError("ElevenLabs API returned an empty audio response")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(audio)
    output_path.chmod(0o600)
    return {
        "http_status": status_code,
        "content_type": content_type,
        "request_id": request_id,
        "bytes_written": len(audio),
    }


def append_learning(character: dict[str, Any], script_path: Path, output_path: Path) -> None:
    lines = [
        "",
        "## ElevenLabs TTS",
        "",
        f"- Process B mode: scripted_tts",
        f"- Character: {character.get('character_id', 'unknown')}",
        f"- Character reference: {character.get('reference_image_path')}",
        f"- Script: {script_path}",
        f"- Voice: {VOICE_NAME} ({VOICE_ID})",
        f"- Model: {MODEL_ID}",
        f"- Output: {output_path}",
        f"- Output format: {OUTPUT_FORMAT}",
    ]
    with LEARNING_PATH.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    LEARNING_PATH.chmod(0o600)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ElevenLabs TTS for scripted Process B voiceover.")
    parser.add_argument("--script", type=Path, help="Script text file. Defaults to script.md, then script.txt.")
    parser.add_argument("--character-dir", type=Path, default=DEFAULT_CHARACTER_DIR, help="Character folder containing character.json and reference.png.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output MP3 path.")
    parser.add_argument("--dry-run", action="store_true", help="Write metadata without calling the ElevenLabs API.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        script_path = args.script or find_default_script()
        script_text = read_script(script_path)
        character = load_character(args.character_dir)
        write_status("running", "elevenlabs_tts", character=character.get("character_id"), script=str(script_path))

        api_result: dict[str, Any] | None = None
        if not args.dry_run:
            api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("ELEVENLABS_API_KEY is not set. Put it in /etc/ugc-pipeline/fal.env for supervisor injection.")
            api_result = synthesize_speech(api_key, script_text, args.output)

        result = {
            "status": "dry_run" if args.dry_run else "succeeded",
            "created_at": now_utc(),
            "goal": "text_to_speech",
            "character": character,
            "script_path": str(script_path),
            "script_characters": len(script_text),
            "voice_name": VOICE_NAME,
            "voice_id": VOICE_ID,
            "model_id": MODEL_ID,
            "voice_settings": VOICE_SETTINGS,
            "output_format": OUTPUT_FORMAT,
            "output_path": str(args.output),
            "api_result": api_result,
        }
        write_json(RESULT_PATH, result)
        append_learning(character, script_path, args.output)
        write_status(result["status"], "elevenlabs_tts", result_path=str(RESULT_PATH), output_path=str(args.output))
        print(args.output)
        return 0
    except Exception as exc:
        write_status("failed", "elevenlabs_tts", error=str(exc))
        print(f"elevenlabs_tts failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
