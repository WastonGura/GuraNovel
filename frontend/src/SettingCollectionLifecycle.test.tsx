import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SettingCollectionWorkspace from './SettingCollectionWorkspace'
import * as api from './api/client'

vi.mock('./api/client', async original => ({ ...await original<typeof api>(),
  getSettingCollection: vi.fn(), listCollectionProjects: vi.fn(), listCollectionDocuments: vi.fn(),
  readDocumentContent: vi.fn(), getDocument: vi.fn(), writeDocument: vi.fn(), patchDocument: vi.fn(),
  listDocumentVersions: vi.fn(), readDocumentVersionContent: vi.fn(), listSettingDocumentRestorePoints: vi.fn(), createSettingDocumentRestorePoint: vi.fn(), restoreSettingDocumentRestorePoint: vi.fn(), archiveSettingCollection: vi.fn(),
}))
const collection: api.SettingCollection = { id: 'collection', slug: 'lighthouse', title: '灯塔设定集', description: '', status: 'active', revision: 3,
  owner_id: 'author', workspace_root: '', metadata: {}, created_at: '2026-10-21T01:00:00Z', updated_at: '2026-10-21T01:00:00Z' }
const document: api.Document = { id: 'note', title: '守灯人', type: 'character_profile', setting_collection_id: 'collection', current_version_id: 'v3',
  project_id: null, chapter_id: null, path: 'setting/keeper.md', current_version: null, metadata: { category: 'setting' },
  created_at: '2026-10-21T01:00:00Z', updated_at: '2026-10-21T01:00:00Z' }
const version = (id: string, number: number): api.DocumentVersion => ({ id, document_id: 'note', version_number: number, parent_version_id: null, source: 'user', actor_user_id: null, agent_role: null, workflow_run_id: null, content_hash: id, byte_size: 20, word_count: 8, file_path: '', change_summary: null, created_at: '2026-10-21T01:00:00Z' })
const point = (id = 'point-1', number = 1, savedVersion = version('v1', 1)): api.SettingDocumentRestorePoint => ({ id, document_id: 'note', version_id: savedVersion.id,
  point_number: number, summary: null, created_at: '2026-10-21T02:00:00Z', version: savedVersion, matches_current: savedVersion.id === 'v3' })
function open() { return render(<MemoryRouter initialEntries={['/setting-collections/collection?note=note&mode=note']}><Routes><Route path="/setting-collections/:settingCollectionId" element={<SettingCollectionWorkspace />} /></Routes></MemoryRouter>) }
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear(); localStorage.clear()
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
  vi.mocked(api.getSettingCollection).mockResolvedValue(collection)
  vi.mocked(api.listCollectionDocuments).mockResolvedValue([document])
  vi.mocked(api.listCollectionProjects).mockResolvedValue([{ id: 'novel', title: '潮汐手记' } as api.Project])
  vi.mocked(api.getDocument).mockResolvedValue(document)
  vi.mocked(api.readDocumentContent).mockResolvedValue({ document_id: 'note', version_id: 'v3', content: '现在的正文' })
  vi.mocked(api.listDocumentVersions).mockResolvedValue([version('v1', 1), version('v3', 3)])
  vi.mocked(api.listSettingDocumentRestorePoints).mockResolvedValue([point()])
  vi.mocked(api.readDocumentVersionContent).mockImplementation(async (_id, id) => ({ document_id: 'note', version_id: id, content: id === 'v1' ? '最早的正文' : '现在的正文' }))
  vi.mocked(api.writeDocument).mockResolvedValue(version('v4', 4))
  vi.mocked(api.patchDocument).mockResolvedValue(document)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function historyAtFirstVersion() {
  fireEvent.click(await screen.findByRole('button', { name: /^历史存档$/ }))
  const history = await screen.findByRole('dialog', { name: '历史存档' })
  fireEvent.click(await within(history).findByRole('button', { name: /^存档 1$/ }))
  await within(history).findByText('最早的正文')
  return history
}
async function confirmRestore(history: HTMLElement) {
  fireEvent.click(within(history).getByRole('button', { name: '恢复此存档' }))
  fireEvent.click(await within(history).findByRole('button', { name: '确认恢复' }))
}
async function saveCurrent() {
  fireEvent.click(await screen.findByRole('button', { name: '存档当前内容' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '存档当前内容' })).toHaveAttribute('data-state', 'success'))
  expect(screen.queryByText('已存档')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '存档当前内容' })).toHaveAccessibleDescription('当前内容已存档')
}
async function archiveConfirmation() {
  const attempts = vi.mocked(api.archiveSettingCollection).mock.calls.length
  fireEvent.click(await screen.findByRole('button', { name: '设定集操作' }))
  const menu = await screen.findByRole('menu', { name: '设定集操作' })
  expect(screen.queryByRole('dialog', { name: '归档设定集' })).not.toBeInTheDocument()
  expect(api.archiveSettingCollection).toHaveBeenCalledTimes(attempts)
  fireEvent.click(within(menu).getByRole('menuitem', { name: '归档设定集' }))
  const archive = await screen.findByRole('dialog', { name: '归档设定集' })
  await waitFor(() => expect(within(archive).getByRole('button', { name: '确认归档' })).toBeEnabled())
  return archive
}

