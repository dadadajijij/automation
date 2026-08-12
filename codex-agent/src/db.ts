import { DatabaseSync } from "node:sqlite";
import { dirname, resolve } from "node:path";
import { mkdirSync } from "node:fs";
import { config } from "./config.js";

export type Conversation = {
  id: string;
  tenant_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
};

export type Message = {
  id: string;
  tenant_id: string;
  conversation_id: string;
  role: "system" | "user" | "assistant";
  content: string;
  model: string | null;
  latency_ms: number | null;
  created_at: string;
};

const dbPath = resolve(config.DATABASE_URL);
mkdirSync(dirname(dbPath), { recursive: true });

export const db = new DatabaseSync(dbPath);
db.exec("PRAGMA journal_mode = WAL");
db.exec("PRAGMA foreign_keys = ON");

db.exec(`
  CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant')),
    content TEXT NOT NULL,
    model TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
  );

  CREATE INDEX IF NOT EXISTS idx_conversations_tenant
    ON conversations(tenant_id, updated_at DESC);

  CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(tenant_id, conversation_id, created_at);
`);

export function nowIso(): string {
  return new Date().toISOString();
}

export function createConversation(tenantId: string, title: string | null): Conversation {
  const conversation: Conversation = {
    id: crypto.randomUUID(),
    tenant_id: tenantId,
    title,
    created_at: nowIso(),
    updated_at: nowIso()
  };

  db.prepare(`
    INSERT INTO conversations (id, tenant_id, title, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?)
  `).run(conversation.id, conversation.tenant_id, conversation.title, conversation.created_at, conversation.updated_at);

  return conversation;
}

export function listConversations(tenantId: string): Conversation[] {
  return db.prepare(`
    SELECT id, tenant_id, title, created_at, updated_at
    FROM conversations
    WHERE tenant_id = ?
    ORDER BY updated_at DESC
  `).all(tenantId) as Conversation[];
}

export function getConversation(tenantId: string, conversationId: string): Conversation | undefined {
  return db.prepare(`
    SELECT id, tenant_id, title, created_at, updated_at
    FROM conversations
    WHERE tenant_id = ? AND id = ?
  `).get(tenantId, conversationId) as Conversation | undefined;
}

export function addMessage(input: {
  tenantId: string;
  conversationId: string;
  role: Message["role"];
  content: string;
  model?: string | null;
  latencyMs?: number | null;
}): Message {
  const message: Message = {
    id: crypto.randomUUID(),
    tenant_id: input.tenantId,
    conversation_id: input.conversationId,
    role: input.role,
    content: input.content,
    model: input.model ?? null,
    latency_ms: input.latencyMs ?? null,
    created_at: nowIso()
  };

  db.exec("BEGIN");
  try {
    db.prepare(`
      INSERT INTO messages (id, tenant_id, conversation_id, role, content, model, latency_ms, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      message.id,
      message.tenant_id,
      message.conversation_id,
      message.role,
      message.content,
      message.model,
      message.latency_ms,
      message.created_at
    );

    db.prepare(`
      UPDATE conversations
      SET updated_at = ?
      WHERE tenant_id = ? AND id = ?
    `).run(message.created_at, input.tenantId, input.conversationId);

    db.exec("COMMIT");
  } catch (error) {
    db.exec("ROLLBACK");
    throw error;
  }

  return message;
}

export function listMessages(tenantId: string, conversationId: string): Message[] {
  return db.prepare(`
    SELECT id, tenant_id, conversation_id, role, content, model, latency_ms, created_at
    FROM messages
    WHERE tenant_id = ? AND conversation_id = ?
    ORDER BY created_at ASC
  `).all(tenantId, conversationId) as Message[];
}

export function recentMessages(tenantId: string, conversationId: string, limit: number): Message[] {
  return db.prepare(`
    SELECT id, tenant_id, conversation_id, role, content, model, latency_ms, created_at
    FROM messages
    WHERE tenant_id = ? AND conversation_id = ?
    ORDER BY created_at DESC
    LIMIT ?
  `).all(tenantId, conversationId, limit).reverse() as Message[];
}
