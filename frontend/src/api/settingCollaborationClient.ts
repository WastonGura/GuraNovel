/** Authoring discussions belong to the setting Agent, independently of global Gura. */
import { getApiBaseUrl } from '../config'
import { ApiError, decodeDocument, type Document, type SettingChangeProposal } from './client'

export interface SettingSelection { start: number; end: number; text: string }

export interface SettingCollaborationMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  client_message_id: string | null
  proposal: SettingChangeProposal | null
  proposal_status: 'pending' | 'accepted' | 'rejected' | null
  base_content: string | null
  base_title: string | null
  accepted_version_id: string | null
  created_at: string
}

export interface SettingCollaboration {
  id: string
  setting_collection_id: string
  document_id: string | null
  agent_role: 'worldbuilding_agent' | 'lore_agent'
  messages: SettingCollaborationMessage[]
  created_at: string
  updated_at: string
}

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

function conversation(value: unknown, collectionId: string, documentId: string | null): SettingCollaboration {
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
      proposal: change, proposal_status: state, base_content: base, base_title: title, accepted_version_id: accepted, created_at: date(item.created_at) }
  })
  if (new Set(messages.map(message => message.id)).size !== messages.length) return invalid()
  return { id: identity(data.id), setting_collection_id: collectionId, document_id: documentId, agent_role: data.agent_role,
    messages, created_at: date(data.created_at), updated_at: date(data.updated_at) }
}

async function request<T>(url: string, decode: (value: unknown) => T, body?: object): Promise<T> {
  let response: Response
  try {
    response = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}),
    })
  } catch {
    throw new ApiError(0, 'setting_collaboration_unavailable', '连接失败，讨论和草稿已保留，可以重试。')
  }
  if (!response.ok) {
    throw new ApiError(response.status, 'setting_collaboration_failed', response.status === 409
      ? '正文或标题已变化，请重新比较后再采纳。'
      : response.status === 503 ? '设定 Agent 暂不可用，请稍后重试；正文未被修改。'
        : '无法完成设定协作，内容已保留，可以重试。')
  }
  let value: unknown
  try { value = await response.json() } catch { return invalid() }
  return decode(value)
}

export function getOrCreateSettingCollaboration(collectionId: string, documentId: string): Promise<SettingCollaboration> {
  return request(endpoint(collectionId, documentId), value => conversation(value, collectionId, documentId))
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
