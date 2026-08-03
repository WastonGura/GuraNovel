import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Link, MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Project, ProjectMaintenanceRun } from './api/client'
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
  startChapterProduction: vi.fn(),
  resolveChapterProductionAction: vi.fn(),
  startProjectMaintenance: vi.fn(),
  getProjectMaintenanceRun: vi.fn(),
  resolveProjectMaintenanceAction: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    code: string
    constructor(status: number, code: string, message: string) {
      super(message)
      this.status = status
      this.code = code
    }
  },
}))

import * as api from './api/client'

const mockedApi = vi.mocked(api)
const ids = {
  run: '11111111-1111-4111-8111-111111111111',
  action: '22222222-2222-4222-8222-222222222222',
  change: '33333333-3333-4333-8333-333333333333',
  plan: '44444444-4444-4444-8444-444444444444',
  planDocument: '55555555-5555-4555-8555-555555555555',
  planVersion: '66666666-6666-4666-8666-666666666666',
  affectedChapter: '77777777-7777-4777-8777-777777777777',
  affectedStyle: '88888888-8888-4888-8888-888888888888',
  chapterDocument: '99999999-9999-4999-8999-999999999999',
  chapterVersion: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  styleDocument: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  styleVersion: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  operationChapter: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  operationStyle: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
}

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: 'project-1', slug: 'archive-of-ash', title: 'Archive of Ash', genre: null,
    target_platform: null, status: 'draft', workspace_root: '/workspace/archive-of-ash', metadata: {},
    created_at: '2026-07-19T00:00:00Z', updated_at: '2026-07-19T00:00:00Z', ...overrides,
  }
}

function run(overrides: Partial<ProjectMaintenanceRun> = {}): ProjectMaintenanceRun {
  return {
    id: ids.run,
    maintenance_change_id: ids.change,
    type: 'project_maintenance',
    status: 'USER_CONFIRMATION',
    current_node: 'user_confirm_revision',
    next_node: null,
    awaiting_user: true,
    title: 'Move the reveal earlier',
    change_request: 'Move the identity reveal into chapter three while preserving the timeline.',
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:01:00Z',
    completed_at: null,
    affected_items: [
      {
        id: ids.affectedChapter, position: 0, type: 'chapter', stable_reference: 'chapter/three',
        impact_level: 'high', reason: 'The reveal scene must move.', document_id: ids.chapterDocument,
        chapter_id: 'abababab-abab-4bab-8bab-abababababab',
      },
      {
        id: ids.affectedStyle, position: 1, type: 'style', stable_reference: 'style/suspense',
        impact_level: 'low', reason: 'Earlier clues need a lighter touch.', document_id: ids.styleDocument,
        chapter_id: null,
      },
    ],
    revision_plan: {
      id: ids.plan,
      document_id: ids.planDocument,
      version_id: ids.planVersion,
      review_outcome: 'passed',
      summary: 'Revise the reveal and retain the established voice.',
      operations: [
        {
          id: ids.operationChapter, sequence: 1, operation: 'revise', document_id: ids.chapterDocument,
          expected_version_id: ids.chapterVersion, affected_item_ids: [ids.affectedChapter],
          instruction: 'Move the reveal scene without changing the event order.',
        },
        {
          id: ids.operationStyle, sequence: 2, operation: 'retain', document_id: ids.styleDocument,
          expected_version_id: ids.styleVersion, affected_item_ids: [ids.affectedStyle],
          instruction: 'Keep the current prose style.',
        },
      ],
    },
    consistency_review: null,
    applied_document_version_ids: [],
    pending_action: {
      id: ids.action,
      type: 'project_maintenance_revision_confirmation',
      status: 'pending',
      confirmation_kind: 'revision_confirmation',
      review_outcome: 'passed',
      allowed_decisions: ['approve', 'revise', 'cancel'],
    },
    ...overrides,
  }
}

function consistencyWarningRun(overrides: Partial<ProjectMaintenanceRun> = {}): ProjectMaintenanceRun {
  return run({
    pending_action: {
      ...run().pending_action!,
      type: 'project_maintenance_consistency_warning',
      confirmation_kind: 'consistency_warning',
      review_outcome: 'warning',
      allowed_decisions: ['accept_warning', 'revise'],
    },
    ...overrides,
  })
}

