import { getApiBaseUrl } from '../config'
import { ApiError } from './client'

export type ChapterProductionStatus =
  | 'DRAFTING'
  | 'AUTHOR_REVISION'
  | 'EDITOR_REVIEW'
  | 'REVIEW_REVISION'
  | 'CHIEF_FINAL_REVIEW'
  | 'LORE_FINAL_REVIEW'
  | 'REVISION_READY'
  | 'ARCHIVE_UPDATE'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'FAILED'

export type ChapterActionKind =
  | 'author_revision'
  | 'review_warning'
  | 'review_revision'

export type ChapterActionDecision =
  | 'accept'
  | 'request_feedback_revision'
  | 'submit_manual_edit'
  | 'proceed_with_warnings'
  | 'request_review_revision'
  | 'accept_warning'
  | 'request_revision'

export type ChapterFailureCode =
  | 'provider_unavailable'
  | 'provider_timeout'
  | 'invalid_provider_output'
  | 'document_commit_indeterminate'
  | 'persistence_unavailable'
  | 'archive_unavailable'
  | 'reconciliation_required'

export interface ChapterProductionState {
  chapter_workflow_run_id: string
  chapter_id: string
  status: ChapterProductionStatus
  current_node: string
  awaiting_user: boolean
  review_policy_version: string
  chief_editor_required: boolean
  document_id: string | null
  document_version_id: string | null
  content_hash: string | null
  editor_report_id: string | null
  chief_editor_report_id: string | null
  lore_report_id: string | null
  action_request_id: string | null
  action_kind: ChapterActionKind | null
  failed_from_status: ChapterProductionStatus | null
  failure_code: ChapterFailureCode | null
}

export interface ChapterProductionRunSummary {
  workflow_run_id: string
  project_id: string
  chapter_id: string
  status: ChapterProductionStatus
  current_node: string | null
  started_at: string
  updated_at: string
}

export interface ChapterProductionStarted {
  workflow_run_id: string
  action_request_id: string
  outline_document_id: string
  outline_version_id: string
  draft_document_id: string
  draft_version_id: string
}

export interface ChapterProductionUpdated {
  workflow_run_id: string
  draft_document_id: string
  draft_version_id: string
  action_request_id: string | null
}

export interface ChapterProductionFinalized {
  workflow_run_id: string
  final_document_id: string
  final_version_id: string
}

export interface ResolveChapterProductionV2ActionPayload {
  decision: ChapterActionDecision
  feedback?: string
  target_segment_ids?: string[]
  content?: string
  report_ids?: string[]
}

export interface ListChapterProductionRunsOptions {
  offset?: number
  limit?: number
}

const PRODUCTION_STATUSES = new Set<ChapterProductionStatus>([
  'DRAFTING',
  'AUTHOR_REVISION',
  'EDITOR_REVIEW',
  'REVIEW_REVISION',
  'CHIEF_FINAL_REVIEW',
  'LORE_FINAL_REVIEW',
  'REVISION_READY',
  'ARCHIVE_UPDATE',
  'COMPLETED',
  'CANCELLED',
  'FAILED',
])

const ACTION_KINDS = new Set<ChapterActionKind>([
  'author_revision',
  'review_warning',
  'review_revision',
])

const ACTION_DECISIONS = new Set<ChapterActionDecision>([
  'accept',
  'request_feedback_revision',
  'submit_manual_edit',
  'proceed_with_warnings',
  'request_review_revision',
  'accept_warning',
  'request_revision',
])

const FAILURE_CODES = new Set<ChapterFailureCode>([
  'provider_unavailable',
  'provider_timeout',
  'invalid_provider_output',
  'document_commit_indeterminate',
  'persistence_unavailable',
  'archive_unavailable',
  'reconciliation_required',
])

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const NIL_UUID = '00000000-0000-0000-0000-000000000000'
const SHA256_PATTERN = /^[0-9a-f]{64}$/
const ISO_TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/

