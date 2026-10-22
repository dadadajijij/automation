# Athena Skills Manager

共享 Skills 管理工具。接口通过 Athena 尝试识别操作人，页面不展示用户信息；Athena 暂时不可用时，服务继续提供本地 Skills 管理能力，修改记录会使用 `anonymous` 作为操作人。

## 权限控制

服务固定将 `panweikui@agora.io` 视为管理用户。管理用户专属操作包括新建、改名和删除分类，以及批量移动和批量删除 Skill。普通用户可以查看和下载 Skill，也可以单个移动或删除 Skill；批量下载对普通用户开放。

后端会独立校验管理权限，不能通过直接调用接口绕过页面限制。以下接口需要管理用户身份：

```text
POST   /api/categories
PATCH  /api/categories/{category}
DELETE /api/categories/{category}
POST   /api/skills/batch-category
POST   /api/skills/batch-delete
```

页面通过 `GET /api/me` 返回的 `can_manage` 字段控制管理按钮显示。Athena 无法识别用户时，`can_manage` 为 `false`；单个 Skill 移动和删除接口仍可用。

## 本地运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn skill_manager.app:app --reload
```

生产环境将 `/tools2/skills/` 反向代理到容器的 8000 端口，并将 `/data` 挂载为持久化卷。代理需要剥掉该路径前缀，并设置 `X-Forwarded-Prefix: /tools2/skills`；服务会将前缀注入页面，供 API、文件预览和下载使用。其他部署路径可设置对应的请求头。本地直接运行时不设置该请求头，页面使用 `/api/skills` 等根路径，无需额外配置。`ATHENA_ME_URL` 可通过环境变量覆盖。

## Skill 分类

Skill 文件统一保存为：

```text
data/skills/<category>/<skill-name>/
```

系统会预置不可删除的 `general` 分类。所有新上传和 Git 导入的 Skill 默认进入：

```text
data/skills/general/<skill-name>/
```

页面左侧支持查看分类、按分类筛选、新建分类、单个移动和批量移动。新建分类使用页面内联表单；批量移动点击“批量移动”后才展开分类选择；单个 Skill 的移动选择器直接展开在对应 Skill 行内。分类标识只允许小写字母、数字和连字符，例如 `customer-private`。分类由管理页面控制，导入包中 `SKILL.md` 的 `category` 字段不会改变系统分类。

分类接口：

```http
GET /api/categories
POST /api/categories
PATCH /api/categories/{category}
DELETE /api/categories/{category}
PATCH /api/skills/{skill_name}/category
POST /api/skills/batch-category
```

创建分类时提供路径标识和中文名称。分类标识用于磁盘目录名：

```json
{
  "slug": "customer-private",
  "name_zh": "客户私有"
}
```

分类名称对照会自动写入 `data/category_names.json`，供其他项目读取：

```json
{
  "customer-private": "客户私有"
}
```

修改分类时可以同时修改 `slug` 和 `name_zh`。如果 `slug` 变化，服务会同步修改 `data/skills/<category>/`、`data/archives/<category>/` 和数据库中的分类引用；修改后的中文名会立即反映到页面。删除分类时，分类下的 Skill 会先移动到 `general`，随后删除该分类及其 JSON 对照信息。

移动 Skill：

```json
{
  "category": "customer-private"
}
```

`general` 不允许删除。分类移动只改变归属和目录路径，不增加 Skill 内容版本；版本快照保存于 `data/archives/<category>/`。

已有旧目录 `data/skills/<skill-name>` 会在服务启动时自动迁移到 `data/skills/general/<skill-name>`。迁移前应备份 `data/skill-manager.db`；如果目标目录已经存在，服务会停止迁移并报告冲突。

## 页面能力

Skill 描述优先使用 `SKILL.md` YAML frontmatter 中的非空字符串 `description`（支持 `>`、`|` 多行语法，保留解析后的完整内容）。没有声明、值为空或空白时，跳过标题等非正文内容，提取第一段正文，多行段落以空格连接；只有标题而没有正文时描述为空。导入和在线编辑使用同一规则，frontmatter 格式错误或非字符串描述会返回包含 Skill 名称的校验错误，批量导入不会部分写入。已有记录需重新导入或保存 `SKILL.md` 后才会更新描述。

左侧 Skills 列表支持独立勾选、多选和全选当前搜索结果。顶部的批量下载 Zip 和批量删除只操作勾选项，未勾选时禁用。取消勾选可点击对应复选框，或取消全选当前搜索结果。每个 Skill 行都有独立下载、删除按钮，默认隐藏，鼠标悬停或键盘聚焦时显示；行内操作只作用于对应 Skill，与勾选和当前打开的 Skill 无关。搜索不会清除已有勾选，操作区显示整个选择的数量；删除前会列出待删除的 Skill 进行确认。

批量接口：

```http
POST /api/skills/batch-download
Content-Type: application/json

{"names": ["skill-a", "skill-b"]}
```

下载返回 `skills.zip`，每个 Skill 保留独立顶层目录，可重新批量导入。只选一个时页面继续使用原有单 Skill 下载接口。

```http
POST /api/skills/batch-delete
Content-Type: application/json

