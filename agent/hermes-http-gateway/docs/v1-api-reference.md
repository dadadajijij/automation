# Hermes HTTP Gateway 对外 v1 API 文档

本文档仅面向 Gateway 调用方，覆盖当前全部 `/v1` 对外接口：

- `GET /v1/sessions`
- `GET /v1/messages`
- `POST /v1/chat`
- `GET /v1/capabilities?scope=private`
- `GET /v1/users/{user_id}/private-skills`
- `PUT /v1/users/{user_id}/private-skills`
- `GET /v1/reports/{filename}/{download_token}`

不包含 `/admin` 和 `/admin/api` 管理员接口。

## 1. 基础信息

当前线上基础地址：

```text
https://athena.agoralab.co/tools2/hermes-gateway
```

会话列表、消息查询和聊天接口在服务端配置 `GATEWAY_API_KEY` 时，需要携带以下任意一种鉴权头；报告页面使用链接中的随机凭证，无需该请求头：

```http
X-Gateway-Key: <gateway_api_key>
```

```http
Authorization: Bearer <gateway_api_key>
```

`user_id` 是调用方传入的业务隔离标识，不是服务端认证的用户身份。未传、空字符串或全空白时默认使用 `guest`。

`session_id` 为 Gateway 对外会话 ID，非空时必须符合：

```text
^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$
```

业务请求固定使用 Hermes `default` profile。请求中不支持 `profile`、`model`、`provider`、`skills` 等额外字段，传入会返回 `422`。

`POST /v1/chat` 是同步接口。当前部署的 Hermes CLI 等待上限为 `1200` 秒；该值由服务端 `HERMES_TIMEOUT_SECONDS` 控制，代码未配置时默认 `300` 秒。调用方的 HTTP 客户端和反向代理超时应至少覆盖该时间。

## 2. 查询会话

### `GET /v1/sessions`

列出一个用户的会话，按最后更新时间倒序返回。

| 参数 | 位置 | 必填 | 默认值 | 作用 |
|---|---|---:|---:|---|
| `user_id` | Query | 否 | `guest` | 指定要查询的用户 |
| `limit` | Query | 否 | `50` | 最大返回会话数量 |
| `offset` | Query | 否 | `0` | 分页偏移量 |

请求示例：

```bash
curl -G 'https://athena.agoralab.co/tools2/hermes-gateway/v1/sessions' \
  -H 'X-Gateway-Key: <gateway_api_key>' \
  --data-urlencode 'user_id=guest' \
  --data-urlencode 'limit=50' \
  --data-urlencode 'offset=0'
```

成功响应：

```json
{
  "sessions": [
    {
      "session_id": "demo-session",
      "user_id": "guest",
      "hermes_session_id": "20260923_example",
      "title": "请分析这个问题",
      "status": "completed",
      "last_error": null,
      "created_at": "2026-09-23T00:00:00+00:00",
      "updated_at": "2026-09-23T00:01:00+00:00",
      "message_count": 4
    }
  ]
}
```

返回字段：

| 字段 | 说明 |
|---|---|
| `session_id` | Gateway 对外会话 ID，后续聊天和消息查询使用此值 |
| `user_id` | 会话所属用户 |
| `hermes_session_id` | Hermes 内部会话 ID，仅用于排查，不作为后续请求参数 |
| `title` | 网关从首轮文本生成的会话标题 |
| `status` | 当前会话状态，例如 `completed`、`running`、`error` |
| `last_error` | 最近失败错误；无错误时为 `null` |
| `created_at` / `updated_at` | ISO 8601 时间 |
| `message_count` | 已保存的用户与助手消息总数 |

## 3. 查询或搜索消息

### `GET /v1/messages`

该接口同时支持完整读取一个会话、在一个会话内搜索、搜索一个用户的全部会话。

| 参数 | 位置 | 必填 | 默认值 | 作用 |
|---|---|---:|---:|---|
| `user_id` | Query | 否 | `guest` | 用户范围 |
| `session_id` | Query | 条件必填 | 无 | 限定一个会话 |
| `q` | Query | 条件必填 | 无 | 搜索词；未传 `session_id` 时必须提供非空值 |
| `limit` | Query | 否 | `20` | 最大返回消息数量 |
| `offset` | Query | 否 | `0` | 分页偏移量 |

### 3.1 读取一个会话

```bash
curl -G 'https://athena.agoralab.co/tools2/hermes-gateway/v1/messages' \
  -H 'X-Gateway-Key: <gateway_api_key>' \
  --data-urlencode 'user_id=guest' \
  --data-urlencode 'session_id=demo-session' \
  --data-urlencode 'limit=20' \
  --data-urlencode 'offset=0'
```

