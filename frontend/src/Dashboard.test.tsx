import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
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

describe('Dashboard Setting Collections and Novel Integration', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('renders setting collections carousel with revision and referencing novel count', async () => {
    const col = sampleCollection({ id: 'col-1', title: 'Great Lore', revision: 2 })
    const proj = sampleProject({ setting_collection_id: 'col-1' })
    mockedApi.listProjects.mockResolvedValue([proj])
    mockedApi.listSettingCollections.mockResolvedValue([col])

    renderDashboard()

    expect(await screen.findByRole('heading', { name: /setting collections/i })).toBeInTheDocument()
    const card = await screen.findByRole('button', { name: 'Open Great Lore setting collection' })
    expect(card).toBeInTheDocument()
    expect(within(card).getByText('r2')).toBeInTheDocument()
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

    expect(await screen.findByText('No setting collections yet. Create one to begin.')).toBeInTheDocument()
  })

  it('creates a new setting collection from the dashboard and navigates to it', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.listSettingCollections.mockResolvedValue([])
    mockedApi.createSettingCollection.mockResolvedValue(sampleCollection({ id: 'col-new', title: 'New Realm' }))

    renderDashboard()
    await screen.findByText('No setting collections yet. Create one to begin.')

    fireEvent.click(screen.getByRole('button', { name: 'Create setting collection' }))
    const dialog = screen.getByRole('dialog', { name: 'Create setting collection' })
    expect(dialog).toBeInTheDocument()

    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'New Realm' } })
    fireEvent.change(within(dialog).getByLabelText('Slug (optional)'), { target: { value: 'new-realm' } })
    fireEvent.change(within(dialog).getByLabelText('Description (optional)'), { target: { value: 'Realm overview' } })

    const submitBtn = within(dialog).getByRole('button', { name: 'Create setting collection' })
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
    await screen.findByText('No setting collections yet. Create one to begin.')

    fireEvent.click(screen.getByRole('button', { name: 'Create setting collection' }))
    const dialog = screen.getByRole('dialog', { name: 'Create setting collection' })
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'Pending Realm' } })

    const submitBtn = within(dialog).getByRole('button', { name: 'Create setting collection' })
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
    await screen.findByText('No projects yet. Create one to begin.')

    fireEvent.click(screen.getByRole('button', { name: 'Create novel' }))
    const dialog = screen.getByRole('dialog', { name: 'Create project' })

    fireEvent.change(within(dialog).getByLabelText('Slug'), { target: { value: 'new-novel' } })
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'New Novel' } })

    // Verify setting collection selector default is 'new' (blank collection)
    const select = within(dialog).getByLabelText('Setting collection') as HTMLSelectElement
    expect(select.value).toBe('new')

    fireEvent.click(within(dialog).getByRole('button', { name: 'Create project' }))

    await waitFor(() => {
      expect(mockedApi.createProject).toHaveBeenCalledWith({
        slug: 'new-novel',
        title: 'New Novel',
        genre: null,
        target_platform: null,
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
    await screen.findByText('No projects yet. Create one to begin.')

    fireEvent.click(screen.getByRole('button', { name: 'Create novel' }))
    const dialog = screen.getByRole('dialog', { name: 'Create project' })

    fireEvent.change(within(dialog).getByLabelText('Slug'), { target: { value: 'cyber-chronicles' } })
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'Cyber Chronicles' } })
    fireEvent.change(within(dialog).getByLabelText('Genre (optional)'), { target: { value: 'Sci-Fi' } })
    fireEvent.change(within(dialog).getByLabelText('Target platform (optional)'), { target: { value: 'Qidian' } })
    fireEvent.change(within(dialog).getByLabelText('Setting collection'), { target: { value: 'col-42' } })
    fireEvent.change(within(dialog).getByLabelText('Introduction (optional)'), { target: { value: 'Neon lights and shadows.' } })
    fireEvent.change(within(dialog).getByLabelText('Labels (optional)'), { target: { value: 'Cyberpunk, Action' } })
    fireEvent.change(within(dialog).getByLabelText('Cover URL or preset (optional)'), { target: { value: 'cover-2.jpg' } })

    fireEvent.click(within(dialog).getByRole('button', { name: 'Create project' }))

    await waitFor(() => {
      expect(mockedApi.createProject).toHaveBeenCalledWith({
        slug: 'cyber-chronicles',
        title: 'Cyber Chronicles',
        genre: 'Sci-Fi',
        target_platform: 'Qidian',
        setting_collection_id: 'col-42',
        metadata: {
          introduction: 'Neon lights and shadows.',
          labels: ['Cyberpunk', 'Action'],
          cover: 'cover-2.jpg',
        },
      })
    })
  })

  it('displays enriched novel details including setting collection link and metadata', async () => {
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
    expect(within(dialog).getByText(/字数：5.5万/)).toBeInTheDocument()
    expect(within(dialog).getByText(/状态：草稿/)).toBeInTheDocument()
    expect(within(dialog).getByText('Dark Fantasy')).toBeInTheDocument()
    expect(within(dialog).getByText('WebNovel')).toBeInTheDocument()
    expect(within(dialog).getByText('The world burned to ash, but embers remain.')).toBeInTheDocument()
    expect(within(dialog).getByText('Grimdark')).toBeInTheDocument()
    expect(within(dialog).getByText('Magic')).toBeInTheDocument()

    // Verify setting collection link badge
    const colLink = within(dialog).getByRole('button', { name: 'Open setting collection Ash World Lore' })
    expect(colLink).toBeInTheDocument()
    expect(within(colLink).getByText('r3')).toBeInTheDocument()

    // Click setting collection link navigates to setting collection workspace
    fireEvent.click(colLink)
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/setting-collections/col-1')
    })
  })

  it('supports inline editing of novel details and saves via updateProject', async () => {
    const col1 = sampleCollection({ id: 'col-1', title: 'Ash World Lore' })
    const col2 = sampleCollection({ id: 'col-2', title: 'Frost Kingdom Lore', revision: 2 })
    const proj = sampleProject({
      id: 'project-1',
      title: 'Archive of Ash',
      genre: 'Fantasy',
      setting_collection_id: 'col-1',
      metadata: {
        introduction: 'Old introduction.',
        labels: ['Fantasy'],
      },
    })
    const updatedProj = sampleProject({
      id: 'project-1',
      title: 'Archive of Ash: Reborn',
      genre: 'Dark Fantasy',
      target_platform: 'Kindle',
      setting_collection_id: 'col-2',
      metadata: {
        introduction: 'New expanded introduction.',
        labels: ['Dark Fantasy', 'Epic'],
      },
    })

    mockedApi.listProjects.mockResolvedValue([proj])
    mockedApi.listSettingCollections.mockResolvedValue([col1, col2])
    mockedApi.listChapters.mockResolvedValue([sampleChapter()])
    mockedApi.updateProject.mockResolvedValue(updatedProj)

    renderDashboard('/?project=project-1')
    const dialog = await screen.findByRole('dialog', { name: 'Archive of Ash' })

    // Click edit toggle
    fireEvent.click(within(dialog).getByRole('button', { name: '编辑资料' }))

    // Edit inputs are displayed
    const titleInput = within(dialog).getByLabelText('Edit title')
    expect(titleInput).toHaveValue('Archive of Ash')
    fireEvent.change(titleInput, { target: { value: 'Archive of Ash: Reborn' } })

    const genreInput = within(dialog).getByLabelText('Edit genre')
    fireEvent.change(genreInput, { target: { value: 'Dark Fantasy' } })

    const platformInput = within(dialog).getByLabelText('Edit target platform')
    fireEvent.change(platformInput, { target: { value: 'Kindle' } })

    const colSelect = within(dialog).getByLabelText('Edit setting collection')
    fireEvent.change(colSelect, { target: { value: 'col-2' } })

    const introInput = within(dialog).getByLabelText('Edit introduction')
    fireEvent.change(introInput, { target: { value: 'New expanded introduction.' } })

    const labelsInput = within(dialog).getByLabelText('Edit labels')
    fireEvent.change(labelsInput, { target: { value: 'Dark Fantasy, Epic' } })

    // Save changes
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    await waitFor(() => {
      expect(mockedApi.updateProject).toHaveBeenCalledWith('project-1', {
        title: 'Archive of Ash: Reborn',
        genre: 'Dark Fantasy',
        target_platform: 'Kindle',
        setting_collection_id: 'col-2',
        metadata: {
          introduction: 'New expanded introduction.',
          labels: ['Dark Fantasy', 'Epic'],
        },
      })
    })

    // Dialog updates with new values and exits edit mode
    expect(await within(dialog).findByRole('heading', { name: 'Archive of Ash: Reborn' })).toBeInTheDocument()
    expect(within(dialog).getByText('New expanded introduction.')).toBeInTheDocument()
    expect(within(dialog).getAllByText('Dark Fantasy').length).toBeGreaterThanOrEqual(1)
    expect(within(dialog).getByText('Epic')).toBeInTheDocument()
    expect(within(dialog).getByText('Kindle')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Open setting collection Frost Kingdom Lore' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '编辑资料' })).toBeInTheDocument()
  })

  it('searches and reveals both novels and setting collections', async () => {
    const proj = sampleProject({ id: 'project-1', title: 'Solar Empire' })
    const col = sampleCollection({ id: 'col-1', title: 'Solar Tech Bible' })
    mockedApi.listProjects.mockResolvedValue([proj])
    mockedApi.listSettingCollections.mockResolvedValue([col])

    renderDashboard()
    await screen.findAllByRole('button', { name: 'Open Solar Empire' })

    const search = await screen.findByRole('searchbox', { name: 'Search novels' })
    fireEvent.focus(search)
    fireEvent.change(search, { target: { value: 'Solar' } })

    const results = screen.getByLabelText('Search results')
    expect(within(results).getByRole('button', { name: 'Solar Empire' })).toBeInTheDocument()
    const colResult = within(results).getByRole('button', { name: /Solar Tech Bible/ })
    expect(colResult).toBeInTheDocument()

    // Clicking setting collection search result navigates
    fireEvent.click(colResult)
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/setting-collections/col-1')
    })
  })
})
