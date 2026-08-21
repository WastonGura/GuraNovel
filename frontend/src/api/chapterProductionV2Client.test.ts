// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import {
  decodeChapterActionKind,
  decodeChapterFailureCode,
  decodeChapterProductionFinalized,
  decodeChapterProductionRunSummary,
  decodeChapterProductionStarted,
  decodeChapterProductionState,
  decodeChapterProductionStatus,
  decodeChapterProductionUpdated,
  finalizeChapterProduction,
  getChapterProductionRun,
  listChapterProductionRuns,
  reconcileChapterProduction,
  resolveChapterProductionAction,
  resumeChapterProduction,
  startChapterProductionV2,
  triggerChapterReview,
  validateContentHash,
  validateIsoTimestamp,
  validateUuid,
} from './chapterProductionV2Client'

const ids = {
  project: '11111111-1111-4111-8111-111111111111',
  chapter: '22222222-2222-4222-8222-222222222222',
  run: '33333333-3333-4333-8333-333333333333',
  action: '44444444-4444-4444-8444-444444444444',
  document: '55555555-5555-4555-8555-555555555555',
  version: '66666666-6666-4666-8666-666666666666',
  finalDoc: '77777777-7777-4777-8777-777777777777',
  finalVer: '88888888-8888-4888-8888-888888888888',
  report: '99999999-9999-4999-8999-999999999999',
  chiefReport: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  loreReport: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  segment: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
}

const sampleHash = 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2'

function sampleValidState(): Record<string, unknown> {
  return {
    chapter_workflow_run_id: ids.run,
    chapter_id: ids.chapter,
    status: 'AUTHOR_REVISION',
    current_node: 'author_revision',
    awaiting_user: true,
    review_policy_version: 'chapter-quality-v1',
    chief_editor_required: true,
    document_id: ids.document,
    document_version_id: ids.version,
    content_hash: sampleHash,
    editor_report_id: ids.report,
    chief_editor_report_id: ids.chiefReport,
    lore_report_id: ids.loreReport,
    action_request_id: ids.action,
    action_kind: 'author_revision',
    failed_from_status: null,
    failure_code: null,
  }
}

function sampleValidSummary(): Record<string, unknown> {
  return {
    workflow_run_id: ids.run,
    project_id: ids.project,
    chapter_id: ids.chapter,
    status: 'DRAFTING',
    current_node: 'drafting',
    started_at: '2026-08-21T10:00:00Z',
    updated_at: '2026-08-21T10:05:00Z',
  }
}

function sampleValidStarted(): Record<string, unknown> {
  return {
    workflow_run_id: ids.run,
    action_request_id: ids.action,
    outline_document_id: ids.document,
    outline_version_id: ids.version,
    draft_document_id: ids.finalDoc,
    draft_version_id: ids.finalVer,
  }
}

function sampleValidUpdated(): Record<string, unknown> {
  return {
    workflow_run_id: ids.run,
    draft_document_id: ids.document,
    draft_version_id: ids.version,
    action_request_id: ids.action,
  }
}

function sampleValidFinalized(): Record<string, unknown> {
  return {
    workflow_run_id: ids.run,
    final_document_id: ids.finalDoc,
    final_version_id: ids.finalVer,
  }
}