const genericErrorMessage = 'The chapter production request could not be completed.'
const invalidResponseMessage = 'The server returned an invalid chapter production response.'

export function invalidResponse(): ApiError {
  return new ApiError(0, 'invalid_response', invalidResponseMessage)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasOnlyKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const allowed = new Set(keys)
  return Object.keys(value).every((k) => allowed.has(k))
}

export function validateUuid(value: unknown): string {
  if (typeof value !== 'string' || !UUID_PATTERN.test(value) || value === NIL_UUID) {
    throw invalidResponse()
  }
  return value.toLowerCase()
}

export function validateNullableUuid(value: unknown): string | null {
  if (value === null || value === undefined) return null
  return validateUuid(value)
}

export function validateNonEmptyString(value: unknown): string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    throw invalidResponse()
  }
  return value
}

export function validateNullableString(value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'string') throw invalidResponse()
  return value
}

export function validateBoolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw invalidResponse()
  return value
}

export function validateIsoTimestamp(value: unknown): string {
  if (typeof value !== 'string' || !ISO_TIMESTAMP_PATTERN.test(value)) {
    throw invalidResponse()
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    throw invalidResponse()
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/.exec(value)
  if (!match) {
    throw invalidResponse()
  }
  const [, yStr, mStr, dStr, hStr, minStr, sStr] = match
  const year = Number(yStr)
  const month = Number(mStr)
  const day = Number(dStr)
  const hour = Number(hStr)
  const min = Number(minStr)
  const sec = Number(sStr)

  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || min > 59 || sec > 59) {
    throw invalidResponse()
  }
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate()
  if (day > daysInMonth) {
    throw invalidResponse()
  }
  return value
}

export function validateContentHash(value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'string' || !SHA256_PATTERN.test(value)) {
    throw invalidResponse()
  }
  return value
}

export function decodeChapterProductionStatus(value: unknown): ChapterProductionStatus {
  if (typeof value !== 'string' || !PRODUCTION_STATUSES.has(value as ChapterProductionStatus)) {
    throw invalidResponse()
  }
  return value as ChapterProductionStatus
}

export function decodeChapterActionKind(value: unknown): ChapterActionKind | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'string' || !ACTION_KINDS.has(value as ChapterActionKind)) {
    throw invalidResponse()
  }
  return value as ChapterActionKind
}

export function decodeChapterFailureCode(value: unknown): ChapterFailureCode | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'string' || !FAILURE_CODES.has(value as ChapterFailureCode)) {
    throw invalidResponse()
  }
  return value as ChapterFailureCode
}

const STATE_KEYS: readonly string[] = [
  'chapter_workflow_run_id',
  'chapter_id',
  'status',
  'current_node',
  'awaiting_user',
  'review_policy_version',
  'chief_editor_required',
  'document_id',
  'document_version_id',
  'content_hash',
  'editor_report_id',
  'chief_editor_report_id',
  'lore_report_id',
  'action_request_id',
  'action_kind',
  'failed_from_status',
  'failure_code',
]

export function decodeChapterProductionState(value: unknown): ChapterProductionState {
  if (!isRecord(value) || !hasOnlyKeys(value, STATE_KEYS)) {
    throw invalidResponse()
  }

  const status = decodeChapterProductionStatus(value.status)
  const failedFromStatus = value.failed_from_status === null || value.failed_from_status === undefined
    ? null
    : decodeChapterProductionStatus(value.failed_from_status)

  return {
    chapter_workflow_run_id: validateUuid(value.chapter_workflow_run_id),
    chapter_id: validateUuid(value.chapter_id),
    status,
    current_node: validateNonEmptyString(value.current_node),
    awaiting_user: validateBoolean(value.awaiting_user),
    review_policy_version: validateNonEmptyString(value.review_policy_version),
    chief_editor_required: validateBoolean(value.chief_editor_required),
    document_id: validateNullableUuid(value.document_id),
    document_version_id: validateNullableUuid(value.document_version_id),
    content_hash: validateContentHash(value.content_hash),
    editor_report_id: validateNullableUuid(value.editor_report_id),
    chief_editor_report_id: validateNullableUuid(value.chief_editor_report_id),
    lore_report_id: validateNullableUuid(value.lore_report_id),
    action_request_id: validateNullableUuid(value.action_request_id),
    action_kind: decodeChapterActionKind(value.action_kind),
    failed_from_status: failedFromStatus,
    failure_code: decodeChapterFailureCode(value.failure_code),
  }
}

