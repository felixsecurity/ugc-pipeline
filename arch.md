# Tenant Permission Scoping on Ubuntu 24

## Recommendation

Use a layered isolation model:

1. Store every client's data in a predictable host directory layout.
2. Run each client-scoped process in its own container.
3. Mount only that client's directory into the container.
4. Run the container as a non-root UID/GID dedicated to that client.
5. Use read-only mounts wherever a process only needs context.
6. Keep shared/global state, especially `Brain.md`, outside client containers and expose it only through a scrubbed update process.

Folder permissions are useful, but they should be treated as a second line of defense, not the main tenant boundary. Docker or Podman containers with strict bind mounts are the right primitive for Process B and Process C because those processes are explicitly expected to be unable to escape a client's scope.

## Suggested Host Layout

```text
/srv/ugc-pipeline/
  clients/
    client_brutus/
      requests/
        request_000005/
          inputs/
          work/
          outputs/
          script.md
          prompts.json
          learning.md
          human.md
      client_context/
  brain/
    Brain.md
  intake/
  quarantine/
```

`intake/` receives new external uploads first. Process A validates and sanitizes files there, then copies accepted inputs into the target client's `requests/.../inputs/` directory.

`brain/Brain.md` should not be mounted into client containers. Process D should read `learning.md` and `human.md` through a scrubber that removes PII and concrete project identifiers before any update to `Brain.md`.

## Linux Users and Permissions

Create one Linux group per client and one service user per client:

```sh
groupadd ugc-client-brutus
useradd --system --no-create-home --gid ugc-client-brutus ugc-client-brutus
```

Client directories should be owned by that user/group and inaccessible to others:

```sh
chown -R ugc-client-brutus:ugc-client-brutus /srv/ugc-pipeline/clients/client_brutus
chmod -R u+rwX,g-rwx,o-rwx /srv/ugc-pipeline/clients/client_brutus
```

This prevents accidental cross-client reads from host-side scripts. It does not fully contain a compromised process by itself, especially if the process runs as a privileged user, has broad filesystem access, or can reach secrets through environment variables.

## Container Boundary

For Process B and Process C, run one container per client/request execution. The container should get exactly the mounts it needs:

```sh
docker run --rm \
  --name ugc-client-brutus-request-000005-process-b \
  --user "$(id -u ugc-client-brutus):$(id -g ugc-client-brutus)" \
  --read-only \
  --cap-drop=ALL \
  --security-opt no-new-privileges \
  --pids-limit=256 \
  --memory=4g \
  --cpus=2 \
  --network=none \
  --mount type=bind,src=/srv/ugc-pipeline/clients/client_brutus,dst=/workspace/client \
  ugc-process-b:latest
```

Use `--network=none` for steps that do not need outbound API access. For steps that call fal.ai, ElevenLabs, or LLM APIs, allow egress deliberately and inject only the required credentials. Prefer short-lived tokens or a small host-side API proxy over mounting broad `.env` files into every container.

For request-specific work, prefer mounting only the request directory plus a read-only client context directory:

```sh
--mount type=bind,src=/srv/ugc-pipeline/clients/client_brutus/requests/request_000005,dst=/workspace/request \
--mount type=bind,src=/srv/ugc-pipeline/clients/client_brutus/client_context,dst=/workspace/client_context,readonly
```

This gives Process B and Process C access to the same client's history/context without exposing other clients.

## Docker vs Podman

Docker is acceptable if operational simplicity matters most, but on Ubuntu 24, rootless Podman is a strong fit for this design:

- It reduces the risk of a container escape becoming host root.
- It integrates cleanly with systemd user services.
- It supports the same bind-mount based scoping model.

If the team already operates Docker well, use Docker with non-root containers, dropped capabilities, AppArmor, read-only root filesystems, and narrow mounts. If starting fresh, prefer rootless Podman.

## AppArmor and Seccomp

Ubuntu 24 ships with AppArmor support. Keep the default container AppArmor/seccomp profiles enabled, and add stricter profiles later for production. The first production hardening pass should block unnecessary filesystem paths, ptrace, raw sockets, privilege escalation, and unexpected executable locations.

This is especially important for scripts generated or selected by LLM agents.

## Process-Specific Scoping

### Process A: Intake

Process A should run outside the client container boundary because it routes incoming material. It should have write access to `intake/`, `quarantine/`, and the destination client's `inputs/` folder only after routing is decided.

Process A should never run generated scripts. It should do validation, malware/content checks, metadata extraction, normalization, and copying.

### Process B: Generation

Process B should run inside a client/request container. It needs write access to the request workspace and outputs, read access to same-client context, and controlled API access.

It should not see:

- Other clients' folders.
- Host SSH keys.
- Git credentials.
- `Brain.md`.
- The raw global database, if one exists.

### Process C: Evaluation

Process C should run in a separate container from Process B. It can mount Process B outputs and same-client context. If possible, mount Process B artifacts read-only and give Process C a separate writable evaluation directory.

This separation makes evaluation less likely to be contaminated by Process B's runtime state.

### Process D: Sleep / Brain Update

Process D should not run in a client container. It is a global process with access to `Brain.md`, so it needs stricter controls:

1. Read candidate `learning.md` and `human.md` files.
2. Scrub PII and concrete client/project references.
3. Produce a proposed patch to `Brain.md`.
4. Optionally require human approval before applying it.

Process D should write generalized lessons only. It should not preserve client names, raw prompts containing private data, exact file names, faces, voices, brands, or project identifiers.

## Why Not Folder Permissions Only?

Folder permissions are necessary but insufficient. They help prevent accidental leakage from normal host processes, but they do not provide a strong execution sandbox for Process B/C if the process can run arbitrary tools, exploit a dependency, access inherited credentials, or traverse broadly mounted paths.

Use folder permissions for host-side hygiene. Use containers for the actual execution boundary.

## Practical First Version

The first production-like version should implement:

- `/srv/ugc-pipeline/clients/<client_id>/requests/<request_id>/...`
- One Linux user/group per client.
- One Process B container image and one Process C container image.
- Per-run bind mounts limited to that client/request.
- Non-root container users.
- `--cap-drop=ALL`, `--security-opt no-new-privileges`, default AppArmor/seccomp, memory/CPU/PID limits.
- No global `Brain.md` mount inside Process B or C.
- A separate scrubbed Process D workflow for `Brain.md` updates.

This gives a clean path from the README's folder-based mental model to a real tenant boundary on Ubuntu 24.
