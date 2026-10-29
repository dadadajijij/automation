# Hermes HTTP Gateway

一个放在 Hermes 外层的 HTTP 服务，用来做：

- 请求用户隔离
- 会话映射
- 外部 `session_id` 到 Hermes 内部 `session_id` 的持久化

## 结构

- 外部只调用这个服务
- Hermes 只放本机或内网
- 每个外部 `session_id` 对应一个 Hermes 会话
- 会话与消息都落盘到本地 SQLite
- Skills 绑定关系落盘到 SQLite，并按 `user_id` 生效

## 入口规则

- `user_id` 放在请求 body 或 query 里；不传时默认 `guest`
- 可选 `GATEWAY_API_KEY`
- 管理页使用单独的管理员登录
- 新请求没有 `session_id` 时，网关会自动创建一个
- 同一 `session_id` 会复用同一个 Hermes 会话
- 同一 `user_id + session_id` 的新请求会替代旧的未完成请求；如果旧请求正在下载附件，会先等附件下载完成并合并到新请求
- 同一会话的文件-only 请求在 2 秒窗口内会尝试继承最近被替代请求的明确问题文本，避免“问题请求”和“文件请求”分开发送时丢失用户意图
- 所有业务请求固定使用 Hermes `default` profile，调用端不传 profile
- 私有 skills 由管理员按 `user_id` 绑定；绑定后网关自动追加 Hermes `-s`

## 运行

```bash
cd /home/devops/ka/automation/agent/hermes-http-gateway
uv sync
```

编辑 `.env` 里的 `HERMES_BIN`，然后启动：

```bash
uv run hermes-http-gateway
```

启动时会自动读取同目录下的 `.env`。

也可以使用项目自带脚本后台运行：

```bash
bash scripts/hermes-gateway.sh start
bash scripts/hermes-gateway.sh status
bash scripts/hermes-gateway.sh restart
bash scripts/hermes-gateway.sh stop
```

脚本默认监听 `127.0.0.1:8003`，日志写入项目根目录 `.hermes-gateway.log`，PID 写入 `.hermes-gateway.pid`。脚本使用 Bash，请使用 `bash scripts/hermes-gateway.sh ...` 或直接执行脚本。

启停检查使用 Linux `/proc` 和 Python 标准库。脚本使用 `.hermes-gateway.pid.lock` 防止并发操作，校验 PID 的用户、项目工作目录和启动命令；PID 文件丢失时，仅对身份匹配且唯一占用目标端口的进程恢复 PID。无关进程占用端口、PID 指向无关进程或进程由服务管理器托管时会明确失败，不自动终止。端口检查保守地将任意监听地址上的同端口视为冲突。

只有新进程存活、独占监听目标端口且 `/health` 返回 HTTP 200 后才报告启动成功。启动失败返回非零状态并显示本次启动日志。停止使用 SIGTERM，超时保留 PID 并报错，不自动 SIGKILL；`restart` 在停止失败时不会继续启动。可用 `START_TIMEOUT`（默认 15 秒）和 `STOP_TIMEOUT`（默认 10 秒）调整等待时间。`status` 在未运行或未就绪时返回非零状态。

当前服务器使用：

```text
HOST=127.0.0.1
PORT=8003
PUBLIC_BASE_PATH=/tools2/hermes-gateway
HERMES_TIMEOUT_SECONDS=1200
```

`load_settings()` 在没有 `.env` 或环境变量时的代码默认端口是 `8011`、Hermes 超时是 `300` 秒；`scripts/hermes-gateway.sh` 的默认监听端口是 `8003`。部署时以实际 `.env` 或启动命令为准。

Hermes 二进制会优先自动寻找：

1. `$HERMES_WORKDIR/.venv/bin/hermes`
2. `$HERMES_WORKDIR/hermes`
3. `PATH` 里的 `hermes`

### 主要配置项

