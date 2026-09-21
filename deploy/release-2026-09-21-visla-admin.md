# 发布文档：WebUI 管理员入口控制 + 会话 userId 记录（2026-09-21）

> 变更范围：`feat` 功能发布，涉及网关后端 + WebUI 前端 + 部署配置。
> 风险等级：低（全部向前兼容，不配置新选项 = 行为不变）。

---

## 1. 变更概览

| 功能 | 说明 |
|---|---|
| **管理员入口控制** | 新配置 `channels.websocket.vislaAdminUsers`（用户名/邮箱，大小写不敏感）。配置后，名单外的 Visla SSO 用户在 WebUI 中看不到 Settings / Skills / Apps / Automations / Channels 五个入口（快捷键、URL 直达一并拦截）；本机密码登录、可信代理等非 SSO 路径不受影响。名单为空（默认）= 不启用。 |
| **会话归属记录** | 每个会话（Topic）首条消息时把创建者的 Visla user id 持久化到 session metadata（`visla_user_id`，首写者保留），会话列表 API 新增返回 `user_id` 字段。 |

**为什么需要**：多用户共享同一网关时，Settings 暴露供应商密钥、渠道凭据等服务端配置，需要限制到指定管理员；会话归属是后续按用户隔离/审计的基础数据。

## 2. 代码变更清单（13 文件 +261/−106，新增 2 个测试文件）

**后端（Python）**

| 文件 | 变更 |
|---|---|
| `nanobot/channels/websocket/runtime.py` | `WebSocketConfig` 新增 `visla_admin_users` 字段（strip/lower/去重校验） |
| `nanobot/webui/visla_auth.py` | `VislaTokenStore` 新增 profile 存储（SSO 交换时记 user_name/email，TTL 24h，进程内存） |
| `nanobot/webui/ws_http.py` | bootstrap 响应新增 `is_admin: bool`；`_is_visla_admin()` 判断逻辑 |
| `nanobot/webui/gateway_endpoint.py` | 新增 `visla_user_id_for(connection)` |
| `nanobot/webui/metadata.py` | 新增 `SESSION_OWNER_METADATA_KEY = "visla_user_id"` |
| `nanobot/webui/inbound_commands.py` | 首条消息后 `record_session_owner()` 写 session metadata（best-effort，不阻断消息流） |
| `nanobot/webui/session_list_index.py` | 会话列表索引 v8→v9，行新增 `user_id` 字段并导出 |

**前端（TypeScript，随镜像构建打包）**

| 文件 | 变更 |
|---|---|
| `webui/src/lib/types.ts` | `BootstrapResponse.is_admin`、`ChatSummary.user_id` |
| `webui/src/App.tsx` | `isAdmin` 状态；非 admin 时五个视图入口禁用 + 路由重定向回 chat + 模型设置按钮禁用 |
| `webui/src/components/Sidebar.tsx` | 非 admin 隐藏五个侧边栏入口 |

**部署/文档**

| 文件 | 变更 |
|---|---|
| `deploy/config.template.json` | websocket 段新增 `vislaAdminUsers`（porter.liu / perlin.gan / allan.song 三邮箱） |
| `deploy/README.md`、`docs/visla-auth.md` | 配置说明同步 |

**新增测试**：`tests/webui/test_visla_admin.py`（6 用例）、`tests/webui/test_session_owner.py`（4 用例）。

## 3. 已完成的验证

- pytest（webui / gateway / websocket 全量）：**885 passed, 1 skipped**
- ruff + basedpyright（改动文件，strict）：**全部通过 / 0 errors**
- Docker 构建（`tsc -p tsconfig.build.json && vite build`）：**通过**
- 容器内 vitest（bootstrap / settings-sidebar / thread-shell / sidebar-highlight）：**104 passed**
- 兼容性：旧 config 缺新字段走默认值；旧前端忽略 `is_admin`；索引 v8 缓存自动重建；存量会话 `user_id: null`

## 4. 发布步骤（prod）

**前置确认**：代码在本地 main 工作区（未提交），prod 数据目录为 `~/.nanobot-prod`。