function Location() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderApp(path: string, extra?: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      {extra}
      <App />
      <Location />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('project maintenance entry and start', () => {
  it('adds a visible project-workspace entry and routes to a labelled bounded form', async () => {
    mockedApi.getProject.mockResolvedValue(project())
    mockedApi.listChapters.mockResolvedValue([])
    const view = renderApp('/projects/project-1')

    const entry = await screen.findByRole('link', { name: 'Project maintenance' })
    expect(entry).toHaveAttribute('href', '/projects/project-1/maintenance/start')
    fireEvent.click(entry)

    expect(await screen.findByRole('heading', { name: 'Plan a project change' })).toBeInTheDocument()
    expect(screen.getByRole('form', { name: 'Start project maintenance' })).toBeInTheDocument()
    expect(screen.getByLabelText('Change title')).toHaveAttribute('maxlength', '512')
    expect(screen.getByLabelText('Change request')).toHaveAttribute('maxlength', '4000')
    expect(screen.getAllByRole('checkbox')).toHaveLength(7)
    expect(screen.getByRole('main')).toContainElement(screen.getByRole('form', { name: 'Start project maintenance' }))
    view.unmount()
  })

  it('submits trimmed content and unique optional scope hints once, then uses the server run ID', async () => {
    let resolveStart!: (value: ProjectMaintenanceRun) => void
    mockedApi.startProjectMaintenance.mockReturnValue(new Promise((resolve) => { resolveStart = resolve }))
    mockedApi.getProjectMaintenanceRun.mockResolvedValue(run())
    renderApp('/projects/project-1/maintenance/start')

    fireEvent.change(screen.getByLabelText('Change title'), { target: { value: '  Move the reveal earlier  ' } })
    fireEvent.change(screen.getByLabelText('Change request'), { target: { value: '  Preserve the timeline.  ' } })
    fireEvent.click(screen.getByLabelText('Chapters'))
    fireEvent.click(screen.getByLabelText('Style'))
    const submit = screen.getByRole('button', { name: 'Analyze change' })
    fireEvent.click(submit)
    fireEvent.click(submit)

    expect(mockedApi.startProjectMaintenance).toHaveBeenCalledTimes(1)
    expect(mockedApi.startProjectMaintenance).toHaveBeenCalledWith('project-1', {
      title: 'Move the reveal earlier',
      change_request: 'Preserve the timeline.',
      scope_hints: ['chapter', 'style'],
    }, expect.any(AbortSignal))
    expect(submit).toBeDisabled()
    resolveStart(run())
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(
      `/projects/project-1/maintenance/${ids.run}`,
    ))
  })

  it('resets and aborts start work when the project route changes', async () => {
    let resolveOld!: (value: ProjectMaintenanceRun) => void
    let oldSignal: AbortSignal | undefined
    mockedApi.startProjectMaintenance.mockImplementation((_projectId, _request, signal) => {
      oldSignal = signal
      return new Promise((resolve) => { resolveOld = resolve })
    })
    renderApp(
      '/projects/project-a/maintenance/start',
      <Link to="/projects/project-b/maintenance/start">Switch project</Link>,
    )

    fireEvent.change(screen.getByLabelText('Change title'), { target: { value: 'Project A title' } })
    fireEvent.change(screen.getByLabelText('Change request'), { target: { value: 'Project A request' } })
    fireEvent.click(screen.getByRole('button', { name: 'Analyze change' }))
    fireEvent.click(screen.getByRole('link', { name: 'Switch project' }))

    await waitFor(() => expect(oldSignal?.aborted).toBe(true))
    expect(screen.getByLabelText('Change title')).toHaveValue('')
    expect(screen.getByLabelText('Change request')).toHaveValue('')
    resolveOld(run())
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/projects/project-b/maintenance/start'))
  })
})

