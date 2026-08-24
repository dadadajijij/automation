# POST /v1/chat 接口说明

## 1. 接口作用

`POST /v1/chat` 是 HTTP 网关对外提供的主聊天接口。

它有两种使用方式：

- 不传 `session_id`：创建一个新会话，网关自动生成外部 `session_id`
- 传 `session_id`：先按该 `session_id` 查询已有会话；查到则续聊，查不到则用该 `session_id` 创建新会话

网关会维护两层会话 ID：

| 名称 | 来源 | 作用 |
|---|---|---|
| `session_id` | 外部请求传入，或网关自动生成 | 对外暴露的会话 ID |
| `hermes_session_id` | Hermes 首次调用后返回 | Hermes 内部续聊用的会话 ID |

外部调用方只需要关心 `session_id`。

## 2. 请求格式

```http
POST /v1/chat
Content-Type: application/json
```

如果服务端设置了 `GATEWAY_API_KEY`，还需要带服务鉴权：

```http
X-Gateway-Key: <gateway_api_key>
```

或：

```http
Authorization: Bearer <gateway_api_key>
```

## 3. Body 参数

| 参数 | 必填 | 类型 | 长度限制 | 缺省语义 | 作用 |
|---|---:|---|---|---|---|
| `question` | 是 | string | 1 到 20000 | 无 | 本轮用户问题，会作为 prompt 发给 Hermes |
| `session_id` | 否 | string | 最长 120 | 不传时网关自动生成 | 外部会话 ID，用于查找或创建会话 |
| `user_id` | 否 | string | 最长 120 | 不传默认值为 `guest` | 外部用户 ID，用于网关侧用户隔离 |
| `profile` | 否 | string | 最长 120 | 不传时按 profile 选择规则处理 | Hermes profile，用于决定 Hermes 运行配置 |
| `title` | 否 | string | 最长 120 | 根据 `question` 自动生成 | 会话标题，只用于网关数据库和管理员页面展示 |

注意：

- `user_id` 不传时的缺省值是 `guest`
- 如果传入的 `session_id` 已经存在，网关会优先继承该会话数据库里保存的 `user_id`
- 因此，已有会话续聊时不传 `user_id`，实际返回的 `user_id` 可能不是 `guest`，而是原会话用户

## 4. 不传 `session_id` 的完整逻辑

请求示例：

```json
{
  "question": "如何分析音画不同步问题",
  "user_id": "toolless",
  "profile": "tool_less"
}
```

处理流程：

1. 网关确认 body 里没有 `session_id`。
2. 网关自动生成新的外部 `session_id`。
3. 解析 `user_id`：
   - 传了 `user_id`：使用传入值
   - 没传 `user_id`：使用 `guest`
4. 解析 `profile`：
   - 传了 `profile`：使用传入值
   - 没传 `profile`：先读取该用户默认 profile
   - 用户没有默认 profile：使用网关默认 profile
5. 网关创建新的会话记录：
   - `external_session_id`
   - `user_id`
   - `hermes_profile`
   - `title`
   - `status`
6. 网关保存本轮用户消息。
7. 网关调用 Hermes 新建会话。
8. Hermes 返回 `hermes_session_id` 和回答。
9. 网关保存 `hermes_session_id`。
10. 网关保存助手回复。
11. 网关返回本轮结果。

底层 Hermes 调用形态：

```bash
hermes -p <profile> -z <question>
```

如果服务端配置了 `DEFAULT_MODEL`：

```bash
hermes -p <profile> -m <DEFAULT_MODEL> -z <question>
```

如果服务端同时配置了 `DEFAULT_MODEL` 和 `DEFAULT_PROVIDER`：

```bash
hermes -p <profile> -m <DEFAULT_MODEL> --provider <DEFAULT_PROVIDER> -z <question>
```

## 5. 传 `session_id` 的完整逻辑

请求示例：

```json
{
  "question": "继续分析",
  "session_id": "12a0015d-1428-4cd4-b3fc-4b0b33d33cd3"
}
```

处理流程：

1. 网关读取 body 里的 `session_id`。
2. 校验 `session_id` 格式。
3. 网关按 `session_id` 查询数据库里的已有会话。

### 5.1 查到已有会话

网关读取该会话保存的信息：

| 字段 | 来源 | 作用 |
|---|---|---|
| `user_id` | `conversations.user_id` | 原会话所属用户 |
| `hermes_profile` | `conversations.hermes_profile` | 原会话使用的 Hermes profile |
| `hermes_session_id` | `conversations.hermes_session_id` | Hermes 内部续聊 ID |

然后进行一致性判断：

| 本次请求参数 | 判断逻辑 |
|---|---|
| 未传 `user_id` | 继承原会话 `user_id` |
| 传了 `user_id` 且等于原会话 `user_id` | 正常续聊 |
| 传了 `user_id` 但不等于原会话 `user_id` | 返回 `409` |
| 未传 `profile` | 继承原会话 `hermes_profile` |
| 传了 `profile` 且等于原会话 `hermes_profile` | 正常续聊 |
| 传了 `profile` 但不等于原会话 `hermes_profile` | 返回 `409` |

