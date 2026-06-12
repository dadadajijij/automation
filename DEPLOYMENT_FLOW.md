# Deployment Flow

本文梳理当前新项目在 `automation/runner.py` 中的一次完整部署流程，包括：

- 流程从哪里进入
- 每个阶段状态如何流转
- 每个阶段会写哪些关键日志
- 成功路径下哪些产物会落盘
- 失败时通常会停在哪一层

本文基于当前代码实现和真实成功任务日志整理：

- [runner.sh](/home/devops/ka/automation/runner.sh:1)
- [runner.py](/home/devops/ka/automation/runner.py:3307)
- 成功任务样例：
  - [api-examples-web result.json](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/result.json:1)
  - [api-examples-web runner.log](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/runner.log:1)
  - [audioqas result.json](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/result.json:1)
  - [audioqas runner.log](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/runner.log:1)

## 1. 总体入口

外部通常通过 `runner.sh` 启动，`runner.sh` 主要做两件事：

1. 确保 `codex` 在 `PATH` 中可用，见 [runner.sh](/home/devops/ka/automation/runner.sh:7)
2. 预读取本机已有的官方基础镜像列表，写入 `AUTOMATION_LOCAL_OFFICIAL_IMAGES` 和缓存文件，见 [runner.sh](/home/devops/ka/automation/runner.sh:31)

然后执行：

```bash
python3 runner.py "$@"
```

真正的部署主流程在 [runner.py `main()`](/home/devops/ka/automation/runner.py:3307)。

## 2. 关键目录和输出物

对一个项目 `project_slug` 和一次任务 `job_id`，关键目录如下：

- 项目目录：`automation/jobs/<project_slug>/`
- 共享源码目录：`automation/jobs/<project_slug>/repo`
- 本次任务目录：`automation/jobs/<project_slug>/<job_id>/`
- 本次任务输出目录：`automation/jobs/<project_slug>/<job_id>/output/`
- 会复制出独立工作副本：`automation/jobs/<project_slug>/<job_id>/work-repo`

典型输出物：

- `output/result.json`
- `output/runner.log`
- `output/fetch.log`
- `output/codex.log`
- `output/codex-summary.txt`
- `output/build.log`
- `output/run.log`

并且 `result.json` 中会维护完整状态快照，最终再生成 `final_result` 字段，见 [runner.py](/home/devops/ka/automation/runner.py:3268) 和 [runner.py](/home/devops/ka/automation/runner.py:3297)。

## 3. 状态流转总览

一次完整成功部署，请求参数通常是：

```bash
--source-type git --source <repo> --ref <branch> --build --run
```

成功路径上的主要状态流转如下：

1. `INITIALIZED`
2. `FETCHING_SOURCE`
3. `SOURCE_READY`
4. `GENERATING_FILES`
5. `FILES_GENERATED`
6. `BUILDING_IMAGE`
7. `BUILD_SUCCEEDED`
8. `STARTING_CONTAINER`
9. `WAITING_FOR_HEALTHCHECK`
10. `RUN_SUCCEEDED`

如果跳过某些阶段，也可能出现：

- `COMPLETED_WITHOUT_BUILD`
- `COMPLETED_WITH_BUILD`

非成功结束状态包括：

- `ARGUMENT_ERROR`：参数在真正建 job 前就失败，例如 `--run requires --build`
- `FETCH_FAILED`：Git 拉源码阶段抛出常规异常
- `SUBPATH_STATIC_AUDIT_FAILED`：静态子路径审计在自动修复后仍有问题
- `GENERATION_FAILED`：`codex exec` 非零退出，或生成阶段抛出常规异常
- `VALIDATION_FAILED`：`Dockerfile` / `PROJECT_ONBOARDING.md` 校验失败
- `BUILD_SKIPPED`：要求 build，但本机没有 `podman`
- `BUILD_FAILED`：`podman build` 返回非零
- `RUN_SKIPPED`：无法确定容器端口或无法分配宿主机端口
- `RUN_FAILED`：`podman run` 失败、健康检查失败，或启动阶段抛异常
- `SUBPATH_RUNTIME_AUDIT_FAILED`：服务已启动，但运行时子路径审计失败
- `FAILED`：未归类异常，或任一子命令超时

有两个容易误判的点：

- `BUILD_SUCCEEDED` 是中间态，不是 `--run` 场景下的最终成功态
- 任何子命令只要触发 `command_timeout`，最终都会统一收口为 `FAILED`，不会落到 `FETCH_FAILED` / `GENERATION_FAILED` / `BUILD_FAILED` 这类阶段化状态

## 4. 详细流程

### 4.1 参数解析与任务初始化

入口先解析参数，并做一个硬约束：

- `--run` 必须同时带 `--build`

逻辑位置：

