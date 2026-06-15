# Subpath Refactor Roadmap

本文给出当前子路径链路的改造路线图，目标不是微调几个正则，而是把 `runner.py` 里的子路径能力升级为更稳定、更可扩展的平台能力。

聚焦两个问题：

- 从通用性和适配性角度，当前逻辑最值得优先修的是什么
- 这些优化点应该如何直接落到 `runner.py` 的拆分与重构上

本文以当前实现为准，关键入口包括：

- [detect_subpath_strategy()](../runner.py:3130)
- [find_subpath_audit_targets()](../runner.py:2830)
- [rewrite_frontend_subpath_urls()](../runner.py:3778)
- [run_static_subpath_audit()](../runner.py:2994)
- [run_runtime_subpath_audit()](../runner.py:4784)
- [main() 子路径主流程](../runner.py:5061)

## 1. 总体判断

当前子路径体系已经具备“可用”的平台基础：

- 有统一策略对象 `SubpathStrategy`
- 有识别、改写、静态审计、运行时审计四段主链路
- 对 `nextjs / vite / vue_cli / cra / static_html` 已有明确规则
- 对 Node 静态托管、FastAPI/Flask 模板项目有一定兜底适配

但如果目标是“非常重要的通用项目自动化部署平台”，当前还存在四类结构性问题：

1. 识别精度不够高
2. 策略粒度不够细
3. 审计闭环不够完整
4. 可扩展机制还停留在单文件正则堆叠

因此建议按 `P0 / P1 / P2` 三层推进，而不是继续沿着当前单函数叠补丁。

## 2. P0 路线图

`P0` 代表会直接影响平台稳定性、误判率、跨项目通用性的改造，应该最先做。

### 2.1 收紧项目识别规则，降低误判

当前风险：

- [is_express_static_project()](../runner.py:3063) 只要“能找到 Node 入口 + 能找到运行时根”就可能命中
- [detect_frontend_runtime_roots()](../runner.py:1719) 又会把 `src/public/static/client/web/views/templates` 这类目录启发式加入候选
- 导致普通 Node 项目也可能被提前归类为 `express_static`

改造目标：

- 区分“明确证据”与“目录启发”
- 只有检测到真实静态托管证据时，才允许进入 `express_static`
- 目录存在本身只能作为 `generic` 辅助信息，不能直接驱动 framework 判定

建议新增结构：

```python
RuntimeRootEvidence(
  path: Path,
  source: Literal["node_static_call", "python_template_decl", "workspace_anchor", "directory_hint"],
  confidence: Literal["high", "medium", "low"],
  detail: str,
)
```

建议新增函数：

- `detect_runtime_root_evidence(repo_dir) -> list[RuntimeRootEvidence]`
- `classify_runtime_roots(evidence) -> tuple[confirmed_roots, hinted_roots]`
- `detect_framework_candidates(repo_dir, evidence) -> list[FrameworkCandidate]`

`runner.py` 落点：

- 把 [detect_static_root_hints_from_node_entry()](../runner.py:1525) 改为返回 evidence，而不是裸 `Path`
- 把 [detect_frontend_root_hints_from_python_file()](../runner.py:1694) 改为返回 evidence
- 把 [detect_frontend_runtime_roots()](../runner.py:1719) 改为兼容层，只做汇总，不再承担最终判定
- 重写 [is_express_static_project()](../runner.py:3063)，只消费高置信度 evidence

### 2.2 把策略模型改成“每个前端项目一份完整策略”

当前风险：

- 顶层 [SubpathStrategy](../runner.py:2571) 只有一套 `framework/proxy_mode/adapter/source_adapter`
- `frontend_projects` 已存在，但更多只是附属字段
- 多前端包 monorepo、混合仓库里，一套 repo 级策略不够表达真实情况

改造目标：

- 顶层对象只做汇总
- 每个 `FrontendProject` 自己持有完整的 `framework/proxy_mode/adapter/source_adapter`
- 审计、rewrite、runtime audit 都以项目实例为粒度执行