### 3.2 在一个会话内搜索

```bash
curl -G 'https://athena.agoralab.co/tools2/hermes-gateway/v1/messages' \
  -H 'X-Gateway-Key: <gateway_api_key>' \
  --data-urlencode 'user_id=guest' \
  --data-urlencode 'session_id=demo-session' \
  --data-urlencode 'q=错误' \
  --data-urlencode 'limit=20'
```

### 3.3 搜索一个用户的全部会话

```bash
curl -G 'https://athena.agoralab.co/tools2/hermes-gateway/v1/messages' \
  -H 'X-Gateway-Key: <gateway_api_key>' \
  --data-urlencode 'user_id=guest' \
  --data-urlencode 'q=错误' \
  --data-urlencode 'limit=20' \
  --data-urlencode 'offset=0'
```

成功响应：

```json
{
  "conversation": {
    "session_id": "demo-session",
    "user_id": "guest",
    "hermes_session_id": "20260923_example",
    "title": "请分析这个问题",
    "status": "completed",
    "last_error": null,
    "created_at": "2026-09-23T00:00:00+00:00",
    "updated_at": "2026-09-23T00:01:00+00:00",
    "message_count": null
  },
  "messages": [
    {
      "id": "8f2c7c17-1a72-4f1e-aab4-0eaebca26d7f",
      "session_id": "demo-session",
      "user_id": "guest",
      "role": "assistant",
      "kind": "chat",
      "content": "这是已保存的回答内容。",
      "model": "gpt-5.6",
      "provider": "provider-name",
      "created_at": "2026-09-23T00:01:00+00:00"
    }
  ],
  "limit": 20,
  "offset": 0
}
```

返回字段：

| 字段 | 说明 |
|---|---|
| `conversation` | 指定 `session_id` 时返回会话元信息；跨会话搜索时为 `null` |
| `messages` | 消息列表或搜索命中列表 |
| `messages[].id` | 消息记录 ID |
| `messages[].session_id` | 消息所在的 Gateway 会话 ID |
| `messages[].user_id` | 消息所属用户 |
| `messages[].role` | `user` 或 `assistant` |
| `messages[].kind` | 当前消息类型，正常对话为 `chat` |
| `messages[].content` | 消息正文 |
| `messages[].model` / `provider` | 保存消息时记录的实际模型与提供方；用户消息通常为 `null` |
| `limit` / `offset` | 本次查询实际使用的分页参数 |

注意：当前 `GET /v1/messages` 在指定会话时未聚合 `conversation.message_count`，该字段会返回 `null`；如需消息总数，可使用 `GET /v1/sessions` 的 `message_count`。

错误规则：

- 未传 `session_id` 且 `q` 为空：`422`，`detail` 为 `q is required when session_id is not provided`。
- 指定的会话不存在，或不属于该 `user_id`：`404`，`detail` 为 `Session not found`。

## 4. 发起或续聊

### `POST /v1/chat`

创建一个新 Gateway 会话，或继续已存在的会话。接口同步等待 Hermes 返回。

请求头：

```http
Content-Type: application/json
X-Gateway-Key: <gateway_api_key>
```

请求字段：

| 字段 | 必填 | 类型 | 限制 | 作用 |
|---|---:|---|---|---|
| `question` | 是 | object | 不允许额外字段 | 本轮请求对象 |
| `question.text` | 条件必填 | string | 最大 20,000 字符 | 用户文本；无附件时必须为非空白 |
| `question.attachments` | 否 | array | 最多 8 项 | 图片或文件 URL 附件 |
| `question.attachments[].url` | 是 | string | 长度 1 到 2,048 | 需要下载的公网 `http/https` URL |
| `question.attachments[].type` | 是 | string | `image` 或 `file` | 指定图片或普通文件处理方式 |
| `user_id` | 否 | string | 最大 120 字符 | 新会话的所属用户；缺失时为 `guest` |
| `session_id` | 否 | string | 符合会话 ID 格式 | Gateway 对外会话 ID；缺失时自动生成 |

### 4.1 创建纯文本会话

```bash
curl -X POST 'https://athena.agoralab.co/tools2/hermes-gateway/v1/chat' \
  -H 'Content-Type: application/json' \
  -H 'X-Gateway-Key: <gateway_api_key>' \
  -d '{
    "question": {
      "text": "你好，请简单介绍一下你能做什么"
    },
    "user_id": "guest"
  }'
```

### 4.2 创建带图片和文件的会话

