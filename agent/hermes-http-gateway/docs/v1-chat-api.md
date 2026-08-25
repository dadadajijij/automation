# v1/chat 接口文档

本文档只说明 HTTP 网关对外聊天接口：

- `POST /v1/chat`

该接口最终会调用 Hermes CLI。外部调用方只需要维护网关暴露的 `session_id`，不需要直接处理 Hermes 内部的 `hermes_session_id`。

## 1. 基本概念

| 名称 | 外部是否可传 | 来源 | 作用 |
|---|---:|---|---|
| `session_id` | 可选 | 外部传入，或网关自动生成 | 网关对外暴露的会话 ID，用于创建会话和续聊 |
| `hermes_session_id` | 否 | Hermes 首轮调用后返回 | Hermes 内部会话 ID，网关续聊时用它调用 `hermes chat --resume` |
| `user_id` | 可选 | 外部传入，或网关默认 `guest` | 网关侧用户隔离字段，不同用户不能互相读取会话 |
| `profile` | 可选 | 外部传入、用户默认 profile、或网关默认 profile | Hermes profile，用于选择本会话的 Hermes 运行配置 |

## 2. 鉴权

如果服务端配置了 `GATEWAY_API_KEY`，调用 `/v1/chat` 时必须带其中一种鉴权头。

方式一：

```http
X-Gateway-Key: <gateway_api_key>
```

方式二：

```http
Authorization: Bearer <gateway_api_key>
```

如果没有配置 `GATEWAY_API_KEY`，则不校验服务鉴权。

## 3. POST /v1/chat

### 3.1 作用

主聊天接口。既可以创建新会话，也可以续聊已有会话。

### 3.2 何时创建新会话

- 请求体不传 `session_id`
- 请求体传了 `session_id`，但数据库里不存在该会话

### 3.3 何时续聊

- 请求体传了 `session_id`
- 数据库里已经存在该会话
- 本次显式传入的 `user_id`、`profile` 没有和原会话冲突

### 3.4 请求示例

```bash
curl -X POST "http://127.0.0.1:8011/v1/chat" \
  -H "Content-Type: application/json" \
  -H "X-Gateway-Key: your_gateway_key" \
  -d '{
    "question": {
      "text": "如何分析音画不同步问题"
    },
    "user_id": "alice",
    "profile": "default"
  }'
```

## 4. 请求 Body 参数

| 参数 | 必填 | 类型 | 限制 | 默认/继承逻辑 | 作用 |
|---|---:|---|---|---|---|
| `question` | 是 | object | 不允许多余字段 | 无 | 本轮问题对象 |
| `question.text` | 条件必填 | string | 最长 20000 字符 | 有附件时可不传或传空字符串；无附件时必须非空 | 本轮用户问题文本，会作为 prompt 主体发给 Hermes |
| `question.attachments` | 否 | array | 最多 8 个附件 | 默认空数组 | 附件列表，支持 `type=image` 和 `type=file` |
| `user_id` | 否 | string | 最长 120 字符 | 不传时默认 `guest`；如果是已有会话且不传，则继承原会话 `user_id` | 用户隔离字段 |
| `session_id` | 否 | string | 最长 120 字符，且需满足 session_id 正则 | 不传时网关自动生成；传了但不存在则创建；传了且存在则续聊 | 外部会话 ID |
| `profile` | 否 | string | 最长 120 字符 | 新会话不传时使用用户默认 profile，再 fallback 到网关默认 profile；已有会话不传时继承原会话 profile | Hermes profile |

请求体不允许传入未定义字段。多余字段会触发请求校验错误。会话标题由网关根据 `question.text` 自动生成，外部不再传 `title`。

如果 `question.attachments` 非空，`question.text` 可以不传或传空字符串。此时网关会只用附件路径生成 Hermes prompt，会话标题使用 `附件请求`。

## 5. session_id 传与不传的完整逻辑

### 5.1 不传 session_id

适用于创建全新会话。

处理流程：

1. 网关自动生成新的外部 `session_id`。
2. 解析 `user_id`：
   - 传了 `user_id`：使用传入值。
   - 没传 `user_id`：使用 `guest`。
3. 解析 `profile`：
   - 传了 `profile`：使用传入值。
   - 没传 `profile`：读取该用户默认 profile。
   - 用户没有默认 profile：使用网关默认 profile，默认是 `default`。
4. 创建网关会话记录。
5. 保存本轮用户消息。
6. 调用 Hermes 创建内部会话。
7. 从 Hermes usage 信息里读取 `hermes_session_id`。
8. 保存助手回复和 `hermes_session_id`。
9. 返回本轮结果。