建议调整结构：

```python
FrontendProjectStrategy(
  framework: str,
  proxy_mode: str,
  adapter: str,
  source_adapter: str,
  project_root: Path,
  source_roots: tuple[Path, ...],
  runtime_roots: tuple[Path, ...],
  config_file: Optional[Path],
  evidence: tuple[str, ...],
)

SubpathPlan(
  projects: tuple[FrontendProjectStrategy, ...],
  default_project: Optional[str],
  notes: tuple[str, ...],
)
```

`runner.py` 落点：

- 用 `FrontendProjectStrategy` 替代当前 [FrontendProject](../runner.py:2537) 的“半策略半目录”模型
- 用 `SubpathPlan` 替代当前 [SubpathStrategy](../runner.py:2571) 的 repo 级决策模型
- 把 [frontend_projects_for_strategy()](../runner.py:2748) 改成 `subpath_projects(plan)`
- 把 [detect_subpath_strategy()](../runner.py:3130) 改成 `build_subpath_plan()`

### 2.3 扩展 workspace / monorepo 的框架识别，不再偏向 Vite

当前风险：

- [vite_project_roots()](../runner.py:1595) 已支持 workspace
- 但 [is_nextjs_project()](../runner.py:3034)、[is_vue_cli_project()](../runner.py:3054)、[is_create_react_app_project()](../runner.py:3047) 仍然只看 repo 根
- monorepo 下 Next/Vue/CRA 子包很容易被识别失败

改造目标：

- 统一把 workspace package 作为一等识别对象
- 每个 package 都能独立判断 `nextjs / vite / vue_cli / cra / generic`

建议新增函数：

- `discover_frontend_packages(repo_dir) -> list[PackageCandidate]`
- `detect_package_framework(package_dir) -> FrameworkCandidate`
- `build_frontend_project_strategies(repo_dir, packages, evidence) -> list[FrontendProjectStrategy]`

`runner.py` 落点：

- 保留 [discover_workspace_packages()](../runner.py:1012) 作为底层工具
- 把 [workspace_frontend_package_dirs()](../runner.py:1576) 升级为框架无关的前端包发现器
- 废弃 `is_*_project(repo_dir)` 这种 repo 级判定方式，改成 `detect_*_package(package_dir)`

### 2.4 修正“配置改写只覆盖一个项目”的问题

当前风险：

- [vite_config_file()](../runner.py:3243) 只返回第一份配置文件
- [ensure_vite_base_config()](../runner.py:3252) 因而只改一个 Vite 项目
- 这和 [find_subpath_audit_targets()](../runner.py:2830) 已支持多项目扫描不一致

改造目标：

- 所有配置改写都应该针对 project strategy 列表逐个执行
- 不允许“扫描多项目，配置只修一个”

建议新增函数：

- `apply_framework_config_adapter(project: FrontendProjectStrategy, project_slug: str) -> list[str]`
- `apply_framework_config_adapters(plan: SubpathPlan, project_slug: str) -> list[str]`

`runner.py` 落点：

- [ensure_vite_base_config()](../runner.py:3252) 改为接收 `config_path`
- [ensure_nextjs_basepath_config()](../runner.py:3371) 改为接收 `config_path`
- [ensure_vue_cli_public_path()](../runner.py:3296) 改为接收 `config_path`
- [ensure_cra_homepage()](../runner.py:3324) 改为接收 `package_path`
- [apply_subpath_rewrites()](../runner.py:3883) 改成先遍历 plan，再按 project 执行

## 3. P1 路线图

`P1` 代表不会立刻导致大规模误判，但会明显影响长期通用性、审计准确率和后续扩展成本。

### 3.1 拆分“首轮 rewrite”和“finding 驱动 auto-fix”

当前问题：

