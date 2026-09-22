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

## 3. Kubernetes (Helm) mode (`deploy.sh` + `helm-chart/`)

EKS 部署。与 visla-api 同模式：**所有机密存集群 Secret `visla-main`，chart 只引用不创建；非机密配置在区域 values（git）**。

### 环境对照

| | dev | prod |
| --- | --- | --- |
| 区域 values | `values-cn-northwest-1-dev-01.yaml` | `values-us-west-2-prod-01.yaml` |
| kubectl context | `eks_cn-northwest-1-dev-01` | `eks_us-west-2-prod-01` |
| 域名 | `nanobot.dev01.vislaus.cn` | `nanobot.visla.us` |
| ECR | `665444435332.dkr.ecr.cn-northwest-1.../nanobot` | `820814109514.dkr.ecr.us-west-2.../nanobot` |
| 部署命令 | `./deploy.sh cn-northwest-1 dev-01` | `./deploy.sh us-west-2 prod-01` |

### 配置项在哪配（三类）

**① 集群 Secret `visla-main`（机密，git 不可见，运维一次性 patch）**

| Secret key | 必须? | 格式 | 用途 |
| --- | --- | --- | --- |
| `nanobot-web-token` | ✅ 必须 | 任意高熵字符串，推荐 `openssl rand -hex 32`；md5 值也可（输入须真随机） | WebUI 备用密码（`tokenIssueSecret`） |
| `custom-model-api-key` | 自定义网关时必须 | 网关签发的 key | `providers.<providerName>.apiKey` |
| `openai-api-key` | 直连 OpenAI 时 | `sk-...` | `providers.openai.apiKey` |
| `anthropic-api-key` | 用 Anthropic 时 | `sk-ant-...` | `providers.anthropic.apiKey` |

```bash
# 一次性注入（每个集群独立执行；dev/prod 的 token 必须不同值）
kubectl --context eks_us-west-2-prod-01 patch secret visla-main -p '{
  "stringData": {
    "nanobot-web-token": "<openssl rand -hex 32>",
    "custom-model-api-key": "<网关 key>"
  }}'
```

**② 区域 values（git，非机密）**

| 字段 | dev/prod 当前值 |
| --- | --- |
| `customModel.providerName` | `myllm`（model 前缀，不能撞内置名 openai/anthropic/groq…） |
| `customModel.baseUrl` | `https://REPLACE-*-GATEWAY/v1` ⚠️ 待填实际网关地址 |
| `defaultModel` | `myllm/gpt-5.6-luna`（前缀必须 = providerName） |
| `providers.openai.baseUrl` | 默认 `https://api.openai.com/v1`（不用官方可不配） |
| `vislaAdminUsers` | 三邮箱（空数组 = 全员 admin） |
| `visla.currentUserUrl` / `visla.adminApiBaseUrl` | 各环境 admin-api 地址 |
| `host` / `resources` / `waf_acl_id` 等 | 基础设施配置 |

**③ chart 模板内固定（不需要配）**：Secret key 名（`custom-model-api-key` 等）、workspace 挂载路径、`render-config.json` 结构。

### 容器环境变量（chart 自动注入，无需手动配）

| 容器 env | 来源 | 解析到 config.json |
| --- | --- | --- |
| `NANOBOT_WEB_TOKEN` | secretKeyRef `nanobot-web-token` | `channels.websocket.tokenIssueSecret` |
| `CUSTOM_MODEL_API_KEY` | secretKeyRef `custom-model-api-key` | `providers.<name>.apiKey` |
| `OPENAI_API_KEY` | secretKeyRef `openai-api-key` | `providers.openai.apiKey` |
| `ANTHROPIC_API_KEY` | secretKeyRef `anthropic-api-key` | `providers.anthropic.apiKey` |
| `OPENAI_BASE_URL` | values `providers.openai.baseUrl` | `providers.openai.apiBase` |
| `VISLA_CURRENT_USER_URL` | values `visla.currentUserUrl` | `channels.websocket.vislaCurrentUserUrl` |
| `VISLA_ADMIN_API_BASE_URL` | values `visla.adminApiBaseUrl` | 运行时 Visla SSO 校验 |
| `RENDER` | 固定 `true` | 首启渲染 render-config.json → config.json |

config loader 每次启动把 `${VAR}` 占位符解析为上述 env 值。

### 模型配置两种方式

**方式一：OpenAI 官方**——values 只配 `defaultModel: openai/<model>`（+ 可选 `providers.openai.baseUrl`），Secret patch `openai-api-key`，`customModel` 整段不配。

**方式二：自定义网关（OpenAI 兼容，当前 dev/prod 均采用）**——

```yaml
customModel:
  providerName: myllm                    # 前缀
  baseUrl: https://你的网关/v1             # 须兼容 POST {baseUrl}/chat/completions
defaultModel: myllm/gpt-5.6-luna          # 前缀 = providerName
```

Secret patch `custom-model-api-key`。两种可同时配（都进 providers），`defaultModel` 前缀决定实际路由。

### 部署与模拟

```bash
# 本地渲染验证（无集群依赖）
helm template nanobot helm-chart/nanobot/ -f helm-chart/nanobot/values-us-west-2-prod-01.yaml --set-string image.tag=test

# 服务端模拟（真实 API 校验，不落库；需 configmap/pvc 读权限）
helm template ... | kubectl apply --dry-run=server --context eks_us-west-2-prod-01 -f -

# 正式部署（build 镜像 + push ECR + helm apply）
./deploy.sh us-west-2 prod-01
```

### 运维操作

- **轮换网关 key**：patch Secret `custom-model-api-key` → `kubectl rollout restart deployment nanobot`。无需 rebuild 镜像。
- **换默认模型**：改 values `defaultModel` → 重新部署。⚠️ 首次启动后 PVC 上已有 `config.json` 不会被模板覆盖：要么 `kubectl exec rm /home/nanobot/.nanobot/config.json` 后重启（以 values 为准），要么 WebUI settings 里改。
- **Skills**：WebUI marketplace 安装 → workspace PVC（5Gi 独立卷）持久化，pod 重启/镜像升级不丢；镜像本身不打包 skill。

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
      "vislaCurrentUserUrl": "${VISLA_CURRENT_USER_URL}",
      "vislaAdminUsers": [
        "porter.liu@visla.us",
        "perlin.gan@visla.us",
        "allan.song@visla.us"
      ]
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
