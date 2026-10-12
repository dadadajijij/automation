# Hermes Gateway v1 对外接口

本文档说明能力目录、用户 Private Skills 绑定、会话列表、消息查询、聊天和报告页面等 `/v1` 接口。

## 基础信息

当前服务地址：

```text
https://athena.agoralab.co/tools2/hermes-gateway
```

会话列表、消息查询和聊天请求使用以下鉴权头（Key 由服务提供方提供）；报告页面使用链接内的访问凭证：

```http
X-Gateway-Key: <gateway_api_key>
```

或：

```http
Authorization: Bearer <gateway_api_key>
```

查询接口和新会话中，`user_id` 未传或为空白时使用 `guest`；续聊时未传则继承原会话用户。所有查询参数均位于 URL，聊天参数使用 JSON Body。

`session_id` 在网关内全局唯一，长度不超过 120 字符，格式为 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$`。聊天请求不接受未声明字段，例如 `profile`、`model`、`provider`、`skills`。

## 1. 查询 Private Skills 能力目录

### `GET /v1/capabilities?scope=private`

查询当前 Gateway `default` profile 下可用的 Private Skills 总目录。该接口不包含任何用户信息，也不返回用户绑定状态。

请求示例：

```bash
curl 'https://athena.agoralab.co/tools2/hermes-gateway/v1/capabilities?scope=private' \
  -H 'X-Gateway-Key: <gateway_api_key>'
```

返回示例：

```json
{
  "scope": "private",
  "profile": "default",
  "skills": [
    {
      "id": "qa-jira-issue-list-retro",
      "name": "qa-jira-issue-list-retro",
      "category": "agora-skills",
      "description": "Jira 问题回顾分析",
      "version": "1.0.0",
      "enabled": true
    }
  ]
}
```

## 2. 查询用户 Private Skills 绑定

### `GET /v1/users/{user_id}/private-skills`

查询指定用户的绑定关系，不返回完整 Skills 目录。不存在的用户会自动创建。

请求示例：

```bash
curl 'https://athena.agoralab.co/tools2/hermes-gateway/v1/users/customer-a/private-skills' \
  -H 'X-Gateway-Key: <gateway_api_key>'
