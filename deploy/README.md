# Multi-Environment Deployment (dev / prod)

Two deployment modes share one principle: **all environment differences live in
`deploy/envs/<env>.env`** — Visla admin-api URLs, ports, data directories, and
container names. The generated `config.json` references
`${VISLA_CURRENT_USER_URL}`, which nanobot's config loader resolves from the
process environment at every startup (see `nanobot/config/loader.py`). A
missing variable fails the gateway at startup instead of silently validating
Visla SSO tokens against the wrong environment.

| | prod | dev |
| --- | --- | --- |
| admin-api base | `https://admin-api.prod01.visla.us` | `https://admin-api.dev01.vislaus.cn` |
| gateway health (loopback) | `127.0.0.1:18790` | `127.0.0.1:18791` |
| WebUI / WebSocket | `:8765` | `:8766` |
| OpenAI-compatible API (loopback) | `127.0.0.1:8900` | `127.0.0.1:8901` |
| data directory (config + runtime state) | `~/.nanobot-prod` | `~/.nanobot-dev` |
| docker containers | `nanobot-prod-*` | `nanobot-dev-*` |

The **workspace is shared**: both environments (and any plain local `nanobot`
install) use the standard `~/.nanobot/workspace` — skills, memory, cron jobs
and generated files live there once. Environment differences never leak into
the workspace; they reach skills only through env vars
(`VISLA_ADMIN_API_BASE_URL` etc.) resolved at runtime.

Both environments can run **on the same host simultaneously** (local mode,
docker mode, or a mix) — ports and data directories never collide. Sessions
are isolated per data dir; only the workspace contents (memory, cron) are
shared, so avoid running cron-heavy workloads on both gateways at once.

## 1. Local multi-instance mode (`run-local.sh`)

Wraps `nanobot gateway --background/--config` lifecycle management; no pidfile
handling of our own. Requires the `nanobot` CLI in PATH.

```bash
# one-time init: creates ~/.nanobot-<env>/config.json (never overwrites)
deploy/run-local.sh dev init
deploy/run-local.sh prod init

# start / stop / restart / status / logs / health
deploy/run-local.sh dev start
deploy/run-local.sh prod start
deploy/run-local.sh dev logs
deploy/run-local.sh dev health
deploy/run-local.sh prod stop
```

Open the WebUI at `http://127.0.0.1:8766` (dev) or `http://127.0.0.1:8765`
(prod) and log in with the Visla token issued by that environment.

## 2. Docker Compose mode (`run-docker.sh`)

Uses `deploy/docker-compose.yml` (parameterized; separate from the upstream
root `docker-compose.yml`). The rendered config.json stays on the host and is
bind-mounted into the containers, and Visla variables are injected via
`environment:`.

```bash
# one-time init: render ~/.nanobot-<env>/config.json on the host
deploy/run-docker.sh dev init
deploy/run-docker.sh prod init

# build + start gateway and API containers
deploy/run-docker.sh dev up
deploy/run-docker.sh prod up

deploy/run-docker.sh dev logs
deploy/run-docker.sh dev health
deploy/run-docker.sh dev status
deploy/run-docker.sh dev down

# one-off CLI inside the container
deploy/run-docker.sh dev shell status
deploy/run-docker.sh dev shell agent -m "hello"
```

Preinstall channel dependencies into the image with
`NANOBOT_CHANNELS=telegram,slack deploy/run-docker.sh dev build`.

## Config lifecycle

`init` renders `config.json` from `config.template.json` only when the file
does **not** exist (same policy as the Render entrypoint path). Runtime edits
made via the WebUI settings survive restarts and redeploys. To add a model
provider, either edit the config before first start or use the WebUI settings
after logging in.

If you manage `config.json` by hand, keep these multi-environment fields:

```json
{
  "channels": {
    "websocket": {
      "vislaAuthEnabled": true,
      "vislaCurrentUserUrl": "${VISLA_CURRENT_USER_URL}"
    }
  },
  "tools": {
    "exec": {
      "allowedEnvKeys": ["VISLA_ADMIN_API_BASE_URL", "VISLA_CURRENT_USER_URL"]
    }
  }
}
```

## Skills

nanobot discovers **workspace skills** under `<workspace>/skills/` — one
subdirectory with a `SKILL.md` per skill (see `nanobot/agent/skills.py`).
Both modes and **both environments** use the same shared directory, created
by `init`:

```
~/.nanobot/workspace/skills/<skill-name>/SKILL.md
```

- **local mode**: drop skill folders there (or `ln -s` an external repo); nanobot
  picks them up on the next gateway start
- **docker mode**: nothing extra to do — `deploy/docker-compose.yml` bind-mounts
  `NANOBOT_WORKSPACE` into the container, so the same skills are visible at
  `/home/nanobot/.nanobot/workspace/skills`

One skill works everywhere: reference `$VISLA_ADMIN_API_BASE_URL` (or
`$VISLA_CURRENT_USER_URL`) inside the skill and the value follows the
environment the gateway runs in — no per-environment skill copies.

## Skill access to environment variables

Skills run shell commands through the `exec` tool, which by default passes only
a minimal environment (`HOME`, `LANG`, `TERM`). Two kinds of variables reach
skills:

| Variable | Scope | How it arrives |
| --- | --- | --- |
| `VISLA_ADMIN_API_BASE_URL` / `VISLA_CURRENT_USER_URL` | deployment-wide (dev or prod domain) | whitelisted via `tools.exec.allowedEnvKeys` in the rendered config |
| `VISLA_TOKEN` | per-conversing-user (their live Visla JWT) | injected by the gateway on every turn after Visla SSO login — no config needed, and it overrides any static value |

Inside a skill, both compose naturally:

```bash
curl -s -H "token: $VISLA_TOKEN" "$VISLA_ADMIN_API_BASE_URL/api/my/current-user"
```

The same skill file works in every environment: the base URL follows the
deployment (env file → container environment) and the token follows the
logged-in user (SSO exchange → `VislaTokenStore` → exec injection). Notes:

- `VISLA_TOKEN` lives in memory only; a gateway restart clears it and users
  simply log in again (their JWT expiry is the real limit).
- Do **not** declare `VISLA_TOKEN` in a skill's `metadata.nanobot.requires.env`:
  availability checks read the gateway process environment, where this
  variable never exists — the skill would always show as unavailable.
  Declaring `VISLA_ADMIN_API_BASE_URL` there is fine (it is in the process env).
- To expose more deployment-level variables, extend `allowedEnvKeys` — it is a
deliberate allowlist (keeps API keys away from LLM-driven subprocesses).

## Adding a new environment

Copy `deploy/envs/dev.env`, adjust the URLs/ports/paths/prefix, and both
scripts pick it up automatically: `deploy/run-local.sh staging init`.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| gateway exits with `missing_env` | `VISLA_CURRENT_USER_URL` not in the process env | Start via `run-local.sh`/`run-docker.sh` (they export it); check `deploy/envs/<env>.env` |
| Visla login `502 Upstream Unavailable` | Gateway cannot reach the admin-api URL for that environment | Verify DNS/network from the host (local mode) or container (docker mode); check `HTTPS_PROXY` |
| Visla login `401` with a valid token | Token from the other environment, or `config.json` lost its `${VISLA_CURRENT_USER_URL}` reference | Compare the config against the template; `init` warns when the reference is missing |
| Port already in use | Another environment or process holds it | Port assignments are in `deploy/envs/<env>.env` |
