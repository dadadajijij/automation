# 常见代码示例

## 1. Vite 设置 `base`

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/tools2/my-tool/',
  plugins: [react()],
})
```

## 2. 修正手写 favicon

错误：

```html
<link rel="icon" href="/vite.svg" />
```

改成：

```html
<link rel="icon" href="/tools2/my-tool/vite.svg" />
```

## 3. 修正 fetch

错误：

```ts
fetch('/api/jobs')
```

建议：

```ts
const base = '/tools2/my-tool'
fetch(`${base}/api/jobs`)
```

如果客户自己有公共 helper，更建议统一封装：

```ts
function withBase(path: string): string {
  return `/tools2/my-tool${path}`
}

fetch(withBase('/api/jobs'))
```

## 4. 修正模板字符串 fetch

错误：

```ts
fetch(`/api/jobs/${jobId}`)
```

改成：

```ts
fetch(`/tools2/my-tool/api/jobs/${jobId}`)
```

或：

```ts
fetch(withBase(`/api/jobs/${jobId}`))
```

## 5. 修正 EventSource / SSE

错误：

```ts
new EventSource(`/api/jobs/${jobId}/progress`)
```

改成：

```ts
new EventSource(`/tools2/my-tool/api/jobs/${jobId}/progress`)
```

或：

```ts
new EventSource(withBase(`/api/jobs/${jobId}/progress`))
```

## 6. 修正下载链接 helper

错误：

```ts
export function downloadUrl(jobId: string): string {
  return `/api/jobs/${jobId}/download`
}
```

改成：

```ts
export function downloadUrl(jobId: string): string {
  return `/tools2/my-tool/api/jobs/${jobId}/download`
}
```

或：

```ts
export function downloadUrl(jobId: string): string {
  return withBase(`/api/jobs/${jobId}/download`)
}
```

## 7. Fastify 静态托管 Vite 产物

```ts
const frontendDist = path.resolve(__dirname, '../../frontend/dist')

await fastify.register(fastifyStatic, {
  root: frontendDist,
  prefix: '/',
})
```

这种情况下通常代表：

- 前端运行时只理解根路径 `/`
- 外层代理应走 `strip_prefix`
- 前端源码中的 `/api/...`、`/vite.svg`、SSE 路径必须自己修

## 8. FastAPI 模板中的静态资源

错误：

```html
<link rel="stylesheet" href="/static/styles.css" />
<script src="/static/app.js"></script>
```

改成：

```html
<link rel="stylesheet" href="/tools2/my-tool/static/styles.css" />
<script src="/tools2/my-tool/static/app.js"></script>
```

## 9. Flask 模板中的静态资源

错误：

```html
<script src="/static/app.js"></script>
```

改成：

```html
<script src="/tools2/my-tool/static/app.js"></script>
```

## 10. 交付前最小验收

至少验证这些请求最终都带了子路径：

- 页面入口
- `/api/...`
- `/api/.../progress`
- `/api/.../download`
- favicon / logo / assets