- 主流程先跑 [apply_subpath_rewrites()](../runner.py:3883)
- 失败后再跑 [auto_fix_subpath_issues()](../runner.py:3020)
- 对非 Next 项目，后者本质上还是重复执行通用 rewrite

改造目标：

- 首轮阶段只做框架配置修复和安全 rewrite
- auto-fix 阶段只根据 finding 类型做定向修复
- 让“为什么修、修了什么、还剩什么问题”变得清晰

建议新增结构：

```python
SubpathFinding(
  code: str,
  file: str,
  line: int,
  project_root: str,
  framework: str,
  severity: str,
  message: str,
)
```

建议新增函数：

- `group_subpath_findings_by_code(findings)`
- `auto_fix_findings(plan, findings, project_slug)`
- `apply_safe_source_rewrites(plan, project_slug)`

`runner.py` 落点：

- [auto_fix_subpath_issues()](../runner.py:3020) 改成按 finding code 分发
- [rewrite_frontend_subpath_urls()](../runner.py:3778) 保留为通用底层能力，不再直接承担“自动修复总入口”
- [main()](../runner.py:5064) 里的子路径阶段应拆成独立 orchestrator

### 3.2 扩大静态审计覆盖，补上构建前代码盲区

当前问题：

- [SUBPATH_CODE_SCAN_FRAMEWORKS](../runner.py:2492) 只包含 `nextjs/vite/cra/vue_cli`
- `generic/express_static/static_html` 大量情况下只扫运行时根里的 `html/js/mjs`
- 如果源码在 `frontend/src/**/*.ts(x)`、构建产物在 `dist/`，可能在 build 前漏审

改造目标：

- 把“是否代码级扫描”从 framework 白名单，改成由 project capabilities 决定
- 对任意带前端源码结构的项目都尽量做代码级静态检查

建议新增函数：

- `should_enable_code_scan(project: FrontendProjectStrategy) -> bool`
- `discover_project_source_files(project, include_code_scan: bool)`

`runner.py` 落点：

- [find_subpath_audit_targets()](../runner.py:2830) 改为按 project 粒度决定扫描模式
- [should_include_subpath_target_file()](../runner.py:2793) 继续保留为统一过滤器

### 3.3 增加构建产物审计

当前问题：

- 现有静态审计主要看源码
- 运行时审计主要看最终 HTML
- 缺中间层：构建产物里的 `asset manifest / built html / emitted js`

改造目标：

- 在 build 成功后、run 之前，加一层“构建产物审计”
- 专门扫描 `dist/build/out/.next export` 一类目录中的根相对路径泄漏

建议新增函数：

- `detect_build_output_roots(repo_dir, plan) -> list[Path]`
- `run_build_output_subpath_audit(output_roots, project_slug, plan)`

`runner.py` 落点：

- 在 [main()](../runner.py:5213) build 成功之后、[main()](../runner.py:5243) run 之前插入 build-output audit
- 结果落到 `artifacts.subpath_build_output_audit`

### 3.4 运行时审计升级为浏览器级能力

当前问题：

- [run_runtime_subpath_audit()](../runner.py:4784) 只抓 HTML 中的 `href/src/action`
- 无法捕获浏览器执行后的 `fetch/XHR/SSE/WebSocket/router jump`

改造目标：

- 增加无头浏览器运行时审计
- 关注控制台错误、4xx/5xx、根相对请求、跳转失败

建议新增函数：

- `run_browser_runtime_subpath_audit(host_port, project_slug, plan)`
- `collect_browser_network_findings(trace)`

`runner.py` 落点：

- 保留当前 [run_runtime_subpath_audit()](../runner.py:4784) 作为轻量 HTML fallback
- 新增 `browser runtime audit`，优先使用；浏览器不可用时再回退

### 3.5 补齐 Next.js 专项修复闭环

当前问题：

- [rewrite_nextjs_source_calls()](../runner.py:3417) 还是空实现
- `scan_subpath_findings()` 会报 Next `<form action="/...">`
- 但自动修复只覆盖 `<a>` 和部分请求调用

