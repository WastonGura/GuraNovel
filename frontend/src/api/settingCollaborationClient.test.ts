import { afterEach, describe, expect, it, vi } from 'vitest'
import { acceptSettingCollaborationProposal, getOrCreateSettingCollaboration, sendSettingCollaborationMessage } from './settingCollaborationClient'
import * as collectionClient from './settingCollaborationClient'

const date = '2026-10-20T13:00:00Z'
const proposal = { id: 'proposal', setting_collection_id: 'collection', target_document_id: 'document', base_version_id: 'version',
  title: 'Tide', category: 'world', proposed_content: 'New text', reason: 'A cost', status: 'pending', created_at: date,
  source_task: { agent_role: 'worldbuilding_agent', workflow_run_id: null, chapter_id: null, novel_title: null } }
const data = { id: 'conversation', setting_collection_id: 'collection', document_id: 'document', agent_role: 'worldbuilding_agent',
  created_at: date, updated_at: date, messages: [{ id: 'message', role: 'assistant', content: 'Compare this', client_message_id: null,
    proposal, proposal_status: 'pending', base_content: 'Old text', base_title: 'Tide', accepted_version_id: null, created_at: date }] }

afterEach(() => vi.unstubAllGlobals())

function response(value: unknown) { vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(value), { status: 200 }))) }

describe('setting Agent client', () => {
  it('discusses the whole collection without binding requests to a selected document', async () => {
    const whole = { ...data, document_id: null, agent_role: 'lore_agent', messages: [] }
    response(whole)
    expect(await collectionClient.getOrCreateSettingCollectionCollaboration('collection')).toEqual(whole)
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
    expect(await getOrCreateSettingCollaboration('collection', 'document')).toEqual(data)
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