it('opens real version history from the active entry and restores only after confirmation', async () => {
  open()
  await screen.findByRole('textbox', { name: '设定正文' })
  const history = await historyAtFirstVersion()
  expect(within(history).queryByRole('button', { name: '存档当前内容' })).not.toBeInTheDocument()
  expect(api.restoreSettingDocumentRestorePoint).not.toHaveBeenCalled()
  fireEvent.click(within(history).getByRole('button', { name: '恢复此存档' }))
  await within(history).findByRole('button', { name: '确认恢复' })
  expect(history).toHaveTextContent('潮汐手记')
  expect(api.restoreSettingDocumentRestorePoint).not.toHaveBeenCalled()
})

it.each([0, 1])('keeps the known association count while opening history refreshes its impact in the background (%i novels)', async number => {
  const projects = [{ id: 'novel', title: '潮汐手记' } as api.Project].slice(0, number)
  let finish!: (value: api.Project[]) => void
  vi.mocked(api.listCollectionProjects).mockResolvedValueOnce(projects).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  open()
  const count = await screen.findByRole('button', { name: `关联小说 (${number})` })
  const history = await historyAtFirstVersion()
  expect(count).toHaveAccessibleName(`关联小说 (${number})`)
  fireEvent.click(within(history).getByRole('button', { name: '恢复此存档' }))
  expect(await within(history).findByText('正在读取关联小说…', { selector: '[role="status"]' })).toBeInTheDocument()
  expect(api.restoreSettingDocumentRestorePoint).not.toHaveBeenCalled()
  await act(async () => finish(projects))
  await within(history).findByText(number ? '潮汐手记' : '暂无关联小说')
  expect(count).toHaveAccessibleName(`关联小说 (${number})`)
})

it('retains a known header count on refresh failure without treating stale associations as safe to restore', async () => {
  const projects = [{ id: 'novel', title: '潮汐手记' } as api.Project]
  let fail!: (reason: Error) => void
  vi.mocked(api.listCollectionProjects).mockResolvedValueOnce(projects)
    .mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject }))
    .mockRejectedValueOnce(new Error('still offline'))
    .mockResolvedValue([...projects, { id: 'second-novel', title: '夜渡' } as api.Project])
  const restored = { ...version('v4', 4), parent_version_id: 'v3', restored_from_version_id: 'v1', restored_from_restore_point_id: 'point-1' }
  vi.mocked(api.restoreSettingDocumentRestorePoint).mockResolvedValue(restored)
  vi.mocked(api.getDocument).mockResolvedValue({ ...document, current_version_id: restored.id, current_version: restored })
  open()
  const count = await screen.findByRole('button', { name: '关联小说 (1)' })
  const history = await historyAtFirstVersion()
  fireEvent.click(within(history).getByRole('button', { name: '恢复此存档' }))
  await act(async () => fail(new Error('offline')))
  await within(history).findByRole('button', { name: '重新加载关联小说' })
  expect(count).toHaveAccessibleName('关联小说 (1)')
  fireEvent.click(within(history).getByRole('button', { name: '确认恢复' }))
  expect(await within(history).findByText('关联小说未能读取，请重新加载后再恢复。', { selector: '[role="alert"]' })).toBeInTheDocument()
  expect(api.restoreSettingDocumentRestorePoint).not.toHaveBeenCalled()
  expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveTextContent('现在的正文')
  fireEvent.click(within(history).getByRole('button', { name: '重新加载关联小说' }))
  await within(history).findByText('夜渡')
  expect(count).toHaveAccessibleName('关联小说 (2)')
  expect(history).toHaveTextContent('关联小说 · 2 部')
  fireEvent.click(within(history).getByRole('button', { name: '确认恢复' }))
  await waitFor(() => expect(history).not.toBeInTheDocument())
  expect(api.restoreSettingDocumentRestorePoint).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveTextContent('最早的正文')
})

