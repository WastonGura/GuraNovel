/** Authoring discussions belong to the setting Agent, independently of global Gura. */
import { getApiBaseUrl } from '../config'
import { ApiError, decodeDocument, type Document, type SettingChangeProposal } from './client'

export interface SettingSelection { start: number; end: number; text: string }

export interface SettingCollaborationMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  client_message_id: string | null
  attempt_id: string | null
  proposal: SettingChangeProposal | null
  proposal_status: 'pending' | 'accepted' | 'rejected' | null
  base_content: string | null
  base_title: string | null
  accepted_version_id: string | null
  created_at: string
}

export interface SettingCollaboration {
  id: string
  title: string
  is_default: boolean
  client_request_id: string | null
  setting_collection_id: string
  document_id: string | null
  agent_role: 'worldbuilding_agent' | 'lore_agent'
  messages: SettingCollaborationMessage[]
  attempts: SettingCollaborationAttempt[]
  created_at: string
  updated_at: string
}

export interface SettingConversationSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
  preview: string
  is_default: boolean
}
export interface SettingConversationPage { items: SettingConversationSummary[]; next_offset: number | null }

export interface SettingCollaborationSend {
  client_message_id: string
  content: string
  base_version_id: string
  draft_content: string
  selection?: SettingSelection | null
}

export interface SettingCollectionCollaborationSend {
  client_message_id: string
  content: string
}

export interface SettingCollaborationAttempt {
  id: string
  client_message_id: string
  content: string
  request: SettingCollectionCollaborationSend | SettingCollaborationSend
  status: 'prepared' | 'dispatched' | 'result_saved' | 'completed' | 'finished'
  error_code: string | null
  provenance: Record<string, unknown> | null
  has_result: boolean
  user_message_id: string | null
  assistant_message_id: string | null
  created_at: string
  updated_at: string
}

export interface SettingCollaborationAcceptance {
  conversation: SettingCollaboration
  document: Document
  content: string
}

function endpoint(collectionId: string, documentId: string, suffix = '') {
  return `${getApiBaseUrl().replace(/\/$/, '')}/setting-collections/${encodeURIComponent(collectionId)}/documents/${encodeURIComponent(documentId)}/collaboration${suffix}`
}
function collectionEndpoint(collectionId: string, suffix = '') {
  return `${getApiBaseUrl().replace(/\/$/, '')}/setting-collections/${encodeURIComponent(collectionId)}/collaboration${suffix}`
}

function invalid(): never { throw new ApiError(0, 'invalid_response', '设定协作的响应不完整，请重试；正文未被修改。') }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid()
  return value as Record<string, unknown>
}
function string(value: unknown): string { return typeof value === 'string' ? value : invalid() }
function identity(value: unknown): string { const result = string(value); return result.length > 0 ? result : invalid() }
function nullableString(value: unknown): string | null { return value === null ? null : string(value) }
function date(value: unknown): string { const result = string(value); return Number.isFinite(Date.parse(result)) ? result : invalid() }
function status(value: unknown): 'pending' | 'accepted' | 'rejected' | null {
  return value === null || value === 'pending' || value === 'accepted' || value === 'rejected' ? value : invalid()
}

