# Subpath Policy

本文定义平台内部针对根路径部署项目的统一子路径审核处理规范。

目标不是解释某个函数怎么写，而是明确：

- 哪些项目必须进入子路径链路
- 子路径链路的标准输入输出是什么
- 审核、处理、自动修复、失败收口分别怎么做
- 哪些问题允许放行，哪些必须失败

本文以当前 `runner.py` 实现为准，并约束后续演进方向。

关键实现入口：

- [detect_subpath_strategy()](./runner.py:2998)
- [find_subpath_audit_targets()](./runner.py:2690)
- [find_strategy_rewrite_targets()](./runner.py:2784)
- [apply_subpath_rewrites()](./runner.py:3724)
- [run_static_subpath_audit()](./runner.py:2938)
- [auto_fix_subpath_issues()](./runner.py:2962)
- [run_runtime_subpath_audit()](./runner.py:4584)

## 1. 基本原则

所有平台项目统一对外暴露在：

```text
/tools2/<project_slug>
```

对子路径处理有 4 条硬原则：

1. 先识别，再处理，不允许 rewrite、audit、runtime 各自重复猜项目形态。
2. 优先使用框架原生能力，再使用通用源码改写兜底。
3. 静态审计和自动修复必须消费同一份策略结果，避免“审计能发现、处理不覆盖”。
4. 运行时审计只做兜底，不承担主修复职责。

## 2. 统一输入模型

子路径链路的唯一标准输入是 `SubpathStrategy`。

但在 `detect_subpath_strategy()` 内部，当前已经允许两层来源：

1. 项目显式声明
2. 平台自动识别

优先级固定：

1. 若仓库存在 `.ka/subpath.json` 且格式有效，优先使用声明式配置构建内部 `SubpathPlan`
2. 若无声明或声明无效，再走自动识别
3. 自动识别仍无法明确时，退化到 `generic + strip_prefix + static_rewrite`

标准字段：

```python
SubpathStrategy(
  framework: str,
  proxy_mode: str,
  adapter: str,
  source_adapter: str,
  frontend_projects: tuple[FrontendProject, ...],
  notes: tuple[str, ...],
)
```

其中每个 `FrontendProject` 至少包含：

```python
FrontendProject(
  framework: str,
  project_root: Path,
  source_roots: tuple[Path, ...],
  runtime_roots: tuple[Path, ...],
  config_file: Optional[Path],
  proxy_mode: str,
  adapter: str,
  source_adapter: str,
)
```

要求：

- `detect_subpath_strategy()` 是唯一允许产出该结构的入口。
- `apply_subpath_rewrites()`、`run_static_subpath_audit()`、`auto_fix_subpath_issues()`、`run_runtime_subpath_audit()` 必须以该结构为准。
- `analysis_summary` 必须落盘这份结构，便于事后诊断。

补充要求：

- 如果 `.ka/subpath.json` 生效，`analysis_summary` 和 `artifacts` 中必须能看到：
  - `subpath_declaration`
  - `subpath_plan`
- 平台不允许“部分声明、部分自动猜”的隐式混合模式。
  当前实现采用“声明有效则整份计划优先信声明”的策略。

## 3. 策略选择规则

平台只允许两种代理模式：

- `preserve_prefix`
- `strip_prefix`

含义：

- `preserve_prefix`：应用自身理解 `/tools2/<slug>`，代理不去前缀。
- `strip_prefix`：应用只理解 `/`，外层代理转发前去掉 `/tools2/<slug>`。

默认选择规则：

1. Next.js
   走 `preserve_prefix`
   原因：原生支持 `basePath` / `assetPrefix`

2. 纯前端 Vite
   走 `preserve_prefix`
   原因：原生支持 `base`

3. Vite 构建产物由 Node 服务挂在 `/`
   走 `strip_prefix`
   原因：运行时往往只理解根路径 `/`

4. Vue CLI / CRA
   走 `preserve_prefix`
   原因：分别有 `publicPath` / `homepage`

5. Express / Fastify / Koa 静态托管
   默认走 `strip_prefix`

6. FastAPI / Flask 模板渲染
   默认走 `strip_prefix`

7. 纯静态 HTML
   默认走 `strip_prefix`

如果识别不准：

- 不允许跳过子路径链路
- 必须退化到 `generic + strip_prefix + static_rewrite`

### 3.1 声明式配置规范

