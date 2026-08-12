# 声网 SDK 问答 Agent 使用手册

本文档说明当前 MVP 版本 agent 的定位、启动方式、配置项、租户隔离规则和全部 HTTP 接口。

## 1. Agent 定位

当前 agent 是一个面向声网 SDK 问题的智能问答服务，主要帮助开发者解答声网 SDK 和声网文档相关问题。

回答范围包括：

- RTC、RTM、IM、互动直播。
- 云端录制、旁路推流、媒体处理。
- SDK 集成、API 使用、错误排查和最佳实践。
- 与声网官方文档站相关的问题：https://doc.shengwang.cn/

当前版本只做最小闭环：

- 多租户会话隔离。
- 会话创建和查询。
- 消息历史保存。
- 调用 LLM 生成回答。

当前版本暂不包含：

- 用户登录和鉴权。
- 知识库 RAG。
- 流式输出。
- 工具调用。
- 后台管理页面。

## 2. 启动服务

安装依赖：

```bash
npm install
```

准备配置：

```bash
cp .env.example .env
```

编辑 `.env`，填入真实 LLM 配置。

开发启动：

```bash
npm run dev
```

生产方式启动：

```bash
npm run build
npm start
```

默认服务地址：

```text
http://localhost:3001
```

## 3. 配置项

配置文件是 `.env`。

| 配置项 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `PORT` | 否 | `3001` | HTTP 服务端口。 |
| `DATABASE_URL` | 否 | `./data/qa-agent.sqlite` | SQLite 数据库文件路径。 |
| `LLM_MOCK` | 否 | `false` | 是否启用本地 mock 回复。只能是 `true` 或 `false`。 |
| `LLM_BASE_URL` | 否 | `https://sub2api.agoralab.co` | OpenAI-compatible API 网关地址。 |
| `LLM_API_KEY` | 是 | 无 | LLM API key。`LLM_MOCK=true` 时可以不填。 |
| `LLM_MODEL` | 否 | `gpt-5.5` | 使用的模型名称。 |
| `LLM_REASONING_EFFORT` | 否 | `high` | 推理强度，可选 `low`、`medium`、`high`。 |

mock 模式示例：

```env
LLM_MOCK=true
```

真实模型模式示例：

```env
LLM_MOCK=false
LLM_API_KEY=your-api-key
```

## 4. 租户隔离规则

除 `/health` 外，所有业务接口都必须传请求头：

```text
x-tenant-id: <tenant_id>
```

示例：

```text
x-tenant-id: customer_a
```

隔离行为：

- 创建会话时，会话会绑定当前 `x-tenant-id`。
- 查询会话列表时，只返回当前租户的会话。
- 查询消息历史时，只能查询当前租户自己的会话。
- 向其他租户的会话发送消息或查询历史，会返回 `404 Conversation not found`。

当前版本没有真实用户登录系统，`x-tenant-id` 只是最小 MVP 的租户标识。正式产品中应由认证系统在服务端解析租户身份，而不是信任客户端随意传入。

## 5. 通用错误

缺少 `x-tenant-id`：

```json
{
  "error": "Missing x-tenant-id header"
}
```

请求体不合法：

```json
{
  "error": "Invalid request",
  "issues": []
}
```

会话不存在或不属于当前租户：

```json
{
  "error": "Conversation not found"
}
```

服务端错误：

```json
{
  "error": "Internal server error"
}
```

## 6. 接口列表

### 6.1 健康检查

检查服务是否启动，以及当前配置的模型名称。

```text
GET /health
```

请求示例：

```bash
curl http://localhost:3001/health
```

响应示例：

```json
{
  "ok": true,
  "model": "gpt-5.5"
}
```

说明：

- 该接口不需要 `x-tenant-id`。
- 只表示服务可访问，不代表 LLM 网关一定可用。

### 6.2 创建会话

为当前租户创建一个新会话。

```text
POST /v1/conversations
```

请求头：

```text
content-type: application/json
x-tenant-id: customer_a
```

请求体：

```json
{
  "title": "Web RTC 集成问题"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `title` | string | 否 | 会话标题，最长 120 个字符。 |

请求示例：

```bash
curl -X POST http://localhost:3001/v1/conversations \
  -H 'content-type: application/json' \
  -H 'x-tenant-id: customer_a' \
  -d '{"title":"Web RTC 集成问题"}'
```

响应示例：

```json
{
  "conversation": {
    "id": "86b28cc7-6056-45e6-b4eb-b20f5ccb6276",
    "tenant_id": "customer_a",
    "title": "Web RTC 集成问题",
    "created_at": "2026-07-28T10:00:00.000Z",
    "updated_at": "2026-07-28T10:00:00.000Z"
  }
}
```

### 6.3 查询会话列表

查询当前租户的全部会话，按更新时间倒序返回。

```text
GET /v1/conversations
```

请求头：

```text
x-tenant-id: customer_a
```

请求示例：

```bash
curl http://localhost:3001/v1/conversations \
  -H 'x-tenant-id: customer_a'
