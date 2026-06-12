# 框架判定矩阵

## 目标

帮助客户先判断项目应走：

- `preserve_prefix`
- `strip_prefix`

## 1. Next.js

特征：

- 依赖里有 `next`
- 有 `next.config.js` / `next.config.mjs` / `next.config.ts`

默认策略：

- `preserve_prefix`

客户应改：

- `next.config.*` 里的 `basePath`
- `next.config.*` 里的 `assetPrefix`
- 页面里的原始 `<a href="/...">`
- 客户端里的 `fetch('/...')`

## 2. Vite

特征：

- 依赖里有 `vite`
- 或存在 `vite.config.ts` / `vite.config.js` / `vite.config.mjs`
- 或脚本里出现 `vite`

### 2.1 纯前端静态站

默认策略：

- `preserve_prefix`

客户应改：

- `vite.config.*` 里的 `base`
- `index.html` 里手写的 `/logo.svg`、`/favicon.svg`
- `src/**/*` 里所有 `fetch('/...')`
- `EventSource('/...')`
- 下载 URL helper

### 2.2 Vite 只负责构建，后端把 `dist/` 挂在 `/`

常见后端：

- Fastify
- Express
- Koa

默认策略：

- `strip_prefix`

原因：

- 应用运行时只理解根路径 `/`
- `/tools2/<slug>` 由外层代理去前缀

客户应改：

- 仍然要补 `vite.config.*` 里的 `base`
- 仍然要修源码里的 `/api/...`、`/vite.svg`、SSE、下载链接
- 后端静态目录挂载保持根路径 `/`

## 3. Vue CLI

特征：

- `@vue/cli-service`
- 或 `vue.config.js`

默认策略：

- `preserve_prefix`

客户应改：

- `vue.config.js` 里的 `publicPath`
- 客户端 API / 下载 / SSE 路径

## 4. CRA

特征：

- `react-scripts`

默认策略：

- `preserve_prefix`

客户应改：

- `package.json.homepage`
- 前端 API / 下载 / SSE 路径

## 5. Express / Fastify 静态托管

特征：

- `express.static(...)`
- `fastifyStatic`
- `serveStatic(...)`
- `koaStatic(...)`

默认策略：

- 如果静态目录直接挂在 `/`，通常走 `strip_prefix`

客户应改：

- 前端源码里的根路径调用
- 不要只修框架配置，不修源码

## 6. FastAPI / Flask 模板渲染

特征：

- `Jinja2Templates(directory=...)`
- `StaticFiles(directory=...)`
- `template_folder=...`
- `static_folder=...`

默认策略：

- 多数情况走 `strip_prefix`

客户应改：

- 模板里的 `<script src="/...">`
- 模板里的 `<link href="/...">`
- 前端脚本里的 `fetch('/...')`
- 下载 / SSE / 跳转逻辑

## 7. workspace / monorepo

特征：

- 根 `package.json.workspaces`
- `pnpm-workspace.yaml`

判断原则：

- 不要只看仓库根目录
- 必须找出：
  - 前端子包目录
  - 后端服务入口
  - 运行时静态根

高风险误区：

- 根目录是 Node workspace，但真正前端在 `frontend/`
- Vite 配置在子包里，不在 repo 根
- 后端脚本通过 `npm run start --workspace=backend` 间接启动

## 8. 策略选择总结

优先 `preserve_prefix`：

- Next.js
- 纯前端 Vite
- Vue CLI
- CRA

优先 `strip_prefix`：

- Express/Fastify 根路径静态托管
- FastAPI/Flask 模板渲染
- Vite 构建产物由后端挂在 `/`

如果不确定：

1. 看应用运行时是否真的理解 `/tools2/<slug>`
2. 如果不理解，就不要强行 `preserve_prefix`

