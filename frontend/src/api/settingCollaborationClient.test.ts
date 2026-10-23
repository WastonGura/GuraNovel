import { afterEach, describe, expect, it, vi } from 'vitest'
import { acceptSettingCollaborationProposal, getOrCreateSettingCollaboration, sendSettingCollaborationMessage } from './settingCollaborationClient'
import * as collectionClient from './settingCollaborationClient'

const date = '2026-10-20T13:00:00Z'
const proposal = { id: 'proposal', setting_collection_id: 'collection', target_document_id: 'document', base_version_id: 'version',
  title: 'Tide', category: 'world', proposed_content: 'New text', reason: 'A cost', status: 'pending', created_at: date,
  source_task: { agent_role: 'worldbuilding_agent', workflow_run_id: null, chapter_id: null, novel_title: null } }
const data = { id: 'conversation', title: '原有对话', is_default: true, client_request_id: null, setting_collection_id: 'collection', document_id: 'document', agent_role: 'worldbuilding_agent',
  created_at: date, updated_at: date, messages: [{ id: 'message', role: 'assistant', content: 'Compare this', client_message_id: null,
    proposal, proposal_status: 'pending', base_content: 'Old text', base_title: 'Tide', accepted_version_id: null, created_at: date }] }

afterEach(() => vi.unstubAllGlobals())

function response(value: unknown) { vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(value), { status: 200 }))) }
const payload = { client_message_id: 'original-request', content: 'Continue the port.' }
const attempt = { id: 'attempt', ...payload, request: payload, status: 'dispatched', error_code: null, provenance: { provider: 'configured' }, has_result: false, user_message_id: null, assistant_message_id: null, created_at: date, updated_at: date }
const whole = { ...data, document_id: null, agent_role: 'lore_agent', messages: [], attempts: [attempt] }