| 配置项 | 默认值 | 作用 |
|---|---:|---|
| `HOST` | `0.0.0.0` | Gateway 监听地址；脚本默认使用 `127.0.0.1` |
| `PORT` | `8011` | Gateway 监听端口；当前部署为 `8003` |
| `DATABASE_URL` | `./data/hermes_gateway.sqlite` | SQLite 数据库路径 |
| `HERMES_BIN` | 自动探测 | Hermes 可执行文件路径 |
| `HERMES_WORKDIR` | 相邻 `hermes-agent` 目录 | Hermes 工作目录 |
| `CATEGORY_NAMES_FILE` | `../../data/skills-manager/data/category_names.json` | `/v1/capabilities` 返回的 Skill 分类中英文映射文件；文件不可用时保留 Hermes 原始分类 |
| `SKILL_NAMES_FILE` | `../../data/skills-manager/data/skill_names.json` | `/v1/capabilities` 返回的 Skill 名称中英文映射文件；文件不可用或未匹配时保留 Hermes 原始名称 |
| `GATEWAY_API_KEY` | 空 | 配置后保护 `/v1/*` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / 空 | 管理员登录凭据 |
| `PUBLIC_BASE_PATH` | 空 | 反向代理子路径，例如 `/tools2/hermes-gateway` |
| `HERMES_TIMEOUT_SECONDS` | `300` | 单次 Hermes CLI 等待上限；当前部署为 `1200` |
| `DEFAULT_MODEL` | 空 | 服务端默认模型，不由调用方覆盖 |
| `DEFAULT_PROVIDER` | 空 | 服务端默认 provider；配置时必须同时有 `DEFAULT_MODEL` |
| `HERMES_SOURCE_TAG` | `tool` | 写入 Hermes 会话来源标识 |
| `ATTACHMENT_MAX_BYTES` | `10MB` | 单个附件最大字节数 |
| `ATTACHMENT_DOWNLOAD_TIMEOUT_SECONDS` | `30` | 单个附件下载超时 |
| `ATTACHMENT_MAX_IMAGES` | `8` | 单次最多图片数 |
| `ATTACHMENT_REDIRECT_LIMIT` | `3` | 附件下载最大跳转次数 |
| `ATTACHMENT_STORAGE_ROOT` | `/tmp/hermes` | 附件临时保存根目录 |
| `ATTACHMENT_RETENTION_DAYS` | `3` | 附件目录保留天数 |
| `REPORT_SOURCE_ROOT` | `~/.hermes/cache/documents` | 允许发布的 HTML 报告源目录 |
| `REPORT_STORAGE_ROOT` | `./data/reports` | 报告页面副本及有效期信息的持久化目录 |
| `REPORT_PUBLIC_BASE_URL` | `https://athena.agoralab.co/tools2/hermes-gateway` | 完整的外部网关地址，包含部署前缀 |
| `REPORT_RETENTION_DAYS` | `7` | 报告链接及副本的保留天数 |
| `REPORT_MAX_BYTES` | `10485760` | 单个 HTML 报告最大字节数 |

业务流量始终固定使用 Hermes `default` profile，`HERMES_PROFILE_DEFAULT` 不会改变此行为。

## 接口

对外调用方请使用精简版 [docs/v1-api-reference-public.md](./docs/v1-api-reference-public.md)。完整 `/v1` 参考见 [docs/v1-api-reference.md](./docs/v1-api-reference.md)；接口和 Hermes 参数的详细对应关系见 [docs/interface-hermes-mapping.md](./docs/interface-hermes-mapping.md)，`POST /v1/chat` 的附件和传参细节见 [docs/v1-chat-api.md](./docs/v1-chat-api.md)。

### `POST /v1/chat`

创建或续聊一个会话。

```bash
curl -X POST http://127.0.0.1:8003/v1/chat \
  -H 'content-type: application/json' \
  -d '{"user_id":"alice","question":{"text":"帮我总结一下今天的重点"}}'
```

