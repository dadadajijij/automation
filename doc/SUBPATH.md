# Subpath

本文是当前项目子路径适配系统的统一说明文档，合并了原先分散的 architecture / policy / current status 三类内容。

如果你关心的是 `runner.py` 端到端任务状态机，而不是子路径子系统本身，优先看：

- [DEPLOYMENT_FLOW.md](./DEPLOYMENT_FLOW.md)

如果你关心的是 build output audit 的灰度和 enforce rollout，优先看：

- [SUBPATH_ENFORCEMENT_POLICY.md](./SUBPATH_ENFORCEMENT_POLICY.md)

## 1. 目标与边界

平台统一把应用暴露在：

```text
/tools2/<project_slug>
```

子路径系统的目标不是把任意仓库完美迁移成多租户 Web 应用，而是：

1. 在不要求上游仓库主动改造的前提下，让常见前端/全栈项目尽量可部署
2. 在 build 前尽量通过静态分析发现问题
3. 对已知模式自动修复
4. 在服务启动后通过运行时审计兜底

当前系统更像：

- 平台部署自动修复器

而不是：

- 完备的前端/全栈编译迁移器

## 2. 模块与稳定入口

当前主实现已经迁到 `subpath/`：

- [subpath/discovery.py](../subpath/discovery.py)
  负责项目识别、前端根发现、策略生成
- [subpath/rewrite.py](../subpath/rewrite.py)
  负责源码级 URL / HTML / runtime helper 改写
- [subpath/static_audit.py](../subpath/static_audit.py)
  负责静态审计目标发现与 finding 扫描
- [subpath/runtime_audit.py](../subpath/runtime_audit.py)
  负责运行时 HTML 审计
- [subpath/adapters.py](../subpath/adapters.py)
  负责框架配置级适配
- [subpath/apply.py](../subpath/apply.py)
  负责改写与 auto-fix 顶层入口
- [subpath/build_output_audit.py](../subpath/build_output_audit.py)
  负责 build output 审计
- [subpath/build_output_policy.py](../subpath/build_output_policy.py)
  负责 build output finding policy
- [subpath/pipeline.py](../subpath/pipeline.py)
  负责 prepare/runtime 两阶段 orchestration

`runner.py` 仍保留少量包装函数，把仓库已有的分析、读写和网络能力接到 `subpath.pipeline`。

对调用方和后续维护最重要的入口是：

- `build_subpath_plan()`
- `prepare_subpath_sources()`
- `run_static_subpath_audit()`
- `auto_fix_subpath_issues()` / `auto_fix_findings()`
- `run_build_output_subpath_audit()`
- `runtime_subpath_phase()`
- `run_runtime_subpath_audit()`

## 3. 基本原则

当前实现遵循 4 条硬原则：

1. 先识别，再处理，不允许 rewrite、audit、runtime 各自重复猜项目形态
2. 优先使用框架原生能力，再使用通用源码改写兜底
3. 静态审计和自动修复必须消费同一份策略结果，避免“审计能发现、处理不覆盖”
4. 运行时审计只做兜底，不承担主修复职责

## 4. 统一输入模型

子路径链路的唯一标准输入是 `SubpathPlan`。

计划来源固定为两层：

1. 显式声明
2. 自动识别

优先级固定：

1. 若仓库存在 `.ka/subpath.json` 且能成功产出至少一个项目策略，则优先信声明
2. 否则回退自动识别
3. 若自动识别仍不明确，则退化到 `generic + strip_prefix + static_rewrite`

内部标准策略对象是：

```python
FrontendProjectStrategy(
  project_id: str,
  framework: str,
  proxy_mode: str,
  adapter: str,
  source_adapter: str,
  project_root: Path,
  source_roots: tuple[Path, ...],
  runtime_roots: tuple[Path, ...],
  config_files: tuple[Path, ...],
  capabilities: tuple[str, ...],
  evidence: tuple[str, ...],
  runtime_entry_hint: Optional[str],
  runtime_entry_candidates: tuple[str, ...],
)
```

要求：

- `build_subpath_plan()` 是唯一允许产出完整计划的入口
- `prepare_subpath_sources()`、`run_static_subpath_audit()`、`auto_fix_subpath_issues()`、`run_runtime_subpath_audit()` 都必须以 `SubpathPlan` 为准
- `analysis_summary` 中必须能看到 `subpath_plan` 与 `subpath_default_project`
- 声明式配置生效时，`analysis_summary` 和 `artifacts` 中必须能看到 `subpath_declaration`