```

返回示例：

```json
{
  "user_id": "customer-a",
  "profile": "default",
  "bound_skill_ids": ["qa-jira-issue-list-retro"],
  "active_skill_ids": ["qa-jira-issue-list-retro"],
  "unavailable_skill_ids": []
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `bound_skill_ids` | 数据库中保存的全部绑定 ID |
| `active_skill_ids` | 当前目录中存在且 `enabled=true` 的绑定 ID |
| `unavailable_skill_ids` | 已绑定但当前不可用的 ID |

## 3. 更新用户 Private Skills 绑定

### `PUT /v1/users/{user_id}/private-skills`

替换指定用户的全部 Private Skills 绑定。最多 10 个，只能绑定当前 capabilities 目录中启用的 Skill。

请求示例：

```bash
curl -X PUT 'https://athena.agoralab.co/tools2/hermes-gateway/v1/users/customer-a/private-skills' \
  -H 'Content-Type: application/json' \
  -H 'X-Gateway-Key: <gateway_api_key>' \
  -d '{
    "skill_ids": ["qa-jira-issue-list-retro"]
  }'
```

解绑全部：

```json
{
  "skill_ids": []
}
```

成功响应与查询接口相同。无效 ID 返回 `422`：

```json
{
  "detail": {
    "invalid_private_skill_ids": ["unknown-skill"]
  }
}
```

## 4. 查询会话

### `GET /v1/sessions`

查询指定用户的会话列表，按更新时间倒序返回。

请求参数：

| 参数 | 位置 | 必填 | 默认值 | 说明 |
|---|---|---:|---:|---|
| `user_id` | Query | 否 | `guest` | 用户 ID |
| `limit` | Query | 否 | `50` | 最大返回数量 |
| `offset` | Query | 否 | `0` | 分页偏移量 |

请求示例：

```bash
curl -G 'https://athena.agoralab.co/tools2/hermes-gateway/v1/sessions' \
  -H 'X-Gateway-Key: <gateway_api_key>' \
  --data-urlencode 'user_id=guest' \
  --data-urlencode 'limit=50' \
  --data-urlencode 'offset=0'
```

返回示例：

```json
{
  "sessions": [
    {
      "session_id": "demo-session",
      "user_id": "guest",
      "hermes_session_id": "20261008_example",
      "title": "请分析这个问题",
      "status": "open",
      "last_error": null,
      "created_at": "2026-10-08T00:00:00+00:00",
      "updated_at": "2026-10-08T00:01:00+00:00",
      "message_count": 4
    }
  ]
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `session_id` | Gateway 对外会话 ID，后续聊天和消息查询使用该值 |
| `user_id` | 会话所属用户 |
| `hermes_session_id` | Hermes 内部会话 ID，仅用于排查 |
| `title` | 会话标题 |
| `status` | 会话状态：`open`（可继续对话）、`running`（处理中）、`error`（执行失败） |
| `last_error` | 最近一次错误，没有错误时为 `null` |
| `created_at` / `updated_at` | ISO 8601 时间 |
| `message_count` | 已保存的用户和助手消息数量 |

## 5. 查询消息

### `GET /v1/messages`

统一查询会话消息和搜索消息历史。

请求参数：

| 参数 | 位置 | 必填 | 默认值 | 说明 |
|---|---|---:|---:|---|
| `user_id` | Query | 否 | `guest` | 用户 ID |
| `session_id` | Query | 条件必填 | - | 指定会话 ID；传入后只查询该会话 |
| `q` | Query | 条件必填 | - | 搜索关键词；未传 `session_id` 时必须传非空值 |
| `limit` | Query | 否 | `20` | 最大返回数量 |
| `offset` | Query | 否 | `0` | 分页偏移量 |

支持三种用法：

```text
# 分页读取指定会话消息
GET /v1/messages?user_id=guest&session_id=demo-session

# 搜索指定会话
GET /v1/messages?user_id=guest&session_id=demo-session&q=错误

# 搜索用户的全部会话
GET /v1/messages?user_id=guest&q=错误
```

请求示例：

```bash
curl -G 'https://athena.agoralab.co/tools2/hermes-gateway/v1/messages' \
  -H 'X-Gateway-Key: <gateway_api_key>' \
  --data-urlencode 'user_id=guest' \
  --data-urlencode 'session_id=demo-session' \
  --data-urlencode 'limit=20'
```

返回示例：

```json
{
  "conversation": {
    "session_id": "demo-session",
    "user_id": "guest",
    "hermes_session_id": "20261008_example",
    "title": "请分析这个问题",
    "status": "open",
    "last_error": null,
    "created_at": "2026-10-08T00:00:00+00:00",
    "updated_at": "2026-10-08T00:01:00+00:00",
    "message_count": null
  },
  "messages": [
    {
      "event_type": "message",
      "id": "message-id",
      "session_id": "demo-session",
      "user_id": "guest",
      "role": "assistant",
      "kind": "chat",
      "content": "这是回答内容。",
      "model": "gpt-5.6",
      "provider": "provider-name",
      "created_at": "2026-10-08T00:01:00+00:00"
    }
  ],
  "limit": 20,
  "offset": 0
}
```

消息字段说明：

| 字段 | 说明 |
|---|---|
| `conversation` | 会话信息，字段同会话列表；跨会话搜索为 `null` |
| `messages` | 本页消息数组；无数据时为 `[]` |
| `messages[].id` | 消息 ID |
| `messages[].session_id` / `user_id` | 所属会话和用户 |
| `messages[].role` | 消息角色，通常为 `user` 或 `assistant` |
| `messages[].kind` | 消息类型：`chat` 为正常消息，`error` 为失败信息 |
| `messages[].content` | 消息内容 |
| `messages[].model` / `provider` | 记录的模型与提供方，可能为 `null` |
| `messages[].created_at` | ISO 8601 时间 |
| `messages[].event_type` | 不带搜索词读取消息时为 `message`；搜索结果没有此字段 |
| `limit` / `offset` | 本次查询使用的分页参数 |

查询规则：

- 查询指定会话时，该会话必须存在且属于 `user_id`，否则返回 `404`。
- 未传 `session_id` 且 `q` 为空时返回 `422`。
- 跨会话搜索时 `conversation` 为 `null`。
- 当前接口的 `conversation.message_count` 为 `null`，消息总数可通过 `/v1/sessions` 查询。
- 不搜索时按时间正序读取；搜索时按时间倒序返回。`limit`、`offset` 使用整数，建议 `limit > 0`、`offset >= 0`。

## 6. 创建或继续聊天

### `POST /v1/chat`

创建新会话，或使用已有 `session_id` 继续聊天。接口会同步等待 Hermes 返回结果。

请求头：

```http
Content-Type: application/json
X-Gateway-Key: <gateway_api_key>
```

请求参数：

| 参数 | 位置 | 必填 | 说明 |
|---|---|---:|---|
| `question` | JSON Body | 是 | 当前问题对象 |
| `question.text` | JSON Body | 条件必填 | string，默认空字符串，最大 20,000 字符；无附件时必须非空白 |
| `question.attachments` | JSON Body | 否 | array，默认 `[]`，最多 8 个附件 |
| `question.attachments[].url` | JSON Body | 有附件时必填 | string，公网 `http/https` URL，长度 1 到 2,048 字符 |
| `question.attachments[].type` | JSON Body | 有附件时必填 | string，`image` 或 `file` |
| `user_id` | JSON Body | 否 | string，最长 120 字符；新会话默认 `guest` |
| `session_id` | JSON Body | 否 | string，最长 120 字符；不传或空字符串时自动生成 |

请求示例（附件可省略）：

```bash
curl -X POST 'https://athena.agoralab.co/tools2/hermes-gateway/v1/chat' \
  -H 'Content-Type: application/json' \
  -H 'X-Gateway-Key: <gateway_api_key>' \
  -d '{
    "user_id": "guest",
    "session_id": "demo-session",
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
    }
  }'
