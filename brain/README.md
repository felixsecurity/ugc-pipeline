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

For this image-generation MVP, Process B is represented by:

```sh
./brain/nano_banana.py --request request.json
```

The script reads `request.json` from the caller's current working directory, calls fal.ai Nano Banana, and writes all outputs into that same request directory.

Text-only requests use:

```text
fal-ai/nano-banana-2
```

Requests with one or more image inputs use:

```text
fal-ai/nano-banana-2/edit
```

Process B writes:

- `fal_result.json`
- `output_images/`
- `learning.md`
- `status.json`

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