### 4.1 `.ka/subpath.json`

当前最小声明格式：

```json
{
  "projects": [
    {
      "root": "frontend",
      "framework": "vite",
      "proxy_mode": "preserve_prefix",
      "config_file": "frontend/vite.config.ts",
      "runtime_roots": ["frontend/dist"],
      "source_roots": ["frontend/src"]
    }
  ]
}
```

当前支持字段：

- `root`
- `framework`
- `proxy_mode`
- `adapter`
- `source_adapter`
- `config_file`
- `runtime_roots`
- `source_roots`
- `runtime_entry_hint`
- `runtime_entry_candidates`
- `build_output_policy`
- `default_project`

规则：

- `root`、`framework`、`proxy_mode` 是最小必填集合
- 所有路径按 repo 相对路径解析
- 路径不存在时该字段会被忽略，不阻断流程
- 当前不支持“部分声明、部分自动识别”的隐式 merge

## 5. 识别与策略选择

### 5.1 当前识别的框架类别

当前会识别：

- `nextjs`
- `vite`
- `vue_cli`
- `cra`
- `express_static`
- `static_html`
- `generic`

### 5.2 代理模式

平台只允许两种代理模式：

- `preserve_prefix`
- `strip_prefix`

含义：

- `preserve_prefix`
  应用自身理解 `/tools2/<slug>`，代理不去前缀
- `strip_prefix`
  应用只理解 `/`，外层代理转发前去掉 `/tools2/<slug>`

### 5.3 默认判定规则

1. Next.js
   - 走 `preserve_prefix`
   - 依赖 `basePath` / `assetPrefix`

2. 普通 Vite
   - 默认走 `preserve_prefix`
   - 依赖 `base`

3. Vite 构建产物由 Node 服务挂在 `/`
   - 走 `strip_prefix`
   - 典型特征是既识别到 Node/TS 后端入口，又识别到前端运行时静态根

4. Vue CLI / CRA
   - 走 `preserve_prefix`
   - 分别依赖 `publicPath` / `homepage`

5. `express_static` / `static_html` / `generic`
   - 默认走 `strip_prefix`
   - 依赖源码改写和运行时注入兜底

### 5.4 当前发现能力

识别阶段会尽量推断：

- workspace / monorepo 前端包
- Node 入口脚本
- Node 静态根
- Python 模板目录 / 静态目录
- Vite 项目根
- runtime root evidence

当前这些能力仍强依赖：

- 正则
- 目录启发式
- 文件名约定
- package scripts / dependencies

因此通用性是“工程上可接受”，不是严格语义正确。

## 6. 处理链路

当前标准顺序固定为 8 步：

1. 识别项目并生成 `SubpathPlan`
2. 做框架配置级修复
3. 做源码级 rewrite
4. 跑首轮静态审计
5. 若有 finding，执行自动修复
6. 跑二次静态审计
7. 构建并运行服务
8. 跑运行时审计

任何新逻辑都必须挂到这 8 步之一，不允许插入“旁路修复器”。

在 `runner.py` 主流程里，这条链路主要通过两步消费：

1. `prepare_subpath_sources()`
2. `runtime_subpath_phase()`

## 7. 配置级与源码级改写

### 7.1 配置级改写

配置级修复只负责让框架理解子路径，不负责修正业务源码中的根路径调用。

当前标准动作：

- Next.js：补 `basePath` / `assetPrefix`
- Vite：补 `base`
- Vue CLI：补 `publicPath`
- CRA：补 `homepage`

要求：

- 幂等
- 完成后仍必须进入源码级 rewrite
- 不允许因为“框架原生支持 basePath”就跳过源码审计

### 7.2 源码级改写

当前源码级策略分两类：

1. `nextjs`
   - 使用 Next 专项修复

2. `static_rewrite`
   - 使用通用 rewrite 兜底

`adapter` 与 `source_adapter` 含义不同：

- `adapter` 表示框架配置怎么修
- `source_adapter` 表示源码怎么修

### 7.3 当前改写覆盖

当前会改写：

- HTML 的 `href="/..."` / `src="/..."` / `action="/..."`
- `fetch('/...')`
- 部分模板字符串请求
- `axios.get('/...')` 等直接请求
- `new Request('/...')`
- `new EventSource('/...')`
- `url: '/...'`
- `return '/...'`
- `window.location.href = '/...'`
- `window.location.assign('/...')`
- `window.open('/...')`
- 部分 origin-based 路径拼装逻辑

