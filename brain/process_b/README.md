# Process B Flavors

Process B should choose a flavor from the request shape instead of treating all
requests as image generation.

## Flavor Selection

- Use `nano_banana_static_video.md` for the `still_images` option: product/reference
  image generation or image-edit requests that need static images rendered into a
  silent short video.
- Use `astrid_scripted_avatar.md` for the `avatar_voice` option: a character,
  currently Astrid, should appear as a talking avatar and say a supplied script,
  for example: `Use Astrid and let her say: "...script..."`.
- Use `voice_over.md` for the `voice_over` option: a character is referenced, the
  request supplies stage direction plus exact voiceover text, and the output is a
  staged visual sequence with ElevenLabs audio, stitched Kling image-to-video
  segments, and baked subtitles.
- Use `motion_control.md` for the `motion_control` option: a video input provides
  the exact movement, a character reference supplies the generated character, and
  optional background/outfit direction may first edit that reference image.
- Use `total_control.md` for the `total_control` option: a video input provides
  the source voice, body motion, lip movement, timing, and camera motion; the
  second frame first seeds a Nano Banana character pose reference, the source
  voice is converted with ElevenLabs, then the pose reference and dubbed driver
  video are sent through Kling motion control and subtitled.
- Use `slide_show.md` for the `slide_show` option: a local folder provides
  `script.txt` plus numbered images, and Process B creates an ElevenLabs narrated
  9:16 slideshow with line-timed baked subtitles in the top black band.

If a request includes both a character speaking task and product/image generation,
split the work into explicit stages and keep each stage's artifacts named in the
request folder.