带图片 URL：

```bash
curl -X POST http://127.0.0.1:8003/v1/chat \
  -H 'content-type: application/json' \
  -d '{
    "question": {
      "text": "请分析这张图",
      "attachments": [
        {
          "url": "https://example.com/image.png",
          "type": "image"
        }
      ]
    },
    "session_id": "demo-session",
    "user_id": "alice"
  }'
```

`question.attachments[].type=image` 会下载到 `/tmp/hermes/<YYYY-MM-DD>/<session_id>/`，并通过 `--image` 交给 agent；prompt 只追加图片数量的自然语言提示。
`question.attachments[].type=file` 会下载到 `/tmp/hermes/<YYYY-MM-DD>/<session_id>/`，只提供文件路径和元信息，并通过 `--attachment-root` 让 Hermes 工具按需分段读取，不使用 `@file:` 全文注入。
有附件时，`question.text` 可以不传或传空字符串；无附件时必须传非空文本。
图片和文件默认只保留最近 3 天数据。

### `GET /v1/sessions`

列出当前用户的会话。

```bash
curl 'http://127.0.0.1:8003/v1/sessions?user_id=alice'
```

不传 `user_id` 时默认查询 `guest`。

### `GET /v1/messages`

统一查询消息：带 `session_id` 时读取一个会话的消息；同时带 `session_id` 和 `keyword` 时搜索该会话；只带 `keyword` 时搜索当前用户全部会话。全局搜索必须提供非空 `keyword`。所有模式支持 `limit`（默认 `20`）和 `offset`（默认 `0`）。

```bash
curl 'http://127.0.0.1:8003/v1/messages?user_id=alice&session_id=demo-session'
curl 'http://127.0.0.1:8003/v1/messages?user_id=alice&session_id=demo-session&keyword=hello'
curl 'http://127.0.0.1:8003/v1/messages?user_id=alice&keyword=hello'
```

### `POST /v1/chat`

创建会话或向指定会话继续发送消息。指定会话时，把 `session_id` 放在请求体里。

```bash
curl -X POST http://127.0.0.1:8003/v1/chat \
  -H 'content-type: application/json' \
  -d '{"session_id":"demo-session","user_id":"alice","question":{"text":"继续聊这个话题"}}'
```

如果该 `session_id` 已经存在，也可以省略 `user_id`：

```bash
curl -X POST http://127.0.0.1:8003/v1/chat \
  -H 'content-type: application/json' \
  -d '{"session_id":"demo-session","question":{"text":"继续聊这个话题"}}'
```

### `GET /admin`

管理员网页，可以查看所有用户、会话、消息，并做全局搜索。会话详情按时间顺序展示每次请求：成功请求保留正常问答消息；被同一会话后续请求替代的调用显示 HTTP 409 和中断原因；Hermes 调用失败会显示失败状态与错误详情。

管理面板按回车应用两个搜索框的交集条件：会话信息搜索仅展示匹配会话及其所属用户；用户搜索默认选择首个匹配用户，自动加载其首个会话与消息。点击用户进一步收窄会话，清除会话条件可查看该用户全部会话。消息支持关键词高亮、命中导航和仅看命中；刷新保留筛选，重置全部清除筛选。

`GET /admin/v1/browse` 使用管理员 Cookie，接收 `keyword`、`user_query`、`user_id`、`limit`（默认 100，最多 500）、`offset`。返回完整匹配用户集合、当前用户范围下的会话分页及总数；用户计数按匹配会话统计。匹配范围包含会话标题、会话 ID 以及已索引消息的正文、标题、角色和类型。原有搜索接口保持兼容。

### `GET /admin/login`

管理员登录页，用户名默认预填。

### `GET /admin/chat`

管理员在线对话页，支持输入文本并直接上传本地图片或文件。页面通过管理员 Cookie 和 CSRF token 调用 `POST /admin/v1/chat`，不会向浏览器暴露 `GATEWAY_API_KEY`。上传附件与 `POST /v1/chat` 的 URL 附件会复用同一套 Hermes 调用、会话和消息记录逻辑。

