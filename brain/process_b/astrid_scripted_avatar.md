# Process B Flavor: Astrid Scripted Avatar

Use this flavor when the client asks Astrid to speak a supplied script, for
example:

```text
Use Astrid and let her say: "This is the ad read."
```

Prepared runner:

```sh
/opt/ugc-pipeline-venv/bin/python /srv/ugc-pipeline/brain/astrid_avatar.py --request request.json --character-dir /srv/ugc-pipeline/characters/astrid
```

## Required Inputs

- `request.json` containing either:
  - `script`: exact spoken script, or
  - `prompt` / `client_request`: a phrase matching `Use Astrid and let her say: "..."`
  - optional `background_direction`, `background`, `scene_direction`, or a prompt
    phrase such as `change her background to ...`
- Character reference image:
  - `/srv/ugc-pipeline/characters/astrid/reference.png`

## Steps

1. Extract the exact script and save it to `script.md`.
2. Generate `output_audio/voiceover.mp3` with ElevenLabs text-to-speech:
   - model: `eleven_multilingual_v2`
   - voice: Riley
   - voice ID: `hA4zGnmTwX2NQiTRMt7o`
   - speed: `0.92`
   - stability: `0.78`
   - similarity boost: `0.85`
   - style exaggeration: `0.23`
   - format: `mp3_44100_128`
3. Prevalidate the MP3 with `ffprobe`; duration must be less than 60 seconds.
4. If a background direction is present, call `fal-ai/nano-banana-2/edit` with
   Astrid's reference image to create `output_images/avatar_reference.png`.
   Preserve Astrid's identity and replace only the background.
5. Upload the original or edited Astrid reference image and the MP3 to fal
   storage.
6. Invoke `fal-ai/kling-video/ai-avatar/v2/standard` with:
   - `image_url`: uploaded Astrid reference image URL
   - `audio_url`: uploaded ElevenLabs MP3 URL
   - `prompt`: natural UGC talking-head delivery, preserve Astrid appearance, sync lips to the supplied voiceover
7. Download the returned MP4 to `output_videos/kling_avatar.mp4`.
8. Run Whisper base on the MP3 and save timestamps to `whisper_timestamps.json`.
9. Convert timestamps into short social subtitles:
   - few words per screen
   - large white text
   - black outline/border
   - centered social-media composition
10. Burn subtitles into the video with ffmpeg and write
   `output_videos/final_subtitled.mp4`.

## Outputs

- `script.md`
- optional `avatar_reference_result.json`
- optional `output_images/avatar_reference.png`
- `output_audio/voiceover.mp3`
- `kling_avatar_result.json`
- `output_videos/kling_avatar.mp4`
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
