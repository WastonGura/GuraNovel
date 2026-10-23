import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import SettingCollectionWorkspace from './SettingCollectionWorkspace'
import * as client from './api/client'

vi.mock('./api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof client>()
  return {
    ...actual,
    getSettingCollection: vi.fn(),
    listCollectionProjects: vi.fn(),
    listCollectionDocuments: vi.fn(),
    readDocumentContent: vi.fn(),
    writeDocument: vi.fn(),
    patchDocument: vi.fn(),
    createCollectionDocument: vi.fn(),
    deleteDocument: vi.fn(),
    getDocument: vi.fn(),
    applySettingProposal: vi.fn(),
  }
})

const mockedGetSettingCollection = vi.mocked(client.getSettingCollection)
const mockedListCollectionProjects = vi.mocked(client.listCollectionProjects)
const mockedListCollectionDocuments = vi.mocked(client.listCollectionDocuments)
const mockedReadDocumentContent = vi.mocked(client.readDocumentContent)
const mockedWriteDocument = vi.mocked(client.writeDocument)
const mockedCreateCollectionDocument = vi.mocked(client.createCollectionDocument)
const mockedGetDocument = vi.mocked(client.getDocument)
const mockedApplySettingProposal = vi.mocked(client.applySettingProposal)

const mockCollection: client.SettingCollection = {
  id: 'col-123',
  owner_id: 'user-1',
  slug: 'test-collection',
  title: '天穹纪元设定集',
  description: '核心世界观与角色设定',
  status: 'active',
  workspace_root: '/workspace/col-123',
  revision: 3,
  metadata: {},
  created_at: '2026-07-19T00:00:00Z',
  updated_at: '2026-07-19T00:00:00Z',
}

const mockProjects: client.Project[] = [
  {
    id: 'proj-1',
    slug: 'novel-one',
    title: '天穹之剑',
    genre: '玄幻',
    target_platform: '起点',
    status: 'active',
    workspace_root: '/workspace/proj-1',
    metadata: {},
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
  },
  {
    id: 'proj-2',
    slug: 'novel-two',
    title: '星穹旅人',
    genre: '科幻',
    target_platform: '纵横',
    status: 'active',
    workspace_root: '/workspace/proj-2',
    metadata: {},
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
  },
]

const mockDocs: client.Document[] = [
  {
    id: 'doc-char-1',
    project_id: null,
    setting_collection_id: 'col-123',
    chapter_id: null,
    type: 'character_profile',
    title: '陆云舟',
    path: 'setting/luyunzhou.md',
    current_version_id: 'ver-101',
    current_version: null,
    metadata: { category: 'setting' },
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
  },
  {
    id: 'doc-world-1',
    project_id: null,
    setting_collection_id: 'col-123',
    chapter_id: null,
    type: 'world_overview',
    title: '天穹神域',
    path: 'world/tianqiong.md',
    current_version_id: 'ver-201',
    current_version: null,
    metadata: { category: 'world' },
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
  },
]

function documentVersion(overrides: Partial<client.DocumentVersion> = {}): client.DocumentVersion {
  return {
    id: 'ver-auto-1',
    document_id: 'doc-char-1',
    version_number: 2,
    parent_version_id: null,
    source: 'user',
    actor_user_id: null,
    agent_role: null,
    workflow_run_id: null,
    content_hash: 'hash',
    byte_size: 12,
    word_count: 2,
    file_path: 'setting/note.md',
    change_summary: null,
    created_at: '2026-07-19T00:00:00Z',
    ...overrides,
  }
}

