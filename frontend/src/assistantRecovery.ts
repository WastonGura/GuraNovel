import type { AssistantMessage, AssistantPageContext } from './api/studioAssistantClient'

export const activeAssistantConversationKey = 'guranovel_assistant_active_conversation'

export interface PendingAssistantMessage {
  id: string
  text: string
  conversationId: string
  context: AssistantPageContext
}

function canonicalContextId(value: string): string {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value) ? value.toLowerCase() : value
}

/** UUID fields must match the canonical UUID strings returned in durable server acknowledgements. */
export function canonicalAssistantContext(context: AssistantPageContext): AssistantPageContext {
  if (context.kind === 'project') return {
    ...context, project_id: canonicalContextId(context.project_id),
    ...(typeof context.chapter_id === 'string' ? { chapter_id: canonicalContextId(context.chapter_id) } : {}),
  }
  if (context.kind === 'collection') return {
    ...context, collection_id: canonicalContextId(context.collection_id),
    ...(typeof context.document_id === 'string' ? { document_id: canonicalContextId(context.document_id) } : {}),
    ...(typeof context.document_version_id === 'string' ? { document_version_id: canonicalContextId(context.document_version_id) } : {}),
  }
  return { ...context }
}

export function readActiveAssistantConversation(): string | null {
  try {
    const value = sessionStorage.getItem(activeAssistantConversationKey)
    return value && value.length <= 128 ? value : null
  } catch { return null }
}

export function persistActiveAssistantConversation(id: string) {
  try {
    sessionStorage.setItem(activeAssistantConversationKey, id)
    if (sessionStorage.getItem(activeAssistantConversationKey) === id) return
  } catch { /* Selection must remain recoverable before it replaces the current chat. */ }
  throw new Error('浏览器无法保留当前对话，请检查本地存储后再试。')
}

export function readAssistantDraft(key: string, pending: PendingAssistantMessage | null): { text: string; recovered: boolean } {
  try {
    const value = sessionStorage.getItem(key)
    const recovered = Boolean(pending && (value === null || (value === '' || value === pending.text) && sessionStorage.getItem(`${key}:origin`) === pending.id))
    return { text: recovered ? pending!.text : value || '', recovered }
  } catch { return { text: pending?.text || '', recovered: Boolean(pending) } }
}

export function assignUnassignedAssistantDraft(sourceKey: string, conversationId: string, text: string): boolean {
  const targetKey = `guranovel_assistant_draft:conversation:${conversationId}`
  try {
    const existing = sessionStorage.getItem(targetKey) || ''
    if (!text) {
      if (existing) return false
      for (const legacyKey of ['guranovel_assistant_draft:home', 'guranovel_assistant_draft']) {
        const legacy = sessionStorage.getItem(legacyKey)
        if (!legacy) continue
        sessionStorage.setItem(targetKey, legacy)
        if (sessionStorage.getItem(targetKey) !== legacy) throw new Error('draft not persisted')
        sessionStorage.removeItem(legacyKey)
        return false
      }
      return false
    }
    const merged = Boolean(existing && existing !== text)
    const draft = merged ? `${existing}\n\n${text}` : text
    sessionStorage.setItem(targetKey, draft)
    if (sessionStorage.getItem(targetKey) !== draft) throw new Error('draft not persisted')
    if (sessionStorage.getItem(sourceKey) === text) sessionStorage.removeItem(sourceKey)
    return merged
  } catch { throw new Error('浏览器无法保留消息草稿，请检查本地存储后重新载入会话。') }
}

export function readAssistantCreationRequest(key: string): string | null {
  try {
    const value = sessionStorage.getItem(key)
    return value && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value) ? value : null
  } catch { return null }
}

export function persistAssistantCreationRequest(key: string, id: string) {
  try {
    sessionStorage.setItem(key, id)
    if (sessionStorage.getItem(key) === id) return
  } catch { /* A new conversation request also needs a durable retry identity. */ }
  throw new Error('浏览器无法保留新建对话的重试信息，请检查本地存储后再试。')
}

function validContext(value: unknown): value is AssistantPageContext {
  if (!value || typeof value !== 'object') return false
  const context = value as Record<string, unknown>
  if (context.kind === 'home') return Object.keys(context).every(key => key === 'kind' || key === 'current_view')
    && (context.current_view === undefined || context.current_view === 'home')
  const fields = context.kind === 'project' ? ['kind', 'project_id', 'chapter_id', 'current_view']
    : context.kind === 'collection' ? ['kind', 'collection_id', 'document_id', 'document_version_id'] : null
  const identity = context.kind === 'project' ? context.project_id : context.collection_id
  return Boolean(fields && typeof identity === 'string' && identity
    && Object.keys(context).every(key => fields.includes(key) && (context[key] === null || typeof context[key] === 'string')))
}

export function readPendingAssistantMessage(key: string, conversationId: string | null): PendingAssistantMessage | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (value && typeof value.id === 'string' && value.id.length > 0 && value.id.length <= 128
      && typeof value.text === 'string' && value.text.trim().length > 0 && value.text.length <= 16384
      && value.conversationId === conversationId && validContext(value.context)) return { ...value, context: canonicalAssistantContext(value.context) }
  } catch { /* Corrupt browser data is never a request identity. */ }
  return null
}

export function persistPendingAssistantMessage(key: string, value: PendingAssistantMessage) {
  const record = JSON.stringify(value)
  try {
    sessionStorage.setItem(key, record)
    if (sessionStorage.getItem(key) === record) return
  } catch { /* A send requires a durable identity before starting transport. */ }
  throw new Error('浏览器无法保留消息重试信息，请检查本地存储后再试。')
}

export function matchesAssistantAcknowledgement(message: AssistantMessage, pending: PendingAssistantMessage): boolean {
  if (message.role !== 'user' || message.content !== pending.text) return false
  return Boolean(message.tool_calls?.some(metadata => {
    if (!('client_message_id' in metadata) || metadata.client_message_id !== pending.id || !metadata.context || metadata.exchange_complete !== true) return false
    const original = pending.context as unknown as Record<string, unknown>
    return Object.keys(metadata.context).length === Object.keys(original).length
      && Object.keys(original).every(key => Object.hasOwn(metadata.context!, key) && metadata.context![key] === original[key])
  }))
}
