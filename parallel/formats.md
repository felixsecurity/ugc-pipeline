# Batch File Formats

## `instructions.txt`

Expected shape:

```text
input_0
character: Astrid
clothing: exact match to video
background: identical to video
motion: exact mimic

input_1
motion_control
characters: woman on the left: Astrid. Guy on the right: George
clothing: exact match to video
background: identical to video
motion: exact mimic

No text on screen. No overlays
```

Rules:

- Each block begins with a video stem such as `input_0` or `vid_3`
- The next non-empty line may be the mode; if omitted, the parser defaults to
  `motion_control`
- Remaining `key: value` lines become request constraints
- A trailing free-form line outside any block becomes a global instruction

## `orchestrate.json`

Top-level fields:

- `batch_dir`
- `created_at`
- `updated_at`
- `status`
- `global_rules`
- `scheduler`
- `videos`

Scheduler fields:

- `nano_poll_seconds`
- `kling_poll_seconds`
- `next_action_at`
- `completed_videos`
- `failed_videos`

Per-video fields:

- `video_id`
- `source_video`
- `phase`
- `mode`
- `request`
- `character_layout`
- `artifacts`
- `remote`
- `timings`
- `next_action_at`
- `last_error`

Important phases:

- `ready_for_prompting`
- `nano_wait`
- `kling_ready`
- `kling_wait`
- `confirm_ready`
- `done`
- `failed`

## `events.log`

One JSON object per line.

Suggested fields:

- `ts`
- `video_id`
- `phase`
- `event`
- `status`
- `duration_ms`
- `wait_until`
- `remote_job_id`
- `details`

Examples:

```json
{"ts":"2026-05-04T12:00:00Z","video_id":"input_0","phase":"prompting","event":"prompt_package_built","status":"ok","duration_ms":822}
{"ts":"2026-05-04T12:00:01Z","video_id":"input_0","phase":"nano_wait","event":"remote_submitted","status":"ok","remote_job_id":"abc123","wait_until":"2026-05-04T12:00:11Z"}
{"ts":"2026-05-04T12:02:41Z","video_id":"input_0","phase":"kling_wait","event":"poll_rescheduled","status":"waiting","wait_until":"2026-05-04T12:05:11Z"}
```

## `reflection.md`

Generated from `events.log` and should report:

- total elapsed time
- active prompting time
- estimated remote wait time
- per-phase breakdown
- per-video summary
- bottlenecks
- scheduling recommendations

## World-Visible Publish Folder

After the batch reaches a terminal state with at least one successful video,
publish:

- `/var/www/html/<batch_name>/`

Rules:

- copy only final result videos
- do not copy source inputs
- do not copy intermediate JSON, images, or work files
- use `<video_id>.mp4` for published filenames
- keep the directory web-readable with `755` permissions and files with `644`
