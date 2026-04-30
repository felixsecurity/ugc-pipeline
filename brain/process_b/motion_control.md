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
2. Resolve the input video. If `video_input` is a URL, download it to
   `output_videos/work/motion_control_input.mp4` for frame extraction.
3. Validate the resolved input video with `ffprobe`; fal's motion-control
   standard endpoint requires 3s to 30.05s input duration when
   `character_orientation` is `video`.
4. Extract the second video frame, frame index `1`, to
   `output_images/motion_control_frame_02.png`.
5. Prepare the character reference for upload.
   If the original reference image exceeds fal's upload limit, create an
   upload-sized JPEG copy at `output_images/motion_control_reference_upload.jpg`
   and use that copy without modifying the source character asset.
6. Call `fal-ai/nano-banana-2/edit` with both the character reference and the
   extracted second frame to create `output_images/motion_control_reference.png`.
   Use 2K resolution.
   - Place the character into the exact pose, body angle, limb placement,
     framing, and camera perspective visible in the second frame.
   - Preserve character identity, face, age, hair, proportions, and recognizable
     appearance from the character reference.
   - If `direction` includes a background change, apply that background or scene
     instruction.
   - If no background direction is present, keep the background, lighting,
     camera angle, framing, and scene layout from the second frame.
   - Keep the character unobstructed and suitable for motion control.
7. Upload the generated pose reference and resolved input video to fal storage.
8. Call:

```text
fal-ai/kling-video/v2.6/standard/motion-control
```

Use:

- `image_url`: uploaded Nano Banana pose reference image
- `video_url`: uploaded resolved input video
- `character_orientation`: `"video"`
- `keep_original_sound`: `true`
- `prompt`: tell the model to transfer the exact body movement, timing, gesture
  rhythm, pose progression, camera movement, and action path from the input
  video while using the character/background from the supplied image.

## Outputs

- `motion_control_plan.json`
- `kling_motion_control_result.json`
- `output_images/motion_control_frame_02.png`
- `output_images/motion_control_reference.png`
- `output_images/motion_control_reference_upload.jpg` when the source reference
  image is too large for fal upload
- `output_videos/work/motion_control_input.mp4` when `video_input` is a URL
- `output_videos/motion_control.mp4`
- `output_videos/final.mp4`
- `status.json`
- `learning.md`

## Credentials And Tools

Supervisor must inject:

- `FAL_KEY`

Host tools required:

- `ffmpeg`
- `ffprobe`

## Notes

- This flavor keeps the input video's original sound through Kling's
  `keep_original_sound: true`; it does not synthesize voiceover audio.
- This flavor does not create subtitles by default because the audio is inherited
  from the source video and may not be speech.
- Prefer input videos where the source person has a realistic style, visible head
  and full or upper body, and no major occlusion.