- [runner.py](/home/devops/ka/automation/runner.py:3337)

初始化时完成：

- 计算 `project_slug`
- 计算 `job_id`
- 创建任务目录和输出目录
- 初始化 `result.json`
- 写入初始 artifacts

关键日志：

- `raw_runner_invocation`
- `runtime_environment`
- `job_initialized`
- `local_official_images`

示例：

- [api-examples-web runner.log](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/runner.log:1)
- [audioqas runner.log](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/runner.log:1)

对应代码：

- [runner.py](/home/devops/ka/automation/runner.py:3360)

### 4.2 准备源码

如果是 `git` 源：

- 状态先变为 `FETCHING_SOURCE`
- 调用 `prepare_shared_repo()`
- 若共享 repo 已命中相同签名，则复用已有 `jobs/<slug>/repo`
- 若未命中，则重新 clone 到临时目录后替换共享 repo

Runner 会从共享 repo 复制一份到 `work-repo`：

- 后续所有生成、构建、运行都基于 `work-repo`
- 并且在写入 `source_ready` 之前，还会先经过一轮静态子路径审计；这一步失败时会直接落到 `SUBPATH_STATIC_AUDIT_FAILED`

关键代码：

- [runner.py](/home/devops/ka/automation/runner.py:2525)
- [runner.py](/home/devops/ka/automation/runner.py:3400)

关键状态：

- `FETCHING_SOURCE`
- `SOURCE_READY`

关键日志：

- `fetch.log` 中的 `git_clone_strategy` / `git_clone_attempt` / `git_clone_retry` / `git_clone_transport_failed` / `git_clone_succeeded`
- `runner.log` 中的 `source_ready repo_dir=... reused_shared_repo=...`
- `runner.log` 中的 `analysis_summary summary=...`

示例：

- [api-examples-web runner.log](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/runner.log:5)
- [audioqas runner.log](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/runner.log:5)

失败与常见原因：

- Git 源的详细拉取过程主要在 `fetch.log`，不是 `runner.log`；因为 clone 阶段没有把流式输出写进 `runner.log`
- Git 源常规失败时，最终状态通常是 `FETCH_FAILED`，`errors` 固定为 `git clone failed`
- `warnings` 里可能会追加这些诊断语句：
  - `Git fetch failed because HTTPS authentication is required but no non-interactive credentials were provided.`
  - `Git fetch failed because the provided GitHub token is invalid or expired.`
  - `Git fetch failed because SSH authentication was attempted but the configured SSH key was not accepted.`
  - `Git fetch failed because DNS could not resolve github.com from the current environment.`
  - `Git fetch failed because the current environment could not establish an HTTPS connection to GitHub.`
  - `Git fetch hit an unstable HTTP/2 transport error; retrying with HTTP/1.1 is appropriate.`
  - `Git fetch failed during remote transfer; this is often a transient network or transport-layer issue.`
  - `Git fetch failed because the repository is private or unavailable to the current credentials.`
- 如果 `git clone` 触发的是命令级超时，而不是普通非零退出，最终状态会直接变成 `FAILED`，并在 `runner.log` 出现 `command_timeout`
- 本地源码场景的常见失败是源码路径不存在或源码路径不是目录，这类不会归到 `FETCH_FAILED`，而是直接进入 `FAILED`

### 4.3 仓库分析

源码准备好后，Runner 会立即做结构化分析：

- 运行时类型：`python` / `node` / `unknown`
- 包管理器
- 入口命令
- 端口
- 环境变量
- 系统依赖提示
- config / storage / database hints

分析结果：

- 写入 `result.json.analysis_summary`
- 也写入 `runner.log` 的 `analysis_summary summary=...`

关键代码：

- [runner.py `collect_repo_analysis()`](/home/devops/ka/automation/runner.py:1677)
- [runner.py `summarize_analysis()`](/home/devops/ka/automation/runner.py:1964)

成功样例：

- [audioqas result.json](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/result.json:24)

### 4.4 子路径改写

当前任务默认按平台工具部署处理，最终会挂在 `/tools2/<project_slug>` 子路径下：

- 会先识别项目所处的前端/静态类型，再决定具体的子路径适配方式
- 改写结果记录在 `artifacts.rewritten_frontend_files`
- 代理策略会记录在 `artifacts.subpath_proxy_mode`

这一步发生在源码就绪之后、文件生成之前。

关键代码：

- [runner.py `detect_subpath_strategy()`](/home/devops/ka/automation/runner.py:1888)
- [runner.py `apply_subpath_rewrites()`](/home/devops/ka/automation/runner.py:2234)
- [runner.py](/home/devops/ka/automation/runner.py:3155)

成功样例：

- [audioqas result.json](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/result.json:54)