改造目标：

- 让 Next 审计规则和修复规则一一对应
- 不允许出现“审计能报，修复器没有实现”的长期空洞

建议新增函数：

- `rewrite_nextjs_form_actions(text)`
- `rewrite_nextjs_navigation_calls(text)`
- `rewrite_nextjs_event_source_calls(text)`

`runner.py` 落点：

- 删除空壳 [rewrite_nextjs_source_calls()](../runner.py:3417) 或把它补成真实专项入口
- [auto_fix_nextjs_subpath_issues()](../runner.py:3521) 改为显式分步骤执行

## 4. P2 路线图

`P2` 代表平台成熟度与后续演进能力建设，优先级低于 P0/P1，但对长期维护价值很高。

### 4.1 引入显式声明机制

目标：

- 支持项目通过配置文件声明自己的前端项目、basePath、proxyMode、runtimeRoots
- 平台优先信“明确声明”，其次信自动检测

建议格式：

- `.ka/subpath.json`
- `tool.deploy.yaml`

建议字段：

```json
{
  "projects": [
    {
      "root": "frontend",
      "framework": "vite",
      "proxy_mode": "preserve_prefix",
      "config_file": "frontend/vite.config.ts",
      "runtime_roots": ["frontend/dist"]
    }
  ]
}
```

当前状态：

- `.ka/subpath.json` 最小可用版本已经落地
- 当前优先支持 JSON，不支持 `tool.deploy.yaml`
- 当前行为是“声明有效则优先信声明”，而不是细粒度 merge

### 4.2 把 rewrite 规则从正则堆叠升级为规则引擎

目标：

- 每一类 rewrite 都有独立 rule id
- 可以按 framework、file type、finding code 精准启用

建议结构：

```python
SubpathRewriteRule(
  rule_id: str,
  applies_to: tuple[str, ...],
  file_suffixes: tuple[str, ...],
  rewrite: Callable[..., RewriteResult],
)
```

### 4.3 提升可观测性与诊断能力

目标：

- 让每次策略选择都带证据
- 让每个 finding 都能回溯到 project / adapter / phase

建议落盘：

- `artifacts.subpath_plan`
- `artifacts.subpath_detection_evidence`
- `artifacts.subpath_rewrite_report`
- `artifacts.subpath_build_output_audit`

### 4.4 做缓存与增量扫描

目标：

- 大仓库里避免反复跑 `detect_frontend_runtime_roots()`
- 避免每个文件 rewrite 时重复推导全仓 runtime roots

建议方式：

- 在 plan 构建阶段缓存 runtime roots
- 后续 rewrite/audit 都只消费 plan

## 5. runner.py 建议拆分

当前 `runner.py` 已经超过 5000 行，[wc 结果](../runner.py:1) 对维护已经不友好。即使暂时不拆文件，也建议先做“逻辑分区重构”。

建议分成 6 个区域：

### 5.1 `subpath_detection`

职责：

- workspace 包发现
- runtime root evidence 收集
- framework candidate 判定
- project strategy / subpath plan 构建

建议承接函数：

- [discover_workspace_packages()](../runner.py:1012)
- [detect_node_entry_script_paths()](../runner.py:1446)
- [detect_static_root_hints_from_node_entry()](../runner.py:1525)
- [detect_frontend_root_hints_from_python_file()](../runner.py:1694)
- [detect_frontend_runtime_roots()](../runner.py:1719)
- [detect_subpath_strategy()](../runner.py:3130)

### 5.2 `subpath_targets`

职责：

- 按 project 决定 source roots / runtime roots / 扫描文件
- 统一过滤 server/test/build files

建议承接函数：

- [find_frontend_rewrite_targets()](../runner.py:2657)
- [frontend_projects_for_strategy()](../runner.py:2748)
- [should_include_subpath_target_file()](../runner.py:2793)
- [find_subpath_audit_targets()](../runner.py:2830)
- [find_strategy_rewrite_targets()](../runner.py:2850)

