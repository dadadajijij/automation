# Subpath Enforcement Policy

本文定义当前子路径 build output audit 的 rollout 策略。目标不是立即把 shadow mode 改成 hard fail，而是把“哪些 findings 将来适合进入 enforce”收成稳定、可演进的规则。

当前前提：

- `runner.py` 默认仍保持 build output audit 为 shadow mode
- findings 只落 artifact / summary，并追加 warning
- 只有 policy 明确标记为 `enforce` 的 finding 才会阻断 `COMPLETED_WITH_BUILD` / `RUN_SUCCEEDED`

---

## 1. 当前策略版本

当前 build output audit rollout 策略版本：

- `policy_version = "v1"`

该字段已经写入：

- `artifacts["subpath_build_output_audit"]["summary"]["policy_version"]`
- `analysis_summary["subpath_build_output_audit_summary"]["policy_version"]`

后续如果调整 code -> category / severity / enforcement candidate 映射，应提升版本号。

---

## 2. 当前 code 策略映射

### `build_output_html_root_relative_url`

- `category = "html"`
- `severity = "error"`
- `enforcement_candidate = "future_blocker"`

理由：

- 这类问题通常意味着构建产物 HTML 仍然直接输出根相对资源或链接
- 风险高，且更接近“发布后立即可见”的子路径破坏

### `build_output_client_root_relative_url`

- `category = "client"`
- `severity = "error"`
- `enforcement_candidate = "observe"`

理由：

- 这类问题通常来自 built JS 中的请求、导航或运行时路径
- 风险真实存在，但误报和上下文依赖性高于 HTML
- 当前阶段适合继续 shadow 观察

### `build_output_manifest_root_relative_url`

- `category = "manifest"`
- `severity = "error"`
- `enforcement_candidate = "observe"`

理由：

- 这类问题通常来自 manifest / metadata
- 是否构成真实运行破坏依赖部署方式和消费方式
- 当前阶段继续 shadow 更稳

### 未知 code

默认策略：

- `category = "other"`
- `severity = "error"`
- `enforcement_candidate = "observe"`

理由：

- 新 code 在没有明确样本和验证前，不直接纳入 blocker

---

## 3. recommended_mode 规则

当前 summary 中的：

- `recommended_mode`

按 `enforcement_candidate_counts` 推导：

### `candidate_for_enforce`

条件：

- 仅出现 `future_blocker`

表示：

- 当前 findings 主要是高置信度 blocker 候选
- 适合进入后续 enforce 评估名单

### `enforce`

条件：

- 出现 `enforce`

表示：

- 当前 findings 已被显式策略要求直接阻断
- runner 会把状态切为 `SUBPATH_BUILD_OUTPUT_AUDIT_FAILED`

### `shadow_only`

条件：

- 只出现 `observe`

表示：

- 当前 findings 仍应保持 shadow 观察

### `mixed_shadow`

条件：

- 同时出现 `future_blocker` 与 `observe`

表示：

- 当前 findings 混合了高风险和观察项
- 仍建议保持 shadow，并先看明细分布

---

## 4. 当前 rollout 原则

### 4.1 先 summary，后拦截

在没有足够样本前：

- 默认只写 summary / warning
- 只有显式 policy override 才进入 hard fail

### 4.2 先 HTML blocker，后 client / manifest

当前若进入 enforce，优先建议从：

- `build_output_html_root_relative_url`

暂不优先考虑：

- `build_output_client_root_relative_url`
- `build_output_manifest_root_relative_url`

### 4.3 先单 code rollout，再扩展

建议顺序：

1. 先只对高置信度 HTML blocker 做 enforce
2. 稳定后再评估 client 类
3. manifest 类保持更长时间的 observe

---

## 5. 后续可演进方向

### 5.1 增加更细 severity

当前 severity 仍然基本统一是：

- `error`

后续可以扩展：

- `warning`
- `notice`

### 5.2 增加按环境或项目类型的策略层

例如未来可以支持：

- 静态站更严格
- 混合 SSR/CSR 项目更保守

### 5.3 进入 enforce 的状态设计

后续候选：

- 新增 `SUBPATH_BUILD_OUTPUT_AUDIT_FAILED`
- 或继续通过现有状态机包装错误

当前已经支持：

- 显式 policy override 把某类 code 标成 `enforce`
- `.ka/subpath.json` 的 `build_output_policy` 可直接提供该 override
- runner 在命中时返回 `SUBPATH_BUILD_OUTPUT_AUDIT_FAILED`

默认策略仍然不会自动进入 enforce。
