/**
 * Client for Gura Studio Assistant conversation API.
 */

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
  project_id: string
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
): Promise<AssistantConversation> {
  const response = await fetch(`/api/v1/projects/${projectId}/assistant/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      content,
      chapter_id: chapterId || null,
      current_view: currentView || null,
    }),
  })
  if (!response.ok) {
    throw new Error(`Failed to send message to assistant: ${response.statusText}`)
  }
  return response.json()
}
