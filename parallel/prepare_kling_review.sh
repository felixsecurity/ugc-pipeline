#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo "usage: $0 /srv/batch.zip batch_name publish_name [env_file]" >&2
  exit 2
fi

archive_path="$1"
batch_name="$2"
publish_name="$3"
env_file="${4:-/etc/ugc-pipeline/fal.env}"
batch_dir="/srv/${batch_name}"
publish_dir="/var/www/html/${publish_name}"
freeze_ts="9999-12-31T00:00:00+00:00"

if [[ ! -d "$batch_dir" ]]; then
  /srv/ugc-pipeline/parallel/unpack_batch.sh "$archive_path" "$batch_name" >/dev/null
fi

if [[ ! -f "$env_file" ]]; then
  echo "env file not found: $env_file" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

if [[ -z "${FAL_KEY:-}" ]]; then
  echo "FAL_KEY is not set after sourcing $env_file" >&2
  exit 1
fi

while true; do
  /srv/ugc-pipeline/parallel/run_batch.sh "$batch_dir" --once
  state_path="${batch_dir}/orchestrate.json"
  if [[ ! -f "$state_path" ]]; then
    echo "missing orchestrator state: $state_path" >&2
    exit 1
  fi

  python3 - "$state_path" "$freeze_ts" <<'PY'
import json
import sys
from pathlib import Path

state_path = Path(sys.argv[1])
freeze_ts = sys.argv[2]
state = json.loads(state_path.read_text(encoding="utf-8"))
changed = False
for video in state["videos"].values():
    if video.get("phase") == "kling_ready" and video.get("next_action_at") != freeze_ts:
        video["next_action_at"] = freeze_ts
        video.setdefault("timings", {})["frozen_pending_approval_at"] = freeze_ts
        changed = True
if changed:
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

  state_json="$(python3 - "$state_path" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
videos = state["videos"]
phases = {video_id: video["phase"] for video_id, video in videos.items()}
gate_ready = all(phase in {"kling_ready", "failed"} for phase in phases.values())
has_kling_work = any(phase in {"kling_wait", "confirm_ready", "done"} for phase in phases.values())
print(json.dumps({
    "gate_ready": gate_ready,
    "has_kling_work": has_kling_work,
    "next_action_at": state.get("scheduler", {}).get("next_action_at"),
    "phases": phases,
}))
PY
)"

  gate_ready="$(python3 -c 'import json,sys; print("1" if json.load(sys.stdin)["gate_ready"] else "0")' <<<"$state_json")"
  has_kling_work="$(python3 -c 'import json,sys; print("1" if json.load(sys.stdin)["has_kling_work"] else "0")' <<<"$state_json")"
  next_action_at="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["next_action_at"] or "")' <<<"$state_json")"

  if [[ "$has_kling_work" == "1" ]]; then
    echo "refusing to continue: Kling already started for at least one video" >&2
    exit 1
  fi

  if [[ "$gate_ready" == "1" ]]; then
    break
  fi

  sleep_seconds="$(python3 - "$next_action_at" <<'PY'
from datetime import datetime, timezone
import sys

value = sys.argv[1]
if not value:
    print(5)
    raise SystemExit
target = datetime.fromisoformat(value.replace("Z", "+00:00"))
delta = int((target - datetime.now(timezone.utc)).total_seconds())
print(max(1, min(15, delta)))
PY
)"
  sleep "$sleep_seconds"
done

python3 - "$batch_dir" "$publish_dir" "$publish_name" <<'PY'
import html
import json
import shutil
import sys
from pathlib import Path

batch_dir = Path(sys.argv[1])
publish_dir = Path(sys.argv[2])
publish_name = sys.argv[3]
state = json.loads((batch_dir / "orchestrate.json").read_text(encoding="utf-8"))

publish_dir.mkdir(parents=True, exist_ok=True)
publish_dir.chmod(0o755)
for existing in publish_dir.iterdir():
    if existing.is_file():
        existing.unlink()

cards = []
for video_id in sorted(state["videos"]):
    video = state["videos"][video_id]
    second_frame = Path(video["artifacts"]["second_frame"])
    pose_reference = Path(video["artifacts"]["pose_reference"])
    copied = []
    for src, suffix in ((second_frame, "input_frame"), (pose_reference, "nano")):
        if not src.is_file():
            continue
        ext = src.suffix.lower() or ".png"
        target = publish_dir / f"{video_id}_{suffix}{ext}"
        shutil.copyfile(src, target)
        target.chmod(0o644)
        copied.append((suffix, target.name))
    cards.append((video_id, video["phase"], copied))

index_lines = [
    "<!doctype html>",
    '<html lang="en">',
    "  <head>",
    '    <meta charset="utf-8" />',
    '    <meta name="viewport" content="width=device-width, initial-scale=1" />',
    f"    <title>{html.escape(publish_name)} review</title>",
    "    <style>",
    "      body { font-family: Arial, sans-serif; margin: 2rem; background: #f5f5f5; color: #111; }",
    "      h1 { margin-bottom: 0.25rem; }",
    "      .meta { color: #555; margin-bottom: 1.5rem; }",
    "      .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1.5rem; }",
    "      .card { background: #fff; border: 1px solid #ddd; border-radius: 12px; padding: 1rem; }",
    "      .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }",
    "      figure { margin: 0; }",
    "      img { width: 100%; height: auto; border-radius: 8px; border: 1px solid #ddd; background: #fafafa; }",
    "      figcaption { font-size: 0.9rem; margin-top: 0.35rem; color: #444; }",
    "      code { font-size: 0.95rem; }",
    "    </style>",
    "  </head>",
    "  <body>",
    f"    <h1>{html.escape(publish_name)} review</h1>",
    "    <div class=\"meta\">Input frame and Nano Banana output for each video. Kling has not been started.</div>",
    "    <div class=\"grid\">",
]

for video_id, phase, copied in cards:
    index_lines.extend(
        [
            "      <section class=\"card\">",
            f"        <h2><code>{html.escape(video_id)}</code></h2>",
            f"        <div class=\"meta\">Current phase: {html.escape(phase)}</div>",
            "        <div class=\"pair\">",
        ]
    )
    copied_map = {suffix: name for suffix, name in copied}
    for suffix, label in (("input_frame", "Input frame"), ("nano", "Nano Banana output")):
        name = copied_map.get(suffix)
        if name:
            index_lines.extend(
                [
                    "          <figure>",
                    f'            <a href="{html.escape(name)}"><img src="{html.escape(name)}" alt="{html.escape(video_id)} {html.escape(label)}" /></a>',
                    f"            <figcaption>{html.escape(label)}</figcaption>",
                    "          </figure>",
                ]
            )
        else:
            index_lines.extend(
                [
                    "          <figure>",
                    f"            <figcaption>{html.escape(label)} missing</figcaption>",
                    "          </figure>",
                ]
            )
    index_lines.extend(
        [
            "        </div>",
            "      </section>",
        ]
    )

index_lines.extend(
    [
        "    </div>",
        "  </body>",
        "</html>",
        "",
    ]
)

index_path = publish_dir / "index.html"
index_path.write_text("\n".join(index_lines), encoding="utf-8")
index_path.chmod(0o644)
PY

find "$publish_dir" -type d -exec chmod 755 {} +
find "$publish_dir" -type f -exec chmod 644 {} +

echo "$batch_dir"
echo "$publish_dir"
