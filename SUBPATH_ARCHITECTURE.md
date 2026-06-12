# Subpath Architecture

本文专门描述 `automation/runner.py` 中当前的子路径适配体系，聚焦：

- 如何识别项目是否需要子路径适配
- 如何发现前端项目根与运行时静态根
- 源码改写、静态审计、运行时审计分别做什么
- 当前方案的通用性、兼容性边界
- 已知问题与优化方向

本文以当前代码实现为准，不以旧文档表述为准。

关键代码入口：

- [detect_subpath_strategy()](./runner.py:2910)
- [detect_frontend_runtime_roots()](./runner.py:1719)
- [find_frontend_rewrite_targets()](./runner.py:2552)
- [find_subpath_audit_targets()](./runner.py:2623)
- [rewrite_frontend_subpath_urls()](./runner.py:3472)
- [run_static_subpath_audit()](./runner.py:2833)
- [run_runtime_subpath_audit()](./runner.py:4473)

## 1. 目标

平台统一把应用暴露在：

```text
/tools2/<project_slug>
```

子路径体系的目标不是“把任意项目完美迁移成多租户 Web 应用”，而是：

1. 在不要求上游仓库主动适配的前提下，让常见前端/全栈项目尽量可部署
2. 尽可能在构建前通过静态分析发现问题
3. 对已知模式自动修复
4. 在服务启动后再通过运行时审计兜底

## 2. 总体链路

子路径处理发生在 4 个阶段：

1. 识别项目类型和运行模式
2. 发现前端项目根、源码改写目标和运行时静态根
3. 执行源码改写，并做静态审计
4. 容器启动后，再做运行时审计

对应顺序是：

1. `detect_subpath_strategy()`
2. `apply_subpath_rewrites()`
3. `run_static_subpath_audit()`
4. `run_runtime_subpath_audit()`

## 3. 项目识别

### 3.1 支持的框架类别

当前会识别：

- `nextjs`
- `vite`
- `vue_cli`
- `cra`
- `express_static`
- `static_html`
- `generic`

入口：

- [detect_subpath_strategy()](./runner.py:2910)

### 3.2 代理模式

当前有两种模式：

- `preserve_prefix`
- `strip_prefix`

含义：

- `preserve_prefix`
  应用自身理解 `/tools2/<slug>`，请求路径原样转发给应用

- `strip_prefix`
  应用只理解根路径 `/`，由外层代理把 `/tools2/<slug>` 去掉后再转发

### 3.3 当前判定规则

#### `nextjs`

- 走 `preserve_prefix`
- 因为 Next.js 原生支持 `basePath` / `assetPrefix`

#### `vite`

- 默认识别为 `vite`
- 若同时满足：
  - 能识别到 Node/TS 后端入口
  - 能识别到前端运行时静态根
  则走 `strip_prefix`
- 否则走 `preserve_prefix`

这条规则是为了解决这类项目：

- Vite 只负责构建静态资源
- 后端（如 Fastify / Express）把 `dist/` 挂在 `/`
- 应用本身并不理解 `/tools2/<slug>/...`

对应代码：

- [detect_node_entry_script_paths()](./runner.py:1446)
- [detect_static_root_hints_from_node_entry()](./runner.py:1525)
- [detect_frontend_runtime_roots()](./runner.py:1719)
- [detect_subpath_strategy()](./runner.py:2910)

#### `vue_cli` / `cra`

- 走 `preserve_prefix`
- 通过配置字段注入子路径

#### `express_static` / `static_html` / `generic`

- 走 `strip_prefix`
- 依赖源码改写和运行时注入兜底

## 4. 前端项目根与运行时静态根发现

子路径体系依赖两个概念：

- 前端项目根：哪一层目录里放着框架配置和源码入口
- 运行时静态根：应用启动后会对外提供哪些 HTML/JS/CSS/静态资源目录

### 4.1 workspace / monorepo 识别

当前会从两处发现 workspace：

- 根 `package.json.workspaces`
- `pnpm-workspace.yaml`

对应代码：

- [discover_workspace_packages()](./runner.py:1012)

这一步会记录：

- package 名称
- 相对目录
- `package_dir`
- scripts
- dependencies

### 4.2 Node 入口脚本发现

当前会解析：

- 根 scripts 里的 `node ...`
- `tsx ...`
- `ts-node ...`
- `npm run <script> --workspace=<name>`
- `npm --workspace=<name> run <script>`

这一步是为了继续追到：

- 后端实际入口文件
- 后端静态目录引用

对应代码：

