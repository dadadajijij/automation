# Subpath P0 Implementation Design

本文是 `P0` 子路径重构的实施设计，目标不是再次讨论“为什么要改”，而是明确：

- 第一轮重构具体改什么
- `runner.py` 应该先怎么拆
- 哪些兼容层必须保留
- 现有测试应该如何迁移

本文对应上游路线图：

- [SUBPATH_REFACTOR_ROADMAP.md](./SUBPATH_REFACTOR_ROADMAP.md)

关键现有代码：

- [detect_frontend_runtime_roots()](../runner.py:1719)
- [detect_subpath_strategy()](../runner.py:3130)
- [apply_subpath_rewrites()](../runner.py:3883)
- [main() 子路径主流程](../runner.py:5061)

## 1. P0 目标范围

本轮只做 4 件事：

1. 建立 runtime root evidence 模型，降低误判
2. 把 repo 级 `SubpathStrategy` 升级为 project 级 `SubpathPlan`
3. 把 workspace 识别从 Vite 扩展到 Next/Vue/CRA
4. 让配置改写按 project 粒度执行，不再只改第一份配置

本轮明确不做：

- 浏览器级 runtime audit
- build output audit
- rewrite 规则引擎
- 声明式配置文件

这些属于后续 `P1/P2`。

## 2. 实施原则

### 2.1 保持主流程可运行

`runner.py` 里的主链路不能一次性替换为全新实现。第一轮必须采取“新结构 + 兼容层”方式推进：

- 保留 `detect_subpath_strategy()` 对外函数名
- 内部先改为委托 `build_subpath_plan()`
- 再把 plan 兼容映射回旧 `SubpathStrategy`

同理：

- 保留 `detect_frontend_runtime_roots()`
- 保留 `apply_subpath_rewrites()`
- 保留 `find_subpath_audit_targets()`

但让它们优先消费新结构。

### 2.2 先重构识别层，再碰 rewrite

当前最大风险在“识别错了导致后续全错”。所以顺序必须是：

1. 先改 detection
2. 再改 plan
3. 再改 config adapter
4. 最后再改 orchestrator 接线

不要先动 rewrite 正则。

### 2.3 先补测试，再切逻辑

现有测试覆盖已经不差，尤其是这些区域：

- runtime roots 识别： [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:410)
- audit targets： [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:693)
- strategy 检测： [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1381)
- rewrite 行为： [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1142)
- analysis summary： [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1731)

P0 应先新增测试，再修改实现。

## 3. 新数据结构设计

### 3.1 `RuntimeRootEvidence`

用途：

- 替代“直接返回一组 runtime root 路径”的方式
- 保存路径来源、置信度和证据说明

建议结构：

```python
@dataclass(frozen=True)
class RuntimeRootEvidence:
    path: Path
    source: str
    confidence: str
    detail: str
```

取值建议：

- `source`
  - `node_static_call`
  - `python_template_decl`
  - `python_static_decl`
  - `workspace_anchor`
  - `directory_hint`

- `confidence`
  - `high`
  - `medium`
  - `low`

判定建议：

- 从 `express.static(...)` / `fastifyStatic root` / `Jinja2Templates(directory=...)` / `StaticFiles(directory=...)` 抓到的路径：`high`
- 从 workspace 前端包下 `src/public/...` 推断出的路径：`medium`
- 单纯根目录下存在 `src/public/static/...`：`low`

### 3.2 `FrameworkCandidate`

用途：

- 把“项目像什么框架”单独表示出来
- 避免 `detect_subpath_strategy()` 里硬编码串行 `if/elif`

建议结构：

```python
@dataclass(frozen=True)
class FrameworkCandidate:
    framework: str
    package_dir: Path
    confidence: str
    reasons: Tuple[str, ...]
```

### 3.3 `FrontendProjectStrategy`

用途：

- 成为新的 project 级策略载体

建议结构：

```python
@dataclass(frozen=True)
class FrontendProjectStrategy:
    framework: str
    proxy_mode: str
    adapter: str
    source_adapter: str
    project_root: Path
    source_roots: Tuple[Path, ...]
    runtime_roots: Tuple[Path, ...]
    config_file: Optional[Path]
    evidence: Tuple[str, ...] = ()
```

### 3.4 `SubpathPlan`

用途：

- 替代 repo 级 `SubpathStrategy`
- 顶层只承担“多个项目的汇总视图”

建议结构：

```python
@dataclass(frozen=True)
class SubpathPlan:
    projects: Tuple[FrontendProjectStrategy, ...]
    default_project: Optional[Path]
    notes: Tuple[str, ...] = ()
```

### 3.5 旧结构兼容策略

P0 不能直接删掉现有结构：

- [FrontendProject](../runner.py:2537)
- [SubpathStrategy](../runner.py:2571)

建议保留，但调整角色：

