# Parallel Batch Motion Control Skill

Use this folder when a request is a batch motion-control job packaged as an
archive with multiple input videos plus one `instructions.txt`.

## What This Skill Does

- Unpack a batch archive under `/srv/`
- Parse `instructions.txt` into structured per-video requests
- Build explicit prompt artifacts rather than gluing raw strings together
- Submit Nano Banana and Kling work inline from one orchestrator
- Overlap prompt work with remote waits across multiple videos
- Record durable scheduler state in `orchestrate.json`
- Append timestamped JSONL events to `events.log`
- Produce `reflection.md` from those events
- Publish any successful finals to `/var/www/html/<batch_name>/`, even when
  the batch ends in a mixed terminal state

## Inputs

- Archive path:
  - `.zip`
  - `.tar.gz`
- Or an already-unpacked batch folder under `/srv/`

## Required Batch Layout After Unpack

- batch root contains:
  - `instructions.txt`
  - one or more `.mp4` files

## Output Layout

- batch root:
  - `orchestrate.json`
  - `events.log`
  - `reflection.md`
- per-video:
  - `videos/<video_id>/request.json`
  - `videos/<video_id>/prompt_brief.json`
  - `videos/<video_id>/invocation_plan.json`
  - `videos/<video_id>/nano_banana_result.json`
  - `videos/<video_id>/kling_motion_control_result.json`
  - `videos/<video_id>/status.json`
  - `videos/<video_id>/learning.md`
  - `videos/<video_id>/output_images/...`
  - `videos/<video_id>/output_videos/...`
- world-visible:
  - `/var/www/html/<batch_name>/<video_id>.mp4`

## Prompting Rules

- Treat prompt construction as a reasoning step.
- First normalize user intent into explicit visual and motion constraints.
- Preserve identity and position for all named characters.
- Preserve exact movement and timing in Kling prompts.
- Always include:
  - `No text on screen.`
  - `No text overlays.`
- Allow one bounded Nano Banana retry for transient generic failures, and vary
  the retry prompt slightly to improve recovery odds.

## Scheduler Rules

- Do not process videos sequentially by default.
- After submitting one remote request, move to the next locally actionable item.
- Poll Nano Banana after roughly 10 seconds.
- Poll Kling after roughly 150 seconds.
- Log every submission, poll, wait, completion, and failure.
- Keep publish output web-readable with `755` directories and `644` files.
