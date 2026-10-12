# Hermes HTTP Gateway 接口与 Hermes 参数对照

## 总览

`POST /v1/chat` 和 `POST /admin/v1/chat` 会执行 Hermes 对话命令。业务聊天在调用前还会解析 Private Skill 目录；管理员用户查询或绑定也会在缓存失效时执行一次 Hermes skill-list 命令。

## 1. 会调用 Hermes 的接口

### `POST /v1/chat`

作用：
新建会话，或在请求里带 `session_id` 时续聊。

网关请求字段到 Hermes 的映射：

| 网关字段 | Hermes 侧 | 说明 |
|---|---|---|
| `question.text` | `prompt` | 本轮输入文本 |
| `user_id` | 网关用户隔离 | 新会话不传时默认为 `guest`；已有 `session_id` 不传时继承原会话用户 |
| `session_id` | 网关内部会话映射 | 不直接传给 Hermes，用来找对应的 `hermes_session_id` 和原会话信息 |
| `question.attachments[].type=image` | 重复的 `--image <local_image_path>` + `--attachment-root <session_attachment_dir>` | 网关先下载公网图片 URL 到 `/tmp/hermes/<YYYY-MM-DD>/<session_id>/`，再按顺序把本地路径传给 Hermes；prompt 只追加自然语言图片提示，不注入图片 `@file:` |
| `question.attachments[].type=file` | prompt 里的 `@file:<local_file_path>` + `--attachment-root <session_attachment_dir>` | 网关先下载公网文件 URL 到 `/tmp/hermes/<YYYY-MM-DD>/<session_id>/`，再把本地路径以 `@file:` 引用追加到 prompt |
| Gateway 固定策略 | `-p default` | 所有业务请求统一使用 Hermes `default` profile |
| `default_model` | `-m` | 服务端配置的默认模型，不允许外部请求覆盖 |
| `default_provider` | `--provider` | 服务端配置的 provider，不允许外部请求覆盖 |
| 用户已绑定且启用的 private skills | 重复的 `-s <skill_id>` | 绑定来自 SQLite；仅在当前 Hermes 私有目录仍启用时传入 |
| 固定报告交付说明 | 本轮 CLI prompt 的 `[HTTP Gateway report delivery]` 前缀 | 纯文本、附件首轮和续聊均注入；报告由 Skill 写入本地、由 Gateway 生成报告页面链接 |
| `HERMES_TIMEOUT_SECONDS` | Hermes 子进程 `communicate(timeout=...)` | 当前部署为 1200 秒；未配置时代码默认 300 秒 |

实际行为：

- 首次纯文本会话：`hermes -p default [-m model] [--provider provider] [-s skill]... -z <prompt> --usage-file <usage_json_path>`
- 首次图片或文件会话：`hermes -p default [...] chat -Q -q <prompt> --usage-file <usage_json_path> --attachment-root <session_attachment_dir> --image <local_image_path>...`
- 已有 Hermes 会话：`hermes -p default [...] chat --resume <hermes_session_id> -Q -q <prompt> --usage-file <usage_json_path>`
- 已有 Hermes 会话带附件：按需要追加 `--attachment-root` 和多个 `--image` 参数。

业务聊天会先运行以下目录查询，并在网关进程内缓存 45 秒：

```bash
hermes -p default skills list --scope private --json
```

网关将用户绑定与该目录中的启用项求交集，得到要传给 Hermes 的 `-s` 参数。目录查询失败不会阻止聊天；本轮不传 private skill，管理员审计中会记录“解析私有 Skills”失败原因。

### `POST /admin/v1/chat`

管理员在线对话页使用的 multipart 上传入口。它要求有效的管理员 Cookie 和页面下发的 CSRF token，不接受也不暴露 `GATEWAY_API_KEY`。

- 浏览器本地上传的图片和文件先落盘到与 `session_id` 对应的附件目录。
- 图片、文件随后分别转换为与 `/v1/chat` URL 附件相同的 `DownloadedImage`、`DownloadedFile`。
- 两个入口共用用户绑定解析、会话恢复、prompt 构造、Hermes CLI 调用、消息记录和响应结构；区别仅在于附件来源。

### 请求阶段审计

每次聊天请求都会在网关数据库中记录可由网关精确观测的阶段耗时：等待前序附件下载、附件下载、等待会话锁、Hermes CLI 运行和保存结果。管理员会话详情的请求时间线可展开查看这些阶段。

