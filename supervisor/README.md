# Supervisor

Root-only Process A scripts for the mini user-separation prototype.

This directory should be `700` and owned by `root:root`.

## Scripts

- `init_permissions.sh`
  - Enforces repo permissions.
  - Keeps `./brain` readable/executable by client users and writable only by root.
  - Keeps `./supervisor` root-only.

- `submit_request.sh <client-id> <prompt> [image-url-or-file ...]`
  - Creates the Linux user on first request.
  - Creates the private client folder under `/srv/ugc-clients/<client-id>`.
  - Rejects obvious nudity in prompt text and image references before execution.
  - Copies local image inputs into the private request folder.
  - Writes `request.json`.
  - Runs Nano Banana Process B as the client user, capped at four static images.
  - Renders a silent short-form MP4 from those images with local ffmpeg effects.
  - Runs basic Process C evaluation as the client user.
  - Writes `status.json`, `learning.md`, `output_images/`, `output_videos/final.mp4`, `video_plan.json`, and `evaluation.md` in the request folder.

- `publish_debug_image.sh <request-dir> [label]`
  - Explicitly publishes selected request outputs to nginx for debugging.
  - Copies images from `output_images/` and `output_videos/final.mp4` into `/var/www/html/debug/ugc/<request-id>/`.
  - Creates a per-request debug page and refreshes `/var/www/html/debug/ugc/index.html`.
  - Does not run automatically from Process B; publishing remains a supervisor/debug decision.

- `run_codex_for_client.sh <client-id> <codex-args...>`
  - Runs `codex` with dropped permissions as the client user.
  - Copies root's existing Codex `auth.json` and `config.toml` into the client user's private `.codex` folder.

- `collect_learnings.sh [output-path]`
  - Reads all `learning.md` and `learnings.md` files under `/srv/ugc-clients`.
  - Writes an aggregate report, defaulting to `/srv/ugc-pipeline/all_learnings.md` so the aggregate can be version-controlled.

- `sleep_on_learnings.sh [--apply]`
  - Runs the root-level sleeping process.
  - Refreshes `all_learnings.md`.
  - Uses root-level Codex to reason about whether `brain/` should change.
  - Default mode writes `sleep_report.md` without modifying `brain/`.
  - `--apply` allows Codex to edit `brain/` if it decides a change is necessary.

## Important Security Note

This prototype intentionally recycles the root Codex login into each client user's private home. That proves the mechanics, but it means any process running as that client user can read its copied Codex token. For production, prefer a narrow API proxy, scoped service tokens, or one managed service identity per tenant.

The root-owned Codex credential template is stored outside git at:

```text
/etc/ugc-pipeline/codex-template
```

The fal API key is stored outside git at:

```text
/etc/ugc-pipeline/fal.env
```

Use the official fal environment variable name:

```sh
FAL_KEY=your_fal_api_key
```

`FALAPIKEY=...` is also accepted as a compatibility alias, but `FAL_KEY` is preferred because that is what the fal client expects.

Create it as root:

```sh
install -d -m 700 -o root -g root /etc/ugc-pipeline
install -m 600 -o root -g root /dev/null /etc/ugc-pipeline/fal.env
printf 'FAL_KEY=%s\n' 'your_fal_api_key' > /etc/ugc-pipeline/fal.env
```

Install Python dependencies:

```sh
apt-get install -y python3-venv
python3 -m venv /opt/ugc-pipeline-venv
/opt/ugc-pipeline-venv/bin/pip install -r /srv/ugc-pipeline/requirements.txt
chown -R root:root /opt/ugc-pipeline-venv
chmod -R a+rX /opt/ugc-pipeline-venv
```

Install ffmpeg for local silent video rendering:

```sh
apt-get install -y ffmpeg
```

Example text-only request:

```sh
/srv/ugc-pipeline/supervisor/submit_request.sh UserA "a clean product photo of a red ceramic mug on a white table"
```

Example image-edit request with a URL:

```sh
/srv/ugc-pipeline/supervisor/submit_request.sh UserA "change the mug color to blue" "https://example.com/mug.png"
```

Example image-edit request with a local file:

```sh
/srv/ugc-pipeline/supervisor/submit_request.sh UserA "replace the background with a bright kitchen" /path/to/input.png
```

Example debug publishing:

```sh
/srv/ugc-pipeline/supervisor/publish_debug_image.sh /srv/ugc-clients/usera/requests/<request-id> "UserA review"
```

Then open:

```text
http://SERVER_IP/debug/ugc/
```