it('requires explicit collection archive confirmation and keeps the archived workspace readable', async () => {
  vi.mocked(api.archiveSettingCollection).mockResolvedValue({ ...collection, status: 'archived', revision: 4 })
  open()
  await screen.findByRole('textbox', { name: '设定正文' })
  let options = await archiveConfirmation()
  expect(api.archiveSettingCollection).not.toHaveBeenCalled()
  expect(options).toHaveTextContent('潮汐手记')
  fireEvent.click(within(options).getByRole('button', { name: '取消' }))
  await waitFor(() => expect(options).not.toBeInTheDocument())
  expect(api.archiveSettingCollection).not.toHaveBeenCalled()
  options = await archiveConfirmation()
  fireEvent.click(within(options).getByRole('button', { name: '确认归档' }))
  await waitFor(() => expect(api.archiveSettingCollection).toHaveBeenCalledWith('collection'))
  await screen.findAllByText('已归档 · 只读')
  expect(screen.getByRole('button', { name: '新建内容' })).toBeDisabled()
  expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveAttribute('contenteditable', 'false')
  expect(screen.queryByRole('button', { name: '存档当前内容' })).not.toBeInTheDocument()
  const history = await historyAtFirstVersion()
  expect(within(history).queryByRole('button', { name: '存档当前内容' })).not.toBeInTheDocument()
  expect(within(history).getByRole('button', { name: '恢复此存档' })).toBeDisabled()
  expect(api.createSettingDocumentRestorePoint).not.toHaveBeenCalled()
  fireEvent.click(within(history).getByRole('button', { name: '关闭历史存档' }))
  await waitFor(() => expect(history).not.toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: '设定集操作' }))
  const menu = await screen.findByRole('menu', { name: '设定集操作' })
  expect(within(menu).getByRole('menuitem', { name: '与设定 Agent 协作' })).toBeEnabled()
  expect(within(menu).queryByRole('menuitem', { name: '归档设定集' })).not.toBeInTheDocument()
})

it('restores historical body and comments, then saves further writing against the new current version', async () => {
  const restored = { ...version('v4', 4), parent_version_id: 'v3', restored_from_version_id: 'v1', restored_from_restore_point_id: 'point-1' }
  const oldComment = { id: 'old-comment', start: 3, end: 5, quote: '正文', text: '历史存档中的意见', color: '#8d9bff', submitted: false, orphaned: false }
  vi.mocked(api.restoreSettingDocumentRestorePoint).mockResolvedValue(restored)
  vi.mocked(api.getDocument).mockResolvedValue({ ...document, current_version_id: restored.id, current_version: restored,
    metadata: { category: 'setting', comments: [oldComment], comments_source_version_id: restored.id } })
  vi.mocked(api.writeDocument).mockResolvedValue(version('v5', 5))
  open()
  await screen.findByRole('textbox', { name: '设定正文' })
  const history = await historyAtFirstVersion()
  await confirmRestore(history)
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '历史存档' })).not.toBeInTheDocument())
  const editor = screen.getByRole('textbox', { name: '设定正文' })
  expect(editor).toHaveTextContent('最早的正文')
  expect(api.restoreSettingDocumentRestorePoint).toHaveBeenCalledWith('note', 'point-1', expect.objectContaining({ expected_current_version_id: 'v3' }))
  expect(api.writeDocument).not.toHaveBeenCalled()
  fireEvent.input(editor, { target: { textContent: '最早的正文，继续补充。' } })
  await waitFor(() => expect(api.writeDocument).toHaveBeenCalledWith('note', expect.objectContaining({
    content: '最早的正文，继续补充。', expected_current_version_id: 'v4', setting_comments: expect.arrayContaining([oldComment]),
  })))
})

