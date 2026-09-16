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
  }
})

const mockedGetSettingCollection = vi.mocked(client.getSettingCollection)
const mockedListCollectionProjects = vi.mocked(client.listCollectionProjects)
const mockedListCollectionDocuments = vi.mocked(client.listCollectionDocuments)
const mockedReadDocumentContent = vi.mocked(client.readDocumentContent)
const mockedWriteDocument = vi.mocked(client.writeDocument)
const mockedCreateCollectionDocument = vi.mocked(client.createCollectionDocument)
const mockedGetDocument = vi.mocked(client.getDocument)

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
  })

  it('renders collection title, revision, referencing novel count, and impact warning', async () => {
    renderWorkspace()

    expect(screen.getByText('正在加载设定集…')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })

    expect(screen.getByText('r3')).toBeInTheDocument()
    expect(screen.getByText('关联小说 (2)')).toBeInTheDocument()
    expect(screen.getByText('⚠️ 修改将影响关联小说后续启动的新任务')).toBeInTheDocument()
    expect(screen.getAllByText('已保存').length).toBeGreaterThan(0)
  })

  it('renders documents as setting notes in the library sidebar', async () => {
    renderWorkspace()

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })

    const list = screen.getByLabelText('设定条目', { exact: true })
    expect(within(list).getByRole('button', { name: '陆云舟' })).toBeInTheDocument()
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

    const manualAddBtn = screen.getByRole('button', { name: '手动新建条目' })
    expect(manualAddBtn).toBeDisabled()
  })

  it('retains local draft and renders conflict banner when saving encounters 409 Conflict', async () => {
    renderWorkspace()

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })

    // Click edit button
    const editButton = screen.getByRole('button', { name: '编辑' })
    fireEvent.click(editButton)

    // Simulate 409 conflict upon saving content
    mockedWriteDocument.mockRejectedValueOnce(
      new client.ApiError(409, 'conflict', 'The document has a newer version.')
    )

    const textarea = screen.getByLabelText('设定正文')
    fireEvent.change(textarea, { target: { value: '本地未提交修改内容' } })

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })

    expect(screen.getByText(/版本冲突（409 Conflict）/)).toBeInTheDocument()
    expect(screen.getByText(/拉取最新版本覆盖草稿/)).toBeInTheDocument()
    expect(screen.getByText(/以本地草稿覆盖服务端/)).toBeInTheDocument()
    expect(screen.getByText(/保留本地草稿继续编辑/)).toBeInTheDocument()
  })

  it('allows reloading latest server content to resolve conflict', async () => {
    renderWorkspace()

    await waitFor(() => {
      expect(screen.getByText('天穹纪元设定集')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))

    mockedWriteDocument.mockRejectedValueOnce(
      new client.ApiError(409, 'conflict', 'The document has a newer version.')
    )

    const textarea = screen.getByLabelText('设定正文')
    fireEvent.change(textarea, { target: { value: '冲突草稿' } })

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

    expect(screen.getAllByText('已保存').length).toBeGreaterThan(0)
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

    const manualAddBtn = screen.getByRole('button', { name: '手动新建条目' })
    await act(async () => {
      fireEvent.click(manualAddBtn)
    })

    expect(mockedCreateCollectionDocument).toHaveBeenCalledWith(
      'col-123',
      expect.objectContaining({
        type: 'character_profile',
        metadata: { category: 'setting' },
      })
    )
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
})