- `FrontendProject` 变成 `FrontendProjectStrategy` 的兼容别名或瘦包装
- `SubpathStrategy` 继续提供旧字段：
  - `framework`
  - `proxy_mode`
  - `adapter`
  - `source_adapter`
  - `frontend_projects`

兼容映射规则：

- `framework/proxy_mode/adapter/source_adapter` 取 `default_project`
- `frontend_projects` 映射所有项目
- `notes` 追加“strategy derived from plan”

这样现有调用方不用在第一轮全改。

## 4. 新函数边界

### 4.1 detection 层

建议新增：

```python
def detect_runtime_root_evidence(repo_dir: Path) -> List[RuntimeRootEvidence]: ...
def classify_runtime_roots(evidence: Iterable[RuntimeRootEvidence]) -> Tuple[Tuple[Path, ...], Tuple[Path, ...]]: ...
def discover_frontend_packages(repo_dir: Path) -> List[Path]: ...
def detect_package_framework(package_dir: Path) -> Optional[FrameworkCandidate]: ...
def detect_framework_candidates(repo_dir: Path) -> List[FrameworkCandidate]: ...
def build_subpath_plan(repo_dir: Path) -> SubpathPlan: ...
```

现有函数的命运：

- [detect_static_root_hints_from_node_entry()](../runner.py:1525)
  变成 `detect_node_runtime_root_evidence_from_entry()`
- [detect_frontend_root_hints_from_python_file()](../runner.py:1694)
  变成 `detect_python_runtime_root_evidence_from_file()`
- [detect_frontend_runtime_roots()](../runner.py:1719)
  保留，但内部改为：
  1. `evidence = detect_runtime_root_evidence(repo_dir)`
  2. `confirmed, hinted = classify_runtime_roots(evidence)`
  3. 返回 `confirmed + hinted`

### 4.2 project 组装层

建议新增：

```python
def build_frontend_project_strategy(
    repo_dir: Path,
    candidate: FrameworkCandidate,
    confirmed_runtime_roots: Iterable[Path],
    hinted_runtime_roots: Iterable[Path],
) -> FrontendProjectStrategy: ...

def build_frontend_project_strategies(
    repo_dir: Path,
    candidates: Iterable[FrameworkCandidate],
    evidence: Iterable[RuntimeRootEvidence],
) -> Tuple[FrontendProjectStrategy, ...]: ...
```

### 4.3 adapter 层

建议新增：

```python
def apply_framework_config_adapter(project: FrontendProjectStrategy, project_slug: str) -> List[str]: ...
def apply_framework_config_adapters(plan: SubpathPlan, project_slug: str) -> List[str]: ...
```

现有函数调整：

- [ensure_vite_base_config()](../runner.py:3252) 改为 `ensure_vite_base_config(config_path, project_slug)`
- [ensure_nextjs_basepath_config()](../runner.py:3371) 改为 `ensure_nextjs_basepath_config(config_path, project_slug)`
- [ensure_vue_cli_public_path()](../runner.py:3296) 改为 `ensure_vue_cli_public_path(config_path, project_slug)`
- [ensure_cra_homepage()](../runner.py:3324) 改为 `ensure_cra_homepage(package_path, project_slug)`

### 4.4 compatibility 层

建议新增：

```python
def subpath_plan_to_strategy(plan: SubpathPlan, repo_dir: Path) -> SubpathStrategy: ...
def strategy_to_subpath_plan(strategy: object, repo_dir: Path) -> SubpathPlan: ...
```

用途：

- 保证旧调用方还能跑
- 给第二阶段的完全切换留下缓冲

## 5. 现有函数如何迁移

### 5.1 `detect_subpath_strategy()`

当前：

- 直接做 repo 级框架判断和策略拼装

P0 改造后：

```python
def detect_subpath_strategy(repo_dir: Path) -> SubpathStrategy:
    plan = build_subpath_plan(repo_dir)
    return subpath_plan_to_strategy(plan, repo_dir)
```

这样现有接口不变，但内部语义已经切到 plan。

### 5.2 `frontend_projects_for_strategy()`

当前：

- 在 strategy 缺少 `frontend_projects` 时，自己再从 `framework + runtime_roots` 推导

P0 改造后：

- 优先走 `strategy_to_subpath_plan()`
- 再从 plan 直接产出项目列表
- 不再重复自己猜项目

### 5.3 `apply_subpath_rewrites()`

当前：

- 先 `detect_subpath_strategy(repo_dir)`
- 再根据 repo 级 `adapter` 执行

P0 改造后：

- 先 `plan = build_subpath_plan(repo_dir)`
- 再：
  1. `apply_framework_config_adapters(plan, project_slug)`
  2. 对每个 project 找 rewrite targets
  3. 执行源码 rewrite

兼容要求：

- 返回值仍然是 `list[str]`
- 仍然可被现有主流程直接消费

### 5.4 `collect_repo_analysis()`

当前：

