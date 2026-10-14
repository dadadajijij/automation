# Athena Skills Manager

共享 Skills 管理工具。接口通过 Athena 尝试识别操作人，页面不展示用户信息；Athena 暂时不可用时，服务继续提供本地 Skills 管理能力，修改记录会使用 `anonymous` 作为操作人。

## 本地运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn skill_manager.app:app --reload
```

生产环境将 `/tools2/skill-manager/` 反向代理到容器的 8000 端口，并将 `/data` 挂载为持久化卷。`ATHENA_ME_URL` 可通过环境变量覆盖。

## 页面能力

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

删除后重新导入会从历史最高版本继续编号，避免覆盖原有快照。导入和在线编辑将 Skill 文件、版本记录及快照一起提交，失败则回滚；数据库暂时被占用时返回 HTTP 503 和 `Retry-After`，稍后可重试。

页面按仓库浏览方式管理 Skill：左侧可以搜索和选择 Skill，详情区域显示文件目录树，文本文件支持源码查看和编辑，Markdown 支持预览，图片和 PDF 支持直接预览，其他二进制文件提供下载。Skill 可以通过本地 ZIP、在线 ZIP 链接或 Git 仓库导入；一个来源中包含多个 `SKILL.md` 时会自动批量识别并导入。导入同名 Skill 会生成新版本并覆盖当前文件内容。

在线 ZIP 导入接口：

```http
POST /api/skills/import-url
Content-Type: application/json

{
  "url": "https://example.com/my-skill.zip"
}
```

也可以提供可选的文件名，用于压缩包没有顶层目录、只能从文件名推导 Skill 名称的情况：

```json
{
  "url": "https://example.com/download?id=123",
  "filename": "my-skill.zip"
}
```

在线地址必须最终返回 ZIP 文件，压缩包仍然必须包含 `SKILL.md`。服务会限制下载大小、校验重定向，并默认拒绝解析到本机、内网或其他非公网地址的域名。公司内部域名可以通过环境变量显式放行：

```bash
SKILL_URL_ALLOWED_HOSTS=bitbucket.agoralab.co,git.agoralab.co
```

Git 仓库导入接口：

```http
POST /api/skills/import-git
Content-Type: application/json

{
  "url": "https://bitbucket.agoralab.co/scm/AI/agora-skills.git",
  "ref": "main"
}
```

Git 导入不再接收目录参数，始终扫描整个仓库并批量导入所有 Skill。服务支持标准 Git clone 地址，也会解析常见的 Bitbucket `projects/.../repos/.../browse` 和 GitHub `tree` 链接；链接中的子目录不限制扫描范围。`ref` 可选，优先使用显式分支，其次使用浏览链接中的分支，两者均未指定时使用仓库默认分支。Git 导入使用服务端已有的 Git 凭据，客户不需要在请求中传 Token。

扫描时先定位 `SKILL.md`，再读取对应 Skill 目录内的资源；非 Skill 文件不计入资源大小和数量限制。根目录含 `SKILL.md` 时，优先使用 YAML frontmatter 的 `name`，未声明时使用仓库名。其他 Skill 继续使用目录名。同名 Skill 更新，重复名称、嵌套 Skill 和 Skill 内的符号链接会被拒绝；远端删除的 Skill 不会自动删除本地记录。

本地 ZIP 上传、在线 ZIP 导入和 Git 导入都返回批量结果：

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
