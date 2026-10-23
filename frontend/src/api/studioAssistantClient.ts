/**
 * Client for Gura Studio Assistant conversation API.
 */
import { ApiError } from './client'

export interface AssistantToolCall {
  id: string
  name: string
  arguments: Record<string, unknown>
}

/** Durable request identity metadata stored with user messages, never a tool badge. */
export interface AssistantRequestMetadata {
  client_message_id: string
  context?: Record<string, unknown>
  exchange_complete?: boolean
}

export interface AssistantToolResult {
  tool_call_id: string
  name: string
  result: Record<string, unknown>
}

export interface AssistantMessage {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  tool_calls?: (AssistantToolCall | AssistantRequestMetadata)[] | null
  tool_results?: AssistantToolResult[] | null
  created_at: string
}

export interface AssistantConversation {
  id: string
  project_id: string | null
  setting_collection_id?: string | null
  chapter_id?: string | null
  title?: string | null
  messages: AssistantMessage[]
  created_at: string
  updated_at: string
}

export type AssistantPageContext =
  | { kind: 'home'; current_view?: 'home' }
  | { kind: 'project'; project_id: string; chapter_id?: string | null; current_view?: string | null }
  | { kind: 'collection'; collection_id: string; document_id?: string | null; document_version_id?: string | null }

export interface AssistantConversationSummary {
  id: string
  title: string | null
  created_at: string
  updated_at: string
  message_count: number
  preview: string
}

export type LegacyAssistantScope = { project_id: string } | { collection_id: string }

async function assistantResponse<T = AssistantConversation>(response: Response, collection = false): Promise<T> {
  if (!response.ok) {
    let code = 'assistant_request_failed'
    try { const body = await response.json(); if (typeof body.error?.code === 'string') code = body.error.code } catch { /* Keep a translated transport error. */ }
    throw new ApiError(response.status, code, code === 'assistant_exchange_unconfirmed'
      ? '操作结果尚未确认，请先查看作品状态；原消息已保留，请勿重复执行。'
      : response.status === 409 ? collection ? '条目版本已变化，请核对当前内容后重试。' : '消息重试信息与原请求不一致，请重新载入会话后再试。'
        : response.status === 503 ? '助手暂不可用，请检查服务配置或稍后重试；消息已保留。'
          : '无法连接 Gura 助手服务，请稍后重试；消息已保留。')
  }
  return response.json()
}

export function getOrCreateGlobalAssistantConversation(newConversation = false, clientConversationId?: string): Promise<AssistantConversation> {
  return fetch('/api/v1/assistant/conversations', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ new_conversation: newConversation,
      ...(newConversation && clientConversationId ? { client_conversation_id: clientConversationId } : {}), }),
  }).then(response => assistantResponse(response))
}

export function getGlobalAssistantConversation(conversationId: string): Promise<AssistantConversation> {
  return fetch(`/api/v1/assistant/conversations/${encodeURIComponent(conversationId)}`, { headers: { Accept: 'application/json' } })
    .then(response => assistantResponse(response))
}

export function listGlobalAssistantConversations(): Promise<AssistantConversationSummary[]> {
  return fetch('/api/v1/assistant/conversations', { headers: { Accept: 'application/json' } })
    .then(response => assistantResponse<AssistantConversationSummary[]>(response))
}

export function sendGlobalAssistantMessage(conversationId: string, content: string, clientMessageId: string, context: AssistantPageContext): Promise<AssistantConversation> {
  return fetch(`/api/v1/assistant/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content, client_message_id: clientMessageId, context }),
  }).then(response => assistantResponse(response, context.kind === 'collection'))
}

function legacyAssistantUrl(scope: LegacyAssistantScope, conversationId?: string) {
  return `/api/v1/assistant/legacy-conversations${conversationId ? `/${encodeURIComponent(conversationId)}` : ''}?${new URLSearchParams(scope)}`
}

export function listLegacyAssistantConversations(scope: LegacyAssistantScope): Promise<AssistantConversationSummary[]> {
  return fetch(legacyAssistantUrl(scope), { headers: { Accept: 'application/json' } })
    .then(response => assistantResponse<AssistantConversationSummary[]>(response))
}

export function getLegacyAssistantConversation(scope: LegacyAssistantScope, conversationId: string): Promise<AssistantConversation> {
  return fetch(legacyAssistantUrl(scope, conversationId), { headers: { Accept: 'application/json' } })
    .then(response => assistantResponse(response))
}