- 调用 [detect_subpath_strategy()](../runner.py:2228)
- 落盘 `subpath_strategy.to_dict(repo_dir)`

P0 改造后：

- 同时落盘：
  - `subpath_strategy`
  - `subpath_plan`
  - `subpath_detection_evidence`

兼容要求：

- `analysis["subpath_strategy"]` 保留
- 新增字段不能影响现有 summary 逻辑

## 6. 迁移步骤

### Step 1: 加数据结构，不改主逻辑

改动：

- 新增 `RuntimeRootEvidence`
- 新增 `FrameworkCandidate`
- 新增 `FrontendProjectStrategy`
- 新增 `SubpathPlan`
- 新增 `to_dict()` 方法

测试：

- 只加结构序列化单测

风险：

- 低

### Step 2: 把 runtime root 检测改成 evidence 驱动

改动：

- 新增 `detect_runtime_root_evidence()`
- 新增 `classify_runtime_roots()`
- `detect_frontend_runtime_roots()` 改成 evidence 汇总兼容层

测试新增：

- `test_detect_runtime_root_evidence_from_express_static_entry`
- `test_detect_runtime_root_evidence_from_fastapi_template_and_static_dirs`
- `test_classify_runtime_roots_separates_confirmed_and_hinted`

现有测试应继续通过：

- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:410)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:434)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:466)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:490)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:520)

### Step 3: 引入 `build_subpath_plan()`

改动：

- 新增 `discover_frontend_packages()`
- 新增 `detect_package_framework()`
- 新增 `build_frontend_project_strategies()`
- 新增 `build_subpath_plan()`
- `detect_subpath_strategy()` 改成 plan 兼容映射

测试新增：

- `test_build_subpath_plan_for_workspace_vite_project`
- `test_build_subpath_plan_for_workspace_next_project`
- `test_build_subpath_plan_for_vue_cli_workspace_package`
- `test_build_subpath_plan_prefers_high_confidence_runtime_roots`

现有测试应继续通过：

- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1381)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1554)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1606)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1644)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1667)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1691)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1720)

### Step 4: 配置改写改成逐项目执行

改动：

- 改造各 `ensure_*` 函数入参
- 新增 `apply_framework_config_adapter()`
- `apply_subpath_rewrites()` 改为遍历 plan.projects

测试新增：

- `test_apply_subpath_rewrites_updates_all_vite_configs_in_monorepo`
- `test_apply_subpath_rewrites_updates_workspace_next_config`

现有测试应继续通过：

- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1142)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1206)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1337)
- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1414)

### Step 5: 分析结果与主流程接线

改动：

- `collect_repo_analysis()` 加 `subpath_plan` 和 `subpath_detection_evidence`
- `main()` 仍然调用旧函数名，但内部已经走 plan

测试新增：

- `test_collect_repo_analysis_includes_subpath_plan_and_evidence`

现有测试应继续通过：

- [tests/test_runner.py](/home/devops/ka/automation/tests/test_runner.py:1731)

## 7. 测试策略

### 7.1 保留原测试，新增平行测试

P0 不建议大规模改原测试断言，因为旧接口还要保留。最好的做法是：

- 原有 `detect_subpath_strategy()` 测试继续保留
- 新增一组 `build_subpath_plan()` 测试

这样可以保证：

- 新结构正确
- 旧兼容层也没坏

### 7.2 新增两类关键回归测试

第一类：误判回归

- 有 `src/` 但没有静态托管调用的 Node 项目，不应被识别为 `express_static`
- 有 `views/templates` 但只是后端模板辅助目录的项目，不应直接提升为强框架

第二类：多项目覆盖回归

- 两个 Vite package 都有 `vite.config.ts`，两者都应被改写
- workspace Next package 应被识别，不应退化为 root generic

## 8. 与主流程的兼容要求

P0 完成后，主流程 [main()](../runner.py:5061) 不应有明显行为变化：

- `analysis_summary.subpath_strategy` 保持存在
- `artifacts.rewritten_frontend_files` 保持存在
- `subpath_proxy_mode` 保持存在
- `subpath_static_audit` / `subpath_runtime_audit` 结果结构保持兼容

允许新增：

- `analysis_summary.subpath_plan`
- `artifacts.subpath_plan`
- `artifacts.subpath_detection_evidence`

## 9. 最小交付标准

P0 完成的判断标准：

1. workspace 下 `vite / nextjs / vue_cli / cra` 都能产出 project 级策略
2. `express_static` 只基于高置信度静态托管证据命中
3. 多前端项目仓库的配置改写不再只改第一份配置
4. 旧接口 `detect_subpath_strategy()`、`apply_subpath_rewrites()`、`find_subpath_audit_targets()` 继续可用
5. 现有核心测试继续通过，且新增 plan/evidence 测试通过

如果这 5 条都满足，就说明 P0 重构已经具备合并价值，可以继续推进 P1。
