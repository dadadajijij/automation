# Phase 1 Automation

这个目录负责把源码准备、`Dockerfile` / `PROJECT_ONBOARDING.md` 生成、以及 Podman build/run 串成一条流水线。

## 文件

- `runner.py`：主控脚本
- `runner.sh`：shell 入口
- `generation_rules.md`：生成规则
- `templates/`：文档模板
- `jobs/`：共享源码工作区和单次执行产物

## 支持输入

- 本地目录
- Git 仓库 URL

## 私有仓库认证

- SSH 方式：
  - 如果 `--source` 使用 `git@github.com:owner/repo.git` 这类 SSH URL，则直接复用当前机器已配置的 SSH key / ssh-agent / `GIT_SSH_COMMAND`
- HTTPS + GitHub Token 方式：
  - 如果 `--source` 使用 `https://github.com/owner/repo.git` 这类 HTTPS URL，可在执行前传入 `GITHUB_TOKEN`
  - 对 GitHub HTTPS URL，脚本会先自动尝试等价的 SSH URL；如果 SSH 失败，再回退到 HTTPS + Token
  - 示例：

```bash
GITHUB_TOKEN=ghp_xxx ./automation/runner.sh --source-type git --source https://github.com/owner/private-repo.git --ref main
```

  - 也兼容 `GH_TOKEN`
  - 脚本内部会自动启用非交互式 `GIT_ASKPASS` 认证，并强制 `GIT_TERMINAL_PROMPT=0`，避免卡在用户名/密码输入

## 用法

本地目录，只生成文件：

```bash
./automation/runner.sh --source-type local --source /path/to/project
```

Git 仓库，生成文件并构建镜像：

```bash
./automation/runner.sh --source-type git --source https://github.com/example/repo.git --build
```

Git 仓库，生成文件、构建镜像并尝试运行：

```bash
./automation/runner.sh --source-type git --source https://github.com/example/repo.git --build --run
```

如果需要在 `RUN_SUCCEEDED` 时输出访问 URL，可额外传入：

```bash
./automation/runner.sh --source-type git --source https://github.com/example/repo.git --build --run --tool-id my-tool
```

当传入 `--tool-id` 时，runner 会先从共享源码副本复制出一个 job 私有工作副本，再在该私有副本中对前端绝对路径 API/静态资源引用做第一版子路径改写，并在 `automation/jobs/<项目名>/nginx-add.conf` 生成需要新增的 nginx 规则。对外访问路径不再使用 `tool-id`，统一改为 `tools2/<项目名>`。如果本机存在 `/etc/nginx/sites-available/athena.conf`，runner 只会在 `RUN_SUCCEEDED` 之后检查其中的 `athena.agoralab.co` 的 `443 ssl http2` server block：如果已存在该项目的 nginx 规则，则完全不做修改；只有在缺少该项目规则时，才会先通过 `sudo -n` 在同目录备份出 `/etc/nginx/sites-available/athena_<时间戳>.conf`，再把对应 `nginx-add.conf` 内容插入到 `client_max_body_size 100m;` 后面，并通过 `sudo -n` 执行配置读取、写回、`nginx -t` 与 reload；失败时只记 warning，不会让工具部署结果回滚为失败。

前端子路径改写只发生在 job 私有工作副本中，不会修改原始上游仓库；当前已覆盖根目录、`src/`、`static/`、`public/`、`client/`、`web/` 等常见前端目录，并会对 `window.location.origin` / `window.location.href` 一类运行时跳转逻辑做部署时补丁，使其优先使用 `window.__TOOL_BASE_PATH__`。

对于 Node 静态站点项目，runner 还会尝试从入口脚本里自动识别真实静态根目录，例如 `express.static(path.join(__dirname, "../src"))`、`serveStatic(...)`、`koaStatic(...)`，并以识别出的静态根为准做资源路径改写，减少对固定目录命名的依赖。

本地源码场景下，项目名优先从源码元数据读取，例如 `package.json` 的 `name` 或 `pyproject.toml` 的 `[project].name`。如果拿不到元数据，则回退到目录名归一化；像 `agora-token-generator 2`、`agora-token-generator-2` 这类本地副本目录会统一识别为 `agora-token-generator`。

## 目录结构

- `automation/jobs/<项目名>/repo`：按项目隔离的共享源码工作区
- `automation/jobs/<项目名>/repo-state.json`：按项目隔离的共享源码状态
- `automation/jobs/<项目名>/<job_id>`：该项目下单次执行产物，主要包含 `output/` 和 `codex-home/`
- `automation/codex-home-cache`：共享静态 Codex 缓存
- `automation/project-ports.json`：项目名到端口映射关系，格式为 `"项目名": "宿主机端口:容器端口"`，每个项目只保留一个运行容器

## 执行流程

1. 准备或复用 `automation/jobs/<项目名>/repo`
2. 准备最小化 `codex-home` 写入层，并复用共享静态缓存
3. 使用 `codex exec` 生成 `Dockerfile` 和 `PROJECT_ONBOARDING.md`
4. 写入 `runner.log`、阶段日志和 `result.json`
5. 如果指定 `--build`，执行 `podman build`
6. 如果指定 `--run`，优先结合 `PROJECT_ONBOARDING.md` 和镜像 inspect 自动确定端口并运行容器

## 运行细节

- 默认 job 目录名为 Unix 时间戳
- 不同项目会按项目名隔离到 `automation/jobs/<项目名>/...`
- 本地源码输入在内容未变化时会复用对应项目下的 `automation/jobs/<项目名>/repo`；本地临时解压路径变化本身不会打破复用
- 当本地源码内容有变化但源码里仍未自带 `Dockerfile` 时，runner 会优先沿用 `automation/jobs/<项目名>/repo` 下已有的 `Dockerfile`，避免再次调用 `codex exec` 重新生成同一份 Dockerfile
- 如果源码仓库里已经存在 `Dockerfile`，则只补生成 `PROJECT_ONBOARDING.md`，不会重写现有 `Dockerfile`
- 如果 `Dockerfile` 已存在但 `PROJECT_ONBOARDING.md` 缺失，runner 仍会调用一次 `codex exec`，但只生成 `PROJECT_ONBOARDING.md`
- `codex exec` 阶段每 10 秒会在 `runner.log` 写一次心跳；超过 10 分钟仍未完成会判定超时，并仅终止当前任务自己拉起的 `codex exec` 进程组
- Podman 构建和运行默认直接使用宿主机当前的 Podman 环境与默认镜像仓库
- `--run` 默认按 `project-ports.json` 为每个项目分配固定映射；若项目不在映射表中，会从宿主机端口 `8003` 开始向后查找空位（`8003`、`8004`、`8005` ...）并写回 `"宿主机端口:容器端口"`
- `--run` 当前以 detached 模式启动容器，宿主机端口仅绑定 `127.0.0.1`，并轮询 `http://127.0.0.1:<host_port>/` 做最小可用性检查

## 已知限制

- 当前依赖本机可用的 `codex` 和 `podman`
- Git 源场景还依赖本机可用的 `git`
- 某些受限沙箱里 rootless Podman 仍可能因为 `newuidmap` 或 user namespace 限制而失败，这类情况需要在沙箱外重试
- `--run` 当前只做最小 HTTP 可用性检查，未实现更细粒度的业务级 health check
