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
- 如果带 `tool_id`，会复制出独立工作副本：`automation/jobs/<project_slug>/<job_id>/work-repo`

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
--source-type git --source <repo> --ref <branch> --tool-id <tool> --build --run
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

失败路径常见状态包括：

- `FETCH_FAILED`
- `GENERATION_FAILED`
- `VALIDATION_FAILED`
- `BUILD_FAILED`
- `RUN_FAILED`
- `RUN_SKIPPED`
- `BUILD_SKIPPED`
- `FAILED`

状态设置集中在 [runner.py](/home/devops/ka/automation/runner.py:3380) 到 [runner.py](/home/devops/ka/automation/runner.py:3748)。

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

如果这次任务带 `tool_id`：

- 再从共享 repo 复制一份到 `work-repo`
- 后续所有生成、构建、运行都基于 `work-repo`

关键代码：

- [runner.py](/home/devops/ka/automation/runner.py:2525)
- [runner.py](/home/devops/ka/automation/runner.py:3400)

关键状态：

- `FETCHING_SOURCE`
- `SOURCE_READY`

关键日志：

- `source_ready repo_dir=... reused_shared_repo=...`
- `analysis_summary summary=...`

示例：

- [api-examples-web runner.log](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/runner.log:5)
- [audioqas runner.log](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/runner.log:5)

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

如果带 `tool_id`，说明最终要挂在 `/tools2/<project_slug>` 子路径下：

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

### 4.5 Dockerfile / PROJECT_ONBOARDING 生成

Runner 先检查 repo 中是否已经有：

- `Dockerfile`
- `PROJECT_ONBOARDING.md`

对应三种情况：

1. 两者都已存在
   - 跳过 Codex 生成
   - 直接复用
2. 只有 `Dockerfile`
   - 仅生成 `PROJECT_ONBOARDING.md`
3. 两者都不存在
   - 同时生成两个文件

逻辑代码：

- [runner.py `determine_generation_mode()`](/home/devops/ka/automation/runner.py:2172)
- [runner.py `build_codex_prompt()`](/home/devops/ka/automation/runner.py:2240)
- [runner.py `invoke_codex_generation()`](/home/devops/ka/automation/runner.py:2300)

关键状态：

- `GENERATING_FILES`

关键 artifacts：

- `codex_log`
- `codex_summary`

Codex 执行日志会写入：

- `runner.log` 中的 `command_start` / `command_heartbeat` / `command_end`
- `codex_output_stdout`
- `codex_output_stderr`

如果本次复用了已有文件，则会在 `warnings` 中留下复用说明，而不会进入 Codex 阶段。样例如：

- [api-examples-web result.json](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/result.json:23)

### 4.6 生成结果校验

无论文件是复用还是生成，都会经过统一校验：

- `Dockerfile` 是否存在
- `PROJECT_ONBOARDING.md` 是否存在
- `Dockerfile` 是否有 JSON-form `CMD` / `ENTRYPOINT`
- 多阶段 `COPY` 路径是否合理
- 若仓库需要 build，Dockerfile 是否有 build 步骤
- `PROJECT_ONBOARDING.md` 是否缺 section 4
- 环境变量是否在 onboarding 中体现

关键代码：

- [runner.py `validate_generated_files()`](/home/devops/ka/automation/runner.py:2817)

校验结果：

- `findings` 会进入 `errors`
- `warnings` 会追加到 `result.warnings`

若 `findings` 非空：

- 状态变为 `VALIDATION_FAILED`
- 直接结束，不进入 build / run

若校验通过：

- 同步 `Dockerfile` / `PROJECT_ONBOARDING.md` 回共享 repo
- 状态进入 `FILES_GENERATED`

关键日志：

- `synced_outputs_to_shared_repo files=[...]`
- `files_generated confirmed_port=<port>`

示例：

- [api-examples-web runner.log](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/runner.log:7)
- [audioqas runner.log](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/runner.log:7)

