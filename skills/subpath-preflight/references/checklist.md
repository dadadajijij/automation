# 客户改造清单

## 1. 入口和框架配置

检查：

- Next.js 是否设置了 `basePath` / `assetPrefix`
- Vite 是否设置了 `base`
- Vue CLI 是否设置了 `publicPath`
- CRA 是否设置了 `homepage`

要求：

- 不要只改前端请求，不改框架配置
- 也不要只改框架配置，不改源码中的手写绝对路径

## 2. HTML 静态资源

检查：

- `href="/favicon.ico"`
- `href="/vite.svg"`
- `src="/logo.png"`
- `src="/assets/..."`

要求：

- 所有手写的根路径静态资源都要处理
- 对 Vite 项目，`/src/main.tsx` 这类构建入口不要误改

## 3. API 请求

检查：

- `fetch('/api/...')`
- ``fetch(`/api/...${}`)``
- `axios.get('/api/...')`
- `url: '/api/...'`
- `new Request('/api/...')`

要求：

- 所有浏览器侧 API 路径都必须子路径兼容
- 不能只修一两个入口函数

## 4. EventSource / SSE

检查：

- `new EventSource('/api/...')`
- ``new EventSource(`/api/...${}`)``

要求：

- 这类路径极易漏掉
- 页面能打开不代表 SSE 正常

## 5. 下载链接

检查：

- `downloadUrl()`
- `return '/api/.../download'`
- ``return `/api/.../${id}/download` ``
- `window.location.href = ...`

要求：

- 下载路径必须和 API 路径一样做子路径兼容

## 6. 跳转逻辑

检查：

- `window.location.href = '/...'`
- `window.location.assign('/...')`
- `window.open('/...')`

要求：

- 所有浏览器跳转都要检查

## 7. 后端静态目录挂载

检查：

- `express.static(...)`
- `fastifyStatic`
- `StaticFiles(...)`
- Flask `static_folder`

要求：

- 明确运行时静态根是什么
- 明确它是挂在 `/` 还是自己理解子路径

## 8. workspace / monorepo

检查：

- 前端是否在子包目录
- 后端是否在另一个子包
- 构建命令是否跨 workspace

要求：

- 必须同时检查前端包和后端包
- 不能只改 repo 根文件

## 9. 本地验证

客户提交前必须验证：

- 前端 build 成功
- 后端 build 成功
- 服务启动成功
- 页面打开成功
- 上传成功
- API 成功
- SSE 成功
- 下载成功

## 10. 交付前结论

至少确认：

- 哪些文件已经改
- 哪些风险还没验证
- 是否建议提交给平台

