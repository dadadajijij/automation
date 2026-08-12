import OpenAI from "openai";
import { config } from "./config.js";
import type { Message } from "./db.js";

const client = new OpenAI({
  apiKey: config.LLM_API_KEY ?? "mock-key",
  baseURL: config.LLM_BASE_URL
});

const systemPrompt = [
  "你是一个声网 SDK 智能问答助手，主要帮助开发者解答声网 SDK 和声网文档相关问题。",
  "你的回答范围包括但不限于 RTC、RTM、IM、互动直播、云端录制、旁路推流、媒体处理、SDK 集成、API 使用、错误排查和最佳实践。",
  "优先围绕声网官方文档站 https://doc.shengwang.cn/ 的内容和术语来组织回答。",
  "如果用户问题缺少平台、产品、SDK 版本、报错信息或代码上下文，应先说明需要补充哪些关键信息。",
  "如果你不能确定答案，必须明确说明不确定，并建议用户核对声网官方文档或提供更多上下文；不要编造 API、参数、版本行为、错误码或计费/合规结论。",
  "你必须只使用当前客户当前会话中的上下文，不要混用其他客户的历史问题或数据。",
  "回答应直接、工程化，能给步骤、排查路径或示例时优先给可执行建议。"
].join("\n");

export async function answerQuestion(messages: Message[]): Promise<string> {
  if (config.LLM_MOCK) {
    const lastUserMessage = [...messages].reverse().find((message) => message.role === "user");
    return [
      "这是声网 SDK 问答助手的本地 mock 回复，用于跑通多租户会话流程。",
      lastUserMessage ? `你刚才的问题是：${lastUserMessage.content}` : "当前没有找到用户问题。",
      "设置 LLM_MOCK=false 并配置 LLM_API_KEY 后，会调用真实模型。"
    ].join("\n");
  }

  const input = [
    {
      role: "system" as const,
      content: systemPrompt
    },
    ...messages.map((message) => ({
      role: message.role as "user" | "assistant" | "system",
      content: message.content
    }))
  ];

  const response = await client.responses.create({
    model: config.LLM_MODEL,
    reasoning: {
      effort: config.LLM_REASONING_EFFORT
    },
    input
  });

  const text = response.output_text?.trim();
  if (text) return text;

  throw new Error("LLM response did not include output_text");
}
