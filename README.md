# Automation Workspace

这个仓库当前包含两条主线：

- 部署流水线：[`runner.sh`](./runner.sh) / [`runner.py`](./runner.py)
- 机器巡检：[`machine_health_check/`](./machine_health_check/)

其中 [`subpath/`](./subpath/) 是部署流水线内部使用的子路径适配库，相关实现说明在 [`doc/`](./doc/)。

## 部署流水线

### 当前能力

- 输入本地目录或 Git 仓库
- 复用项目级共享源码副本
- 生成或补齐 `Dockerfile` / `PROJECT_ONBOARDING.md`
- 执行子路径识别、源码改写、静态审计、运行时审计
- 可选执行 `podman build` / `podman run`
- 为 `/tools2/<project_slug>` 生成 nginx 片段，并在条件满足时尝试同步到 Athena nginx 配置

### 入口

外部入口是：

```bash
./runner.sh ...
```

`runner.sh` 会先：

- 尝试把 `codex` 放进 `PATH`
- 预读本机已有的 `docker.io/library/*` 基础镜像并缓存到 `codex-home-cache/local-official-images.txt`

随后执行：

```bash
python3 runner.py "$@"
```

### 常用命令

只生成文件：

```bash
./runner.sh --source-type local --source /path/to/project
```

Git 仓库，生成文件并构建镜像：

```bash
./runner.sh --source-type git --source https://github.com/example/repo.git --build
```

Git 仓库，生成文件、构建镜像并尝试运行：

```bash
./runner.sh --source-type git --source https://github.com/example/repo.git --build --run
```

指定已有项目端口覆盖时，也可以显式传入宿主机端口：

```bash
./runner.sh --source-type git --source https://github.com/example/repo.git --build --run --host-port 8010
```

### 私有仓库认证

- SSH URL 会直接复用当前机器的 SSH key / `ssh-agent` / `GIT_SSH_COMMAND`
- GitHub HTTPS URL 会先尝试等价 SSH URL，失败后再回退到 HTTPS + Token
- HTTPS 场景支持 `GITHUB_TOKEN`，也兼容 `GH_TOKEN`
- clone 阶段会启用非交互式 `GIT_ASKPASS`，并强制 `GIT_TERMINAL_PROMPT=0`

示例：

```bash
GITHUB_TOKEN=ghp_xxx ./runner.sh --source-type git --source https://github.com/owner/private-repo.git --ref main
```

### 目录结构

- `jobs/<project_slug>/repo`
  项目级共享源码副本
- `jobs/<project_slug>/repo-state.json`
  共享副本对应的本地内容签名或 Git 源签名
- `jobs/<project_slug>/<job_id>/work-repo`
  本次任务实际操作的私有工作副本
- `jobs/<project_slug>/<job_id>/output/`
  本次任务的日志与结果
- `jobs/<project_slug>/nginx-add.conf`
  当前项目对应的 nginx 片段
- `codex-home-cache/`
  共享的 Codex 静态缓存
- `project-ports.json`
  项目级运行时覆盖配置

### 当前执行逻辑

1. 解析参数，计算 `project_slug` 和 `job_id`，创建 `result.json`
2. 准备 `codex-home` 写入层和 Podman 环境
3. 准备或复用 `jobs/<slug>/repo`
4. 复制共享副本到 `jobs/<slug>/<job_id>/work-repo`
5. 对工作副本做结构化分析，写入 `analysis_summary`
6. 执行子路径 prepare 阶段
   - 构建 `SubpathPlan`
   - 做框架配置适配和源码改写
   - 跑静态审计
   - 有 finding 时尝试 auto-fix，再审一次
   - 仍有 finding 时直接失败为 `SUBPATH_STATIC_AUDIT_FAILED`
7. 判断是否复用已有 `Dockerfile` / `PROJECT_ONBOARDING.md`
   - 两者都存在且对 Git 源是 tracked 文件时，直接复用
   - 只有 `Dockerfile` 可复用时，只生成 `PROJECT_ONBOARDING.md`
   - 其他情况同时生成两者
8. 调用 `codex exec` 生成文件
   - 生成阶段有最多 2 次上游重试
   - 本地校验失败时，会带校验反馈再生成一次
9. 把最终生成的 `Dockerfile` / `PROJECT_ONBOARDING.md` 同步回共享副本
10. 如指定 `--build`，执行 `podman build`
11. build 成功后执行 build output subpath audit
   - 默认是 shadow mode，只记 artifact / warning
   - 只有 `.ka/subpath.json` 中显式把某类 finding 标成 `enforce` 时才阻断为 `SUBPATH_BUILD_OUTPUT_AUDIT_FAILED`
