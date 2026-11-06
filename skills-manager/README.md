# Athena Skills Manager

共享 Skills 管理工具。接口通过 Athena 尝试识别操作人，页面不展示用户信息；Athena 暂时不可用时，服务继续提供本地 Skills 管理能力，修改记录会使用 `anonymous` 作为操作人。

## 权限控制

服务固定将 `panweikui@agora.io` 视为管理用户。所有用户可创建、改名、删除自定义分类；六个固定节点不可删除或修改英文标识，其中文名仅管理员可改。普通分组下的 Skill 可单个编辑、改名、移动和删除；批量移动和批量删除仅管理员可用，批量下载对所有用户开放。

后端会独立校验管理权限，不能通过直接调用接口绕过页面限制。以下接口需要管理用户身份：

```text
POST   /api/skills/batch-category
POST   /api/skills/batch-delete
```

此外，`customer-common`、`current-common` 下的所有 Skill 写入（含导入覆盖、移入、移出）仅限管理员，其他用户只能查看和下载。页面使用 `GET /api/me` 的 `can_manage` 及分类/Skill 响应中的操作权限控制按钮。Athena 无法识别用户时，不授予任何管理员权限，普通分组及自定义分类仍可管理。

## 本地运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn skill_manager.app:app --reload
```

生产环境将 `/tools2/skills/` 反向代理到容器的 8000 端口，并将 `/data` 挂载为持久化卷。代理需要剥掉该路径前缀，并设置 `X-Forwarded-Prefix: /tools2/skills`；服务会将前缀注入页面，供 API、文件预览和下载使用。其他部署路径可设置对应的请求头。本地直接运行时不设置该请求头，页面使用 `/api/skills` 等根路径，无需额外配置。`ATHENA_ME_URL` 可通过环境变量覆盖。

## Skill 分类

分类最多两层，一级和二级分组都能直接存放 Skill。固定节点不可删除、改英文标识或更换父级；管理员可以修改其中文显示名：

页面“分类分组”右侧的 `+` 只创建一级分组；每个一级分组后的 `+` 只在该分组下创建二级分组，表单在对应组名下方展开。创建表单不提供层级切换，依次填写中文名称和英文标识。新建分组、分组改名、Skill 移动/改名和批量移动的输入选择框全局互斥，打开新操作会关闭旧框并清除其未提交输入。

| 标识 | 中文名 | 父级 | 相对 data/skills 的路径 |
|---|---|---|---|
| general | 默认 | 无 | general/ |
| internal | 内部 | 无 | internal/ |
| customer | 客户 | 无 | customer/ |
| customer-private | 客户私有 | customer | customer/customer_private/customer-private/ |
| customer-common | 客户通用 | customer | customer/customer_common/customer-common/ |
| current-common | 现有通用 | customer | customer/current_common/current-common/ |

自定义一级分组路径为 `<slug>/`；二级为 `<parent>/<slug 中连字符替换为下划线>/<slug>/`。
例如 `education/primary_school/primary-school/<skill-id>/`。中间下划线目录只用于磁盘组织，页面不显示第三层。分组标识全局唯一，仅允许小写字母、数字、连字符，不能由客户端指定磁盘路径或创建三级分组。

所有用户可以创建、改名和删除自定义分组，包括当前未获取到 Athena 身份的本地用户。删除不是删除 Skill：
- 创建某一级分组的首个二级分组时，父组所有直接 Skill 自动迁入新子组；之后创建其他子组不移动父组 Skill。
- 删除二级分组：Skill 移回父组。
- 删除自定义一级分组：直接及子组的所有 Skill 移到 general，同时删除其自定义子组。
- 两层都能作为移动目标；已有子组的一级分组仍可直接接收 Skill。
- 六个固定节点均不可删除。自定义分组改英文标识会同步搬移路径，并更新子组的父级引用；不支持更换父级。
- 目录重名、Skill 与子组容器重名、符号链接或非管理文件会拒绝操作，不覆盖目标数据。

`customer-common` 和 `current-common` 仅允许 `panweikui@agora.io` 修改内容、改名、删除、移入、移出，以及 ZIP/Git 同名覆盖。其他用户只能查看、预览和下载，身份查询失败也不授予写权限。普通分组保持单 Skill 写操作开放；批量移动和批量删除仍仅管理员可用。ZIP/Git 导入中新 Skill 默认进入 general，更新已有 Skill 保留分类和中文名；批量覆盖遇到无权更新项时整批回滚。Git 的 `overwrite=false` 会跳过已有项。

接口：
```http
GET    /api/categories
POST   /api/categories
PATCH  /api/categories/{slug}
DELETE /api/categories/{slug}
PATCH  /api/skills/{name}/category
POST   /api/skills/batch-category
```

创建一级分组传 `{"slug":"education","name_zh":"教育场景","parent_slug":null}`；
创建二级分组传 `{"slug":"primary-school","name_zh":"小学场景","parent_slug":"education"}`。
创建成功返回 `category`、`moved` 和 `moved_count`。删除返回 `removed_categories`、`moved`、`target_category`。
分类列表保持扁平数组，通过 `parent_slug` 表达层级；包含 `direct_skill_count`、含后代的 `skill_count`、`storage_relpath`、`is_system`、`is_protected` 及 `can_rename/can_delete/can_move_into`。Skill 列表与详情包含 `can_edit/can_rename/can_move/can_delete`，前端据此隐藏不可用操作，后端独立校验。

`data/category_names.json` 仍为 `{"customer-private":"客户私有"}` 的简单映射；
`data/skill_names.json` 仍为 `{"skill-id":"中文名称"}`，分类移动及删除分组不改变 Skill 名称映射。
两个文件都用原子替换导出，失败后自动重试，响应头分别是 `X-Category-Names-Sync` 与 `X-Skill-Names-Sync`。

版本归档仍按生成时的分类标识保存到 `data/archives/<category>/<skill-id>-vN.zip`，最多保留两个版本。分类移动、改名和删除不搬动历史归档，也不改写其真实路径或增加内容版本。Skill ID 全局唯一且不随分类改变。

### 升级迁移

升级时先停止旧服务，再用新代码启动。首次启动会将数据库、skills、archives 和两个名称映射备份到 `data/backups/category-tree-<时间>-<随机后缀>/`，再迁移原 `skills/customer-private/`、`skills/customer-common/`（及存在的 current-common）到新层级。保留 Skill ID、名称、内容和版本。迁移版本记在数据库中，重启不会重复迁移。目标路径存在非空数据时停止启动并报冲突，不覆盖。

分类操作采用 SQLite 写事务和持久目录搬移日志；异常时回滚，进程中断后启动会按提交记录恢复搬移。不要在运行中手动移动这些目录。历史备份不自动删除。回退时停止服务，将同一份备份中的数据库、skills、archives、名称映射恢复，并使用与备份匹配的旧代码；不要仅恢复数据库。

其他项目（例如 Hermes）如果配置了原目录，需要同步改为新的实际路径；本服务不修改用户的 Hermes 配置。目录层级不自动赋予客户身份隔离能力，customer-private 仍是分类名称。
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

新导入的 Skill 默认中文名等于 Skill ID；重新导入同名 Skill 会保留已有中文名。普通分组的所有用户都可以在 Skill 行的“改名”操作中修改中文名；`customer-common`、`current-common` 下的 Skill 只有 `panweikui@agora.io` 可以改名，身份查询不可用时不授予此权限。名称去除首尾空白后不能为空，最长 120 字符。Skill ID、磁盘路径、SKILL.md 和内容版本不会改变。也可以调用：

```http
PATCH /api/skills/{skill_name}/name
Content-Type: application/json