### 4.4.1 子路径适配矩阵

当前实现已经不再只有 “Next.js 特判”，而是先识别项目类型，再决定：

- 走 `strip_prefix` 还是 `preserve_prefix`
- 用框架原生配置，还是继续用静态资源 rewrite

当前矩阵如下：

1. `nextjs`
   - 判定依据：
     - `package.json` 依赖里出现 `next`
     - 或存在 `next.config.js` / `next.config.mjs` / `next.config.ts`
   - 代理模式：`preserve_prefix`
   - 适配方式：
     - 自动补 `basePath`
     - 自动补 `assetPrefix`
   - 代码位置：
     - [runner.py `is_nextjs_project()`](/home/devops/ka/automation/runner.py:1841)
     - [runner.py `ensure_nextjs_basepath_config()`](/home/devops/ka/automation/runner.py:1900)

2. `vite`
   - 判定依据：
     - 依赖里出现 `vite`
     - 或存在 `vite.config.ts` / `vite.config.js` / `vite.config.mjs`
     - 或 scripts 中显式出现 `vite`
   - 代理模式：`preserve_prefix`
   - 适配方式：
     - 自动补 `vite.config.*` 中的 `base`
   - 代码位置：
     - [runner.py `is_vite_project()`](/home/devops/ka/automation/runner.py:1850)
     - [runner.py `ensure_vite_base_config()`](/home/devops/ka/automation/runner.py:1928)

3. `vue_cli`
   - 判定依据：
     - 依赖里出现 `@vue/cli-service`
     - 或存在 `vue.config.js`
   - 代理模式：`preserve_prefix`
   - 适配方式：
     - 自动补 `vue.config.js` 中的 `publicPath`
   - 代码位置：
     - [runner.py `is_vue_cli_project()`](/home/devops/ka/automation/runner.py:1869)
     - [runner.py `ensure_vue_cli_public_path()`](/home/devops/ka/automation/runner.py:1961)

4. `cra`
   - 判定依据：
     - 依赖里出现 `react-scripts`
   - 代理模式：`preserve_prefix`
   - 适配方式：
     - 自动补 `package.json.homepage`
   - 代码位置：
     - [runner.py `is_create_react_app_project()`](/home/devops/ka/automation/runner.py:1862)
     - [runner.py `ensure_cra_homepage()`](/home/devops/ka/automation/runner.py:1982)

5. `express_static`
   - 判定依据：
     - 能识别 Node 入口脚本
     - 且能从 `express.static(...)` / `serveStatic(...)` / `koaStatic(...)` 推断静态根目录
   - 代理模式：`strip_prefix`
   - 适配方式：
     - 使用当前静态 rewrite 方案
   - 代码位置：
     - [runner.py `is_express_static_project()`](/home/devops/ka/automation/runner.py:1878)

6. `static_html`
   - 判定依据：
     - 存在 `index.html` 或常见静态 HTML 文件
   - 代理模式：`strip_prefix`
   - 适配方式：
     - 使用当前静态 rewrite 方案
   - 代码位置：
     - [runner.py `is_static_html_project()`](/home/devops/ka/automation/runner.py:1884)

7. `generic`
   - 判定依据：
     - 以上都不命中
   - 代理模式：`strip_prefix`
   - 适配方式：
     - 退化到静态 rewrite 方案

可以把这个矩阵概括成一句话：

- 能通过框架原生配置显式声明子路径的项目，优先走 `preserve_prefix`
- 应用内部只理解根路径 `/`、需要靠外层代理“去前缀”的项目，走 `strip_prefix`

### 4.4.2 `strip_prefix` 与 `preserve_prefix` 的区别

`strip_prefix`

- 适用场景：
  - Express 静态托管
  - 纯静态 HTML
  - 无法确认支持原生子路径的通用项目
- 工作方式：
  - 用户访问 `/tools2/<slug>/...`
  - Nginx 负责把 `/tools2/<slug>` 前缀去掉
  - 容器内实际收到的是 `/...`
- 典型表现：
  - 会继续使用静态资源 rewrite
  - 无斜杠入口通常由 Nginx 301 到带斜杠路径

`preserve_prefix`

- 适用场景：
  - Next.js
  - Vite
  - Vue CLI
  - CRA
- 工作方式：
  - 用户访问 `/tools2/<slug>/...`
  - Nginx 把完整路径原样交给应用
  - 容器内实际收到的仍然是 `/tools2/<slug>/...`
- 典型表现：
  - 优先补框架原生 `basePath` / `assetPrefix` / `base` / `publicPath` / `homepage`
  - Nginx 不再做“补斜杠后再 strip”的旧逻辑
  - `location = /tools2/<slug>` 和 `location ^~ /tools2/<slug>/` 都直接 proxy，避免与应用自身的 slash 规范化冲突

