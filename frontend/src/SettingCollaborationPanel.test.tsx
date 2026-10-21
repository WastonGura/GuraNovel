import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SettingCollaborationPanel, { type SettingCollaborationPanelProps } from './SettingCollaborationPanel'
import * as client from './api/settingCollaborationClient'
import type { SettingNote } from './settingNotes'
import { ApiError } from './api/client'

vi.mock('./api/settingCollaborationClient', () => ({ getOrCreateSettingCollectionCollaboration: vi.fn(), sendSettingCollectionCollaborationMessage: vi.fn(), acceptSettingCollectionCollaborationProposal: vi.fn(), discardSettingCollectionCollaborationProposal: vi.fn() }))
const note: SettingNote = { id: 'note-one', documentId: 'doc-one', versionId: 'version-one', title: '旧港', body: '港口在黄昏关闭。', category: 'world' }
const other: SettingNote = { id: 'note-two', documentId: 'doc-two', versionId: 'version-two', title: '守灯人', body: '守灯人每晚巡逻。', category: 'setting' }
const proposal = { id: 'proposal-one', setting_collection_id: 'world-one', target_document_id: 'doc-one', base_version_id: 'version-one', title: '旧港', category: 'world', proposed_content: '港口在黄昏关闭，只有灯塔仍然亮着。', reason: '补充地点细节。' }
const conversation = (messages: client.SettingCollaborationMessage[] = []): client.SettingCollaboration => ({ id: 'conversation-one', setting_collection_id: 'world-one', document_id: null, agent_role: 'lore_agent', messages, created_at: '2026-10-20T00:00:00Z', updated_at: '2026-10-20T00:00:00Z' })
const suggestion = (): client.SettingCollaborationMessage => ({ id: 'message-agent', role: 'assistant', content: '可以从灯塔补充这个地点。', client_message_id: null, proposal, proposal_status: 'pending', base_content: note.body, base_title: note.title, accepted_version_id: null, created_at: '2026-10-20T00:00:00Z' })
const newSuggestion = (): client.SettingCollaborationMessage => ({ ...suggestion(), proposal: { ...proposal, target_document_id: null, base_version_id: null, title: '灯塔', proposed_content: '灯塔建在港湾入口。' }, base_content: '', base_title: '' })
const userMessage = (id: string, content: string): client.SettingCollaborationMessage => ({ id: 'message-user', role: 'user', content, client_message_id: id, proposal: null, proposal_status: null, base_content: null, base_title: null, accepted_version_id: null, created_at: '2026-10-20T00:00:00Z' })
const accepted = (message = suggestion(), documentId = 'doc-one'): client.SettingCollaborationAcceptance => ({ conversation: conversation([{ ...message, proposal_status: 'accepted' }]), document: { id: documentId, project_id: null, setting_collection_id: 'world-one', chapter_id: null, type: 'world_overview', title: message.proposal!.title, path: 'world/old-port.md', current_version_id: 'accepted-version', current_version: null, created_at: '', updated_at: '' }, content: message.proposal!.proposed_content })
const props = (changes: Partial<SettingCollaborationPanelProps> = {}): SettingCollaborationPanelProps => ({ collectionId: 'world-one', notes: [note, other], onPrepare: vi.fn().mockResolvedValue(true), onApply: vi.fn(() => true), onClose: vi.fn(), ...changes })
const ready = () => screen.findByRole('heading', { name: '一起构建这个世界' })
const storageKey = 'guranovel:setting-agent:collection:world-one'