describe('project maintenance analysis and confirmation', () => {
  it('reconstructs a deep link and shows safe analysis progress without internal node data', async () => {
    mockedApi.getProjectMaintenanceRun.mockResolvedValue(run({
      status: 'LORE_IMPACT_ANALYSIS', current_node: 'private_lore_agent_node', awaiting_user: false,
      affected_items: [], revision_plan: null, pending_action: null,
    }))
    renderApp(`/projects/project-1/maintenance/${ids.run}`)

    expect(await screen.findByText('Reviewing story-world impact')).toBeInTheDocument()
    expect(mockedApi.getProjectMaintenanceRun).toHaveBeenCalledWith('project-1', ids.run, expect.any(AbortSignal))
    expect(screen.queryByText('private_lore_agent_node')).not.toBeInTheDocument()
    expect(screen.queryByText(/identity reveal into chapter three/)).not.toBeInTheDocument()
  })

  it.each([
    ['passed', 'Ready for your decision'],
    ['warning', 'Review warnings before deciding'],
    ['blocking', 'Revision required'],
  ] as const)('renders %s plan outcome with affected groups, operations, and fixed rollback guidance', async (outcome, label) => {
    const pending = run().pending_action!
    mockedApi.getProjectMaintenanceRun.mockResolvedValue(run({
      revision_plan: { ...run().revision_plan!, review_outcome: outcome },
      pending_action: {
        ...pending,
        review_outcome: outcome,
        allowed_decisions: outcome === 'blocking' ? ['revise', 'cancel'] : ['approve', 'revise', 'cancel'],
      },
    }))
    renderApp(`/projects/project-1/maintenance/${ids.run}`)

    expect(await screen.findByText(label)).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Affected chapters' })).toHaveTextContent('High impact')
    expect(screen.getByRole('region', { name: 'Affected style' })).toHaveTextContent('Low impact')
    expect(screen.getByText('Revise the reveal and retain the established voice.')).toBeInTheDocument()
    expect(screen.getByText('Move the reveal scene without changing the event order.')).toBeInTheDocument()
    expect(screen.getByText('Step 1')).toBeInTheDocument()
    expect(screen.getByText('Step 2')).toBeInTheDocument()
    expect(screen.getByText(`Target document ${ids.chapterDocument}`)).toBeInTheDocument()
    expect(screen.getByText(/No documents change from this screen until you approve/)).toBeInTheDocument()
    if (outcome === 'blocking') expect(screen.queryByRole('button', { name: 'Approve plan' })).not.toBeInTheDocument()
  })

  it('renders only approve, revision, and cancel decisions returned by the live pending action', async () => {
    mockedApi.getProjectMaintenanceRun.mockResolvedValue(run({
      pending_action: { ...run().pending_action!, allowed_decisions: ['revise'] },
    }))
    renderApp(`/projects/project-1/maintenance/${ids.run}`)

    expect(await screen.findByRole('button', { name: 'Request revision' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve plan' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel change' })).not.toBeInTheDocument()
  })

  it('shows a clear empty affected-items state during valid analysis', async () => {
    mockedApi.getProjectMaintenanceRun.mockResolvedValue(run({
      status: 'CHIEF_EDITOR_IMPACT_ANALYSIS',
      current_node: 'chief_editor_impact_review',
      awaiting_user: false,
      affected_items: [],
      revision_plan: null,
      pending_action: null,
    }))
    renderApp(`/projects/project-1/maintenance/${ids.run}`)

    expect(await screen.findByText('No affected items have been identified yet.')).toBeInTheDocument()
  })

  it.each([
    ['revise', 'Request revision'],
    ['cancel', 'Cancel change'],
  ] as const)('submits the exact live %s decision', async (decision, label) => {
    const liveRun = run()
    mockedApi.getProjectMaintenanceRun.mockResolvedValue(liveRun)
    mockedApi.resolveProjectMaintenanceAction.mockResolvedValue(run({
      status: decision === 'cancel' ? 'CANCELLED' : 'REVISION_PLAN',
      current_node: decision === 'cancel' ? 'cancel_maintenance' : 'build_revision_plan',
      awaiting_user: false,
      completed_at: decision === 'cancel' ? '2026-08-03T00:02:00Z' : null,
      pending_action: null,
    }))
    renderApp(`/projects/project-1/maintenance/${ids.run}`)

    fireEvent.click(await screen.findByRole('button', { name: label }))
    await waitFor(() => expect(mockedApi.resolveProjectMaintenanceAction).toHaveBeenCalledWith(
      'project-1', liveRun, decision, expect.any(AbortSignal),
    ))
  })

  it('submits one exact live decision and hands off without claiming the project was updated', async () => {
    let resolveDecision!: (value: ProjectMaintenanceRun) => void
    const liveRun = run()
    mockedApi.getProjectMaintenanceRun
      .mockResolvedValueOnce(liveRun)
      .mockResolvedValue(run({ status: 'APPLY_CHANGE', current_node: 'apply_revision', awaiting_user: false, pending_action: null }))
    mockedApi.resolveProjectMaintenanceAction.mockReturnValue(new Promise((resolve) => { resolveDecision = resolve }))
    renderApp(`/projects/project-1/maintenance/${ids.run}`)

    const approve = await screen.findByRole('button', { name: 'Approve plan' })
    fireEvent.click(approve)
    fireEvent.click(approve)
    expect(mockedApi.resolveProjectMaintenanceAction).toHaveBeenCalledTimes(1)
    expect(mockedApi.resolveProjectMaintenanceAction).toHaveBeenCalledWith(
      'project-1', liveRun, 'approve', expect.any(AbortSignal),
    )
    expect(screen.queryByRole('button', { name: 'Approve plan' })).not.toBeInTheDocument()

    resolveDecision(run({ status: 'APPLY_CHANGE', current_node: 'apply_revision_plan', awaiting_user: false, pending_action: null }))
    expect(await screen.findByRole('heading', { name: 'Decision recorded' })).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(`/projects/project-1/maintenance/${ids.run}/status`)
    expect(screen.getByText(/This gate does not report apply progress or claim that project documents changed/)).toBeInTheDocument()
    expect(screen.queryByText(/project updated/i)).not.toBeInTheDocument()
  })

  it('verifies a status deep link before claiming that a decision was recorded', async () => {
    mockedApi.getProjectMaintenanceRun.mockResolvedValue(run({
      status: 'LORE_IMPACT_ANALYSIS',
      current_node: 'lore_impact_analysis',
      awaiting_user: false,
      affected_items: [],
      revision_plan: null,
      pending_action: null,
    }))

    renderApp(`/projects/project-1/maintenance/${ids.run}/status`)

    expect(await screen.findByRole('heading', { name: 'Maintenance analysis continues' })).toHaveFocus()
    expect(mockedApi.getProjectMaintenanceRun).toHaveBeenCalledWith('project-1', ids.run, expect.any(AbortSignal))
    expect(screen.queryByRole('heading', { name: 'Decision recorded' })).not.toBeInTheDocument()
  })

  it('keeps a later consistency-warning gate in the safe status handoff', async () => {
    mockedApi.getProjectMaintenanceRun.mockResolvedValue(consistencyWarningRun())

    renderApp(`/projects/project-1/maintenance/${ids.run}/status`)

    expect(await screen.findByRole('heading', { name: 'Additional review is required' })).toHaveFocus()
    expect(screen.getByTestId('location')).toHaveTextContent(`/projects/project-1/maintenance/${ids.run}/status`)
    expect(screen.queryByRole('button', { name: 'Request revision' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Revision plan' })).not.toBeInTheDocument()
  })

  it('clears a prior handoff when the status route identity changes', async () => {
    const nextRunId = 'ffffffff-ffff-4fff-8fff-ffffffffffff'
    mockedApi.getProjectMaintenanceRun.mockImplementation((_projectId, runId) => {
      if (runId === ids.run) {
        return Promise.resolve(run({
          status: 'APPLY_CHANGE', current_node: 'apply_revision', awaiting_user: false, pending_action: null,
        }))
      }
      return Promise.reject(new api.ApiError(404, 'not_found', 'raw missing response'))
    })
    renderApp(
      `/projects/project-1/maintenance/${ids.run}/status`,
      <Link to={`/projects/project-2/maintenance/${nextRunId}/status`}>Switch status</Link>,
    )

    expect(await screen.findByRole('heading', { name: 'Decision recorded' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: 'Switch status' }))

    expect(await screen.findByRole('alert')).toHaveFocus()
    expect(screen.queryByRole('heading', { name: 'Decision recorded' })).not.toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(`/projects/project-2/maintenance/${nextRunId}/status`)
  })

  it('restores focus to the reconstructed confirmation heading', async () => {
    mockedApi.getProjectMaintenanceRun.mockResolvedValue(run())

    renderApp(`/projects/project-1/maintenance/${ids.run}`)

    expect(await screen.findByRole('heading', { name: 'Move the reveal earlier' })).toHaveFocus()
  })
})

describe('project maintenance safe failures and stale work', () => {
  it.each([
    [new api.ApiError(409, 'workflow_state_error', 'raw conflict payload'), 'This maintenance decision is stale. Reload the current gate and try again.'],
    [new api.ApiError(404, 'not_found', 'raw missing payload'), 'This maintenance request was not found. Return to the project workspace and start again if needed.'],
    [new api.ApiError(0, 'request_failed', 'provider URL and credentials'), 'Maintenance could not be loaded. Check your connection and try again.'],
    [new api.ApiError(0, 'invalid_response', 'raw invalid JSON'), 'Maintenance is in an invalid state. Return to the project workspace and try again later.'],
  ])('uses fixed focused error copy for %s', async (failure, safeCopy) => {
    mockedApi.getProjectMaintenanceRun.mockRejectedValue(failure)
    renderApp(`/projects/project-1/maintenance/${ids.run}`)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(safeCopy)
    expect(alert).toHaveFocus()
    expect(alert).not.toHaveTextContent(/raw|provider|credentials|JSON/)
  })

  it('consumes the local decision capability when resolution becomes stale', async () => {
    const liveRun = run()
    const refreshedRun = run({
      pending_action: { ...run().pending_action!, allowed_decisions: ['revise'] },
    })
    mockedApi.getProjectMaintenanceRun
      .mockResolvedValueOnce(liveRun)
      .mockResolvedValueOnce(refreshedRun)
    mockedApi.resolveProjectMaintenanceAction.mockRejectedValue(
      new api.ApiError(409, 'workflow_state_error', 'raw conflict payload'),
    )
    renderApp(`/projects/project-1/maintenance/${ids.run}`)

    fireEvent.click(await screen.findByRole('button', { name: 'Approve plan' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveFocus()
    expect(screen.queryByRole('button', { name: 'Approve plan' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Request revision' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('button', { name: 'Request revision' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve plan' })).not.toBeInTheDocument()
  })

  it('hands an indeterminate approval retry with a consistency warning to status', async () => {
    const warningRun = consistencyWarningRun()
    mockedApi.getProjectMaintenanceRun
      .mockResolvedValueOnce(run())
      .mockResolvedValue(warningRun)
    mockedApi.resolveProjectMaintenanceAction.mockRejectedValue(
      new api.ApiError(0, 'request_failed', 'raw timeout after commit'),
    )
    renderApp(`/projects/project-1/maintenance/${ids.run}`)

    fireEvent.click(await screen.findByRole('button', { name: 'Approve plan' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('heading', { name: 'Additional review is required' })).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(`/projects/project-1/maintenance/${ids.run}/status`)
    expect(screen.queryByRole('heading', { name: 'Revision plan' })).not.toBeInTheDocument()
  })

  it('suppresses a late route response and aborts in-flight work on unmount', async () => {
    let resolveOld!: (value: ProjectMaintenanceRun) => void
    let oldSignal: AbortSignal | undefined
    mockedApi.getProjectMaintenanceRun.mockImplementation((_projectId, runId, signal) => {
      if (runId === ids.run) {
        oldSignal = signal
        return new Promise((resolve) => { resolveOld = resolve })
      }
      return Promise.resolve(run({ id: 'ffffffff-ffff-4fff-8fff-ffffffffffff', title: 'Current route title' }))
    })
    const view = renderApp(
      `/projects/project-1/maintenance/${ids.run}`,
      <Link to="/projects/project-1/maintenance/ffffffff-ffff-4fff-8fff-ffffffffffff">New route</Link>,
    )
    fireEvent.click(screen.getByRole('link', { name: 'New route' }))
    expect(await screen.findByRole('heading', { name: 'Current route title' })).toBeInTheDocument()
    expect(oldSignal?.aborted).toBe(true)

    resolveOld(run({ title: 'Stale route title' }))
    await waitFor(() => expect(screen.queryByText('Stale route title')).not.toBeInTheDocument())
    view.unmount()
  })
})
