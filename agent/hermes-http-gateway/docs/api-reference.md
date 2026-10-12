# Hermes HTTP Gateway API 参考

本文档是当前 Gateway 的完整接口目录。聊天附件和 Hermes 调用细节见 [v1-chat-api.md](./v1-chat-api.md)，Gateway 到 Hermes CLI 的字段映射见 [interface-hermes-mapping.md](./interface-hermes-mapping.md)。

## 基础地址与通用约定

当前部署地址：

```text
https://athena.agoralab.co/tools2/hermes-gateway
```

下文路径均相对于该地址。服务默认还提供 FastAPI 自动生成的 `GET /openapi.json`、`GET /docs` 和 `GET /redoc`；是否能从公网访问取决于反向代理规则。

`user_id` 是调用方传入的业务隔离标识，不是服务端认证得到的用户身份。缺失、空字符串或全空白 `user_id` 都会解析为 `guest`。

非空 `session_id` 必须满足：

```text
^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$
```

## 鉴权

### 业务接口

配置了 `GATEWAY_API_KEY` 时，会话、消息、聊天接口必须携带以下任意一种请求头。报告报告接口使用链接中的随机访问凭证，不要求 Gateway Key：

```http
X-Gateway-Key: <gateway_api_key>
```

```http
Authorization: Bearer <gateway_api_key>
```

未配置 `GATEWAY_API_KEY` 时，业务接口不校验该 Key。

### 管理员接口

`/admin/*` 和 `/admin/v1/*` 使用 `POST /admin/login` 设置的管理员 Session Cookie。写操作还要求管理页面下发的 CSRF Token：

```http
X-CSRF-Token: <csrf_token>
```

## 健康检查

### `GET /health`

无需鉴权，返回服务和 Hermes 二进制可用性：

```json
{
  "ok": true,
  "database": "/path/to/hermes_gateway.sqlite",
  "hermes_bin": "../hermes-agent/.venv/bin/hermes",
  "hermes_workdir": "/path/to/hermes-agent",
  "hermes_available": true
}
```

该响应包含服务端路径信息，生产环境应仅向受信任的探针或调用方开放。

## 业务接口

所有业务聊天固定使用 Hermes `default` profile。请求模型不接受 `profile`、`model`、`provider`、`skills` 等未声明字段。

### `GET /v1/sessions`

列出指定用户的会话。

| Query 参数 | 必填 | 默认值 | 说明 |
|---|---:|---:|---|
| `user_id` | 否 | `guest` | 会话所属用户 |
| `limit` | 否 | `50` | 最大返回会话数 |
| `offset` | 否 | `0` | 偏移量 |

```json
{
  "sessions": [
    {
      "session_id": "demo-session",
      "user_id": "guest",
      "hermes_session_id": "20260923_example",
      "title": "会话标题",
      "status": "completed",
      "last_error": null,
      "created_at": "2026-09-23T00:00:00+00:00",
      "updated_at": "2026-09-23T00:01:00+00:00",
      "message_count": 4
    }
  ]
}
```

### `GET /v1/messages`

统一读取会话消息和搜索消息历史。

| Query 参数 | 必填 | 默认值 | 说明 |
|---|---:|---:|---|
| `user_id` | 否 | `guest` | 用户范围 |
| `session_id` | 条件必填 | - | 限定到一个会话 |
| `q` | 条件必填 | - | 搜索词；未传 `session_id` 时必须提供非空值 |
| `limit` | 否 | `20` | 最大返回消息数 |
| `offset` | 否 | `0` | 偏移量 |

支持三种模式：

```text
GET /v1/messages?user_id=guest&session_id=demo-session
GET /v1/messages?user_id=guest&session_id=demo-session&q=error
GET /v1/messages?user_id=guest&q=error
```

- 指定 `session_id` 时，该会话必须存在且属于 `user_id`，否则返回 `404`。
- 未指定 `session_id` 且 `q` 缺失或为空白时返回 `422`。
- 全局搜索时 `conversation` 为 `null`；限定会话的读取或搜索返回会话元信息。
- 当前代码中，`GET /v1/messages` 的单会话 `conversation.message_count` 没有聚合计算，值为 `null`；`GET /v1/sessions` 和管理员时间线的会话信息会返回实际统计数量。

