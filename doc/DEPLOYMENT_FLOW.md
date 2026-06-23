# Deployment Flow

本文按当前 [`runner.py`](../runner.py) 的真实实现说明一次任务的执行流程、状态机、产物和失败收口方式。

## 1. 入口

外部入口通常是 [`runner.sh`](../runner.sh)：

```bash
./runner.sh --source-type git --source <repo> --build --run
```

`runner.sh` 只做两件事：

1. 尝试把 `codex` 放进 `PATH`
2. 预读本机已有的 `docker.io/library/*` 基础镜像并写入环境变量/缓存

随后执行：

```bash
python3 runner.py "$@"
```

## 2. 目录与产物

对项目 `project_slug` 和任务 `job_id`，主目录如下：

- `jobs/<project_slug>/repo`
  项目级共享源码副本
- `jobs/<project_slug>/repo-state.json`
  共享副本对应的签名
- `jobs/<project_slug>/<job_id>/work-repo`
  本次任务的私有工作副本
- `jobs/<project_slug>/<job_id>/output/`
  本次任务输出

常见输出文件：

- `output/result.json`
- `output/runner.log`
- `output/fetch.log`
- `output/codex.log`
- `output/codex-summary.txt`
- `output/build.log`
- `output/run.log`

其中 `result.json` 会持续覆盖写入当前状态，结束前再补一份 `final_result`。

## 3. 主流程

### 3.1 初始化

`main()` 先解析参数并创建任务目录。硬约束只有一条：

- `--run` 必须同时带 `--build`

初始化后的 `result.json` 会先落一个 skeleton，初始状态是：

- `INITIALIZED`

同时写出：

- `job_id`
- `project_slug`
- `source_type`
- `source`
- `build_requested`
- `run_requested`
- 初始 `artifacts`

### 3.2 准备运行环境

初始化后会准备两类环境：

- `codex-home`
  以 `codex-home-cache/` 为只读种子，再在 job 下创建最小写入层
- Podman 环境
  默认先直接使用宿主机环境；如果后续 build 命中只读运行目录，再回退到隔离的 Podman storage

### 3.3 准备源码

如果 `--source-type=git`：

- 状态先切到 `FETCHING_SOURCE`
- 使用 `prepare_shared_repo()` 准备 `jobs/<slug>/repo`
- Git 源签名由 `source + ref` 决定

如果 `--source-type=local`：

- 不会进入 `FETCHING_SOURCE`
- 使用本地目录内容签名决定是否复用共享副本

共享副本准备完成后，一定会复制一份到：

- `jobs/<slug>/<job_id>/work-repo`

后续所有分析、改写、生成、构建、运行都只针对 `work-repo`。

### 3.4 仓库分析

源码就绪后会立刻执行 `collect_repo_analysis()`，提取：

- 服务运行时类型：`python` / `node` / `unknown`
- 包管理器
- 入口命令候选
- 端口候选
- 环境变量名
- system dependency hints
- config / database / storage hints
- workspace service package 摘要

整理后的简版结果写入：

- `result.json.analysis_summary`

此时状态变为：

- `SOURCE_READY`

### 3.5 子路径 prepare 阶段

在文件生成前，runner 会执行 [`subpath.pipeline.prepare_subpath_sources()`](../subpath/pipeline.py)：

1. 构建 `SubpathPlan`
2. 识别默认项目与代理模式
3. 做框架配置适配和源码 rewrite
4. 跑首轮静态审计
5. 若有 finding，尝试 auto-fix
6. 跑二轮静态审计

这一步会把以下内容写进 `artifacts` 和 `analysis_summary`：

- `subpath_plan`
- `subpath_default_project`
- `subpath_detection_evidence`
- `subpath_rewrite_report`
- `subpath_static_audit`
- `subpath_static_audit_attempts`
- `.ka/subpath.json` 生效时的 `subpath_declaration`

如果自动修复后仍有静态 finding，任务直接失败：

- `SUBPATH_STATIC_AUDIT_FAILED`

`errors` 里会写静态 finding 文本。

### 3.6 决定是否生成文件