### 5.3 `subpath_rewrite`

职责：

- 框架配置改写
- 通用源码改写
- framework-specific rewrite

建议承接函数：

- [ensure_vite_base_config()](../runner.py:3252)
- [ensure_vue_cli_public_path()](../runner.py:3296)
- [ensure_cra_homepage()](../runner.py:3324)
- [ensure_nextjs_basepath_config()](../runner.py:3371)
- [auto_fix_nextjs_subpath_issues()](../runner.py:3521)
- [rewrite_frontend_subpath_urls()](../runner.py:3778)
- [apply_subpath_rewrites()](../runner.py:3883)

### 5.4 `subpath_audit_static`

职责：

- 静态源码审计
- finding 结构化
- 按 finding 定向自动修复

建议承接函数：

- [scan_subpath_findings()](../runner.py:2916)
- [run_static_subpath_audit()](../runner.py:2994)
- [auto_fix_subpath_issues()](../runner.py:3020)

### 5.5 `subpath_audit_runtime`

职责：

- HTML runtime audit
- 浏览器 runtime audit
- build output audit

建议承接函数：

- [translate_external_to_upstream_path()](../runner.py:4719)
- [translate_upstream_to_external_path()](../runner.py:4731)
- [runtime_subpath_findings_for_html()](../runner.py:4748)
- [run_runtime_subpath_audit()](../runner.py:4784)

### 5.6 `subpath_orchestrator`

职责：

- 在主流程里统一执行 plan -> rewrite -> static audit -> build output audit -> runtime audit
- 负责 artifacts 落盘和失败状态收口

建议新增函数：

```python
def execute_subpath_pipeline(
    repo_dir: Path,
    project_slug: str,
    *,
    enable_runtime_audit: bool,
    host_port: Optional[int] = None,
) -> SubpathPipelineResult:
    ...
```

当前主流程挂点：

- [main()](../runner.py:5061) 中 `5064-5089` 是“源码期子路径阶段”
- [main()](../runner.py:5343) 是“运行期子路径阶段”

应改为：

1. `plan = build_subpath_plan(repo_dir)`
2. `prepare = run_subpath_prepare_phase(plan, repo_dir, project_slug)`
3. `static = run_subpath_static_phase(plan, repo_dir, project_slug)`
4. build
5. `build_output = run_subpath_build_output_phase(...)`
6. run
7. `runtime = run_subpath_runtime_phase(...)`

## 6. 推荐实施顺序

### 第一阶段

- 重构识别层，建立 evidence 模型
- 把 repo 级 strategy 改成 project 级 plan
- 统一 workspace 框架识别

交付标准：

- `vite / nextjs / vue_cli / cra / generic` 都能在 workspace 下产出独立 project strategy
- `express_static` 不再因目录启发轻易误判

### 第二阶段

- 重构 rewrite / static audit / auto-fix 的边界
- 修复多项目配置注入问题
- 引入 build output audit

交付标准：

- auto-fix 不再只是重复通用 rewrite
- 多前端包仓库配置改写和扫描目标一致

### 第三阶段

- 增加浏览器 runtime audit
- 引入声明式配置
- 增强 artifacts 和排障信息

交付标准：

- 能输出“为什么识别成这个策略、为什么失败、失败发生在哪个项目和阶段”

## 7. 最小可行改造集

如果只允许先做一轮重构，最值得优先投入的是这 5 项：

1. 建立 runtime root evidence，重写 framework 判定入口
2. 把 `SubpathStrategy` 升级为多项目 `SubpathPlan`
3. 把 workspace 识别从 Vite 扩展到 Next/Vue/CRA
4. 改造配置注入逻辑，支持逐项目执行
5. 把子路径流程从 `main()` 中抽成独立 orchestrator

这 5 项做完，当前子路径体系就会从“若干启发式规则的组合”升级为“可持续演进的平台子系统”。