如果已有 `hermes_session_id`，调用 Hermes 续聊：

```bash
hermes chat --resume <hermes_session_id> -Q -q <question>
```

如果历史数据里暂时还没有 `hermes_session_id`，调用 Hermes 新建会话：

```bash
hermes -p <profile> -z <question>
```

### 5.2 没查到已有会话

如果数据库里没有该 `session_id`，网关会把传入的 `session_id` 当作新的外部会话 ID 创建会话。

处理逻辑：

1. 使用请求里的 `session_id` 作为新会话的外部 ID。
2. 解析 `user_id`：
   - 传了 `user_id`：使用传入值
   - 没传 `user_id`：使用 `guest`
3. 解析 `profile`：
   - 传了 `profile`：使用传入值
   - 没传 `profile`：先使用用户默认 profile
   - 用户没有默认 profile：使用网关默认 profile
4. 创建新的网关会话记录。
5. 调用 Hermes 新建会话。
6. 保存 Hermes 返回的 `hermes_session_id`。
7. 保存本轮用户消息和助手回复。
8. 返回本轮结果。

## 6. 成功响应

成功响应主要字段：

```json
{
  "session_id": "12a0015d-1428-4cd4-b3fc-4b0b33d33cd3",
  "hermes_session_id": "hermes-internal-session-id",
  "user_id": "toolless",
  "hermes_profile": "tool_less",
  "answer": "Hermes 返回内容",
  "assistant_message": {},
  "conversation": {},
  "usage": {}
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `session_id` | 对外会话 ID，后续续聊继续传它 |
| `hermes_session_id` | Hermes 内部会话 ID，外部调用方一般不需要使用 |
| `user_id` | 本轮最终归属用户 |
| `hermes_profile` | 本轮最终使用的 Hermes profile |
| `answer` | Hermes 返回的回答文本 |
| `assistant_message` | 网关落库后的助手消息对象 |
| `conversation` | 网关落库后的会话对象 |
| `usage` | Hermes 返回的使用信息；没有时可能为 `null` |

## 7. 常见错误

| 状态码 | 场景 |
|---:|---|
| `400` | `session_id` 格式不合法 |
| `401` | 设置了 `GATEWAY_API_KEY`，但请求没传或传错 |
| `409` | 已有会话下，显式传入的 `user_id` 或 `profile` 与原会话不一致 |
| `422` | `question` 缺失、字段类型错误或字段长度超限 |
| `502` | Hermes CLI 调用失败 |

## 8. 调用示例

### 8.1 新建会话，不指定 `session_id`

```bash
curl -X POST http://127.0.0.1:8011/v1/chat \
  -H 'content-type: application/json' \
  -d '{
    "question": "如何分析音画不同步问题",
    "user_id": "toolless",
    "profile": "tool_less"
  }'
```

### 8.2 指定 `session_id` 创建或续聊

```bash
curl -X POST http://127.0.0.1:8011/v1/chat \
  -H 'content-type: application/json' \
  -d '{
    "question": "继续分析",
    "session_id": "12a0015d-1428-4cd4-b3fc-4b0b33d33cd3"
  }'
```

### 8.3 首次创建时显式绑定用户和 profile

```bash
curl -X POST http://127.0.0.1:8011/v1/chat \
  -H 'content-type: application/json' \
  -d '{
    "question": "如何分析音画不同步问题",
    "user_id": "toolless",
    "profile": "tool_less",
    "session_id": "12a0015d-1428-4cd4-b3fc-4b0b33d33cd3"
  }'
```

### 8.4 后续只传 `session_id` 和问题

如果上一步已经创建过会话，后续可以只传：

```bash
curl -X POST http://127.0.0.1:8011/v1/chat \
  -H 'content-type: application/json' \
  -d '{
    "question": "继续分析这个问题",
    "session_id": "12a0015d-1428-4cd4-b3fc-4b0b33d33cd3"
  }'
```

网关会自动继承原会话的 `user_id=toolless` 和 `profile=tool_less`。

## 9. 核心规则总结

| 场景 | 结果 |
|---|---|
| 不传 `session_id` | 网关自动生成新 `session_id` 并创建新会话 |
| 传了 `session_id` 且查到已有会话 | 续聊已有会话 |
| 传了 `session_id` 但没查到已有会话 | 用该 `session_id` 创建新会话 |
| 不传 `user_id` | 缺省值是 `guest`；但已有会话命中时继承原会话用户 |
| 已有会话显式传入不同 `user_id` | 返回 `409` |
| 不传 `profile` | 新会话使用用户默认 profile 或网关默认 profile；已有会话命中时继承原 profile |
| 已有会话显式传入不同 `profile` | 返回 `409` |
