/**
 * Client for Gura Studio Assistant conversation API.
 */
import { ApiError } from './client'

export interface AssistantToolCall {
  id: string
  name: string
  arguments: Record<string, unknown>
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
  tool_calls?: AssistantToolCall[] | null
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

export async function getOrCreateAssistantConversation(
  projectId: string,
  chapterId?: string,
  title?: string,
): Promise<AssistantConversation> {
  const response = await fetch(`/api/v1/projects/${projectId}/assistant/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chapter_id: chapterId || null,
      title: title || null,
    }),
  })
  if (!response.ok) {
    throw new Error(`Failed to initialize assistant conversation: ${response.statusText}`)
  }
  return response.json()
}

export async function getAssistantConversation(
  projectId: string,
  conversationId: string,
): Promise<AssistantConversation> {
  const response = await fetch(`/api/v1/projects/${projectId}/assistant/conversations/${conversationId}`, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`Failed to fetch assistant conversation: ${response.statusText}`)
  }
  return response.json()
}

export async function sendAssistantMessage(
  projectId: string,
  conversationId: string,
  content: string,
  chapterId?: string,
  currentView?: string,
  clientMessageId?: string,
): Promise<AssistantConversation> {
  const response = await fetch(`/api/v1/projects/${projectId}/assistant/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      content,
      chapter_id: chapterId || null,
      current_view: currentView || null,
      client_message_id: clientMessageId || null,
    }),
  })
  if (!response.ok) {
    throw new Error(`Failed to send message to assistant: ${response.statusText}`)
  }
  return response.json()
}

async function collectionRequest(collectionId: string, path = '', payload: object = {}): Promise<AssistantConversation> {
  const response = await fetch(`/api/v1/setting-collections/${encodeURIComponent(collectionId)}/assistant/conversations${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  if (!response.ok) throw new ApiError(response.status, 'assistant_request_failed', response.status === 409 ? '条目版本已变化，请核对当前内容后重试。'
    : response.status === 503 ? '模型暂不可用，请检查模型配置或稍后重试；消息已保留。'
      : '无法连接设定集助手，请稍后重试；消息已保留。')
  return response.json()
}

export function getOrCreateSettingAssistantConversation(collectionId: string) {
  return collectionRequest(collectionId)
}

export function sendSettingAssistantMessage(collectionId: string, conversationId: string, content: string, clientMessageId: string, documentId?: string, versionId?: string) {
  return collectionRequest(collectionId, `/${encodeURIComponent(conversationId)}/messages`, {
    content, client_message_id: clientMessageId, document_id: documentId ?? null, document_version_id: versionId ?? null,
  })
}
