# 公共 v1 接口

本文档描述网关当前对外提供的前四个公共 `/v1` 接口：对话、能力列表，以及用户 Skills 绑定的查询和更新。

## 基础约定

- 基础地址由部署环境决定，例如 `https://example.com/hermes-gateway`。
- 如果配置了 `GATEWAY_API_KEY`，请求必须携带以下任一认证头：

  ```http
  X-Gateway-Key: <GATEWAY_API_KEY>
  ```

  或

  ```http
  Authorization: Bearer <GATEWAY_API_KEY>
  ```

- 未配置 `GATEWAY_API_KEY` 时，公共接口不要求认证头。
- JSON 请求使用 `Content-Type: application/json`。
- `user_id` 未提供时使用 `guest`。用户 ID 和 Skill ID 应使用非空、长度不超过 120 个字符的字符串。
- 常见错误响应格式为：

  ```json
  {"detail": "错误说明"}
  ```

## 1. 对话

### `POST /v1/chat`

创建会话或向已有会话继续发送消息。

#### 请求体

```json
{
  "user_id": "alice",
  "session_id": "demo-session",
  "question": {
    "text": "请总结今天的重点",
    "attachments": [
      {
        "url": "https://example.com/image.png",
        "type": "image"
      }
    ]
  }
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `question` | object | 是 | 提问内容。对象只接受 `text` 和 `attachments` 字段 |
| `question.text` | string | 条件 | 最多 20,000 个字符；没有附件时必须为非空文本 |
| `question.attachments` | array | 否 | 最多 8 个附件；有附件时 `text` 可为空 |
| `question.attachments[].url` | string | 是 | 附件 URL，长度 1–2,048 个字符 |
| `question.attachments[].type` | string | 是 | `image` 或 `file` |
| `user_id` | string | 否 | 用户标识；新会话缺省为 `guest` |
| `session_id` | string | 否 | 会话标识。格式为 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$`；不传则创建新会话 |

继续已有会话时，`session_id` 放在请求体中。若该会话属于其他用户，会返回 `409`。

#### 成功响应 `200 OK`

```json
{
  "session_id": "demo-session",
  "hermes_session_id": "hermes-abc123",
  "user_id": "alice",
  "answer": "今天的重点是……"
}
```

`answer` 可能包含已发布报告的外部访问链接。

#### 常见错误

| 状态码 | 说明 |
|---:|---|
| `400` | 配置或请求参数错误，例如默认 Provider 缺少 Model |
| `401` | Gateway API Key 缺失或错误 |
| `409` | 会话属于其他用户，或当前请求被同一会话的新请求替代 |
| `422` | 请求体字段不符合校验规则 |
| `502` | Hermes 调用失败 |

## 2. 获取可用 Skills

### `GET /v1/capabilities`

获取hermes的私有 Skills。私有Skills hermes默认无法使用，
只有通过后续`PATCH /v1/users/{user_id}/skills` 接口进行了私有skills绑定的user_id才能使用对应的私有skills

#### 请求示例

```bash
curl https://example.com/hermes-gateway/v1/capabilities \
  -H 'X-Gateway-Key: <GATEWAY_API_KEY>'
```

#### 成功响应 `200 OK`

```json
{
  "skills": [
    {
      "id": "summarize",
      "name": "summarize",
      "category": "客户私有",
      "description": "总结长文本",
      "version": "1.0.0",
      "enabled": true
    }
  ]
}
```

Skills 由 Hermes 动态提供，列表内容可能变化。只有 `enabled: true` 的 Skill 可以新增到用户绑定中。

### 返回字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `skills` | array | 私有 Skills 列表 |
| `skills[].id` | string | Skill ID，取 Hermes 返回的 `name` |
| `skills[].name` | string | Skill 展示名称；优先使用 `SKILL_NAMES_FILE` 中的中文映射，未匹配时使用 Hermes 原始 `name` |
| `skills[].category` | string | Skill 分类；网关会用分类映射文件转换为中文 |
| `skills[].description` | string | Skill 描述 |
| `skills[].version` | string | Skill 版本 |
| `skills[].enabled` | boolean | 是否启用 |

`category` 当前映射示例：

```json
{
  "customer-common": "客户通用",
  "customer-private": "客户私有",
  "general": "默认",
  "internal": "内部使用"
}
```

命中映射时返回中文；空分类保持空字符串；未命中的分类保留 Hermes 原始值。映射文件不存在、格式错误或无法读取时，接口仍然返回，分类保留原始值并记录日志。映射文件按修改时间缓存，更新后会自动重新加载。

`skills[].id` 始终保留 Hermes 返回的原始 `name`，用于 Skills 绑定和后续请求；`skills[].name` 仅用于展示。Skill 名称映射文件由 `SKILL_NAMES_FILE` 指定，默认路径为：

```text
/home/devops/ka/data/skills-manager/data/skill_names.json
```

例如：

```json
{
  "lre-issue-triage": "火花问题排查",
  "zhangmen-troubleshooting": "掌门问题排查"
}
```

名称映射未命中、文件不存在、格式错误或无法读取时，`name` 保留 Hermes 原始名称。映射文件按修改时间缓存，更新后会自动重新加载。

#### 常见错误

| 状态码 | 说明 |
|---:|---|
| `401` | Gateway API Key 缺失或错误 |
| `422` | 传入了已废弃的 `scope` 参数 |
| `503` | Hermes Skills 目录不可用；分类映射文件异常不会单独导致 `503` |

## 3. 查询用户 Skills 绑定

### `GET /v1/users/{user_id}/skills`

查询指定用户当前绑定的 Skills，并区分仍可用和已不可用的绑定。

#### 请求示例

```bash
curl https://example.com/hermes-gateway/v1/users/alice/skills \
  -H 'X-Gateway-Key: <GATEWAY_API_KEY>'
```

#### 成功响应 `200 OK`

```json
{
  "user_id": "alice",
  "bound_skill_ids": ["summarize", "legacy-tool"],
  "active_skill_ids": ["summarize"],
  "unavailable_skill_ids": ["legacy-tool"]
}
```

- `bound_skill_ids`：数据库中保存的全部绑定。
- `active_skill_ids`：绑定且当前目录中启用的 Skill。
- `unavailable_skill_ids`：仍保存绑定，但当前不可用或未启用的 Skill。

## 4. 更新用户 Skills 绑定

### `PATCH /v1/users/{user_id}/skills`

以增量方式添加或移除用户 Skills。空数组或空对象不会改变现有绑定。

#### 请求体

```json
{
  "add": ["summarize"],
  "remove": ["legacy-tool"]
}
```

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `add` | string[] | `[]` | 要新增的 Skill ID，最多 10 项；只能添加当前已启用的 Skill |
| `remove` | string[] | `[]` | 要移除的 Skill ID，最多 10 项；移除不存在或已失效的绑定不会报错 |

同一个 Skill ID 不能同时出现在 `add` 和 `remove` 中。用户最终最多绑定 10 个 Skill。

#### 成功响应 `200 OK`

响应结构与查询接口相同：

```json
{
  "user_id": "alice",
  "bound_skill_ids": ["summarize"],
  "active_skill_ids": ["summarize"],
  "unavailable_skill_ids": []
}
```

#### 常见错误

| 状态码 | 说明 |
|---:|---|
| `401` | Gateway API Key 缺失或错误 |
| `422` | 请求体格式错误、存在冲突 ID、添加了无效/未启用 Skill，或超过绑定上限 |
| `503` | Hermes Skills 目录不可用 |