12. 如指定 `--run`，合并运行参数并启动容器
13. 健康检查通过后执行 runtime subpath audit
14. `RUN_SUCCEEDED` 后写出 nginx 片段，并尽力同步 Athena nginx 配置

### 结果与日志

每次任务都会在 `jobs/<slug>/<job_id>/output/` 下写结果：

- `result.json`
  当前完整状态快照；末尾还会写入 `final_result`
- `runner.log`
  主流程日志，以及所有外部命令的开始/结束/心跳
- `fetch.log`
  Git clone 日志，仅 Git 源场景存在
- `codex.log`
  `codex exec` 全量输出
- `codex-summary.txt`
  `codex exec --output-last-message` 的最终输出
- `build.log`
  `podman build` 输出
- `run.log`
  `podman run` 输出

最终 stdout 也会打印一份精简 JSON：

- `COMPLETED_WITHOUT_BUILD`
- `COMPLETED_WITH_BUILD`
- `RUN_SUCCEEDED`
- 失败态及 `errors`

### 运行时端口与环境变量

- `--run` 必须和 `--build` 一起使用
- 容器端口优先级：
  - `project-ports.json` 中已有映射
  - `PROJECT_ONBOARDING.md` 中解析出的确认端口
  - 镜像 `EXPOSE`
- 宿主机端口优先级：
  - `--host-port`
  - `project-ports.json` 中已有映射
  - 自动从 `8003` 开始分配空闲端口，并写回 `project-ports.json`
- `project-ports.json` 还支持：
  - `env`
  - `env_file`
  - `volumes`
- 运行时容器固定绑定到 `127.0.0.1:<host_port>:<container_port>`

### 子路径与 nginx

平台统一对外暴露路径：

```text
/tools2/<project_slug>
```

子路径改写只发生在 `work-repo`，不会修改原始上游目录。当前会把以下内容写入 `result.json` 的 `artifacts` 或 `analysis_summary`：

- `subpath_plan`
- `subpath_default_project`
- `subpath_detection_evidence`
- `subpath_rewrite_report`
- `subpath_static_audit`
- `subpath_build_output_audit`
- `subpath_runtime_audit`
- `.ka/subpath.json` 生效时的 `subpath_declaration`

`RUN_SUCCEEDED` 后会生成 `jobs/<slug>/nginx-add.conf`。如果本机存在 `/etc/nginx/sites-available/tools.conf`，runner 还会尝试：

- 备份原文件
- 以 `# BEGIN KA TOOL <slug>` block 形式写入或覆盖
- 执行 `nginx -t`
- reload nginx

这一步失败只记 warning，不会回滚已成功的容器运行结果。

### 本地源码与共享副本复用

- 本地源码会根据内容签名复用 `jobs/<slug>/repo`
- 目录名变化本身不会打破复用
- 当本地源码内容有变化，但共享副本里已有 `Dockerfile` 且新源码没有自带 `Dockerfile` 时，会把旧 `Dockerfile` 带到新的共享副本里
- Git 源刷新共享副本时，会删除 clone 出来的未跟踪 `Dockerfile` / `PROJECT_ONBOARDING.md`，避免把上一次生成物误当成源码的一部分

### 依赖与限制

- 依赖本机可用的 `codex`
- Git 源依赖本机可用的 `git`
- `--build` / `--run` 依赖本机可用的 `podman`
- `codex exec` 超时时间当前固定为 600 秒
- 容器就绪检查当前默认轮询 60 秒
- 运行成功后的健康检查仍是最小 HTTP 可用性检查，不是业务级 health check
- 受限沙箱里 rootless Podman 仍可能因为 `newuidmap`、user namespace 或只读运行目录失败；代码会先尝试默认模式，必要时回退到隔离的 Podman storage

## 机器巡检子项目

`machine_health_check/` 是独立的主机巡检工具，负责：

- 采集系统、CPU、内存、磁盘、网络指标
- 读取 `project-ports.json` 并对宿主机服务 / Podman 容器做健康检查
- 写 `reports/latest.json` 和 `reports/history/`
- 可选发送企业微信文本告警

本地执行：

```bash
cd machine_health_check
bash scripts/run_check.sh
```

相关文档：

- [`machine_health_check/docs/solution.md`](./machine_health_check/docs/solution.md)
- [`machine_health_check/docs/deploy.md`](./machine_health_check/docs/deploy.md)
- [`doc/SUBPATH.md`](./doc/SUBPATH.md)
- [`doc/DEPLOYMENT_FLOW.md`](./doc/DEPLOYMENT_FLOW.md)
