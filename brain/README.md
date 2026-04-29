# Brain

This folder is the shared, root-controlled process library for the mini UGC pipeline prototype.

The repo is expected to live at `/srv/ugc-pipeline`, so dropped client users can execute these scripts directly from the checkout without traversing `/root`.

Permissions model:

- Owned by `root:root`.
- Directories are `755`.
- Scripts are executable by all users.
- Only root can write or modify files here.

The supervisor should run:

```sh
./supervisor/init_permissions.sh
```

after cloning or updating the repo to enforce those permissions.

## Process B

Process B runs as the requesting Linux user inside that user's private request directory.
It should behave like an advertising art director, not a literal prompt relay. Many
clients will provide incomplete direction, so Process B expands weak briefs into a
concrete commercial image concept before calling the image model.

Process B flavor-specific instructions live in:

```text
brain/process_b/
```

Before running a model, choose the flavor that matches the request shape.

For this static-image-to-video MVP, Process B is represented by:

```sh
./brain/nano_banana.py --request request.json
```

An alternative scripted voiceover Process B is represented by:

```sh
./brain/elevenlabs_tts.py --script script.md --character-dir /srv/ugc-pipeline/characters/astrid
```

The full Astrid scripted avatar flavor is represented by:

```sh
./brain/astrid_avatar.py --request request.json --character-dir /srv/ugc-pipeline/characters/astrid
```

The script reads `request.json` from the caller's current working directory,
normalizes the prompt into an advertising-grade generation brief, calls fal.ai
Nano Banana for static image generation only, then renders a silent short-form
MP4 locally with ffmpeg effects. All outputs are written into that same request
directory.

Process B accepts either:

- A text prompt.
- One or more product/reference images plus a short prompt.
- One or more product/reference images with an empty or minimal prompt.

The scripted voiceover variant accepts a script text file and a reusable character
reference. Character references live under
`/srv/ugc-pipeline/characters/<character_id>/`; the first character is `astrid`,
backed by `/srv/ugc-pipeline/characters/astrid/reference.png`.

The Astrid scripted avatar variant accepts a client request shaped like
`Use Astrid and let her say: "...script..."`. It extracts the script to
`script.md`, generates `output_audio/voiceover.mp3` with ElevenLabs, validates
that the MP3 is less than 60 seconds, uses Kling AI Avatar v2 Standard to create
the talking-head video, runs Whisper base for timestamps, and burns social-style
subtitles into `output_videos/final_subtitled.mp4`.

When the client does not know exactly what they want, Process B should infer a
commercially useful presentation from the available product image and prompt. It
must preserve explicit client constraints, but otherwise it should make strong
advertising choices about setting, lighting, framing, product visibility, model
presence, and buyer context.

Text-only requests use:

```text
fal-ai/nano-banana-2
```

Requests with one or more image inputs use:

```text
fal-ai/nano-banana-2/edit
```

Prompt strategy:

- `human_usage`: Used when the prompt asks for people, holding, wearing, usage,
  lifestyle, UGC, or when a product image is supplied with only a vague brief.
  The effective prompt asks for a photorealistic adult person naturally using,
  holding, wearing, or presenting the product in a believable advertising scene.
- `product_only`: Used when the prompt asks for no people, product-only,
  packshot, flat lay, or still life. The effective prompt asks for an elevated
  product scene with no human, body parts, face, or mannequin, using aesthetic
  surroundings that make semantic sense for the product category.
- `general_advertising`: Used for clearer text-only or mixed requests that do
  not force either mode. The effective prompt asks the model to infer the
  strongest commercial concept while preserving the client's stated direction.

For all strategies, Process B should push toward photorealistic presentation
quality: premium lighting, natural shadows, sharp product detail, credible scale,
clean composition, visible product identity, and enough negative space for ad
copy when appropriate. It should avoid distorted packaging, misspelled visible
text, extra logos, watermarks, clutter, gimmicky effects, uncanny anatomy, and
unrealistic product interaction.

Process B always produces a silent `9:16` short video after the static images are
downloaded. Nano Banana remains the only AI generation cost and is capped at four
static images per clip. Video motion is created locally with ffmpeg: Ken Burns
zoom/pan movement, fast punch zooms, pullbacks, whip pans, short visual beats,
kinetic hook/payoff/CTA text, contrast and saturation polish, vignette, and
lightweight impact overlays. Audio is ignored.

Marketing text must not be baked into Nano Banana images. The image prompt should
ask for clean photorealistic visuals with no captions, slogans, hook text, CTA
text, banners, stickers, or UI. Only real text printed on the physical product or
packaging should be preserved. All ad copy belongs in the ffmpeg overlay stage,
using high-contrast bold white text with black outline/shadow and pop-in motion
so zooms and pans do not distort the message.

Process B writes:

- `fal_result.json`
- `output_images/`
- `output_videos/final.mp4`
- `video_plan.json`
- `learning.md`
- `status.json`

`fal_result.json` records both the original client `prompt` and the generated
`effective_prompt`, plus the selected `prompt_strategy`, static image count, and
video output metadata. `video_plan.json` records the local edit decisions and
effect list. `learning.md` also includes the effective prompt so Process C and
human operators can evaluate whether the advertising interpretation and video
edit were appropriate.

Because all output paths are based on the caller's directory, the supervisor must set the working directory to the specific request folder before dropping privileges.

Provider API keys are not stored in the client folder. The fal key and ElevenLabs key live in `/etc/ugc-pipeline/fal.env` as `FAL_KEY` and `ELEVENLABS_API_KEY`; the supervisor injects only the credentials required by the dropped Process B environment.

## Process C

Process C evaluates the result of Process B and reflects on the request-specific learning.

For this prototype, Process C should inspect:

- `request.txt`
- `request.json`
- `request_check.json`
- `fal_result.json`
- `output_images/`
- `output_videos/`
- `video_plan.json`
- `learning.md`
- `status.json`

It should answer:

- Did Process B satisfy the user request?
- Was the API response valid and relevant?
- Did Process A reject obvious nudity before execution?
- Do the generated or edited images and final silent video avoid disallowed nudity?
- Is the video readable, silent, and based on no more than four Nano Banana images?
- Was the output written only inside the user's private request folder?
- Does `learning.md` contain a useful general observation?
- Is there anything that should be escalated to a human operator?

Process C may append to `learning.md`, but it must not modify the shared brain folder.

The MVP Process C implementation is:

```sh
./brain/evaluate_image.py <request-dir>
```

It writes `evaluation.md` and performs structural checks: expected files exist,
Process A accepted the request, Process B succeeded, at least one local output
image exists, the silent MP4 exists, and the four-image Nano Banana cap was
respected. Human visual review is still required for prompt match, text overlay
readability, motion quality, and nudity verification.