{"name_zh": "火花问题排查"}
```

`skill_names.json` 只保存全局唯一 Skill ID 到显示名称的映射，不含分类或磁盘路径。数据库是权威来源，其他项目只读 JSON；手动修改 JSON 不会覆盖数据库已有名称。旧数据名称为空时才从有效字符串映射补齐，缺少映射时使用 ID。

移动 Skill、分类改名和删除分类后迁移 Skill 都保留中文名称；删除 Skill 本身才移除映射。删除后重新导入按新 Skill 使用 ID 作为默认显示名，内容版本仍沿用历史编号。

导出使用 Linux/macOS 的文件锁、数据库写锁和同目录唯一临时文件原子替换。数据库变更通过触发器持久记录待同步状态。导出失败保留上一份完整 JSON，不撤销已提交的数据；下一次 API 请求、后台每 30 秒及重启时重试。API 响应头 `X-Skill-Names-Sync: synced|pending` 表示导出状态；`pending` 不表示业务操作失败，不应因此重复导入。页面会显示待同步提示。备份/迁移时一起保留数据库与名称 JSON，先完成数据库名称补齐再重新导出。

两层分类的移动、改名、删除流程与名称映射共用数据库中的 Skill ID；受保护分组的所有写入入口都在后端检查权限。

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

同时支持 SSH 简写 `git@github.com:JamieLiu67/orobas-agora-support.git` 和 `git@git.agoralab.co:ai/agora-skills.git`。后端验证主机后将简写地址原样交给 Git，保留相对路径及 SSH 配置语义，不强制改成绝对路径的 `ssh://` URL。两种 SSH 格式都使用服务运行机器的 SSH 密钥、agent 和 known_hosts；仍沿用地址白名单及非公网地址检查。

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
