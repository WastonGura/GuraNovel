import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Chapter, Project } from './api/client'
import App from './App'

vi.mock('./api/client', () => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  getProject: vi.fn(),
  listChapters: vi.fn(),
  createChapter: vi.fn(),
  getChapter: vi.fn(),
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

function Location() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
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
})
