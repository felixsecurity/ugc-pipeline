#!/usr/bin/env python3
"""Run Nano Banana image generation or editing from a request directory."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any


TEXT_TO_IMAGE_MODEL = "fal-ai/nano-banana-2"
EDIT_MODEL = "fal-ai/nano-banana-2/edit"
DEFAULT_REQUEST_PATH = "request.json"
RESULT_PATH = "fal_result.json"
STATUS_PATH = "status.json"
DOWNLOAD_DIR = "output_images"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(status: str, stage: str, **extra: Any) -> None:
    payload = {
        "status": status,
        "stage": stage,
        "updated_at": now_utc(),
        **extra,
    }
    Path(STATUS_PATH).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(STATUS_PATH).chmod(0o600)


def is_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def load_request(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        request = json.load(handle)

    prompt = str(request.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("request.json must include a non-empty prompt")

    image_inputs = request.get("image_inputs", [])
    if not isinstance(image_inputs, list):
        raise ValueError("request.json image_inputs must be a list")

    return request


def upload_or_pass_image(fal_client: Any, image_ref: str) -> str:
    if is_url(image_ref):
        return image_ref

    image_path = Path(image_ref)
    if not image_path.is_absolute():
        image_path = Path.cwd() / image_path
    if not image_path.is_file():
        raise FileNotFoundError(f"image input does not exist: {image_ref}")

    return fal_client.upload_file(str(image_path))


def download_outputs(result: dict[str, Any]) -> list[dict[str, str]]:
    output_dir = Path.cwd() / DOWNLOAD_DIR
    output_dir.mkdir(mode=0o700, exist_ok=True)
    downloaded: list[dict[str, str]] = []

    for index, image in enumerate(result.get("images", []), start=1):
        url = image.get("url")
        if not url:
            continue

        file_name = image.get("file_name") or f"image_{index}.png"
        suffix = Path(file_name).suffix
        if not suffix:
            content_type = image.get("content_type") or "image/png"
            suffix = mimetypes.guess_extension(content_type) or ".png"
        local_path = output_dir / f"{index:02d}{suffix}"

        with urllib.request.urlopen(url, timeout=60) as response:
            local_path.write_bytes(response.read())
        local_path.chmod(0o600)
        downloaded.append({"url": url, "path": str(local_path.relative_to(Path.cwd()))})

    return downloaded


def write_learning(
    request: dict[str, Any],
    mode: str,
    model: str,
    result: dict[str, Any],
    downloads: list[dict[str, str]],
    elapsed_seconds: float,
) -> None:
    learning = Path.cwd() / "learning.md"
    image_count = len(result.get("images", []))
    request_id = request.get("request_id", Path.cwd().name)
    learning.write_text(
        "\n".join(
            [
                "# Learning",
                "",
                f"- Request ID: `{request_id}`",
                f"- Process B mode: {mode}",
                f"- Model: `{model}`",
                f"- Input images: {len(request.get('image_inputs', []))}",
                f"- Images returned by fal: {image_count}",
                f"- Images downloaded locally: {len(downloads)}",
                f"- Output paths: {', '.join(item['path'] for item in downloads) if downloads else 'none'}",
                f"- Elapsed seconds: {elapsed_seconds:.3f}",
                "- Process C should verify the generated image matches the prompt and contains no disallowed nudity.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    learning.chmod(0o600)


def run(request_path: Path) -> int:
    started_at = now_utc()
    started = monotonic()
    write_status("running", "process_b", started_at=started_at)

    if "FAL_KEY" not in os.environ or not os.environ["FAL_KEY"].strip():
        message = "FAL_KEY is not set. Put it in /etc/ugc-pipeline/fal.env for supervisor injection."
        write_status("failed", "process_b", started_at=started_at, error=message)
        print(message, file=sys.stderr)
        return 1

    try:
        import fal_client
    except ImportError:
        message = "fal-client is not installed. Install it in /opt/ugc-pipeline-venv."
        write_status("failed", "process_b", started_at=started_at, error=message)
        print(message, file=sys.stderr)
        return 1

    request = load_request(request_path)
    request_id = request.get("request_id", Path.cwd().name)
    prompt = request["prompt"].strip()
    image_inputs = [str(item) for item in request.get("image_inputs", []) if str(item).strip()]

    arguments: dict[str, Any] = {
        "prompt": prompt,
        "num_images": int(request.get("num_images", 1)),
        "aspect_ratio": request.get("aspect_ratio", "auto"),
        "output_format": request.get("output_format", "png"),
        "safety_tolerance": str(request.get("safety_tolerance", "1")),
        "resolution": request.get("resolution", "1K"),
        "limit_generations": bool(request.get("limit_generations", True)),
    }

    if image_inputs:
        model = EDIT_MODEL
        mode = "image_edit"
        arguments["image_urls"] = [upload_or_pass_image(fal_client, item) for item in image_inputs]
    else:
        model = TEXT_TO_IMAGE_MODEL
        mode = "text_to_image"

    result = fal_client.subscribe(model, arguments=arguments, with_logs=True, client_timeout=300)
    downloads = download_outputs(result)

    output = {
        "mode": mode,
        "model": model,
        "request_id": request_id,
        "ran_at": now_utc(),
        "request": {
            "prompt": prompt,
            "image_input_count": len(image_inputs),
            "num_images": arguments["num_images"],
            "aspect_ratio": arguments["aspect_ratio"],
            "resolution": arguments["resolution"],
            "output_format": arguments["output_format"],
            "safety_tolerance": arguments["safety_tolerance"],
        },
        "result": result,
        "downloaded_images": downloads,
    }

    Path(RESULT_PATH).write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(RESULT_PATH).chmod(0o600)
    elapsed_seconds = monotonic() - started
    write_learning(request, mode, model, result, downloads, elapsed_seconds)
    write_status(
        "succeeded",
        "process_b",
        started_at=started_at,
        completed_at=now_utc(),
        elapsed_seconds=round(elapsed_seconds, 3),
        request_id=request_id,
        mode=mode,
        model=model,
        output_paths=[item["path"] for item in downloads],
        fal_image_count=len(result.get("images", [])),
    )
    print(Path.cwd() / RESULT_PATH)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", default=DEFAULT_REQUEST_PATH, help="Path to request.json")
    args = parser.parse_args(argv[1:])

    try:
        return run(Path(args.request))
    except Exception as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        try:
            write_status("failed", "process_b", error=str(exc), error_type=type(exc).__name__)
        except Exception:
            pass
        print(f"nano_banana failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