### 4.4.3 静态子路径审计失败

源码准备好后、写入 `source_ready` 之前，还会先做一轮静态子路径审计：

- 首轮审计结果会写入 `artifacts.subpath_static_audit`
- 若框架支持自动修复，目前只会对 Next.js 做补救；补救后的再次审计会追加到 `artifacts.subpath_static_audit_attempts`
- 若仍有 findings，状态直接变为 `SUBPATH_STATIC_AUDIT_FAILED`

这一阶段的特点：

- 失败时通常还看不到 `runner.log` 里的 `source_ready`
- `errors` 由审计 finding 格式化而来，通常会带上文件、行号、规则码和具体说明
- `warnings` 中可能会有：
  - `Applied subpath rewrites for tool deployment path to N frontend files.`
  - `Auto-fixed N subpath source files before deployment.`

### 4.5 Dockerfile / PROJECT_ONBOARDING 生成

Runner 会先检查 repo 中是否已经存在：

- `Dockerfile`
- `PROJECT_ONBOARDING.md`

对应决策如下：

- 两者都存在：跳过 Codex，直接复用
- 只有 `Dockerfile`：只生成 `PROJECT_ONBOARDING.md`
- 两者都不存在：同时生成两个文件

如果进入生成阶段：

- 状态会先变为 `GENERATING_FILES`
- `artifacts` 会补上 `codex_log` 和 `codex_summary`
- `runner.log` 会出现：
  - `command_start ... args=["codex","exec",...]`
  - `command_heartbeat ... args=["codex","exec","<prompt omitted>"]`
  - `codex_output_stdout ...`
  - `codex_output_stderr ...`
  - `command_end returncode=... args=["codex","exec",...]`
  - 如需重试，还会有 `codex_retry attempt=...`

如果复用了现成文件：

- 不会出现上述 `codex exec` 相关日志
- `warnings` 中会留下复用说明，例如复用共享 repo、跳过生成、仅补生成 onboarding 等

生成阶段的常见失败与报错：

- 常规非零退出会落到 `GENERATION_FAILED`
- `errors` 通常是以下三种之一：
  - `codex exec timed out`
  - `codex exec failed`
  - `file generation failed`
- `warnings` 中可能带有更具体诊断：
  - `Upstream Codex gateway failed while streaming the response.`
  - `Codex CLI is not available in PATH.`
  - `codex exec timed out before producing final output.`
  - `Codex generation lost its upstream connection while streaming the response.`
  - `Codex generation failed because the upstream API connection dropped before completion.`
  - `Codex generation failed because the upstream API rate-limited the request.`
  - `Codex generation failed because the current codex session is not authenticated.`
  - `See codex_log artifact for full codex output.`
- 如果触发的是命令级超时，最终仍可能直接收口为 `FAILED`

### 4.6 生成结果校验

无论文件来自复用还是 Codex 生成，都会进入统一校验。

会直接进入 `errors` 的典型 findings 包括：

- `Missing Dockerfile`
- `Missing PROJECT_ONBOARDING.md`
- `Dockerfile is missing JSON-form CMD or ENTRYPOINT`
- 多阶段 `COPY` 源路径不合法
- `PROJECT_ONBOARDING.md is missing section 4`
- `Dockerfile is missing an application build step even though package.json.scripts.build exists`
- `Dockerfile start command appears to require built artifacts, but no build step was detected`
- `Dockerfile prunes devDependencies even though next.config.ts requires TypeScript to remain available at runtime`
- `Dockerfile may not provide pnpm at runtime even though next.config.ts can trigger pnpm-based TypeScript installation during next start`
- `Dockerfile does not install sqlite3 even though runtime evidence indicates sqlite3 is required`
- `Dockerfile does not install ffmpeg even though runtime evidence indicates ffmpeg is required`

只记入 `warnings`、但不会中断的典型项包括：

- `PROJECT_ONBOARDING.md still contains unresolved confirmation items`
- `Dockerfile contains TODO/UNKNOWN/NEEDS_CONFIRMATION markers`
- `Dockerfile references pnpm but does not clearly enable or install pnpm in the image`
- `PROJECT_ONBOARDING.md does not mention detected environment variable <NAME>`
- `Dockerfile CMD may not match the detected Node entrypoint`
- `Dockerfile CMD may not match the detected Python entrypoint`

校验通过后：

- 如有需要，会把 `Dockerfile` / `PROJECT_ONBOARDING.md` 同步回共享 repo
- `runner.log` 会出现 `synced_outputs_to_shared_repo files=[...]`
- 然后写出 `files_generated confirmed_port=<port>`
- 状态变为 `FILES_GENERATED`

校验失败时：