```bash
curl -X POST 'https://athena.agoralab.co/tools2/hermes-gateway/v1/chat' \
  -H 'Content-Type: application/json' \
  -H 'X-Gateway-Key: <gateway_api_key>' \
  -d '{
    "question": {
      "text": "请结合图片和日志分析问题",
      "attachments": [
        {
          "url": "https://example.com/screenshot.png",
          "type": "image"
        },
        {
          "url": "https://example.com/client.log",
          "type": "file"
        }
      ]
    },
    "user_id": "guest",
    "session_id": "demo-session"
  }'
```

图片会下载并以 Hermes `--image` 参数传入；文件会下载并以 Hermes `@file:` 引用传入。默认单附件最大 10MB、图片最多 8 张，具体限制受服务端附件环境变量控制。附件 URL 必须是可访问的公网地址，内网、localhost、metadata 地址会被拒绝。

### 4.3 继续已有会话

调用方应持久化首轮响应的 `session_id`，续聊时传回该值：

```bash
curl -X POST 'https://athena.agoralab.co/tools2/hermes-gateway/v1/chat' \
  -H 'Content-Type: application/json' \
  -H 'X-Gateway-Key: <gateway_api_key>' \
  -d '{
    "session_id": "demo-session",
    "question": {
      "text": "继续分析，还有哪些排查步骤？"
    }
  }'
```

续聊时可省略 `user_id`，网关会继承原会话用户；若显式传入不同的 `user_id`，接口返回 `409`。

成功响应：

```json
{
  "session_id": "demo-session",
  "hermes_session_id": "20260923_example",
  "user_id": "guest",
  "answer": "Hermes 的最终回答内容"
}
```

| 字段 | 说明 |
|---|---|
| `session_id` | Gateway 对外会话 ID；后续续聊和消息查询使用该值 |
| `hermes_session_id` | Hermes 内部会话 ID；用于排查，不作为请求参数传入 |
| `user_id` | 本次会话归属用户 |
| `answer` | Hermes 返回的最终文本 |

用户绑定的 Private Skills 由服务端自动判断并追加到 Hermes 调用。调用方不需要也不能通过 `/v1/chat` 直接指定 Skill。

## 5. HTML 报告页面

### `GET /v1/reports/{filename}/{download_token}`

聊天回答中明确给出的 HTML 报告路径会转换成 HTTPS 报告页面链接。报告副本与有效期持久化保存，默认保留 7 天。

| 参数 | 位置 | 必填 | 说明 |
|---|---|---:|---|
| `filename` | Path | 是 | 原始 HTML 文件名，必须与访问凭证对应 |
| `download_token` | Path | 是 | 随机访问凭证，使用回答中返回的完整链接 |

在浏览器中打开：

`https://athena.agoralab.co/tools2/hermes-gateway/v1/reports/argus-investigation-example.html/<download_token>`

接口无需 Gateway Key 或管理员登录；持有链接即可打开页面。成功响应为 `200` HTML 文件内容，使用 `Content-Disposition: inline`；无效、过期或文件不存在时返回 `404`。文件发布失败不影响聊天结论，回答会提示“报告暂不可用”。

## 6. 常见错误

| 状态码 | 场景 | 说明 |
|---:|---|---|
| `400` | 会话 ID 不合法 | `session_id` 不符合格式 |
| `400` | 附件处理失败 | URL、下载、大小、图片格式或图片数量不符合限制 |
| `400` | 服务端模型配置错误 | 配置了 `DEFAULT_PROVIDER` 但未配置 `DEFAULT_MODEL` |
| `401` | Key 缺失或错误 | `GATEWAY_API_KEY` 校验失败 |
| `404` | 会话不存在 | `GET /v1/messages` 指定的会话不存在或不属于该用户 |
| `409` | 用户不匹配 | 已有会话传入了不同 `user_id` |
| `409` | 请求被替代 | 同一 `user_id + session_id` 的新请求替代旧的未完成请求 |
| `422` | 请求字段不合法 | JSON 结构、未声明字段、字段长度、空文本且无附件等校验失败 |
| `502` | Hermes 调用失败 | Hermes 非零退出、超时、二进制不可用，或首轮未返回内部 Session ID |

Hermes 调用失败时，`detail` 为对象，通常包含 `message`、`error`、`stdout`、`stderr`、`returncode` 与 Gateway `session_id`。

## 7. 调用建议

1. 首轮不传 `session_id`，保存响应中的 `session_id`；后续请求使用该值续聊。
2. 调用方可以首次自定义 `session_id`，但它在 Gateway 中全局唯一，且必须符合格式。
3. 查询完整会话使用 `GET /v1/messages?session_id=...`；跨会话检索使用 `GET /v1/messages?q=...`。
4. 请妥善保存 Gateway Key，不要将其放入附件 URL 或日志。