it('keeps the author draft and history selection after a restore conflicts with a newer server version', async () => {
  vi.mocked(api.restoreSettingDocumentRestorePoint).mockRejectedValue(new api.ApiError(409, 'version_conflict', 'Changed'))
  vi.mocked(api.getDocument).mockResolvedValue({ ...document, current_version_id: 'other-version', current_version: version('other-version', 5) })
  open()
  const editor = await screen.findByRole('textbox', { name: '设定正文' })
  fireEvent.input(editor, { target: { textContent: '保留作者刚写的最新正文' } })
  const history = await historyAtFirstVersion()
  await confirmRestore(history)
  expect(await within(history).findByRole('alert')).toHaveTextContent('版本已更新，当前草稿已保留')
  expect(editor).toHaveTextContent('保留作者刚写的最新正文')
  expect(within(history).getByRole('button', { name: '存档 1' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(within(history).getByRole('button', { name: '返回预览' }))
  expect(within(history).getByRole('region', { name: '存档正文' })).toHaveTextContent('最早的正文')
  expect(api.restoreSettingDocumentRestorePoint).toHaveBeenCalledWith('note', 'point-1', expect.objectContaining({ expected_current_version_id: 'v4' }))
})

it('lets a failed draft save keep history readable while disabling restore without a POST', async () => {
  vi.mocked(api.writeDocument).mockRejectedValue(new Error('offline'))
  open()
  const editor = await screen.findByRole('textbox', { name: '设定正文' })
  fireEvent.input(editor, { target: { textContent: '这段未同步的正文不能丢失' } })
  fireEvent.click(screen.getByRole('button', { name: '存档当前内容' }))
  expect(await screen.findByText(/当前草稿未能保存，已暂停存档/, { selector: '[role="alert"]' })).toBeInTheDocument()
  const history = await historyAtFirstVersion()
  expect(within(history).getByRole('button', { name: '恢复此存档' })).toBeDisabled()
  expect(within(history).queryByRole('button', { name: '存档当前内容' })).not.toBeInTheDocument()
  expect(history).toHaveTextContent('当前草稿未能保存')
  expect(api.restoreSettingDocumentRestorePoint).not.toHaveBeenCalled()
  expect(api.createSettingDocumentRestorePoint).not.toHaveBeenCalled()
  fireEvent.click(within(history).getByRole('button', { name: '关闭历史存档' }))
  await waitFor(() => expect(history).not.toBeInTheDocument())
  expect(editor).toHaveTextContent('这段未同步的正文不能丢失')
})

it('halts archival when a draft cannot save and leaves the collection editable', async () => {
  vi.mocked(api.writeDocument).mockRejectedValue(new Error('offline'))
  open()
  const editor = await screen.findByRole('textbox', { name: '设定正文' })
  fireEvent.input(editor, { target: { textContent: '归档前必须保存的草稿' } })
  const options = await archiveConfirmation()
  fireEvent.click(within(options).getByRole('button', { name: '确认归档' }))
  expect(await within(options).findByRole('alert')).toHaveTextContent('已暂停归档')
  expect(api.archiveSettingCollection).not.toHaveBeenCalled()
  expect(editor).toHaveTextContent('归档前必须保存的草稿')
  expect(editor).toHaveAttribute('contenteditable', 'true')
})

it.each(['current-version', 'version-list'])('recovers a lost restore response through %s without a second restore POST', async mode => {
  const restored = { ...version('v4', 4), parent_version_id: 'v3', restored_from_version_id: 'v1', restored_from_restore_point_id: 'point-1' }
  vi.mocked(api.restoreSettingDocumentRestorePoint).mockRejectedValue(new Error('response lost'))
  vi.mocked(api.getDocument).mockResolvedValue({ ...document, current_version_id: restored.id, current_version: mode === 'current-version' ? restored : null })
  if (mode === 'version-list') vi.mocked(api.listDocumentVersions).mockResolvedValue([restored, version('v1', 1), version('v3', 3)])
  open()
  await screen.findByRole('textbox', { name: '设定正文' })
  const history = await historyAtFirstVersion()
  await confirmRestore(history)
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '历史存档' })).not.toBeInTheDocument())
  expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveTextContent('最早的正文')
  expect(api.restoreSettingDocumentRestorePoint).toHaveBeenCalledTimes(1)
  expect(api.getDocument).toHaveBeenCalledWith('note')
})