准备好 `work-repo` 后，runner 会检查：

- 是否已有 `Dockerfile`
- 是否已有 `PROJECT_ONBOARDING.md`
- 对 Git 源，这两个文件是否为 tracked 文件

决策规则：

- 两者都可复用：跳过 `codex exec`
- 只有 `Dockerfile` 可复用：只生成 `PROJECT_ONBOARDING.md`
- 其他情况：同时生成两者

如果跳过生成，会直接给 `warnings` 追加“复用共享副本/复用 tracked 输出”的说明。

### 3.7 生成与本地校验

需要生成时，状态切到：

- `GENERATING_FILES`

随后调用 `invoke_codex_generation()`。当前行为有两层重试：

- 上游生成失败时，`codex exec` 最多重试 2 次
- 若生成成功但本地校验失败，会带校验反馈再生成 1 次

生成后会执行 `validate_generated_files()`。当前主要校验：

- `Dockerfile` / `PROJECT_ONBOARDING.md` 是否都存在
- `Dockerfile` 是否包含 JSON-form `CMD` 或 `ENTRYPOINT`
- `PROJECT_ONBOARDING.md` 是否包含“4 运行参数”
- `EXPOSE UNKNOWN`、`ENV PORT=UNKNOWN` 等无效占位
- 需要 build 的项目是否真的有 build step
- `ffmpeg` / `sqlite3` 等运行依赖是否进入镜像

如果校验仍失败，任务结束为：

- `VALIDATION_FAILED`

### 3.8 同步生成结果

本轮生成通过后，runner 会把：

- `Dockerfile`
- `PROJECT_ONBOARDING.md`

从 `work-repo` 同步回 `jobs/<slug>/repo`，供后续复用。

这一步结束后状态切到：

- `FILES_GENERATED`

同时会从 `PROJECT_ONBOARDING.md` 中提取确认端口，写入：

- `result.json.confirmed_port`

## 4. Build 阶段

如果未指定 `--build`，任务到这里直接成功结束：

- `COMPLETED_WITHOUT_BUILD`

如果指定了 `--build`：

1. 检查 `podman` 是否存在
2. 状态切到 `BUILDING_IMAGE`
3. 执行 `podman build -t <image> -f Dockerfile .`
4. 成功后记录 `image` / `image_id`

Build 成功后的中间状态是：

- `BUILD_SUCCEEDED`

如果本机没有 `podman`：

- `BUILD_SKIPPED`

如果 `podman build` 非零：

- `BUILD_FAILED`

### 4.1 Build Output Audit

build 成功后，如果 `artifacts` 里存在可反序列化的 `subpath_plan`，runner 会执行 build output audit：

- 扫描 `dist/`、`build/`、`out/`
- 查找 build 产物中仍然输出到根路径的 URL
- 生成 `subpath_build_output_audit`

当前默认策略是 shadow mode：

- finding 会写入 artifact / summary
- 也会追加 warning
- 默认不会阻断 build 成功

只有 `.ka/subpath.json` 的 `build_output_policy` 显式把某类 code 标成 `enforce` 时，才会失败为：

- `SUBPATH_BUILD_OUTPUT_AUDIT_FAILED`

## 5. Run 阶段

如果指定了 `--run`，build 成功后继续：

1. 从 `PROJECT_ONBOARDING.md` 解析运行参数
2. 从镜像 `inspect` 读取 `EXPOSE` / `Env`
3. 读取 `project-ports.json` 中该项目的运行时覆盖
4. 合并为 `run_spec`

端口优先级：

- 容器端口：
  - `project-ports.json` 已存在映射
  - onboarding 确认端口
  - 镜像 `EXPOSE`
- 宿主机端口：
  - `--host-port`
  - `project-ports.json` 已存在映射
  - 自动从 `8003` 开始分配，并写回 `project-ports.json`

同时还会合并：

- `env`
- `env_file`
- `volumes`

如果无法确定容器端口或宿主机端口，状态为：

- `RUN_SKIPPED`

### 5.1 启动容器

可运行时，状态流转为：