beforeEach(() => { vi.resetAllMocks(); sessionStorage.clear(); vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true }))); vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(conversation()); vi.mocked(client.acceptSettingCollectionCollaborationProposal).mockResolvedValue(accepted()) })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('keeps one collection conversation and composer when entries or categories change', async () => {
  const initial = props(), view = render(<SettingCollaborationPanel {...initial} />)
  await ready()
  const input = screen.getByRole('textbox', { name: '给设定 Agent 的消息' })
  fireEvent.change(input, { target: { value: '把地理与守灯人的故事联系起来。' } })
  view.rerender(<SettingCollaborationPanel {...initial} notes={[{ ...other, title: '新的角色名' }, note]} />)
  expect(screen.getByRole('textbox')).toBe(input)
  expect(input).toHaveValue('把地理与守灯人的故事联系起来。')
  expect(screen.queryByRole('heading', { name: '新的角色名' })).not.toBeInTheDocument()
  expect(client.getOrCreateSettingCollectionCollaboration).toHaveBeenCalledExactlyOnceWith('world-one')
})

it('can begin with an empty collection and flushes writing only on an explicit generation request', async () => {
  const prepare = vi.fn().mockResolvedValue(true)
  vi.mocked(client.sendSettingCollectionCollaborationMessage).mockImplementation(async (_collection, payload) => conversation([userMessage(payload.client_message_id, payload.content), newSuggestion()]))
  render(<SettingCollaborationPanel {...props({ notes: [], onPrepare: prepare })} />)
  await ready()
  expect(prepare).not.toHaveBeenCalled()
  expect(screen.getByRole('textbox')).toHaveAttribute('placeholder', '描述想新增或完善的设定…')
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '给这个世界创建一个灯塔。' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '发送给设定 Agent' })) })
  expect(prepare).toHaveBeenCalledTimes(1)
  expect(screen.getByText('新建条目 · 灯塔')).toBeInTheDocument()
  expect(client.acceptSettingCollectionCollaborationProposal).not.toHaveBeenCalled()
})

it('persists one collection request before flushing and preserves text typed while the Agent works', async () => {
  let finishPrepare!: (value: boolean) => void, finishSend!: (value: client.SettingCollaboration) => void
  const prepare = vi.fn(() => new Promise<boolean>(resolve => { finishPrepare = resolve }))
  vi.mocked(client.sendSettingCollectionCollaborationMessage).mockImplementation(() => new Promise(resolve => { finishSend = resolve }))
  render(<SettingCollaborationPanel {...props({ onPrepare: prepare })} />)
  await ready()
  const input = screen.getByRole('textbox')
  fireEvent.change(input, { target: { value: '补充灯塔的细节。' } })
  fireEvent.click(screen.getByRole('button', { name: '发送给设定 Agent' }))
  fireEvent.keyDown(input, { key: 'Enter' })
  const cached = JSON.parse(sessionStorage.getItem(storageKey)!)
  expect(cached.pending.id).toBeTruthy()
  expect(prepare).toHaveBeenCalledTimes(1)
  expect(client.sendSettingCollectionCollaborationMessage).not.toHaveBeenCalled()
  await act(async () => { finishPrepare(true) })
  const payload = vi.mocked(client.sendSettingCollectionCollaborationMessage).mock.calls[0][1]
  expect(payload).toEqual({ client_message_id: cached.pending.id, content: '补充灯塔的细节。' })
  fireEvent.change(input, { target: { value: '我接着写下一条想法。' } })
  await act(async () => { finishSend(conversation([userMessage(payload.client_message_id, payload.content), suggestion()])) })
  expect(input).toHaveValue('我接着写下一条想法。')
  expect(JSON.parse(sessionStorage.getItem(storageKey)!).pending).toBeNull()
})

it('retries an uncertain request with its collection message identity after other entries change', async () => {
  vi.mocked(client.sendSettingCollectionCollaborationMessage).mockRejectedValueOnce(new ApiError(0, 'offline', '连接失败，可以重试。')).mockImplementationOnce(async (_collection, payload) => conversation([userMessage(payload.client_message_id, payload.content)]))
  const initial = props(), view = render(<SettingCollaborationPanel {...initial} />)
  await ready()
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '请补充。' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '发送给设定 Agent' })) })
  const first = vi.mocked(client.sendSettingCollectionCollaborationMessage).mock.calls[0][1]
  view.rerender(<SettingCollaborationPanel {...initial} notes={[{ ...other, body: '我后来写的角色正文', versionId: 'later' }]} />)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重试上一条' })) })
  expect(vi.mocked(client.sendSettingCollectionCollaborationMessage).mock.calls[1][1]).toEqual(first)
  expect(initial.onPrepare).toHaveBeenCalledTimes(1)
})