{"names": ["skill-a", "skill-b"]}
```

成功返回 `{"ok": true, "deleted": ["skill-a", "skill-b"], "count": 2}`。两种批量接口都要求非空列表，最多 500 项，重复名称自动去重；名称无效或 Skill 不存在时返回错误。批量删除在写入前校验全部目标，失败会回滚已暂存的目录和数据库修改。删除保留历史版本 Zip 快照，当前没有页面恢复入口。

删除后重新导入会从历史最高版本继续编号，避免覆盖原有快照。每个 Skill 只保留最近两个版本的 Zip 快照和历史记录，生成新版本后自动清理更早版本；服务启动时也会清理已有超量快照。导入和在线编辑将 Skill 文件、版本记录及快照一起提交，失败则回滚；数据库暂时被占用时返回 HTTP 503 和 `Retry-After`，稍后可重试。

页面按仓库浏览方式管理 Skill：左侧可以搜索和选择 Skill，详情区域显示文件目录树，文本文件支持源码查看和编辑，Markdown 支持预览，图片和 PDF 支持直接预览，其他二进制文件提供下载。Skill 可以通过本地 Zip 压缩包或 Git 仓库导入；一个来源中包含多个 `SKILL.md` 时会自动批量识别并导入。导入同名 Skill 会生成新版本并覆盖当前文件内容。

每个 Skill 保留不可变的 Skill ID，同时支持独立的中文显示名。中文名保存在数据库 `skills.name_zh`，并同步导出到 `data/skill_names.json`，格式如下：

```json
{
  "lre-issue-triage": "火花问题排查",
  "zhangmen-troubleshooting": "掌门问题排查"
}
```

新导入的 Skill 默认中文名等于 Skill ID；重新导入同名 Skill 会保留已有中文名。所有用户都可以在 Skill 行的“改名”操作中修改中文名，Skill ID、磁盘路径和历史版本不会改变。也可以调用：

```http
PATCH /api/skills/{skill_name}/name
Content-Type: application/json

{"name_zh": "火花问题排查"}
```

Git 仓库导入接口：

```http
POST /api/skills/import-git
Content-Type: application/json

{
  "url": "ssh://git@git.agoralab.co/ai/agora-skills.git",
  "ref": "main"
}
```

页面导入私有 Git / Bitbucket 仓库时，建议填写 SSH 克隆地址，例如 `ssh://git@git.agoralab.co/ai/agora-skills.git`。HTTPS 地址如果经过 OAuth 登录网关，可能跳转到网页登录，Git 客户端无法完成该跳转。

Git 导入不再接收目录参数，始终扫描整个仓库并批量导入所有 Skill。服务支持标准 Git clone 地址，也会解析常见的 Bitbucket `projects/.../repos/.../browse` 和 GitHub `tree` 链接；链接中的子目录不限制扫描范围。`ref` 可选，优先使用显式分支，其次使用浏览链接中的分支，两者均未指定时使用仓库默认分支。Git 导入使用服务端已有的 Git 凭据，客户不需要在请求中传 Token。

私有 HTTP/HTTPS 仓库支持通过容器的 `env_file` 注入小写环境变量 `username` 和 `password`，两者必须同时设置。服务使用临时 `GIT_ASKPASS` 脚本提供认证信息，密码不拼入仓库地址，也不写入 Git 凭据文件；导入结束后临时脚本自动删除。仓库平台要求访问 Token 时，可将 Token 作为 `password`。未设置这两个变量时，继续使用本机已有的 Git 认证配置；SSH 仓库仍使用 SSH 认证。不要将真实账号密码提交到项目仓库。

当前部署使用 `ssh://git@git.agoralab.co/ai/agora-skills.git`，避免 HTTPS 入口的浏览器 OAuth 跳转。镜像安装 `openssh-client`；自动部署配置只读挂载服务器上已获仓库访问权限的 SSH 私钥及 `known_hosts`，并通过 `GIT_SSH_COMMAND` 设置 `BatchMode=yes`、`StrictHostKeyChecking=yes` 和指定密钥路径。SSH 导入不使用 `username`、`password`。本地运行继续使用本机 SSH 配置，仓库地址中不指定分支时使用默认分支。

扫描时先定位 `SKILL.md`，再读取对应 Skill 目录内的资源；非 Skill 文件不计入资源大小和数量限制。根目录含 `SKILL.md` 时，优先使用 YAML frontmatter 的 `name`，未声明时使用仓库名。其他 Skill 继续使用目录名。同名 Skill 更新，重复名称、嵌套 Skill 和 Skill 内的符号链接会被拒绝；远端删除的 Skill 不会自动删除本地记录。

本地 Zip 上传和 Git 导入都返回批量结果：

```json
{
  "summary": {"total": 2, "created": 1, "updated": 1, "skipped": 0, "failed": 0},
  "skills": [
    {"name": "skill-a", "status": "created", "version": 1},
    {"name": "skill-b", "status": "updated", "version": 2}
  ]
}
```

Skill 详情接口只返回文件元数据，不会把所有文件内容一次性读入内存。单个文件通过以下接口按需读取：

```text
GET /api/skills/{skill_name}/files/{path}
```

文件接口会原样返回文件内容，并校验文件路径必须位于对应 Skill 目录内。`SKILL.md`、Markdown、JSON、YAML、代码和纯文本文件会被标记为可编辑文本；图片、PDF 和其他二进制文件不会被当成 UTF-8 文本读取。
