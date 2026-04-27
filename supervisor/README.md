# Supervisor

Root-only Process A scripts for the mini user-separation prototype.

This directory should be `700` and owned by `root:root`.

## Scripts

- `init_permissions.sh`
  - Enforces repo permissions.
  - Keeps `./brain` readable/executable by client users and writable only by root.
  - Keeps `./supervisor` root-only.

- `submit_request.sh <client-id> <pokemon-name>`
  - Creates the Linux user on first request.
  - Creates the private client folder under `/srv/ugc-clients/<client-id>`.
  - Copies the request into a private request folder.
  - Runs Process B as the client user.

- `run_codex_for_client.sh <client-id> <codex-args...>`
  - Runs `codex` with dropped permissions as the client user.
  - Copies root's existing Codex `auth.json` and `config.toml` into the client user's private `.codex` folder.

- `collect_learnings.sh [output-path]`
  - Reads all `learning.md` and `learnings.md` files under `/srv/ugc-clients`.
  - Writes an aggregate report, defaulting to `/srv/ugc-pipeline/all_learnings.md` so the aggregate can be version-controlled.

## Important Security Note

This prototype intentionally recycles the root Codex login into each client user's private home. That proves the mechanics, but it means any process running as that client user can read its copied Codex token. For production, prefer a narrow API proxy, scoped service tokens, or one managed service identity per tenant.

The root-owned Codex credential template is stored outside git at:

```text
/etc/ugc-pipeline/codex-template
```