- [extract_node_script_path_from_command()](./runner.py:1416)
- [extract_workspace_script_reference()](./runner.py:1426)
- [detect_node_entry_script_paths()](./runner.py:1446)

### 4.3 Node 静态根发现

当前支持从后端入口文件里识别：

- `express.static(...)`
- `serveStatic(...)`
- `koaStatic(...)`
- `fastifyStatic` 的 `root: ...`

也支持：

- `const frontendDist = path.resolve(__dirname, "../../frontend/dist")`
- 再在 `root: frontendDist` 中引用

对应代码：

- [detect_static_root_hints_from_node_entry()](./runner.py:1525)

### 4.4 Python 模板/静态目录发现

当前支持：

- FastAPI `Jinja2Templates(directory=...)`
- FastAPI `StaticFiles(directory=...)`
- Flask `template_folder=...`
- Flask `static_folder=...`

对应代码：

- [detect_frontend_runtime_roots()](./runner.py:1719)

### 4.5 Vite 项目根发现

当前会识别：

- 根目录的 Vite 项目
- workspace 子包里的 Vite 项目

判定依据包括：

- `vite` 依赖
- `vite.config.*`
- scripts 中出现 `vite`

对应代码：

- [workspace_frontend_package_dirs()](./runner.py:1576)
- [vite_project_roots()](./runner.py:1595)

## 5. 源码改写

### 5.1 框架配置级改写

当前会做：

- Next.js：补 `basePath` / `assetPrefix`
- Vite：补 `base`
- Vue CLI：补 `publicPath`
- CRA：补 `homepage`

对应代码：

- [ensure_nextjs_basepath_config()](./runner.py:3025)
- [ensure_vite_base_config()](./runner.py:2952)
- [ensure_vue_cli_public_path()](./runner.py:2995)
- [ensure_cra_homepage()](./runner.py:3023)

### 5.2 源码级改写

当前会改写：

- HTML 的 `href="/..."` / `src="/..."` / `action="/..."`
- JS/TS 中的 `fetch('/...')`
- 模板字符串 `fetch(\`/api/...${}\`)`
- `axios.get('/...')`
- `url: '/...'`
- `return '/api/...'`
- ``return `/api/...${}```
- 部分 `window.location.href = ...`

对应代码：

- [rewrite_frontend_subpath_urls()](./runner.py:3472)
- [rewrite_origin_based_subpath_logic()](./runner.py:3406)
- [rewrite_vite_fetch_template_calls()](./runner.py:3452)

### 5.3 运行时 helper 注入

HTML 会注入：

- `window.__TOOL_BASE_PATH__`
- `window.__TOOL_ORIGIN_URL__`
- `window.withToolBase()`

对应代码：

- [inject_tool_base_runtime()](./runner.py:3273)

### 5.4 当前 Vite 特殊规则

为兼容 Vite：

- `index.html` 中 `/src/main.tsx` 视为构建入口，不作为错误
- `index.html` 中 `/vite.svg` 会被改写
- `src/**/*.ts(x)` 会进入 rewrite / audit

对应代码：

- [is_allowed_vite_root_html_url()](./runner.py:2579)
- [find_frontend_rewrite_targets()](./runner.py:2552)
- [find_subpath_audit_targets()](./runner.py:2623)
- [apply_vite_subpath_adapter()](./runner.py:2985)

## 6. 静态子路径审计

静态审计负责在构建前发现明显问题。

入口：

- [run_static_subpath_audit()](./runner.py:2833)

### 6.1 审计目标

当前会扫描：

- HTML
- JS / MJS
- 对 `nextjs` 还会扩展到 TS / TSX / JSX
- 对 Vite workspace 项目会补扫描 `frontend/index.html` 和 `frontend/src/**/*.ts(x)`

目标发现入口：

- [find_subpath_audit_targets()](./runner.py:2623)

### 6.2 审计规则

当前主要检查：

- 根路径 HTML 资源引用
- 根路径浏览器请求 URL
- Next.js 的原始 `<a href="/...">`
- Next.js 的 `<form action="/...">`

规则入口：

- [scan_subpath_findings()](./runner.py:2759)

### 6.3 自动修复

当前自动修复不再仅限 Next.js：

- Next.js：专门修复
- `static_rewrite`：通用二次改写

入口：

- [auto_fix_subpath_issues()](./runner.py:2857)

## 7. 运行时审计

运行时审计负责兜底：

- 构建成功
- 容器启动成功
- 健康检查成功

之后再检查真实返回页面是否仍有子路径问题。

入口：