```json
{
  "conversation": {
    "session_id": "demo-session",
    "user_id": "guest",
    "hermes_session_id": "20260923_example",
    "title": "会话标题",
    "status": "completed",
    "last_error": null,
    "created_at": "2026-09-23T00:00:00+00:00",
    "updated_at": "2026-09-23T00:01:00+00:00",
    "message_count": null
  },
  "messages": [
    {
      "id": "message-id",
      "session_id": "demo-session",
      "user_id": "guest",
      "role": "assistant",
      "kind": "chat",
      "content": "回答内容",
      "model": "gpt-5.6",
      "provider": "provider-name",
      "created_at": "2026-09-23T00:01:00+00:00"
    }
  ],
  "limit": 20,
  "offset": 0
}
```

### `POST /v1/chat`

创建会话或继续已有会话。附件处理和 Hermes 调用细节见 [v1-chat-api.md](./v1-chat-api.md)。

```http
Content-Type: application/json
X-Gateway-Key: <gateway_api_key>
```

```json
{
  "question": {
    "text": "请分析附件",
    "attachments": [
      {"url": "https://example.com/image.png", "type": "image"},
      {"url": "https://example.com/report.pdf", "type": "file"}
    ]
  },
  "user_id": "guest",
  "session_id": "optional-session-id"
}
```

| 字段 | 必填 | 限制 |
|---|---:|---|
| `question` | 是 | 对象，不允许未声明字段 |
| `question.text` | 条件必填 | 最大 20,000 字符；未带附件时必须非空白 |
| `question.attachments` | 否 | 最多 8 个 URL 附件 |
| `question.attachments[].url` | 是 | 公网 `http` 或 `https` URL，长度 1 到 2,048 |
| `question.attachments[].type` | 是 | `image` 或 `file` |
| `user_id` | 否 | 最大 120 字符；新会话默认 `guest` |
| `session_id` | 否 | 缺失时自动生成，否则必须符合会话 ID 格式 |

成功响应：

```json
{
  "session_id": "demo-session",
  "hermes_session_id": "20260923_example",
  "user_id": "guest",
  "answer": "Hermes 的回答"
}
```

已有会话不传 `user_id` 时继承原用户；显式传入不同用户时返回 `409`。首次创建新会话时，网关会自动创建不存在的用户记录。

### `GET /v1/capabilities?scope=private`

使用 Gateway API Key 查询当前 `default` profile 的 Private Skills 总目录。响应不包含用户信息和用户绑定状态。

### `GET /v1/users/{user_id}/private-skills`

使用 Gateway API Key 查询用户的 `bound_skill_ids`、`active_skill_ids` 和 `unavailable_skill_ids`。

### `PUT /v1/users/{user_id}/private-skills`

使用 Gateway API Key 替换用户绑定关系，请求体为 `{"skill_ids": ["skill-id"]}`。最多 10 个，只允许当前启用的 Private Skills；空数组表示解绑全部。

### `GET /v1/reports/{filename}/{download_token}`

打开聊天回答生成的 HTML 报告页面。两个聊天入口均在保存助手消息前，将 `REPORT_SOURCE_ROOT` 下明确给出的单个 HTML 路径转换为报告页面链接。

Path 参数为 `filename`（原始 HTML 报告文件名）和 `download_token`（随机访问凭证）；文件名必须与凭证对应。直接使用回答返回的 HTTPS 链接即可打开页面，无需 Gateway Key 或管理员 Cookie。成功返回 HTML 文件内容（`text/html`、`Content-Disposition: inline`），默认 7 天过期。凭证无效、文件名不匹配、已过期或副本缺失时返回 `404`：`{"detail":"Report not found or expired"}`。

副本和元信息保存到 `REPORT_STORAGE_ROOT`，重启不会使链接失效。文件不存在、超出 `REPORT_MAX_BYTES` 或真实路径超出源目录时，聊天回答提示“报告暂不可用”。

