import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Link, MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Chapter, ChapterProductionRun, Project } from './api/client'
import App from './App'

vi.mock('./api/client', () => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  getProject: vi.fn(),
  listChapters: vi.fn(),
  createChapter: vi.fn(),
  getChapter: vi.fn(),
  startChapterProduction: vi.fn(),
  resolveChapterProductionAction: vi.fn(),
}))

import * as api from './api/client'

const mockedApi = vi.mocked(api)

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: 'project-1', slug: 'archive-of-ash', title: 'Archive of Ash', genre: null,
    target_platform: null, status: 'draft', workspace_root: '/workspace/archive-of-ash', metadata: {},
    created_at: '2026-07-19T00:00:00Z', updated_at: '2026-07-19T00:00:00Z', ...overrides,
  }
}

function chapter(overrides: Partial<Chapter> = {}): Chapter {
  return {
    id: 'chapter-1', project_id: 'project-1', chapter_number: 7, title: null, status: 'draft',
    current_outline_document_id: null, current_draft_document_id: null, final_document_id: null,
    summary_document_id: null, word_count: 0, metadata: {}, created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z', ...overrides,
  }
}

function productionRun(overrides: Partial<ChapterProductionRun> = {}): ChapterProductionRun {
  return {
    id: 'run-1', type: 'chapter_production', status: 'awaiting_approval', current_node: 'review_outline',
    next_node: 'await_user', awaiting_user: true,
    actions: [{ id: 'action-server-id', type: 'chapter_production_approval', status: 'pending', options: ['approved', 'rejected'], default_option: null, user_decision: null }],
    events: [
      { event_type: 'production_started', node_name: 'start', message: 'Production started', payload: {} },
      { event_type: 'generation_output_stored', node_name: 'store_outline', message: 'Outline stored', payload: { outline_document_id: 'outline-safe-id' } },
    ],
    outline_document_id: 'outline-safe-id', draft_document_id: null, ...overrides,
  }
}

function Location() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function RouteTransition({ path }: { path: string }) {
  return <><Link to={path}>Navigate test route</Link><App /><Location /></>
}