### 4.7 构建镜像

如果没有 `--build`：

- 状态直接变为 `COMPLETED_WITHOUT_BUILD`

如果有 `--build`：

- 先检查 `podman` 是否存在
- 生成镜像名 `<slug>:<job_id>`
- 状态变为 `BUILDING_IMAGE`
- 执行 `podman build -t <image> -f Dockerfile .`

关键代码：

- [runner.py `build_image()`](/home/devops/ka/automation/runner.py:3064)
- [runner.py](/home/devops/ka/automation/runner.py:3525)

关键日志：

- `build_started image=...`
- `command_start ... args=["podman","build",...]`
- `command_end returncode=0 args=["podman","build",...]`
- `build_succeeded image=...`

示例：

- [api-examples-web runner.log](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/runner.log:8)
- [audioqas runner.log](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/runner.log:8)

特殊逻辑：

- 如果默认 Podman 模式遇到只读运行时或存储目录，会切换到 isolated Podman 环境并重试 build

关键代码：

- [runner.py `should_fallback_to_isolated_podman()`](/home/devops/ka/automation/runner.py:3087)
- [runner.py `prepare_podman_environment()`](/home/devops/ka/automation/runner.py:471)

build 成功后：

- 写入 `image_id`
- 状态变为 `BUILD_SUCCEEDED`

如果这次没有 `--run`：

- 状态变为 `COMPLETED_WITH_BUILD`

### 4.8 解析运行规格

如果带 `--run`，在 build 成功后进入运行前准备：

1. 从 `PROJECT_ONBOARDING.md` 提取：
   - 容器端口
   - 环境变量
   - env file hint
   - start command
   - persistence paths
   - health path hint
2. 从镜像 inspect 中提取：
   - `EXPOSE`
   - `Env`
   - `Cmd`
   - `Entrypoint`
3. 融合成最终 `run_spec`

关键代码：

- [runner.py `parse_onboarding_run_spec()`](/home/devops/ka/automation/runner.py:2894)
- [runner.py `inspect_image_runtime_spec()`](/home/devops/ka/automation/runner.py:2982)
- [runner.py `merge_run_spec()`](/home/devops/ka/automation/runner.py:3019)

融合后会写入：

- `result.json.artifacts.run_spec`

成功样例：

- [api-examples-web result.json](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/result.json:44)
- [audioqas result.json](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/result.json:58)

### 4.9 启动容器

运行前会先做端口检查：

- 没有识别出容器端口：`RUN_SKIPPED`
- 没有分配出 host 端口：`RUN_SKIPPED`

正常路径下：

- 容器名固定为 `project_slug`
- 状态变为 `STARTING_CONTAINER`
- 先执行 `podman rm -f <container_name>` 做清理
- 再执行 `podman run -d --name ... -p 127.0.0.1:host:container ...`

关键代码：

- [runner.py `run_container()`](/home/devops/ka/automation/runner.py:3100)
- [runner.py](/home/devops/ka/automation/runner.py:3596)

关键日志：

- `run_started container=... host_port=... container_port=...`
- `command_start ... args=["podman","rm","-f",...]`
- `command_start ... args=["podman","run","-d",...]`

示例：

- [api-examples-web runner.log](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/runner.log:14)
- [audioqas runner.log](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/runner.log:13)

### 4.10 健康检查等待

`podman run` 成功后：

- 保存 `container_id`
- 状态变为 `WAITING_FOR_HEALTHCHECK`
- 轮询检查容器是否还在运行
- 同时对 `127.0.0.1:<host_port><health_path>` 发 HTTP GET

成功条件：

- HTTP 状态码在 `200 <= status < 500`

也就是说这里不是严格要求 `200`，`404` 这类也会被认为容器已经起来，只要 HTTP 服务可响应。

关键代码：

- [runner.py `wait_for_container_ready()`](/home/devops/ka/automation/runner.py:3159)

