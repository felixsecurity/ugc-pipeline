# Process B Flavor: Nano Banana Static Video

Use this flavor for the current image-first MVP:

```sh
/opt/ugc-pipeline-venv/bin/python /srv/ugc-pipeline/brain/nano_banana.py --request request.json
```

Inputs:

- `request.json` with `prompt` and optional `image_inputs`.

Outputs:

- `fal_result.json`
- `output_images/`
- `output_videos/final.mp4`
- `video_plan.json`
- `status.json`
- `learning.md`

API credentials:

- Supervisor injects `FAL_KEY` only.

This flavor remains silent-first. It should not call ElevenLabs, Kling avatar, or
Whisper.