function attempt(value: unknown, documentId: string | null): SettingCollaborationAttempt {
  const data = record(value), payload = record(data.request)
  const id = identity(data.client_message_id), content = identity(data.content)
  if (id.length > 128 || content.length > 16000 || !content.trim() || payload.client_message_id !== id || payload.content !== content
    || !['prepared', 'dispatched', 'result_saved', 'completed', 'finished'].includes(string(data.status)) || typeof data.has_result !== 'boolean') return invalid()
  let request: SettingCollectionCollaborationSend | SettingCollaborationSend = { client_message_id: id, content }
  if (documentId === null) {
    if (Object.keys(payload).some(key => key !== 'client_message_id' && key !== 'content')) return invalid()
  } else {
    if (Object.keys(payload).some(key => !['client_message_id', 'content', 'base_version_id', 'draft_content', 'selection'].includes(key))) return invalid()
    const draft = string(payload.draft_content)
    request = { ...request, base_version_id: identity(payload.base_version_id), draft_content: draft }
    if (draft.length > 65536) return invalid()
    if (payload.selection !== undefined) {
      if (payload.selection === null) request.selection = null
      else {
        const selection = record(payload.selection)
        if (Object.keys(selection).some(key => !['start', 'end', 'text'].includes(key)) || !Number.isInteger(selection.start) || !Number.isInteger(selection.end)
          || (selection.start as number) < 0 || (selection.end as number) <= (selection.start as number) || (selection.end as number) > draft.length
          || typeof selection.text !== 'string' || draft.slice(selection.start as number, selection.end as number) !== selection.text) return invalid()
        request.selection = { start: selection.start as number, end: selection.end as number, text: selection.text }
      }
    }
  }
  const user = nullableString(data.user_message_id), assistant = nullableString(data.assistant_message_id)
  if (data.status === 'completed' && (!user || !assistant || user === assistant || !data.has_result)) return invalid()
  return { id: identity(data.id), client_message_id: id, content, request, status: data.status as SettingCollaborationAttempt['status'],
    error_code: nullableString(data.error_code), provenance: data.provenance === null ? null : record(data.provenance), has_result: data.has_result,
    user_message_id: user, assistant_message_id: assistant, created_at: date(data.created_at), updated_at: date(data.updated_at) }
}

function proposal(value: unknown, collectionId: string, documentId: string | null): SettingChangeProposal | null {
  if (value === null) return null
  const data = record(value)
  const target = nullableString(data.target_document_id), base = nullableString(data.base_version_id)
  if (data.setting_collection_id !== collectionId || documentId !== null && target !== documentId || status(data.status) === null
    || (target === null ? base !== null : !target || !base)) return invalid()
  const source = record(data.source_task)
  if (source.agent_role !== 'worldbuilding_agent' && source.agent_role !== 'lore_agent') return invalid()
  return {
    id: identity(data.id), setting_collection_id: collectionId, target_document_id: target,
    base_version_id: base, title: identity(data.title), category: identity(data.category),
    proposed_content: identity(data.proposed_content), reason: identity(data.reason), status: status(data.status) ?? undefined,
    source_task: { agent_role: source.agent_role, workflow_run_id: nullableString(source.workflow_run_id),
      chapter_id: nullableString(source.chapter_id), novel_title: nullableString(source.novel_title) }, created_at: date(data.created_at),
  }
}

function conversation(value: unknown, collectionId: string, documentId: string | null, explicit = false): SettingCollaboration {
  const data = record(value)
  if (data.setting_collection_id !== collectionId || data.document_id !== documentId || !Array.isArray(data.messages)
    || (data.agent_role !== 'worldbuilding_agent' && data.agent_role !== 'lore_agent')) return invalid()
  if (documentId === null && data.agent_role !== 'lore_agent') return invalid()
  const messages = data.messages.map((value): SettingCollaborationMessage => {
    const item = record(value)
    if (item.role !== 'user' && item.role !== 'assistant') return invalid()
    const change = proposal(item.proposal, collectionId, documentId)
    const state = status(item.proposal_status)
    const base = nullableString(item.base_content), title = nullableString(item.base_title), accepted = nullableString(item.accepted_version_id)
    if (change ? item.role !== 'assistant' || state !== change.status || base === null || title === null
      || change.source_task?.agent_role !== data.agent_role || (change.target_document_id === null && (base !== '' || title !== ''))
      || (state === 'accepted' ? accepted === null : accepted !== null)
      : state !== null || accepted !== null) return invalid()
    return { id: identity(item.id), role: item.role, content: identity(item.content), client_message_id: nullableString(item.client_message_id),
      attempt_id: item.attempt_id === undefined ? null : nullableString(item.attempt_id),
      proposal: change, proposal_status: state, base_content: base, base_title: title, accepted_version_id: accepted, created_at: date(item.created_at) }
  })
  if (new Set(messages.map(message => message.id)).size !== messages.length) return invalid()
  const attempts = data.attempts === undefined ? [] : Array.isArray(data.attempts) ? data.attempts.map(value => attempt(value, documentId)) : invalid()
  if (new Set(attempts.map(item => item.id)).size !== attempts.length || new Set(attempts.map(item => item.client_message_id)).size !== attempts.length) return invalid()
  const title = data.title === undefined && !explicit ? '新对话' : identity(data.title)
  const isDefault = data.is_default === undefined && !explicit ? true : data.is_default
  const requestId = data.client_request_id === undefined && !explicit ? null : nullableString(data.client_request_id)
  if (typeof isDefault !== 'boolean' || title.length > 1000 || requestId !== null && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(requestId)) return invalid()
  return { id: identity(data.id), title, is_default: isDefault, client_request_id: requestId, setting_collection_id: collectionId, document_id: documentId, agent_role: data.agent_role,
    messages, attempts, created_at: date(data.created_at), updated_at: date(data.updated_at) }
}