1. `STARTING_CONTAINER`
2. `WAITING_FOR_HEALTHCHECK`

运行命令是 detached 模式：

```text
podman run -d --name <project_slug> -p 127.0.0.1:<host_port>:<container_port> --restart always ...
```

如果 `PORT` 出现在 onboarding 的环境变量列表里，runner 会自动注入：

- `PORT=<container_port>`

### 5.2 容器就绪检查

`wait_for_container_ready()` 会：

- 先看 `podman ps` 里容器是否还在
- 再轮询 `http://127.0.0.1:<host_port><health_path>`

`health_path` 优先来自 onboarding 中解析出的提示，默认是 `/`。

以下情况会失败为：

- `RUN_FAILED`

包括：

- `podman run` 非零退出
- 容器提前退出
- HTTP 就绪检查在超时内没有成功

## 6. Runtime Subpath Audit 与成功收口

容器就绪后，runner 会调用 [`subpath.pipeline.runtime_subpath_phase()`](../subpath/pipeline.py)：

- 根据 `SubpathPlan` 选择 runtime audit target
- 汇总 checked paths / findings / warnings

结果写入：

- `artifacts.subpath_runtime_audit`
- `analysis_summary.subpath_runtime_audit_summary`

如果 runtime finding 非空，任务失败为：

- `SUBPATH_RUNTIME_AUDIT_FAILED`

否则进入最终成功态：

- `RUN_SUCCEEDED`

同时还会写入：

- `artifacts.run_result`
- `jobs/<slug>/nginx-add.conf`

## 7. Athena nginx 同步

`RUN_SUCCEEDED` 后 runner 会尝试：

1. 生成 `/tools2/<slug>` 对应的 nginx block
2. 写入 `jobs/<slug>/nginx-add.conf`
3. 如果本机存在 `/etc/nginx/sites-available/tools.conf`
   - 先备份
   - 以 `# BEGIN KA TOOL <slug>` block 方式 upsert
   - `nginx -t`
   - reload nginx

这一段无论成功或失败，都只会影响 `warnings`，不会把已成功运行的任务回滚成失败。

## 8. 状态机摘要

成功路径常见状态：

1. `INITIALIZED`
2. `FETCHING_SOURCE` 或直接准备本地源码
3. `SOURCE_READY`
4. `GENERATING_FILES`
5. `FILES_GENERATED`
6. `BUILDING_IMAGE`
7. `BUILD_SUCCEEDED`
8. `STARTING_CONTAINER`
9. `WAITING_FOR_HEALTHCHECK`
10. `RUN_SUCCEEDED`

中途成功结束状态：

- `COMPLETED_WITHOUT_BUILD`
- `COMPLETED_WITH_BUILD`

失败状态：

- `ARGUMENT_ERROR`
- `FETCH_FAILED`
- `SUBPATH_STATIC_AUDIT_FAILED`
- `GENERATION_FAILED`
- `VALIDATION_FAILED`
- `BUILD_SKIPPED`
- `BUILD_FAILED`
- `SUBPATH_BUILD_OUTPUT_AUDIT_FAILED`
- `RUN_SKIPPED`
- `RUN_FAILED`
- `SUBPATH_RUNTIME_AUDIT_FAILED`
- `FAILED`

其中 `FAILED` 是兜底态，常见于：

- 命令级 timeout
- 未归类异常

## 9. 调试顺序

排查任务时，建议固定按这个顺序看：

1. `output/result.json` 的 `status`
2. `output/result.json` 的 `errors`
3. `output/result.json` 的 `warnings`
4. `output/runner.log`
5. 阶段日志
   - `fetch.log`
   - `codex.log`
   - `build.log`
   - `run.log`
6. `artifacts` 和 `analysis_summary`
   - `subpath_*`
   - `run_spec`
   - `run_result`
   - `athena_nginx_sync`

如果只关心最终对外结果，也可以直接看：

- `result.json.final_result`

它会把成功态压缩成最小输出：

- `status`
- `job_id`
- `image` / `image_id`
- `container_id`
- `port`
- `url`