it('reconciles a committed message after a lost response and reload without generating it twice', async () => {
  vi.mocked(client.sendSettingCollectionCollaborationMessage).mockRejectedValue(new ApiError(0, 'offline', '响应丢失。'))
  const initial = props(), view = render(<SettingCollaborationPanel {...initial} />)
  await ready()
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '继续完善。' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '发送给设定 Agent' })) })
  const sent = vi.mocked(client.sendSettingCollectionCollaborationMessage).mock.calls[0][1]
  view.unmount()
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(conversation([userMessage(sent.client_message_id, sent.content), suggestion()]))
  render(<SettingCollaborationPanel {...initial} />)
  await screen.findByRole('button', { name: '比较建议' })
  expect(screen.getByRole('textbox')).toHaveValue('')
  expect(screen.queryByRole('button', { name: '重试上一条' })).not.toBeInTheDocument()
  expect(client.sendSettingCollectionCollaborationMessage).toHaveBeenCalledTimes(1)
})

it('adopts a proposal for its actual target even when that entry is not the first note', async () => {
  const message = { ...suggestion(), proposal: { ...proposal, target_document_id: 'doc-two', base_version_id: 'version-two', title: other.title, category: other.category, proposed_content: '守灯人守望整个海湾。' }, base_content: other.body, base_title: other.title }
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(conversation([message]))
  const result = accepted(message, 'doc-two'), apply = vi.fn(() => true)
  vi.mocked(client.acceptSettingCollectionCollaborationProposal).mockResolvedValue(result)
  render(<SettingCollaborationPanel {...props({ onApply: apply })} />)
  expect(await screen.findByRole('button', { name: '采纳建议' })).toBeDisabled()
  expect(apply).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '比较建议' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '采纳建议' })) })
  expect(client.acceptSettingCollectionCollaborationProposal).toHaveBeenCalledExactlyOnceWith('world-one', 'proposal-one', 'version-two', other.body, other.title)
  expect(apply).toHaveBeenCalledExactlyOnceWith(result, other)
})

it('creates a proposed entry only after comparison and explicit adoption', async () => {
  const message = newSuggestion(), result = accepted(message, 'new-document'), apply = vi.fn(() => true)
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(conversation([message]))
  vi.mocked(client.acceptSettingCollectionCollaborationProposal).mockResolvedValue(result)
  render(<SettingCollaborationPanel {...props({ notes: [], onApply: apply })} />)
  expect(await screen.findByRole('button', { name: '采纳建议' })).toBeDisabled()
  expect(apply).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '比较建议' }))
  expect(screen.queryByRole('heading', { name: '讨论时的原文' })).not.toBeInTheDocument()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '采纳建议' })) })
  expect(client.acceptSettingCollectionCollaborationProposal).toHaveBeenCalledExactlyOnceWith('world-one', 'proposal-one', null, '', '')
  expect(apply).toHaveBeenCalledExactlyOnceWith(result, undefined)
})

it('refuses to adopt if flushing reveals newer text in the actual target', async () => {
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(conversation([suggestion()]))
  let finish!: (value: boolean) => void
  const initial = props({ onPrepare: vi.fn(() => new Promise<boolean>(resolve => { finish = resolve })) })
  const view = render(<SettingCollaborationPanel {...initial} />)
  fireEvent.click(await screen.findByRole('button', { name: '比较建议' }))
  fireEvent.click(screen.getByRole('button', { name: '采纳建议' }))
  view.rerender(<SettingCollaborationPanel {...initial} notes={[{ ...note, body: '我已经改写了这段正文。', versionId: 'newer-version' }, other]} />)
  await act(async () => { finish(true) })
  expect(client.acceptSettingCollectionCollaborationProposal).not.toHaveBeenCalled()
  expect(initial.onApply).not.toHaveBeenCalled()
  expect(screen.getByRole('alert')).toHaveTextContent('目标条目的正文或标题已更新')
})

