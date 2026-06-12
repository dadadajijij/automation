---
name: subpath-preflight
description: 在客户提交代码给平台之前，先指导客户自行把项目改造成可部署到 tools2 路径前缀下的版本。用于 Next.js、Vite、Vue CLI、CRA、Fastify/Express 静态托管、FastAPI/Flask 模板渲染、workspace 或 monorepo 前端项目的源码预改造、自检、风险提示和交付前验收。
---

# Subpath Preflight

## 概述

这个技能用于在客户提交源码之前，先帮助客户自己完成子路径适配，尽量减少提交后才被平台拦截、返工或补修的情况。

目标是让项目在部署到：

`/tools2/<project_slug>`

时，尽可能一次通过构建、静态审计、运行时访问和关键交互验证。

## 你要给客户的输出

不要先讲平台内部实现，优先给客户这些内容：

- 这是什么类型的项目
- 应该走哪种子路径策略
- 客户必须自己改哪些文件
- 客户提交前必须自己验证哪些功能
- 还有哪些风险没验证完

## 工作流程

按下面的顺序执行：

1. 识别项目形态
2. 选择子路径策略
3. 列出客户必须改的点
4. 给出最小代码示例
5. 给出提交前验收清单
6. 输出是否建议提交

## 1. 识别项目形态

先判断客户项目属于哪一类：

- Next.js
- Vite
- Vue CLI
- CRA
- Express/Fastify 托管前端静态产物
- FastAPI/Flask 模板渲染
- workspace/monorepo 前端子包

读取：

- [references/framework-matrix.md](references/framework-matrix.md)

输出最少需要包含：

- 前端框架类型
- 后端是否直接托管静态资源
- 是否是 workspace/monorepo
- 哪个目录是前端项目根
- 哪个目录是运行时静态根

如果这些信息都还没确认，不要直接给客户改法。

## 2. 选择子路径策略

只在两个策略里选一个：

- `preserve_prefix`
- `strip_prefix`

原则：

- 当前端框架原生支持配置子路径时，优先 `preserve_prefix`
- 当应用只理解根路径 `/`、由后端把静态资源挂在 `/` 下时，使用 `strip_prefix`

如果判断不准，不要猜，先根据 [references/framework-matrix.md](references/framework-matrix.md) 复核，再告诉客户为什么这样选。

## 3. 列出客户必须改的点

优先要求客户自己改这些内容，而不是等平台兜底：

- 框架配置里的 basePath / base / publicPath / homepage
- HTML 中的静态资源路径
- 前端 API 请求路径
- EventSource / SSE 路径
- 下载链接构造函数
- `window.location` 跳转
- 后端静态目录挂载方式

逐项检查：

- [references/checklist.md](references/checklist.md)

输出时优先写“必须改哪些文件、为什么要改”，再给示例。

需要给客户展示最小改法时，再读取：

- [references/examples.md](references/examples.md)

## 4. 指导客户本地验证

客户提交前，必须自己验证：

- 前端构建通过
- 后端构建通过
- 启动成功
- 入口页可访问
- API 请求成功
- SSE / EventSource 成功
- 下载链接成功
- 页面里不再残留根路径资源泄漏

如果客户只说“页面能打开”，不能视为完成。必须继续追问：

- 上传是否成功
- 轮询或 SSE 是否成功
- 下载是否成功

## 5. 输出交付前结论

最终输出给客户时，保持简洁，至少包含：

- 项目类型
- 选用的子路径策略
- 已完成的源码修改
- 客户已完成的本地验证
- 仍然存在的风险
- 是否建议提交到平台

如果仍有风险，必须明确指出，不要给模糊结论。

如果客户还没完成本地验证，不要说“可以提交”。

最终输出时，优先直接套用：

- [references/customer-delivery-template.md](references/customer-delivery-template.md)

## 约束

- 优先让客户改源码，不优先建议“交给平台自动修”
- 不要默认平台侧会自动修所有路径问题
- 不要把运行时注入 helper 当成客户源码长期方案的首选
- 对 TypeScript 项目，避免建议引入会破坏 `tsc` 的临时写法
- 对 workspace/monorepo 项目，必须同时检查前端子包、后端入口和静态根
- 面向客户输出时，少讲平台内部细节，多讲“为什么这里不改会出什么问题”