当前最小支持的显式配置文件为：

```text
.ka/subpath.json
```

最小示例：

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

规则：

- `root`、`framework`、`proxy_mode` 是声明式项目的最小必填集合
- `adapter` 缺省时：
  - `nextjs / vite / vue_cli / cra` 默认取同名 adapter
  - 其他框架默认取 `static_rewrite`
- `source_adapter` 缺省时：
  - `nextjs` 默认 `nextjs`
  - 其他框架默认 `static_rewrite`
- 配置文件和路径字段都按 repo 相对路径解析
- 路径不存在时该字段会被忽略，不会自动生成虚假路径

失败行为：

- `.ka/subpath.json` 不存在：正常走自动识别
- 文件存在但 JSON 非法：当前实现退回自动识别，不阻断流程
- `projects` 缺失或全部无效：当前实现退回自动识别，不阻断流程
- 如果声明有效并成功产出至少一个 project strategy，则后续不再回退自动识别结果

## 4. 处理链路

标准顺序固定为 8 步：

1. 识别项目并生成 `SubpathStrategy`
2. 做框架配置级修复
3. 做源码级通用 rewrite
4. 跑首轮静态审计
5. 若有 finding，执行自动修复
6. 跑二次静态审计
7. 构建并运行服务
8. 跑运行时审计

任何新逻辑都必须挂到这 8 步之一，不允许额外插入“旁路修复器”。

## 5. 配置级修复规范

配置级修复只负责让框架理解子路径，不负责修正业务源码中的根路径调用。

当前标准动作：

- Next.js：补 `basePath` / `assetPrefix`
- Vite：补 `base`
- Vue CLI：补 `publicPath`
- CRA：补 `homepage`

要求：

- 配置级修复必须幂等。
- 配置级修复完成后，仍必须进入源码级 rewrite。
- 不允许因为“框架支持 basePath”就跳过源码审计。

## 6. 源码级 rewrite 规范

源码级 rewrite 分两类：

1. `nextjs`
   使用 Next 专项修复

2. `static_rewrite`
   使用通用 rewrite 兜底

`source_adapter` 用来决定源码层策略，不再复用 `adapter`。

这是强约束：

- `adapter` 表示框架配置怎么修
- `source_adapter` 表示源码怎么修

例如：

- `vite` 项目：`adapter="vite"`，`source_adapter="static_rewrite"`
- `vue_cli` 项目：`adapter="vue_cli"`，`source_adapter="static_rewrite"`
- `cra` 项目：`adapter="cra"`，`source_adapter="static_rewrite"`

## 7. rewrite 目标文件规范

rewrite 目标必须由统一函数选择，不允许每个阶段各扫一遍不同文件。

标准规则：

1. Next.js
   扫描 `src/app`、`pages`、`components` 等浏览器侧 TS/TSX/JS/JSX

2. Vite / Vue CLI / CRA
   扫描：
   - `index.html`
   - `src/**/*.ts(x)|js(x)|mjs`
   - runtime root 下可被浏览器直接消费的 `html/js/mjs`

3. Generic / static_html / express_static
   扫描：
   - runtime root 下的 `html/js/mjs`
   - `views/`、`templates/`、`static/`、`public/` 等浏览器面文件

禁止项：

- 不要把后端入口文件、API route、测试文件混入 rewrite 目标
- 不要把 `dist/`、`build/`、`.next/`、`node_modules/` 混入扫描范围

## 8. 静态审计规范

静态审计是 build 前的主闸门。

必须检查：

- HTML 的 `href="/..."` / `src="/..."` / `action="/..."`
- `fetch('/...')`
- `axios.get('/...')` 等直接请求
- `new Request('/...')`
- `new EventSource('/...')`
- `url: '/...'`
- `return '/...'`
- `window.location.href = '/...'`
- `window.location.assign('/...')`
- `window.open('/...')`

框架专项规则：

- Next.js 禁止原始 `<a href="/...">`
- Next.js 禁止原始 `<form action="/...">`
- Vite `index.html` 中 `/src/...` 构建入口允许保留

静态审计失败准则：

- 首轮静态审计有 finding：进入自动修复
- 自动修复后仍有 finding：直接 `SUBPATH_STATIC_AUDIT_FAILED`

## 9. 自动修复规范

自动修复只允许做两类事情：