function mockJsonResponse(body: unknown, status = 200): void {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status })))
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('Chapter Production V2 Decoders', () => {
  describe('validateUuid', () => {
    it('accepts valid UUIDs in canonical lowercase format', () => {
      expect(validateUuid(ids.project)).toBe(ids.project)
      expect(validateUuid('ABCDEF01-2345-6789-ABCD-EF0123456789')).toBe('abcdef01-2345-6789-abcd-ef0123456789')
    })

    it('rejects nil UUIDs and malformed UUIDs', () => {
      expect(() => validateUuid('00000000-0000-0000-0000-000000000000')).toThrow()
      expect(() => validateUuid('not-a-uuid')).toThrow()
      expect(() => validateUuid(123)).toThrow()
      expect(() => validateUuid(null)).toThrow()
    })
  })

  describe('validateIsoTimestamp', () => {
    it('accepts valid ISO-8601 strings', () => {
      expect(validateIsoTimestamp('2026-08-21T10:00:00Z')).toBe('2026-08-21T10:00:00Z')
      expect(validateIsoTimestamp('2026-08-21T10:00:00.123+09:00')).toBe('2026-08-21T10:00:00.123+09:00')
    })

    it('rejects non-ISO date strings or invalid dates', () => {
      expect(() => validateIsoTimestamp('2026-02-31T10:00:00Z')).toThrow()
      expect(() => validateIsoTimestamp('2026/08/21 10:00:00')).toThrow()
      expect(() => validateIsoTimestamp('just now')).toThrow()
    })
  })

  describe('validateContentHash', () => {
    it('accepts valid 64-character lowercase hex sha256 hashes or null', () => {
      expect(validateContentHash(sampleHash)).toBe(sampleHash)
      expect(validateContentHash(null)).toBeNull()
      expect(validateContentHash(undefined)).toBeNull()
    })

    it('rejects non-sha256 or bad length hashes', () => {
      expect(() => validateContentHash('short')).toThrow()
      expect(() => validateContentHash(sampleHash.toUpperCase())).toThrow()
      expect(() => validateContentHash('not-hex-at-all-0000000000000000000000000000000000000000000000000000000')).toThrow()
    })
  })

  describe('decodeChapterProductionStatus', () => {
    it('accepts all known production statuses', () => {
      const statuses = [
        'DRAFTING', 'AUTHOR_REVISION', 'EDITOR_REVIEW', 'REVIEW_REVISION',
        'CHIEF_FINAL_REVIEW', 'LORE_FINAL_REVIEW', 'REVISION_READY',
        'ARCHIVE_UPDATE', 'COMPLETED', 'CANCELLED', 'FAILED',
      ]
      for (const s of statuses) {
        expect(decodeChapterProductionStatus(s)).toBe(s)
      }
    })

    it('rejects unknown status strings', () => {
      expect(() => decodeChapterProductionStatus('UNKNOWN_STATUS')).toThrow()
      expect(() => decodeChapterProductionStatus(null)).toThrow()
    })
  })

  describe('decodeChapterActionKind', () => {
    it('accepts known action kinds or null', () => {
      expect(decodeChapterActionKind('author_revision')).toBe('author_revision')
      expect(decodeChapterActionKind('review_warning')).toBe('review_warning')
      expect(decodeChapterActionKind('review_revision')).toBe('review_revision')
      expect(decodeChapterActionKind(null)).toBeNull()
      expect(decodeChapterActionKind(undefined)).toBeNull()
    })

    it('rejects unknown action kinds', () => {
      expect(() => decodeChapterActionKind('custom_action')).toThrow()
    })
  })

  describe('decodeChapterFailureCode', () => {
    it('accepts known failure codes or null', () => {
      const codes = [
        'provider_unavailable', 'provider_timeout', 'invalid_provider_output',
        'document_commit_indeterminate', 'persistence_unavailable',
        'archive_unavailable', 'reconciliation_required',
      ]
      for (const c of codes) {
        expect(decodeChapterFailureCode(c)).toBe(c)
      }
      expect(decodeChapterFailureCode(null)).toBeNull()
    })

    it('rejects unknown failure codes', () => {
      expect(() => decodeChapterFailureCode('unknown_failure')).toThrow()
    })
  })

  describe('decodeChapterProductionState', () => {
    it('decodes a valid state object', () => {
      const input = sampleValidState()
      const decoded = decodeChapterProductionState(input)
      expect(decoded.chapter_workflow_run_id).toBe(ids.run)
      expect(decoded.status).toBe('AUTHOR_REVISION')
      expect(decoded.awaiting_user).toBe(true)
      expect(decoded.content_hash).toBe(sampleHash)
    })

    it('decodes a state with nullable fields set to null', () => {
      const input = {
        ...sampleValidState(),
        document_id: null,
        document_version_id: null,
        content_hash: null,
        editor_report_id: null,
        chief_editor_report_id: null,
        lore_report_id: null,
        action_request_id: null,
        action_kind: null,
        failed_from_status: null,
        failure_code: null,
      }
      const decoded = decodeChapterProductionState(input)
      expect(decoded.document_id).toBeNull()
      expect(decoded.action_kind).toBeNull()
    })

    it('fails closed on unknown properties', () => {
      const input = { ...sampleValidState(), internal_secret: 'leaked' }
      expect(() => decodeChapterProductionState(input)).toThrow()
    })

    it('fails closed on missing required fields', () => {
      const input = sampleValidState()
      delete input.chapter_workflow_run_id
      expect(() => decodeChapterProductionState(input)).toThrow()
    })

    it('fails closed on invalid UUID in state', () => {
      const input = { ...sampleValidState(), chapter_workflow_run_id: 'bad-uuid' }
      expect(() => decodeChapterProductionState(input)).toThrow()
    })
  })

  describe('decodeChapterProductionRunSummary', () => {
    it('decodes a valid run summary', () => {
      const input = sampleValidSummary()
      const decoded = decodeChapterProductionRunSummary(input)
      expect(decoded.workflow_run_id).toBe(ids.run)
      expect(decoded.status).toBe('DRAFTING')
    })

    it('fails closed on unknown keys', () => {
      const input = { ...sampleValidSummary(), extra: 1 }
      expect(() => decodeChapterProductionRunSummary(input)).toThrow()
    })
  })

  describe('decodeChapterProductionStarted', () => {
    it('decodes a valid started response', () => {
      const input = sampleValidStarted()
      const decoded = decodeChapterProductionStarted(input)
      expect(decoded.workflow_run_id).toBe(ids.run)
      expect(decoded.action_request_id).toBe(ids.action)
    })

    it('fails closed on bad UUID', () => {
      const input = { ...sampleValidStarted(), action_request_id: 'bad' }
      expect(() => decodeChapterProductionStarted(input)).toThrow()
    })
  })

  describe('decodeChapterProductionUpdated', () => {
    it('decodes a valid updated response', () => {
      const input = sampleValidUpdated()
      const decoded = decodeChapterProductionUpdated(input)
      expect(decoded.workflow_run_id).toBe(ids.run)
      expect(decoded.action_request_id).toBe(ids.action)
    })

    it('decodes updated response with null action_request_id', () => {
      const input = { ...sampleValidUpdated(), action_request_id: null }
      const decoded = decodeChapterProductionUpdated(input)
      expect(decoded.action_request_id).toBeNull()
    })
  })

  describe('decodeChapterProductionFinalized', () => {
    it('decodes a valid finalized response', () => {
      const input = sampleValidFinalized()
      const decoded = decodeChapterProductionFinalized(input)
      expect(decoded.workflow_run_id).toBe(ids.run)
      expect(decoded.final_document_id).toBe(ids.finalDoc)
    })
  })
})

