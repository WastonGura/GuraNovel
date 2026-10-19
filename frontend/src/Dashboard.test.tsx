import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Dashboard from './Dashboard'
import type { Chapter, Project, SettingCollection } from './api/client'

vi.mock('./api/client', () => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  updateProject: vi.fn(),
  listChapters: vi.fn(),
  listSettingCollections: vi.fn(),
  createSettingCollection: vi.fn(),
}))

import * as api from './api/client'

const mockedApi = vi.mocked(api)

function LocationDisplay() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}{location.search}</div>
}

function renderDashboard(startPath = '/') {
  return render(
    <MemoryRouter initialEntries={[startPath]}>
      <LocationDisplay />
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/setting-collections/:settingCollectionId" element={<div data-testid="setting-workspace">Setting Workspace</div>} />
        <Route path="/projects/:projectId/studio" element={<div data-testid="studio-workspace">Studio Workspace</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function chooseCollection(dialog: HTMLElement, title: string) {
  fireEvent.click(within(dialog).getByRole('button', { name: '选择设定集' }))
  const picker = screen.getByRole('dialog', { name: '选择设定集' })
  fireEvent.click(within(picker).getByRole('button', { name: title }))
}

function sampleProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 'project-1',
    slug: 'archive-of-ash',
    title: 'Archive of Ash',
    genre: 'Fantasy',
    target_platform: 'Web',
    setting_collection_id: 'col-1',
    status: 'draft',
    workspace_root: '/workspace/archive-of-ash',
    metadata: {
      word_count: 12000,
      introduction: 'A tale of ash and rebirth.',
      labels: ['Fantasy', 'Adventure'],
    },
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
    ...overrides,
  }
}

function sampleCollection(overrides: Partial<SettingCollection> = {}): SettingCollection {
  return {
    id: 'col-1',
    slug: 'ash-world',
    title: 'Ash World Lore',
    description: 'Worldbuilding and characters for Ash universe',
    status: 'active',
    owner_id: 'user-1',
    workspace_root: '/settings/ash-world',
    revision: 3,
    metadata: {},
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
    ...overrides,
  }
}

function sampleChapter(overrides: Partial<Chapter> = {}): Chapter {
  return {
    id: 'chapter-1',
    project_id: 'project-1',
    chapter_number: 1,
    title: 'Beginning of Ash',
    status: 'draft',
    current_outline_document_id: null,
    current_draft_document_id: 'doc-1',
    final_document_id: null,
    summary_document_id: null,
    word_count: 0,
    metadata: {},
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
  sessionStorage.clear(); localStorage.clear()
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new Event('close')) }
})