## 管理员页面

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/admin/login` | 登录页面；已登录时重定向至 `/admin` |
| `POST` | `/admin/login` | JSON 登录，字段为 `username`、`password`；成功后设置管理员 Cookie |
| `GET` | `/admin/logout` | 删除管理员 Session 并重定向到登录页 |
| `GET` | `/admin` | 管理面板 |
| `GET` | `/admin/chat` | 支持本地图片和文件上传的管理员对话页 |

## 管理员 API

### `POST /admin/v1/chat`

管理员专用 `multipart/form-data` 上传对话接口。它与 `POST /v1/chat` 共用会话、附件、用户 Private Skills、Hermes 调用和响应逻辑。

| Multipart 字段 | 必填 | 默认值 | 说明 |
|---|---:|---:|---|
| `text` | 条件必填 | 空字符串 | 无上传文件时必须为非空白文本 |
| `user_id` | 否 | `admin-playground` | 会话所属用户 |
| `session_id` | 否 | 新 UUID | Gateway 会话 ID |
| `files` | 否 | - | 一个或多个本地图片或文件 |

### `GET /admin/v1/dashboard`

返回概览统计与分页用户列表。

| Query 参数 | 默认值 | 说明 |
|---|---:|---|
| `user_query` | - | 按用户 ID 或存储 profile 模糊筛选 |
| `user_limit` | `100` | 最大返回用户数 |
| `user_offset` | `0` | 用户列表偏移量 |

```json
{
  "summary": {"users": 12, "conversations": 50, "messages": 160, "requests": 90},
  "users": [],
  "user_pagination": {"limit": 100, "offset": 0}
}
```

### `GET /admin/v1/users/{user_id}`

返回用户记录和该用户已绑定的 Private Skill ID。不存在的 `user_id` 会先自动创建。当前 Private Skills 总目录通过独立的 `GET /admin/v1/capabilities?scope=private` 获取。

### `GET /admin/v1/capabilities?scope=private`

返回当前 `default` profile 下的 Private Skills 总目录。该接口需要管理员登录。

返回示例：

```json
{
  "scope": "private",
  "profile": "default",
  "skills": []
}
```

总目录由以下命令获取：

```bash
hermes -p default skills list --scope private --json
```

该目录在 Gateway 进程内缓存 45 秒；用户绑定关系从 SQLite 读取。

### `PATCH /admin/v1/users/{user_id}`

替换指定用户的全部 Private Skill 绑定。需要 `X-CSRF-Token`。

```json
{
  "private_skill_ids": ["qa-jira-issue-list-retro"]
}
```

- 最多传入 10 个 ID。
- 每个 ID 必须是 Hermes 当前私有目录中的启用 Skill。
- 无效 ID 返回 `422`，响应 `detail.invalid_private_skill_ids` 列出无效项。
- 传入空数组可解绑该用户的全部 Private Skills。

### `GET /admin/v1/conversations`

列出全部会话。

| Query 参数 | 默认值 | 说明 |
|---|---:|---|
| `query` | - | 按会话 ID、用户 ID、标题或存储 profile 模糊筛选 |
| `user_id` | - | 精确限定用户 |
| `limit` | `100` | 最大返回会话数 |
| `offset` | `0` | 偏移量 |

### `GET /admin/v1/conversations/{session_id}/timeline`

返回某会话完整的管理员审计数据：

```json
{
  "conversation": {},
  "messages": [],
  "requests": [],
  "timeline": []
}
```

`timeline` 是按时间合并的 `messages` 与 `requests`。请求事件含原始请求 Body、状态、错误详情、阶段耗时和可用的 `runtime_audit`。后者可能包含实际主模型、回退事件、图片/视觉模型信息、上下文窗口、本轮生效的 Private Skills，以及 `skill_audit.skills[]` 的 Skill 最终状态。Skill 没有回传审计时会记录为 `unknown`，不会根据 Hermes 总体成功状态推断 Skill 成功。

### `GET /admin/v1/search`

在全部用户范围内搜索已存储消息，也可限定用户或会话。

| Query 参数 | 必填 | 默认值 | 说明 |
|---|---:|---:|---|
| `q` | 是 | - | 搜索词 |
| `user_id` | 否 | - | 精确用户范围 |
| `session_id` | 否 | - | 精确会话范围 |
| `limit` | 否 | `50` | 最大返回数 |

## 常见错误

| 状态码 | 常见原因 |
|---:|---|
| `400` | 会话 ID、附件 URL/下载/格式/大小不合法，或服务端模型配置不合法 |
| `401` | Gateway Key 缺失/错误，或管理员未登录 |
| `403` | 管理员写操作缺少或使用了错误的 CSRF Token |
| `404` | 会话不存在，或在指定 `user_id` 范围内不存在 |
| `409` | 会话属于另一显式用户，或同会话旧请求被新请求替代 |
| `422` | JSON 或 multipart 结构不合法、字段越界、空请求，或 Private Skill ID 无效 |
| `502` | Hermes 对话失败、超时，或首轮没有返回 Hermes Session ID |
| `503` | Private Skill 目录加载失败，或管理员上传依赖不可用 |

## 已删除接口

以下旧路由已移除，调用时返回 `404`：

```text
GET /v1/users/me
GET /v1/sessions/search
GET /v1/sessions/{session_id}/messages

GET /admin/api/summary
GET /admin/api/users
GET /admin/api/users/{user_id}/private-skills
PUT /admin/api/users/{user_id}/private-skills
GET /admin/api/conversations/{session_id}
GET /admin/api/conversations/{session_id}/messages
```
