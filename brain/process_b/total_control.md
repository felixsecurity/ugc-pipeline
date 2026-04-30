# Process B Flavor: Total Control

Use this flavor for the `total_control` option when the client provides a
source video whose voice, body movement, lip movement, timing, and camera motion
should drive the final character video.

Prepared runner:

```sh
/opt/ugc-pipeline-venv/bin/python /srv/ugc-pipeline/brain/total_control.py --request request.json --character-dir /srv/ugc-pipeline/characters/astrid
```

## Required Inputs

- `request.json` with:
  - `video_input`: URL or local path to the input video
  - `character_id`, or a clear character reference such as `Astrid`
  - optional `direction`: scene or styling direction that must not override the
    copied performance
- Character reference image:
  - `/srv/ugc-pipeline/characters/<character_id>/reference.png`

## Steps

1. Resolve the character reference image.
2. Validate the source video with `ffprobe`; Kling motion control requires 3s
   to 30.05s input duration when `character_orientation` is `video`.
3. Extract the second video frame, frame index `1`, to
   `output_images/total_control_frame_02.png`.
4. Prepare the character reference for upload. If the original reference image
   exceeds fal's upload limit, create an upload-sized JPEG copy at
   `output_images/total_control_reference.jpg` and use that copy without
   modifying the source character asset.
5. Call `fal-ai/nano-banana-2/edit` with both the character reference and the
   extracted second frame to create `output_images/total_control_pose_reference.png`.
   Use 2K resolution.
   - Place the character into the exact pose, body angle, limb placement,
     framing, mouth position, and camera perspective visible in the second
     frame.
   - Preserve character identity, face, age, hair, proportions, and recognizable
     appearance from the character reference.
   - If `direction` includes a background change, apply that background or scene
     instruction.
   - If no background direction is present, keep the background, lighting,
     camera angle, framing, and scene layout from the second frame.
   - Keep the character unobstructed and suitable for motion control.
6. Extract the source video's audio to `output_audio/source_audio.wav`.
7. Call ElevenLabs speech-to-speech to convert the source performance into the
   same destination voice used by the other Process B scripts:
   - speech-to-speech model: `eleven_multilingual_sts_v2`
   - voice: Riley
   - voice ID: `hA4zGnmTwX2NQiTRMt7o`
   - speed: `0.92`
   - stability: `0.78`
   - similarity boost: `0.85`
   - style exaggeration: `0.23`
   - format: `mp3_44100_128`
8. Save the converted voice to `output_audio/voice_changed.mp3`.
9. Mux the converted voice back onto the source video and write
   `output_videos/work/total_control_driver.mp4`.
10. Upload the Nano Banana pose reference and dubbed driver video to fal storage.
11. Call the exact Kling route used by `motion_control`:

```text
fal-ai/kling-video/v2.6/standard/motion-control
```

Use:

- `image_url`: uploaded Nano Banana pose reference image
- `video_url`: uploaded dubbed driver video
- `character_orientation`: `"video"`
- `keep_original_sound`: `true`
- `prompt`: tell the model to transfer the exact body movement, lip movement,
  timing, gesture rhythm, pose progression, camera movement, action path, and
  framing from the driver video while using the supplied character image.

12. Download the Kling result to `output_videos/total_control_motion.mp4`.
13. Replace the final audio with `output_audio/voice_changed.mp3` locally to
    guarantee the delivered video contains the ElevenLabs voice-changed audio.
14. Run Whisper base on `output_audio/voice_changed.mp3` and save timestamps to
    `whisper_timestamps.json`.
15. Convert timestamps into the same subtitle style as `avatar_voice` and
    `voice_over`:
    - few words per screen
    - large white text
    - black outline/border
    - centered social-media composition
16. Burn subtitles into the final video with ffmpeg and write
    `output_videos/final_subtitled.mp4`.

## Outputs

- `total_control_plan.json`
- `kling_total_control_result.json`
- `output_audio/source_audio.wav`
- `output_audio/voice_changed.mp3`
- `output_images/total_control_frame_02.png`
- `output_images/total_control_pose_reference.png`
- `output_images/total_control_reference.jpg` when the source reference image is
  too large for fal upload
- `output_videos/work/total_control_driver.mp4`
- `output_videos/total_control_motion.mp4`
- `output_videos/work/total_control_with_audio.mp4`
- `whisper_timestamps.json`
- `output_videos/work/subtitles.ass`
- `output_videos/final_subtitled.mp4`
- `status.json`
- `learning.md`

## Credentials And Tools

Supervisor must inject:

- `ELEVENLABS_API_KEY`
- `FAL_KEY`

Host tools required:

- `ffmpeg`
- `ffprobe`
- `whisper` with the `base` model available or downloadable

## Notes

- This flavor does not invent a script. Subtitles are transcribed from the
  ElevenLabs voice-changed audio.
- The dubbed driver video is the motion-control source, so Kling receives the
  new voice as the original sound to preserve.
- The final audio is replaced locally after Kling as a guardrail in case the
  provider response changes the audio stream.
