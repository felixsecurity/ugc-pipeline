# Process B Flavors

Process B should choose a flavor from the request shape instead of treating all
requests as image generation.

## Flavor Selection

- Use `nano_banana_static_video.md` for product/reference image generation or
  image-edit requests that need static images rendered into a silent short video.
- Use `astrid_scripted_avatar.md` when the client asks for Astrid to say a script,
  for example: `Use Astrid and let her say: "...script..."`.

If a request includes both a character speaking task and product/image generation,
split the work into explicit stages and keep each stage's artifacts named in the
request folder.