it('disables stale proposals for a deleted target while retaining their suggested text', async () => {
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(conversation([suggestion()]))
  render(<SettingCollaborationPanel {...props({ notes: [other] })} />)
  fireEvent.click(await screen.findByRole('button', { name: '比较建议' }))
  expect(screen.getByText(proposal.proposed_content)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '采纳建议' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '基于最新设定集重新讨论' }))
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toContain('设定集的最新内容')
})

it('retains newer author text when adoption commits while writing continues', async () => {
  let finish!: (value: client.SettingCollaborationAcceptance) => void
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(conversation([suggestion()]))
  vi.mocked(client.acceptSettingCollectionCollaborationProposal).mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const initial = props({ onApply: vi.fn(() => false) }), view = render(<SettingCollaborationPanel {...initial} />)
  fireEvent.click(await screen.findByRole('button', { name: '比较建议' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '采纳建议' })) })
  view.rerender(<SettingCollaborationPanel {...initial} notes={[{ ...note, body: '新的本地文字' }, other]} />)
  await act(async () => { finish(accepted()) })
  expect(initial.onApply).toHaveBeenCalledWith(accepted(), note)
  expect(screen.getByRole('alert')).toHaveTextContent('你刚输入的文稿已保留')
})

it('checks an uncertain new-entry adoption after reload without creating another proposal', async () => {
  const message = newSuggestion(), result = accepted(message, 'new-document')
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(conversation([message]))
  vi.mocked(client.acceptSettingCollectionCollaborationProposal).mockRejectedValueOnce(new ApiError(0, 'offline', '采纳响应丢失。')).mockResolvedValueOnce(result)
  const initial = props({ notes: [] }), view = render(<SettingCollaborationPanel {...initial} />)
  fireEvent.click(await screen.findByRole('button', { name: '比较建议' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '采纳建议' })) })
  view.unmount()
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(result.conversation)
  render(<SettingCollaborationPanel {...initial} />)
  const retry = await screen.findByRole('button', { name: '检查采纳结果' })
  await act(async () => { fireEvent.click(retry) })
  expect(vi.mocked(client.acceptSettingCollectionCollaborationProposal).mock.calls[1]).toEqual(vi.mocked(client.acceptSettingCollectionCollaborationProposal).mock.calls[0])
  expect(initial.onPrepare).toHaveBeenCalledTimes(1)
  expect(client.sendSettingCollectionCollaborationMessage).not.toHaveBeenCalled()
  expect(initial.onApply).toHaveBeenCalledExactlyOnceWith(result, undefined)
})

it('retains adoption recovery and never merges a response naming another target document', async () => {
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(conversation([suggestion()]))
  vi.mocked(client.acceptSettingCollectionCollaborationProposal).mockResolvedValueOnce({ ...accepted(), document: { ...accepted().document, id: 'wrong-document' } }).mockResolvedValueOnce(accepted())
  const initial = props()
  render(<SettingCollaborationPanel {...initial} />)
  fireEvent.click(await screen.findByRole('button', { name: '比较建议' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '采纳建议' })) })
  expect(initial.onApply).not.toHaveBeenCalled()
  expect(screen.getByRole('alert')).toHaveTextContent('采纳结果与目标条目不一致')
  expect(JSON.parse(sessionStorage.getItem(storageKey)!).acceptance.proposalId).toBe('proposal-one')
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查采纳结果' })) })
  expect(initial.onApply).toHaveBeenCalledExactlyOnceWith(accepted(), note)
  expect(initial.onPrepare).toHaveBeenCalledTimes(1)
})