function selectedConversation(value: unknown, collectionId: string, conversationId: string): SettingCollaboration {
  const result = conversation(value, collectionId, null, true)
  return result.id === conversationId ? result : invalid()
}

function selectedEndpoint(collectionId: string, conversationId: string, suffix = ''): string {
  return collectionEndpoint(collectionId, `/conversations/${encodeURIComponent(conversationId)}${suffix}`)
}

async function request<T>(url: string, decode: (value: unknown) => T, body?: object, method: 'POST' | 'GET' = 'POST'): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, {
      method, ...(method === 'GET' ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) }),
    })
  } catch {
    throw new ApiError(0, 'setting_collaboration_unavailable', '连接失败，讨论和草稿已保留；请检查结果。')
  }
  if (!response.ok) {
    let code = 'setting_collaboration_failed'
    try {
      const value = await response.json(), envelope = value?.error ?? value?.detail
      if (envelope && typeof envelope.code === 'string') code = envelope.code
    } catch { /* The status remains useful when the server cannot supply an error envelope. */ }
    throw new ApiError(response.status, code, code === 'setting_agent_attempt_pending'
      ? '上一条请求尚未结束，请先检查结果或结束等待；本次输入已保留。'
      : code === 'setting_agent_attempt_finished' ? '这条请求已结束等待，输入已保留；再次发送会创建新请求。'
      : code === 'setting_agent_attempt_unconfirmed' ? '原请求的完成情况尚未确认，请检查结果；不会重复调用设定 Agent。'
      : response.status === 409
      ? url.includes('/proposals/') ? '正文或标题已变化，请重新比较后再采纳。' : '请求状态或原始内容不一致，输入已保留；请检查结果。'
      : response.status === 429 ? '设定 Agent 请求受限，输入已保留；请检查请求状态。'
      : response.status === 503 ? '设定 Agent 暂不可用，输入已保留；请检查请求状态。'
      : response.status === 504 ? '等待设定 Agent 超时，结果尚未确认；请检查结果。'
        : '无法完成设定协作，内容已保留；请检查请求状态。')
  }
  let value: unknown
  try { value = await response.json() } catch { return invalid() }
  return decode(value)
}

export function getOrCreateSettingCollaboration(collectionId: string, documentId: string): Promise<SettingCollaboration> {
  return request(endpoint(collectionId, documentId), value => conversation(value, collectionId, documentId))
}

export function getSettingCollaboration(collectionId: string, documentId: string): Promise<SettingCollaboration> {
  return request(endpoint(collectionId, documentId), value => conversation(value, collectionId, documentId), undefined, 'GET')
}

export function getSettingCollaborationAttempt(collectionId: string, documentId: string, attemptId: string): Promise<SettingCollaborationAttempt> {
  return request(endpoint(collectionId, documentId, `/attempts/${encodeURIComponent(attemptId)}`), value => {
    const result = attempt(value, documentId)
    return result.id === attemptId ? result : invalid()
  }, undefined, 'GET')
}

export function resumeSettingCollaborationAttempt(collectionId: string, documentId: string, attemptId: string): Promise<SettingCollaboration> {
  return request(endpoint(collectionId, documentId, `/attempts/${encodeURIComponent(attemptId)}/resume`), value => conversation(value, collectionId, documentId))
}

export function finishSettingCollaborationAttempt(collectionId: string, documentId: string, payload: SettingCollaborationSend): Promise<SettingCollaboration> {
  return request(endpoint(collectionId, documentId, '/attempts/finish'), value => conversation(value, collectionId, documentId), payload)
}