`Hermes CLI 运行` 当前包含模型调用、MCP、工具执行和最终生成；Hermes CLI 尚未向网关输出可关联的结构化内部 trace，因此这些内部子阶段不会被伪造为独立耗时。

## 2. 不执行 Hermes 对话的接口

以下接口不会执行 Hermes 对话命令：

- `GET /health`
- `GET /v1/sessions`
- `GET /v1/messages`
- `GET /v1/capabilities?scope=private`
- `GET /v1/users/{user_id}/private-skills`
- `PUT /v1/users/{user_id}/private-skills`
- `GET /v1/reports/{filename}/{download_token}`
- `GET /admin/login`
- `POST /admin/login`
- `GET /admin/logout`
- `GET /admin`
- `GET /admin/chat`
- `GET /admin/v1/dashboard`
- `GET /admin/v1/capabilities?scope=private` (may refresh the cached private Skill directory)
- `GET /admin/v1/users/{user_id}`
- `PATCH /admin/v1/users/{user_id}` (may refresh the cached private Skill directory for validation)
- `GET /admin/v1/conversations`
- `GET /admin/v1/conversations/{session_id}/timeline`
- `GET /admin/v1/search`

`GET /admin/v1/capabilities?scope=private` runs the skill-list command only when its 45-second process-local cache is absent or expired. `PATCH /admin/v1/users/{user_id}` may also refresh that cache for validation. `GET /admin/v1/users/{user_id}` only reads the user and binding records.

### Skill execution audit

The gateway passes each Hermes invocation a unique `HERMES_GATEWAY_SKILL_AUDIT_FILE` path. Hermes writes compact JSON or JSONL Skill events to this file; the gateway stores the normalized result in `request_events.runtime_audit.skill_audit`. Each record contains a Skill id and a status such as `succeeded`, `failed`, `timeout`, `unavailable`, `skipped`, or `unknown`, with optional duration and a short reason. Missing telemetry is recorded as `unknown`; a successful Hermes response is never treated as proof that every Skill succeeded. The admin conversation timeline displays these statuses.

## 3. 网关侧关键映射

### 会话映射

网关维护自己的 `external_session_id -> hermes_session_id` 映射：

- `external_session_id`：外部传入或网关生成
- `hermes_session_id`：Hermes 首次返回后写入数据库

续聊时，网关只会把新问题发给 Hermes，不会重复重发完整历史。

### 用户隔离

`user_id` 只在网关侧生效：

- 用来区分不同用户
- 用来查询对应会话和消息
- 不直接传给 Hermes
- `POST` 对话接口从 JSON body 读取 `user_id`
- `GET` 查询接口从 query 参数读取 `user_id`
- `POST /v1/chat` 不传 `user_id` 时缺省值是 `guest`；如果命中已有 `session_id`，则继承原会话保存的用户

### 历史消息

网关会把消息落到 SQLite：

- `conversations`：会话元信息
- `messages`：每轮用户和助手消息

这些历史主要用于后台展示、搜索和审计，不是每次都回灌给 Hermes。

### 生成的 HTML 报告

共用聊天逻辑在 Hermes 成功返回后、保存助手消息前，将允许源目录中的单个 HTML 路径替换为 HTTPS 报告页面链接。Skill 负责生成并验证本地文件，网关保存独立副本和过期时间。报告接口仅访问网关副本，不执行 Hermes。

网关每次调用在 CLI prompt 中明确报告交付渠道；报告 Skill 遇到 `[HTTP Gateway report delivery]` 时跳过 StaticSpaces、发布脚本和发布凭据检查。其他渠道仍使用 Skill 原有的在线发布流程。Hermes `-Q` 模式下关闭文件预览回调，避免 `review diff` 与报告 HTML 源码进入 stdout；交互模式继续显示文件 diff。

报告与随机访问凭证默认保留 7 天；无效或过期链接返回 `404`。通过 `REPORT_SOURCE_ROOT`、`REPORT_STORAGE_ROOT`、`REPORT_PUBLIC_BASE_URL`、`REPORT_RETENTION_DAYS` 和 `REPORT_MAX_BYTES` 配置。`REPORT_PUBLIC_BASE_URL` 包含完整代理前缀，避免拼接重复路径。
