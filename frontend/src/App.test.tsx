import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Link, MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Chapter, ChapterProductionRun, Document, DocumentContent, DocumentVersion, Project } from './api/client'
import App from './App'

vi.mock('./api/client', () => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  getProject: vi.fn(),
  listChapters: vi.fn(),
  createChapter: vi.fn(),
  getChapter: vi.fn(),
  getDocument: vi.fn(),
  readDocumentContent: vi.fn(),
  listDocumentVersions: vi.fn(),
  readDocumentVersionContent: vi.fn(),
  writeDocument: vi.fn(),
  restoreDocument: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    code: string
    constructor(status: number, code: string, message: string) {
      super(message)
      this.status = status
      this.code = code
    }
  },
  startChapterProduction: vi.fn(),
  resolveChapterProductionAction: vi.fn(),
}))

vi.mock('./ReaderPanelWorkbench', () => ({
  ReaderPanelWorkbench: ({
    projectId, chapterId, documentId, documentVersionId, sessionId, onSessionStarted,
  }: {
    projectId: string
    chapterId: string
    documentId: string
    documentVersionId: string
    sessionId?: string
    onSessionStarted?: (sessionId: string) => void
  }) => <section aria-label="Reader Panel test page">
    <p>{[projectId, chapterId, documentId, documentVersionId, sessionId ?? 'start'].join('|')}</p>
    {!sessionId && <button type="button" onClick={() => onSessionStarted?.('server-session-id')}>Mock start panel</button>}
  </section>,
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
    outline_document_id: null, draft_document_id: null, ...overrides,
  }
}

function documentVersion(overrides: Partial<DocumentVersion> = {}): DocumentVersion {
  return {
    id: 'version-current', document_id: 'document-draft', version_number: 2, parent_version_id: 'version-first',
    source: 'user', actor_user_id: null, agent_role: null, workflow_run_id: null, content_hash: 'hash',
    byte_size: 12, word_count: 2, file_path: 'chapters/draft.md', change_summary: null,
    created_at: '2026-07-19T00:00:00Z', ...overrides,
  }
}

function document(overrides: Partial<Document> = {}): Document {
  return {
    id: 'document-draft', project_id: 'project-1', chapter_id: 'chapter-1', type: 'chapter_draft',
    title: 'Chapter draft', path: 'chapters/draft.md', current_version_id: 'version-current',
    current_version: documentVersion(), created_at: '2026-07-19T00:00:00Z', updated_at: '2026-07-19T00:00:00Z', ...overrides,
  }
}

function documentContent(overrides: Partial<DocumentContent> = {}): DocumentContent {
  return { document_id: 'document-draft', version_id: 'version-current', content: '# Server draft', ...overrides }
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

describe('reader panel routes', () => {
  const startPath = '/projects/project-1/chapters/chapter-1/documents/document-1/versions/version-1/reader-panel'

  it('restores the exact document-version identity from a deep link and returns to the chapter', () => {
    renderApp(startPath)
    expect(screen.getByRole('region', { name: 'Reader Panel test page' })).toHaveTextContent(
      'project-1|chapter-1|document-1|version-1|start',
    )
    expect(screen.getByRole('link', { name: 'Back to chapter' })).toHaveAttribute(
      'href', '/projects/project-1/chapters/chapter-1',
    )
  })

  it('updates the URL from the server session ID and restores a session deep link on refresh', async () => {
    const view = renderApp(startPath)
    fireEvent.click(screen.getByRole('button', { name: 'Mock start panel' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(`${startPath}/server-session-id`))

    view.unmount()
    renderApp(`${startPath}/session-from-url`)
    expect(screen.getByRole('region', { name: 'Reader Panel test page' })).toHaveTextContent(
      'project-1|chapter-1|document-1|version-1|session-from-url',
    )
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
    expect(screen.getByRole('link', { name: '开始构思' })).toHaveAttribute(
      'href',
      '/projects/project-1/creation/start',
    )
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

  it('makes exact document IDs from a started run available without reloading the chapter', async () => {
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockResolvedValue(chapter())
    mockedApi.startChapterProduction.mockResolvedValue(productionRun({
      outline_document_id: 'run-outline-id', draft_document_id: 'run-draft-id',
    }))
    mockedApi.getDocument.mockImplementation(async (id) => document({
      id, title: id === 'run-outline-id' ? 'Server outline' : 'Server draft',
      current_version_id: `${id}-version`, current_version: documentVersion({ id: `${id}-version`, document_id: id }),
    }))
    mockedApi.readDocumentContent.mockImplementation(async (id) => documentContent({
      document_id: id, version_id: `${id}-version`, content: `# ${id}`,
    }))
    mockedApi.listDocumentVersions.mockResolvedValue([])
    renderApp('/projects/project-1/chapters/chapter-1')

    expect(await screen.findByText('No server documents are available for this chapter.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Start chapter production' }))

    expect(await screen.findByRole('button', { name: 'Outline' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Draft' })).toBeInTheDocument()
    expect(await screen.findByLabelText('Server outline')).toHaveValue('# run-outline-id')
    expect(mockedApi.getDocument).toHaveBeenCalledWith('run-outline-id')
    expect(mockedApi.readDocumentContent).toHaveBeenCalledWith('run-outline-id')
    expect(mockedApi.listDocumentVersions).toHaveBeenCalledWith('run-outline-id')

    fireEvent.click(screen.getByRole('button', { name: 'Draft' }))
    expect(await screen.findByLabelText('Server draft')).toHaveValue('# run-draft-id')
    expect(mockedApi.getDocument).toHaveBeenCalledWith('run-draft-id')
    expect(mockedApi.readDocumentContent).toHaveBeenCalledWith('run-draft-id')
    expect(mockedApi.listDocumentVersions).toHaveBeenCalledWith('run-draft-id')
    expect(mockedApi.startChapterProduction).toHaveBeenCalledTimes(1)
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

describe('chapter document workspace', () => {
  function mockDocumentWorkspace() {
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockResolvedValue(chapter({ current_draft_document_id: 'document-draft' }))
    mockedApi.getDocument.mockResolvedValue(document())
    mockedApi.readDocumentContent.mockResolvedValue(documentContent())
    mockedApi.listDocumentVersions.mockResolvedValue([
      documentVersion({ id: 'version-first', version_number: 1 }),
      documentVersion(),
    ])
  }

  it('derives its only available document from the chapter and saves with its current server version', async () => {
    mockDocumentWorkspace()
    mockedApi.readDocumentContent.mockResolvedValue(documentContent({ content: '<img src=x onerror=alert(1)>' }))
    mockedApi.writeDocument.mockResolvedValue(documentVersion({ id: 'version-saved', version_number: 3 }))
    renderApp('/projects/project-1/chapters/chapter-1')

    const editor = await screen.findByLabelText('Chapter draft')
    expect(editor).toHaveValue('<img src=x onerror=alert(1)>')
    expect(globalThis.document.querySelector('img')).toBeNull()
    expect(mockedApi.getDocument).toHaveBeenCalledWith('document-draft')
    expect(mockedApi.readDocumentContent).toHaveBeenCalledWith('document-draft')
    expect(screen.queryByRole('button', { name: /outline|final|summary/i })).not.toBeInTheDocument()
    fireEvent.change(editor, { target: { value: 'My local revision' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save document' }))
    await waitFor(() => expect(mockedApi.writeDocument).toHaveBeenCalledWith('document-draft', {
      content: 'My local revision', expected_current_version_id: 'version-current',
    }))
  })

  it('saves against the exact version of the loaded content snapshot', async () => {
    mockDocumentWorkspace()
    mockedApi.getDocument.mockResolvedValue(document({ current_version_id: 'V2' }))
    mockedApi.readDocumentContent.mockResolvedValue(documentContent({ version_id: 'V1', content: 'Version one draft' }))
    mockedApi.writeDocument.mockResolvedValue(documentVersion({ id: 'V3', version_number: 3 }))
    renderApp('/projects/project-1/chapters/chapter-1')

    const editor = await screen.findByLabelText('Chapter draft')
    fireEvent.change(editor, { target: { value: 'Edited version one draft' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save document' }))

    await waitFor(() => expect(mockedApi.writeDocument).toHaveBeenCalledWith('document-draft', {
      content: 'Edited version one draft', expected_current_version_id: 'V1',
    }))
  })

  it('restores against the exact version of the loaded content snapshot', async () => {
    mockDocumentWorkspace()
    mockedApi.getDocument.mockResolvedValue(document({ current_version_id: 'V2' }))
    mockedApi.readDocumentContent.mockResolvedValue(documentContent({ version_id: 'V1', content: 'Version one draft' }))
    mockedApi.readDocumentVersionContent.mockResolvedValue(documentContent({ version_id: 'version-first', content: 'First server revision' }))
    mockedApi.restoreDocument.mockResolvedValue(documentVersion({ id: 'V3', version_number: 3 }))
    renderApp('/projects/project-1/chapters/chapter-1')

    await screen.findByLabelText('Chapter draft')
    fireEvent.click(screen.getByRole('button', { name: 'Version 1' }))
    expect(await screen.findByText('First server revision')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Restore version 1' }))

    await waitFor(() => expect(mockedApi.restoreDocument).toHaveBeenCalledWith('document-draft', 'version-first', {
      expected_current_version_id: 'V1',
    }))
  })

  it('keeps the local draft and gives a fixed message when the server reports a version conflict', async () => {
    mockDocumentWorkspace()
    mockedApi.writeDocument.mockRejectedValue(new api.ApiError(409, 'document_version_conflict', 'unsafe server detail'))
    renderApp('/projects/project-1/chapters/chapter-1')

    const editor = await screen.findByLabelText('Chapter draft')
    fireEvent.change(editor, { target: { value: 'Keep this local draft' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save document' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This document changed on the server. Your local draft was kept.')
    expect(editor).toHaveValue('Keep this local draft')
    expect(screen.queryByText(/unsafe server detail/)).not.toBeInTheDocument()
  })

  it('reads immutable selected server versions and restores the selected server ID', async () => {
    mockDocumentWorkspace()
    mockedApi.readDocumentVersionContent.mockResolvedValue(documentContent({ version_id: 'version-first', content: 'First server revision' }))
    mockedApi.restoreDocument.mockResolvedValue(documentVersion({ id: 'version-restored', version_number: 3 }))
    renderApp('/projects/project-1/chapters/chapter-1')

    await screen.findByLabelText('Chapter draft')
    fireEvent.click(screen.getByRole('button', { name: 'Version 1' }))
    expect(await screen.findByText('First server revision')).toBeInTheDocument()
    expect(mockedApi.readDocumentVersionContent).toHaveBeenCalledWith('document-draft', 'version-first')
    fireEvent.click(screen.getByRole('button', { name: 'Restore version 1' }))
    await waitFor(() => expect(mockedApi.restoreDocument).toHaveBeenCalledWith('document-draft', 'version-first', {
      expected_current_version_id: 'version-current',
    }))
  })

  it('keeps the most recently selected server version when earlier content arrives late', async () => {
    let resolveFirst!: (content: DocumentContent) => void
    let resolveCurrent!: (content: DocumentContent) => void
    mockDocumentWorkspace()
    mockedApi.readDocumentVersionContent.mockImplementation((_documentId, versionId) => new Promise((resolve) => {
      if (versionId === 'version-first') resolveFirst = resolve
      else resolveCurrent = resolve
    }))
    renderApp('/projects/project-1/chapters/chapter-1')

    await screen.findByLabelText('Chapter draft')
    fireEvent.click(screen.getByRole('button', { name: 'Version 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Version 2' }))
    resolveCurrent(documentContent({ version_id: 'version-current', content: 'Version B content' }))
    expect(await screen.findByText('Version B content')).toBeInTheDocument()

    resolveFirst(documentContent({ version_id: 'version-first', content: 'Version A content' }))
    await waitFor(() => expect(screen.getByText('Version B content')).toBeInTheDocument())
    expect(screen.queryByText('Version A content')).not.toBeInTheDocument()
    expect(screen.getByText('Viewing immutable server version 2.')).toBeInTheDocument()
  })

  it('renders only the allowed server-returned metadata for an immutable version view', async () => {
    mockDocumentWorkspace()
    mockedApi.listDocumentVersions.mockResolvedValue([
      Object.assign(documentVersion({
        id: 'version-first',
        version_number: 1,
        created_at: '2026-07-18T12:34:56Z',
        source: 'writer_agent',
        change_summary: 'Tightened the opening scene.',
        file_path: 'private/drafts/first.md',
        content_hash: 'private-content-hash',
      }), { raw_error: 'private server error', unexpected_field: 'private arbitrary value' }),
    ])
    mockedApi.readDocumentVersionContent.mockResolvedValue(documentContent({
      version_id: 'version-first', content: 'First server revision',
    }))
    renderApp('/projects/project-1/chapters/chapter-1')

    await screen.findByLabelText('Chapter draft')
    fireEvent.click(screen.getByRole('button', { name: 'Version 1' }))

    const versionView = await screen.findByRole('region', { name: 'Immutable version details' })
    expect(versionView).toHaveTextContent(/Version\s*1/)
    expect(versionView).toHaveTextContent('2026-07-18T12:34:56Z')
    expect(versionView).toHaveTextContent('writer_agent')
    expect(versionView).toHaveTextContent('Tightened the opening scene.')
    expect(versionView).not.toHaveTextContent(/private\/drafts|private-content-hash|private server error|private arbitrary value/)
  })

  it('renders Markdown as text and ignores stale document responses after navigation', async () => {
    let resolveContent!: (content: DocumentContent) => void
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.getChapter.mockImplementation(async (_projectId, id) => chapter({
      id, title: id, current_draft_document_id: id === 'chapter-a' ? 'document-a' : 'document-b',
    }))
    mockedApi.getDocument.mockImplementation(async (id) => document({ id, title: id, current_version_id: `${id}-version`, current_version: documentVersion({ id: `${id}-version`, document_id: id }) }))
    mockedApi.listDocumentVersions.mockResolvedValue([])
    mockedApi.readDocumentContent.mockImplementation((id) => id === 'document-a'
      ? new Promise((resolve) => { resolveContent = resolve })
      : Promise.resolve(documentContent({ document_id: id, version_id: `${id}-version`, content: 'Safe document B' })))
    const view = render(<MemoryRouter initialEntries={['/projects/project-1/chapters/chapter-a']}><RouteTransition path="/projects/project-1/chapters/chapter-b" /></MemoryRouter>)

    await screen.findByRole('heading', { name: 'chapter-a' })
    view.rerender(<MemoryRouter initialEntries={['/projects/project-1/chapters/chapter-a']}><RouteTransition path="/projects/project-1/chapters/chapter-b" /></MemoryRouter>)
    fireEvent.click(screen.getByRole('link', { name: 'Navigate test route' }))
    expect(await screen.findByDisplayValue('Safe document B')).toBeInTheDocument()
    resolveContent(documentContent({ document_id: 'document-a', content: '<img src=x onerror=alert(1)>' }))
    await waitFor(() => expect(screen.queryByDisplayValue(/<img/)).not.toBeInTheDocument())
    expect(globalThis.document.querySelector('img')).toBeNull()
  })
})
