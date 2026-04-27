#!/usr/bin/env python3
"""Basic Process C evaluator for generated image request folders."""

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


def main(argv: list[str]) -> int:
    request_dir = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    checks: list[tuple[str, bool, str]] = []

    request_json = request_dir / "request.json"
    request_check = request_dir / "request_check.json"
    fal_result = request_dir / "fal_result.json"
    output_dir = request_dir / "output_images"
    status_json = request_dir / "status.json"
    learning_md = request_dir / "learning.md"

    checks.append(("request.json exists", request_json.is_file(), "Process A request payload is present."))
    checks.append(("request_check.json exists", request_check.is_file(), "Process A safety check report is present."))
    checks.append(("fal_result.json exists", fal_result.is_file(), "Process B fal result is present."))
    checks.append(("output_images/ exists", output_dir.is_dir(), "Process B output image directory is present."))
    checks.append(("status.json exists", status_json.is_file(), "Process B status metadata is present."))
    checks.append(("learning.md exists", learning_md.is_file(), "Process B learning note is present."))

    request: dict[str, Any] = load_json(request_json) if request_json.is_file() else {}
    result: dict[str, Any] = load_json(fal_result) if fal_result.is_file() else {}
    status: dict[str, Any] = load_json(status_json) if status_json.is_file() else {}
    check_report: dict[str, Any] = load_json(request_check) if request_check.is_file() else {}
    downloaded = result.get("downloaded_images", []) if isinstance(result.get("downloaded_images", []), list) else []
    pngs = sorted(output_dir.glob("*")) if output_dir.is_dir() else []

    checks.append(("Process A accepted request", bool(check_report.get("accepted")), "Request checker accepted the request."))
    checks.append(("Process B succeeded", status.get("status") == "succeeded", "Process B status is succeeded."))
    checks.append(("at least one image downloaded", len(downloaded) > 0 and len(pngs) > 0, "At least one local output image exists."))

    passed = all(item[1] for item in checks)
    mode = result.get("mode", "unknown")
    model = result.get("model", "unknown")
    prompt = request.get("prompt", "")
    request_details = result.get("request", {}) if isinstance(result.get("request", {}), dict) else {}
    effective_prompt = request_details.get("effective_prompt", "")
    prompt_strategy = request_details.get("prompt_strategy", "unknown")

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
            "- Verify generated or edited image contains no disallowed nudity.",
            "- Verify the output is acceptable before publishing outside the debug workflow.",
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
