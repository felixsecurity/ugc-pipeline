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

if [[ ! -d "$request_dir/output_images" ]]; then
  echo "request has no output_images directory: $request_dir" >&2
  exit 1
fi

safe_request_id="$(printf '%s' "$(basename "$request_dir")" | tr -c 'A-Za-z0-9_.-' '_')"
publish_root="/var/www/html/debug/ugc"
publish_dir="$publish_root/$safe_request_id"

install -d -m 755 -o root -g root "$publish_dir"

image_count=0
while IFS= read -r image_path; do
  image_count=$((image_count + 1))
  extension="${image_path##*.}"
  if [[ "$extension" == "$image_path" ]]; then
    extension="png"
  fi
  install -m 644 -o root -g root "$image_path" "$publish_dir/$(printf '%02d.%s' "$image_count" "$extension")"
done < <(find "$request_dir/output_images" -maxdepth 1 -type f | sort)

if [[ "$image_count" -eq 0 ]]; then
  echo "no output images found in: $request_dir/output_images" >&2
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
fi

python3 - "$publish_dir/index.html" "$label" "$safe_request_id" "$prompt" "$mode" "$model" "$image_count" <<'PY'
import html
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
label = sys.argv[2]
request_id = sys.argv[3]
prompt = sys.argv[4]
mode = sys.argv[5]
model = sys.argv[6]
image_count = int(sys.argv[7])

figures = []
for index in range(1, image_count + 1):
    name = f"{index:02d}.png"
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
    img {{ max-width: 100%; height: auto; display: block; }}
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
