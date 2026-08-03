import { describe, expect, it, vi } from 'vitest'
import type { ProjectMaintenanceRun } from './client'
import { ProjectMaintenanceQuery } from './projectMaintenanceQuery'

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
} {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function run(id: string, status: ProjectMaintenanceRun['status']): ProjectMaintenanceRun {
  return {
    id,
    maintenance_change_id: '22222222-2222-4222-8222-222222222222',
    type: 'project_maintenance',
    status,
    current_node: status === 'USER_CONFIRMATION' ? 'user_confirm_revision' : 'lore_impact_analysis',
    next_node: null,
    awaiting_user: status === 'USER_CONFIRMATION',
    title: 'Retcon the rule',
    change_request: 'Preserve the timeline.',
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:01:00Z',
    completed_at: null,
    affected_items: [],
    revision_plan: null,
    consistency_review: null,
    applied_document_version_ids: [],
    pending_action: null,
  }
}

describe('ProjectMaintenanceQuery', () => {
  it('suppresses late success and failure after route identity changes', async () => {
    const first = deferred<ProjectMaintenanceRun>()
    const second = deferred<ProjectMaintenanceRun>()
    const load = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise)
    const updates: string[] = []
    const errors: unknown[] = []
    const query = new ProjectMaintenanceQuery(load)

    const oldPoll = query.poll(
      { projectId: 'project-a', workflowRunId: 'run-a' },
      { maxAttempts: 1, intervalMs: 0, onUpdate: (value) => updates.push(value.id), onError: (error) => errors.push(error) },
    )
    const currentPoll = query.poll(
      { projectId: 'project-b', workflowRunId: 'run-b' },
      { maxAttempts: 1, intervalMs: 0, onUpdate: (value) => updates.push(value.id), onError: (error) => errors.push(error) },
    )
    second.resolve(run('run-b', 'USER_CONFIRMATION'))
    await expect(currentPoll).resolves.toBe('completed')
    first.reject(new Error('private late failure'))
    await expect(oldPoll).resolves.toBe('cancelled')

    expect(updates).toEqual(['run-b'])
    expect(errors).toEqual([])
  })

  it('aborts the active request and suppresses a late result when cancelled', async () => {
    const pending = deferred<ProjectMaintenanceRun>()
    let signal: AbortSignal | undefined
    const load = vi.fn((_projectId: string, _runId: string, requestSignal?: AbortSignal) => {
      signal = requestSignal
      return pending.promise
    })
    const onUpdate = vi.fn()
    const query = new ProjectMaintenanceQuery(load)

    const polling = query.poll(
      { projectId: 'project-a', workflowRunId: 'run-a' },
      { maxAttempts: 1, intervalMs: 0, onUpdate },
    )
    query.cancel()
    expect(signal?.aborted).toBe(true)
    pending.resolve(run('run-a', 'USER_CONFIRMATION'))

    await expect(polling).resolves.toBe('cancelled')
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it('runs requests serially and stops at the bounded maximum attempt count', async () => {
    let active = 0
    let maximumActive = 0
    const load = vi.fn(async () => {
      active += 1
      maximumActive = Math.max(maximumActive, active)
      await Promise.resolve()
      active -= 1
      return run('run-a', 'LORE_IMPACT_ANALYSIS')
    })
    const onUpdate = vi.fn()
    const query = new ProjectMaintenanceQuery(load)

    await expect(query.poll(
      { projectId: 'project-a', workflowRunId: 'run-a' },
      { maxAttempts: 3, intervalMs: 0, onUpdate },
    )).resolves.toBe('max_attempts')

    expect(load).toHaveBeenCalledTimes(3)
    expect(onUpdate).toHaveBeenCalledTimes(3)
    expect(maximumActive).toBe(1)
  })

  it.each(['USER_CONFIRMATION', 'PROJECT_UPDATED', 'CANCELLED'] as const)(
    'stops immediately at %s',
    async (status) => {
      const load = vi.fn().mockResolvedValue(run('run-a', status))
      const query = new ProjectMaintenanceQuery(load)

      await expect(query.poll(
        { projectId: 'project-a', workflowRunId: 'run-a' },
        { maxAttempts: 5, intervalMs: 0, onUpdate: vi.fn() },
      )).resolves.toBe('completed')
      expect(load).toHaveBeenCalledTimes(1)
    },
  )

  it('keeps the replacement controller cancellable when onUpdate starts a new poll', async () => {
    const replacement = deferred<ProjectMaintenanceRun>()
    let replacementSignal: AbortSignal | undefined
    const load = vi.fn()
      .mockResolvedValueOnce(run('run-a', 'USER_CONFIRMATION'))
      .mockImplementationOnce((_projectId: string, _runId: string, signal?: AbortSignal) => {
        replacementSignal = signal
        return replacement.promise
      })
    const query = new ProjectMaintenanceQuery(load)
    let replacementPoll: Promise<string> | undefined

    const originalPoll = query.poll(
      { projectId: 'project-a', workflowRunId: 'run-a' },
      {
        maxAttempts: 1,
        intervalMs: 0,
        onUpdate: () => {
          replacementPoll = query.poll(
            { projectId: 'project-b', workflowRunId: 'run-b' },
            { maxAttempts: 1, intervalMs: 0, onUpdate: vi.fn() },
          )
        },
      },
    )

    await expect(originalPoll).resolves.toBe('cancelled')
    query.cancel()
    expect(replacementSignal?.aborted).toBe(true)
    replacement.resolve(run('run-b', 'USER_CONFIRMATION'))
    await expect(replacementPoll).resolves.toBe('cancelled')
  })

  it('keeps the replacement controller cancellable when onError starts a new poll', async () => {
    const replacement = deferred<ProjectMaintenanceRun>()
    let replacementSignal: AbortSignal | undefined
    const load = vi.fn()
      .mockRejectedValueOnce(new Error('private failure'))
      .mockImplementationOnce((_projectId: string, _runId: string, signal?: AbortSignal) => {
        replacementSignal = signal
        return replacement.promise
      })
    const query = new ProjectMaintenanceQuery(load)
    let replacementPoll: Promise<string> | undefined

    const originalPoll = query.poll(
      { projectId: 'project-a', workflowRunId: 'run-a' },
      {
        maxAttempts: 1,
        intervalMs: 0,
        onUpdate: vi.fn(),
        onError: () => {
          replacementPoll = query.poll(
            { projectId: 'project-b', workflowRunId: 'run-b' },
            { maxAttempts: 1, intervalMs: 0, onUpdate: vi.fn() },
          )
        },
      },
    )

    await expect(originalPoll).resolves.toBe('cancelled')
    query.cancel()
    expect(replacementSignal?.aborted).toBe(true)
    replacement.resolve(run('run-b', 'USER_CONFIRMATION'))
    await expect(replacementPoll).resolves.toBe('cancelled')
  })
})