const SUMMARY_KEYS: readonly string[] = [
  'workflow_run_id',
  'project_id',
  'chapter_id',
  'status',
  'current_node',
  'started_at',
  'updated_at',
]

export function decodeChapterProductionRunSummary(value: unknown): ChapterProductionRunSummary {
  if (!isRecord(value) || !hasOnlyKeys(value, SUMMARY_KEYS)) {
    throw invalidResponse()
  }

  return {
    workflow_run_id: validateUuid(value.workflow_run_id),
    project_id: validateUuid(value.project_id),
    chapter_id: validateUuid(value.chapter_id),
    status: decodeChapterProductionStatus(value.status),
    current_node: validateNullableString(value.current_node),
    started_at: validateIsoTimestamp(value.started_at),
    updated_at: validateIsoTimestamp(value.updated_at),
  }
}

const STARTED_KEYS: readonly string[] = [
  'workflow_run_id',
  'action_request_id',
  'outline_document_id',
  'outline_version_id',
  'draft_document_id',
  'draft_version_id',
]

export function decodeChapterProductionStarted(value: unknown): ChapterProductionStarted {
  if (!isRecord(value) || !hasOnlyKeys(value, STARTED_KEYS)) {
    throw invalidResponse()
  }

  return {
    workflow_run_id: validateUuid(value.workflow_run_id),
    action_request_id: validateUuid(value.action_request_id),
    outline_document_id: validateUuid(value.outline_document_id),
    outline_version_id: validateUuid(value.outline_version_id),
    draft_document_id: validateUuid(value.draft_document_id),
    draft_version_id: validateUuid(value.draft_version_id),
  }
}

const UPDATED_KEYS: readonly string[] = [
  'workflow_run_id',
  'draft_document_id',
  'draft_version_id',
  'action_request_id',
]

export function decodeChapterProductionUpdated(value: unknown): ChapterProductionUpdated {
  if (!isRecord(value) || !hasOnlyKeys(value, UPDATED_KEYS)) {
    throw invalidResponse()
  }

  return {
    workflow_run_id: validateUuid(value.workflow_run_id),
    draft_document_id: validateUuid(value.draft_document_id),
    draft_version_id: validateUuid(value.draft_version_id),
    action_request_id: validateNullableUuid(value.action_request_id),
  }
}

const FINALIZED_KEYS: readonly string[] = [
  'workflow_run_id',
  'final_document_id',
  'final_version_id',
]

export function decodeChapterProductionFinalized(value: unknown): ChapterProductionFinalized {
  if (!isRecord(value) || !hasOnlyKeys(value, FINALIZED_KEYS)) {
    throw invalidResponse()
  }

  return {
    workflow_run_id: validateUuid(value.workflow_run_id),
    final_document_id: validateUuid(value.final_document_id),
    final_version_id: validateUuid(value.final_version_id),
  }
}

