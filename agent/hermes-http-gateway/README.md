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

## 入口规则

- `user_id` 放在请求 body 或 query 里；不传时默认 `guest`
- 可选 `GATEWAY_API_KEY`
- 管理页使用单独的管理员登录
- 新请求没有 `session_id` 时，网关会自动创建一个
- 同一 `session_id` 会复用同一个 Hermes 会话
- 同一 `user_id + session_id` 的新请求会替代旧的未完成请求；如果旧请求正在下载附件，会先等附件下载完成并合并到新请求
- 已存在的 `session_id` 会自动继承原会话的 `user_id` 和 `profile`
- 只有显式传入的 `user_id` 或 `profile` 与已有会话不一致时才会报错

## 运行

```bash
cd /Users/panweikui/Pwk-Coding/automation/agent/hermes-http-gateway
uv sync
```

编辑 `.env` 里的 `HERMES_BIN`，然后启动：

```bash
uv run hermes-http-gateway
```

启动时会自动读取同目录下的 `.env`。

Hermes 二进制会优先自动寻找：

1. `/Users/panweikui/Pwk-Coding/automation/agent/hermes-agent/.venv/bin/hermes`
2. `/Users/panweikui/Pwk-Coding/automation/agent/hermes-agent/hermes`
3. `PATH` 里的 `hermes`

## 接口

接口和 Hermes 参数的详细对应关系见 [docs/interface-hermes-mapping.md](./docs/interface-hermes-mapping.md)。
`POST /v1/chat` 的详细参数和传参逻辑见 [docs/v1-chat-api.md](./docs/v1-chat-api.md)。

### `POST /v1/chat`

创建或续聊一个会话。

```bash
curl -X POST http://127.0.0.1:8011/v1/chat \
  -H 'content-type: application/json' \
  -d '{"user_id":"alice","question":{"text":"帮我总结一下今天的重点"}}'
```

带图片 URL：

```bash
curl -X POST http://127.0.0.1:8011/v1/chat \
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
    "user_id": "alice",
    "profile": "default"
  }'
```

`question.attachments[].type=image` 会下载到 `/tmp/hermes/<YYYY-MM-DD>/<session_id>/`，并通过 `--image` 交给 agent；prompt 只追加图片数量的自然语言提示。
`question.attachments[].type=file` 会下载到 `/tmp/hermes/<YYYY-MM-DD>/<session_id>/`，并通过 Hermes 的 `@file:` 引用机制交给 agent。
有附件时，`question.text` 可以不传或传空字符串；无附件时必须传非空文本。
图片和文件默认只保留最近 3 天数据。

### `GET /v1/sessions`

列出当前用户的会话。

```bash
curl 'http://127.0.0.1:8011/v1/sessions?user_id=alice'
```

不传 `user_id` 时默认查询 `guest`。

### `GET /v1/sessions/{session_id}/messages`

查看指定会话历史。需要会话内搜索时，在同一个接口上追加 `q`：

```bash
curl 'http://127.0.0.1:8011/v1/sessions/demo-session/messages?user_id=alice&q=hello'
```

### `POST /v1/chat`

创建会话或向指定会话继续发送消息。指定会话时，把 `session_id` 放在请求体里。

```bash
curl -X POST http://127.0.0.1:8011/v1/chat \
  -H 'content-type: application/json' \
  -d '{"session_id":"demo-session","user_id":"alice","question":{"text":"继续聊这个话题"}}'
```

如果该 `session_id` 已经存在，也可以省略 `user_id` 和 `profile`：

```bash
curl -X POST http://127.0.0.1:8011/v1/chat \
  -H 'content-type: application/json' \
  -d '{"session_id":"demo-session","question":{"text":"继续聊这个话题"}}'
```

### `GET /v1/users/me`

查看当前用户的默认 Hermes profile。

### `GET /admin`

管理员网页，可以查看所有用户、会话、消息，并做全局搜索。会话详情按时间顺序展示每次请求：成功请求保留正常问答消息；被同一会话后续请求替代的调用显示 HTTP 409 和中断原因；Hermes 调用失败会显示失败状态与错误详情。

### `GET /admin/login`

管理员登录页，用户名默认预填。

### `GET /admin/chat`

管理员在线对话页，支持输入文本并直接上传本地图片或文件。页面通过管理员 Cookie 和 CSRF token 调用 `POST /admin/api/chat`，不会向浏览器暴露 `GATEWAY_API_KEY`。上传附件与 `POST /v1/chat` 的 URL 附件会复用同一套 Hermes 调用、会话和消息记录逻辑。

服务部署在反向代理子路径时，设置 `PUBLIC_BASE_PATH`。例如当前部署路径为 `/tools2/hermes-gateway`：

```dotenv
PUBLIC_BASE_PATH=/tools2/hermes-gateway
```

之后从 `https://athena.agoralab.co/tools2/hermes-gateway/admin/chat` 访问页面。

## 测试

```bash
cd /Users/panweikui/Pwk-Coding/automation/agent/hermes-http-gateway
.venv/bin/python -m unittest discover -s tests
```

## 认证

- 业务用户标识使用 `user_id`，POST 接口放 JSON body，GET 接口放 query
- `user_id` 不传时缺省值是 `guest`；已有会话命中时会继承原会话用户
- 如果设置了 `GATEWAY_API_KEY`，还需要 `X-Gateway-Key` 或 `Authorization: Bearer ...`
- 管理页和管理 API 需要先登录，账号密码来自 `ADMIN_USERNAME` / `ADMIN_PASSWORD`
- 管理员上传接口需要 `python-multipart`，已作为项目运行依赖声明