it.each([
  { parent_version_id: 'other-base', restored_from_version_id: 'v1', restored_from_restore_point_id: 'point-1' },
  { parent_version_id: 'v3', restored_from_version_id: 'other-target' },
  { parent_version_id: 'v3', restored_from_version_id: 'v1', restored_from_restore_point_id: 'other-point' },
  { parent_version_id: 'v3', restored_from_version_id: 'v1', restored_from_restore_point_id: null },
])('does not adopt an unrelated latest version after losing a restore response: %j', async identity => {
  vi.mocked(api.restoreSettingDocumentRestorePoint).mockRejectedValue(new Error('response lost'))
  const unrelated = { ...version('v4', 4), restored_from_restore_point_id: 'point-1', ...identity }
  vi.mocked(api.getDocument).mockResolvedValue({ ...document, current_version_id: unrelated.id, current_version: unrelated })
  open()
  await screen.findByRole('textbox', { name: '设定正文' })
  const history = await historyAtFirstVersion()
  await confirmRestore(history)
  expect(await within(history).findByRole('alert')).toHaveTextContent('恢复结果未能确认')
  expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveTextContent('现在的正文')
  expect(api.restoreSettingDocumentRestorePoint).toHaveBeenCalledTimes(1)
})

it('recovers a lost archive response by checking durable archived status without repeating the POST', async () => {
  vi.mocked(api.archiveSettingCollection).mockRejectedValue(new Error('response lost'))
  vi.mocked(api.getSettingCollection).mockResolvedValueOnce(collection).mockResolvedValue({ ...collection, status: 'archived', revision: 4 })
  open()
  await screen.findByRole('textbox', { name: '设定正文' })
  const options = await archiveConfirmation()
  fireEvent.click(within(options).getByRole('button', { name: '确认归档' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '归档设定集' })).not.toBeInTheDocument())
  expect(screen.getByRole('button', { name: '新建内容' })).toBeDisabled()
  expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveAttribute('contenteditable', 'false')
  expect(api.archiveSettingCollection).toHaveBeenCalledTimes(1)
})

it('keeps autosaves out of user history and creates one explicit point over the latest flushed version', async () => {
  const points: api.SettingDocumentRestorePoint[] = []
  vi.mocked(api.listSettingDocumentRestorePoints).mockImplementation(async () => points)
  vi.mocked(api.createSettingDocumentRestorePoint).mockImplementation(async (_id, request) => {
    const saved = { ...point(request.request_id, 1, version(request.expected_current_version_id, 4)), matches_current: true }
    points.push(saved); return saved
  })
  const view = open()
  const editor = await screen.findByRole('textbox', { name: '设定正文' })
  fireEvent.input(editor, { target: { textContent: '自动同步的最新正文' } })
  await waitFor(() => expect(api.writeDocument).toHaveBeenCalled())
  expect(api.createSettingDocumentRestorePoint).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '历史存档' }))
  const history = await screen.findByRole('dialog', { name: '历史存档' })
  expect(within(history).queryByRole('button', { name: /^存档 \d+$/ })).not.toBeInTheDocument()
  expect(within(history).queryByRole('button', { name: '存档当前内容' })).not.toBeInTheDocument()
  expect(api.listDocumentVersions).not.toHaveBeenCalled()
  fireEvent.click(within(history).getByRole('button', { name: '关闭历史存档' }))
  await waitFor(() => expect(history).not.toBeInTheDocument())
  await saveCurrent()
  expect(screen.queryByRole('dialog', { name: '历史存档' })).not.toBeInTheDocument()
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledWith('note', { request_id: expect.any(String), expected_current_version_id: 'v4' })
  expect(points).toHaveLength(1)
  expect(api.writeDocument).toHaveBeenCalledTimes(1)
  await saveCurrent()
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledTimes(1)
  view.unmount()
  vi.mocked(api.listCollectionDocuments).mockResolvedValue([{ ...document, current_version_id: 'v4' }])
  vi.mocked(api.readDocumentContent).mockResolvedValue({ document_id: 'note', version_id: 'v4', content: '自动同步的最新正文' })
  open()
  await screen.findByRole('textbox', { name: '设定正文' })
  fireEvent.click(screen.getByRole('button', { name: '历史存档' }))
  await screen.findByRole('button', { name: '存档 1' })
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledTimes(1)
})

