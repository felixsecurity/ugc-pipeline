# Sleep Report

## Inputs Reviewed
- `all_learnings.md`
- `brain/README.md`
- `brain/nano_banana.py`
- `brain/evaluate_image.py`

## Decision
Brain change needed

## Reasoning
The learnings say Process B should expand weak client briefs into concrete advertising concepts while preserving explicit constraints. `brain/nano_banana.py` already does this through prompt strategies, advertising-grade effective prompts, product preservation guidance, and learning output.

The learnings also say Process C should verify prompt match and absence of disallowed nudity. `brain/evaluate_image.py` currently performs structural checks and writes human review reminders, but it does not actually inspect generated images for prompt relevance or nudity. That leaves a gap between the recorded learning and current Process C behavior.

## Proposed Or Applied Changes
Report-only mode: no files modified.

Proposed change: enhance Process C so `evaluate_image.py` performs actual image review, or clearly integrates with a required visual review step, for:
- generated image matches request/effective prompt
- generated image contains no disallowed nudity
- any uncertainty escalates to a human operator

## Follow-Up
- Operator should decide whether Process C gets automated visual evaluation now or remains structural-only with mandatory human review.