function renderWorkspace(initialUrl = '/setting-collections/col-123') {
  return render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <Routes>
        <Route path="/" element={<p data-testid="home-page">首页</p>} />
        <Route
          path="/setting-collections/:settingCollectionId"
          element={<SettingCollectionWorkspace />}
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('SettingCollectionWorkspace', () => {
  beforeEach(() => {
    sessionStorage.clear()
    vi.clearAllMocks()
    mockedGetSettingCollection.mockResolvedValue(mockCollection)
    mockedListCollectionProjects.mockResolvedValue(mockProjects)
    mockedListCollectionDocuments.mockResolvedValue(mockDocs)
    mockedReadDocumentContent.mockImplementation(async (id) => {
      if (id === 'doc-char-1') return { document_id: id, version_id: 'ver-101', content: '青云宗第一剑修。' }
      if (id === 'doc-world-1') return { document_id: id, version_id: 'ver-201', content: '九重天界的总称。' }
      return { document_id: id, version_id: 'ver-0', content: '' }
    })
    mockedWriteDocument.mockResolvedValue(documentVersion())
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('shows useful collection context without revision or routine saved badges', async () => {
    renderWorkspace()

    expect(screen.getByText('正在加载设定集…')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })

    expect(screen.queryByText('r3')).not.toBeInTheDocument()
    expect(screen.getByText('关联小说 (2)')).toBeInTheDocument()
    expect(screen.queryByText('修改会影响关联作品的后续创作')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '回到书架' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('button', { name: /^Gura$/ })).toBeInTheDocument()
    expect(screen.queryByText('已保存')).not.toBeInTheDocument()
  })

  it('renders documents as setting notes in the library sidebar', async () => {
    renderWorkspace()

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })

    const list = screen.getByLabelText('设定条目', { exact: true })
    expect(within(list).getByRole('button', { name: '陆云舟' })).toBeInTheDocument()
  })

  it('uses only collection context and the selected saved document version for Gura', async () => {
    const conversation = { id: 'conv-world', project_id: null, setting_collection_id: null, messages: [], created_at: '', updated_at: '' }
    const calls: { url: string; body: Record<string, unknown> }[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      const body = JSON.parse(String(init?.body))
      calls.push({ url: String(url), body })
      return new Response(JSON.stringify(String(url).endsWith('/messages') ? { ...conversation, messages: [{ id: 'user', role: 'user', content: body.content,
        tool_calls: [{ client_message_id: body.client_message_id, context: body.context, exchange_complete: true }] }] } : conversation), { status: 200 })
    })
    renderWorkspace('/setting-collections/col-123?note=doc-world-1&category=world')
    await screen.findByRole('heading', { name: '天穹神域' })
    fireEvent.click(screen.getByRole('button', { name: /^Gura$/ }))
    await waitFor(() => expect(calls).toHaveLength(1))
    fireEvent.change(screen.getByRole('textbox', { name: '给 Gura 的消息' }), { target: { value: '帮我理解这个世界' } })
    await waitFor(() => expect(screen.getByRole('button', { name: '发送给 Gura' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '发送给 Gura' }))
    await waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[0].url).toBe('/api/v1/assistant/conversations')
    expect(calls[1].body).toMatchObject({ context: { kind: 'collection', collection_id: 'col-123', document_id: 'doc-world-1', document_version_id: 'ver-201' }, content: '帮我理解这个世界' })
    expect(calls.every(call => !call.url.includes('/projects/'))).toBe(true)
    expect(screen.queryByText('交互预览')).not.toBeInTheDocument()
  })

  it('does not leave for home when a title update is rejected', async () => {
    vi.mocked(client.patchDocument).mockRejectedValueOnce(new Error('offline'))
    renderWorkspace()
    await screen.findByText('天穹纪元设定集')
    fireEvent.change(screen.getByRole('textbox', { name: '设定标题' }), { target: { value: '不能丢失的标题' } })
    await act(async () => { fireEvent.click(screen.getByRole('link', { name: '回到书架' })) })
    expect(screen.queryByTestId('home-page')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '设定标题' })).toHaveValue('不能丢失的标题')
  })

  it('displays read-only badge and restricts editing when collection is archived', async () => {
    mockedGetSettingCollection.mockResolvedValue({
      ...mockCollection,
      status: 'archived',
    })

    renderWorkspace()

    await waitFor(() => {
      expect(screen.getAllByText('已归档 · 只读').length).toBeGreaterThan(0)
    })

    const manualAddBtn = screen.getByRole('button', { name: '新建内容' })
    expect(manualAddBtn).toBeDisabled()
  })

  it('retains local draft and renders conflict banner when saving encounters 409 Conflict', async () => {
    renderWorkspace()

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })

    // Simulate 409 conflict upon saving content
    mockedWriteDocument.mockRejectedValueOnce(
      new client.ApiError(409, 'conflict', 'The document has a newer version.')
    )

    const editor = screen.getByLabelText('设定正文')
    fireEvent.input(editor, { target: { textContent: '本地未提交修改内容' } })

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })

    expect(screen.getByText('版本冲突')).toBeInTheDocument()
    expect(screen.queryByText(/409 Conflict/)).not.toBeInTheDocument()
    expect(screen.getByText(/拉取最新版本覆盖草稿/)).toBeInTheDocument()
    expect(screen.getByText(/以本地草稿覆盖服务端/)).toBeInTheDocument()
    expect(screen.getByText(/保留本地草稿继续编辑/)).toBeInTheDocument()
  })

  it('allows reloading latest server content to resolve conflict', async () => {
    renderWorkspace()

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })


    mockedWriteDocument.mockRejectedValueOnce(
      new client.ApiError(409, 'conflict', 'The document has a newer version.')
    )

    const editor = screen.getByLabelText('设定正文')
    fireEvent.input(editor, { target: { textContent: '冲突草稿' } })

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })

    // Setup fresh server version
    mockedGetDocument.mockResolvedValueOnce({
      ...mockDocs[0],
      current_version_id: 'ver-102',
    })
    mockedReadDocumentContent.mockResolvedValueOnce({
      document_id: 'doc-char-1',
      version_id: 'ver-102',
      content: '服务端更新后的最新内容。',
    })

    const reloadBtn = screen.getByRole('button', { name: '拉取最新版本覆盖草稿' })
    await act(async () => {
      fireEvent.click(reloadBtn)
    })

    await waitFor(() => {
      expect(screen.queryByText(/版本冲突/)).not.toBeInTheDocument()
    })

    expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveTextContent('服务端更新后的最新内容。')
    expect(sessionStorage.getItem('guranovel:setting-draft-recovery:doc-char-1')).toBeNull()
    expect(screen.queryByText('已保存')).not.toBeInTheDocument()
  })

  it('creates new setting document in the collection', async () => {
    mockedCreateCollectionDocument.mockResolvedValueOnce({
      id: 'doc-new-1',
      project_id: null,
      setting_collection_id: 'col-123',
      chapter_id: null,
      type: 'character_profile',
      title: '叶清寒',
      path: 'setting/yeqinghan.md',
      current_version_id: 'ver-new-1',
      current_version: null,
      metadata: { category: 'setting' },
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    })

    renderWorkspace()

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })

    const manualAddBtn = screen.getByRole('button', { name: '新建内容' })
    await act(async () => {
      fireEvent.click(manualAddBtn)
    })
    expect(mockedCreateCollectionDocument).not.toHaveBeenCalled()
    fireEvent.change(screen.getByRole('textbox', { name: '设定标题' }), { target: { value: '叶清寒' } })
    await waitFor(() => expect(mockedCreateCollectionDocument).toHaveBeenCalled())

    expect(mockedCreateCollectionDocument).toHaveBeenCalledWith(
      'col-123',
      expect.objectContaining({
        type: 'character_profile',
        client_request_id: expect.any(String),
        metadata: { category: 'setting', comments: [] },
      })
    )
  })

  it.each(['reload', 'overwrite'] as const)('resolves a newly created entry conflict with its stable local identity using %s', async action => {
    const created = { ...mockDocs[0], id: 'doc-new-stable', title: '新角色', current_version_id: 'ver-created' }
    mockedListCollectionDocuments.mockResolvedValue([])
    mockedCreateCollectionDocument.mockResolvedValue(created)
    renderWorkspace()
    await screen.findByText('天穹纪元设定集')
    fireEvent.click(screen.getByRole('button', { name: '新建内容' }))
    fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: created.title } })
    fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '创建时的原文' } })
    await waitFor(() => expect(mockedCreateCollectionDocument).toHaveBeenCalledTimes(1))
    await act(async () => { await Promise.resolve() })
    mockedWriteDocument.mockRejectedValueOnce(new client.ApiError(409, 'conflict', 'newer version'))
    fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '冲突时的本地草稿' } })
    await screen.findByText('版本冲突')
    mockedGetDocument.mockResolvedValue({ ...created, current_version_id: 'ver-remote' })
    mockedReadDocumentContent.mockResolvedValue({ document_id: created.id, version_id: 'ver-remote', content: '远端最新正文' })
    const recoveredVersion = action === 'reload' ? 'ver-remote' : 'ver-overwritten'
    if (action === 'overwrite') mockedWriteDocument.mockResolvedValueOnce(documentVersion({ id: recoveredVersion, document_id: created.id }))
    fireEvent.click(screen.getByRole('button', { name: action === 'reload' ? '拉取最新版本覆盖草稿' : '以本地草稿覆盖服务端' }))
    await waitFor(() => expect(screen.queryByText('版本冲突')).not.toBeInTheDocument())
    expect(screen.getByLabelText('设定正文')).toHaveTextContent(action === 'reload' ? '远端最新正文' : '冲突时的本地草稿')
    fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '解决冲突后继续写' } })
    await waitFor(() => expect(mockedWriteDocument).toHaveBeenLastCalledWith(created.id, expect.objectContaining({
      content: '解决冲突后继续写', expected_current_version_id: recoveredVersion,
    })))
  })

  it('overwrites with the latest local draft and preserves further writing while the overwrite response is pending', async () => {
    renderWorkspace()
    await screen.findByText('天穹纪元设定集')
    mockedWriteDocument.mockRejectedValueOnce(new client.ApiError(409, 'conflict', 'newer version'))
    fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '第一次保存失败的草稿' } })
    await screen.findByText('版本冲突')
    mockedGetDocument.mockResolvedValue({ ...mockDocs[0], current_version_id: 'ver-remote' })
    let finishOverwrite!: () => void
    mockedWriteDocument.mockImplementationOnce(() => new Promise(resolve => { finishOverwrite = () => resolve(documentVersion({ id: 'ver-overwritten' })) }))
    fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '点击覆盖前继续写的最新草稿' } })
    fireEvent.click(screen.getByRole('button', { name: '以本地草稿覆盖服务端' }))
    await waitFor(() => expect(mockedWriteDocument).toHaveBeenNthCalledWith(2, 'doc-char-1', expect.objectContaining({
      content: '点击覆盖前继续写的最新草稿', expected_current_version_id: 'ver-remote',
    })))
    fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '覆盖请求期间继续写的内容' } })
    await act(async () => { finishOverwrite() })
    expect(screen.getByLabelText('设定正文')).toHaveTextContent('覆盖请求期间继续写的内容')
    await waitFor(() => expect(mockedWriteDocument).toHaveBeenLastCalledWith('doc-char-1', expect.objectContaining({
      content: '覆盖请求期间继续写的内容', expected_current_version_id: 'ver-overwritten',
    })))
  })

  it('ignores an older autosave acknowledgement after an explicit overwrite and a newer save have completed', async () => {
    renderWorkspace()
    await screen.findByText('天穹纪元设定集')
    mockedWriteDocument.mockRejectedValueOnce(new client.ApiError(409, 'conflict', 'newer version'))
    fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '首次冲突草稿' } })
    await screen.findByText('版本冲突')
    let finishLookup!: () => void, finishOldAcknowledgement!: () => void
    mockedGetDocument.mockImplementationOnce(() => new Promise(resolve => {
      finishLookup = () => resolve({ ...mockDocs[0], current_version_id: 'ver-ordinary-committed' })
    }))
    mockedWriteDocument.mockImplementationOnce(() => new Promise(resolve => {
      // The write committed before the overwrite lookup; only its response is delayed.
      finishOldAcknowledgement = () => resolve(documentVersion({ id: 'ver-ordinary-committed' }))
    })).mockResolvedValueOnce(documentVersion({ id: 'ver-overwritten' }))
      .mockResolvedValueOnce(documentVersion({ id: 'ver-newer-saved' }))
    fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '点击覆盖时的草稿' } })
    fireEvent.click(screen.getByRole('button', { name: '以本地草稿覆盖服务端' }))
    fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '覆盖等待期间继续写' } })
    await waitFor(() => expect(mockedWriteDocument).toHaveBeenNthCalledWith(2, 'doc-char-1', expect.objectContaining({ content: '覆盖等待期间继续写' })))
    await act(async () => { finishLookup() })
    await waitFor(() => expect(mockedWriteDocument).toHaveBeenNthCalledWith(4, 'doc-char-1', expect.objectContaining({
      content: '覆盖等待期间继续写', expected_current_version_id: 'ver-overwritten',
    })))
    await act(async () => { finishOldAcknowledgement() })
    expect(screen.getByLabelText('设定正文')).toHaveTextContent('覆盖等待期间继续写')
    fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '迟到响应之后继续写' } })
    await waitFor(() => expect(mockedWriteDocument).toHaveBeenLastCalledWith('doc-char-1', expect.objectContaining({
      content: '迟到响应之后继续写', expected_current_version_id: 'ver-newer-saved',
    })))
  })

  it('supports deep linking via url search params', async () => {
    renderWorkspace('/setting-collections/col-123?note=doc-world-1&category=world&mode=note')

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })

    // When category=world and note=doc-world-1, the world category and note should be selected
    const list = screen.getByLabelText('世界观条目', { exact: true })
    expect(within(list).getByRole('button', { name: '天穹神域' })).toBeInTheDocument()
  })

  it('recovers draft from sessionStorage on initial load', async () => {
    sessionStorage.setItem('guranovel:setting-draft-recovery:doc-char-1', '本地暂存的未提交草稿。')
    renderWorkspace()

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })

    expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveTextContent('本地暂存的未提交草稿。')
    sessionStorage.removeItem('guranovel:setting-draft-recovery:doc-char-1')
  })

  it('renders error page when reading document content fails and allows retry', async () => {
    mockedReadDocumentContent.mockRejectedValueOnce(new Error('Network error'))

    renderWorkspace()

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })

    expect(screen.getByText('设定集加载失败，请刷新后重试。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试加载' })).toBeInTheDocument()

    // Retry loading
    mockedReadDocumentContent.mockResolvedValueOnce({
      document_id: 'doc-char-1',
      version_id: 'ver-101',
      content: '重试成功加载的内容。',
    })

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '重试加载' }))
    })

    await waitFor(() => {
      expect(screen.queryByText('设定集加载失败，请刷新后重试。')).not.toBeInTheDocument()
    })
    expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
  })

  it('does not manufacture example proposals in the formal workspace', async () => {
    mockedApplySettingProposal.mockResolvedValueOnce({
      id: 'doc-char-1',
      project_id: null,
      setting_collection_id: 'col-123',
      chapter_id: null,
      type: 'character_profile',
      title: '陆云舟',
      path: 'setting/luyunzhou.md',
      current_version_id: 'ver-applied-1',
      current_version: null,
      metadata: { category: 'setting' },
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    })

    renderWorkspace('/setting-collections/col-123?mode=chat')

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })

    expect(screen.queryByRole('button', { name: '示例回复' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '接受此条' })).not.toBeInTheDocument()
    expect(mockedApplySettingProposal).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox', { name: '设定正文' })).toBeEnabled()
    expect(screen.queryByPlaceholderText('设定对话暂不可用')).not.toBeVisible()
  })
})