### 5.2 传 session_id，且数据库不存在

适用于外部系统希望自己指定会话 ID 的新会话。

处理流程：

1. 校验 `session_id` 格式。
2. 使用传入的 `session_id` 创建网关会话。
3. `user_id`、`profile` 按新会话规则解析。
4. 调用 Hermes 创建内部会话。
5. 保存 Hermes 返回的 `hermes_session_id`。
6. 返回本轮结果。

### 5.3 传 session_id，且数据库已存在

适用于续聊。

处理流程：

1. 校验 `session_id` 格式。
2. 查询已有会话。
3. 如果本次没有传 `user_id`，继承原会话 `user_id`。
4. 如果本次传了 `user_id`，必须等于原会话 `user_id`，否则返回 `409`。
5. 如果本次没有传 `profile`，继承原会话 `hermes_profile`。
6. 如果本次传了 `profile`，必须等于原会话 `hermes_profile`，否则返回 `409`。
7. 使用原会话保存的 `hermes_session_id` 调用 Hermes 续聊。
8. 保存本轮用户消息和助手回复。
9. 返回本轮结果。

### 5.4 同 session_id 并发请求

同一个 `user_id + session_id` 如果前一次请求还没有完成，后一次请求会替代前一次请求，并以最新请求为准。

如果前一次请求还在下载图片或文件附件：

1. 后一次请求先等待前一次请求附件下载结束。
2. 下载成功的附件会进入该 session 的待提交附件列表。
3. 后一次请求把这些待提交附件合并进自己的 prompt、`--image` 和 `--attachment-root`。
4. Hermes 成功返回后，这些待提交附件才会被标记为已消费。

如果前一次请求已经启动 Hermes 子进程：

1. 后一次请求会切换请求版本。
2. 网关会终止旧 Hermes 进程。
3. 前一次请求发现自己已经过期后停止写入结果，并返回 `409`。
4. 后一次请求继续执行，并最终写入会话历史。

## 6. user_id 逻辑

`user_id` 是网关侧隔离用户的字段。

| 场景 | 结果 |
|---|---|
| 新会话传了 `user_id` | 使用传入值 |
| 新会话没传 `user_id` | 使用 `guest` |
| 已有会话没传 `user_id` | 继承该会话原来的 `user_id` |
| 已有会话传了相同 `user_id` | 正常续聊 |
| 已有会话传了不同 `user_id` | 返回 `409` |

注意：`user_id` 不传时默认值是 `guest`。但如果传入的是已有 `session_id`，网关会优先继承会话里已经保存的用户，所以返回的 `user_id` 可能不是 `guest`。

## 7. profile 逻辑

`profile` 用来指定 Hermes profile。

| 场景 | 结果 |
|---|---|
| 新会话传了 `profile` | 使用传入值 |
| 新会话没传 `profile`，用户设置了默认 profile | 使用用户默认 profile |
| 新会话没传 `profile`，用户也没有默认 profile | 使用网关默认 profile，默认是 `default` |
| 已有会话没传 `profile` | 继承该会话原来的 `hermes_profile` |
| 已有会话传了相同 `profile` | 正常续聊 |
| 已有会话传了不同 `profile` | 返回 `409` |

## 8. attachments 参数

### 8.1 请求结构

```json
{
  "question": {
    "text": "请分析这些图片",
    "attachments": [
      {
        "url": "https://example.com/image-1.png",
        "type": "image"
      },
      {
        "url": "https://example.com/image-2.jpg",
        "type": "image"
      }
    ]
  },
  "session_id": "demo-session",
  "user_id": "alice",
  "profile": "default"
}
```

### 8.2 字段说明

| 参数 | 必填 | 类型 | 限制 | 作用 |
|---|---:|---|---|---|
| `question.attachments[].url` | 是 | string | 1 到 2048 字符 | 在线附件 URL |
| `question.attachments[].type` | 是 | `image` 或 `file` | 只能取这两个值 | 附件类型 |

### 8.3 image 支持情况

当前已经支持多图片。

默认限制：

| 项 | 默认值 | 环境变量 |
|---|---:|---|
| 单次最多图片数 | 8 | `ATTACHMENT_MAX_IMAGES` |
| 单张图片最大体积 | 10MB | `ATTACHMENT_MAX_BYTES` |
| 下载超时 | 30 秒 | `ATTACHMENT_DOWNLOAD_TIMEOUT_SECONDS` |
| 跳转次数 | 3 | `ATTACHMENT_REDIRECT_LIMIT` |
| 临时保存根目录 | `/tmp/hermes` | `ATTACHMENT_STORAGE_ROOT` |
| 临时文件保留天数 | 3 天 | `ATTACHMENT_RETENTION_DAYS` |