关键日志：

- 多次 `command_start ... args=["podman","ps",...]`
- 每 2 秒轮询一次，直到成功或超时

典型样例：

- `api-examples-web` 几乎立即 ready，只看到 1 次 `podman ps`
  - [runner.log](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/runner.log:18)
- `audioqas` 启动更慢，出现多轮 `podman ps`
  - [runner.log](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/runner.log:18)

### 4.11 Nginx 子路径接入

如果带 `tool_id`，容器 ready 后还会补一层 Athena nginx 接入：

1. 生成 `nginx-add.conf`
2. 同步到 `/etc/nginx/sites-available/athena.conf`
3. 根据现状判断：
   - `synced`
   - `already_managed`
   - `already_present_unmanaged`
   - 其他失败状态

关键代码：

- [runner.py `render_nginx_add_conf()`](/home/devops/ka/automation/runner.py:2247)
- [runner.py `sync_athena_nginx_config()`](/home/devops/ka/automation/runner.py:1684)
- [runner.py](/home/devops/ka/automation/runner.py:3400)

这里的 nginx 规则会显式依赖上一节的 `proxy_mode`：

- `strip_prefix`
  - `location = /tools2/<slug>` 继续做 `301 -> /tools2/<slug>/`
  - `location ^~ /tools2/<slug>/` 代理到 `http://127.0.0.1:<port>/`

- `preserve_prefix`
  - `location = /tools2/<slug>` 直接代理到 `http://127.0.0.1:<port>/tools2/<slug>`
  - `location ^~ /tools2/<slug>/` 直接代理到 `http://127.0.0.1:<port>/tools2/<slug>/`
  - 不再做无斜杠入口 301，避免和 Next.js 等框架自身的 URL 规范化逻辑形成重定向环

关键 artifacts：

- `nginx_add_conf`
- `athena_nginx_sync`

关键日志：

- 通常至少会出现一次读取 Athena nginx 的 `sudo cat`
- 若需要真正写入和 reload，还会出现对应 `sudo cp`、`nginx -t`、`nginx -s reload` 或 `systemctl reload nginx`

成功样例：

- [api-examples-web result.json](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/result.json:67)
- [audioqas result.json](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/result.json:84)

### 4.12 最终收口

容器运行成功后：

- 状态变为 `RUN_SUCCEEDED`
- 写入 `artifacts.run_result`
- 写入最终 `result.json.final_result`
- 标准输出打印简化后的 final result JSON

关键日志：

- `run_succeeded container=...`
- `final_result_detail status=RUN_SUCCEEDED analysis_summary=... warnings=[...]`

关键代码：

- [runner.py `build_final_result()`](/home/devops/ka/automation/runner.py:3232)
- [runner.py `emit_final_result()`](/home/devops/ka/automation/runner.py:3297)

成功样例：

- [api-examples-web runner.log](/home/devops/ka/automation/jobs/api-examples-web/deploy-mq3rgiif-544ffd75e4/output/runner.log:22)
- [audioqas runner.log](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/runner.log:27)

## 5. 成功路径上的关键日志节点

一条典型成功部署日志，按时间顺序最值得关注的是这些点：

1. `raw_runner_invocation`
   - 确认这次任务的原始参数
2. `runtime_environment`
   - 确认 `codex` 路径和运行环境
3. `job_initialized`
   - 任务正式进入 Runner
4. `source_ready`
   - 源码已经准备好，后续都是基于此 repo
5. `analysis_summary`
   - Runner 对项目的结构化判断
6. `files_generated`
   - Dockerfile / onboarding 已就绪，并解析出端口
7. `build_started`
   - 镜像构建开始
8. `build_succeeded`
   - 镜像构建成功
9. `run_started`
   - 容器启动开始
10. `podman ps` 轮询
   - 健康检查等待期
11. `run_succeeded`
   - 容器已 ready
12. `final_result_detail`
   - 最终状态、摘要、警告全部落盘