服务部署在反向代理子路径时，设置 `PUBLIC_BASE_PATH`。例如当前部署路径为 `/tools2/hermes-gateway`：

```dotenv
PUBLIC_BASE_PATH=/tools2/hermes-gateway
```

之后从 `https://athena.agoralab.co/tools2/hermes-gateway/admin/chat` 访问页面。

管理员在管理面板选择用户后，可勾选其 skills。网关仅会将该用户已绑定且当前启用的 skill 转为 Hermes `-s <skill>` 参数；`guest` 用户会在服务启动时自动创建，也可在同一页面管理绑定。

网关接口统一称为 Skills，内部目录仍通过 Hermes `skills list --scope private --json` 获取，仅管理 Hermes private skills。capabilities 不接收 `scope`；用户绑定接口为 `GET/PATCH /v1/users/{user_id}/skills`。公共和管理员更新均使用 add/remove 增量语义，空对象或空数组不改变绑定。新增须启用，移除允许失效项；最终最多 10 个绑定。管理员页面按勾选差异提交，不提供 PUT/DELETE 清空接口。现存数据库绑定无需迁移。

管理员页面使用的业务 API 为 `GET /admin/v1/dashboard`、`GET /admin/v1/capabilities`、`GET/PATCH /admin/v1/users/{user_id}`、`GET /admin/v1/conversations`、`GET /admin/v1/conversations/{session_id}/timeline`、`GET /admin/v1/search`、`GET /admin/v1/browse` 与 `POST /admin/v1/chat`。所有接口均由管理员 Cookie 保护，写操作还要求页面下发的 CSRF token。

### HTML 报告页面

两个聊天入口都会将回复中 `REPORT_SOURCE_ROOT` 下明确给出的单个 `.html` 文件路径替换为 HTTPS 报告页面链接。网关保存独立文件副本，历史消息与当前回答包含相同链接。源文件清理或网关重启不影响未过期的副本。

网关不向 Hermes 提问中注入 HTML 生成、验证或交付提示，首轮、带附件请求和已有会话续聊均保持用户问题原文。是否生成 HTML 由用户需求和 Skills 决定；网关仅在回复包含允许目录内的 HTML 文件路径时执行后处理，没有报告路径的回复原样返回。Hermes 静默模式不输出文件 diff 预览，避免将 HTML 源码混入回答。

`GET /v1/reports/{filename}/{download_token}` 使用原始报告文件名和 256 位随机访问凭证，无需 Gateway Key 或管理员 Cookie；持有链接即可打开页面。默认有效期 7 天，过期、无效凭证或文件名不匹配时返回 `404`。HTML 以页面形式直接打开。文件丢失、超过大小限制或真实路径越界时，回答中的路径替换为“报告暂不可用”，其余聊天结论正常返回。

网关在启动后及每小时清理过期报告，发布新报告时也会清理。当前 Nginx 的 `/tools2/hermes-gateway/v1/` 代理覆盖报告接口，无需新增静态目录映射。

## 测试

```bash
cd /home/devops/ka/automation/agent/hermes-http-gateway
.venv/bin/python -m unittest discover -s tests
```

## 认证

- 业务用户标识使用 `user_id`，POST 接口放 JSON body，GET 接口放 query；业务请求固定使用 Hermes `default` profile
- `user_id` 不传时缺省值是 `guest`；已有会话命中时会继承原会话用户
- 如果设置了 `GATEWAY_API_KEY`，还需要 `X-Gateway-Key` 或 `Authorization: Bearer ...`
- 管理页和管理 API 需要先登录，账号密码来自 `ADMIN_USERNAME` / `ADMIN_PASSWORD`
- 管理员上传接口需要 `python-multipart`，已作为项目运行依赖声明