it('keeps failed collection preparation retryable and preserves the composer', async () => {
  const prepare = vi.fn().mockResolvedValue(false)
  render(<SettingCollaborationPanel {...props({ onPrepare: prepare })} />)
  await ready()
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '再聊一个新地点。' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '发送给设定 Agent' })) })
  expect(client.sendSettingCollectionCollaborationMessage).not.toHaveBeenCalled()
  expect(screen.getByRole('textbox')).toHaveValue('再聊一个新地点。')
  expect(screen.getByRole('alert')).toHaveTextContent('设定集草稿未能保存')
  expect(screen.getByRole('button', { name: '重试上一条' })).toBeEnabled()
})

it('discards a collection suggestion without writing any document', async () => {
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(conversation([suggestion()]))
  vi.mocked(client.discardSettingCollectionCollaborationProposal).mockResolvedValue(conversation([{ ...suggestion(), proposal_status: 'rejected' }]))
  const initial = props()
  render(<SettingCollaborationPanel {...initial} />)
  const dismiss = await screen.findByRole('button', { name: '暂不采用' })
  await act(async () => { fireEvent.click(dismiss) })
  expect(client.discardSettingCollectionCollaborationProposal).toHaveBeenCalledExactlyOnceWith('world-one', 'proposal-one')
  expect(screen.getByText('未采用 · 旧港')).toBeInTheDocument()
  expect(initial.onApply).not.toHaveBeenCalled()
})

it('sends author comments without a separate typed message and acknowledges their exact snapshot', async () => {
  vi.mocked(client.sendSettingCollectionCollaborationMessage).mockImplementation(async (_collection, payload) => conversation([userMessage(payload.client_message_id, payload.content)]))
  const sent = vi.fn()
  render(<SettingCollaborationPanel {...props({ feedback: '原文：港口\n意见：增加来往船只', onSent: sent })} />)
  await ready()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '发送给设定 Agent' })) })
  expect(vi.mocked(client.sendSettingCollectionCollaborationMessage).mock.calls[0][1].content).toBe('作者评论：\n原文：港口\n意见：增加来往船只')
  expect(sent).toHaveBeenCalledExactlyOnceWith('原文：港口\n意见：增加来往船只')
})

it('allows archived collection history while disabling generation and adoption', async () => {
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockResolvedValue(conversation([suggestion()]))
  render(<SettingCollaborationPanel {...props({ readOnly: true })} />)
  fireEvent.click(await screen.findByRole('button', { name: '比较建议' }))
  expect(screen.getByRole('textbox')).toBeDisabled()
  expect(screen.getByRole('button', { name: '采纳建议' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '暂不采用' })).toBeDisabled()
})

it('preserves its collection draft through failed initialization and closes with motion', async () => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })))
  vi.useFakeTimers()
  vi.mocked(client.getOrCreateSettingCollectionCollaboration).mockRejectedValue(new ApiError(503, 'missing_config', '设定 Agent 暂不可用。'))
  const close = vi.fn()
  render(<SettingCollaborationPanel {...props({ onClose: close })} />)
  await act(async () => { await Promise.resolve() })
  expect(screen.getByRole('alert')).toHaveTextContent('设定 Agent 暂不可用')
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '下次继续讨论。' } })
  fireEvent.click(screen.getByRole('button', { name: '关闭设定协作' }))
  expect(screen.getByRole('complementary', { name: '设定 Agent 协作' })).toHaveClass('is-closing')
  expect(close).not.toHaveBeenCalled()
  await act(async () => { vi.advanceTimersByTime(400) })
  expect(close).toHaveBeenCalledTimes(1)
  expect(JSON.parse(sessionStorage.getItem(storageKey)!).draft).toBe('下次继续讨论。')
})
