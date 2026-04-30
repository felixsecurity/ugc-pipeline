# Process B Flavor: Slide Show

Use this flavor for the `slide_show` option when the input is a local folder
containing a narration script and numbered still images.

Prepared runner:

```sh
/opt/ugc-pipeline-venv/bin/python /srv/ugc-pipeline/brain/slide_show.py --input-dir /path/to/folder
```

## Required Inputs

- Local folder containing:
  - `script.txt` or `script.txt.txt`
  - numbered images beginning at `1`, for example `1.jpg`, `2.webp`, `3.png`
- Empty script lines are ignored.
- The number of non-empty script lines must match the number of numbered images.
- Images must be contiguous from `1` with no missing index.

Supported image extensions:

- `.png`
- `.jpg`
- `.jpeg`
- `.webp`
- `.bmp`
- `.tif`
- `.tiff`

## Steps

1. Resolve `script.txt` and numbered images.
2. Fail early if the non-empty script line count does not match the image count.
3. Generate `output_audio/voiceover.mp3` with the same ElevenLabs TTS
   configuration as the other speech modes:
   - model: `eleven_multilingual_v2`
   - voice: Riley
   - voice ID: `hA4zGnmTwX2NQiTRMt7o`
   - speed: `0.92`
   - stability: `0.78`
   - similarity boost: `0.85`
   - style exaggeration: `0.23`
   - format: `mp3_44100_128`
4. Run Whisper base on the generated MP3 and save timestamps to
   `whisper_timestamps.json`.
5. Align each non-empty script line to the Whisper word timeline in order.
   - Use `script.txt` text for subtitle spelling.
   - Prefer fuzzy sequential word matching so small spelling, punctuation,
     number, or name differences do not force a full fallback.
   - If word-level alignment is incomplete, fall back to proportional timing by
     script word count.
6. Render one video segment per image.
   - Final canvas: `720x1280`
   - Top 20%: black padding
   - Middle 60%: image area
   - Bottom 20%: subtitle area
   - Scale each image down/up to fit inside the image area.
   - Place the image immediately below the top padding.
   - Pad with black below or left/right as needed.
7. Concatenate segments, attach the ElevenLabs audio, and burn short subtitles
   at fixed anchor position `y=780`.
8. Write `output_videos/final_subtitled.mp4`.

## Outputs

- `script.md`
- `slide_show_plan.json`
- `output_audio/voiceover.mp3`
- `whisper_timestamps.json`
- `output_videos/work/subtitles.ass`
- `output_videos/work/segment_*.mp4`
- `output_videos/final_subtitled.mp4`
- `status.json`
- `learning.md`

## Credentials And Tools

Supervisor must inject:

- `ELEVENLABS_API_KEY`

Host tools required:

- `ffmpeg`
- `ffprobe`
- `whisper` with the `base` model available or downloadable