describe('Dashboard Setting Collections and Novel Integration', () => {
  it('offers an actionable first-novel empty state', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    renderDashboard()
    fireEvent.click(await screen.findByRole('button', { name: '创建第一部作品' }))
    expect(screen.getByRole('dialog', { name: '创建作品' })).toBeInTheDocument()
    expect(screen.queryByText('让故事开始')).not.toBeInTheDocument()
    expect(screen.queryByText('给新故事取个名字，其余的可以慢慢补充。')).not.toBeInTheDocument()
  })

  it('restores the collection trigger focus after native modal cancellation', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    renderDashboard()
    const trigger = await screen.findByRole('button', { name: '新建设定集' })
    trigger.focus(); fireEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: '新建设定集' })
    expect(within(dialog).getByLabelText('标题')).toHaveFocus()
    expect(within(dialog).queryByText('收好一个世界')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('人物、地点与世界规则，放在一起慢慢生长。')).not.toBeInTheDocument()
    fireEvent(dialog, new Event('cancel', { cancelable: true }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '新建设定集' })).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
  })

  it('keeps a pending collection modal open and focuses a recoverable failure', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    let reject!: (cause: Error) => void
    mockedApi.createSettingCollection.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail }))
    renderDashboard()
    fireEvent.click(await screen.findByRole('button', { name: '新建设定集' }))
    const dialog = screen.getByRole('dialog', { name: '新建设定集' })
    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: '雾港设定' } })
    fireEvent.submit(within(dialog).getByRole('form'))
    fireEvent.submit(within(dialog).getByRole('form'))
    fireEvent.keyDown(window, { key: 'Escape' })
    fireEvent(dialog, new Event('cancel', { cancelable: true }))
    expect(dialog).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '关闭新建设定集' })).toBeDisabled()
    expect(mockedApi.createSettingCollection).toHaveBeenCalledTimes(1)
    reject(new Error('network unavailable'))
    const error = await screen.findByRole('alert')
    expect(error).toHaveTextContent('已保留内容')
    expect(error).toHaveFocus()
    expect(within(dialog).getByLabelText('标题')).toHaveValue('雾港设定')
  })

  it('clearly marks global preferences as unavailable', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    renderDashboard()
    expect(await screen.findByRole('button', { name: '全局设置（暂未提供）' })).toBeDisabled()
  })
  it('focuses the title and creates a Chinese-titled novel without a manual slug', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    mockedApi.createProject.mockResolvedValue(sampleProject({ id: 'auto-novel' }))
    renderDashboard()
    await screen.findByText('还没有作品，新建一部开始创作。')
    fireEvent.click(screen.getByRole('button', { name: '新建作品' }))
    const dialog = screen.getByRole('dialog', { name: '创建作品' })
    const title = within(dialog).getByLabelText('标题')
    expect(title).toHaveFocus()
    fireEvent.change(title, { target: { value: '雨停之前' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '创建作品' }))
    await waitFor(() => expect(mockedApi.createProject).toHaveBeenCalledWith(expect.objectContaining({
      title: '雨停之前', slug: expect.stringMatching(/^novel-[a-f0-9-]{36}$/),
    })))
  })

  it('restores focus to the creation trigger when the modal is cancelled', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    renderDashboard()
    await screen.findByText('还没有作品，新建一部开始创作。')
    const trigger = screen.getByRole('button', { name: '新建作品' })
    trigger.focus(); fireEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: '创建作品' })
    fireEvent(dialog, new Event('cancel', { cancelable: true }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '创建作品' })).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
  })

  it('retains a failed form and focuses an actionable conflict message', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    mockedApi.createProject.mockRejectedValueOnce({ status: 409 })
    renderDashboard()
    await screen.findByText('还没有作品，新建一部开始创作。')
    fireEvent.click(screen.getByRole('button', { name: '新建作品' }))
    const dialog = screen.getByRole('dialog', { name: '创建作品' })
    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: '雨停之前' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '创建作品' }))
    const error = await screen.findByRole('alert')
    expect(error).toHaveTextContent('检查书架')
    expect(error).toHaveFocus()
    expect(within(dialog).getByLabelText('标题')).toHaveValue('雨停之前')
  })

  it('uses the same generated identity on creation retry and submits the neutral cover', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    mockedApi.createProject.mockRejectedValue(new Error('offline'))
    renderDashboard()
    fireEvent.click(await screen.findByRole('button', { name: '新建作品' }))
    const dialog = screen.getByRole('dialog', { name: '创建作品' })
    expect(within(dialog).queryByLabelText('Slug')).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: '取消' })).not.toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: 'First' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '创建作品' }))
    await within(dialog).findByRole('alert')
    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: 'New title' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '创建作品' }))
    await waitFor(() => expect(mockedApi.createProject).toHaveBeenCalledTimes(2))
    expect(mockedApi.createProject.mock.calls[0][0].slug).toBe(mockedApi.createProject.mock.calls[1][0].slug)
    expect(mockedApi.createProject.mock.calls[1][0].metadata).toEqual({ cover: 'cover-default' })
  })

  it('submits only once and keeps the pending modal open', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    let reject!: (cause: Error) => void
    mockedApi.createProject.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail }))
    renderDashboard()
    await screen.findByText('还没有作品，新建一部开始创作。')
    fireEvent.click(screen.getByRole('button', { name: '新建作品' }))
    const dialog = screen.getByRole('dialog', { name: '创建作品' })
    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: '雨停之前' } })
    const form = within(dialog).getByRole('form')
    fireEvent.submit(form); fireEvent.submit(form)
    fireEvent(dialog, new Event('cancel', { cancelable: true }))
    expect(dialog).toBeInTheDocument()
    expect(mockedApi.createProject).toHaveBeenCalledTimes(1)
    reject(new Error('network unavailable'))
    expect(await screen.findByRole('alert')).toHaveTextContent('已保留填写内容')
    fireEvent.submit(form)
    await waitFor(() => expect(mockedApi.createProject).toHaveBeenCalledTimes(2))
    const calls = mockedApi.createProject.mock.calls
    expect(calls[0][0].slug).toBe(calls[1][0].slug)
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks(); vi.unstubAllGlobals()
  })

  it('renders setting collections carousel with revision and referencing novel count', async () => {
    const col = sampleCollection({ id: 'col-1', title: 'Great Lore', revision: 2 })
    const proj = sampleProject({ setting_collection_id: 'col-1' })
    mockedApi.listProjects.mockResolvedValue([proj])
    mockedApi.listSettingCollections.mockResolvedValue([col])

    renderDashboard()

    expect(await screen.findByRole('heading', { name: '设定集' })).toBeInTheDocument()
    const card = await screen.findByRole('button', { name: 'Open Great Lore setting collection' })
    expect(card).toBeInTheDocument()
    expect(within(card).queryByText('r2')).not.toBeInTheDocument()
    expect(within(card).getByText('关联小说 (1)')).toBeInTheDocument()

    // Clicking the card navigates to the setting collection workspace
    fireEvent.click(card)
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/setting-collections/col-1')
    })
  })

  it('shows empty state when no setting collections exist', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])

    renderDashboard()

    expect(await screen.findByText('还没有设定集，可以新建或随作品自动创建。')).toBeInTheDocument()
  })

  it('creates a new setting collection from the dashboard and navigates to it', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    mockedApi.createSettingCollection.mockResolvedValue(sampleCollection({ id: 'col-new', title: 'New Realm' }))

    renderDashboard()
    await screen.findByText('还没有设定集，可以新建或随作品自动创建。')

    fireEvent.click(screen.getByRole('button', { name: '新建设定集' }))
    const dialog = screen.getByRole('dialog', { name: '新建设定集' })
    expect(dialog).toBeInTheDocument()

    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: 'New Realm' } })
    fireEvent.change(within(dialog).getByLabelText('设定集标识'), { target: { value: 'new-realm' } })
    fireEvent.change(within(dialog).getByLabelText('说明（可选）'), { target: { value: 'Realm overview' } })

    const submitBtn = within(dialog).getByRole('button', { name: '新建设定集' })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(mockedApi.createSettingCollection).toHaveBeenCalledWith({
        title: 'New Realm',
        slug: 'new-realm',
        description: 'Realm overview',
      })
    })
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/setting-collections/col-new')
    })
  })

  it('prevents duplicate setting collection submission while pending', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    mockedApi.createSettingCollection.mockReturnValue(new Promise(() => undefined))

    renderDashboard()
    await screen.findByText('还没有设定集，可以新建或随作品自动创建。')

    fireEvent.click(screen.getByRole('button', { name: '新建设定集' }))
    const dialog = screen.getByRole('dialog', { name: '新建设定集' })
    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: 'Pending Realm' } })

    const submitBtn = within(dialog).getByRole('button', { name: '新建设定集' })
    fireEvent.click(submitBtn)
    fireEvent.click(submitBtn)

    expect(mockedApi.createSettingCollection).toHaveBeenCalledTimes(1)
    expect(submitBtn).toBeDisabled()
  })

  it('creates novel with blank setting collection by default', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([sampleCollection()])
    mockedApi.createProject.mockResolvedValue(sampleProject({ id: 'proj-new' }))

    renderDashboard()
    await screen.findByText('还没有作品，新建一部开始创作。')

    fireEvent.click(screen.getByRole('button', { name: '新建作品' }))
    const dialog = screen.getByRole('dialog', { name: '创建作品' })

    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: 'New Novel' } })

    // Verify setting collection selector default is 'new' (blank collection)
    expect(within(dialog).getByRole('button', { name: '选择设定集' })).toHaveTextContent('同时创建空白设定集')

    fireEvent.click(within(dialog).getByRole('button', { name: '创建作品' }))

    await waitFor(() => {
      expect(mockedApi.createProject).toHaveBeenCalledWith({
        slug: expect.stringMatching(/^novel-[a-f0-9-]{36}$/),
        title: 'New Novel',
        target_platform: null,
        metadata: { cover: 'cover-default' },
      })
    })
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/projects/proj-new/studio')
    })
  })

  it('creates novel bound to an existing setting collection with full metadata', async () => {
    const col = sampleCollection({ id: 'col-42', title: 'Cyber World', revision: 5 })
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([col])
    mockedApi.createProject.mockResolvedValue(sampleProject({ id: 'proj-bound' }))

    renderDashboard()
    await screen.findByText('还没有作品，新建一部开始创作。')

    fireEvent.click(screen.getByRole('button', { name: '新建作品' }))
    const dialog = screen.getByRole('dialog', { name: '创建作品' })

    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: 'Cyber Chronicles' } })
    fireEvent.change(within(dialog).getByLabelText('目标平台（可选）'), { target: { value: 'Qidian' } })
    chooseCollection(dialog, 'Cyber World')
    fireEvent.change(within(dialog).getByLabelText('简介（可选）'), { target: { value: 'Neon lights and shadows.' } })
    fireEvent.click(within(dialog).getByText('更多作品信息'))
    expect(within(dialog).queryByLabelText('标签（可选）')).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('group', { name: '作品标签' })).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '更换封面' }))
    fireEvent.click(within(screen.getByRole('dialog', { name: '更换封面' })).getByRole('button', { name: '封面 2' }))

    fireEvent.click(within(dialog).getByRole('button', { name: '创建作品' }))

    await waitFor(() => {
      expect(mockedApi.createProject).toHaveBeenCalledWith({
        slug: expect.stringMatching(/^novel-[a-f0-9-]{36}$/),
        title: 'Cyber Chronicles',
        target_platform: 'Qidian',
        setting_collection_id: 'col-42',
        metadata: {
          introduction: 'Neon lights and shadows.',
          cover: 'cover-2.jpg',
        },
      })
    })
  })

  it('displays metadata and association selection without a redundant collection navigation link', async () => {
    const col = sampleCollection({ id: 'col-1', title: 'Ash World Lore', revision: 3 })
    const proj = sampleProject({
      id: 'project-1',
      title: 'Archive of Ash',
      genre: 'Dark Fantasy',
      target_platform: 'WebNovel',
      setting_collection_id: 'col-1',
      metadata: {
        word_count: 55000,
        introduction: 'The world burned to ash, but embers remain.',
        labels: ['Grimdark', 'Magic'],
      },
    })
    mockedApi.listProjects.mockResolvedValue([proj])
    mockedApi.listSettingCollections.mockResolvedValue([col])
    mockedApi.listChapters.mockResolvedValue([sampleChapter()])

    renderDashboard('/?project=project-1')

    const dialog = await screen.findByRole('dialog', { name: 'Archive of Ash' })
    expect(dialog).toBeInTheDocument()

    // Verify metadata
    fireEvent.click(within(dialog).getByRole('button', { name: '编辑资料' }))
    expect(within(dialog).getByLabelText('字数：5.5万')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('状态：草稿')).toBeInTheDocument()
    expect(within(dialog).queryByLabelText('编辑题材')).not.toBeInTheDocument()
    expect(within(dialog).getByLabelText('编辑目标平台')).toHaveValue('WebNovel')
    expect(within(dialog).getByLabelText('编辑简介')).toHaveValue('The world burned to ash, but embers remain.')
    expect(within(dialog).getByRole('group', { name: '作品标签' })).toHaveTextContent('Grimdark')
    expect(within(dialog).getByRole('group', { name: '作品标签' })).toHaveTextContent('Magic')

    fireEvent.click(within(dialog).getByText('更多作品信息'))
    expect(within(dialog).getByRole('button', { name: '选择设定集' })).toHaveTextContent('Ash World Lore')
    expect(within(dialog).queryByRole('button', { name: 'Open setting collection Ash World Lore' })).not.toBeInTheDocument()
  })

  it('previews the title immediately and automatically saves without leaving editing', async () => {
    const project = sampleProject()
    mockedApi.listProjects.mockResolvedValue([project])
    mockedApi.listSettingCollections.mockResolvedValue([])
    mockedApi.listChapters.mockResolvedValue([])
    mockedApi.updateProject.mockImplementation(async (_id, payload) => ({ ...project, ...payload } as Project))
    renderDashboard('/?project=project-1')
    const dialog = await screen.findByRole('dialog', { name: project.title })
    fireEvent.click(within(dialog).getByRole('button', { name: '编辑资料' }))
    fireEvent.change(within(dialog).getByLabelText('编辑标题'), { target: { value: '实时更新的标题' } })
    expect(within(dialog).getByRole('heading', { name: '实时更新的标题' })).toBeVisible()
    expect(within(dialog).queryByRole('button', { name: '保存修改' })).not.toBeInTheDocument()
    await waitFor(() => expect(mockedApi.updateProject).toHaveBeenCalledWith('project-1', expect.objectContaining({ title: '实时更新的标题' })))
    expect(within(dialog).getByLabelText('编辑标题')).toHaveValue('实时更新的标题')
  })

  it('reuses the searchable label picker in metadata editing', async () => {
    mockedApi.listProjects.mockResolvedValue([sampleProject()])
    mockedApi.listSettingCollections.mockResolvedValue([])
    mockedApi.listChapters.mockResolvedValue([])
    renderDashboard('/?project=project-1')
    const dialog = await screen.findByRole('dialog', { name: 'Archive of Ash' })
    fireEvent.click(within(dialog).getByRole('button', { name: '编辑资料' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '添加标签' }))
    const picker = screen.getByRole('dialog', { name: '添加标签' })
    fireEvent.change(within(picker).getByRole('textbox', { name: '搜索或输入标签' }), { target: { value: '科' } })
    expect(within(picker).getByRole('group', { name: '常用标签' })).toHaveTextContent('科幻')
  })

  it('automatically saves metadata and keeps unrelated metadata when edits are combined', async () => {
    const project = sampleProject({ metadata: { word_count: 12000, introduction: 'Old', labels: ['Fantasy'] } })
    mockedApi.listProjects.mockResolvedValue([project])
    mockedApi.listSettingCollections.mockResolvedValue([sampleCollection({ id: 'col-2', title: 'New World' })])
    mockedApi.listChapters.mockResolvedValue([])
    mockedApi.updateProject.mockImplementation(async (_id, payload) => ({ ...project, ...payload } as Project))
    renderDashboard('/?project=project-1')
    const dialog = await screen.findByRole('dialog', { name: project.title })
    fireEvent.click(within(dialog).getByRole('button', { name: '编辑资料' }))
    fireEvent.change(within(dialog).getByLabelText('编辑标题'), { target: { value: 'New Title' } })
    fireEvent.change(within(dialog).getByLabelText('编辑目标平台'), { target: { value: 'Kindle' } })
    fireEvent.click(within(dialog).getByText('更多作品信息'))
    chooseCollection(dialog, 'New World')
    fireEvent.change(within(dialog).getByLabelText('编辑简介'), { target: { value: 'New introduction' } })
    await waitFor(() => expect(mockedApi.updateProject).toHaveBeenCalledWith('project-1', {
      title: 'New Title', target_platform: 'Kindle', setting_collection_id: 'col-2',
      metadata: { word_count: 12000, introduction: 'New introduction', labels: ['Fantasy'] },
    }))
    expect(mockedApi.updateProject).toHaveBeenCalledTimes(1)
    expect(within(dialog).getByLabelText('编辑简介')).toHaveValue('New introduction')
    fireEvent.click(within(dialog).getByRole('button', { name: '章节目录' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '编辑资料' }))
    expect(within(dialog).getByLabelText('编辑标题')).toHaveValue('New Title')
  })

  it('opens directly on the chapters and exposes only the edit entry', async () => {
    mockedApi.listProjects.mockResolvedValue([sampleProject()])
    mockedApi.listSettingCollections.mockResolvedValue([sampleCollection()])
    mockedApi.listChapters.mockResolvedValue([sampleChapter()])
    renderDashboard('/?project=project-1')
    const dialog = await screen.findByRole('dialog', { name: 'Archive of Ash' })
    expect(await within(dialog).findByRole('button', { name: /第1话.*Beginning of Ash/ })).toBeVisible()
    expect(within(dialog).queryByRole('tablist')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('章节目录')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('作品资料')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '编辑资料' }))
    expect(within(dialog).getByLabelText('编辑简介')).toHaveValue('A tale of ash and rebirth.')
    expect(mockedApi.updateProject).not.toHaveBeenCalled()
  })

  it('retains failed automatic edits across directory switches and retries synchronization', async () => {
    const project = sampleProject()
    mockedApi.listProjects.mockResolvedValue([project])
    mockedApi.listSettingCollections.mockResolvedValue([])
    mockedApi.listChapters.mockResolvedValue([sampleChapter()])
    mockedApi.updateProject.mockRejectedValueOnce(new Error('offline'))
    mockedApi.updateProject.mockImplementationOnce(async (_id, payload) => ({ ...project, ...payload } as Project))
    renderDashboard('/?project=project-1')
    const dialog = await screen.findByRole('dialog', { name: project.title })
    fireEvent.click(within(dialog).getByRole('button', { name: '编辑资料' }))
    fireEvent.change(within(dialog).getByLabelText('编辑标题'), { target: { value: '断网时的书名' } })
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('已保留修改')
    fireEvent.click(within(dialog).getByRole('button', { name: '章节目录' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '编辑资料' }))
    expect(within(dialog).getByLabelText('编辑标题')).toHaveValue('断网时的书名')
    fireEvent.click(within(dialog).getByRole('button', { name: '重试同步' }))
    await waitFor(() => expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument())
    expect(mockedApi.updateProject).toHaveBeenCalledTimes(2)
  })

  it('keeps a recoverable draft and allows close when synchronization fails', async () => {
    mockedApi.listProjects.mockResolvedValue([sampleProject()])
    mockedApi.listSettingCollections.mockResolvedValue([])
    mockedApi.listChapters.mockResolvedValue([])
    mockedApi.updateProject.mockRejectedValue(new Error('offline'))
    renderDashboard('/?project=project-1')
    const dialog = await screen.findByRole('dialog', { name: 'Archive of Ash' })
    fireEvent.click(within(dialog).getByRole('button', { name: '编辑资料' }))
    fireEvent.change(within(dialog).getByLabelText('编辑标题'), { target: { value: '即将关闭的修改' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭作品详情' }))
    await waitFor(() => expect(dialog).not.toBeInTheDocument())
    expect(JSON.parse(localStorage.getItem('guranovel:novel-metadata:project-1')!).title).toBe('即将关闭的修改')
    fireEvent.click(screen.getAllByRole('button', { name: 'Open Archive of Ash' })[0])
    const restored = await screen.findByRole('dialog', { name: '即将关闭的修改' })
    fireEvent.click(within(restored).getByRole('button', { name: '编辑资料' }))
    expect(within(restored).getByLabelText('编辑标题')).toHaveValue('即将关闭的修改')
  })

  it('adds and removes labels through the existing picker and automatically syncs them', async () => {
    const project = sampleProject({ metadata: { labels: ['都市', '科幻'] } })
    mockedApi.listProjects.mockResolvedValue([project])
    mockedApi.listSettingCollections.mockResolvedValue([])
    mockedApi.listChapters.mockResolvedValue([])
    mockedApi.updateProject.mockImplementation(async (_id, payload) => ({ ...project, ...payload } as Project))
    renderDashboard('/?project=project-1')
    const dialog = await screen.findByRole('dialog', { name: project.title })
    fireEvent.click(within(dialog).getByRole('button', { name: '编辑资料' }))
    const tags = within(dialog).getByRole('group', { name: '作品标签' })
    fireEvent.click(within(tags).getByRole('button', { name: '添加标签' }))
    const picker = screen.getByRole('dialog', { name: '添加标签' })
    fireEvent.click(within(picker).getByRole('button', { name: '悬疑' }))
    fireEvent.click(within(picker).getByRole('button', { name: '关闭标签选择' }))
    fireEvent.click(within(tags).getByRole('button', { name: '删除标签：都市' }))
    await waitFor(() => expect(mockedApi.updateProject).toHaveBeenCalledWith('project-1', expect.objectContaining({ metadata: { labels: ['科幻', '悬疑'] } })))
    expect(within(tags).getByRole('button', { name: '删除标签：悬疑' })).toBeVisible()
  })

  it('searches and reveals both novels and setting collections', async () => {
    const proj = sampleProject({ id: 'project-1', title: 'Solar Empire' })
    const col = sampleCollection({ id: 'col-1', title: 'Solar Tech Bible' })
    mockedApi.listProjects.mockResolvedValue([proj])
    mockedApi.listSettingCollections.mockResolvedValue([col])

    renderDashboard()
    await screen.findAllByRole('button', { name: 'Open Solar Empire' })

    fireEvent.click(screen.getByRole('button', { name: '搜索' }))
    const search = await screen.findByRole('searchbox', { name: '搜索作品' })
    fireEvent.focus(search)
    fireEvent.change(search, { target: { value: 'Solar' } })

    const results = screen.getByLabelText('搜索结果')
    expect(within(results).getByRole('button', { name: 'Solar Empire' })).toBeInTheDocument()
    const colResult = within(results).getByRole('button', { name: /Solar Tech Bible/ })
    expect(colResult).toBeInTheDocument()

    // Clicking setting collection search result navigates
    fireEvent.click(colResult)
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/setting-collections/col-1')
    })
  })

  it('opens a preview search from the toolbar, filters types and restores focus', async () => {
    mockedApi.listProjects.mockResolvedValue([sampleProject({ title: 'Solar Empire' })])
    mockedApi.listSettingCollections.mockResolvedValue([sampleCollection({ title: 'Solar Lore' })])
    renderDashboard()
    await screen.findAllByRole('button', { name: 'Open Solar Empire' })
    expect(document.querySelector('.dashboard-search')).toBeNull()
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    const trigger = screen.getByRole('button', { name: '搜索' })
    trigger.focus(); fireEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: '搜索' })
    const input = within(dialog).getByRole('searchbox', { name: '搜索作品' })
    expect(input).toHaveFocus()
    expect(within(dialog).getByRole('region', { name: '预览' })).toHaveTextContent('Solar Empire')
    fireEvent.click(within(dialog).getByRole('button', { name: '设定集' }))
    const results = within(dialog).getByRole('group', { name: '搜索结果' })
    expect(within(results).queryByRole('button', { name: 'Solar Empire' })).not.toBeInTheDocument()
    expect(within(results).getByRole('button', { name: 'Solar Lore' })).toBeInTheDocument()
    fireEvent.change(input, { target: { value: 'nothing' } })
    expect(within(results).queryByRole('button')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('region', { name: '预览' })).not.toHaveTextContent('Solar Empire')
    fireEvent(dialog, new Event('cancel', { cancelable: true }))
    await waitFor(() => expect(dialog).not.toBeInTheDocument())
    expect(trigger).toHaveFocus()
  })

  it('shows clean label metadata in search rows and searches labels without displaying the introduction there', async () => {
    mockedApi.listProjects.mockResolvedValue([sampleProject({ title: '海雾', metadata: { labels: ['科幻', 42, '', '科幻'], introduction: '一段只供预览的简介。' } })])
    mockedApi.listSettingCollections.mockResolvedValue([])
    renderDashboard()
    await screen.findAllByRole('button', { name: 'Open 海雾' })
    fireEvent.click(screen.getByRole('button', { name: '搜索' }))
    const dialog = screen.getByRole('dialog', { name: '搜索' })
    const row = within(dialog).getByRole('button', { name: '海雾' })
    expect(row).toHaveTextContent('科幻')
    expect(row).not.toHaveTextContent('一段只供预览')
    fireEvent.change(within(dialog).getByRole('searchbox'), { target: { value: '科幻' } })
    expect(within(dialog).getByRole('button', { name: '海雾' })).toBeInTheDocument()
    expect(within(dialog).getByRole('region', { name: '预览' })).toHaveTextContent('一段只供预览的简介。')
  })
})
