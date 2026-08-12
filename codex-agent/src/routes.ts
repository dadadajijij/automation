import type { FastifyInstance, FastifyRequest } from "fastify";
import { z } from "zod";
import { config } from "./config.js";
import {
  addMessage,
  createConversation,
  getConversation,
  listConversations,
  listMessages,
  recentMessages
} from "./db.js";
import { answerQuestion } from "./llm.js";

function tenantIdFromRequest(request: FastifyRequest): string {
  const value = request.headers["x-tenant-id"];
  const tenantId = Array.isArray(value) ? value[0] : value;

  if (!tenantId || tenantId.trim().length === 0) {
    throw Object.assign(new Error("Missing x-tenant-id header"), { statusCode: 400 });
  }

  return tenantId.trim();
}

const CreateConversationSchema = z.object({
  title: z.string().trim().min(1).max(120).optional()
});

const SendMessageSchema = z.object({
  content: z.string().trim().min(1).max(20000)
});

export async function registerRoutes(app: FastifyInstance): Promise<void> {
  app.get("/health", async () => ({
    ok: true,
    model: config.LLM_MODEL
  }));

  app.post("/v1/conversations", async (request, reply) => {
    const tenantId = tenantIdFromRequest(request);
    const body = CreateConversationSchema.parse(request.body ?? {});
    const conversation = createConversation(tenantId, body.title ?? null);

    return reply.code(201).send({ conversation });
  });

  app.get("/v1/conversations", async (request) => {
    const tenantId = tenantIdFromRequest(request);
    return { conversations: listConversations(tenantId) };
  });

  app.get<{ Params: { id: string } }>("/v1/conversations/:id/messages", async (request, reply) => {
    const tenantId = tenantIdFromRequest(request);
    const conversation = getConversation(tenantId, request.params.id);

    if (!conversation) {
      return reply.code(404).send({ error: "Conversation not found" });
    }

    return {
      conversation,
      messages: listMessages(tenantId, request.params.id)
    };
  });

  app.post<{ Params: { id: string } }>("/v1/conversations/:id/messages", async (request, reply) => {
    const tenantId = tenantIdFromRequest(request);
    const conversation = getConversation(tenantId, request.params.id);

    if (!conversation) {
      return reply.code(404).send({ error: "Conversation not found" });
    }

    const body = SendMessageSchema.parse(request.body);
    addMessage({
      tenantId,
      conversationId: conversation.id,
      role: "user",
      content: body.content
    });

    const history = recentMessages(tenantId, conversation.id, 20);
    const startedAt = Date.now();
    const answer = await answerQuestion(history);
    const latencyMs = Date.now() - startedAt;

    const assistantMessage = addMessage({
      tenantId,
      conversationId: conversation.id,
      role: "assistant",
      content: answer,
      model: config.LLM_MODEL,
      latencyMs
    });

    return reply.send({
      conversation_id: conversation.id,
      message: assistantMessage
    });
  });
}
