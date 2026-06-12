# 生成规则

这个文件是 `automation` 调用 `codex exec` 时使用的固定规则模板。

下面“Prompt Body”之后的内容才会传给模型。

允许由 `runner.py` 填充以下占位符：

- `RUNTIME_RULES`
- `SOURCE_CONTEXT_RULES`
- `REPO_FACTS`

<!-- PROMPT_BODY_START -->

## 任务

只生成 `Dockerfile` 和 `PROJECT_ONBOARDING.md`。

## 强约束

- 面向 Ubuntu 22.04.5 LTS，后续由管理员使用 Podman 构建镜像
- 只允许读取最少必要证据；若 runner 已明确给出端口、环境变量、状态目录或持久化目录等事实，不要再自行展开相关源码文件，尤其不要再展开 `settings`、`history`、`store` 一类源码文件
- 不要做 git 探测、整仓扫描、无关运行时验证，也不要展开读取大文件全文，尤其是 lockfile、测试夹具、模型权重、二进制资源
- 不要输出分析过程、规则复述、diff 或长说明；未知项统一使用 `UNKNOWN`、`NEEDS_CONFIRMATION` 或 `TBD - 由管理员构建`，不要猜测无直接证据的内容

## 允许读取的输入

优先读取：

- `README.md`
- `pyproject.toml`
- `requirements.txt`
- `package.json`
- 常见入口文件，如 `run_local.py`、`main.py`、`app.py`、`server.py`、`server.js`
- 仅在 runner 未给出相关事实、事实冲突、或以上证据仍不足以确定最终输出时，才允许读取与运行参数、环境变量、状态目录直接相关的最小必要文件

除以上情况外，不要再读取其他文件。

## Dockerfile 规则

{{RUNTIME_RULES}}

- 默认优先生成最简单、最稳定、最容易通过当前校验的单阶段 Dockerfile；只有在已有直接证据表明最终镜像只需部分构建产物，且运行时不再依赖源码、`tsx`/`ts-node`、workspace 原始目录结构、本地二进制、静态资源目录或包管理器脚本时，才使用多阶段
- 使用多阶段 Dockerfile 时，最终运行时阶段必须保留全部实际运行依赖；复制时优先选择可稳定证明存在的路径，无法证明深层产物路径存在时优先复制整个工作目录或已有顶层路径；如果运行时入口依赖 `pnpm start`、`yarn start` 等命令，必须确保对应包管理器在最终镜像中真实可用，否则优先直接执行已安装的应用二进制

## PROJECT_ONBOARDING 规则

- 只记录真实存在的环境变量、配置和外部依赖
- 仅在发现文件写入、数据库、上传目录、缓存目录、状态目录或卷挂载证据时，才声明持久化目录
- `已确认信息` 只写直接证据支持的事实；`推断信息` 只写必要工程判断；`仍需确认的事实` 和 `待确认问题` 只保留会影响交付的内容

{{SOURCE_CONTEXT_RULES}}

## PROJECT_ONBOARDING 骨架

- 文档标题必须是 `# PROJECT_ONBOARDING`，并按固定顺序包含：`1 项目基础信息`、`2 代码和版本信息`、`3 启动信息`、`4 运行参数`、`5 配置与密钥`、`6 存储信息`、`7 证据与判断说明`、`8 待确认问题`
- 每章只保留最小必要字段，优先覆盖：项目标识、代码来源、启动命令、端口、环境变量、配置文件、持久化目录、已确认事实、待确认问题
- 不要输出空表格、空代码块或与交付无关的占位段落

## 已确认事实

{{REPO_FACTS}}
