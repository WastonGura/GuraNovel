import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  getProjectMaintenanceRun,
  listProjectMaintenanceRuns,
  resolveProjectMaintenanceAction,
  startProjectMaintenance,
  type StartProjectMaintenanceRequest,
} from './client'

const ids = {
  run: '11111111-1111-4111-8111-111111111111',
  change: '22222222-2222-4222-8222-222222222222',
  affected: '33333333-3333-4333-8333-333333333333',
  document: '44444444-4444-4444-8444-444444444444',
  version: '55555555-5555-4555-8555-555555555555',
  plan: '66666666-6666-4666-8666-666666666666',
  operation: '77777777-7777-4777-8777-777777777777',
  action: '88888888-8888-4888-8888-888888888888',
  applied: '99999999-9999-4999-8999-999999999999',
  review: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  finding: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  affectedSecond: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  documentSecond: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  operationSecond: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
  versionSecond: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
}

function confirmationRun(): Record<string, unknown> {
  return {
    id: ids.run,
    maintenance_change_id: ids.change,
    type: 'project_maintenance',
    status: 'USER_CONFIRMATION',
    current_node: 'user_confirm_revision',
    next_node: null,
    awaiting_user: true,
    title: 'Retcon the world rule',
    change_request: 'Preserve history while changing the rule.',
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:01:00Z',
    completed_at: null,
    affected_items: [{
      id: ids.affected,
      position: 0,
      type: 'world',
      stable_reference: 'world/world-rule',
      impact_level: 'high',
      reason: 'The rule affects the established setting.',
      document_id: ids.document,
      chapter_id: null,
    }],
    revision_plan: {
      id: ids.plan,
      document_id: ids.document,
      version_id: ids.version,
      review_outcome: 'passed',
      summary: 'Revise the rule without changing prior events.',
      operations: [{
        id: ids.operation,
        sequence: 1,
        operation: 'revise',
        document_id: ids.document,
        expected_version_id: ids.version,
        affected_item_ids: [ids.affected],
        instruction: 'Clarify the rule and retain the existing timeline.',
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
  }
}

function consistencyReview(outcome: 'clean' | 'warning' | 'blocking'): Record<string, unknown> {
  const findings = outcome === 'clean' ? [] : [{
    id: ids.finding,
    sequence: 1,
    code: outcome === 'warning' ? 'timeline_warning' : 'timeline_blocker',
    severity: outcome,
    blocking: outcome === 'blocking',
    affected_documents: [{ document_id: ids.document, version_id: ids.applied }],
    suggested_corrective_action: 'Reconcile the timeline before publishing.',
  }]
  return { id: ids.review, outcome, findings }
}

function statusRun(status: string): Record<string, unknown> {
  const value = confirmationRun()
  value.status = status
  value.current_node = ({
    CHANGE_REQUESTED: 'user_change_request',
    LORE_IMPACT_ANALYSIS: 'lore_impact_analysis',
    CHIEF_EDITOR_IMPACT_ANALYSIS: 'chief_editor_impact_review',
    REVISION_PLAN: 'revision_plan',
    USER_CONFIRMATION: 'user_confirm_revision',
    APPLY_CHANGE: 'apply_revision',
    CONSISTENCY_REVIEW: 'consistency_review',
    PROJECT_UPDATED: 'project_updated',
    CANCELLED: 'cancelled',
  } as Record<string, string>)[status]
  if (status !== 'USER_CONFIRMATION') {
    value.awaiting_user = false
    value.pending_action = null
  }
  if (['CHANGE_REQUESTED', 'LORE_IMPACT_ANALYSIS', 'CHIEF_EDITOR_IMPACT_ANALYSIS'].includes(status)) {
    value.affected_items = []
    value.revision_plan = null
  }
  if (status === 'REVISION_PLAN') value.revision_plan = null
  if (['CONSISTENCY_REVIEW', 'PROJECT_UPDATED'].includes(status)) {
    value.applied_document_version_ids = [ids.applied]
  }
  if (status === 'PROJECT_UPDATED') {
    value.consistency_review = consistencyReview('clean')
    value.completed_at = '2026-08-03T00:02:00Z'
  }
  if (status === 'CANCELLED') value.completed_at = '2026-08-03T00:02:00Z'
  return value
}

function mockJsonResponse(body: unknown, status = 200): void {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status })))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('project maintenance API client', () => {
  it('strictly decodes a valid confirmation run', async () => {
    const run = confirmationRun()
    mockJsonResponse(run)

    await expect(getProjectMaintenanceRun('project one', ids.run)).resolves.toEqual(run)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/project%20one/maintenance/${ids.run}`,
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    )
  })

  it('forwards cancellation signals to the shared transport', async () => {
    mockJsonResponse(confirmationRun())
    const controller = new AbortController()

    await getProjectMaintenanceRun('project-1', ids.run, controller.signal)

    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it('fails closed on unexpected nested fields', async () => {
    const run = confirmationRun()
    const pending = run.pending_action as Record<string, unknown>
    pending.raw_model_output = 'private provider output'
    mockJsonResponse(run)

    await expect(getProjectMaintenanceRun('project-1', ids.run)).rejects.toMatchObject({
      code: 'invalid_response',
      message: 'The server returned an invalid response.',
    })
  })

  it('normalizes and allowlists the start body', async () => {
    mockJsonResponse(confirmationRun(), 201)
    const payload = {
      title: '  Retcon the world rule  ',
      change_request: '  Preserve history.  ',
      scope_hints: ['world'],
      provider: 'must-not-be-sent',
    } as unknown as StartProjectMaintenanceRequest

    await startProjectMaintenance('project/one', payload)

    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/projects/project%2Fone/maintenance/start',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          title: 'Retcon the world rule',
          change_request: 'Preserve history.',
          scope_hints: ['world'],
        }),
      }),
    )
  })

  it('binds resolution to the decoded pending action and rejects undeclared decisions', async () => {
    mockJsonResponse(confirmationRun())
    const run = await getProjectMaintenanceRun('project-1', ids.run)

    await expect(resolveProjectMaintenanceAction('project-1', run, 'accept_warning')).rejects.toMatchObject({
      code: 'invalid_request',
    })

    mockJsonResponse(confirmationRun())
    await resolveProjectMaintenanceAction('project-1', run, 'approve')
    expect(fetch).toHaveBeenLastCalledWith(
      `/api/v1/projects/project-1/maintenance/${ids.run}/actions/${ids.action}/resolve`,
      expect.objectContaining({ method: 'POST', body: '{"decision":"approve"}' }),
    )
  })

  it('does not expose server error messages or details', async () => {
    mockJsonResponse({
      error: {
        code: 'workflow_state_error',
        message: 'Raw prompt and C:\\private\\workspace',
        details: { provider_endpoint: 'https://private.example' },
      },
    }, 409)

    const error = await getProjectMaintenanceRun('project-1', ids.run).catch((reason: unknown) => reason)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 409,
      code: 'workflow_state_error',
      message: 'The project maintenance state changed. Refresh and try again.',
    })
    expect(String(error)).not.toContain('private')
    expect(String(error)).not.toContain('prompt')
  })

  it('maps an unknown error code to a fixed safe category', async () => {
    mockJsonResponse({ error: { code: 'private_backend_category', message: 'private body', details: null } }, 422)

    await expect(getProjectMaintenanceRun('project-1', ids.run)).rejects.toMatchObject({
      status: 422,
      code: 'validation_error',
      message: 'The project maintenance request could not be processed.',
    })
  })

  it('fully decodes list entries before returning bounded history summaries', async () => {
    mockJsonResponse([confirmationRun()])

    await expect(listProjectMaintenanceRuns('project-1', { offset: 2, limit: 10 })).resolves.toEqual([{
      id: ids.run,
      maintenance_change_id: ids.change,
      status: 'USER_CONFIRMATION',
      title: 'Retcon the world rule',
      awaiting_user: true,
      created_at: '2026-08-03T00:00:00Z',
      updated_at: '2026-08-03T00:01:00Z',
      completed_at: null,
    }])
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/projects/project-1/maintenance?offset=2&limit=10',
      expect.anything(),
    )
  })

  it.each([
    'CHANGE_REQUESTED',
    'LORE_IMPACT_ANALYSIS',
    'CHIEF_EDITOR_IMPACT_ANALYSIS',
    'REVISION_PLAN',
    'USER_CONFIRMATION',
    'APPLY_CHANGE',
    'CONSISTENCY_REVIEW',
    'PROJECT_UPDATED',
    'CANCELLED',
  ])('decodes the %s status fixture', async (status) => {
    mockJsonResponse(statusRun(status))

    await expect(getProjectMaintenanceRun('project-1', ids.run)).resolves.toMatchObject({ status })
  })

  it.each([
    { outcome: 'warning', applied: false, decisions: ['approve', 'revise', 'cancel'] },
    { outcome: 'blocking', applied: false, decisions: ['revise', 'cancel'] },
    { outcome: 'passed', applied: true, decisions: ['approve', 'revise'] },
    { outcome: 'blocking', applied: true, decisions: ['revise'] },
  ] as const)('decodes the revision gate %#', async ({ outcome, applied, decisions }) => {
    const value = confirmationRun()
    const plan = value.revision_plan as Record<string, unknown>
    const pending = value.pending_action as Record<string, unknown>
    plan.review_outcome = outcome
    pending.review_outcome = outcome
    pending.allowed_decisions = [...decisions]
    if (applied) {
      value.applied_document_version_ids = [ids.applied]
      value.consistency_review = consistencyReview('blocking')
    }
    mockJsonResponse(value)

    await expect(getProjectMaintenanceRun('project-1', ids.run)).resolves.toMatchObject({
      pending_action: { review_outcome: outcome, allowed_decisions: [...decisions] },
    })
  })

  it('decodes the consistency-warning gate', async () => {
    const value = confirmationRun()
    value.applied_document_version_ids = [ids.applied]
    value.consistency_review = consistencyReview('warning')
    value.pending_action = {
      id: ids.action,
      type: 'project_maintenance_consistency_warning',
      status: 'pending',
      confirmation_kind: 'consistency_warning',
      review_outcome: 'warning',
      allowed_decisions: ['accept_warning', 'revise'],
    }
    mockJsonResponse(value)

    await expect(getProjectMaintenanceRun('project-1', ids.run)).resolves.toMatchObject({
      pending_action: { confirmation_kind: 'consistency_warning' },
    })
  })

  it.each([
    ['status', (value: Record<string, unknown>) => { value.status = 'SKIP_CONFIRMATION' }],
    ['affected item', (value: Record<string, unknown>) => {
      const affected = (value.affected_items as Record<string, unknown>[])[0]
      affected.reason = 'Read C:\\private\\workspace\\draft.md'
    }],
    ['revision operation', (value: Record<string, unknown>) => {
      const plan = value.revision_plan as Record<string, unknown>
      const operation = (plan.operations as Record<string, unknown>[])[0]
      operation.affected_item_ids = ['cccccccc-cccc-4ccc-8ccc-cccccccccccc']
    }],
    ['pending action', (value: Record<string, unknown>) => {
      const pending = value.pending_action as Record<string, unknown>
      pending.allowed_decisions = ['approve', 'force_apply']
    }],
    ['consistency finding', (value: Record<string, unknown>) => {
      value.applied_document_version_ids = [ids.applied]
      value.consistency_review = consistencyReview('warning')
      const review = value.consistency_review as Record<string, unknown>
      const finding = (review.findings as Record<string, unknown>[])[0]
      finding.raw_provider_output = 'private'
    }],
  ] as const)('fails closed on a malformed %s', async (_kind, mutate) => {
    const value = confirmationRun()
    mutate(value)
    mockJsonResponse(value)

    await expect(getProjectMaintenanceRun('project-1', ids.run)).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it.each([
    'X-Provider-Request-Id: req_abc123',
    'OperationalError: connection refused on internal host db-primary port 5432',
    'Model gpt-5 in region us-east-1',
    'Error: connection refused',
    'Exception: raw provider failure',
    'Server: nginx internal build',
    'Content-Type: application/json',
    'Provider OpenAI model gpt-5 region us-east-1',
    'Details X-Provider-Request-Id: req_abc123',
    'Details OperationalError: connection refused',
    'Details Server: nginx internal build',
    'ERROR: connection refused',
    'EXCEPTION: raw provider failure',
    'Server: uvicorn',
    'Server: Caddy',
    'Details provider=OpenAI model=gpt-5 region=us-east-1',
    'Details model: claude-sonnet in region us-east-1',
    'Details region=us-east-1',
    'Serving model claude-sonnet in region us-east-1',
    'Server: Werkzeug',
    'Details Server: openresty internal build',
    'Details region=us-central1',
    'Serving model claude-sonnet in region us-central1',
    'Serving model gemini in region europe-west1',
    'Details Error: connection refused',
    'Provider details EXCEPTION: upstream failed',
    'Server: uvicorn 0.30.1',
    'Details Server: Caddy 2',
    'Server: openresty 1.25.3.1',
    'Details model=gemini-2',
    'Provider details model=claude-sonnet',
    'Details region=me-central1',
    'Serving model gemini in region africa-south1',
    'Details error: connection refused',
    'Provider details exception: upstream failed',
    'Details server: uvicorn 0.30.1',
    'Provider details model=deepseek-v3',
    'Details model=phi-4',
    'Details region=eastus',
    'Serving model deepseek-v3 in region eastus',
  ])('rejects unsafe public provider text: %s', async (unsafeText) => {
    const value = confirmationRun()
    const affected = (value.affected_items as Record<string, unknown>[])[0]
    affected.reason = unsafeText
    mockJsonResponse(value)

    await expect(getProjectMaintenanceRun('project-1', ids.run)).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it.each([
    'Move the conflict to region: north.',
    'Character model: reluctant host.',
    'Terror: spreads through the court.',
    'Host: the banquet begins at dusk.',
    'Region: northern kingdom remains stable.',
    'The robot is model T-800 in region north.',
    'Fix the continuity error: the heroine arrives before the letter.',
    'Her fatal error: trusting the envoy.',
    'The continuity error: chapter three contradicts chapter two.',
    'Editorial error: reveal timing weakens the ending.',
    'Character model: Gemini twins with opposing goals.',
    'The tavern server: Mira.',
    'Change the server: Alice.',
    'The cookie: a clue at the crime scene.',
    'The server: Alice.',
  ])('allows normal public story text: %s', async (safeText) => {
    const value = confirmationRun()
    const affected = (value.affected_items as Record<string, unknown>[])[0]
    affected.reason = safeText
    mockJsonResponse(value)

    await expect(getProjectMaintenanceRun('project-1', ids.run)).resolves.toMatchObject({
      affected_items: [expect.objectContaining({ reason: safeText })],
    })
  })

  it('rejects a forged or stale run capability before resolving', async () => {
    mockJsonResponse(confirmationRun())
    const run = await getProjectMaintenanceRun('project-1', ids.run)
    ;(run.pending_action as unknown as Record<string, unknown>).internal_document_id = ids.document

    await expect(resolveProjectMaintenanceAction('project-1', run, 'approve')).rejects.toMatchObject({
      code: 'invalid_response',
    })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['timestamps', (value: Record<string, unknown>) => { value.updated_at = '2026-08-02T23:59:00Z' }],
    ['gate review binding', (value: Record<string, unknown>) => {
      const pending = value.pending_action as Record<string, unknown>
      pending.review_outcome = 'warning'
    }],
    ['required plan', (value: Record<string, unknown>) => {
      value.status = 'APPLY_CHANGE'
      value.current_node = 'apply_revision'
      value.awaiting_user = false
      value.pending_action = null
      value.revision_plan = null
    }],
    ['applied review binding', (value: Record<string, unknown>) => {
      value.applied_document_version_ids = [ids.applied]
      value.consistency_review = consistencyReview('warning')
      const review = value.consistency_review as Record<string, unknown>
      const finding = (review.findings as Record<string, unknown>[])[0]
      finding.affected_documents = [{
        document_id: ids.document,
        version_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      }]
      value.pending_action = {
        id: ids.action,
        type: 'project_maintenance_consistency_warning',
        status: 'pending',
        confirmation_kind: 'consistency_warning',
        review_outcome: 'warning',
        allowed_decisions: ['accept_warning', 'revise'],
      }
    }],
  ] as const)('rejects inconsistent %s', async (_kind, mutate) => {
    const value = confirmationRun()
    mutate(value)
    mockJsonResponse(value)

    await expect(getProjectMaintenanceRun('project-1', ids.run)).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it.each([
    ['incomplete affected-item coverage', (value: Record<string, unknown>) => {
      const affected = value.affected_items as Record<string, unknown>[]
      affected.push({
        ...affected[0],
        id: ids.affectedSecond,
        position: 1,
        stable_reference: 'world/second-rule',
      })
    }],
    ['cross-document affected-item binding', (value: Record<string, unknown>) => {
      const plan = value.revision_plan as Record<string, unknown>
      const operation = (plan.operations as Record<string, unknown>[])[0]
      operation.document_id = ids.documentSecond
    }],
  ] as const)('rejects revision plan %s', async (_kind, mutate) => {
    const value = confirmationRun()
    mutate(value)
    mockJsonResponse(value)

    await expect(getProjectMaintenanceRun('project-1', ids.run)).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('accepts an unbound affected item used by multiple target documents', async () => {
    const value = confirmationRun()
    const affected = (value.affected_items as Record<string, unknown>[])[0]
    affected.document_id = null
    const plan = value.revision_plan as Record<string, unknown>
    const operations = plan.operations as Record<string, unknown>[]
    operations.push({
      ...operations[0],
      id: ids.operationSecond,
      sequence: 2,
      document_id: ids.documentSecond,
      expected_version_id: ids.versionSecond,
    })
    mockJsonResponse(value)

    await expect(getProjectMaintenanceRun('project-1', ids.run)).resolves.toMatchObject({
      revision_plan: { operations: [{ sequence: 1 }, { sequence: 2 }] },
    })
  })

  it.each([
    ['early applied versions', (value: Record<string, unknown>) => {
      Object.assign(value, statusRun('CHANGE_REQUESTED'))
      value.applied_document_version_ids = [ids.applied]
    }],
    ['consistency without applied versions', (value: Record<string, unknown>) => {
      Object.assign(value, statusRun('CONSISTENCY_REVIEW'))
      value.applied_document_version_ids = []
    }],
    ['apply with a consistency review', (value: Record<string, unknown>) => {
      Object.assign(value, statusRun('APPLY_CHANGE'))
      value.applied_document_version_ids = [ids.applied]
      value.consistency_review = consistencyReview('warning')
    }],
    ['corrective revision without its originating review', (value: Record<string, unknown>) => {
      Object.assign(value, statusRun('REVISION_PLAN'))
      value.applied_document_version_ids = [ids.applied]
      value.consistency_review = null
    }],
    ['corrective confirmation without its originating review', (value: Record<string, unknown>) => {
      value.applied_document_version_ids = [ids.applied]
      value.consistency_review = null
      const pending = value.pending_action as Record<string, unknown>
      pending.allowed_decisions = ['approve', 'revise']
    }],
    ['apply with a blocking plan', (value: Record<string, unknown>) => {
      Object.assign(value, statusRun('APPLY_CHANGE'))
      const plan = value.revision_plan as Record<string, unknown>
      plan.review_outcome = 'blocking'
    }],
  ] as const)('rejects impossible lifecycle state: %s', async (_kind, mutate) => {
    const value = confirmationRun()
    mutate(value)
    mockJsonResponse(value)

    await expect(getProjectMaintenanceRun('project-1', ids.run)).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
