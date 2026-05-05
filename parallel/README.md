# Parallel Batch Motion Control

This module adds a batch-oriented Process B variant for motion-control jobs that
should overlap remote waits instead of processing videos sequentially.

## Scope

- Input archive: `.zip` or `.tar.gz`
- Unpacked batch root: `/srv/batch_x`
- Input files inside batch root:
  - multiple `.mp4` files
  - one `instructions.txt`
- Current request type:
  - `motion_control`

## Files Created In The Batch Root

- `orchestrate.json`: durable scheduler state and per-video metadata
- `events.log`: JSONL event stream
- `reflection.md`: generated summary of elapsed time, waits, bottlenecks, and per-video outcomes
- `videos/<video_id>/...`: per-video artifacts and outputs

## Published Outputs

After the batch reaches a terminal state with at least one successful video,
the orchestrator also creates:

- `/var/www/html/<batch_name>/`
- `/var/www/html/<batch_name>/index.html`

Only successful final result videos are copied there. The publish tree is made
web-readable with `755` directories and `644` files. Inputs and intermediate
artifacts are not published. The publish step is idempotent and refreshes the
public tree whenever the completed set changes, including late retry recoveries.

## Main Scripts

- `unpack_batch.sh`: unpack an archive under `/srv/`
- `run_batch.sh`: run the orchestrator and final reflection step
- `orchestrate_batch.py`: inline Nano Banana and Kling scheduler
- `analyze_events.py`: derive `reflection.md` from `events.log`

## Execution Model

The scheduler is stateful and resumable. It uses `orchestrate.json` to track
which videos are ready for prompt construction, waiting on Nano Banana, waiting
on Kling, ready for confirmation, done, or failed.

The scheduler does not busy-poll. It records the next eligible check time for
remote jobs and moves to other videos while those jobs are in flight.

Default polling delays:

- Nano Banana status checks: 10 seconds
- Kling status checks: 150 seconds

## Prompt Construction

Prompt generation is deliberate rather than raw string concatenation:

1. Parse `instructions.txt` into structured intent.
2. Normalize visual constraints and motion requirements.
3. Resolve character references such as `Astrid` and `George`.
4. Build model-specific prompts for:
   - Nano Banana pose/reference generation
   - Kling motion transfer
5. Persist prompt artifacts for auditability before invocation.

Global negatives are always enforced:

- `No text on screen.`
- `No text overlays.`

When the batch reaches a terminal state, each successful per-video `final.mp4`
is copied into the world-visible publish directory as `<video_id>.mp4`, and an
`index.html` is generated to link the published outputs.

Nano Banana has one bounded retry for generic transient failures. The retry
keeps the same core prompt but varies the composition language slightly to
reduce repeat generation failures.

## Environment

- Python runtime: `/opt/ugc-pipeline-venv/bin/python`
- Required env var:
  - `FAL_KEY`
- Host tools:
  - `ffmpeg`
  - `ffprobe`

## Syntax Verification

The implementation is intended to be syntax-checked without running jobs:

```sh
python -m py_compile /srv/ugc-pipeline/parallel/orchestrate_batch.py /srv/ugc-pipeline/parallel/analyze_events.py
bash -n /srv/ugc-pipeline/parallel/unpack_batch.sh /srv/ugc-pipeline/parallel/run_batch.sh
```