图片 URL 要求：

- 只支持 `http` / `https`
- 域名必须能解析到公网地址
- 不允许 localhost、内网地址、link-local 地址、metadata 地址等非公网地址
- 支持 `png`、`jpeg/jpg`、`webp`、`gif`
- 网关会按图片文件魔数识别真实格式，不只依赖 URL 后缀

图片下载后保存到：

```text
/tmp/hermes/<YYYY-MM-DD>/<session_id>/<sha256>.<ext>
```

示例：

```text
/tmp/hermes/2026-08-25/demo-session/1f2a...9c.png
```

发送给 Hermes 时，网关会同时做两件事：

- 按顺序追加多个 `--image <local_image_path>`
- 在 prompt 里追加 `附件图片` 段落，每张图片以 `@file:<local_image_path>` 形式列出

超过保留天数的日期目录会在处理附件时自动清理。

### 8.4 file 支持情况

当前支持 `type=file`。网关会先把文件下载到 session 临时目录，然后在发给 Hermes 的 prompt 里追加 `@file:<local_path>` 引用，并通过 `--attachment-root` 把 Hermes 的文件读取范围限制在该 session 目录内。

请求示例：

```json
{
  "question": {
    "text": "请分析这个文件",
    "attachments": [
      {
        "url": "https://example.com/a.txt",
        "type": "file"
      }
    ]
  }
}
```

文件下载后保存到：

```text
/tmp/hermes/<YYYY-MM-DD>/<session_id>/<sha256>.<ext>
```

文本文件会由 Hermes 的 `@file:` 机制展开为上下文。二进制文件不会被硬塞成文本，Hermes 会收到文件路径、类型、大小等提示，后续可按工具能力处理。

## 9. 底层 Hermes 调用参数

网关会根据是否是新会话、是否带图片，选择不同的 Hermes CLI 调用方式。

### 9.1 新会话，无图片

```bash
hermes -p <profile> -z <question> --usage-file <usage_json_path>
```

如果网关配置了 `DEFAULT_MODEL`：

```bash
hermes -p <profile> -m <DEFAULT_MODEL> -z <question> --usage-file <usage_json_path>
```

如果网关同时配置了 `DEFAULT_MODEL` 和 `DEFAULT_PROVIDER`：

```bash
hermes -p <profile> -m <DEFAULT_MODEL> --provider <DEFAULT_PROVIDER> -z <question> --usage-file <usage_json_path>
```

注意：如果只配置了 `DEFAULT_PROVIDER`，但没有配置 `DEFAULT_MODEL`，接口会返回 `400`，因为当前代码要求 `DEFAULT_PROVIDER` 必须配合 `DEFAULT_MODEL` 使用。

### 9.2 新会话，带图片或文件

带图片或文件时，首轮也会走 `hermes chat`。图片按顺序追加多个 `--image`，并在 prompt 里追加 `@file:` 图片路径；文件会变成 prompt 里的 `@file:` 引用，并追加 `--attachment-root`。

```bash
hermes -p <profile> chat -Q -q <question> --usage-file <usage_json_path> \
  --attachment-root <session_attachment_dir> \
  --image <local_image_path_1> \
  --image <local_image_path_2>
```

### 9.3 续聊，无图片

```bash
hermes -p <profile> chat --resume <hermes_session_id> -Q -q <question> --usage-file <usage_json_path>
```

### 9.4 续聊，带图片或文件

```bash
hermes -p <profile> chat --resume <hermes_session_id> -Q -q <question> --usage-file <usage_json_path> \
  --attachment-root <session_attachment_dir> \
  --image <local_image_path_1> \
  --image <local_image_path_2>
```

## 10. 成功响应

### 10.1 响应示例

```json
{
  "session_id": "demo-session",
  "hermes_session_id": "01K3ATEXAMPLEHERMESSESSION",
  "user_id": "alice",
  "hermes_profile": "default",
  "answer": "Hermes 返回的回答内容"
}
```

### 10.2 字段说明

| 字段 | 说明 |
|---|---|
| `session_id` | 网关对外会话 ID，外部后续续聊继续传这个值 |
| `hermes_session_id` | Hermes 内部会话 ID，通常只供排查使用，外部无需直接传 |
| `user_id` | 本会话归属用户 |
| `hermes_profile` | 本会话实际使用的 Hermes profile |
| `answer` | Hermes 返回的助手回答 |

