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
- Reserve `motion_control` for requests that provide video input and ask Process B
  to control or transform motion. This option is not specified yet.

If a request includes both a character speaking task and product/image generation,
split the work into explicit stages and keep each stage's artifacts named in the
request folder.
