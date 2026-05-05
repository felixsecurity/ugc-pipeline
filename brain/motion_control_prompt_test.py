#!/usr/bin/env python3
"""Lightweight checks for motion-control prompt shaping."""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from motion_control import build_motion_prompt, build_pose_reference_prompt  # noqa: E402


def test_default_reference_prompt_is_character_agnostic() -> None:
    prompt = build_pose_reference_prompt("")
    assert "supplied character reference" in prompt
    assert "Keep the background, camera angle, lighting, framing, and scene layout from the video frame." in prompt
    assert "Astrid" not in prompt


def test_free_form_direction_is_enhanced() -> None:
    direction = "take the outfit from the girl in the inputvideo frame but make the lighting from red to blue"
    prompt = build_pose_reference_prompt(direction)

    assert "free-form visual edit brief" in prompt
    assert "wardrobe or outfit change" in prompt
    assert "lighting or color-temperature change" in prompt
    assert "Client direction: take the outfit from the girl in the inputvideo frame but make the lighting from red to blue" in prompt
    assert "Astrid" not in prompt


def test_motion_prompt_uses_generic_character_language() -> None:
    direction = "keep the outfit from the source frame and change red light to blue"
    prompt = build_motion_prompt(direction)

    assert "the supplied character reference" in prompt
    assert "Transfer the exact body movement, timing, gesture rhythm, pose progression, camera movement, and action path from the reference video." in prompt
    assert "wardrobe or outfit change" in prompt
    assert "lighting or color-temperature change" in prompt
    assert "Astrid" not in prompt


def main() -> int:
    test_default_reference_prompt_is_character_agnostic()
    test_free_form_direction_is_enhanced()
    test_motion_prompt_uses_generic_character_language()
    print("motion_control prompt shaping checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
