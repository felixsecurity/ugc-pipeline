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

For this image-generation MVP, Process B is represented by:

```sh
./brain/nano_banana.py --request request.json
```

The script reads `request.json` from the caller's current working directory,
normalizes the prompt into an advertising-grade generation brief, calls fal.ai
Nano Banana, and writes all outputs into that same request directory.

Process B accepts either:

- A text prompt.
- One or more product/reference images plus a short prompt.
- One or more product/reference images with an empty or minimal prompt.

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

Process B writes:

- `fal_result.json`
- `output_images/`
- `learning.md`
- `status.json`

`fal_result.json` records both the original client `prompt` and the generated
`effective_prompt`, plus the selected `prompt_strategy`. `learning.md` also
includes the effective prompt so Process C and human operators can evaluate
whether the advertising interpretation was appropriate.

Because all output paths are based on the caller's directory, the supervisor must set the working directory to the specific request folder before dropping privileges.

The fal API key is not stored in the client folder. The supervisor reads it from `/etc/ugc-pipeline/fal.env` and injects it into the dropped Process B environment as `FAL_KEY`.

## Process C

Process C evaluates the result of Process B and reflects on the request-specific learning.

For this prototype, Process C should inspect:

- `request.txt`
- `request.json`
- `request_check.json`
- `fal_result.json`
- `output_images/`
- `learning.md`
- `status.json`

It should answer:

- Did Process B satisfy the user request?
- Was the API response valid and relevant?
- Did Process A reject obvious nudity before execution?
- Does the generated or edited image avoid disallowed nudity?
- Was the output written only inside the user's private request folder?
- Does `learning.md` contain a useful general observation?
- Is there anything that should be escalated to a human operator?

Process C may append to `learning.md`, but it must not modify the shared brain folder.

The MVP Process C implementation is:

```sh
./brain/evaluate_image.py <request-dir>
```

It writes `evaluation.md` and performs structural checks: expected files exist, Process A accepted the request, Process B succeeded, and at least one local output image exists. Human visual review is still required for prompt match and nudity verification.