function apiBasePath(): string {
  const base = getApiBaseUrl().trim()
  if (!base.startsWith('/') || /[?#\\%]/.test(base)) throw invalidResponse()
  const normalized = base.replace(/\/+$/, '')
  if (
    normalized.length === 0
    || !normalized.slice(1).split('/').every((segment) => /^[A-Za-z0-9_-]+$/.test(segment))
  ) {
    throw invalidResponse()
  }
  return normalized
}

function apiPath(...segments: string[]): string {
  return `${apiBasePath()}/${segments.map((segment) => encodeURIComponent(segment)).join('/')}`
}

function isSafeErrorCode(value: unknown): value is string {
  return typeof value === 'string' && /^[a-z0-9_]{1,64}$/.test(value)
}

function isSafeErrorMessage(value: unknown): value is string {
  return (
    typeof value === 'string'
    && value.length > 0
    && value.length <= 500
    && !Array.from(value).some((character) => {
      const code = character.charCodeAt(0)
      return code < 32 || code === 127
    })
  )
}

function safeErrorFromStatus(status: number, value: unknown): ApiError {
  if (isRecord(value) && isRecord(value.error) && isSafeErrorCode(value.error.code) && isSafeErrorMessage(value.error.message)) {
    return new ApiError(status, value.error.code, value.error.message)
  }

  let code = 'request_failed'
  let message = genericErrorMessage

  if (status === 404) {
    code = 'not_found'
    message = 'The requested chapter production resource was not found.'
  } else if (status === 409) {
    code = 'state_conflict'
    message = 'Chapter production requires reconciliation or state conflict occurred.'
  } else if (status === 422) {
    code = 'validation_error'
    message = 'Chapter production request failed validation.'
  } else if (status === 500) {
    code = 'commit_indeterminate'
    message = 'Chapter drafting commit outcome is indeterminate. Reconciliation is required.'
  } else if (status === 503) {
    code = 'provider_unavailable'
    message = 'Chapter production provider is unavailable or timed out.'
  }

  return new ApiError(status, code, message)
}

interface RequestOptions {
  signal?: AbortSignal
}

async function request<T>(
  method: 'GET' | 'POST',
  path: () => string,
  decode: (value: unknown) => T,
  body?: object,
  options: RequestOptions = {},
): Promise<T> {
  let response: Response
  try {
    response = await fetch(path(), {
      method,
      credentials: 'same-origin',
      ...(options.signal === undefined ? {} : { signal: options.signal }),
      ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    })
  } catch (error: unknown) {
    if (error instanceof ApiError) throw error
    throw new ApiError(0, 'request_failed', genericErrorMessage)
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw response.ok ? invalidResponse() : new ApiError(response.status, 'request_failed', genericErrorMessage)
  }

  if (!response.ok) {
    throw safeErrorFromStatus(response.status, payload)
  }

  return decode(payload)
}

export function startChapterProductionV2(
  projectId: string,
  chapterId: string,
  signal?: AbortSignal,
): Promise<ChapterProductionStarted> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  return request(
    'POST',
    () => apiPath('projects', pId, 'chapters', cId, 'production-v2', 'start'),
    decodeChapterProductionStarted,
    {},
    { signal },
  )
}

export function listChapterProductionRuns(
  projectId: string,
  chapterId: string,
  options?: ListChapterProductionRunsOptions,
  signal?: AbortSignal,
): Promise<ChapterProductionRunSummary[]> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const queryParams = new URLSearchParams()
  if (options?.offset !== undefined) {
    if (!Number.isInteger(options.offset) || options.offset < 0) throw invalidResponse()
    queryParams.set('offset', String(options.offset))
  }
  if (options?.limit !== undefined) {
    if (!Number.isInteger(options.limit) || options.limit < 1 || options.limit > 100) throw invalidResponse()
    queryParams.set('limit', String(options.limit))
  }
  const queryString = queryParams.toString()
  const query = queryString ? `?${queryString}` : ''

  return request(
    'GET',
    () => `${apiPath('projects', pId, 'chapters', cId, 'production-v2')}${query}`,
    (value) => {
      if (!Array.isArray(value)) throw invalidResponse()
      return value.map(decodeChapterProductionRunSummary)
    },
    undefined,
    { signal },
  )
}