HTML 还会注入：

- `window.__TOOL_BASE_PATH__`
- `window.__TOOL_ORIGIN_URL__`
- `window.withToolBase()`

### 7.4 rewrite 目标文件

rewrite 目标必须由统一函数选择，不允许每个阶段扫出不同范围。

标准规则：

1. Next.js
   - 扫描 `src/app`、`pages`、`components` 等浏览器侧 TS/TSX/JS/JSX

2. Vite / Vue CLI / CRA
   - `index.html`
   - `src/**/*.ts(x)|js(x)|mjs`
   - runtime root 下可被浏览器直接消费的 `html/js/mjs`

3. Generic / static_html / express_static
   - runtime root 下的 `html/js/mjs`
   - `views/`、`templates/`、`static/`、`public/` 等浏览器面文件

禁止项：

- 不要把后端入口文件、API route、测试文件混入 rewrite 目标
- 不要把 `dist/`、`build/`、`.next/`、`node_modules/` 混入扫描范围

### 7.5 rewrite 报告

当前改写阶段不只记录“哪些文件改了”，还会记录“命中了哪些 rewrite rule”。

标准输出位置：

- `artifacts.subpath_rewrite_report`

格式固定为：

```json
{
  "frontend/src/api/jobs.ts": ["generic_fetch_call", "generic_return_root_path"],
  "src/app/page.tsx": ["nextjs_request_call", "nextjs_window_open"]
}
```

要求：

- 只记录实际命中的 rule id
- 不记录候选但未命中的规则
- rule id 一旦对外落盘，就应视为稳定诊断标识

## 8. 静态审计与自动修复

### 8.1 静态审计

静态审计是 build 前的主闸门。

必须检查：

- HTML 的根路径资源与表单引用
- 浏览器请求 URL
- 导航与返回值中的根路径
- `window.location` / `window.open`
- Next.js 的原始 `<a href="/...">`
- Next.js 的原始 `<form action="/...">`

框架专项规则：

- Vite `index.html` 中 `/src/...` 构建入口允许保留

失败准则：

- 首轮静态审计有 finding：进入自动修复
- 自动修复后仍有 finding：直接 `SUBPATH_STATIC_AUDIT_FAILED`

### 8.2 自动修复

自动修复只允许做两类事情：

1. 配置缺失补齐
2. 已知安全的源码替换

必须满足：

- 幂等
- 不引入明显的 TS / 构建错误
- 不修改后端服务语义
- 不改变外部 API 业务行为

自动修复后必须立即重跑静态审计，不允许直接继续 build。

当前 failure reason 已经从单一 `no_matching_fixer` 细化为：

- `build_output_only`
- `framework_specific_only`
- `file_type_not_supported`
- `target_not_discovered`
- `rewriter_made_no_changes`
- `no_matching_fixer`

## 9. Build Output Audit

build output audit 是 build 后、run 前的补充检查，负责扫描 `dist/` / `build/` / `out/` 等产物中的根路径泄漏。

当前已完成：

- code / category / severity summary
- enforcement candidate summary
- `recommended_mode`
- `policy_version`
- `.ka/subpath.json` 的 `build_output_policy` override 接线
- 显式 `enforce` 路径

默认模式仍是：

- shadow mode

也就是：

- finding 会写 artifact / summary
- 也会追加 warning
- 只有策略显式标记为 `enforce` 时才阻断为 `SUBPATH_BUILD_OUTPUT_AUDIT_FAILED`

更细的 rollout 规则见：

- [SUBPATH_ENFORCEMENT_POLICY.md](./SUBPATH_ENFORCEMENT_POLICY.md)

## 10. 运行时审计

运行时审计只做轻量兜底，标准动作是：

1. 访问入口 URL
2. 校验 HTTP 状态
3. 若返回 HTML，提取页面中的 URL
4. 检查是否继续泄漏部署子路径之外的根相对路径
5. 跟随少量同域子链接继续检查

失败条件：

- 入口页返回 4xx/5xx
- 跟随页返回 4xx/5xx
- HTML 继续输出 `/tools2/<slug>` 之外的根相对 URL

当前 runtime 线已经具备：

- capability-aware skip
- `eligible_projects`
- `default_runtime_project`
- `entry_path_hint`
- `entry_path_candidates`
- candidate fallback 执行
- multi-target aggregation
- `audited_projects`
- `projects_with_findings`
- `project_summaries`

当前仍不是完整浏览器回放，也抓不到很多交互后才出现的问题，例如：

