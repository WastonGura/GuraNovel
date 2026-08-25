import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import {
  cancelReaderPanel,
  decodeConcernItem,
  decodeEvidenceRef,
  decodeReactionItem,
  decodeReaderPanelAction,
  decodeReaderPanelBlockingIssue,
  decodeReaderPanelDetail,
  decodeReaderPanelInitialReport,
  decodeReaderPanelIssue,
  decodeReaderPanelMessage,
  decodeReaderPanelReviewReport,
  decodeStrengthItem,
  getReaderPanel,
  listReaderPanels,
  ReaderPanelPoller,
  resumeReaderPanel,
  startReaderPanel,
  validateBoolean,
  validateContentHash,
  validateIsoTimestamp,
  validateNullableIsoTimestamp,
  validateNullableString,
  validateUuid,
  type ReaderPanelSessionDetail,
} from './readerPanelClient'
import { ApiError } from './client'

const validProjectId = '11111111-1111-4111-8111-111111111111'
const validChapterId = '22222222-2222-4222-8222-222222222222'
const validDocId = '33333333-3333-4333-8333-333333333333'
const validDocVerId = '44444444-4444-4444-8444-444444444444'
const validSessionId = '55555555-5555-4555-8555-555555555555'
const validWorkflowRunId = '66666666-6666-4666-8666-666666666666'
const validIssueId = '77777777-7777-4777-8777-777777777777'
const validHash = 'a'.repeat(64)
const validTimestamp = '2026-08-25T10:00:00Z'

const baseSessionDetail: ReaderPanelSessionDetail = {
  is_noop: false,
  session_id: validSessionId,
  workflow_run_id: validWorkflowRunId,
  project_id: validProjectId,
  chapter_id: validChapterId,
  document_id: validDocId,
  document_version_id: validDocVerId,
  source_hash: validHash,
  mode: 'standard',
  status: 'independent_reading',
  stale: false,
  degradation_reason: null,
  failure_reason: null,
  planned_readers: 4,
  completed_readers: 0,
  failed_readers: 0,
  issue_count: 0,
  initial_ballot_count: 0,
  final_ballot_count: 0,
  discussion_message_count: 0,
  created_at: validTimestamp,
  updated_at: validTimestamp,
  completed_at: null,
  review_report: null,
  issues: [],
  initial_reports: null,
  transcript: null,
  permitted_operations: ['cancel'],
}

