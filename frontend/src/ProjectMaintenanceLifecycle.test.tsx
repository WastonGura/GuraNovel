import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ProjectMaintenanceRun } from './api/client'
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
  listProjectMaintenanceRuns: vi.fn(),
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
  affected: '44444444-4444-4444-8444-444444444444',
  document: '55555555-5555-4555-8555-555555555555',
  expectedVersion: '66666666-6666-4666-8666-666666666666',
  plan: '77777777-7777-4777-8777-777777777777',
  planDocument: '88888888-8888-4888-8888-888888888888',
  planVersion: '99999999-9999-4999-8999-999999999999',
  operation: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  review: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  appliedVersion: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  olderRun: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  olderChange: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
}

const sentinels = [
  'private-change-request-never-render',
  'sk-test-never-render-12345678',
  'C:\\private\\maintenance\\artifact.json',
  'Authorization: Bearer never-render',
  'provider=private-model region=us-secret-1',
]

function maintenanceRun(overrides: Partial<ProjectMaintenanceRun> = {}): ProjectMaintenanceRun {
  return {
    id: ids.run,
    maintenance_change_id: ids.change,
    type: 'project_maintenance',
    status: 'USER_CONFIRMATION',
    current_node: 'user_confirm_revision',
    next_node: null,
    awaiting_user: true,
    title: 'Move the reveal earlier',
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:01:00Z',
    completed_at: null,
    affected_items: [{
      id: ids.affected,
      position: 0,
      type: 'chapter',
      stable_reference: 'chapter/three',
      impact_level: 'high',
      reason: 'The reveal scene must move.',
      document_id: ids.document,
      chapter_id: null,
    }],
    revision_plan: {
      id: ids.plan,
      document_id: ids.planDocument,
      version_id: ids.planVersion,
      review_outcome: 'passed',
      summary: 'Move the reveal and retain the established timeline.',
      operations: [{
        id: ids.operation,
        sequence: 1,
        operation: 'revise',
        document_id: ids.document,
        expected_version_id: ids.expectedVersion,
        affected_item_ids: [ids.affected],
        instruction: 'Move the reveal without changing event order.',
      }],
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

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>)
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.resetAllMocks()
})

describe('project maintenance lifecycle', () => {
  it('keeps one live capability across start, approval, completion, history, and status remount', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const confirmation = maintenanceRun()
    const applying = maintenanceRun({
      status: 'APPLY_CHANGE', current_node: 'apply_revision', awaiting_user: false, pending_action: null,
    })
    const reviewing = maintenanceRun({
      status: 'CONSISTENCY_REVIEW', current_node: 'consistency_review', awaiting_user: false,
      pending_action: null, applied_document_version_ids: [ids.appliedVersion],
    })
    const updated = maintenanceRun({
      status: 'PROJECT_UPDATED', current_node: 'project_updated', awaiting_user: false, pending_action: null,
      applied_document_version_ids: [ids.appliedVersion],
      consistency_review: { id: ids.review, outcome: 'clean', findings: [] },
      updated_at: '2026-08-03T00:04:00Z', completed_at: '2026-08-03T00:04:00Z',
    })
    const polled = [confirmation, applying, reviewing, updated]
    let pollIndex = 0
    mockedApi.startProjectMaintenance.mockResolvedValue(confirmation)
    mockedApi.resolveProjectMaintenanceAction.mockResolvedValue(applying)
    mockedApi.getProjectMaintenanceRun.mockImplementation(async () => polled[Math.min(pollIndex++, polled.length - 1)])
    mockedApi.listProjectMaintenanceRuns.mockResolvedValue([
      { id: ids.run, maintenance_change_id: ids.change, status: 'PROJECT_UPDATED', title: 'Newest server run', awaiting_user: false, created_at: confirmation.created_at, updated_at: updated.updated_at, completed_at: updated.completed_at },
      { id: ids.olderRun, maintenance_change_id: ids.olderChange, status: 'CANCELLED', title: 'Older server run', awaiting_user: false, created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:01:00Z', completed_at: '2026-08-02T00:01:00Z' },
    ])

    const view = renderAt('/projects/project-1/maintenance/start')
    fireEvent.change(screen.getByLabelText('Change title'), { target: { value: ' Move the reveal earlier ' } })
    fireEvent.change(screen.getByLabelText('Change request'), { target: { value: ` ${sentinels[0]} ` } })
    const analyze = screen.getByRole('button', { name: 'Analyze change' })
    fireEvent.click(analyze)
    fireEvent.click(analyze)

    const approve = await screen.findByRole('button', { name: 'Approve plan' })
    expect(mockedApi.startProjectMaintenance).toHaveBeenCalledTimes(1)
    expect(mockedApi.startProjectMaintenance).toHaveBeenCalledWith('project-1', {
      title: 'Move the reveal earlier',
      change_request: sentinels[0],
      scope_hints: [],
    }, expect.any(AbortSignal))
    fireEvent.click(approve)
    fireEvent.click(approve)
    expect(mockedApi.resolveProjectMaintenanceAction).toHaveBeenCalledTimes(1)
    expect(mockedApi.resolveProjectMaintenanceAction).toHaveBeenCalledWith(
      'project-1', confirmation, 'approve', expect.any(AbortSignal),
    )

    expect(await screen.findByRole('heading', { name: 'Applying approved changes' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Project updated' })).not.toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500) })
    expect(await screen.findByRole('heading', { name: 'Reviewing project consistency' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Project updated' })).not.toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(1_500) })
    expect(await screen.findByRole('heading', { name: 'Project updated' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: 'Back to maintenance history' }))
    const history = await screen.findByRole('list')
    const historyLinks = within(history).getAllByRole('link')
    expect(historyLinks.map((link) => link.textContent)).toEqual(['Newest server run', 'Older server run'])
    fireEvent.click(historyLinks[0])
    expect(await screen.findByRole('heading', { name: 'Project updated' })).toBeInTheDocument()

    view.unmount()
    renderAt(`/projects/project-1/maintenance/${ids.run}/status`)
    expect(await screen.findByRole('heading', { name: 'Project updated' })).toBeInTheDocument()
    expect(mockedApi.startProjectMaintenance).toHaveBeenCalledTimes(1)
    expect(mockedApi.resolveProjectMaintenanceAction).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(mockedApi.getProjectMaintenanceRun).toHaveBeenCalledWith(
      'project-1', ids.run, expect.any(AbortSignal),
    ))
    for (const sentinel of sentinels) expect(document.body).not.toHaveTextContent(sentinel)
  }, 10_000)
})