- 状态变为 `VALIDATION_FAILED`
- 不会进入 build / run
- 根因通常已经直接体现在 `errors` 里，优先看 `result.json.errors`

### 4.7 构建镜像

如果没有传 `--build`：

- 状态直接变为 `COMPLETED_WITHOUT_BUILD`
- 最终 `final_result.ok=true`

如果传了 `--build`：

- 先检查 `podman` 是否存在
- 不存在时直接变为 `BUILD_SKIPPED`
- `errors` 为 `podman not found in PATH`

真正开始构建时：

- 状态变为 `BUILDING_IMAGE`
- `runner.log` 会出现 `build_started image=...`
- 随后会出现 `command_start ... args=["podman","build",...]`
- 成功时会出现 `command_end returncode=0 ...` 和 `build_succeeded image=...`

构建阶段的特殊逻辑：

- 默认 Podman 模式若命中只读 runtime/storage，会自动切到 isolated Podman 环境再重试一次
- 这时 `warnings` 会追加：
  - `Default Podman mode hit a read-only runtime/storage path; retrying build with isolated Podman storage.`

`BUILD_FAILED` 的表现：

- `errors` 固定是 `podman build failed`
- 更具体原因通常在 `warnings` 和 `build.log`
- 内置诊断会识别这些常见场景：
  - `Podman tried to use the default runtime directory under /run/user, which is read-only in this environment.`
  - `Podman tried to use the default image store under ~/.local/share/containers/storage, which is read-only in this environment.`
  - `Rootless Podman is blocked by the current sandbox or user namespace configuration.`
  - `Podman hit a permission problem while building or running the container.`

如果 build 成功：

- 会写入 `image_id`
- 状态先变为 `BUILD_SUCCEEDED`
- 若没有 `--run`，最终再变为 `COMPLETED_WITH_BUILD`

### 4.8 解析运行规格与 `RUN_SKIPPED`

只有带 `--run` 时才会进入这一段。

Runner 会合并三类信息生成 `artifacts.run_spec`：

- `PROJECT_ONBOARDING.md` 中的端口、环境变量、env file、持久化目录、health path
- `podman image inspect` 中的 `EXPOSE` / `Env` / `Cmd` / `Entrypoint`
- `project-ports.json` 里的项目端口映射和运行时覆盖配置

这里还没有真正 `podman run`，但可能提前结束：

- 未能确认容器端口：状态 `RUN_SKIPPED`
  - `errors` 为 `No confirmed or inspectable service port found for container run`
- 未能分配宿主机端口：状态 `RUN_SKIPPED`
  - `errors` 为 `No host port could be assigned for container run`

这类问题优先看：

- `result.json.confirmed_port`
- `artifacts.run_spec`
- `PROJECT_ONBOARDING.md` 是否写出了明确端口
- 镜像是否真的 `EXPOSE` 了端口

### 4.9 启动容器

真正进入运行时：

- 容器名固定为 `project_slug`
- 状态变为 `STARTING_CONTAINER`
- `runner.log` 会出现：
  - `run_started container=... host_port=... container_port=...`
  - `command_start ... args=["podman","rm","-f",...]`
  - `command_start ... args=["podman","run","-d",...]`

先执行 `rm -f` 再执行 `run`，是为了清理同名旧容器。

这一阶段失败时：

- 若 `podman run` 返回非零，状态为 `RUN_FAILED`
- `errors` 固定是 `podman run failed`
- 详细原因优先看 `run.log`
- `warnings` 中可能追加和 build 阶段相同的 Podman 诊断

如果在 `STARTING_CONTAINER` 或后续 `WAITING_FOR_HEALTHCHECK` 期间抛出异常：

- 也会统一收口为 `RUN_FAILED`
- `errors` 固定是 `container run failed`
- Runner 会尽量自动清理掉已启动的容器

### 4.10 健康检查等待

`podman run` 成功后：

- `result.container_id` 会先写入
- 状态变为 `WAITING_FOR_HEALTHCHECK`
- Runner 会每隔 2 秒：
  - 用 `podman ps` 确认容器还活着
  - 对 `127.0.0.1:<host_port><health_path>` 发 HTTP GET

成功条件是：

- HTTP 状态码满足 `200 <= status < 500`

所以这里并不要求一定 `200`；`404` 也会被视为“HTTP 服务已经起来了”。

日志层面要注意：

- `runner.log` 里看得到重复的 `command_start` / `command_end` for `podman ps`
- HTTP 轮询本身没有逐条写入 `runner.log`
- 成功时也不会单独写一条 `healthcheck passed`，而是继续流向后面的运行时审计或 `run_succeeded`

健康检查失败时：

- 状态变为 `RUN_FAILED`
- `errors` 固定是 `container did not become ready`
- `warnings` 中的 `health_detail` 可能是：
  - 容器日志全文
  - `container exited before becoming ready`
  - 最后一次 HTTP/连接异常，比如连接拒绝、超时等

