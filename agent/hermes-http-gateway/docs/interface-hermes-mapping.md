# Hermes HTTP Gateway 接口与 Hermes 参数对照

## 总览

只有对话接口会真正调用 Hermes。其余接口主要用于健康检查、用户管理、会话查询和后台管理。

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
| `profile` | `-p` | 新会话优先使用请求里的 `profile`；已有 `session_id` 不传时继承原会话 profile |
| `question.attachments[].type=image` | 重复的 `--image <local_image_path>` + `--attachment-root <session_attachment_dir>` | 网关先下载公网图片 URL 到 `/tmp/hermes/<YYYY-MM-DD>/<session_id>/`，再按顺序把本地路径传给 Hermes；prompt 只追加自然语言图片提示，不注入图片 `@file:` |
| `question.attachments[].type=file` | prompt 里的 `@file:<local_file_path>` + `--attachment-root <session_attachment_dir>` | 网关先下载公网文件 URL 到 `/tmp/hermes/<YYYY-MM-DD>/<session_id>/`，再把本地路径以 `@file:` 引用追加到 prompt |
| `user.default_hermes_profile` | `-p` | 请求没传 `profile` 时使用 |
| `hermes_profile_default` | `-p` | 再往后的默认值 |
| `default_model` | `-m` | 服务端配置的默认模型，不允许外部请求覆盖 |
| `default_provider` | `--provider` | 服务端配置的 provider，不允许外部请求覆盖 |

实际行为：

- 首次纯文本会话：调用 `hermes ... -z <prompt> --usage-file <usage_json_path>`
- 首次图片或文件会话：调用 `hermes ... chat -Q -q <prompt> --image <local_image_path_1> ... --attachment-root <session_attachment_dir> --usage-file <usage_json_path>`
- 已有 Hermes 会话：调用 `hermes chat --resume <hermes_session_id> -Q -q <prompt> --usage-file <usage_json_path>`
- 已有 Hermes 会话带图片：额外按顺序追加多个 `--image <local_image_path>`

### `POST /admin/api/chat`

管理员在线对话页使用的 multipart 上传入口。它要求有效的管理员 Cookie 和页面下发的 CSRF token，不接受也不暴露 `GATEWAY_API_KEY`。

- 浏览器本地上传的图片和文件先落盘到与 `session_id` 对应的附件目录。
- 图片、文件随后分别转换为与 `/v1/chat` URL 附件相同的 `DownloadedImage`、`DownloadedFile`。
- 两个入口共用会话恢复、prompt 构造、Hermes CLI 调用、消息记录和响应结构；区别仅在于附件来源。

### 请求阶段审计

每次聊天请求都会在网关数据库中记录可由网关精确观测的阶段耗时：等待前序附件下载、附件下载、等待会话锁、Hermes CLI 运行和保存结果。管理员会话详情的请求时间线可展开查看这些阶段。

`Hermes CLI 运行` 当前包含模型调用、MCP、工具执行和最终生成；Hermes CLI 尚未向网关输出可关联的结构化内部 trace，因此这些内部子阶段不会被伪造为独立耗时。

## 2. 不调用 Hermes 的接口

这些接口只读写网关数据库或返回页面，不会触发 Hermes：

- `GET /health`
- `GET /v1/users/me`
- `PUT /v1/users/me`
- `GET /v1/sessions`
- `GET /v1/sessions/search`
- `GET /v1/sessions/{session_id}/messages`
- `GET /admin/login`
- `POST /admin/login`
- `GET /admin/logout`
- `GET /admin`
- `GET /admin/chat`
- `GET /admin/api/summary`
- `GET /admin/api/users`
- `GET /admin/api/conversations`
- `GET /admin/api/conversations/{session_id}`
- `GET /admin/api/conversations/{session_id}/messages`
- `GET /admin/api/search`

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