```

响应示例：

```json
{
  "conversations": [
    {
      "id": "86b28cc7-6056-45e6-b4eb-b20f5ccb6276",
      "tenant_id": "customer_a",
      "title": "Web RTC 集成问题",
      "created_at": "2026-07-28T10:00:00.000Z",
      "updated_at": "2026-07-28T10:05:00.000Z"
    }
  ]
}
```

说明：

- 当前版本未实现分页。
- 当前版本不支持按标题搜索。

### 6.4 发送消息并获取回答

向指定会话发送用户问题，agent 会保存用户消息、读取最近 20 条上下文、调用 LLM，并保存 assistant 回复。

```text
POST /v1/conversations/:id/messages
```

路径参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 会话 ID。 |

请求头：

```text
content-type: application/json
x-tenant-id: customer_a
```

请求体：

```json
{
  "content": "Web 端集成声网 RTC SDK 时，如何初始化客户端并加入频道？"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `content` | string | 是 | 用户问题，最长 20000 个字符。 |

请求示例：

```bash
curl -X POST http://localhost:3001/v1/conversations/86b28cc7-6056-45e6-b4eb-b20f5ccb6276/messages \
  -H 'content-type: application/json' \
  -H 'x-tenant-id: customer_a' \
  -d '{"content":"Web 端集成声网 RTC SDK 时，如何初始化客户端并加入频道？"}'
```

响应示例：

```json
{
  "conversation_id": "86b28cc7-6056-45e6-b4eb-b20f5ccb6276",
  "message": {
    "id": "b9f3c858-8258-49b5-ac4a-cf7170b489d9",
    "tenant_id": "customer_a",
    "conversation_id": "86b28cc7-6056-45e6-b4eb-b20f5ccb6276",
    "role": "assistant",
    "content": "回答内容",
    "model": "gpt-5.5",
    "latency_ms": 2760,
    "created_at": "2026-07-28T10:05:00.000Z"
  }
}
```

说明：

- 当前接口是非流式返回，需要等 LLM 完整生成后才返回。
- 当前会自动保存用户消息和 assistant 回复。
- 当前上下文窗口取最近 20 条消息。
- 当前没有知识库检索，因此回答依赖模型已有知识和当前会话上下文。

### 6.5 查询消息历史

查询指定会话的完整消息历史。

```text
GET /v1/conversations/:id/messages
```

路径参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 会话 ID。 |

请求头：

```text
x-tenant-id: customer_a
```

请求示例：

```bash
curl http://localhost:3001/v1/conversations/86b28cc7-6056-45e6-b4eb-b20f5ccb6276/messages \
  -H 'x-tenant-id: customer_a'
```

响应示例：

```json
{
  "conversation": {
    "id": "86b28cc7-6056-45e6-b4eb-b20f5ccb6276",
    "tenant_id": "customer_a",
    "title": "Web RTC 集成问题",
    "created_at": "2026-07-28T10:00:00.000Z",
    "updated_at": "2026-07-28T10:05:00.000Z"
  },
  "messages": [
    {
      "id": "34591caf-e55e-43ed-8774-b82bb771dbd9",
      "tenant_id": "customer_a",
      "conversation_id": "86b28cc7-6056-45e6-b4eb-b20f5ccb6276",
      "role": "user",
      "content": "Web 端集成声网 RTC SDK 时，如何初始化客户端并加入频道？",
      "model": null,
      "latency_ms": null,
      "created_at": "2026-07-28T10:04:58.000Z"
    },
    {
      "id": "b9f3c858-8258-49b5-ac4a-cf7170b489d9",
      "tenant_id": "customer_a",
      "conversation_id": "86b28cc7-6056-45e6-b4eb-b20f5ccb6276",
      "role": "assistant",
      "content": "回答内容",
      "model": "gpt-5.5",
      "latency_ms": 2760,
      "created_at": "2026-07-28T10:05:00.000Z"
    }
  ]
}
```

说明：

- 当前版本返回完整历史，未分页。
- 如果当前租户访问其他租户的会话，会返回 `404`。

## 7. 推荐调用流程

一个客户端正常使用流程：

1. 调用 `POST /v1/conversations` 创建会话。
2. 保存返回的 `conversation.id`。
3. 调用 `POST /v1/conversations/:id/messages` 发送问题。
4. 展示返回的 `message.content`。
5. 需要恢复会话时，调用 `GET /v1/conversations/:id/messages`。
6. 需要展示历史列表时，调用 `GET /v1/conversations`。

## 8. 声网 SDK 问题提问建议

为了得到更准确的回答，建议用户问题包含：

- 使用的声网产品，例如 RTC、RTM、IM、云端录制。
- 平台，例如 Web、Android、iOS、macOS、Windows、Linux、Flutter、React Native。
- SDK 版本。
- 具体 API 名称、错误码或日志片段。
- 期望效果和实际现象。
- 相关代码片段。

示例问题：

```text
Web 端使用声网 RTC SDK 4.x 加入频道时报 INVALID_OPERATION，应该怎么排查？
```

```text
Android 端本地预览正常但远端看不到视频，RTC SDK 应该检查哪些 API 调用顺序？
```

## 9. 当前数据模型

当前使用 SQLite，包含两张表。

`conversations`：

| 字段 | 说明 |
|---|---|
| `id` | 会话 ID。 |
| `tenant_id` | 租户 ID。 |
| `title` | 会话标题。 |
| `created_at` | 创建时间。 |
| `updated_at` | 更新时间。 |

`messages`：

| 字段 | 说明 |
|---|---|
| `id` | 消息 ID。 |
| `tenant_id` | 租户 ID。 |
| `conversation_id` | 所属会话 ID。 |
| `role` | 消息角色，取值 `user`、`assistant`、`system`。 |
| `content` | 消息内容。 |
| `model` | assistant 消息使用的模型。 |
| `latency_ms` | assistant 回复耗时，单位毫秒。 |
| `created_at` | 创建时间。 |

## 10. 注意事项

- `.env` 包含真实密钥，不要提交。
- `.env.example` 只放占位配置。
- 当前版本没有 RAG，因此不会实时抓取 https://doc.shengwang.cn/ 的内容。
- 当前版本没有鉴权，不能直接暴露到公网。
- 正式产品中应添加用户系统、服务端租户解析、限流、审计、知识库检索和流式输出。