### 4.11 运行时子路径审计

这一步默认会执行。

这一步发生在健康检查通过之后、`run_succeeded` 之前，主要检查：

- 入口页和少量跟随页面是否返回了 4xx/5xx
- HTML 里是否还发出部署子路径之外的根相对 URL

产物位置：

- `artifacts.subpath_runtime_audit.checked_paths`
- `artifacts.subpath_runtime_audit.findings`
- `artifacts.subpath_runtime_audit.warnings`

失败时：

- 状态变为 `SUBPATH_RUNTIME_AUDIT_FAILED`
- `errors` 直接采用 finding 的 `message`
- 典型报错包括：
  - `Runtime subpath audit received HTTP 404 for /tools2/<slug>/...`
  - `Runtime HTML emits root-relative ... outside deployment subpath ...`
- 失败后容器会被清理掉

只有 warning、不算失败的典型情况：

- `Runtime subpath audit skipped HTML extraction for ... because response is not HTML.`

### 4.12 Athena Nginx 同步

当前流程在前面的运行已经成功时，会执行 Athena nginx 同步。

这一阶段有两个很重要的特征：

- 它不会把整体任务从成功改成失败；即使同步失败，主状态仍然保持 `RUN_SUCCEEDED`
- 具体结果主要体现在 `artifacts.athena_nginx_sync` 和 `warnings`

同步前会先在项目目录下生成：

- `nginx-add.conf`

`athena_nginx_sync.status` 常见取值与含义：

- `synced`：已把当前项目的 managed block 写入或更新到 `/etc/nginx/sites-available/tools.conf`，并完成 reload
- `already_managed`：`tools.conf` 里已存在同内容的 managed block，无需写回
- `updated_managed`：`tools.conf` 里已存在该项目的 managed block，但内容有变化，已按最新规则更新
- `inserted_managed`：`tools.conf` 里原本没有该项目 block，已追加写入
- `sudo_unavailable`：本机没有可用 `sudo`
- `read_failed`：无法读取 `tools.conf`
- `merge_failed`：Runner 无法生成或合并当前项目 block
- `backup_failed`：备份原始配置失败
- `write_failed`：写回更新后的 `tools.conf` 失败
- `nginx_missing`：本机没有 `nginx` 命令
- `nginx_test_failed`：`nginx -t` 失败
- `nginx_reload_failed`：reload 失败，Runner 会尝试回滚

对应的 `warnings` 文案通常是：

- `Synchronized ... and reloaded nginx.`
- `... already contains the managed nginx block ...`
- `... already contains nginx rules ..., so no managed block was injected.`
- `Automatic Athena nginx sync for /tools2/<slug> did not complete: <detail>`

日志层面：

- `runner.log` 能看到 `sudo cat`、`sudo cp`、`nginx -t`、`nginx -s reload` 或 `systemctl reload nginx` 的 `command_start` / `command_end`
- 真正的 merge 结果、备份路径、回滚情况主要看 `artifacts.athena_nginx_sync`

### 4.13 最终收口与异常兜底

正常成功收口时：

- `RUN_SUCCEEDED` 会写入 `artifacts.run_result`
- `emit_final_result()` 会生成 `result.json.final_result`
- `runner.log` 最后会出现：
  - `run_succeeded container=...`
  - `final_result_detail status=... analysis_summary=... warnings=[...]`

`final_result` 的特点：

- `COMPLETED_WITHOUT_BUILD`：只带 `generated_files` 和 `confirmed_port`
- `COMPLETED_WITH_BUILD` / `BUILD_SUCCEEDED`：带镜像信息
- `RUN_SUCCEEDED`：额外带 `container_id`、`port` 和外部访问 `url`
- 失败态统一是：
  - `ok: false`
  - `status: <失败状态>`
  - `errors: [...]`

异常兜底有两条规则最重要：

- 任意子命令超时：
  - `runner.log` 会出现 `command_timeout`
  - 最终状态统一是 `FAILED`
  - `errors` 类似 `command timed out: ["codex","exec","<prompt omitted>"]`
- 只有异常真正逃逸到最外层 `except` 时，`runner.log` 才会额外写 `job_failed error=...`
  - 普通的 `FETCH_FAILED` / `BUILD_FAILED` / `RUN_FAILED` 这类“已被主流程显式处理”的失败，通常不会写 `job_failed`

## 5. 日志事件和阶段映射

最值得对照看的日志事件如下：