describe('Reader Panel Decoders', () => {
  describe('Primitives', () => {
    it('validates UUIDs and converts to lowercase', () => {
      expect(validateUuid(validProjectId.toUpperCase())).toBe(validProjectId)
      expect(() => validateUuid('invalid-uuid')).toThrow(ApiError)
      expect(() => validateUuid('00000000-0000-0000-0000-000000000000')).toThrow(ApiError)
    })

    it('validates boolean values', () => {
      expect(validateBoolean(true)).toBe(true)
      expect(validateBoolean(false)).toBe(false)
      expect(() => validateBoolean('true')).toThrow(ApiError)
    })

    it('validates nullable strings', () => {
      expect(validateNullableString(null)).toBeNull()
      expect(validateNullableString('hello')).toBe('hello')
      expect(() => validateNullableString(123)).toThrow(ApiError)
    })

    it('validates ISO-8601 timestamps', () => {
      expect(validateIsoTimestamp(validTimestamp)).toBe(validTimestamp)
      expect(validateIsoTimestamp('2026-08-25T10:00:00.123Z')).toBe('2026-08-25T10:00:00.123Z')
      expect(validateNullableIsoTimestamp(null)).toBeNull()
      expect(() => validateIsoTimestamp('not-a-date')).toThrow(ApiError)
      expect(() => validateIsoTimestamp('2026-02-30T10:00:00Z')).toThrow(ApiError)
    })

    it('validates 64-character SHA-256 hashes', () => {
      expect(validateContentHash(validHash)).toBe(validHash)
      expect(validateContentHash(null)).toBeNull()
      expect(() => validateContentHash('short-hash')).toThrow(ApiError)
      expect(() => validateContentHash('Z'.repeat(64))).toThrow(ApiError)
    })
  })

  describe('Item decoders', () => {
    it('decodes EvidenceRef with segment_ids array and note', () => {
      const valid = { segment_ids: ['seg_01', 'seg_02'], note: 'A detailed observation' }
      expect(decodeEvidenceRef(valid)).toEqual(valid)
      expect(() => decodeEvidenceRef({ segment_ids: [] })).toThrow(ApiError)
      expect(() => decodeEvidenceRef({ segment_ids: ['seg_01'], note: '', extra: 1 })).toThrow(ApiError)
    })

    it('decodes StrengthItem with summary and evidence', () => {
      const valid = {
        summary: 'Pacing is crisp and engaging.',
        evidence: [{ segment_ids: ['seg_01'], note: 'Action flows smoothly.' }],
      }
      expect(decodeStrengthItem(valid)).toEqual(valid)
      expect(() => decodeStrengthItem({ summary: '' })).toThrow(ApiError)
    })

    it('decodes ReactionItem with segment_ids, reaction, emotion, and confusion', () => {
      const valid = {
        segment_ids: ['seg_01'],
        reaction: 'Exciting duel scene',
        emotion: 'thrilled',
        confusion: null,
      }
      expect(decodeReactionItem(valid)).toEqual(valid)
      expect(() => decodeReactionItem({ ...valid, reaction: '' })).toThrow(ApiError)
    })

    it('decodes ConcernItem with severity, category, symptom, evidence, and suggested_action', () => {
      const valid = {
        category: 'plot',
        symptom: 'Pacing slows down in middle segment',
        severity: 'significant',
        evidence: [{ segment_ids: ['seg_02'], note: 'Excessive description' }],
        suggested_action: 'compress',
      }
      expect(decodeConcernItem(valid)).toEqual(valid)
      expect(() => decodeConcernItem({ ...valid, severity: 'fatal' })).toThrow(ApiError)
    })
  })

  describe('Report and Issue decoders', () => {
    it('decodes ReaderPanelAction and ReaderPanelReviewReport', () => {
      const action = {
        priority: 'must_fix',
        target_segment_ids: ['seg_01'],
        suggested_action: 'clarify',
        instruction: 'Sharpen the character motive',
      }
      expect(decodeReaderPanelAction(action)).toEqual(action)

      const blocking = { issue_number: 1, title: 'Inconsistent character motivation' }
      expect(decodeReaderPanelBlockingIssue(blocking)).toEqual(blocking)

      const report = {
        summary: 'Overall strong draft.',
        blocking_issues: [blocking],
        warnings: ['Check chapter hook.'],
        notes: ['Genre tropes are well executed.'],
        suggested_actions: [action],
      }
      expect(decodeReaderPanelReviewReport(report)).toEqual(report)
    })

    it('decodes ReaderPanelInitialReport with exact contract enums', () => {
      const initialReport = {
        overall_reaction: 'Very engaging fantasy introduction.',
        continue_reading: 'yes',
        confidence: 'high',
        strengths: [{ summary: 'The floating spire scene is vivid.', evidence: [] }],
        reactions: [{ segment_ids: ['seg_01'], reaction: 'Magic system reveal', emotion: 'excited', confusion: null }],
        concerns: [
          {
            category: 'exposition',
            symptom: 'Dense lore chunk in segment 2',
            severity: 'minor',
            evidence: [{ segment_ids: ['seg_02'], note: 'Three paragraphs of history' }],
            suggested_action: 'compress',
          },
        ],
      }
      expect(decodeReaderPanelInitialReport(initialReport)).toEqual(initialReport)
    })

    it('decodes ReaderPanelMessage with cross-field stance invariants', () => {
      const readerMsg = {
        issue_id: validIssueId,
        round_number: 1,
        turn_number: 1,
        speaker_type: 'reader',
        stance: 'support',
        claim: 'The pacing drags in the middle.',
        evidence: [{ segment_ids: ['seg_01'], note: 'Long description' }],
        concession: null,
        proposed_action: 'Trim descriptions.',
        novelty: 'new_evidence',
        created_at: validTimestamp,
      }
      expect(decodeReaderPanelMessage(readerMsg)).toEqual(readerMsg)

      // Reader message without stance must fail
      expect(() => decodeReaderPanelMessage({ ...readerMsg, stance: null })).toThrow(ApiError)

      // Moderator message with stance must fail
      const modMsg = {
        ...readerMsg,
        speaker_type: 'moderator',
        stance: null,
      }
      expect(decodeReaderPanelMessage(modMsg)).toEqual(modMsg)
      expect(() => decodeReaderPanelMessage({ ...modMsg, stance: 'support' })).toThrow(ApiError)
    })

    it('decodes ReaderPanelIssue with exact contract fields and enums', () => {
      const issue = {
        issue_number: 1,
        title: 'Protagonist motivation is ambiguous',
        category: 'character',
        symptom: 'Action in segment 3 feels unearned',
        root_cause_hypotheses: ['Missing backstory prompt', 'Unclear goal'],
        evidence: [{ segment_ids: ['seg_03'], note: 'Why did he run?' }],
        target_audience_relevance: 'high',
        minority_risk: false,
        discussion_status: 'closed',
        consensus_class: 'strong_consensus',
        recommended_priority: 'must_fix',
      }
      expect(decodeReaderPanelIssue(issue)).toEqual(issue)
      expect(() => decodeReaderPanelIssue({ ...issue, target_audience_relevance: 'super_high' })).toThrow(ApiError)
    })
  })

  describe('Detail and Discriminated Union', () => {
    it('decodes session detail successfully', () => {
      expect(decodeReaderPanelDetail(baseSessionDetail)).toEqual(baseSessionDetail)
    })

    it('decodes no-op response when mode is off', () => {
      const noOp = {
        is_noop: true,
        session_id: null,
        workflow_run_id: null,
        project_id: validProjectId,
        chapter_id: validChapterId,
        document_id: validDocId,
        document_version_id: validDocVerId,
        source_hash: null,
        mode: 'off',
        status: 'off',
        stale: false,
        degradation_reason: null,
        failure_reason: null,
        planned_readers: 0,
        completed_readers: 0,
        failed_readers: 0,
        issue_count: 0,
        initial_ballot_count: 0,
        final_ballot_count: 0,
        discussion_message_count: 0,
        created_at: null,
        updated_at: null,
        completed_at: null,
        review_report: null,
        issues: [],
        initial_reports: null,
        transcript: null,
        permitted_operations: [],
      }
      const decoded = decodeReaderPanelDetail(noOp)
      expect(decoded.is_noop).toBe(true)
      expect(decoded.mode).toBe('off')
      expect(decoded.status).toBe('off')
    })

    it('fails closed when session has contradictory no-op state', () => {
      // is_noop: true but mode is standard
      expect(() =>
        decodeReaderPanelDetail({
          ...baseSessionDetail,
          is_noop: true,
        }),
      ).toThrow(ApiError)

      // is_noop: false but mode is off
      expect(() =>
        decodeReaderPanelDetail({
          ...baseSessionDetail,
          mode: 'off',
        }),
      ).toThrow(ApiError)
    })

    it('fails closed on unknown keys or negative counters', () => {
      expect(() =>
        decodeReaderPanelDetail({
          ...baseSessionDetail,
          unknown_field: true,
        }),
      ).toThrow(ApiError)

      expect(() =>
        decodeReaderPanelDetail({
          ...baseSessionDetail,
          planned_readers: -1,
        }),
      ).toThrow(ApiError)
    })
  })
})

