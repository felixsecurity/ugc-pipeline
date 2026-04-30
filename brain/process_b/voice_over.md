# Process B Flavor: Voice Over

Use this flavor for the `voice_over` option when the client request references a
known character and provides both:

- stage direction describing what should happen visually
- exact text to read out as a voiceover

Example request shape:

```text
Use Astrid. Stage direction: she walks through a bright kitchen and presents the serum.
Voiceover: "This is the step I never skip before makeup."
```

This is not the talking-avatar flavor. The character does not need synchronized
mouth animation. The spoken audio is generated separately, and the visuals are a
chain of image-to-video segments.

Prepared runner:

```sh
/opt/ugc-pipeline-venv/bin/python /srv/ugc-pipeline/brain/voice_over.py --request request.json --character-dir /srv/ugc-pipeline/characters/astrid
```

## Required Inputs

- `request.json` with a prompt or client request containing:
  - `character_id`, or a clear character reference such as `Astrid`
  - stage direction
  - exact voiceover text
- Character reference image:
  - `/srv/ugc-pipeline/characters/<character_id>/reference.png`

## Extraction

1. Resolve the character from the request. The first supported character is
   `astrid`.
2. Extract the exact voiceover text and save it to `script.md`.
3. Extract the stage direction separately and save it in `voice_over_plan.json`.
4. Do not invent voiceover words. If the request does not contain exact spoken
   text, fail before calling paid providers.
5. Expand the stage direction into a commercial visual plan, but preserve all
   explicit client constraints.

## Audio

Generate `output_audio/voiceover.mp3` with the same ElevenLabs configuration as
`avatar_voice`:

- model: `eleven_multilingual_v2`
- voice: Riley
- voice ID: `hA4zGnmTwX2NQiTRMt7o`
- speed: `0.92`
- stability: `0.78`
- similarity boost: `0.85`
- style exaggeration: `0.23`
- format: `mp3_44100_128`

Prevalidate the MP3 with `ffprobe`. Let the measured audio duration determine
the video duration:

- round the audio duration up to the next 5-second mark
- 18s audio means 20s video
- the minimum video duration is 5s

Segment count is `rounded_duration_seconds / 5`.

## Intermediate Images

Generate a chain of intermediate images with `fal-ai/nano-banana-2/edit`, using
the same Nano Banana edit usage described by `still_images`.

Rules:

- Use the character reference image as the input to the first image generation.
- Generate `segment_count + 1` images total.
- For a 20s video, generate 5 images total: frame 0, frame 5, frame 10, frame 15,
  and frame 20.
- Always use the most recently generated image as an input to the next image
  generation so character identity, wardrobe, lighting, and setting remain
  continuous.
- Derive each image prompt from the original client stage direction. Do not
  drift into unrelated action.
- Treat the image chain as one continuous route through one coherent physical
  space. Each keyframe must put the character farther along the route than the
  previous keyframe, not back at the start or moving in a contradictory
  direction.
- Preserve the client's stated body orientation. If the client says the
  character is facing away from camera, every keyframe and segment should keep
  the character facing away unless the request explicitly asks for a turn.
- Do not invent new large scene elements between keyframes. Doors, vehicles,
  walls, machinery, props, and background geometry should remain consistent and
  should only appear when implied by the original stage direction or already
  present in the previous keyframe.
- Prompt each image as a clean photorealistic video keyframe with no captions,
  slogans, UI, subtitles, watermarks, or added text.
- Preserve the named character's appearance from the reference image.

Write:

- `output_images/00.png`
- `output_images/01.png`
- continuing through `output_images/<segment_count>.png`

Record the per-frame prompts and source image chain in `voice_over_plan.json`.

## Video Segments

For each 5s segment, call:

```text
fal-ai/kling-video/v2.6/pro/image-to-video
```

Use:

- `start_image_url`: uploaded current image
- `end_image_url`: uploaded next image
- `duration`: `"5"`
- `generate_audio`: `false`
- `prompt`: concise motion direction for only that 5s segment, extracted from
  the original stage direction and the neighboring keyframes
- `negative_prompt`: include blur, distortion, low quality, identity drift,
  warped hands, bad anatomy, turning around, reversing direction, walking
  backward, teleporting, inconsistent environment, newly invented large objects,
  text overlays, captions, logos, and watermarks

The end frame of each clip is the start frame of the next clip by construction:

- clip 1 uses images `00 -> 01`
- clip 2 uses images `01 -> 02`
- continue until the final image

Download segment MP4s to:

- `output_videos/work/segment_01.mp4`
- `output_videos/work/segment_02.mp4`
- continuing through the final segment

Record raw fal responses in `kling_voice_over_result.json`.

## Stitching, Audio, And Subtitles

1. Use ffmpeg concat to stitch all silent 5s segments into
   `output_videos/work/joined.mp4`.
2. Attach `output_audio/voiceover.mp3` with ffmpeg.
3. Trim or pad the final video to the rounded 5-second duration. Audio should
   start at `0.0s`; black/silent tail is acceptable only after the voiceover ends.
4. Run Whisper base on `output_audio/voiceover.mp3` and save timestamps to
   `whisper_timestamps.json`.
5. Convert timestamps into the same subtitle style as `avatar_voice`:
   - few words per screen
   - large white text
   - black outline/border
   - centered social-media composition
6. Burn subtitles into the final video with ffmpeg and write
   `output_videos/final_subtitled.mp4`.

## Outputs

- `script.md`
- `voice_over_plan.json`
- `output_audio/voiceover.mp3`
- `output_images/00.png` through `output_images/<segment_count>.png`
- `kling_voice_over_result.json`
- `output_videos/work/segment_*.mp4`
- `output_videos/work/joined.mp4`
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

- This flavor uses Kling image-to-video without native audio. ElevenLabs remains
  the only voice source.
- This flavor may exceed the four-image cap from the silent `still_images` MVP
  because its image count is derived from voiceover duration.
- Keep paid-provider calls ordered and resumable: audio first, then keyframes,
  then 5s video segments, then local ffmpeg and Whisper work.
