# QA Agent MVP

一个面向声网 SDK 问题的最小可运行多租户问答 agent 服务。第一版只做客户隔离、会话管理、历史消息保存和 LLM 问答调用。

当前 agent 的定位是帮助开发者解答声网 SDK 和声网文档相关问题，覆盖 RTC、RTM、IM、互动直播、云端录制、旁路推流、媒体处理、SDK 集成、API 使用、错误排查和最佳实践。回答时优先围绕声网官方文档站：

```text
https://doc.shengwang.cn/
```

完整使用手册见：[docs/usage-manual.md](docs/usage-manual.md)。

## Setup

```bash
npm install
cp .env.example .env
```

编辑 `.env`，填入可用的 `LLM_API_KEY`。

如果只是先跑通本地流程，可以设置：

```env
LLM_MOCK=true
```

mock 模式不会调用真实模型，也不需要 `LLM_API_KEY`。

## Run

```bash
npm run dev
```

默认服务地址：

```text
http://localhost:3001
```

## API

创建会话：

```bash
curl -X POST http://localhost:3001/v1/conversations \
  -H 'content-type: application/json' \
  -H 'x-tenant-id: customer_a' \
  -d '{"title":"测试会话"}'
```

发送消息：

```bash
curl -X POST http://localhost:3001/v1/conversations/<conversation_id>/messages \
  -H 'content-type: application/json' \
  -H 'x-tenant-id: customer_a' \
  -d '{"content":"Web 端集成声网 RTC SDK 时，如何初始化客户端并加入频道？"}'
```

查看会话：

```bash
curl http://localhost:3001/v1/conversations \
  -H 'x-tenant-id: customer_a'
```

查看消息历史：

```bash
curl http://localhost:3001/v1/conversations/<conversation_id>/messages \
  -H 'x-tenant-id: customer_a'
```