| 日志事件 | 主要文件 | 对应阶段 | 说明 |
| --- | --- | --- | --- |
| `raw_runner_invocation` | `runner.log` | 初始化 | 原始参数和 cwd |
| `runtime_environment` | `runner.log` | 初始化 | 当前 `PATH` 与 `codex` 可执行路径 |
| `job_initialized` | `runner.log` | 初始化 | job 目录、结果骨架已建立 |
| `local_official_images` | `runner.log` | 初始化 | 识别到的本地官方基础镜像 |
| `git_clone_strategy` | `fetch.log` | Git 拉源码 | 当前在试 SSH 还是 HTTPS |
| `git_clone_attempt` | `fetch.log` | Git 拉源码 | 第几次 clone 尝试 |
| `git_clone_retry` | `fetch.log` | Git 拉源码 | 本次 clone 将要重试 |
| `git_clone_transport_failed` | `fetch.log` | Git 拉源码 | 当前 transport 的诊断结果 |
| `git_clone_succeeded` | `fetch.log` | Git 拉源码 | clone 已成功 |
| `source_ready` | `runner.log` | 源码准备完成 | 共享 repo / work-repo 已就绪；tool 场景下也代表静态子路径审计已经通过 |
| `analysis_summary` | `runner.log` | 源码分析完成 | 结构化分析摘要已落盘 |
| `command_start` | `runner.log` | 生成 / 构建 / 运行 / nginx | 子命令开始执行 |
| `command_heartbeat` | `runner.log` | 长命令执行中 | 目前主要是 `codex exec` |
| `codex_output_stdout` / `codex_output_stderr` | `runner.log` | Codex 生成 | Codex 流式输出 |
| `codex_retry` | `runner.log` | Codex 生成 | 因可重试错误再次执行 `codex exec` |
| `synced_outputs_to_shared_repo` | `runner.log` | 生成后同步 | 生成产物已回写共享 repo |
| `files_generated confirmed_port=...` | `runner.log` | 校验通过 | 文件可用且已解析出 onboarding 端口 |
| `build_started` | `runner.log` | 构建开始 | 即将进入 `podman build` |
| `build_succeeded` | `runner.log` | 构建完成 | 镜像已经构建成功 |
| `run_started` | `runner.log` | 启动容器 | 端口已经解析完成，即将 `podman run` |
| `command_timeout` | `runner.log` | 任意长命令 | 该子命令超时，最终状态会转成 `FAILED` |
| `run_succeeded` | `runner.log` | 运行完成 | 容器已经 ready，运行期审计也已通过 |
| `job_failed` | `runner.log` | 异常兜底 | 说明有异常逃逸到最外层；不是所有失败都会出现它 |
| `final_result_detail` | `runner.log` | 最终收口 | 最终状态、分析摘要、warnings 已落盘 |

## 6. 失败状态与典型报错

很多失败状态的 `errors` 设计得很短，真正根因往往在 `warnings` 和对应阶段的 artifact 日志里。

| 最终状态 | `errors` 典型值 | 先看哪里 | 常见根因 |
| --- | --- | --- | --- |
| `ARGUMENT_ERROR` | `--run requires --build` 等 | 标准输出 | 参数组合本身不合法，通常还没有 job 目录 |
| `FETCH_FAILED` | `git clone failed` | `warnings` + `fetch.log` | GitHub 凭证缺失、token 失效、SSH key 无权限、DNS/网络问题、私有仓库不可见 |
| `SUBPATH_STATIC_AUDIT_FAILED` | 审计 finding 文本 | `artifacts.subpath_static_audit*` | 静态资源、页面链接或框架 basePath 配置仍指向根路径 |
| `GENERATION_FAILED` | `codex exec timed out` / `codex exec failed` / `file generation failed` | `warnings` + `codex.log` + `runner.log` | Codex CLI 不存在、鉴权失效、上游断流、限流、502 等 |
| `VALIDATION_FAILED` | 一组具体规则文本 | `result.json.errors` | 生成结果本身不满足 Dockerfile / onboarding 规则 |
| `BUILD_SKIPPED` | `podman not found in PATH` | `result.json.errors` | 环境缺少 Podman |
| `BUILD_FAILED` | `podman build failed` | `warnings` + `build.log` | Dockerfile 语法、依赖安装失败、Rootless Podman 受限、权限问题 |
| `RUN_SKIPPED` | `No confirmed or inspectable service port found...` / `No host port could be assigned...` | `artifacts.run_spec` | onboarding 未写端口、镜像未暴露端口、端口映射无法确定 |
| `RUN_FAILED` | `podman run failed` / `container did not become ready` / `container run failed` | `warnings` + `run.log` + 容器日志 | Podman 运行失败、健康检查超时、容器提前退出、服务没监听端口 |
| `SUBPATH_RUNTIME_AUDIT_FAILED` | finding 的 `message` | `artifacts.subpath_runtime_audit` | 服务启动后仍返回错误页面，或运行时 HTML 还在输出根相对路径 |
| `FAILED` | `command timed out: ...` 或异常文本 | `runner.log` 最后几十行 | 任意子命令超时，或未被阶段分支归类的异常 |