```bash
# ① 提交代码（分支/commit 规范按仓库 AGENTS.md：feat[{issueId}]: ...，push 前先向负责人确认）
cd /Users/ganpanlin/IdeaProjects/visla-nanobot
git checkout -b feat/visla-admin-and-session-owner   # 或按 API-{issue} 分支规范
git add -A
git commit -m "feat: visla admin users gate webui admin surface and record session owner"

# ② 合并到发布分支并构建新镜像（前端 dist 会打进镜像，无需单独构建）
docker build -t <镜像仓库>/nanobot:<新tag> .
docker push <镜像仓库>/nanobot:<新tag>

# ③ 更新 prod 配置（deploy init 不会覆盖已有 config.json，需手工合并）
#    编辑 ~/.nanobot-prod/config.json，在 channels.websocket 下加入：
#    "vislaAdminUsers": ["porter.liu@visla.us", "perlin.gan@visla.us", "allan.song@visla.us"]

# ④ 发布重启（二选一，按现有部署模式）
deploy/run-docker.sh prod stop && deploy/run-docker.sh prod start   # Docker 模式
# 或
deploy/run-local.sh prod restart                                    # 本地模式

# ⑤ 健康检查
deploy/run-docker.sh prod health    # 或 run-local.sh prod health
```

## 5. 发布后验证清单

| # | 检查项 | 方法 | 预期 |
|---|---|---|---|
| 1 | 网关健康 | `deploy/run-local.sh prod health` | healthy |
| 2 | admin 用户登录 | 用 perlin.gan 的 Visla token 登录 WebUI | 侧边栏五个入口正常可见 |
| 3 | 普通用户被限制 | 用名单外账号登录 | Settings/Skills/Apps/Automations/Channels 全部隐藏；URL 直达 `#/settings` 被弹回 chat |
| 4 | bootstrap 标记 | 浏览器 DevTools → `/webui/bootstrap` 响应 | admin 账号 `is_admin: true`，普通账号 `false` |
| 5 | 会话 userId | 发一条新消息后调 `GET /api/sessions`（带 api_token） | 对应会话 `user_id` 为该用户 Visla id；存量旧会话为 `null`（正常） |
| 6 | 会话列表索引重建 | 观察 `~/.nanobot-prod/sessions/.webui_session_index.json` | `"version": 9` |

## 6. 回滚方案

```bash
# 换回旧镜像 + 重启即可
deploy/run-docker.sh prod stop
# （compose/脚本中指回旧 tag）后 start
```

回滚安全性（均已验证）：
- 旧代码忽略 `vislaAdminUsers` 配置字段（未知字段不报错）；
- 旧代码读到 v9 索引缓存 → 版本不匹配 → 自动按 v8 重建；
- session metadata 里的 `visla_user_id` 对旧代码只是多余字段，原样透传；
- 旧前端忽略 `is_admin` / `user_id` 字段。
- **无需**先清理配置即可回滚；建议回滚期间保留 `vislaAdminUsers` 配置，再次发新版时直接生效。

## 7. 已知边界与注意事项

1. **配置需重启生效**：`vislaAdminUsers` 在网关启动时读取；WebUI settings 里改配置也需重启。
2. **存量会话归属**：只在部署后**下一次发消息**时补记 `user_id`（归属 = 当时发消息的用户）；不再使用的旧会话保持 `null`，无回填手段（历史消息无用户信息）。
3. **重启瞬断**：内存态（已签发 token、Visla JWT 绑定）随重启丢失，浏览器会自动重新 SSO，用户最多闪断数秒，无需通知。
4. **浏览器缓存**：前端资源随新镜像下发，个别用户需强制刷新（Cmd+Shift+R）才能拿到隐藏入口的新界面。
5. **匹配规则**：`vislaAdminUsers` 按 Visla SSO 返回的 `userName` **或** `email` 匹配（大小写不敏感）；当前配置的是三个邮箱。
6. **多副本限制**：本功能不改变单实例架构约束（鉴权态在进程内存），仍不可直接横向扩副本。
