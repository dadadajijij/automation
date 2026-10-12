# v1/chat 接口文档

本文档描述 HTTP 网关当前对外聊天接口：

- `POST /v1/chat`

调用方只需要保存和传递网关返回的 `session_id`。`hermes_session_id` 是 Hermes CLI 内部会话 ID，网关会持久化并在续聊时自动使用，外部一般不需要处理。

完整的对外 `/v1` 接口目录见 [v1-api-reference.md](./v1-api-reference.md)；包含管理员接口的完整内部目录见 [api-reference.md](./api-reference.md)。

## 1. 接口概览

`POST /v1/chat` 同时承担两类行为：

- 不传 `session_id`：创建新网关会话，网关自动生成外部 `session_id`。
- 传 `session_id`：如果会话已存在则续聊；如果不存在则用该 ID 创建新会话。

请求和响应都是 JSON。

最小请求：

```bash
curl -X POST "http://127.0.0.1:8003/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "question": {
      "text": "如何分析音画不同步问题"
    }
  }'
```

成功响应只返回当前调用方需要的核心字段：

```json
{
  "session_id": "demo-session",
  "hermes_session_id": "01K3ATEXAMPLEHERMESSESSION",
  "user_id": "alice",
  "answer": "Hermes 返回的回答内容"
}
```

## 2. 鉴权

如果服务端配置了 `GATEWAY_API_KEY`，请求必须带其中一种鉴权头：

```http
X-Gateway-Key: <gateway_api_key>
```

或：

```http
Authorization: Bearer <gateway_api_key>
```

没有配置 `GATEWAY_API_KEY` 时，`/v1/chat` 不校验服务鉴权。

## 3. 请求 Body

请求体不允许传入未定义字段。`question` 必须是对象，不支持旧版字符串写法；附件必须放在 `question.attachments`，不支持顶层 `attachments`。

| 参数 | 必填 | 类型 | 限制 | 当前行为 |
|---|---:|---|---|---|
| `question` | 是 | object | 不允许多余字段 | 本轮问题对象 |
| `question.text` | 条件必填 | string | 默认 `""`，最长 20000 字符 | 无附件时必须包含非空白文本；有附件时可省略或传空字符串 |
| `question.attachments` | 否 | array | 默认 `[]`，最多 8 项 | 支持 `image` 和 `file`，由网关下载后传给 Hermes |
| `question.attachments[].url` | 是 | string | 1 到 2048 字符 | 只支持公网 `http` / `https` URL |
| `question.attachments[].type` | 是 | string | `image` 或 `file` | 决定下载校验和传给 Hermes 的方式 |
| `user_id` | 否 | string | 最长 120 字符 | 新会话不传或传空白时使用 `guest`；已有会话不传时继承原会话用户 |
| `session_id` | 否 | string | 最长 120 字符；非空传入时去除首尾空白后必须匹配 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$` | 外部会话 ID；不传或传空字符串时自动生成 |

会话标题由网关根据 `question.text` 自动生成：非空文本最多保留 80 字符；只有附件且没有文本时标题为 `附件请求`。外部不能传 `title`。

## 4. 会话规则

### 4.1 创建新会话

以下情况会创建新会话：

- 请求不传 `session_id`。
- 请求传了 `session_id`，但数据库里不存在该会话。

新会话的解析顺序：

1. `session_id`：不传或传空字符串则生成 UUID；传入非空值则校验格式后直接使用。
2. `user_id`：传入非空白值则使用去除首尾空白后的值；否则使用 `guest`。
3. 网关固定使用 Hermes `default` profile，创建 `conversations` 记录，保存用户消息，调用 Hermes 首轮，再保存助手消息和 `hermes_session_id`。

### 4.2 续聊已有会话

请求传入的 `session_id` 命中已有会话时，网关按已有会话续聊：

1. 如果没有显式传 `user_id`，继承原会话 `user_id`。
2. 如果显式传了 `user_id`，必须等于原会话 `user_id`，否则返回 `409`。
3. 网关使用原会话保存的 `hermes_session_id` 调用 `hermes chat --resume`，并统一使用 `default` profile。

续聊时调用方推荐只传：

```json
{
  "session_id": "demo-session",
  "question": {
    "text": "继续分析，还有哪些排查步骤？"
  }
}
```

### 4.3 并发与替代

同一个 `user_id + session_id` 同时收到多次请求时，后来的请求会替代尚未完成的旧请求。

- 如果旧请求还在下载附件，新请求会先等待下载结束。旧请求已下载成功的附件会进入待提交列表，并合并进新请求的 prompt、`--image` 和 `--attachment-root`。
- 如果旧请求已经启动 Hermes 子进程，新请求会切换请求版本并终止旧进程。旧请求停止写入结果并返回 `409`，新请求继续执行。
- 附件只有在某次 Hermes 调用成功后才会从待提交列表中清除。

## 5. 附件规则

附件下载到服务端本地临时目录，再交给 Hermes。默认保存路径：

```text
/tmp/hermes/<YYYY-MM-DD>/<session_id>/<sha256>.<ext>
```

默认限制：

| 项 | 默认值 | 环境变量 |
|---|---:|---|
| 单次附件总数 | 8 | 请求模型固定限制 |
| 单次最多图片数 | 8 | `ATTACHMENT_MAX_IMAGES` |
| 单个附件最大体积 | 10MB | `ATTACHMENT_MAX_BYTES` |
| 下载超时 | 30 秒 | `ATTACHMENT_DOWNLOAD_TIMEOUT_SECONDS` |
| 跳转次数 | 3 | `ATTACHMENT_REDIRECT_LIMIT` |
| 临时保存根目录 | `/tmp/hermes` | `ATTACHMENT_STORAGE_ROOT` |
| 临时文件保留天数 | 3 天 | `ATTACHMENT_RETENTION_DAYS` |

URL 校验：

- 只支持 `http` / `https`。
- 域名必须能解析到公网地址。
- 不允许 localhost、内网地址、link-local 地址、metadata 地址等非公网地址。

`type=image` 的额外规则：

- 支持 `png`、`jpeg/jpg`、`webp`、`gif`。
- 会检查响应 `Content-Type` 和图片文件魔数，不能只靠 URL 后缀伪装。
- 传给 Hermes 时，图片按顺序追加为多个 `--image <local_image_path>`。
- 有图片附件时同样会追加 `--attachment-root <session_attachment_dir>`。
- prompt 里会追加自然语言提示，例如 `本轮请求包含 2 张图片附件，请结合随请求传入的图片进行回答。`；图片路径不会以 `@file:` 形式注入 prompt。

`type=file` 的行为：

- 网关按附件大小和 URL 安全规则下载文件，不限制为纯文本。
- 文件扩展名优先来自 URL 路径；没有安全扩展名时根据 `Content-Type` 猜测；仍无法识别时使用 `.bin`。
- prompt 里会追加 `附件文件：` 段落，每个文件以 `@file:<local_file_path>` 引用。
- 传给 Hermes 时会追加 `--attachment-root <session_attachment_dir>`，把文件读取范围限制在该会话附件目录。

附件请求示例：

```bash
curl -X POST "http://127.0.0.1:8003/v1/chat" \
  -H "Content-Type: application/json" \
  -H "X-Gateway-Key: your_gateway_key" \
  -d '{
    "question": {
      "text": "请对比图片并分析文件",
      "attachments": [
        {
          "url": "https://example.com/image-1.png",
          "type": "image"
        },
        {
          "url": "https://example.com/log.txt",
          "type": "file"
        }
      ]
    },
    "session_id": "demo-session",
    "user_id": "alice"
  }'