export function getChapterProductionRun(
  projectId: string,
  chapterId: string,
  workflowRunId: string,
  signal?: AbortSignal,
): Promise<ChapterProductionState> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const rId = validateUuid(workflowRunId)
  return request(
    'GET',
    () => apiPath('projects', pId, 'chapters', cId, 'production-v2', rId),
    decodeChapterProductionState,
    undefined,
    { signal },
  )
}

export function resumeChapterProduction(
  projectId: string,
  chapterId: string,
  workflowRunId: string,
  signal?: AbortSignal,
): Promise<ChapterProductionStarted> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const rId = validateUuid(workflowRunId)
  return request(
    'POST',
    () => apiPath('projects', pId, 'chapters', cId, 'production-v2', rId, 'resume'),
    decodeChapterProductionStarted,
    {},
    { signal },
  )
}

export function resolveChapterProductionAction(
  projectId: string,
  chapterId: string,
  workflowRunId: string,
  actionId: string,
  payload: ResolveChapterProductionV2ActionPayload,
  signal?: AbortSignal,
): Promise<ChapterProductionUpdated> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const rId = validateUuid(workflowRunId)
  const aId = validateUuid(actionId)

  if (!isRecord(payload) || typeof payload.decision !== 'string' || !ACTION_DECISIONS.has(payload.decision)) {
    throw new ApiError(422, 'invalid_request', 'Invalid chapter production decision.')
  }

  const cleanPayload: Record<string, unknown> = {
    decision: payload.decision,
  }

  if (payload.feedback !== undefined) {
    cleanPayload.feedback = validateNonEmptyString(payload.feedback)
  }
  if (payload.target_segment_ids !== undefined) {
    if (!Array.isArray(payload.target_segment_ids) || payload.target_segment_ids.length === 0) {
      throw new ApiError(422, 'invalid_request', 'Target segment IDs must not be empty.')
    }
    cleanPayload.target_segment_ids = payload.target_segment_ids.map(validateUuid)
  }
  if (payload.content !== undefined) {
    cleanPayload.content = validateNonEmptyString(payload.content)
  }
  if (payload.report_ids !== undefined) {
    if (!Array.isArray(payload.report_ids) || payload.report_ids.length === 0) {
      throw new ApiError(422, 'invalid_request', 'Report IDs must not be empty.')
    }
    cleanPayload.report_ids = payload.report_ids.map(validateUuid)
  }

  return request(
    'POST',
    () => apiPath('projects', pId, 'chapters', cId, 'production-v2', rId, 'actions', aId, 'resolve'),
    decodeChapterProductionUpdated,
    cleanPayload,
    { signal },
  )
}

export function triggerChapterReview(
  projectId: string,
  chapterId: string,
  workflowRunId: string,
  signal?: AbortSignal,
): Promise<ChapterProductionUpdated> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const rId = validateUuid(workflowRunId)
  return request(
    'POST',
    () => apiPath('projects', pId, 'chapters', cId, 'production-v2', rId, 'review'),
    decodeChapterProductionUpdated,
    {},
    { signal },
  )
}

export function finalizeChapterProduction(
  projectId: string,
  chapterId: string,
  workflowRunId: string,
  signal?: AbortSignal,
): Promise<ChapterProductionFinalized> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const rId = validateUuid(workflowRunId)
  return request(
    'POST',
    () => apiPath('projects', pId, 'chapters', cId, 'production-v2', rId, 'finalize'),
    decodeChapterProductionFinalized,
    {},
    { signal },
  )
}

export function reconcileChapterProduction(
  projectId: string,
  chapterId: string,
  workflowRunId: string,
  signal?: AbortSignal,
): Promise<ChapterProductionState> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const rId = validateUuid(workflowRunId)
  return request(
    'POST',
    () => apiPath('projects', pId, 'chapters', cId, 'production-v2', rId, 'reconcile'),
    decodeChapterProductionState,
    {},
    { signal },
  )
}