1. 配置缺失补齐
2. 已知安全的源码替换

自动修复必须满足：

- 幂等
- 不引入明显的 TS/构建错误
- 不修改后端服务语义
- 不改变外部 API 业务行为

自动修复后必须立即重跑静态审计，不允许直接继续 build。

补充：

- 自动修复的判断依据当前不再只是一组框架 if/else，还会参考 rewrite rule 的 `finding_codes`
- 平台允许保留少量兼容型兜底映射，但新增修复能力必须优先挂到结构化 rewrite rule 元数据中

## 10. 运行时审计规范

运行时审计只做轻量兜底，标准动作如下：

1. 访问入口 URL
2. 校验 HTTP 状态
3. 若返回 HTML，提取页面中的 URL
4. 检查是否继续泄漏部署子路径之外的根相对路径
5. 跟随少量同域子链接继续检查

运行时审计失败条件：

- 入口页返回 4xx/5xx
- 跟随页返回 4xx/5xx
- HTML 继续输出 `/tools2/<slug>` 之外的根相对 URL

## 11. 可观测性输出

当前子路径链路的标准诊断输出至少包括：

- `analysis_summary.subpath_strategy`
- `analysis_summary.subpath_plan`
- `analysis_summary.subpath_declaration`
- `artifacts.subpath_plan`
- `artifacts.subpath_detection_evidence`
- `artifacts.subpath_rewrite_report`
- `artifacts.subpath_static_audit`
- `artifacts.subpath_build_output_audit`
- `artifacts.subpath_runtime_audit`

其中：

- `subpath_declaration`
  表示用户显式提交的 `.ka/subpath.json` 原始内容；如果未提供则为空

- `subpath_rewrite_report`
  表示每个被改写文件实际命中的 rewrite rule id 列表

标准格式：

```json
{
  "frontend/src/api/jobs.ts": ["generic_fetch_call", "generic_return_root_path"],
  "src/app/page.tsx": ["nextjs_request_call", "nextjs_window_open"]
}
```

要求：

- `subpath_rewrite_report` 只记录实际命中的 rule id
- 不允许输出“候选但未命中”的规则
- rule id 一旦对外落盘，就应视为稳定诊断标识，后续变更需要兼容考虑

运行时审计不覆盖：

- 点击后触发的请求
- 上传表单
- 登录后页面
- SSE / WebSocket 真实连通性
- SPA 懒加载后的动态内容
- 下载链接在浏览器交互中拼装的情况

这些问题必须通过静态规则和专项回归测试补足。

## 11. 允许放行的例外

当前仅允许两类例外：

1. 明确已经带部署前缀的 URL
2. 文件内显式标注 `ka-subpath-allow-root`

例外要求：

- 必须可审计
- 必须局部生效
- 必须有明确理由

不允许建立全局“跳过子路径审计”的开关。

## 12. 平台失败收口规则

子路径链路的失败状态只允许两种：

1. `SUBPATH_STATIC_AUDIT_FAILED`
   含义：源码层面仍有明确不兼容问题

2. `SUBPATH_RUNTIME_AUDIT_FAILED`
   含义：服务已启动，但真实返回页面仍不兼容

其中：

- 静态失败优先级高于 build / run
- 运行时失败发生在健康检查通过之后

## 13. 新项目接入准则

针对新项目，不允许先“碰运气部署”，再从失败日志里临时补规则。

标准接入动作应是：

1. 识别框架类型
2. 确认 `proxy_mode`
3. 确认前端项目根
4. 确认运行时静态根
5. 确认框架配置文件
6. 运行静态审计
7. 补专项规则或回归测试
8. 再进入批量部署

如果一个新模式不能稳定映射到现有规则，应优先补：

- `detect_subpath_strategy()` 识别
- `find_subpath_audit_targets()` 目标发现
- 对应专项测试

而不是继续堆单点正则。

## 14. 后续演进要求

短期要求：

- 所有框架都统一走 `SubpathStrategy`
- `analysis_summary` 必须带 `subpath_strategy`
- build 级回归测试继续补齐

中期要求：

- `FrontendProject` 继续细化到 monorepo 多前端场景
- rewrite 逐步从正则迁移到语义级处理
- 运行时审计引入轻量浏览器自动化

长期要求：

- 平台侧自动修复只保留“通用安全修复”
- 更复杂的路径适配尽量前置到客户仓库自身改造