describe('Chapter Production V2 API Client', () => {
  it('starts chapter production V2 and validates returned shape', async () => {
    const started = sampleValidStarted()
    mockJsonResponse(started, 201)

    const result = await startChapterProductionV2(ids.project, ids.chapter)
    expect(result).toEqual(started)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2/start`,
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        body: '{}',
      }),
    )
  })

  it('lists chapter production runs with pagination options', async () => {
    const summaries = [sampleValidSummary()]
    mockJsonResponse(summaries)

    const result = await listChapterProductionRuns(ids.project, ids.chapter, { offset: 10, limit: 5 })
    expect(result).toEqual(summaries)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2?offset=10&limit=5`,
      expect.objectContaining({ method: 'GET', credentials: 'same-origin' }),
    )
  })

  it('gets chapter production run state', async () => {
    const state = sampleValidState()
    mockJsonResponse(state)

    const result = await getChapterProductionRun(ids.project, ids.chapter, ids.run)
    expect(result).toEqual(state)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2/${ids.run}`,
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('resumes drafting on a chapter production run', async () => {
    const started = sampleValidStarted()
    mockJsonResponse(started)

    const result = await resumeChapterProduction(ids.project, ids.chapter, ids.run)
    expect(result).toEqual(started)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2/${ids.run}/resume`,
      expect.objectContaining({ method: 'POST', body: '{}' }),
    )
  })

  it('resolves action with accept decision', async () => {
    const updated = sampleValidUpdated()
    mockJsonResponse(updated)

    const result = await resolveChapterProductionAction(ids.project, ids.chapter, ids.run, ids.action, {
      decision: 'accept',
    })
    expect(result).toEqual(updated)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2/${ids.run}/actions/${ids.action}/resolve`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ decision: 'accept' }),
      }),
    )
  })

  it('resolves action with feedback revision and segment targets', async () => {
    const updated = sampleValidUpdated()
    mockJsonResponse(updated)

    const result = await resolveChapterProductionAction(ids.project, ids.chapter, ids.run, ids.action, {
      decision: 'request_feedback_revision',
      feedback: 'Expand the combat description',
      target_segment_ids: [ids.segment],
    })
    expect(result).toEqual(updated)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2/${ids.run}/actions/${ids.action}/resolve`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          decision: 'request_feedback_revision',
          feedback: 'Expand the combat description',
          target_segment_ids: [ids.segment],
        }),
      }),
    )
  })

  it('resolves action with manual edit content', async () => {
    const updated = sampleValidUpdated()
    mockJsonResponse(updated)

    const result = await resolveChapterProductionAction(ids.project, ids.chapter, ids.run, ids.action, {
      decision: 'submit_manual_edit',
      content: '# Direct Edit\n\nFixed text.\n',
    })
    expect(result).toEqual(updated)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2/${ids.run}/actions/${ids.action}/resolve`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          decision: 'submit_manual_edit',
          content: '# Direct Edit\n\nFixed text.\n',
        }),
      }),
    )
  })

  it('resolves action with request review revision and report IDs', async () => {
    const updated = sampleValidUpdated()
    mockJsonResponse(updated)

    const result = await resolveChapterProductionAction(ids.project, ids.chapter, ids.run, ids.action, {
      decision: 'request_review_revision',
      report_ids: [ids.report],
      target_segment_ids: [ids.segment],
    })
    expect(result).toEqual(updated)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2/${ids.run}/actions/${ids.action}/resolve`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          decision: 'request_review_revision',
          target_segment_ids: [ids.segment],
          report_ids: [ids.report],
        }),
      }),
    )
  })

  it('triggers chapter review', async () => {
    const updated = sampleValidUpdated()
    mockJsonResponse(updated)

    const result = await triggerChapterReview(ids.project, ids.chapter, ids.run)
    expect(result).toEqual(updated)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2/${ids.run}/review`,
      expect.objectContaining({ method: 'POST', body: '{}' }),
    )
  })

  it('finalizes chapter production', async () => {
    const finalized = sampleValidFinalized()
    mockJsonResponse(finalized)

    const result = await finalizeChapterProduction(ids.project, ids.chapter, ids.run)
    expect(result).toEqual(finalized)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2/${ids.run}/finalize`,
      expect.objectContaining({ method: 'POST', body: '{}' }),
    )
  })

  it('reconciles indeterminate chapter production state', async () => {
    const state = sampleValidState()
    mockJsonResponse(state)

    const result = await reconcileChapterProduction(ids.project, ids.chapter, ids.run)
    expect(result).toEqual(state)
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2/${ids.run}/reconcile`,
      expect.objectContaining({ method: 'POST', body: '{}' }),
    )
  })

  it('forwards AbortSignal for cancellation', async () => {
    mockJsonResponse(sampleValidState())
    const controller = new AbortController()

    await getChapterProductionRun(ids.project, ids.chapter, ids.run, controller.signal)

    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it('sanitizes server error responses without exposing raw provider traces', async () => {
    mockJsonResponse(
      {
        error: {
          code: 'raw_provider_timeout',
          message: 'Provider timed out on model gpt-4 in region us-central1',
        },
      },
      503,
    )

    const error = await getChapterProductionRun(ids.project, ids.chapter, ids.run).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 503,
      code: 'raw_provider_timeout',
      message: 'Provider timed out on model gpt-4 in region us-central1',
    })
  })

  it('handles 409 conflict safely', async () => {
    mockJsonResponse({ error: { code: 'chapter_production_v2_reconciliation_required', message: 'Chapter production requires explicit reconciliation.' } }, 409)

    await expect(getChapterProductionRun(ids.project, ids.chapter, ids.run)).rejects.toMatchObject({
      status: 409,
      code: 'chapter_production_v2_reconciliation_required',
    })
  })
})