注意：附件信息、消息落库记录、会话详情和 usage 仍会在网关内部保存或用于排查，但 `/v1/chat` 成功响应不再直接返回这些内部字段。

## 11. 常见错误

| HTTP 状态码 | 触发场景 | detail |
|---:|---|---|
| `400` | `session_id` 格式不合法 | `session_id must match ^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$` |
| `400` | 只配置了 `DEFAULT_PROVIDER`，没有配置 `DEFAULT_MODEL` | `DEFAULT_PROVIDER requires DEFAULT_MODEL` |
| `400` | 图片数量超过限制 | `only <N> image attachment(s) are supported` |
| `400` | 附件 URL 不是公网 http/https 地址 | 具体错误信息由下载校验返回 |
| `401` | `GATEWAY_API_KEY` 校验失败 | `Unauthorized` |
| `409` | 已有会话显式传入了不同的 `user_id` | `session already belongs to a different user` |
| `409` | 已有会话显式传入了不同的 `profile` | `session already belongs to a different Hermes profile` |
| `409` | 同一 `user_id + session_id` 的旧请求被新请求替代 | `request superseded by a newer request` |
| `502` | Hermes CLI 执行失败 | `Hermes invocation failed` |
| `502` | Hermes 首轮没有返回内部 session_id | `Hermes did not return a session_id` |

## 12. curl 示例

### 12.1 新建会话，由网关生成 session_id

```bash
curl -X POST "http://127.0.0.1:8011/v1/chat" \
  -H "Content-Type: application/json" \
  -H "X-Gateway-Key: your_gateway_key" \
  -d '{
    "question": {
      "text": "如何分析音画不同步问题"
    },
    "user_id": "alice",
    "profile": "default"
  }'
```

### 12.2 新建会话，由外部指定 session_id

```bash
curl -X POST "http://127.0.0.1:8011/v1/chat" \
  -H "Content-Type: application/json" \
  -H "X-Gateway-Key: your_gateway_key" \
  -d '{
    "question": {
      "text": "如何分析音画不同步问题"
    },
    "session_id": "demo-session",
    "user_id": "alice",
    "profile": "default"
  }'
```

### 12.3 续聊时只传 question 和 session_id

只要 `session_id` 已存在，网关会自动继承原会话的 `user_id` 和 `profile`。

```bash
curl -X POST "http://127.0.0.1:8011/v1/chat" \
  -H "Content-Type: application/json" \
  -H "X-Gateway-Key: your_gateway_key" \
  -d '{
    "question": {
      "text": "继续分析，还有哪些排查步骤？"
    },
    "session_id": "demo-session"
  }'
```

### 12.4 多图片请求

```bash
curl -X POST "http://127.0.0.1:8011/v1/chat" \
  -H "Content-Type: application/json" \
  -H "X-Gateway-Key: your_gateway_key" \
  -d '{
    "question": {
      "text": "请对比这两张图片里的问题",
      "attachments": [
        {
          "url": "https://example.com/image-1.png",
          "type": "image"
        },
        {
          "url": "https://example.com/image-2.jpg",
          "type": "image"
        }
      ]
    },
    "session_id": "image-demo-session",
    "user_id": "alice",
    "profile": "default"
  }'
```

### 12.5 Authorization Bearer 鉴权

```bash
curl -X POST "http://127.0.0.1:8011/v1/chat" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_gateway_key" \
  -d '{
    "question": {
      "text": "Hello"
    },
    "user_id": "guest"
  }'
```

### 12.6 只有附件，不传 question.text

```bash
curl -X POST "http://127.0.0.1:8011/v1/chat" \
  -H "Content-Type: application/json" \
  -H "X-Gateway-Key: your_gateway_key" \
  -d '{
    "question": {
      "attachments": [
        {
          "url": "https://raw.githubusercontent.com/AgoraIO-Community/Agora-Electron-Quickstart/master/README.md",
          "type": "file"
        }
      ]
    },
    "session_id": "file-only-session",
    "user_id": "alice",
    "profile": "default"
  }'
```

## 13. 推荐调用方式

外部系统推荐只保存并传递网关的 `session_id`：

1. 第一次请求可以不传 `session_id`，使用返回里的 `session_id`。
2. 后续请求只传 `question.text` 和同一个 `session_id`。
3. 如果外部系统自己有会话 ID，也可以第一次就传自己的 `session_id`。
4. 续聊时不要重复传 `profile`，除非要显式校验本次 profile 是否和原会话一致。
5. 续聊时不要重复传 `user_id`，除非要显式校验本次 user 是否和原会话一致。
