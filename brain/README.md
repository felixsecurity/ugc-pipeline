# Brain

This folder is the shared, root-controlled process library for the mini UGC pipeline prototype.

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

For this prototype, Process B is represented by:

```sh
./brain/get_pokemon.py <pokemon-name>
```

The script calls `https://pokeapi.co/`, reads Pokemon data, and writes the result into the caller's current working directory:

```text
poke_return.json
```

Because the output path is based on the caller's directory, the supervisor must set the working directory to the specific request folder before dropping privileges.

## Process C

Process C evaluates the result of Process B and reflects on the request-specific learning.

For this prototype, Process C should inspect:

- `request.txt`
- `poke_return.json`
- `learning.md`

It should answer:

- Did Process B satisfy the user request?
- Was the API response valid and relevant?
- Was the output written only inside the user's private request folder?
- Does `learning.md` contain a useful general observation?
- Is there anything that should be escalated to a human operator?

Process C may append to `learning.md`, but it must not modify the shared brain folder.