```

只有附件、没有文本也允许：

```json
{
  "question": {
    "attachments": [
      {
        "url": "https://example.com/report.pdf",
        "type": "file"
      }
    ]
  }
}
```

## 6. 传给 Hermes 的 prompt

网关保存到消息表里的用户消息内容是原始 `question.text`。实际 CLI prompt 会在本轮问题前加入 `[HTTP Gateway report delivery]` 交付说明，并按需追加附件引用。交付说明要求报告 Skill 只生成、验证并保留本地 HTML，不执行 StaticSpaces 发布，也不输出 HTML 源码或发布凭据错误；Gateway 在回答返回后生成报告页面链接。首轮和续聊都带该说明，不改写此前的会话历史或缓存系统提示词。

以下示例展示本轮问题及附件段落，省略固定的网关交付说明：

无附件时：

```text
<question.text>
```

有图片时：

```text
<question.text>

本轮请求包含 1 张图片附件，请结合随请求传入的图片进行回答。
```

有文件时：

```text
<question.text>

附件文件：
- @file:/tmp/hermes/<YYYY-MM-DD>/demo-session/<sha256>.txt
```

如果 `question.text` 为空但有附件，本轮用户问题部分只包含附件段落，CLI prompt 仍包含固定的网关交付说明。

## 7. Hermes CLI 调用

网关会在 Hermes 命令前追加服务端配置：

- `-p default`：业务请求固定使用 Hermes `default` profile。
- `-m <DEFAULT_MODEL>`：配置了 `DEFAULT_MODEL` 时追加，外部请求不能覆盖。
- `--provider <DEFAULT_PROVIDER>`：配置了 `DEFAULT_PROVIDER` 时追加，外部请求不能覆盖。

如果只配置了 `DEFAULT_PROVIDER` 但没有配置 `DEFAULT_MODEL`，`/v1/chat` 会返回 `400`。

网关会读取当前用户在管理员页面绑定的 private skills。只有仍存在于 Hermes 私有目录且处于启用状态的绑定，才会追加为重复的 `-s <skill_id>` 参数。调用方不能在 `/v1/chat` 请求体中直接指定 Skills。

私有目录由以下命令读取，并在网关进程内缓存 45 秒：

```bash
hermes -p default skills list --scope private --json
```

目录读取失败时，聊天仍会继续，但本轮不传 private skills，管理员请求时间线会记录该阶段错误。

Hermes 运行环境还会默认设置：

- `HERMES_YOLO_MODE=1`，除非进程环境里已有该变量。
- `HERMES_SESSION_SOURCE=<HERMES_SOURCE_TAG>`，默认 `tool`。

### 7.1 首轮纯文本

只有在首轮没有附件、没有待提交附件，并且最终 prompt 不包含 `@` 时，才走纯文本快捷调用：

```bash
hermes -p default -z <prompt> --usage-file <usage_json_path>
```

### 7.2 首轮带附件或 attachment root

首轮只要有图片、文件、待提交附件，或最终 prompt 包含 `@`，就走 `hermes chat`：

```bash
hermes -p default chat -Q -q <prompt> --usage-file <usage_json_path> \
  --attachment-root <session_attachment_dir> \
  --image <local_image_path_1> \
  --image <local_image_path_2>