export function sendSettingCollaborationMessage(collectionId: string, documentId: string, payload: SettingCollaborationSend): Promise<SettingCollaboration> {
  return request(endpoint(collectionId, documentId, '/messages'), value => conversation(value, collectionId, documentId), payload)
}

export function acceptSettingCollaborationProposal(collectionId: string, documentId: string, proposalId: string, expectedVersionId: string, expectedDraftContent: string, expectedTitle: string): Promise<SettingCollaborationAcceptance> {
  return request(endpoint(collectionId, documentId, `/proposals/${encodeURIComponent(proposalId)}/accept`), value => {
    const data = record(value), document = decodeDocument(data.document)
    if (document.id !== documentId || document.setting_collection_id !== collectionId || !document.current_version_id) return invalid()
    return { conversation: conversation(data.conversation, collectionId, documentId), document, content: string(data.content) }
  }, {
    expected_current_version_id: expectedVersionId, expected_draft_content: expectedDraftContent, expected_title: expectedTitle,
  })
}

export function discardSettingCollaborationProposal(collectionId: string, documentId: string, proposalId: string): Promise<SettingCollaboration> {
  return request(endpoint(collectionId, documentId, `/proposals/${encodeURIComponent(proposalId)}/discard`), value => conversation(value, collectionId, documentId))
}

export function getOrCreateSettingCollectionCollaboration(collectionId: string): Promise<SettingCollaboration> {
  return request(collectionEndpoint(collectionId), value => conversation(value, collectionId, null))
}

export function getSettingCollectionCollaboration(collectionId: string): Promise<SettingCollaboration> {
  return request(collectionEndpoint(collectionId), value => conversation(value, collectionId, null), undefined, 'GET')
}

export function getSettingCollectionCollaborationAttempt(collectionId: string, attemptId: string): Promise<SettingCollaborationAttempt> {
  return request(collectionEndpoint(collectionId, `/attempts/${encodeURIComponent(attemptId)}`), value => {
    const result = attempt(value, null)
    return result.id === attemptId ? result : invalid()
  }, undefined, 'GET')
}

export function resumeSettingCollectionCollaborationAttempt(collectionId: string, attemptId: string): Promise<SettingCollaboration> {
  return request(collectionEndpoint(collectionId, `/attempts/${encodeURIComponent(attemptId)}/resume`), value => conversation(value, collectionId, null))
}

export function finishSettingCollectionCollaborationAttempt(collectionId: string, payload: SettingCollectionCollaborationSend): Promise<SettingCollaboration> {
  return request(collectionEndpoint(collectionId, '/attempts/finish'), value => conversation(value, collectionId, null), payload)
}

export function sendSettingCollectionCollaborationMessage(collectionId: string, payload: SettingCollectionCollaborationSend): Promise<SettingCollaboration> {
  return request(collectionEndpoint(collectionId, '/messages'), value => conversation(value, collectionId, null), payload)
}

export function acceptSettingCollectionCollaborationProposal(collectionId: string, proposalId: string, expectedVersionId: string | null, expectedDraftContent: string, expectedTitle: string): Promise<SettingCollaborationAcceptance> {
  return request(collectionEndpoint(collectionId, `/proposals/${encodeURIComponent(proposalId)}/accept`), value => {
    const data = record(value), document = decodeDocument(data.document), result = conversation(data.conversation, collectionId, null)
    const message = result.messages.find(message => message.proposal?.id === proposalId)
    if (document.setting_collection_id !== collectionId || !document.current_version_id || !message?.proposal || message.proposal_status !== 'accepted'
      || message.accepted_version_id !== document.current_version_id || message.proposal.target_document_id && message.proposal.target_document_id !== document.id) return invalid()
    return { conversation: result, document, content: string(data.content) }
  }, { expected_current_version_id: expectedVersionId, expected_draft_content: expectedDraftContent, expected_title: expectedTitle })
}

export function discardSettingCollectionCollaborationProposal(collectionId: string, proposalId: string): Promise<SettingCollaboration> {
  return request(collectionEndpoint(collectionId, `/proposals/${encodeURIComponent(proposalId)}/discard`), value => conversation(value, collectionId, null))
}