function renderApp(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <Location />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('application shell', () => {
  it('provides accessible landmarks', () => {
    mockedApi.listProjects.mockResolvedValue([])
    renderApp()
    expect(screen.getByRole('banner', { name: 'GuraNovel workbench' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Workbench navigation' })).toBeInTheDocument()
    expect(screen.getByRole('main')).toBeInTheDocument()
  })
})

describe('project list', () => {
  it('shows loading, returned projects, an empty state, and a safe error', async () => {
    let resolveProjects!: (projects: Project[]) => void
    mockedApi.listProjects.mockReturnValue(new Promise((resolve) => { resolveProjects = resolve }))
    let view = renderApp()
    expect(screen.getByText('Loading projects…')).toBeInTheDocument()
    resolveProjects([project()])
    expect(await screen.findByRole('link', { name: 'Archive of Ash' })).toHaveAttribute('href', '/projects/project-1')

    view.unmount()
    mockedApi.listProjects.mockResolvedValue([])
    view = renderApp()
    expect(await screen.findByText('No projects yet. Create one to begin.')).toBeInTheDocument()

    view.unmount()
    mockedApi.listProjects.mockRejectedValue(new Error('internal details must not render'))
    renderApp()
    expect(await screen.findByText('Projects could not be loaded. Try again.')).toBeInTheDocument()
    expect(screen.queryByText(/internal details/)).not.toBeInTheDocument()
  })

  it('creates a project through the typed client and navigates with its server ID', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.createProject.mockResolvedValue(project({ id: 'server-project-id' }))
    renderApp()
    await screen.findByText('No projects yet. Create one to begin.')
    fireEvent.change(screen.getByLabelText('Slug'), { target: { value: 'archive-of-ash' } })
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Archive of Ash' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }))
    await waitFor(() => expect(mockedApi.createProject).toHaveBeenCalledWith({
      slug: 'archive-of-ash', title: 'Archive of Ash', genre: null, target_platform: null,
    }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/projects/server-project-id'))
  })

  it('prevents duplicate project creation while pending', async () => {
    mockedApi.listProjects.mockResolvedValue([])
    mockedApi.createProject.mockReturnValue(new Promise(() => undefined))
    renderApp()
    await screen.findByText('No projects yet. Create one to begin.')
    fireEvent.change(screen.getByLabelText('Slug'), { target: { value: 'archive-of-ash' } })
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Archive of Ash' } })
    const button = screen.getByRole('button', { name: 'Create project' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(mockedApi.createProject).toHaveBeenCalledTimes(1)
    expect(button).toBeDisabled()
  })
})

describe('project workspace', () => {
  it('shows server chapter details and navigates with the created chapter server ID', async () => {
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.listChapters.mockResolvedValue([chapter({ chapter_number: 12, title: 'Arrival' })])
    mockedApi.createChapter.mockResolvedValue(chapter({ id: 'server-chapter-id' }))
    renderApp('/projects/project-1')
    expect(await screen.findByText('Chapter 12')).toBeInTheDocument()
    expect(screen.getByText('Arrival · draft')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Chapter title'), { target: { value: 'A new beginning' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create chapter' }))
    await waitFor(() => expect(mockedApi.createChapter).toHaveBeenCalledWith('project-1', { title: 'A new beginning' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/projects/project-1/chapters/server-chapter-id'))
  })

  it('prevents duplicate chapter creation while pending', async () => {
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.listChapters.mockResolvedValue([])
    mockedApi.createChapter.mockReturnValue(new Promise(() => undefined))
    renderApp('/projects/project-1')
    await screen.findByText('No chapters yet.')
    const button = screen.getByRole('button', { name: 'Create chapter' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(mockedApi.createChapter).toHaveBeenCalledTimes(1)
    expect(button).toBeDisabled()
  })
})

describe('chapter workspace and routing', () => {
  it('renders fetched server identity and safely presents an unknown route', async () => {
    mockedApi.getProject.mockResolvedValue(project({ title: 'The Server Project' }))
    mockedApi.getChapter.mockResolvedValue(chapter({ id: 'chapter-server', chapter_number: 14, title: 'Server chapter' }))
    const view = renderApp('/projects/project-1/chapters/chapter-server')
    expect(await screen.findByRole('heading', { name: 'Server chapter' })).toBeInTheDocument()
    expect(screen.getByText('Chapter 14')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to The Server Project' })).toHaveAttribute('href', '/projects/project-1')

    view.unmount()
    renderApp('/not-a-route')
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })

  it('explains the pre-run state, starts production with route IDs, and renders its returned timeline', async () => {
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockResolvedValue(chapter())
    mockedApi.startChapterProduction.mockResolvedValue(productionRun())
    renderApp('/projects/project-1/chapters/chapter-1')

    expect(await screen.findByText('Chapter production has not started yet.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Start chapter production' }))
    await waitFor(() => expect(mockedApi.startChapterProduction).toHaveBeenCalledWith('project-1', 'chapter-1'))
    expect(await screen.findByText('Production started')).toBeInTheDocument()
    expect(screen.getByText('Node: store_outline')).toBeInTheDocument()
    expect(screen.getByText('Outline document: outline-safe-id')).toBeInTheDocument()
  })

  it('clears production state when navigation reuses the workspace for another chapter', async () => {
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockImplementation(async (_projectId, chapterId) => chapter({
      id: chapterId,
      chapter_number: chapterId === 'chapter-a' ? 1 : 2,
      title: chapterId === 'chapter-a' ? 'Chapter A' : 'Chapter B',
    }))
    mockedApi.startChapterProduction.mockResolvedValue(productionRun())
    const view = render(
      <MemoryRouter initialEntries={['/projects/project-1/chapters/chapter-a']}>
        <RouteTransition path="/projects/project-1/chapters/chapter-a" />
      </MemoryRouter>,
    )

    await screen.findByRole('heading', { name: 'Chapter A' })
    fireEvent.click(screen.getByRole('button', { name: 'Start chapter production' }))
    await screen.findByText('Production started')
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()

    view.rerender(
      <MemoryRouter initialEntries={['/projects/project-1/chapters/chapter-a']}>
        <RouteTransition path="/projects/project-1/chapters/chapter-b" />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('link', { name: 'Navigate test route' }))

    expect(await screen.findByRole('heading', { name: 'Chapter B' })).toBeInTheDocument()
    expect(screen.getByText('Chapter production has not started yet.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start chapter production' })).toBeEnabled()
    expect(screen.queryByText('Production started')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('ignores an in-flight production start after navigation remounts the workspace', async () => {
    let resolveStart!: (run: ChapterProductionRun) => void
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockImplementation(async (_projectId, chapterId) => chapter({
      id: chapterId,
      chapter_number: chapterId === 'chapter-a' ? 1 : 2,
      title: chapterId === 'chapter-a' ? 'Chapter A' : 'Chapter B',
    }))
    mockedApi.startChapterProduction.mockReturnValue(new Promise((resolve) => { resolveStart = resolve }))
    const view = render(
      <MemoryRouter initialEntries={['/projects/project-1/chapters/chapter-a']}>
        <RouteTransition path="/projects/project-1/chapters/chapter-a" />
      </MemoryRouter>,
    )

    await screen.findByRole('heading', { name: 'Chapter A' })
    fireEvent.click(screen.getByRole('button', { name: 'Start chapter production' }))

    view.rerender(
      <MemoryRouter initialEntries={['/projects/project-1/chapters/chapter-a']}>
        <RouteTransition path="/projects/project-1/chapters/chapter-b" />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('link', { name: 'Navigate test route' }))
    expect(await screen.findByRole('heading', { name: 'Chapter B' })).toBeInTheDocument()

    resolveStart(productionRun())
    await waitFor(() => expect(screen.queryByText('Production started')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Start chapter production' })).toBeEnabled()
  })

  it('renders only explicitly allowed production event fields', async () => {
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockResolvedValue(chapter())
    mockedApi.startChapterProduction.mockResolvedValue(productionRun({
      events: [{
        event_type: 'generation_provenance', node_name: 'generate_outline', message: null,
        payload: { provider_kind: 'fake', model_identifier: 'safe-model', prompt_template_version: 'v1', input_tokens: 12, output_tokens: 34, raw_output: 'SECRET RAW OUTPUT', provider_url: 'https://unsafe.example' } as never,
      }],
    }))
    renderApp('/projects/project-1/chapters/chapter-1')
    await screen.findByRole('button', { name: 'Start chapter production' })
    fireEvent.click(screen.getByRole('button', { name: 'Start chapter production' }))

    expect(await screen.findByText('Workflow event recorded.')).toBeInTheDocument()
    expect(screen.getByText('Provider: fake · Model: safe-model · Template: v1')).toBeInTheDocument()
    expect(screen.getByText('Tokens: input 12 · output 34')).toBeInTheDocument()
    expect(screen.queryByText(/SECRET RAW OUTPUT|unsafe\.example|raw_output/)).not.toBeInTheDocument()
  })

  it('resolves a pending approval once with the server action ID and replaces the run from the response', async () => {
    let resolveAction!: (run: ChapterProductionRun) => void
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockResolvedValue(chapter())
    mockedApi.startChapterProduction.mockResolvedValue(productionRun())
    mockedApi.resolveChapterProductionAction.mockReturnValue(new Promise((resolve) => { resolveAction = resolve }))
    renderApp('/projects/project-1/chapters/chapter-1')
    await screen.findByRole('button', { name: 'Start chapter production' })
    fireEvent.click(screen.getByRole('button', { name: 'Start chapter production' }))
    const approve = await screen.findByRole('button', { name: 'Approve' })
    const reject = screen.getByRole('button', { name: 'Reject' })
    fireEvent.click(approve)
    fireEvent.click(approve)
    expect(mockedApi.resolveChapterProductionAction).toHaveBeenCalledTimes(1)
    expect(mockedApi.resolveChapterProductionAction).toHaveBeenCalledWith('project-1', 'chapter-1', 'run-1', 'action-server-id', { decision: 'approved' })
    expect(approve).toBeDisabled()
    expect(reject).toBeDisabled()
    resolveAction(productionRun({ status: 'completed', current_node: null, next_node: null, awaiting_user: false, actions: [{ id: 'action-server-id', type: 'chapter_production_approval', status: 'resolved', options: ['approved', 'rejected'], default_option: null, user_decision: 'approved' }] }))
    expect(await screen.findByText('completed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('does not offer approval controls for unknown or resolved actions', async () => {
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockResolvedValue(chapter())
    mockedApi.startChapterProduction.mockResolvedValue(productionRun({ actions: [{ id: 'action-unknown', type: 'approval', status: 'pending', options: ['approved', 'defer'], default_option: null, user_decision: null }] }))
    renderApp('/projects/project-1/chapters/chapter-1')
    await screen.findByRole('button', { name: 'Start chapter production' })
    fireEvent.click(screen.getByRole('button', { name: 'Start chapter production' }))
    await screen.findByText('awaiting_approval')
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
  })

  it('uses fixed safe errors for production and project or chapter load failures', async () => {
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockResolvedValue(chapter())
    mockedApi.startChapterProduction.mockRejectedValue(new Error('provider URL https://secret.example'))
    const view = renderApp('/projects/project-1/chapters/chapter-1')
    await screen.findByRole('button', { name: 'Start chapter production' })
    fireEvent.click(screen.getByRole('button', { name: 'Start chapter production' }))
    expect(await screen.findByText('Chapter production could not be started. Try again.')).toBeInTheDocument()
    expect(screen.queryByText(/secret\.example/)).not.toBeInTheDocument()

    view.unmount()
    mockedApi.getProject.mockRejectedValue(new Error('internal project details'))
    mockedApi.getChapter.mockResolvedValue(chapter())
    renderApp('/projects/project-1/chapters/chapter-1')
    expect(await screen.findByText('This workspace could not be loaded. Try again.')).toBeInTheDocument()
    expect(screen.queryByText(/internal project details/)).not.toBeInTheDocument()

    cleanup()
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockRejectedValue(new Error('internal chapter details'))
    renderApp('/projects/project-1/chapters/chapter-1')
    expect(await screen.findByText('This workspace could not be loaded. Try again.')).toBeInTheDocument()
    expect(screen.queryByText(/internal chapter details/)).not.toBeInTheDocument()
  })

  it('uses a fixed safe error when approval resolution fails', async () => {
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockResolvedValue(chapter())
    mockedApi.startChapterProduction.mockResolvedValue(productionRun())
    mockedApi.resolveChapterProductionAction.mockRejectedValue(new Error('raw response and headers'))
    renderApp('/projects/project-1/chapters/chapter-1')
    await screen.findByRole('button', { name: 'Start chapter production' })
    fireEvent.click(screen.getByRole('button', { name: 'Start chapter production' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }))
    expect(await screen.findByText('Chapter production approval could not be resolved. Try again.')).toBeInTheDocument()
    expect(screen.queryByText(/raw response and headers/)).not.toBeInTheDocument()
  })
})