it('guards repeated manual clicks while a single point creation is pending', async () => {
  const points: api.SettingDocumentRestorePoint[] = []
  vi.mocked(api.listSettingDocumentRestorePoints).mockImplementation(async () => points)
  let finish!: (saved: api.SettingDocumentRestorePoint) => void
  vi.mocked(api.createSettingDocumentRestorePoint).mockImplementation(() => new Promise(resolve => { finish = resolve }))
  open()
  await screen.findByRole('textbox', { name: '设定正文' })
  const create = screen.getByRole('button', { name: '存档当前内容' })
  fireEvent.click(create); fireEvent.click(create)
  await waitFor(() => expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledTimes(1))
  expect(create).toBeDisabled()
  const request = vi.mocked(api.createSettingDocumentRestorePoint).mock.calls[0][1]
  const saved = point(request.request_id, 1, version('v3', 3))
  points.push(saved)
  await act(async () => finish(saved))
  await waitFor(() => expect(screen.getByRole('button', { name: '存档当前内容' })).toHaveAttribute('data-state', 'success'))
  fireEvent.click(screen.getByRole('button', { name: '历史存档' }))
  const history = await screen.findByRole('dialog', { name: '历史存档' })
  await within(history).findByRole('button', { name: '存档 1' })
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledTimes(1)
})

it('does not carry archive success feedback from the saved entry to another entry', async () => {
  const second = { ...document, id: 'second-note', title: '潮汐法则', path: 'setting/tides.md', current_version_id: 'second-v1' }
  vi.mocked(api.listCollectionDocuments).mockResolvedValue([document, second])
  vi.mocked(api.readDocumentContent).mockImplementation(async id => ({ document_id: id, version_id: id === 'note' ? 'v3' : 'second-v1', content: id === 'note' ? '现在的正文' : '另一篇尚未存档的正文' }))
  vi.mocked(api.listSettingDocumentRestorePoints).mockResolvedValue([])
  vi.mocked(api.createSettingDocumentRestorePoint).mockImplementation(async (_id, request) => point(request.request_id, 1, version(request.expected_current_version_id, 3)))
  open()
  await screen.findByRole('textbox', { name: '设定正文' })
  await saveCurrent()
  expect(screen.getByRole('button', { name: '存档当前内容' })).toHaveAttribute('data-state', 'success')
  fireEvent.click(screen.getByRole('button', { name: '潮汐法则' }))
  expect(screen.getByRole('textbox', { name: '设定标题' })).toHaveValue('潮汐法则')
  expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveTextContent('另一篇尚未存档的正文')
  expect(screen.queryByText('已存档', { selector: '[role="status"]' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '存档当前内容' })).not.toHaveAttribute('data-state', 'success')
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledTimes(1)
})

it('clears archive success feedback immediately when the saved entry is edited again', async () => {
  vi.mocked(api.listSettingDocumentRestorePoints).mockResolvedValue([])
  vi.mocked(api.createSettingDocumentRestorePoint).mockImplementation(async (_id, request) => point(request.request_id, 1, version(request.expected_current_version_id, 3)))
  open()
  const body = await screen.findByRole('textbox', { name: '设定正文' })
  await saveCurrent()
  expect(screen.getByRole('button', { name: '存档当前内容' })).toHaveAttribute('data-state', 'success')
  fireEvent.input(body, { target: { textContent: '刚写的新内容，还没有再次存档' } })
  expect(body).toHaveTextContent('刚写的新内容，还没有再次存档')
  expect(api.writeDocument).not.toHaveBeenCalled()
  expect(screen.queryByText('已存档', { selector: '[role="status"]' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '存档当前内容' })).not.toHaveAttribute('data-state', 'success')
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledTimes(1)
})

