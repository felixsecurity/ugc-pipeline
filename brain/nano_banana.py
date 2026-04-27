#!/usr/bin/env python3
"""Run Nano Banana image generation or editing from a request directory."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
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
VIDEO_DIR = "output_videos"
VIDEO_PLAN_PATH = "video_plan.json"
MAX_NANO_BANANA_IMAGES = 4
DEFAULT_VIDEO_IMAGE_COUNT = 4
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
VIDEO_FPS = 30
VIDEO_SECONDS = 14.0

PRODUCT_ONLY_TERMS = (
    "no human",
    "no humans",
    "no person",
    "no people",
    "without human",
    "without humans",
    "without person",
    "without people",
    "product only",
    "product-only",
    "packshot",
    "still life",
    "flat lay",
    "flatlay",
)

HUMAN_USAGE_TERMS = (
    "person",
    "people",
    "model",
    "holding",
    "hold",
    "using",
    "use",
    "wearing",
    "wear",
    "lifestyle",
    "ugc",
    "creator",
    "hand",
    "hands",
)

COMMERCIAL_TERMS = (
    "ad",
    "advert",
    "advertising",
    "campaign",
    "product",
    "brand",
    "ecommerce",
    "e-commerce",
    "hero shot",
    "presentation",
    "promo",
    "marketing",
)


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
    image_inputs = request.get("image_inputs", [])
    if not isinstance(image_inputs, list):
        raise ValueError("request.json image_inputs must be a list")
    if not prompt and not image_inputs:
        raise ValueError("request.json must include a prompt or at least one image input")

    return request


def contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def choose_prompt_strategy(prompt: str, image_inputs: list[str]) -> str:
    normalized = prompt.lower()
    if contains_any(normalized, PRODUCT_ONLY_TERMS):
        return "product_only"
    if contains_any(normalized, HUMAN_USAGE_TERMS):
        return "human_usage"
    if image_inputs and len(normalized.split()) <= 18:
        return "human_usage"
    if image_inputs and contains_any(normalized, COMMERCIAL_TERMS):
        return "human_usage"
    return "general_advertising"


def build_effective_prompt(prompt: str, image_inputs: list[str]) -> tuple[str, str]:
    """Turn weak client input into an advertising-grade generation brief."""
    original_prompt = prompt.strip()
    strategy = choose_prompt_strategy(original_prompt, image_inputs)
    client_intent = original_prompt or "Create a polished commercial presentation image from the supplied product photo."

    base_lines = [
        "Act as a world-class advertising art director and commercial photographer.",
        f"Client request: {client_intent}",
        "Create a polished, photorealistic advertising image suitable for ecommerce, paid social, and brand presentation.",
        "Preserve the product's core identity, proportions, materials, label text, logo placement, color, and recognizable design from the reference image when provided.",
        "Use premium lighting, natural shadows, sharp product detail, realistic depth of field, clean composition, and credible scale.",
        "Do not add captions, slogans, hook text, CTA text, floating words, title cards, banners, labels, UI, stickers, or any generated marketing text into the image itself. Only preserve real text already printed on the physical product or packaging.",
        "Avoid distorted packaging, misspelled visible text, extra logos, watermarking, clutter, gimmicky effects, uncanny anatomy, and unrealistic product interaction.",
    ]

    if strategy == "product_only":
        base_lines.extend(
            [
                "Make this a product-only scene with no human, body parts, face, or mannequin.",
                "Place the product in aesthetic surroundings that make semantic sense for what it is: choose props, surface, environment, color palette, and lighting that imply the product category, use case, and target buyer.",
                "Compose it like a premium still-life campaign image, with the product as the unmistakable hero and enough negative space for future ad copy.",
            ]
        )
    elif strategy == "human_usage":
        base_lines.extend(
            [
                "Show a photorealistic adult person naturally using, holding, wearing, or presenting the product in a believable lifestyle advertising scene.",
                "If the client did not specify demographics, choose an aspirational but broadly relatable adult model and setting that fit the product category.",
                "Make the product clearly visible, correctly oriented, and heroed in the frame; the person supports the product story without distracting from it.",
                "Keep pose, grip, gaze, skin texture, clothing, and environment natural, editorial, and brand-safe.",
            ]
        )
    else:
        base_lines.extend(
            [
                "If the request is vague, infer the strongest commercial concept from the product or described subject.",
                "Prefer a clear hero composition that communicates what the product is, why it is desirable, and where it belongs in a buyer's life.",
                "Include either a suitable adult lifestyle usage scene or an elevated product still life, whichever best fits the client request.",
            ]
        )

    return "\n".join(base_lines), strategy


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


def clamp_image_count(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = DEFAULT_VIDEO_IMAGE_COUNT
    return max(1, min(MAX_NANO_BANANA_IMAGES, count))


def compact_text(value: str, max_chars: int) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&%+-]*", value)
    if not words:
        return ""
    text = " ".join(words)
    if len(text) <= max_chars:
        return text
    trimmed = text[:max_chars].rsplit(" ", 1)[0]
    return trimmed or text[:max_chars]


def build_video_text(prompt: str, strategy: str) -> dict[str, str]:
    normalized = prompt.lower()
    if "serum" in normalized or "skincare" in normalized or "skin care" in normalized:
        return {
            "hook": "SERUM GLOW CHECK",
            "beat": "DROP BY DROP",
            "payoff": "SOFT SKIN ENERGY",
            "cta": "WOULD YOU TRY IT?",
        }

    hook = compact_text(prompt, 28)
    if not hook or any(term in normalized for term in ("create ", "make ", "generate ", "ugc-style", "short ad")):
        hook = "WAIT FOR THE REVEAL"

    if strategy == "product_only":
        beat = "Premium detail"
        payoff = "Upgrade the everyday"
    elif strategy == "human_usage":
        beat = "See it in real life"
        payoff = "Made for real use"
    else:
        beat = "More than a still image"
        payoff = "Built to grab attention fast"

    return {
        "hook": hook.upper(),
        "beat": beat.upper(),
        "payoff": payoff.upper(),
        "cta": "COMMENT IF YOU WANT THIS",
    }


def ffmpeg_escape_text(value: str) -> str:
    value = value.replace("\\", "\\\\")
    value = value.replace(":", "\\:")
    value = value.replace("'", "\\'")
    value = value.replace("%", "\\%")
    return value


def text_filter(text: str, start: float, end: float, y_expr: str, font_size: int = 64) -> str:
    escaped = ffmpeg_escape_text(text)
    y_pop = (
        f"if(lt(t\\,{start + 0.16:.2f})\\,"
        f"({y_expr})+70*(1-(t-{start:.2f})/0.16)\\,"
        f"if(lt(t\\,{start + 0.28:.2f})\\,({y_expr})-12*(1-(t-{start + 0.16:.2f})/0.12)\\,({y_expr})))"
    )
    alpha = (
        f"if(lt(t\\,{start + 0.10:.2f})\\,(t-{start:.2f})/0.10\\,"
        f"if(gt(t\\,{end - 0.16:.2f})\\,({end:.2f}-t)/0.16\\,1))"
    )
    return (
        "drawtext="
        f"text='{escaped}':"
        "fontcolor=white:"
        f"fontsize={font_size}:"
        "font='DejaVu Sans Condensed Bold':"
        "borderw=5:bordercolor=black:"
        "shadowcolor=black@0.85:shadowx=3:shadowy=4:"
        f"alpha='{alpha}':"
        "x=(w-text_w)/2:"
        f"y='{y_pop}':"
        f"enable='between(t,{start:.2f},{end:.2f})'"
    )


def run_ffmpeg(command: list[str]) -> None:
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with exit {process.returncode}: {process.stderr[-2400:]}")


def create_segment(image_path: Path, output_path: Path, duration: float, index: int) -> dict[str, Any]:
    progress = f"(t/{duration:.3f})"
    modes = [
        (
            f"1.02+0.10*(1-cos(PI*{progress}))/2",
            "max(0,min(iw-ow,(iw-ow)/2))",
            "max(0,min(ih-oh,(ih-oh)/2-18*t))",
            "hook: controlled push toward the hero subject",
        ),
        (
            f"1.12+0.05*(1-cos(PI*{progress}))/2",
            "max(0,min(iw-ow,(iw-ow)/2+22*t))",
            "max(0,min(ih-oh,(ih-oh)/2))",
            "product focus: slow lateral slide across the bottle",
        ),
        (
            f"1.10+0.07*(1-cos(PI*{progress}))/2",
            "max(0,min(iw-ow,(iw-ow)/2-18*t))",
            "max(0,min(ih-oh,(ih-oh)/2+16*t))",
            "usage moment: gentle diagonal move following the applicator",
        ),
        (
            f"1.17-0.05*(1-cos(PI*{progress}))/2",
            "max(0,min(iw-ow,(iw-ow)/2))",
            "max(0,min(ih-oh,(ih-oh)/2-10*t))",
            "context reset: slight pullback for breathing room",
        ),
        (
            f"1.08+0.09*(1-cos(PI*{progress}))/2",
            "max(0,min(iw-ow,(iw-ow)/2-20*t))",
            "max(0,min(ih-oh,(ih-oh)/2-14*t))",
            "benefit beat: steady push into face and glow",
        ),
        (
            f"1.16+0.04*sin(PI*{progress})",
            f"max(0,min(iw-ow,(iw-ow)/2+12*sin(PI*{progress})))",
            "max(0,min(ih-oh,(ih-oh)/2))",
            "detail hold: subtle pulse on product label",
        ),
        (
            f"1.09+0.06*(1-cos(PI*{progress}))/2",
            "max(0,min(iw-ow,(iw-ow)/2+18*t))",
            "max(0,min(ih-oh,(ih-oh)/2+8*t))",
            "social proof: composed slide into smiling creator",
        ),
        (
            f"1.10+0.08*(1-cos(PI*{progress}))/2",
            "max(0,min(iw-ow,(iw-ow)/2))",
            "max(0,min(ih-oh,(ih-oh)/2-16*t))",
            "CTA: clean final push with stable framing",
        ),
    ]
    zoom_expr, x_expr, y_expr, motion = modes[index % len(modes)]

    vf = ",".join(
        [
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase",
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
            "setsar=1",
            f"fps={VIDEO_FPS}",
            f"scale=w='{VIDEO_WIDTH}*({zoom_expr})':h='{VIDEO_HEIGHT}*({zoom_expr})':eval=frame",
            f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}:x='{x_expr}':y='{y_expr}'",
            "eq=contrast=1.11:saturation=1.18:brightness=0.012",
            "vignette=PI/5",
            "fade=t=in:st=0:d=0.12",
            f"fade=t=out:st={max(duration - 0.12, 0):.2f}:d=0.12",
            "format=yuv420p",
        ]
    )
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(VIDEO_FPS),
            "-loop",
            "1",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(image_path),
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "22",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    output_path.chmod(0o600)
    return {"path": str(output_path.relative_to(Path.cwd())), "source": str(image_path.relative_to(Path.cwd())), "motion": motion}


def create_video(downloads: list[dict[str, str]], prompt: str, prompt_strategy: str) -> dict[str, Any] | None:
    if not downloads:
        return None
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to render silent video output")

    video_dir = Path.cwd() / VIDEO_DIR
    work_dir = video_dir / "work"
    video_dir.mkdir(mode=0o700, exist_ok=True)
    work_dir.mkdir(mode=0o700, exist_ok=True)

    images = [Path.cwd() / item["path"] for item in downloads[:MAX_NANO_BANANA_IMAGES]]
    segment_count = max(8, len(images) * 2)
    segment_duration = VIDEO_SECONDS / segment_count
    segments: list[dict[str, Any]] = []
    segment_paths: list[Path] = []

    for index in range(segment_count):
        image_path = images[index % len(images)]
        segment_path = work_dir / f"segment_{index + 1:02d}.mp4"
        segments.append(create_segment(image_path, segment_path, segment_duration, index))
        segment_paths.append(segment_path)

    concat_path = work_dir / "segments.txt"
    concat_path.write_text("".join(f"file '{path.name}'\n" for path in segment_paths), encoding="utf-8")
    concat_path.chmod(0o600)

    joined_path = work_dir / "joined.mp4"
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            str(joined_path),
        ]
    )
    joined_path.chmod(0o600)

    text = build_video_text(prompt, prompt_strategy)
    text_filters = [
        text_filter(text["hook"], 0.05, 2.25, "h*0.11", 62),
        text_filter(text["beat"], 2.85, 5.15, "h*0.72", 58),
        text_filter(text["payoff"], 6.10, 9.15, "h*0.69", 56),
        text_filter(text["cta"], 10.35, VIDEO_SECONDS - 0.18, "h*0.76", 58),
    ]
    # The geometric overlays create silent-first micro-stimulus without hiding the product.
    polish_filters = [
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}",
        *text_filters,
        "format=yuv420p",
    ]
    output_path = video_dir / "final.mp4"
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(joined_path),
            "-vf",
            ",".join(polish_filters),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "22",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    output_path.chmod(0o600)

    plan = {
        "path": str(output_path.relative_to(Path.cwd())),
        "duration_seconds": VIDEO_SECONDS,
        "fps": VIDEO_FPS,
        "size": f"{VIDEO_WIDTH}x{VIDEO_HEIGHT}",
        "audio": "none",
        "source_image_count": len(images),
        "segment_count": segment_count,
        "effects": [
            "short-form 9:16 crop",
            "structured eight-beat motion arc: hook, product focus, usage, context reset, benefit, detail hold, proof, CTA",
            "controlled eased zooms and pans with stable subject framing",
            "TikTok-style bold white text overlays with black outline, shadow, fade, and pop-in motion",
            "contrast/saturation polish",
            "subtle vignette only; no horizontal impact bars",
            "silent MP4 output",
        ],
        "text": text,
        "segments": segments,
    }
    plan_path = Path.cwd() / VIDEO_PLAN_PATH
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    plan_path.chmod(0o600)
    return plan


def write_learning(
    request: dict[str, Any],
    mode: str,
    model: str,
    prompt_strategy: str,
    effective_prompt: str,
    result: dict[str, Any],
    downloads: list[dict[str, str]],
    video: dict[str, Any] | None,
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
                f"- Prompt strategy: {prompt_strategy}",
                f"- Input images: {len(request.get('image_inputs', []))}",
                f"- Images returned by fal: {image_count}",
                f"- Images downloaded locally: {len(downloads)}",
                f"- Output paths: {', '.join(item['path'] for item in downloads) if downloads else 'none'}",
                f"- Silent video output: {video['path'] if video else 'none'}",
                f"- Video effects: {', '.join(video.get('effects', [])) if video else 'none'}",
                f"- Elapsed seconds: {elapsed_seconds:.3f}",
                "- Process B should expand weak client briefs into a concrete advertising concept while preserving explicit constraints.",
                "- Process B must use Nano Banana only for static images, capped at four image generations per video clip; video motion is local ffmpeg editing.",
                "- Process C should verify the generated image and silent video match the prompt and contain no disallowed nudity.",
                "",
                "## Effective Prompt",
                "",
                effective_prompt,
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
    original_prompt = str(request.get("prompt", "")).strip()
    image_inputs = [str(item) for item in request.get("image_inputs", []) if str(item).strip()]
    prompt, prompt_strategy = build_effective_prompt(original_prompt, image_inputs)
    image_count = clamp_image_count(request.get("num_images", DEFAULT_VIDEO_IMAGE_COUNT))

    arguments: dict[str, Any] = {
        "prompt": prompt,
        "num_images": image_count,
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
    video = create_video(downloads, original_prompt, prompt_strategy)

    output = {
        "mode": mode,
        "model": model,
        "request_id": request_id,
        "ran_at": now_utc(),
        "request": {
            "prompt": original_prompt,
            "effective_prompt": prompt,
            "prompt_strategy": prompt_strategy,
            "image_input_count": len(image_inputs),
            "num_images": arguments["num_images"],
            "max_nano_banana_images": MAX_NANO_BANANA_IMAGES,
            "aspect_ratio": arguments["aspect_ratio"],
            "resolution": arguments["resolution"],
            "output_format": arguments["output_format"],
            "safety_tolerance": arguments["safety_tolerance"],
        },
        "result": result,
        "downloaded_images": downloads,
        "video": video,
    }

    Path(RESULT_PATH).write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(RESULT_PATH).chmod(0o600)
    elapsed_seconds = monotonic() - started
    write_learning(request, mode, model, prompt_strategy, prompt, result, downloads, video, elapsed_seconds)
    write_status(
        "succeeded",
        "process_b",
        started_at=started_at,
        completed_at=now_utc(),
        elapsed_seconds=round(elapsed_seconds, 3),
        request_id=request_id,
        mode=mode,
        model=model,
        prompt_strategy=prompt_strategy,
        output_paths=[item["path"] for item in downloads],
        video_path=video["path"] if video else None,
        fal_image_count=len(result.get("images", [])),
        nano_banana_image_count=arguments["num_images"],
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