describe('setting Agent client', () => {
  it('loads lightweight paginated conversation summaries without selecting a server-wide current session', async () => {
    const item = { id: 'conversation', title: '港湾历史', created_at: date, updated_at: date, message_count: 2, preview: '继续补充港湾。', is_default: true }
    response({ items: [item], next_offset: 30 })
    expect(await collectionClient.listSettingCollectionConversations('collection')).toEqual({ items: [item], next_offset: 30 })
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/v1/setting-collections/collection/collaboration/conversations?limit=30&offset=0', { method: 'GET' })
    response({ items: [], next_offset: null })
    await collectionClient.listSettingCollectionConversations('collection', 30)
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('limit=30&offset=30'), { method: 'GET' })
  })

  it.each([
    { items: null, next_offset: null }, { items: [], next_offset: -1 }, { items: [], next_offset: 0 },
    { items: [{ id: 'x', title: '标题', created_at: date, updated_at: date, message_count: -1, preview: '', is_default: true }], next_offset: null },
    { items: [{ id: 'x', title: '', created_at: date, updated_at: date, message_count: 0, preview: '', is_default: true }], next_offset: null },
  ])('rejects malformed history pages %#', async page => {
    response(page)
    await expect(collectionClient.listSettingCollectionConversations('collection')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('creates an idempotent fresh session only when its original request identity is acknowledged', async () => {
    const requestId = '128e428a-a6ec-4125-9269-1b805b97c749'
    const fresh = { ...whole, id: 'new-conversation', title: '新对话', is_default: false, client_request_id: requestId, attempts: [] }
    response(fresh)
    expect((await collectionClient.createSettingCollectionConversation('collection', requestId)).id).toBe('new-conversation')
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/collection/collaboration/conversations', expect.objectContaining({ method: 'POST', body: JSON.stringify({ client_request_id: requestId }) }))
    for (const invalid of [{ ...fresh, client_request_id: null }, { ...fresh, is_default: true }, { ...fresh, setting_collection_id: 'other' }, { ...fresh, title: undefined }]) {
      response(invalid)
      await expect(collectionClient.createSettingCollectionConversation('collection', requestId)).rejects.toMatchObject({ code: 'invalid_response' })
    }
  })

  it('uses the exact selected conversation for messages and all recovery operations', async () => {
    response(whole)
    await collectionClient.getSettingCollectionConversation('collection', 'conversation')
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/collection/collaboration/conversations/conversation', { method: 'GET' })
    response(whole)
    await collectionClient.sendSettingCollectionConversationMessage('collection', 'conversation', payload)
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/conversations/conversation/messages'), expect.objectContaining({ body: JSON.stringify(payload) }))
    response(attempt)
    await collectionClient.getSettingCollectionConversationAttempt('collection', 'conversation', 'attempt')
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/conversations/conversation/attempts/attempt'), { method: 'GET' })
    response(whole)
    await collectionClient.resumeSettingCollectionConversationAttempt('collection', 'conversation', 'attempt')
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/conversations/conversation/attempts/attempt/resume'), expect.objectContaining({ method: 'POST' }))
    response(whole)
    await collectionClient.finishSettingCollectionConversationAttempt('collection', 'conversation', payload)
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/conversations/conversation/attempts/finish'), expect.objectContaining({ body: JSON.stringify(payload) }))
    response({ ...whole, id: 'other' })
    await expect(collectionClient.getSettingCollectionConversation('collection', 'conversation')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('keeps proposal decisions bound to their selected conversation and accepted document', async () => {
    const change = { ...proposal, status: 'accepted', source_task: { ...proposal.source_task, agent_role: 'lore_agent' } }
    const result = { ...whole, messages: [{ ...data.messages[0], proposal: change, proposal_status: 'accepted', accepted_version_id: 'accepted-version' }] }
    const document = { id: 'document', project_id: null, setting_collection_id: 'collection', chapter_id: null, type: 'world_overview', title: 'Tide', path: 'world/tide.md', current_version_id: 'accepted-version', current_version: null, metadata: {}, created_at: date, updated_at: date, deleted_at: null }
    response({ conversation: result, document, content: 'New text' })
    expect((await collectionClient.acceptSettingCollectionConversationProposal('collection', 'conversation', 'proposal', 'version', 'Old text', 'Tide')).document.id).toBe('document')
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/conversations/conversation/proposals/proposal/accept'), expect.objectContaining({ method: 'POST' }))
    response({ conversation: { ...result, id: 'other-conversation' }, document, content: 'New text' })
    await expect(collectionClient.acceptSettingCollectionConversationProposal('collection', 'conversation', 'proposal', 'version', 'Old text', 'Tide')).rejects.toMatchObject({ code: 'invalid_response' })
    response(whole)
    await collectionClient.discardSettingCollectionConversationProposal('collection', 'conversation', 'proposal')
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/conversations/conversation/proposals/proposal/discard'), expect.objectContaining({ method: 'POST' }))
  })

  it('allows old default/document metadata to be absent while requiring it on explicit conversation reads', async () => {
    const legacy = { ...data, title: undefined, is_default: undefined, client_request_id: undefined }
    response(legacy)
    expect(await collectionClient.getSettingCollaboration('collection', 'document')).toMatchObject({ title: '新对话', is_default: true, client_request_id: null })
    response({ ...whole, title: undefined })
    await expect(collectionClient.getSettingCollectionConversation('collection', 'conversation')).rejects.toMatchObject({ code: 'invalid_response' })
    response({ ...whole, client_request_id: 'not-a-uuid' })
    await expect(collectionClient.getSettingCollectionConversation('collection', 'conversation')).rejects.toMatchObject({ code: 'invalid_response' })
    response({ ...whole, is_default: 'true' })
    await expect(collectionClient.getSettingCollectionConversation('collection', 'conversation')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('inspects collection state and attempts using GET without a request body', async () => {
    response(whole)
    expect((await collectionClient.getSettingCollectionCollaboration('collection')).attempts).toEqual([attempt])
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/v1/setting-collections/collection/collaboration', { method: 'GET' })
    response(attempt)
    expect(await collectionClient.getSettingCollectionCollaborationAttempt('collection', 'attempt')).toEqual(attempt)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/v1/setting-collections/collection/collaboration/attempts/attempt', { method: 'GET' })
    response({ ...attempt, id: 'other-attempt' })
    await expect(collectionClient.getSettingCollectionCollaborationAttempt('collection', 'attempt')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('uses explicit resume and finish endpoints while preserving the frozen collection request', async () => {
    response(whole)
    await collectionClient.resumeSettingCollectionCollaborationAttempt('collection', 'attempt')
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/collection/collaboration/attempts/attempt/resume', expect.objectContaining({ method: 'POST', body: '{}' }))
    response(whole)
    await collectionClient.finishSettingCollectionCollaborationAttempt('collection', payload)
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/collection/collaboration/attempts/finish', expect.objectContaining({ method: 'POST', body: JSON.stringify(payload) }))
  })

  it('retains the exact version and selection through legacy document recovery endpoints', async () => {
    const original = { ...payload, base_version_id: 'version', draft_content: 'Old text', selection: { start: 0, end: 3, text: 'Old' } }
    const documentAttempt = { ...attempt, request: original }, documentConversation = { ...data, attempts: [documentAttempt] }
    response(documentConversation)
    await collectionClient.getSettingCollaboration('collection', 'document')
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/documents/document/collaboration'), { method: 'GET' })
    response(documentAttempt)
    expect((await collectionClient.getSettingCollaborationAttempt('collection', 'document', 'attempt')).request).toEqual(original)
    response(documentConversation)
    await collectionClient.resumeSettingCollaborationAttempt('collection', 'document', 'attempt')
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/documents/document/collaboration/attempts/attempt/resume'), expect.objectContaining({ method: 'POST' }))
    response(documentConversation)
    await collectionClient.finishSettingCollaborationAttempt('collection', 'document', original)
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/attempts/finish'), expect.objectContaining({ body: JSON.stringify(original) }))
  })

  it.each([
    null, [{ ...attempt, status: 'unknown' }], [{ ...attempt, request: { ...payload, content: 'changed' } }],
    [{ ...attempt, request: { ...payload, document_id: 'unrelated' } }], [{ ...attempt, has_result: 'true' }],
    [{ ...attempt, status: 'completed' }], [{ ...attempt, provenance: [] }], [{ ...attempt, created_at: 'invalid' }],
    [attempt, attempt], [{ ...attempt, error_code: 503 }],
  ])('rejects malformed attempt metadata instead of weakening completion evidence %#', async attempts => {
    response({ ...whole, attempts })
    await expect(collectionClient.getSettingCollectionCollaboration('collection')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('rejects malformed message attempt identities and allows legacy records without them', async () => {
    response({ ...data, messages: [{ ...data.messages[0], attempt_id: 42 }] })
    await expect(getOrCreateSettingCollaboration('collection', 'document')).rejects.toMatchObject({ code: 'invalid_response' })
    response(data)
    const result = await collectionClient.getSettingCollaboration('collection', 'document')
    expect(result.attempts).toEqual([])
    expect(result.messages[0].attempt_id).toBeNull()
  })

  it.each([429, 503, 504, 422])('preserves the original provider HTTP/code for status %s', async status => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'provider_original_error', message: 'provider detail' } }), { status })))
    await expect(collectionClient.sendSettingCollectionCollaborationMessage('collection', payload)).rejects.toMatchObject({ status, code: 'provider_original_error' })
  })

  it('describes an unconfirmed attempt honestly instead of calling it a document conflict', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'setting_agent_attempt_unconfirmed' } }), { status: 409 })))
    await expect(collectionClient.sendSettingCollectionCollaborationMessage('collection', payload)).rejects.toMatchObject({ status: 409, code: 'setting_agent_attempt_unconfirmed', message: expect.stringContaining('完成情况尚未确认') })
  })

  it('discusses the whole collection without binding requests to a selected document', async () => {
    const whole = { ...data, document_id: null, agent_role: 'lore_agent', messages: [] }
    response(whole)
    expect(await collectionClient.getOrCreateSettingCollectionCollaboration('collection')).toEqual({ ...whole, attempts: [] })
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/collection/collaboration', expect.objectContaining({ method: 'POST' }))
    const payload = { client_message_id: 'collection-retry', content: 'Create a port, then improve its history.' }
    response(whole)
    await collectionClient.sendSettingCollectionCollaborationMessage('collection', payload)
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/collection/collaboration/messages', expect.objectContaining({ body: JSON.stringify(payload) }))
  })

  it('decodes a proposed new entry and the actual document returned by explicit adoption', async () => {
    const change = { ...proposal, target_document_id: null, base_version_id: null, source_task: { ...proposal.source_task, agent_role: 'lore_agent' } }
    const message = { ...data.messages[0], proposal: change, base_content: '', base_title: '' }
    const whole = { ...data, document_id: null, agent_role: 'lore_agent', messages: [message] }
    response(whole)
    expect((await collectionClient.getOrCreateSettingCollectionCollaboration('collection')).messages[0].proposal?.target_document_id).toBeNull()
    const document = { id: 'created-document', project_id: null, setting_collection_id: 'collection', chapter_id: null, type: 'world_overview', title: 'Tide', path: 'world/tide.md', current_version_id: 'accepted-version', current_version: null, metadata: {}, created_at: date, updated_at: date, deleted_at: null }
    response({ conversation: { ...whole, messages: [{ ...message, proposal: { ...change, status: 'accepted' }, proposal_status: 'accepted', accepted_version_id: 'accepted-version' }] }, document, content: 'New text' })
    const result = await collectionClient.acceptSettingCollectionCollaborationProposal('collection', 'proposal', null, '', '')
    expect(result.document.id).toBe('created-document')
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/collection/collaboration/proposals/proposal/accept', expect.objectContaining({ body: JSON.stringify({ expected_current_version_id: null, expected_draft_content: '', expected_title: '' }) }))
  })

  it('rejects collection responses belonging to another scope and a new proposal carrying a false base', async () => {
    response({ ...data, document_id: null, setting_collection_id: 'other', agent_role: 'lore_agent' })
    await expect(collectionClient.getOrCreateSettingCollectionCollaboration('collection')).rejects.toMatchObject({ code: 'invalid_response' })
    const change = { ...proposal, target_document_id: null, base_version_id: 'version', source_task: { ...proposal.source_task, agent_role: 'lore_agent' } }
    response({ ...data, document_id: null, agent_role: 'lore_agent', messages: [{ ...data.messages[0], proposal: change }] })
    await expect(collectionClient.getOrCreateSettingCollectionCollaboration('collection')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('rejects an accepted existing-entry result that names a different document in the same collection', async () => {
    const change = { ...proposal, status: 'accepted', source_task: { ...proposal.source_task, agent_role: 'lore_agent' } }
    const whole = { ...data, document_id: null, agent_role: 'lore_agent', messages: [{ ...data.messages[0], proposal: change, proposal_status: 'accepted', accepted_version_id: 'accepted-version' }] }
    const document = { id: 'wrong-document', project_id: null, setting_collection_id: 'collection', chapter_id: null, type: 'world_overview', title: 'Tide', path: 'world/tide.md', current_version_id: 'accepted-version', current_version: null, metadata: {}, created_at: date, updated_at: date, deleted_at: null }
    response({ conversation: whole, document, content: 'New text' })
    await expect(collectionClient.acceptSettingCollectionCollaborationProposal('collection', 'proposal', 'version', 'Old text', 'Tide')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('uses the separate document collaboration endpoint and retains request identity and exact selection', async () => {
    response(data)
    expect(await getOrCreateSettingCollaboration('collection', 'document')).toEqual({ ...data, attempts: [], messages: data.messages.map(message => ({ ...message, attempt_id: null })) })
    expect(fetch).toHaveBeenCalledWith('/api/v1/setting-collections/collection/documents/document/collaboration', expect.objectContaining({ method: 'POST' }))
    response(data)
    const request = { client_message_id: 'retry-identity', content: 'Expand', base_version_id: 'version', draft_content: 'Old text', selection: { start: 0, end: 3, text: 'Old' } }
    await sendSettingCollaborationMessage('collection', 'document', request)
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/collaboration/messages'), expect.objectContaining({ body: JSON.stringify(request) }))
  })

  it.each([
    null, { ...data, messages: null }, { ...data, document_id: 'other-document' },
    { ...data, agent_role: 'gura' }, { ...data, messages: [{ ...data.messages[0], proposal: { ...proposal, target_document_id: 'other' } }] },
    { ...data, messages: [{ ...data.messages[0], proposal_status: 'accepted', proposal: { ...proposal, status: 'accepted' } }] },
    { ...data, messages: [{ ...data.messages[0], base_content: null }] },
  ])('rejects incomplete or mismatched collaboration response %#', async (value) => {
    response(value)
    await expect(getOrCreateSettingCollaboration('collection', 'document')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('rejects invalid JSON and malformed accepted documents instead of exposing runtime errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{', { status: 200 })))
    await expect(getOrCreateSettingCollaboration('collection', 'document')).rejects.toMatchObject({ code: 'invalid_response' })
    response({ conversation: data, document: {}, content: 'New text' })
    await expect(acceptSettingCollaborationProposal('collection', 'document', 'proposal', 'version', 'Old text', 'Tide')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('decodes the accepted version and updated conversation together', async () => {
    const document = { id: 'document', project_id: null, setting_collection_id: 'collection', chapter_id: null,
      type: 'world_overview', title: 'Tide', path: 'world/tide.md', current_version_id: 'accepted-version', current_version: null,
      metadata: {}, created_at: date, updated_at: date, deleted_at: null }
    const accepted = { ...data, messages: [{ ...data.messages[0], proposal: { ...proposal, status: 'accepted' }, proposal_status: 'accepted', accepted_version_id: 'accepted-version' }] }
    response({ conversation: accepted, document, content: 'New text' })
    const result = await acceptSettingCollaborationProposal('collection', 'document', 'proposal', 'version', 'Old text', 'Tide')
    expect(result.document.current_version_id).toBe('accepted-version')
    expect(result.conversation.messages[0].proposal_status).toBe('accepted')
    expect(result.content).toBe('New text')
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/proposals/proposal/accept'), expect.objectContaining({
      body: JSON.stringify({ expected_current_version_id: 'version', expected_draft_content: 'Old text', expected_title: 'Tide' }),
    }))
  })

  it('preserves retryable errors without substituting a fake reply', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
    await expect(getOrCreateSettingCollaboration('collection', 'document')).rejects.toMatchObject({ code: 'setting_collaboration_unavailable', status: 0 })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 409 })))
    await expect(getOrCreateSettingCollaboration('collection', 'document')).rejects.toMatchObject({ code: 'setting_collaboration_failed', status: 409 })
  })
})