如果只想快速判断一条部署是否成功，优先看：

- `result.json.status`
- `result.json.final_result`
- `runner.log` 是否出现 `run_succeeded`

## 6. 成功时各文件分别看什么

### `output/result.json`

用于看结构化结果，重点字段：

- `status`
- `warnings`
- `errors`
- `analysis_summary`
- `generated_files`
- `image` / `image_id`
- `container` / `container_id`
- `artifacts.run_spec`
- `artifacts.run_result`
- `final_result`

### `output/runner.log`

用于看阶段流转和命令执行边界，重点内容：

- 阶段切换
- 关键命令开始 / 结束
- 健康检查轮询
- 最终收口日志

### `output/fetch.log`

只在 git 源时存在，主要用于看 clone 失败或重试原因。

### `output/codex.log`

只在发生文件生成时存在，主要用于看 Codex 实际输出、读取了哪些文件、生成了什么内容。

### `output/build.log`

用于看 `podman build` 的完整输出，定位 Dockerfile 或依赖安装问题。

### `output/run.log`

用于看 `podman run` 返回的原始输出，通常是容器 ID 或 run 阶段的错误信息。

## 7. 一个完整成功案例如何阅读

以 `audioqas` 为例：

1. 看 [result.json](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/result.json:1)
   - 最终 `status=RUN_SUCCEEDED`
   - `confirmed_port=8000`
   - `run_result.port=8001:8000`
   - `url=https://athena.agoralab.co/tools2/audioqas`
2. 看 [runner.log](/home/devops/ka/automation/jobs/audioqas/deploy-mq4o60hj-96dbba6c84/output/runner.log:1)
   - 初始化
   - `source_ready`
   - `build_started`
   - `build_succeeded`
   - `run_started`
   - 多轮 `podman ps`
   - `run_succeeded`
   - `final_result_detail`
3. 如需深入：
   - 看 `build.log` 理解镜像构建耗时
   - 看 `run_spec` 理解端口、环境变量和持久化策略是怎么解析出来的

## 8. 补充说明

### 8.1 为什么 `confirmed_port` 有时是 `null`

`files_generated confirmed_port=...` 来源于 `PROJECT_ONBOARDING.md` 的端口解析，见 [runner.py](/home/devops/ka/automation/runner.py:2881)。

如果 onboarding 没明确写出端口，这里会是 `null`。但后续 run 阶段仍可能从镜像 `EXPOSE` 中补出端口。

### 8.2 为什么健康检查会接受 404

`wait_for_container_ready()` 只要求 HTTP 服务可响应，不要求业务路由正确，所以 `200 <= status < 500` 都认为服务已经起来，见 [runner.py](/home/devops/ka/automation/runner.py:3186)。

### 8.3 为什么同一个项目会先 `rm -f` 再 `run`

容器名固定为 `project_slug`，所以每次运行前都要清理旧容器，避免同名冲突，见 [runner.py](/home/devops/ka/automation/runner.py:3606)。

### 8.4 为什么有些任务不会进入 Codex

如果 repo 中本来就有 `Dockerfile` 和 `PROJECT_ONBOARDING.md`，Runner 会直接复用，不再重新生成，见 [runner.py](/home/devops/ka/automation/runner.py:3441)。

## 9. 最简排障顺序

如果以后要排查某个任务，建议固定按下面顺序看：

1. 看 `result.json.status`
2. 看 `result.json.errors` / `warnings`
3. 看 `runner.log` 中最后 30 到 100 行
4. 如果卡在拉代码，看 `fetch.log`
5. 如果卡在生成，看 `codex.log`
6. 如果卡在构建，看 `build.log`
7. 如果卡在运行或健康检查，看 `run.log` 和容器日志采样

这样能最快定位任务到底停在：

- 源码准备
- 文件生成
- 静态校验
- 镜像构建
- 容器启动
- 健康检查
- Nginx 接入