describe('Reader Panel API Client', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('starts reader panel session with config overrides and exact URL path', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => baseSessionDetail,
    } as Response)

    const result = await startReaderPanel(validProjectId, validChapterId, {
      document_id: validDocId,
      document_version_id: validDocVerId,
      mode: 'standard',
      config_overrides: {
        max_ballot_issues: 4,
        min_valid_readers: 3,
      },
      test_goals: ['Check pacing'],
      target_audience: ['Fantasy fans'],
      idempotency_key: 'start-req-001',
    })

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe(`/api/v1/projects/${validProjectId}/chapters/${validChapterId}/reader-panels`)
    expect(init?.method).toBe('POST')
    expect(init?.credentials).toBe('same-origin')
    expect(result).toEqual(baseSessionDetail)
  })

  it('lists reader panels with query parameters and exact URL path', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [baseSessionDetail],
    } as Response)

    const list = await listReaderPanels(validProjectId, validChapterId, {
      offset: 0,
      limit: 10,
      include_initial_reports: true,
      include_transcript: false,
      data_limit: 25,
    })

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe(
      `/api/v1/projects/${validProjectId}/chapters/${validChapterId}/reader-panels?offset=0&limit=10&include_initial_reports=true&include_transcript=false&data_limit=25`,
    )
    expect(list).toEqual([baseSessionDetail])

    // Invalid client option throws invalid_request ApiError
    expect(() =>
      listReaderPanels(validProjectId, validChapterId, { offset: -1 }),
    ).toThrow(ApiError)
  })

  it('gets a specific reader panel session with exact URL path', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => baseSessionDetail,
    } as Response)

    const result = await getReaderPanel(validProjectId, validChapterId, validSessionId, {
      include_transcript: true,
    })

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe(
      `/api/v1/projects/${validProjectId}/chapters/${validChapterId}/reader-panels/${validSessionId}?include_transcript=true`,
    )
    expect(result).toEqual(baseSessionDetail)
  })

  it('cancels an in-progress reader panel with exact URL path', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ...baseSessionDetail, status: 'cancelled' }),
    } as Response)

    const result = await cancelReaderPanel(validProjectId, validChapterId, validSessionId)
    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe(
      `/api/v1/projects/${validProjectId}/chapters/${validChapterId}/reader-panels/${validSessionId}/cancel`,
    )
    expect(init?.method).toBe('POST')
    expect(result.status).toBe('cancelled')
  })

  it('resumes a paused reader panel with exact URL path', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ...baseSessionDetail, status: 'discussing' }),
    } as Response)

    const result = await resumeReaderPanel(validProjectId, validChapterId, validSessionId)
    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe(
      `/api/v1/projects/${validProjectId}/chapters/${validChapterId}/reader-panels/${validSessionId}/resume`,
    )
    expect(init?.method).toBe('POST')
    expect(result.status).toBe('discussing')
  })

  it('sanitizes server error responses and supports envelope wrapping', async () => {
    const mockFetch = vi.mocked(fetch)
    // 1. Envelope-wrapped error { error: { code, message } }
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: async () => ({
        error: {
          code: 'provider_unavailable',
          message: 'LLM gateway is currently down.',
        },
      }),
    } as Response)

    await expect(
      getReaderPanel(validProjectId, validChapterId, validSessionId),
    ).rejects.toMatchObject({
      status: 503,
      code: 'provider_unavailable',
      message: 'LLM gateway is currently down.',
    })

    // 2. Unsafe error message triggers safe status fallback
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: async () => ({
        code: 'not_found',
        message: 'Stack trace: /var/secrets/db_password.txt line 14\x00',
      }),
    } as Response)

    await expect(
      getReaderPanel(validProjectId, validChapterId, validSessionId),
    ).rejects.toMatchObject({
      status: 404,
      code: 'not_found',
      message: 'The requested reader panel resource was not found.',
    })
  })

  it('propagates AbortSignal for user cancellation', async () => {
    const mockFetch = vi.mocked(fetch)
    mockFetch.mockImplementationOnce((_url, init) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          reject(new DOMException('Aborted', 'AbortError'))
        })
      })
    })

    const controller = new AbortController()
    const promise = getReaderPanel(validProjectId, validChapterId, validSessionId, undefined, controller.signal)
    controller.abort()

    await expect(promise).rejects.toThrow('Aborted')
  })
})