export function listSettingCollectionConversations(collectionId: string, offset = 0): Promise<SettingConversationPage> {
  return request(collectionEndpoint(collectionId, `/conversations?limit=30&offset=${offset}`), value => {
    const data = record(value)
    if (!Array.isArray(data.items) || data.items.length > 30 || data.next_offset !== null && (!Number.isSafeInteger(data.next_offset) || (data.next_offset as number) <= offset)) return invalid()
    const items = data.items.map((value): SettingConversationSummary => {
      const item = record(value)
      if (typeof item.is_default !== 'boolean' || !Number.isSafeInteger(item.message_count) || (item.message_count as number) < 0) return invalid()
      return { id: identity(item.id), title: identity(item.title), created_at: date(item.created_at), updated_at: date(item.updated_at),
        message_count: item.message_count as number, preview: string(item.preview), is_default: item.is_default }
    })
    if (new Set(items.map(item => item.id)).size !== items.length) return invalid()
    return { items, next_offset: data.next_offset as number | null }
  }, undefined, 'GET')
}

export function createSettingCollectionConversation(collectionId: string, clientRequestId: string): Promise<SettingCollaboration> {
  return request(collectionEndpoint(collectionId, '/conversations'), value => {
    const result = conversation(value, collectionId, null, true)
    return !result.is_default && result.client_request_id === clientRequestId ? result : invalid()
  }, { client_request_id: clientRequestId })
}

export function getSettingCollectionConversation(collectionId: string, conversationId: string): Promise<SettingCollaboration> {
  return request(selectedEndpoint(collectionId, conversationId), value => selectedConversation(value, collectionId, conversationId), undefined, 'GET')
}

export function sendSettingCollectionConversationMessage(collectionId: string, conversationId: string, payload: SettingCollectionCollaborationSend): Promise<SettingCollaboration> {
  return request(selectedEndpoint(collectionId, conversationId, '/messages'), value => selectedConversation(value, collectionId, conversationId), payload)
}

export function getSettingCollectionConversationAttempt(collectionId: string, conversationId: string, attemptId: string): Promise<SettingCollaborationAttempt> {
  return request(selectedEndpoint(collectionId, conversationId, `/attempts/${encodeURIComponent(attemptId)}`), value => {
    const result = attempt(value, null)
    return result.id === attemptId ? result : invalid()
  }, undefined, 'GET')
}

export function resumeSettingCollectionConversationAttempt(collectionId: string, conversationId: string, attemptId: string): Promise<SettingCollaboration> {
  return request(selectedEndpoint(collectionId, conversationId, `/attempts/${encodeURIComponent(attemptId)}/resume`), value => selectedConversation(value, collectionId, conversationId))
}

export function finishSettingCollectionConversationAttempt(collectionId: string, conversationId: string, payload: SettingCollectionCollaborationSend): Promise<SettingCollaboration> {
  return request(selectedEndpoint(collectionId, conversationId, '/attempts/finish'), value => selectedConversation(value, collectionId, conversationId), payload)
}

export function acceptSettingCollectionConversationProposal(collectionId: string, conversationId: string, proposalId: string, expectedVersionId: string | null, expectedDraftContent: string, expectedTitle: string): Promise<SettingCollaborationAcceptance> {
  return request(selectedEndpoint(collectionId, conversationId, `/proposals/${encodeURIComponent(proposalId)}/accept`), value => {
    const data = record(value), document = decodeDocument(data.document), result = selectedConversation(data.conversation, collectionId, conversationId)
    const message = result.messages.find(message => message.proposal?.id === proposalId)
    if (document.setting_collection_id !== collectionId || !document.current_version_id || !message?.proposal || message.proposal_status !== 'accepted'
      || message.accepted_version_id !== document.current_version_id || message.proposal.target_document_id && message.proposal.target_document_id !== document.id) return invalid()
    return { conversation: result, document, content: string(data.content) }
  }, { expected_current_version_id: expectedVersionId, expected_draft_content: expectedDraftContent, expected_title: expectedTitle })
}

export function discardSettingCollectionConversationProposal(collectionId: string, conversationId: string, proposalId: string): Promise<SettingCollaboration> {
  return request(selectedEndpoint(collectionId, conversationId, `/proposals/${encodeURIComponent(proposalId)}/discard`), value => selectedConversation(value, collectionId, conversationId))
}
