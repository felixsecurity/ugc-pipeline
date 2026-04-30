#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_root

if [[ "$#" -lt 1 || "$#" -gt 2 ]]; then
  echo "usage: publish_debug_image.sh <request-dir> [label]" >&2
  exit 2
fi

request_dir="$(readlink -f "$1")"
label="${2:-$(basename "$request_dir")}"

if [[ ! -d "$request_dir" ]]; then
  echo "request directory does not exist: $request_dir" >&2
  exit 1
fi

if [[ ! -d "$request_dir/output_images" && ! -d "$request_dir/output_videos" ]]; then
  echo "request has no output_images or output_videos directory: $request_dir" >&2
  exit 1
fi

safe_request_id="$(printf '%s' "$(basename "$request_dir")" | tr -c 'A-Za-z0-9_.-' '_')"
publish_root="/var/www/html/debug/ugc"
publish_dir="$publish_root/$safe_request_id"

install -d -m 755 -o root -g root "$publish_dir"

image_names=()
if [[ -d "$request_dir/output_images" ]]; then
  image_count=0
  while IFS= read -r image_path; do
    image_count=$((image_count + 1))
    extension="${image_path##*.}"
    if [[ "$extension" == "$image_path" ]]; then
      extension="png"
    fi
    image_name="$(printf '%02d.%s' "$image_count" "$extension")"
    image_names+=("$image_name")
    install -m 644 -o root -g root "$image_path" "$publish_dir/$image_name"
  done < <(find "$request_dir/output_images" -maxdepth 1 -type f | sort)
fi

video_name=""
video_caption="Final video"
if [[ -f "$request_dir/output_videos/final_subtitled.mp4" ]]; then
  video_name="final_subtitled.mp4"
  video_caption="Final subtitled video"
  install -m 644 -o root -g root "$request_dir/output_videos/final_subtitled.mp4" "$publish_dir/$video_name"
elif [[ -f "$request_dir/output_videos/final.mp4" ]]; then
  video_name="final.mp4"
  video_caption="Silent final video"
  install -m 644 -o root -g root "$request_dir/output_videos/final.mp4" "$publish_dir/$video_name"
fi

if [[ -z "$video_name" && "${#image_names[@]}" -eq 0 ]]; then
  echo "no publishable images or final video found in: $request_dir" >&2
  exit 1
fi

prompt=""
mode=""
model=""
if [[ -f "$request_dir/fal_result.json" ]]; then
  prompt="$(python3 - "$request_dir/fal_result.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("request", {}).get("prompt", ""))
PY
)"
  mode="$(python3 - "$request_dir/fal_result.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("mode", ""))
PY
)"
  model="$(python3 - "$request_dir/fal_result.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("model", ""))
PY
)"
elif [[ -f "$request_dir/request.json" ]]; then
  prompt="$(python3 - "$request_dir/request.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("prompt", "") or data.get("script", ""))
PY
)"
  mode="$(python3 - "$request_dir/request.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("process_b_mode", ""))
PY
)"
  if [[ -f "$request_dir/kling_avatar_result.json" ]]; then
    model="$(python3 - "$request_dir/kling_avatar_result.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data.get("model", ""))
PY
)"
  elif [[ -f "$request_dir/kling_voice_over_result.json" ]]; then
    model="$(python3 - "$request_dir/kling_voice_over_result.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
models = [data.get("nano_model", ""), data.get("kling_model", "")]
print(", ".join(model for model in models if model))
PY
)"
  elif [[ -f "$request_dir/kling_motion_control_result.json" ]]; then
    model="$(python3 - "$request_dir/kling_motion_control_result.json" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
kling = data.get("kling", {})
print(kling.get("model", ""))
PY
)"
  fi
fi

python3 - "$publish_dir/index.html" "$label" "$safe_request_id" "$prompt" "$mode" "$model" "$video_name" "$video_caption" "${image_names[@]}" <<'PY'
import html
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
label = sys.argv[2]
request_id = sys.argv[3]
prompt = sys.argv[4]
mode = sys.argv[5]
model = sys.argv[6]
video_name = sys.argv[7]
video_caption = sys.argv[8]
image_names = sys.argv[9:]

figures = []
if video_name:
    figures.append(
        f'<figure><video src="{html.escape(video_name)}" controls playsinline></video>'
        f'<figcaption>{html.escape(video_caption)}</figcaption></figure>'
    )

for index, name in enumerate(image_names, start=1):
    figures.append(
        f'<figure><img src="{html.escape(name)}" alt="Debug output {index}">'
        f'<figcaption>Output {index}</figcaption></figure>'
    )

output_path.write_text(
    """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UGC Debug Request</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 32px; background: #f6f6f6; color: #111; }}
    main {{ max-width: 1100px; margin: 0 auto; }}
    .meta {{ margin-bottom: 24px; padding: 16px; background: white; border: 1px solid #ddd; }}
    figure {{ margin: 0 0 32px; padding: 16px; background: white; border: 1px solid #ddd; }}
    img, video {{ max-width: 100%; height: auto; display: block; }}
    figcaption {{ margin-top: 12px; font-weight: 600; }}
    code {{ overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <main>
    <h1>UGC Debug Request</h1>
    <section class="meta">
      <p><strong>Label:</strong> {label}</p>
      <p><strong>Request ID:</strong> <code>{request_id}</code></p>
      <p><strong>Mode:</strong> {mode}</p>
      <p><strong>Model:</strong> <code>{model}</code></p>
      <p><strong>Prompt:</strong> {prompt}</p>
      <p><a href="../">Back to debug index</a></p>
    </section>
    {figures}
  </main>
</body>
</html>
""".format(
        label=html.escape(label),
        request_id=html.escape(request_id),
        mode=html.escape(mode),
        model=html.escape(model),
        prompt=html.escape(prompt),
        figures="\n    ".join(figures),
    ),
    encoding="utf-8",
)
PY

python3 - "$publish_root/index.html" "$publish_root" <<'PY'
import html
import os
import sys
from pathlib import Path

index_path = Path(sys.argv[1])
root = Path(sys.argv[2])
entries = []
for child in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
    if child.is_dir() and (child / "index.html").is_file():
        entries.append(f'<li><a href="{html.escape(child.name)}/">{html.escape(child.name)}</a></li>')

index_path.write_text(
    """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>UGC Debug Index</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 32px; background: #f6f6f6; color: #111; }}
    main {{ max-width: 900px; margin: 0 auto; padding: 16px; background: white; border: 1px solid #ddd; }}
    li {{ margin: 8px 0; }}
  </style>
</head>
<body>
  <main>
    <h1>UGC Debug Index</h1>
    <ul>
      {entries}
    </ul>
  </main>
</body>
</html>
""".format(entries="\n      ".join(entries)),
    encoding="utf-8",
)
PY

chmod -R a+rX "$publish_root"
echo "$publish_dir"
