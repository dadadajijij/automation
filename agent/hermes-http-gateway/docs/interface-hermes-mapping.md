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
| `question` | `prompt` | 本轮输入内容 |
| `user_id` | 网关用户隔离 | 新会话不传时默认为 `guest`；已有 `session_id` 不传时继承原会话用户 |
| `session_id` | 网关内部会话映射 | 不直接传给 Hermes，用来找对应的 `hermes_session_id` 和原会话信息 |
| `profile` | `-p` | 新会话优先使用请求里的 `profile`；已有 `session_id` 不传时继承原会话 profile |
| `user.default_hermes_profile` | `-p` | 请求没传 `profile` 时使用 |
| `hermes_profile_default` | `-p` | 再往后的默认值 |
| `default_model` | `-m` | 服务端配置的默认模型，不允许外部请求覆盖 |
| `default_provider` | `--provider` | 服务端配置的 provider，不允许外部请求覆盖 |

实际行为：

- 首次会话：调用 `hermes ... -z <prompt>`
- 已有 Hermes 会话：调用 `hermes chat --resume <hermes_session_id> -Q -q <prompt>`

### `POST /v1/chat/{session_id}`

作用：
明确向指定会话续聊。

映射关系：

- 路径里的 `session_id`：网关内部会话 id
- body 里的 `user_id`：网关用户隔离字段；已有会话不传时继承原会话用户
- body 里的 `profile`：已有会话不传时继承原会话 profile
- `body.session_id`：如果传了，必须和路径一致
- 其余字段映射规则与 `POST /v1/chat` 相同

冲突规则：

- 已有 `session_id` 且显式传入的 `user_id` 和原会话不同：返回 `409`
- 已有 `session_id` 且显式传入的 `profile` 和原会话不同：返回 `409`
- 已有 `session_id` 但省略 `user_id` / `profile`：不会报错，自动使用原会话保存的信息

Hermes 实际调用：

```bash
hermes chat --resume <hermes_session_id> -Q -q <prompt>
```

## 2. 不调用 Hermes 的接口

这些接口只读写网关数据库或返回页面，不会触发 Hermes：

- `GET /health`
- `GET /v1/users/me`
- `PUT /v1/users/me`
- `GET /v1/sessions`
- `GET /v1/sessions/search`
- `GET /v1/sessions/{session_id}/messages`
- `GET /v1/sessions/{session_id}/search`
- `GET /admin/login`
- `POST /admin/login`
- `GET /admin/logout`
- `GET /admin`
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
