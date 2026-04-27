#!/usr/bin/env python3
"""Validate a client image request before Process B runs."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import urllib.parse
from pathlib import Path


NUDITY_TERMS = {
    "nude",
    "nudity",
    "naked",
    "topless",
    "bottomless",
    "bare breast",
    "bare breasts",
    "exposed breast",
    "exposed breasts",
    "genitals",
    "explicit",
    "porn",
    "pornographic",
    "nsfw",
}

IMAGE_MIME_PREFIX = "image/"


def contains_nudity_text(value: str) -> str | None:
    normalized = re.sub(r"[_\-.]+", " ", value.lower())
    for term in sorted(NUDITY_TERMS):
        if term in normalized:
            return term
    return None


def is_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_image_ref(value: str) -> None:
    nudity_term = contains_nudity_text(value)
    if nudity_term:
        raise ValueError(f"rejected image reference because it contains nudity term: {nudity_term}")

    if is_url(value):
        return

    path = Path(value)
    if not path.is_file():
        raise ValueError(f"image file does not exist: {value}")

    guessed_type, _ = mimetypes.guess_type(path.name)
    if guessed_type and not guessed_type.startswith(IMAGE_MIME_PREFIX):
        raise ValueError(f"input file does not look like an image: {value}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--image", action="append", default=[])
    parser.add_argument("--json-output", required=True)
    args = parser.parse_args(argv[1:])

    nudity_term = contains_nudity_text(args.prompt)
    if nudity_term:
        print(f"rejected prompt because it contains nudity term: {nudity_term}", file=sys.stderr)
        return 10

    try:
        for image in args.image:
            validate_image_ref(image)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 10

    report = {
        "accepted": True,
        "checks": [
            "prompt_nudity_keyword_scan",
            "image_reference_nudity_keyword_scan",
            "local_image_file_exists",
            "local_image_extension_mime_check",
        ],
        "limitations": [
            "This MVP checker rejects obvious nudity in prompt text and image references. It does not perform pixel-level visual nudity detection.",
        ],
    }
    Path(args.json_output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