```

纯文本请求 Body：

```json
{"user_id": "guest", "question": {"text": "你好"}}
```

续聊请求 Body：

```json
{"session_id": "demo-session", "question": {"text": "继续分析"}}
```

不传 `session_id` 时创建新会话；传入尚不存在的 ID 时以该 ID 创建会话。新用户会自动创建。续聊时可以省略 `user_id`；传入与会话所属用户不同的值会返回 `409`。

有附件时文本可为空。附件需为公网可访问 URL；图片支持 PNG、JPEG、WebP、GIF。单个附件默认最大 `10MB`，图片默认最多 8 张，实际限制由服务端配置决定。

成功返回：

```json
{
  "session_id": "demo-session",
  "hermes_session_id": "20261008_example",
  "user_id": "guest",
  "answer": "Hermes 的最终回答内容"
}
```

返回字段：

| 字段 | 说明 |
|---|---|
| `session_id` | Gateway 对外会话 ID，后续请求继续使用 |
| `hermes_session_id` | Hermes 内部会话 ID，仅用于排查 |
| `user_id` | 会话所属用户 |
| `answer` | Hermes 返回的最终文本 |

## 7. 打开报告页面

### `GET /v1/reports/{filename}/{download_token}`

当聊天回复生成 HTML 排查报告时，`answer` 中的文件地址会自动转换成 HTTPS 报告页面链接。直接访问该链接即可打开页面，不需要额外携带 Gateway Key。

| 参数 | 位置 | 必填 | 说明 |
|---|---|---:|---|
| `filename` | Path | 是 | 原始 HTML 报告文件名，必须与访问凭证对应 |
| `download_token` | Path | 是 | 服务端生成的随机访问凭证，直接使用返回链接中的值 |

回答示例：

```json
{
  "session_id": "demo-session",
  "hermes_session_id": "20261008_example",
  "user_id": "guest",
  "answer": "详细排查报告：\nhttps://athena.agoralab.co/tools2/hermes-gateway/v1/reports/argus-investigation-example.html/<download_token>"
}
```

请求示例：

在浏览器中打开：

`https://athena.agoralab.co/tools2/hermes-gateway/v1/reports/argus-investigation-example.html/<download_token>`

成功返回 `200`，响应内容为 HTML 文件，浏览器会直接打开 HTML 页面。链接默认有效期 7 天；不存在或过期时返回 `404`。持有链接即可访问，请按需分享。报告发布失败时，回答显示“报告暂不可用”，其余结论仍正常返回。

## 8. 常见错误

| 状态码 | 说明 |
|---:|---|
| `400` | 会话 ID、附件地址、附件格式或服务端模型配置不合法 |
| `401` | Gateway Key 缺失或错误 |
| `404` | 指定会话不存在或不属于当前用户，或报告链接无效/过期 |
| `409` | 会话用户不匹配，或请求被同一会话的新请求替代 |
| `422` | 请求体结构、字段长度、附件数量或字段名称不符合要求 |
| `502` | Hermes 执行失败、超时或不可用 |

当前部署的 Hermes 等待上限为 `1200` 秒，由服务端配置决定；调用方的 HTTP 客户端超时应覆盖该时间。

错误响应通常为 `{"detail": "错误说明"}`；参数校验失败时 `detail` 可为数组，执行失败时可为对象。
