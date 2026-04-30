# Process B Flavor: Motion Control

Use this flavor for the `motion_control` option when the client provides an
input video whose movement should drive the output.

Prepared runner:

```sh
/opt/ugc-pipeline-venv/bin/python /srv/ugc-pipeline/brain/motion_control.py --request request.json --character-dir /srv/ugc-pipeline/characters/astrid
```

## Required Inputs

- `request.json` with:
  - `video_input`: URL or local path to the input video
  - `character_id`: defaults to `astrid`
  - optional `direction`: background or outfit modification
- Character reference image:
  - `/srv/ugc-pipeline/characters/<character_id>/reference.png`

## Steps

1. Resolve the character reference image.
2. Validate local input videos with `ffprobe`; fal's motion-control standard
   endpoint requires 3s to 30.05s input duration when `character_orientation` is
   `video`.
3. If `direction` is present, call `fal-ai/nano-banana-2/edit` on the character
   reference image to create `output_images/motion_control_reference.png`.
   - Apply only requested background/outfit changes.
   - Preserve character identity, face, age, hair, proportions, and recognizable
     appearance.
   - Keep the character unobstructed and suitable for motion control.
4. If `direction` is absent, use the original character reference image.
5. Upload the selected reference image and input video to fal storage when local.
6. Call:

```text
fal-ai/kling-video/v2.6/standard/motion-control
```

Use:

- `image_url`: uploaded original or edited character/background reference image
- `video_url`: uploaded or remote input video
- `character_orientation`: `"video"`
- `keep_original_sound`: `true`
- `prompt`: tell the model to transfer the exact body movement, timing, gesture
  rhythm, pose progression, camera movement, and action path from the input
  video while using the character/background from the supplied image.

## Outputs

- `motion_control_plan.json`
- `kling_motion_control_result.json`
- `output_images/motion_control_reference.png` when Nano Banana edit is used
- `output_videos/motion_control.mp4`
- `output_videos/final.mp4`
- `status.json`
- `learning.md`

## Credentials And Tools

Supervisor must inject:

- `FAL_KEY`

Host tools required:

- `ffprobe`

## Notes

- This flavor keeps the input video's original sound through Kling's
  `keep_original_sound: true`; it does not synthesize voiceover audio.
- This flavor does not create subtitles by default because the audio is inherited
  from the source video and may not be speech.
- Prefer input videos where the source person has a realistic style, visible head
  and full or upper body, and no major occlusion.