## 7. 各文件分别看什么

### `output/result.json`

优先用于确认最终收口结论：

- `status`
- `errors`
- `warnings`
- `analysis_summary`
- `generated_files`
- `confirmed_port`
- `artifacts.run_spec`
- `artifacts.run_result`
- `artifacts.subpath_static_audit`
- `artifacts.subpath_runtime_audit`
- `artifacts.athena_nginx_sync`
- `final_result`

### `output/runner.log`

优先用于确认阶段边界和命令边界：

- 哪个阶段已经开始
- 哪个子命令已经启动、退出、超时
- 是否发生 `codex_retry`
- 最后是正常 `final_result_detail` 收口，还是异常 `job_failed`

要注意两点：

- Git clone 的详细输出不在这里，而在 `fetch.log`
- 健康检查的 HTTP 轮询细节也不逐条写在这里，主要还是看 `podman ps` 和最后的状态

### `output/fetch.log`

只在 Git 源存在，用来定位：

- SSH / HTTPS 走了哪条策略
- clone 重试了几次
- 当前 transport 为什么失败
- 最终有没有成功

### `output/codex.log`

只在真的进入生成阶段时存在，用来定位：

- Codex 最后一轮执行的原始输出
- 模型到底失败在读取代码、生成内容，还是网络/鉴权

### `output/codex-summary.txt`

保存 `codex exec --output-last-message` 的最后一条总结信息，适合快速看模型最后给了什么结论。

### `output/build.log`

用来定位 `podman build` 的真实失败点，例如：

- 拉基础镜像失败
- 包安装失败
- Dockerfile 指令错误
- 构建脚本退出非零

### `output/run.log`

用来定位 `podman run` 的返回结果：

- 成功时通常只有容器 ID
- 失败时通常能直接看到 Podman 的报错文本

## 8. 补充说明

### 8.1 为什么 `confirmed_port` 有时是 `null`

`confirmed_port` 来自 `PROJECT_ONBOARDING.md` 的端口解析，不一定能解析出来。

如果 onboarding 没明确写端口，`files_generated confirmed_port=...` 这里可能是 `null`；后续 run 阶段仍有机会从镜像 `EXPOSE` 中补出端口。

### 8.2 为什么健康检查会接受 404

这里检查的是“HTTP 服务是否起来了”，不是“业务路由是否正确”，所以 `200 <= status < 500` 都会算 ready。

真正去检查子路径是否正确，是后面的 `SUBPATH_RUNTIME_AUDIT_FAILED` 那一层。

### 8.3 为什么同一个项目每次都会先 `rm -f`

容器名固定用 `project_slug`，不先清理同名旧容器就会冲突。

### 8.4 为什么 Nginx 同步失败了，但整体还是 `RUN_SUCCEEDED`

当前逻辑把 Athena nginx 同步定义为“运行成功后的附加能力”，不是主成功条件。

所以即使 `athena_nginx_sync.status` 是 `write_failed` 或 `nginx_reload_failed`，主状态仍可能是 `RUN_SUCCEEDED`，只是 `warnings` 会明确告诉你自动同步没完成。

### 8.5 为什么有些失败没有 `job_failed`

因为大量阶段性失败是主流程主动识别并正常收口的，比如：

- `FETCH_FAILED`
- `VALIDATION_FAILED`
- `BUILD_FAILED`
- `RUN_FAILED`

只有异常真的逃出主流程时，才会写 `job_failed error=...`。

## 9. 最简排障顺序

建议固定按下面顺序排：

1. 看 `result.json.status`
2. 看 `result.json.errors`
3. 看 `result.json.warnings`
4. 看 `runner.log` 最后 30 到 100 行
5. 如果是 `FETCH_FAILED`，立刻看 `fetch.log`
6. 如果是 `GENERATION_FAILED`，立刻看 `codex.log`
7. 如果是 `BUILD_FAILED`，立刻看 `build.log`
8. 如果是 `RUN_FAILED`，先看 `run.log`，再看容器日志或 `warnings` 里的 `health_detail`
9. 如果是 `SUBPATH_*_FAILED`，直接看对应 artifact：`subpath_static_audit` 或 `subpath_runtime_audit`
10. 如果主状态成功但访问异常，再单独看 `artifacts.athena_nginx_sync`

这样通常能在几分钟内判断问题是卡在：

- 参数阶段
- 源码准备
- Codex 生成
- 文件校验
- 镜像构建
- 运行规格推导
- 容器启动
- 健康检查
- 运行时子路径审计
- Athena nginx 同步