it('does not mark the local snapshot archived when the server current point belongs to another version', async () => {
  const externalPoint = { ...point('another-window', 2, version('v4', 4)), matches_current: true }
  vi.mocked(api.listSettingDocumentRestorePoints).mockResolvedValue([externalPoint])
  vi.mocked(api.createSettingDocumentRestorePoint).mockRejectedValue(new api.ApiError(409, 'conflict', 'Current version changed'))
  open()
  const editor = await screen.findByRole('textbox', { name: '设定正文' })
  fireEvent.click(screen.getByRole('button', { name: '存档当前内容' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('当前内容已更新，存档结果未能确认')
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledWith('note', {
    request_id: expect.any(String), expected_current_version_id: 'v3',
  })
  expect(screen.getByRole('button', { name: '存档当前内容' })).not.toHaveAttribute('data-state', 'success')
  expect(editor).toHaveTextContent('现在的正文')
})

it('does not mark the local snapshot archived when replaying a different pending point still marked current', async () => {
  const pending = { request_id: 'pending-v4', expected_current_version_id: 'v4' }
  sessionStorage.setItem('guranovel:setting-restore-point-pending:note', JSON.stringify(pending))
  const previousPoint = { ...point(pending.request_id, 2, version('v4', 4)), matches_current: true }
  vi.mocked(api.listSettingDocumentRestorePoints).mockRejectedValueOnce(new Error('list temporarily unavailable'))
  vi.mocked(api.createSettingDocumentRestorePoint).mockResolvedValue(previousPoint)
  open()
  const editor = await screen.findByRole('textbox', { name: '设定正文' })
  fireEvent.click(screen.getByRole('button', { name: '存档当前内容' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('上次存档已核实，当前内容尚未存档')
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledWith('note', pending)
  expect(screen.getByRole('button', { name: '存档当前内容' })).not.toHaveAttribute('data-state', 'success')
  expect(sessionStorage.getItem('guranovel:setting-restore-point-pending:note')).toBeNull()
  expect(editor).toHaveTextContent('现在的正文')
})

it('reconciles a lost manual-create response by exact request identity without a second POST', async () => {
  const points: api.SettingDocumentRestorePoint[] = []
  vi.mocked(api.listSettingDocumentRestorePoints).mockImplementation(async () => points)
  vi.mocked(api.createSettingDocumentRestorePoint).mockImplementation(async (_id, request) => {
    points.push(point(request.request_id, 1, version(request.expected_current_version_id, 3)))
    throw new Error('response lost')
  })
  open()
  await screen.findByRole('textbox', { name: '设定正文' })
  await saveCurrent()
  fireEvent.click(screen.getByRole('button', { name: '历史存档' }))
  const history = await screen.findByRole('dialog', { name: '历史存档' })
  await within(history).findByRole('button', { name: '存档 1' })
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledTimes(1)
  expect(points).toHaveLength(1)
  expect(sessionStorage.getItem('guranovel:setting-restore-point-pending:note')).toBeNull()
})

it('retains the same manual-create request over reload and retries instead of manufacturing a second point', async () => {
  const points: api.SettingDocumentRestorePoint[] = []
  vi.mocked(api.listSettingDocumentRestorePoints).mockImplementation(async () => points)
  vi.mocked(api.createSettingDocumentRestorePoint).mockRejectedValueOnce(new Error('offline')).mockImplementation(async (_id, request) => {
    const saved = point(request.request_id, 1, version(request.expected_current_version_id, 3))
    points.push(saved); return saved
  })
  const view = open()
  await screen.findByRole('textbox', { name: '设定正文' })
  fireEvent.click(screen.getByRole('button', { name: '存档当前内容' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('存档结果未能确认')
  const request = vi.mocked(api.createSettingDocumentRestorePoint).mock.calls[0][1]
  expect(JSON.parse(sessionStorage.getItem('guranovel:setting-restore-point-pending:note')!)).toEqual(request)
  view.unmount(); open()
  await screen.findByRole('textbox', { name: '设定正文' })
  await saveCurrent()
  fireEvent.click(screen.getByRole('button', { name: '历史存档' }))
  const history = await screen.findByRole('dialog', { name: '历史存档' })
  await within(history).findByRole('button', { name: '存档 1' })
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledTimes(2)
  expect(vi.mocked(api.createSettingDocumentRestorePoint).mock.calls[1][1]).toEqual(request)
  expect(sessionStorage.getItem('guranovel:setting-restore-point-pending:note')).toBeNull()
})

it('does not confuse an unrelated point over identical current content with a lost manual-create request', async () => {
  vi.mocked(api.listSettingDocumentRestorePoints).mockResolvedValueOnce([]).mockResolvedValue([point('unrelated-request', 1, version('v3', 3))])
  vi.mocked(api.createSettingDocumentRestorePoint).mockRejectedValue(new Error('response lost'))
  open()
  await screen.findByRole('textbox', { name: '设定正文' })
  fireEvent.click(screen.getByRole('button', { name: '存档当前内容' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('存档结果未能确认')
  expect(screen.queryByText('已存档', { selector: '[role="status"]' })).not.toBeInTheDocument()
  const request = vi.mocked(api.createSettingDocumentRestorePoint).mock.calls[0][1]
  expect(JSON.parse(sessionStorage.getItem('guranovel:setting-restore-point-pending:note')!)).toEqual(request)
})

it('resolves an old committed request after reload and archives newer saved writing on the same explicit click', async () => {
  const points: api.SettingDocumentRestorePoint[] = []
  let pointReadsFail = false
  vi.mocked(api.listSettingDocumentRestorePoints).mockImplementation(async () => {
    if (pointReadsFail) throw new Error('cannot verify committed point yet')
    return points
  })
  vi.mocked(api.createSettingDocumentRestorePoint).mockImplementationOnce(async (_id, request) => {
    points.push(point(request.request_id, 1, version(request.expected_current_version_id, 3)))
    pointReadsFail = true
    throw new Error('committed, response lost')
  }).mockImplementation(async (_id, request) => {
    const saved = { ...point(request.request_id, 2, version(request.expected_current_version_id, 4)), matches_current: true }
    points.push(saved); return saved
  })
  const view = open()
  await screen.findByRole('textbox', { name: '设定正文' })
  fireEvent.click(screen.getByRole('button', { name: '存档当前内容' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('存档结果未能确认')
  const oldRequest = vi.mocked(api.createSettingDocumentRestorePoint).mock.calls[0][1]
  view.unmount()
  pointReadsFail = false; points[0].matches_current = false
  vi.mocked(api.listCollectionDocuments).mockResolvedValue([{ ...document, current_version_id: 'v4' }])
  vi.mocked(api.readDocumentContent).mockResolvedValue({ document_id: 'note', version_id: 'v4', content: '重载后继续写过的新正文' })
  open()
  const editor = await screen.findByRole('textbox', { name: '设定正文' })
  expect(editor).toHaveTextContent('重载后继续写过的新正文')
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledTimes(1)
  await saveCurrent()
  fireEvent.click(screen.getByRole('button', { name: '历史存档' }))
  const history = await screen.findByRole('dialog', { name: '历史存档' })
  await within(history).findByRole('button', { name: '存档 2' })
  expect(api.createSettingDocumentRestorePoint).toHaveBeenCalledTimes(2)
  const newRequest = vi.mocked(api.createSettingDocumentRestorePoint).mock.calls[1][1]
  expect(newRequest.expected_current_version_id).toBe('v4')
  expect(newRequest.request_id).not.toBe(oldRequest.request_id)
  expect(points.map(item => item.version_id)).toEqual(['v3', 'v4'])
  expect(editor).toHaveTextContent('重载后继续写过的新正文')
  expect(sessionStorage.getItem('guranovel:setting-restore-point-pending:note')).toBeNull()
})

it('does not report newer writing as archived when an unavailable list forces replay of an older pending request', async () => {
  const pending = { request_id: 'pending-v1', expected_current_version_id: 'v1' }
  sessionStorage.setItem('guranovel:setting-restore-point-pending:note', JSON.stringify(pending))
  const old = point(pending.request_id, 1, version('v1', 1))
  const points = [old]
  vi.mocked(api.listSettingDocumentRestorePoints).mockRejectedValueOnce(new Error('offline')).mockImplementation(async () => points)
  vi.mocked(api.createSettingDocumentRestorePoint).mockResolvedValueOnce(old).mockImplementation(async (_id, request) => {
    const saved = point(request.request_id, 2, version(request.expected_current_version_id, 3))
    points.push(saved); return saved
  })
  open()
  const editor = await screen.findByRole('textbox', { name: '设定正文' })
  fireEvent.click(screen.getByRole('button', { name: '存档当前内容' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('上次存档已核实，当前内容尚未存档')
  expect(api.createSettingDocumentRestorePoint).toHaveBeenNthCalledWith(1, 'note', pending)
  expect(screen.queryByText('已存档', { selector: '[role="status"]' })).not.toBeInTheDocument()
  expect(sessionStorage.getItem('guranovel:setting-restore-point-pending:note')).toBeNull()
  expect(editor).toHaveTextContent('现在的正文')
  await saveCurrent()
  expect(api.createSettingDocumentRestorePoint).toHaveBeenNthCalledWith(2, 'note', {
    request_id: expect.not.stringMatching(/^pending-v1$/), expected_current_version_id: 'v3',
  })
  expect(points.map(item => item.version_id)).toEqual(['v1', 'v3'])
})