- 点击后触发的请求
- 表单上传
- SPA 路由切换后的懒加载内容
- SSE / WebSocket
- 下载链接在运行时拼装

## 11. 可观测性输出

当前标准诊断输出至少包括：

- `analysis_summary.subpath_plan`
- `analysis_summary.subpath_default_project`
- `analysis_summary.subpath_declaration`
- `artifacts.subpath_plan`
- `artifacts.subpath_detection_evidence`
- `artifacts.subpath_rewrite_report`
- `artifacts.subpath_static_audit`
- `artifacts.subpath_build_output_audit`
- `artifacts.subpath_runtime_audit`

这些输出的目的是：

- 事后诊断识别结果
- 判断改写是否真的命中
- 区分静态失败、build output shadow finding、运行时失败

## 12. 失败收口规则

子路径链路的失败状态当前有三类：

1. `SUBPATH_STATIC_AUDIT_FAILED`
   - 含义：源码层面仍有明确不兼容问题

2. `SUBPATH_BUILD_OUTPUT_AUDIT_FAILED`
   - 含义：build output finding 命中了显式 `enforce` 策略

3. `SUBPATH_RUNTIME_AUDIT_FAILED`
   - 含义：服务已启动，但真实返回页面仍不兼容

其中：

- 静态失败优先级高于 build / run
- build output audit 默认不拦截
- 运行时失败发生在健康检查通过之后

## 13. 当前完成度

当前子路径系统已经完成从单文件启发式逻辑到模块化子系统的迁移。

核心完成项：

- `RuntimeRootEvidence`
- `SubpathPlan`
- `source_adapter`
- workspace / package 级识别
- project-scoped target discovery
- project-scoped config adapter / apply
- finding-driven auto-fix
- build output audit
- runtime capability selection / candidate fallback
- artifact + summary 可观测性
- `.ka/subpath.json` 的 build output policy override 入口

## 14. 当前风险与明显问题

### 14.1 Runtime 仍不是完整 multi-project 审计

现在已经有 target selection 和 fallback，但仍然主要是：

- 选择一个 default runtime project
- 聚合部分 fallback candidates

不是每个 eligible project 都会独立审计。

### 14.2 Declaration 能力仍不完整

`.ka/subpath.json` 当前已经接入：

- `projects`
- `default_project`
- `runtime_roots`
- `source_roots`
- `config_file`
- `source_adapter`
- `runtime_entry_hint`
- `runtime_entry_candidates`
- `build_output_policy`

但还没有完整扩展成：

- runtime entry strategy override
- capability / role override
- apply / audit 路由控制面

### 14.3 rewrite 逻辑仍偏分散

当前 Vite、Next.js、generic 仍保留各自分支，公共行为和特例混在一起，维护成本会继续上升。

### 14.4 rewrite 对构建系统的影响仍需防守

静态审计通过不代表 rewrite 一定不会引入编译问题。build 级回归测试仍需要持续补齐。

### 14.5 识别逻辑仍依赖启发式

Node 入口、静态根、workspace 前端包、前后端边界的识别仍大量依赖正则和目录命名，因此复杂工程仍有误判风险。

## 15. 新项目接入准则

不允许先“碰运气部署”，再从失败日志里临时补规则。

标准接入动作应是：

1. 识别框架类型
2. 确认 `proxy_mode`
3. 确认前端项目根
4. 确认运行时静态根
5. 确认框架配置文件
6. 运行静态审计
7. 补专项规则或回归测试
8. 再进入批量部署

如果新模式不能稳定映射到现有规则，应优先补：

- `build_subpath_plan()` 识别
- `find_subpath_audit_targets()` 目标发现
- 对应专项测试

而不是继续堆单点正则。

## 16. 后续演进方向

短期最值得做：

1. 继续补 build 级回归测试
2. 推进 runtime 多项目审计
3. 扩展 `.ka/subpath.json` 的 declaration 控制面
4. 把 `downloadUrl()` / 上传 / SSE 这类模式继续纳入专项测试

中期建议：

1. 让更多阶段完全消费结构化 `SubpathPlan`
2. 继续细化 monorepo 多前端场景
3. 把更多 rewrite 从分散正则收敛到结构化 rule 元数据

长期方向：

1. 对前端源码做 AST 级处理
2. 对框架配置做结构化修改
3. 对运行时审计引入轻量浏览器自动化

长期上，平台侧自动修复应尽量只保留“通用安全修复”；更复杂的路径适配，优先前置到客户仓库自身改造。