describe('ReaderPanelPoller', () => {
  it('polls until reaching a terminal status', async () => {
    let callCount = 0
    const mockLoader = vi.fn().mockImplementation(async () => {
      callCount += 1
      if (callCount === 1) {
        return { ...baseSessionDetail, status: 'independent_reading' }
      }
      return { ...baseSessionDetail, status: 'completed' }
    })

    const poller = new ReaderPanelPoller(mockLoader)
    const updates: ReaderPanelSessionDetail[] = []

    const result = await poller.poll(
      {
        projectId: validProjectId,
        chapterId: validChapterId,
        sessionId: validSessionId,
      },
      {
        maxAttempts: 5,
        intervalMs: 0,
        onUpdate: (panel) => updates.push(panel),
      },
    )

    expect(result).toBe('completed')
    expect(updates).toHaveLength(2)
    expect(updates[0].status).toBe('independent_reading')
    expect(updates[1].status).toBe('completed')
  })

  it('stops when max attempts are exceeded', async () => {
    const mockLoader = vi.fn().mockResolvedValue({
      ...baseSessionDetail,
      status: 'discussing',
    })

    const poller = new ReaderPanelPoller(mockLoader)
    const updates: ReaderPanelSessionDetail[] = []

    const result = await poller.poll(
      {
        projectId: validProjectId,
        chapterId: validChapterId,
        sessionId: validSessionId,
      },
      {
        maxAttempts: 3,
        intervalMs: 0,
        onUpdate: (panel) => updates.push(panel),
      },
    )

    expect(result).toBe('max_attempts')
    expect(updates).toHaveLength(3)
    expect(mockLoader).toHaveBeenCalledTimes(3)
  })

  it('handles loader errors gracefully', async () => {
    const mockLoader = vi.fn().mockRejectedValue(new ApiError(500, 'server_error', 'Failed'))
    const poller = new ReaderPanelPoller(mockLoader)
    const errors: ApiError[] = []

    const result = await poller.poll(
      {
        projectId: validProjectId,
        chapterId: validChapterId,
        sessionId: validSessionId,
      },
      {
        maxAttempts: 3,
        intervalMs: 0,
        onUpdate: () => {},
        onError: (err) => errors.push(err),
      },
    )

    expect(result).toBe('failed')
    expect(errors).toHaveLength(1)
    expect(errors[0].code).toBe('server_error')
  })

  it('cancels polling when cancel() is invoked', async () => {
    const poller = new ReaderPanelPoller(async () => {
      // Simulate delay
      await new Promise((r) => setTimeout(r, 50))
      return { ...baseSessionDetail, status: 'discussing' }
    })

    const pollPromise = poller.poll(
      {
        projectId: validProjectId,
        chapterId: validChapterId,
        sessionId: validSessionId,
      },
      {
        maxAttempts: 10,
        intervalMs: 10,
        onUpdate: () => {},
      },
    )

    poller.cancel()
    const result = await pollPromise
    expect(result).toBe('cancelled')
  })

  it('fails closed when loader returns mismatched session ID or no-op', async () => {
    const mockLoader = vi.fn().mockResolvedValue({
      is_noop: true,
      session_id: null,
      workflow_run_id: null,
      project_id: validProjectId,
      chapter_id: validChapterId,
      document_id: validDocId,
      document_version_id: validDocVerId,
      source_hash: null,
      mode: 'off',
      status: 'off',
      stale: false,
      degradation_reason: null,
      failure_reason: null,
      planned_readers: 0,
      completed_readers: 0,
      failed_readers: 0,
      issue_count: 0,
      initial_ballot_count: 0,
      final_ballot_count: 0,
      discussion_message_count: 0,
      created_at: null,
      updated_at: null,
      completed_at: null,
      review_report: null,
      issues: [],
      initial_reports: null,
      transcript: null,
      permitted_operations: [],
    })

    const poller = new ReaderPanelPoller(mockLoader)
    const errors: ApiError[] = []

    const result = await poller.poll(
      {
        projectId: validProjectId,
        chapterId: validChapterId,
        sessionId: validSessionId,
      },
      {
        maxAttempts: 3,
        intervalMs: 0,
        onUpdate: () => {},
        onError: (err) => errors.push(err),
      },
    )

    expect(result).toBe('failed')
    expect(errors).toHaveLength(1)
    expect(errors[0].code).toBe('invalid_response')
  })
})