```

没有图片时不会追加 `--image`。没有附件但 prompt 包含 `@` 时，`--attachment-root` 使用该 session 的附件目录。

### 7.3 续聊

续聊始终使用 Hermes 内部会话 ID：

```bash
hermes -p default chat --resume <hermes_session_id> -Q -q <prompt> --usage-file <usage_json_path>
```

如果本轮有附件或 prompt 需要 attachment root，会追加：

```bash
--attachment-root <session_attachment_dir>
```

如果本轮有图片，会按顺序追加：

```bash
--image <local_image_path_1> --image <local_image_path_2>
```

## 8. 成功响应

| 字段 | 说明 |
|---|---|
| `session_id` | 网关对外会话 ID。后续续聊继续传这个值 |
| `hermes_session_id` | Hermes 内部会话 ID。通常只用于排查 |
| `user_id` | 本会话归属用户 |
| `answer` | Hermes stdout 去除首尾空白后的内容；已识别的 HTML 报告本地路径会替换为 HTTPS 报告页面链接 |

响应不会返回附件详情、消息详情、会话详情或 usage。相关信息仍会在网关内部保存或用于排查。

两个聊天入口会识别 `REPORT_SOURCE_ROOT`（默认 `~/.hermes/cache/documents`）下明确给出的单个 `.html` 文件，将副本保存到 `REPORT_STORAGE_ROOT`（默认 `./data/reports`），再把路径替换为 `${REPORT_PUBLIC_BASE_URL}/v1/reports/{filename}/{download_token}`。访问凭证无需额外鉴权头，默认保留 7 天；文件名不匹配或过期返回 `404`。HTML 以页面形式直接打开，历史消息保存同一个链接。

单个报告默认最大 10MB。文件不存在、超过限制或真实路径不在允许源目录内时，仅将报告路径替换为“报告暂不可用”，不丢弃聊天结论。

## 9. 常见错误

| HTTP 状态码 | 触发场景 | detail |
|---:|---|---|
| `400` | `session_id` 格式不合法 | `session_id must match ^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$` |
| `400` | 只配置了 `DEFAULT_PROVIDER`，没有配置 `DEFAULT_MODEL` | `DEFAULT_PROVIDER requires DEFAULT_MODEL` |
| `400` | 图片数量超过 `ATTACHMENT_MAX_IMAGES` | `only <N> image attachment(s) are supported` |
| `400` | 附件 URL、下载、大小、图片格式校验失败 | 具体错误信息由附件校验返回 |
| `401` | `GATEWAY_API_KEY` 校验失败 | `Unauthorized` |
| `409` | 已有会话显式传入了不同的 `user_id` | `session already belongs to a different user` |
| `409` | 旧请求被同一 `user_id + session_id` 的新请求替代 | `request superseded by a newer request` |
| `422` | 请求体结构或字段校验失败，例如缺少 `question`、多余字段、空文本且无附件、附件类型非法、字段超长 | FastAPI/Pydantic 校验详情 |
| `502` | Hermes CLI 执行失败、超时或二进制不存在 | `Hermes invocation failed` |
| `502` | Hermes 首轮成功但 usage 中没有返回内部 `session_id` | `Hermes did not return a session_id` |

Hermes 调用失败时，`detail` 是对象，包含 `message`、`error`、`stdout`、`stderr`、`returncode` 和网关 `session_id`。

## 10. 推荐调用方式

1. 首次请求可以不传 `session_id`，使用响应里的 `session_id` 作为后续会话 ID。
2. 如果外部系统已经有自己的会话 ID，也可以首次请求就传 `session_id`。
3. 续聊时只传 `session_id` 和 `question`；`user_id` 可省略或用于显式校验其与原会话一致。
4. 外部只需要持久化网关 `session_id`。`hermes_session_id` 可用于排查，但不作为请求参数传入。