- [run_runtime_subpath_audit()](./runner.py:4473)

### 7.1 当前检查内容

- 入口页面 HTTP 状态
- 返回内容是否为 HTML
- HTML 中是否仍输出根路径 URL
- 跟随少量链接继续检查

辅助逻辑：

- [collect_runtime_follow_links()](./runner.py:4456)

### 7.2 当前边界

运行时审计目前更像“轻量兜底”，不是完整浏览器回放。它抓不到很多用户交互后才出现的问题，例如：

- 点击按钮后触发的请求
- 表单上传
- SPA 路由切换后懒加载内容
- SSE / WebSocket
- 下载链接由 JS 在运行中拼装

## 8. 当前方案的优点

1. 覆盖面已经明显高于单纯的框架特判
2. 能处理一部分真实 monorepo / workspace 项目
3. 同时具备：
   - 配置级修复
   - 源码级改写
   - 静态审计
   - 运行时兜底
4. 可以在大量项目上用“足够好”的工程启发式自动过部署

## 9. 明显问题

### 9.1 文档与代码不一致

当前 [DEPLOYMENT_FLOW.md](./DEPLOYMENT_FLOW.md) 中关于：

- Vite 一律 `preserve_prefix`
- 自动修复只覆盖 Next.js

已经不符合当前代码。

### 9.2 识别逻辑仍强依赖正则和目录启发式

例如：

- Node 入口脚本靠正则
- Fastify / Express 静态目录靠正则
- workspace 前端靠依赖和脚本名猜测
- 前后端文件边界靠目录名和文件名猜测

这决定了它的通用性是“工程上可接受”，不是严格语义正确。

### 9.3 改写逻辑仍较分散

当前 Vite、Next.js、generic 分别有自己的改写分支，公共行为和特例混在一起，维护成本会继续上升。

### 9.4 rewrite 对 TS/构建系统的影响需要持续防守

这次 `decrypt-online` 已经证明：

- 静态审计过了
- 运行时逻辑设计也没问题
- 但 rewrite 仍可能把源码改成“编译不过”的形式

所以 build 级回归测试必须继续补。

### 9.5 运行时审计覆盖有限

它不是浏览器自动化，不会真正执行用户行为。

## 10. 通用性和兼容性判断

### 10.1 通用性

当前可认为是：

- 对主流 Web 项目：中等偏高
- 对复杂工程：有限

更准确地说，它是：

- 一个平台部署自动修复器
- 不是一个完备的前端/全栈编译迁移器

### 10.2 兼容性

当前兼容性较好的项目类型：

- Next.js
- 普通 Vite
- workspace Vite
- CRA
- Vue CLI
- Express/Fastify 静态托管
- FastAPI/Flask 模板目录

当前高风险边界：

- 更复杂的 monorepo 前端组织
- 动态生成的模板目录/静态目录
- 非主流打包器
- 运行时交互驱动的路径拼装
- 依赖更多 TypeScript 类型约束的前端代码

## 11. 优化方向

### 11.1 短期最值得做

1. 更新 `DEPLOYMENT_FLOW.md`
2. 持续补 build 级回归测试
3. 把 Vite / generic rewrite 继续从“直接操作 `window` 全局”收敛成更稳定的 helper 形式
4. 把 `downloadUrl()` / 上传 / SSE 这类模式继续纳入专项测试

### 11.2 中期建议

建议把“识别结果”结构化，而不是让每个阶段各自再猜一遍。可以抽成类似：

```python
{
  "frontend_projects": [
    {
      "framework": "vite",
      "project_root": "frontend",
      "proxy_mode": "strip_prefix",
      "runtime_roots": ["frontend/dist"],
      "source_roots": ["frontend/src"],
      "config_file": "frontend/vite.config.ts",
    }
  ]
}
```

这样：

- rewrite
- 静态审计
- 运行时审计

都能共用一份检测结果，减少重复推断和分支漂移。

### 11.3 长期建议

如果要继续提升通用性，方向不是继续堆正则，而是：

1. 对前端源码做 AST 级处理
2. 对框架配置做结构化修改
3. 对运行时审计引入轻量浏览器自动化

否则随着支持模式增加，rewrite 规则会越来越脆。

## 12. 当前建议

当前系统已经足够支持“平台内部大量项目自动部署”，但不适合再继续把所有复杂度都堆在一个巨大的 regex 改写器里。

更合理的下一步是：

1. 先同步文档
2. 把检测结果结构化
3. 再逐步把 Vite / generic rewrite 从正则迁移到更稳定的语义处理

