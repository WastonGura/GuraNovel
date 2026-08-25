import { getApiBaseUrl } from '../config'
import { ApiError } from './client'

export type PanelMode = 'off' | 'quick' | 'standard' | 'panel'

export type ReaderPanelStatus =
  | 'created'
  | 'preparing'
  | 'independent_reading'
  | 'initial_reports_locked'
  | 'issue_extraction'
  | 'initial_balloting'
  | 'initial_ballots_locked'
  | 'discussing'
  | 'final_balloting'
  | 'final_ballots_locked'
  | 'report_generating'
  | 'completed'
  | 'degraded_completed'
  | 'failed'
  | 'cancelled'
  | 'off'

export type Severity = 'none' | 'minor' | 'significant' | 'critical' | 'abstain'

export type EditorialDecision =
  | 'must_fix'
  | 'experiment'
  | 'keep'
  | 'manual_review'
  | 'rejected'

export type SuggestedAction =
  | 'keep'
  | 'clarify'
  | 'compress'
  | 'expand'
  | 'move'
  | 'rewrite_local'
  | 'split'
  | 'experiment_ab'
  | 'manual_review'

export type Confidence = 'low' | 'medium' | 'high'
export type ContinueReadingVote = 'yes' | 'maybe' | 'no'
export type TargetAudienceRelevance = 'low' | 'medium' | 'high'
export type DiscussionStatus = 'queued' | 'discussing' | 'closed' | 'skipped'
export type ConsensusClass =
  | 'strong_consensus'
  | 'weak_consensus'
  | 'polarized'
  | 'accepted'
  | 'inconclusive'

export type MessageSpeakerType = 'reader' | 'moderator'
export type MessageStance = 'support' | 'oppose' | 'mixed' | 'abstain'
export type MessageNovelty = 'new_evidence' | 'new_interpretation' | 'repetition' | 'procedural'
export type PermittedOperation = 'cancel' | 'resume'

export interface EvidenceRef {
  segment_ids: string[]
  note: string
}

export interface StrengthItem {
  summary: string
  evidence: EvidenceRef[]
}

export interface ReactionItem {
  segment_ids: string[]
  reaction: string
  emotion: string | null
  confusion: string | null
}

export interface ConcernItem {
  category: string
  symptom: string
  severity: Severity
  evidence: EvidenceRef[]
  suggested_action: SuggestedAction | null
}

export interface ReaderPanelAction {
  priority: EditorialDecision
  target_segment_ids: string[]
  suggested_action: SuggestedAction
  instruction: string
}

export interface ReaderPanelBlockingIssue {
  issue_number: number
  title: string
}

export interface ReaderPanelReviewReport {
  summary: string
  blocking_issues: ReaderPanelBlockingIssue[]
  warnings: string[]
  notes: string[]
  suggested_actions: ReaderPanelAction[]
}

export interface ReaderPanelInitialReport {
  overall_reaction: string
  continue_reading: ContinueReadingVote
  confidence: Confidence
  strengths: StrengthItem[]
  reactions: ReactionItem[]
  concerns: ConcernItem[]
}

export interface ReaderPanelMessage {
  issue_id: string
  round_number: number
  turn_number: number
  speaker_type: MessageSpeakerType
  stance: MessageStance | null
  claim: string
  evidence: EvidenceRef[]
  concession: string | null
  proposed_action: string | null
  novelty: MessageNovelty
  created_at: string | null
}

export interface ReaderPanelIssue {
  issue_number: number
  title: string
  category: string
  symptom: string
  root_cause_hypotheses: string[]
  evidence: EvidenceRef[]
  target_audience_relevance: TargetAudienceRelevance
  minority_risk: boolean
  discussion_status: DiscussionStatus
  consensus_class: ConsensusClass | null
  recommended_priority: EditorialDecision | null
}

export interface ReaderPanelConfigOverrides {
  max_ballot_issues?: number | null
  max_discussion_issues?: number | null
  max_rounds_per_issue?: number | null
  min_valid_readers?: number | null
}

export interface ReaderPanelStartPayload {
  document_id: string
  document_version_id: string
  mode?: PanelMode
  config_overrides?: ReaderPanelConfigOverrides | null
  test_goals?: string[]
  target_audience?: string[]
  idempotency_key?: string | null
}

export interface ListReaderPanelsOptions {
  offset?: number
  limit?: number
  include_initial_reports?: boolean
  include_transcript?: boolean
  data_limit?: number
}

export interface GetReaderPanelOptions {
  include_initial_reports?: boolean
  include_transcript?: boolean
  data_limit?: number
}

export interface ReaderPanelNoOpDetail {
  is_noop: true
  session_id: null
  workflow_run_id: null
  project_id: string
  chapter_id: string
  document_id: string
  document_version_id: string
  source_hash: string | null
  mode: 'off'
  status: 'off'
  stale: false
  degradation_reason: null
  failure_reason: null
  planned_readers: 0
  completed_readers: 0
  failed_readers: 0
  issue_count: 0
  initial_ballot_count: 0
  final_ballot_count: 0
  discussion_message_count: 0
  created_at: string | null
  updated_at: string | null
  completed_at: string | null
  review_report: null
  issues: []
  initial_reports: null
  transcript: null
  permitted_operations: []
}

export interface ReaderPanelSessionDetail {
  is_noop: false
  session_id: string
  workflow_run_id: string
  project_id: string
  chapter_id: string
  document_id: string
  document_version_id: string
  source_hash: string | null
  mode: 'quick' | 'standard' | 'panel'
  status: ReaderPanelStatus
  stale: boolean
  degradation_reason: string | null
  failure_reason: string | null
  planned_readers: number
  completed_readers: number
  failed_readers: number
  issue_count: number
  initial_ballot_count: number
  final_ballot_count: number
  discussion_message_count: number
  created_at: string | null
  updated_at: string | null
  completed_at: string | null
  review_report: ReaderPanelReviewReport | null
  issues: ReaderPanelIssue[]
  initial_reports: ReaderPanelInitialReport[] | null
  transcript: ReaderPanelMessage[] | null
  permitted_operations: PermittedOperation[]
}

export type ReaderPanelDetail = ReaderPanelNoOpDetail | ReaderPanelSessionDetail

const PANEL_MODES = new Set<PanelMode>(['off', 'quick', 'standard', 'panel'])

const READER_PANEL_STATUSES = new Set<ReaderPanelStatus>([
  'created',
  'preparing',
  'independent_reading',
  'initial_reports_locked',
  'issue_extraction',
  'initial_balloting',
  'initial_ballots_locked',
  'discussing',
  'final_balloting',
  'final_ballots_locked',
  'report_generating',
  'completed',
  'degraded_completed',
  'failed',
  'cancelled',
  'off',
])

const SEVERITIES = new Set<Severity>(['none', 'minor', 'significant', 'critical', 'abstain'])

const EDITORIAL_DECISIONS = new Set<EditorialDecision>([
  'must_fix',
  'experiment',
  'keep',
  'manual_review',
  'rejected',
])

const SUGGESTED_ACTIONS = new Set<SuggestedAction>([
  'keep',
  'clarify',
  'compress',
  'expand',
  'move',
  'rewrite_local',
  'split',
  'experiment_ab',
  'manual_review',
])

const CONFIDENCES = new Set<Confidence>(['low', 'medium', 'high'])
const CONTINUE_READING_VOTES = new Set<ContinueReadingVote>(['yes', 'maybe', 'no'])
const TARGET_AUDIENCE_RELEVANCES = new Set<TargetAudienceRelevance>(['low', 'medium', 'high'])
const DISCUSSION_STATUSES = new Set<DiscussionStatus>(['queued', 'discussing', 'closed', 'skipped'])
const CONSENSUS_CLASSES = new Set<ConsensusClass>([
  'strong_consensus',
  'weak_consensus',
  'polarized',
  'accepted',
  'inconclusive',
])
const MESSAGE_SPEAKER_TYPES = new Set<MessageSpeakerType>(['reader', 'moderator'])
const MESSAGE_STANCES = new Set<MessageStance>(['support', 'oppose', 'mixed', 'abstain'])
const MESSAGE_NOVELTIES = new Set<MessageNovelty>([
  'new_evidence',
  'new_interpretation',
  'repetition',
  'procedural',
])
const PERMITTED_OPERATIONS = new Set<PermittedOperation>(['cancel', 'resume'])

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const NIL_UUID = '00000000-0000-0000-0000-000000000000'
const SHA256_PATTERN = /^[0-9a-f]{64}$/
const ISO_TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9:_-]{1,128}$/
const SEGMENT_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/

const genericErrorMessage = 'The reader panel request could not be completed.'
const invalidResponseMessage = 'The server returned an invalid reader panel response.'

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

export function validateNonEmptyString(value: unknown, maxLength = 4000): string {
  if (typeof value !== 'string' || value.trim().length === 0 || value.length > maxLength) {
    throw invalidResponse()
  }
  return value
}

export function validateNullableString(value: unknown, maxLength = 4000): string | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'string' || value.length > maxLength) throw invalidResponse()
  return value
}

export function validateBoolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw invalidResponse()
  return value
}

export function validateNonNegativeInteger(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw invalidResponse()
  }
  return value
}

export function validatePositiveInteger(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1) {
    throw invalidResponse()
  }
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
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2}:\d{2})/.exec(value)
  if (!match) {
    throw invalidResponse()
  }
  const [, yStr, mStr] = match
  const year = Number(yStr)
  const month = Number(mStr)
  const day = Number(value.substring(8, 10))
  const hour = Number(value.substring(11, 13))
  const min = Number(value.substring(14, 16))
  const sec = Number(value.substring(17, 19))

  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || min > 59 || sec > 59) {
    throw invalidResponse()
  }
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate()
  if (day > daysInMonth) {
    throw invalidResponse()
  }
  return value
}

export function validateNullableIsoTimestamp(value: unknown): string | null {
  if (value === null || value === undefined) return null
  return validateIsoTimestamp(value)
}

export function validateContentHash(value: unknown): string | null {
  if (value === null || value === undefined) return null
  if (typeof value !== 'string' || !SHA256_PATTERN.test(value)) {
    throw invalidResponse()
  }
  return value
}

export function decodeEvidenceRef(value: unknown): EvidenceRef {
  if (!isRecord(value)) throw invalidResponse()
  const allowedKeys = ['segment_ids', 'note'] as const
  if (!hasOnlyKeys(value, allowedKeys)) throw invalidResponse()
  if (!Array.isArray(value.segment_ids) || value.segment_ids.length === 0 || value.segment_ids.length > 16) {
    throw invalidResponse()
  }
  const segment_ids = value.segment_ids.map((id) => {
    if (typeof id !== 'string' || !SEGMENT_ID_PATTERN.test(id)) throw invalidResponse()
    return id
  })
  const note = validateNonEmptyString(value.note, 1000)
  return {
    segment_ids,
    note,
  }
}

export function decodeStrengthItem(value: unknown): StrengthItem {
  if (!isRecord(value)) throw invalidResponse()
  const allowedKeys = ['summary', 'evidence'] as const
  if (!hasOnlyKeys(value, allowedKeys)) throw invalidResponse()
  const summary = validateNonEmptyString(value.summary, 500)
  if (!Array.isArray(value.evidence)) throw invalidResponse()
  const evidence = value.evidence.map(decodeEvidenceRef)
  return { summary, evidence }
}

export function decodeReactionItem(value: unknown): ReactionItem {
  if (!isRecord(value)) throw invalidResponse()
  const allowedKeys = ['segment_ids', 'reaction', 'emotion', 'confusion'] as const
  if (!hasOnlyKeys(value, allowedKeys)) throw invalidResponse()
  if (!Array.isArray(value.segment_ids)) throw invalidResponse()
  const segment_ids = value.segment_ids.map((id) => {
    if (typeof id !== 'string' || !SEGMENT_ID_PATTERN.test(id)) throw invalidResponse()
    return id
  })
  const reaction = validateNonEmptyString(value.reaction, 1000)
  const emotion = value.emotion !== undefined ? validateNullableString(value.emotion, 64) : null
  const confusion = value.confusion !== undefined ? validateNullableString(value.confusion, 500) : null
  return {
    segment_ids,
    reaction,
    emotion,
    confusion,
  }
}

export function decodeConcernItem(value: unknown): ConcernItem {
  if (!isRecord(value)) throw invalidResponse()
  const allowedKeys = ['category', 'symptom', 'severity', 'evidence', 'suggested_action'] as const
  if (!hasOnlyKeys(value, allowedKeys)) throw invalidResponse()
  const category = validateNonEmptyString(value.category, 64)
  const symptom = validateNonEmptyString(value.symptom, 1000)
  if (typeof value.severity !== 'string' || !SEVERITIES.has(value.severity as Severity)) {
    throw invalidResponse()
  }
  if (!Array.isArray(value.evidence)) throw invalidResponse()
  const evidence = value.evidence.map(decodeEvidenceRef)

  let suggested_action: SuggestedAction | null = null
  if (value.suggested_action !== null && value.suggested_action !== undefined) {
    if (typeof value.suggested_action !== 'string' || !SUGGESTED_ACTIONS.has(value.suggested_action as SuggestedAction)) {
      throw invalidResponse()
    }
    suggested_action = value.suggested_action as SuggestedAction
  }

  return {
    category,
    symptom,
    severity: value.severity as Severity,
    evidence,
    suggested_action,
  }
}

export function decodeReaderPanelAction(value: unknown): ReaderPanelAction {
  if (!isRecord(value)) throw invalidResponse()
  const allowedKeys = ['priority', 'target_segment_ids', 'suggested_action', 'instruction'] as const
  if (!hasOnlyKeys(value, allowedKeys)) throw invalidResponse()
  if (typeof value.priority !== 'string' || !EDITORIAL_DECISIONS.has(value.priority as EditorialDecision)) {
    throw invalidResponse()
  }
  if (!Array.isArray(value.target_segment_ids) || value.target_segment_ids.length === 0) {
    throw invalidResponse()
  }
  const target_segment_ids = value.target_segment_ids.map((id) => {
    if (typeof id !== 'string' || !SEGMENT_ID_PATTERN.test(id)) throw invalidResponse()
    return id
  })
  if (typeof value.suggested_action !== 'string' || !SUGGESTED_ACTIONS.has(value.suggested_action as SuggestedAction)) {
    throw invalidResponse()
  }
  const instruction = validateNonEmptyString(value.instruction, 2000)
  return {
    priority: value.priority as EditorialDecision,
    target_segment_ids,
    suggested_action: value.suggested_action as SuggestedAction,
    instruction,
  }
}

export function decodeReaderPanelBlockingIssue(value: unknown): ReaderPanelBlockingIssue {
  if (!isRecord(value)) throw invalidResponse()
  const allowedKeys = ['issue_number', 'title'] as const
  if (!hasOnlyKeys(value, allowedKeys)) throw invalidResponse()
  const issue_number = validatePositiveInteger(value.issue_number)
  const title = validateNonEmptyString(value.title, 256)
  return { issue_number, title }
}

export function decodeReaderPanelReviewReport(value: unknown): ReaderPanelReviewReport {
  if (!isRecord(value)) throw invalidResponse()
  const allowedKeys = ['summary', 'blocking_issues', 'warnings', 'notes', 'suggested_actions'] as const
  if (!hasOnlyKeys(value, allowedKeys)) throw invalidResponse()
  const summary = validateNonEmptyString(value.summary, 4000)
  if (!Array.isArray(value.blocking_issues) || !Array.isArray(value.warnings) || !Array.isArray(value.notes) || !Array.isArray(value.suggested_actions)) {
    throw invalidResponse()
  }
  const blocking_issues = value.blocking_issues.map(decodeReaderPanelBlockingIssue)
  const warnings = value.warnings.map((w) => validateNonEmptyString(w, 2000))
  const notes = value.notes.map((n) => validateNonEmptyString(n, 2000))
  const suggested_actions = value.suggested_actions.map(decodeReaderPanelAction)

  return {
    summary,
    blocking_issues,
    warnings,
    notes,
    suggested_actions,
  }
}

export function decodeReaderPanelInitialReport(value: unknown): ReaderPanelInitialReport {
  if (!isRecord(value)) throw invalidResponse()
  const allowedKeys = ['overall_reaction', 'continue_reading', 'confidence', 'strengths', 'reactions', 'concerns'] as const
  if (!hasOnlyKeys(value, allowedKeys)) throw invalidResponse()
  const overall_reaction = validateNonEmptyString(value.overall_reaction, 2000)
  if (typeof value.continue_reading !== 'string' || !CONTINUE_READING_VOTES.has(value.continue_reading as ContinueReadingVote)) {
    throw invalidResponse()
  }
  if (typeof value.confidence !== 'string' || !CONFIDENCES.has(value.confidence as Confidence)) {
    throw invalidResponse()
  }
  if (!Array.isArray(value.strengths) || !Array.isArray(value.reactions) || !Array.isArray(value.concerns)) {
    throw invalidResponse()
  }
  const strengths = value.strengths.map(decodeStrengthItem)
  const reactions = value.reactions.map(decodeReactionItem)
  const concerns = value.concerns.map(decodeConcernItem)

  return {
    overall_reaction,
    continue_reading: value.continue_reading as ContinueReadingVote,
    confidence: value.confidence as Confidence,
    strengths,
    reactions,
    concerns,
  }
}

export function decodeReaderPanelMessage(value: unknown): ReaderPanelMessage {
  if (!isRecord(value)) throw invalidResponse()
  const allowedKeys = [
    'issue_id',
    'round_number',
    'turn_number',
    'speaker_type',
    'stance',
    'claim',
    'evidence',
    'concession',
    'proposed_action',
    'novelty',
    'created_at',
  ] as const
  if (!hasOnlyKeys(value, allowedKeys)) throw invalidResponse()

  const issue_id = validateUuid(value.issue_id)
  const round_number = validatePositiveInteger(value.round_number)
  const turn_number = validatePositiveInteger(value.turn_number)

  if (typeof value.speaker_type !== 'string' || !MESSAGE_SPEAKER_TYPES.has(value.speaker_type as MessageSpeakerType)) {
    throw invalidResponse()
  }
  const speaker_type = value.speaker_type as MessageSpeakerType

  // Cross-field validation: reader turn requires non-null stance; moderator requires null stance
  if (speaker_type === 'reader') {
    if (typeof value.stance !== 'string' || !MESSAGE_STANCES.has(value.stance as MessageStance)) {
      throw invalidResponse()
    }
  } else if (value.stance !== null && value.stance !== undefined) {
    throw invalidResponse()
  }
  const stance = (value.stance as MessageStance) ?? null

  const claim = validateNonEmptyString(value.claim, 2000)
  if (!Array.isArray(value.evidence)) throw invalidResponse()
  const evidence = value.evidence.map(decodeEvidenceRef)
  const concession = value.concession !== undefined ? validateNullableString(value.concession, 1000) : null
  const proposed_action = value.proposed_action !== undefined ? validateNullableString(value.proposed_action, 500) : null

  if (typeof value.novelty !== 'string' || !MESSAGE_NOVELTIES.has(value.novelty as MessageNovelty)) {
    throw invalidResponse()
  }
  const novelty = value.novelty as MessageNovelty
  const created_at = value.created_at !== undefined ? validateNullableIsoTimestamp(value.created_at) : null

  return {
    issue_id,
    round_number,
    turn_number,
    speaker_type,
    stance,
    claim,
    evidence,
    concession,
    proposed_action,
    novelty,
    created_at,
  }
}

export function decodeReaderPanelIssue(value: unknown): ReaderPanelIssue {
  if (!isRecord(value)) throw invalidResponse()
  const allowedKeys = [
    'issue_number',
    'title',
    'category',
    'symptom',
    'root_cause_hypotheses',
    'evidence',
    'target_audience_relevance',
    'minority_risk',
    'discussion_status',
    'consensus_class',
    'recommended_priority',
  ] as const
  if (!hasOnlyKeys(value, allowedKeys)) throw invalidResponse()

  const issue_number = validatePositiveInteger(value.issue_number)
  const title = validateNonEmptyString(value.title, 256)
  const category = validateNonEmptyString(value.category, 64)
  const symptom = validateNonEmptyString(value.symptom, 1000)

  if (!Array.isArray(value.root_cause_hypotheses) || value.root_cause_hypotheses.length === 0) {
    throw invalidResponse()
  }
  const root_cause_hypotheses = value.root_cause_hypotheses.map((h) => validateNonEmptyString(h, 1000))

  if (!Array.isArray(value.evidence)) throw invalidResponse()
  const evidence = value.evidence.map(decodeEvidenceRef)

  if (typeof value.target_audience_relevance !== 'string' || !TARGET_AUDIENCE_RELEVANCES.has(value.target_audience_relevance as TargetAudienceRelevance)) {
    throw invalidResponse()
  }
  const target_audience_relevance = value.target_audience_relevance as TargetAudienceRelevance
  const minority_risk = validateBoolean(value.minority_risk)

  if (typeof value.discussion_status !== 'string' || !DISCUSSION_STATUSES.has(value.discussion_status as DiscussionStatus)) {
    throw invalidResponse()
  }
  const discussion_status = value.discussion_status as DiscussionStatus

  let consensus_class: ConsensusClass | null = null
  if (value.consensus_class !== null && value.consensus_class !== undefined) {
    if (typeof value.consensus_class !== 'string' || !CONSENSUS_CLASSES.has(value.consensus_class as ConsensusClass)) {
      throw invalidResponse()
    }
    consensus_class = value.consensus_class as ConsensusClass
  }

  let recommended_priority: EditorialDecision | null = null
  if (value.recommended_priority !== null && value.recommended_priority !== undefined) {
    if (typeof value.recommended_priority !== 'string' || !EDITORIAL_DECISIONS.has(value.recommended_priority as EditorialDecision)) {
      throw invalidResponse()
    }
    recommended_priority = value.recommended_priority as EditorialDecision
  }

  return {
    issue_number,
    title,
    category,
    symptom,
    root_cause_hypotheses,
    evidence,
    target_audience_relevance,
    minority_risk,
    discussion_status,
    consensus_class,
    recommended_priority,
  }
}

export function decodeReaderPanelDetail(value: unknown): ReaderPanelDetail {
  if (!isRecord(value)) throw invalidResponse()
  const allowedKeys = [
    'session_id',
    'workflow_run_id',
    'project_id',
    'chapter_id',
    'document_id',
    'document_version_id',
    'source_hash',
    'mode',
    'status',
    'is_noop',
    'stale',
    'degradation_reason',
    'failure_reason',
    'planned_readers',
    'completed_readers',
    'failed_readers',
    'issue_count',
    'initial_ballot_count',
    'final_ballot_count',
    'discussion_message_count',
    'message_count',
    'created_at',
    'updated_at',
    'completed_at',
    'review_report',
    'issues',
    'initial_reports',
    'transcript',
    'discussion_transcript',
    'permitted_operations',
  ] as const
  if (!hasOnlyKeys(value, allowedKeys)) throw invalidResponse()

  const is_noop = validateBoolean(value.is_noop ?? false)
  const project_id = validateUuid(value.project_id)
  const chapter_id = validateUuid(value.chapter_id)
  const document_id = validateUuid(value.document_id)
  const document_version_id = validateUuid(value.document_version_id)
  const source_hash = validateContentHash(value.source_hash)

  if (typeof value.mode !== 'string' || !PANEL_MODES.has(value.mode as PanelMode)) {
    throw invalidResponse()
  }
  const mode = value.mode as PanelMode

  if (typeof value.status !== 'string' || !READER_PANEL_STATUSES.has(value.status as ReaderPanelStatus)) {
    throw invalidResponse()
  }
  const status = value.status as ReaderPanelStatus

  const stale = validateBoolean(value.stale ?? false)
  const degradation_reason = validateNullableString(value.degradation_reason, 1000)
  const failure_reason = validateNullableString(value.failure_reason, 1000)

  const planned_readers = validateNonNegativeInteger(value.planned_readers ?? 0)
  const completed_readers = validateNonNegativeInteger(value.completed_readers ?? 0)
  const failed_readers = validateNonNegativeInteger(value.failed_readers ?? 0)
  const issue_count = validateNonNegativeInteger(value.issue_count ?? 0)
  const initial_ballot_count = validateNonNegativeInteger(value.initial_ballot_count ?? 0)
  const final_ballot_count = validateNonNegativeInteger(value.final_ballot_count ?? 0)

  const discussion_message_count = validateNonNegativeInteger(
    value.discussion_message_count ?? value.message_count ?? 0,
  )

  const created_at = validateNullableIsoTimestamp(value.created_at)
  const updated_at = validateNullableIsoTimestamp(value.updated_at)
  const completed_at = validateNullableIsoTimestamp(value.completed_at)

  let review_report: ReaderPanelReviewReport | null = null
  if (value.review_report !== null && value.review_report !== undefined) {
    review_report = decodeReaderPanelReviewReport(value.review_report)
  }

  if (!Array.isArray(value.issues ?? [])) throw invalidResponse()
  const issues = (value.issues as unknown[] ?? []).map(decodeReaderPanelIssue)

  let initial_reports: ReaderPanelInitialReport[] | null = null
  if (value.initial_reports !== null && value.initial_reports !== undefined) {
    if (!Array.isArray(value.initial_reports)) throw invalidResponse()
    initial_reports = value.initial_reports.map(decodeReaderPanelInitialReport)
  }

  const rawTranscript = value.transcript ?? value.discussion_transcript
  let transcript: ReaderPanelMessage[] | null = null
  if (rawTranscript !== null && rawTranscript !== undefined) {
    if (!Array.isArray(rawTranscript)) throw invalidResponse()
    transcript = rawTranscript.map(decodeReaderPanelMessage)
  }

  if (!Array.isArray(value.permitted_operations ?? [])) throw invalidResponse()
  const permitted_operations = (value.permitted_operations as unknown[] ?? []).map((op) => {
    if (typeof op !== 'string' || !PERMITTED_OPERATIONS.has(op as PermittedOperation)) {
      throw invalidResponse()
    }
    return op as PermittedOperation
  })

  // Discriminated union validation
  if (is_noop) {
    if (
      mode !== 'off'
      || status !== 'off'
      || value.session_id !== null
      || value.workflow_run_id !== null
      || stale !== false
      || degradation_reason !== null
      || failure_reason !== null
      || planned_readers !== 0
      || completed_readers !== 0
      || failed_readers !== 0
      || issue_count !== 0
      || initial_ballot_count !== 0
      || final_ballot_count !== 0
      || discussion_message_count !== 0
      || review_report !== null
      || issues.length !== 0
      || initial_reports !== null
      || transcript !== null
      || permitted_operations.length !== 0
    ) {
      throw invalidResponse()
    }

    return {
      is_noop: true,
      session_id: null,
      workflow_run_id: null,
      project_id,
      chapter_id,
      document_id,
      document_version_id,
      source_hash,
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
      created_at,
      updated_at,
      completed_at,
      review_report: null,
      issues: [],
      initial_reports: null,
      transcript: null,
      permitted_operations: [],
    }
  }

  // Active or completed session
  const session_id = validateUuid(value.session_id)
  const workflow_run_id = validateUuid(value.workflow_run_id)
  if (mode === 'off' || status === 'off') {
    throw invalidResponse()
  }

  return {
    is_noop: false,
    session_id,
    workflow_run_id,
    project_id,
    chapter_id,
    document_id,
    document_version_id,
    source_hash,
    mode: mode as 'quick' | 'standard' | 'panel',
    status,
    stale,
    degradation_reason,
    failure_reason,
    planned_readers,
    completed_readers,
    failed_readers,
    issue_count,
    initial_ballot_count,
    final_ballot_count,
    discussion_message_count,
    created_at,
    updated_at,
    completed_at,
    review_report,
    issues,
    initial_reports,
    transcript,
    permitted_operations,
  }
}

function apiBasePath(): string {
  const base = getApiBaseUrl().trim()
  if (!base.startsWith('/') || /[?#\\%]/.test(base)) {
    throw invalidResponse()
  }
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
  if (isRecord(value)) {
    if (isRecord(value.error) && isSafeErrorCode(value.error.code) && isSafeErrorMessage(value.error.message)) {
      return new ApiError(status, value.error.code, value.error.message)
    }
    if (isSafeErrorCode(value.code) && isSafeErrorMessage(value.message)) {
      return new ApiError(status, value.code, value.message)
    }
  }

  let code = 'request_failed'
  let message = genericErrorMessage

  if (status === 404) {
    code = 'not_found'
    message = 'The requested reader panel resource was not found.'
  } else if (status === 409) {
    code = 'conflict'
    message = 'The reader panel operation could not be completed due to a conflict.'
  } else if (status === 422) {
    code = 'validation_error'
    message = 'The reader panel request failed validation.'
  } else if (status === 503) {
    code = 'provider_unavailable'
    message = 'The reader panel provider is unavailable or timed out.'
  }

  return new ApiError(status, code, message)
}

interface RequestOptions {
  signal?: AbortSignal
}

async function request<T>(
  method: 'GET' | 'POST',
  pathProvider: () => string,
  decoder: (value: unknown) => T,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const url = pathProvider()
  const headers: Record<string, string> = {
    Accept: 'application/json',
  }
  let payload: string | undefined
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  let response: Response
  try {
    response = await fetch(url, {
      method,
      headers,
      body: payload,
      credentials: 'same-origin',
      signal: options.signal,
    })
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error
    }
    if (error instanceof ApiError) throw error
    throw new ApiError(0, 'network_error', genericErrorMessage)
  }

  if (!response.ok) {
    let errorPayload: unknown
    try {
      errorPayload = await response.json()
    } catch {
      // ignore
    }
    throw safeErrorFromStatus(response.status, errorPayload)
  }

  let json: unknown
  try {
    json = await response.json()
  } catch {
    throw invalidResponse()
  }
  return decoder(json)
}

export function startReaderPanel(
  projectId: string,
  chapterId: string,
  payload: ReaderPanelStartPayload,
  signal?: AbortSignal,
): Promise<ReaderPanelDetail> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const docId = validateUuid(payload.document_id)
  const docVerId = validateUuid(payload.document_version_id)

  const mode = payload.mode ?? 'standard'
  if (!PANEL_MODES.has(mode)) {
    throw new ApiError(422, 'invalid_request', 'Invalid reader panel mode.')
  }

  const cleanPayload: Record<string, unknown> = {
    document_id: docId,
    document_version_id: docVerId,
    mode,
  }

  if (payload.config_overrides !== undefined && payload.config_overrides !== null) {
    if (!isRecord(payload.config_overrides)) {
      throw new ApiError(422, 'invalid_request', 'Invalid config overrides.')
    }
    const cleanOverrides: Record<string, unknown> = {}
    if (payload.config_overrides.max_ballot_issues !== undefined && payload.config_overrides.max_ballot_issues !== null) {
      cleanOverrides.max_ballot_issues = validatePositiveInteger(payload.config_overrides.max_ballot_issues)
    }
    if (payload.config_overrides.max_discussion_issues !== undefined && payload.config_overrides.max_discussion_issues !== null) {
      cleanOverrides.max_discussion_issues = validateNonNegativeInteger(payload.config_overrides.max_discussion_issues)
    }
    if (payload.config_overrides.max_rounds_per_issue !== undefined && payload.config_overrides.max_rounds_per_issue !== null) {
      cleanOverrides.max_rounds_per_issue = validateNonNegativeInteger(payload.config_overrides.max_rounds_per_issue)
    }
    if (payload.config_overrides.min_valid_readers !== undefined && payload.config_overrides.min_valid_readers !== null) {
      cleanOverrides.min_valid_readers = validatePositiveInteger(payload.config_overrides.min_valid_readers)
    }
    cleanPayload.config_overrides = cleanOverrides
  }

  if (payload.test_goals !== undefined && payload.test_goals !== null) {
    if (!Array.isArray(payload.test_goals)) {
      throw new ApiError(422, 'invalid_request', 'Test goals must be a list of strings.')
    }
    cleanPayload.test_goals = payload.test_goals.map((g) => validateNonEmptyString(g, 256))
  }

  if (payload.target_audience !== undefined && payload.target_audience !== null) {
    if (!Array.isArray(payload.target_audience)) {
      throw new ApiError(422, 'invalid_request', 'Target audience must be a list of strings.')
    }
    cleanPayload.target_audience = payload.target_audience.map((a) => validateNonEmptyString(a, 256))
  }

  if (payload.idempotency_key !== undefined && payload.idempotency_key !== null) {
    if (typeof payload.idempotency_key !== 'string' || !IDEMPOTENCY_KEY_PATTERN.test(payload.idempotency_key)) {
      throw new ApiError(422, 'invalid_request', 'Invalid idempotency key format.')
    }
    cleanPayload.idempotency_key = payload.idempotency_key
  }

  return request(
    'POST',
    () => apiPath('projects', pId, 'chapters', cId, 'reader-panels'),
    decodeReaderPanelDetail,
    cleanPayload,
    { signal },
  )
}

export function listReaderPanels(
  projectId: string,
  chapterId: string,
  options?: ListReaderPanelsOptions,
  signal?: AbortSignal,
): Promise<ReaderPanelDetail[]> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const queryParams = new URLSearchParams()

  if (options?.offset !== undefined) {
    if (!Number.isInteger(options.offset) || options.offset < 0) {
      throw new ApiError(0, 'invalid_request', 'Invalid query options.')
    }
    queryParams.set('offset', String(options.offset))
  }
  if (options?.limit !== undefined) {
    if (!Number.isInteger(options.limit) || options.limit < 1 || options.limit > 100) {
      throw new ApiError(0, 'invalid_request', 'Invalid query options.')
    }
    queryParams.set('limit', String(options.limit))
  }
  if (options?.include_initial_reports !== undefined) {
    queryParams.set('include_initial_reports', String(Boolean(options.include_initial_reports)))
  }
  if (options?.include_transcript !== undefined) {
    queryParams.set('include_transcript', String(Boolean(options.include_transcript)))
  }
  if (options?.data_limit !== undefined) {
    if (!Number.isInteger(options.data_limit) || options.data_limit < 1 || options.data_limit > 200) {
      throw new ApiError(0, 'invalid_request', 'Invalid query options.')
    }
    queryParams.set('data_limit', String(options.data_limit))
  }

  const queryString = queryParams.toString()
  const query = queryString ? `?${queryString}` : ''

  return request(
    'GET',
    () => `${apiPath('projects', pId, 'chapters', cId, 'reader-panels')}${query}`,
    (value) => {
      if (!Array.isArray(value)) throw invalidResponse()
      return value.map(decodeReaderPanelDetail)
    },
    undefined,
    { signal },
  )
}

export function getReaderPanel(
  projectId: string,
  chapterId: string,
  sessionId: string,
  options?: GetReaderPanelOptions,
  signal?: AbortSignal,
): Promise<ReaderPanelDetail> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const sId = validateUuid(sessionId)

  const queryParams = new URLSearchParams()
  if (options?.include_initial_reports !== undefined) {
    queryParams.set('include_initial_reports', String(Boolean(options.include_initial_reports)))
  }
  if (options?.include_transcript !== undefined) {
    queryParams.set('include_transcript', String(Boolean(options.include_transcript)))
  }
  if (options?.data_limit !== undefined) {
    if (!Number.isInteger(options.data_limit) || options.data_limit < 1 || options.data_limit > 200) {
      throw new ApiError(0, 'invalid_request', 'Invalid query options.')
    }
    queryParams.set('data_limit', String(options.data_limit))
  }

  const queryString = queryParams.toString()
  const query = queryString ? `?${queryString}` : ''

  return request(
    'GET',
    () => `${apiPath('projects', pId, 'chapters', cId, 'reader-panels', sId)}${query}`,
    decodeReaderPanelDetail,
    undefined,
    { signal },
  )
}

export function cancelReaderPanel(
  projectId: string,
  chapterId: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<ReaderPanelDetail> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const sId = validateUuid(sessionId)

  return request(
    'POST',
    () => apiPath('projects', pId, 'chapters', cId, 'reader-panels', sId, 'cancel'),
    decodeReaderPanelDetail,
    {},
    { signal },
  )
}

export function resumeReaderPanel(
  projectId: string,
  chapterId: string,
  sessionId: string,
  signal?: AbortSignal,
): Promise<ReaderPanelDetail> {
  const pId = validateUuid(projectId)
  const cId = validateUuid(chapterId)
  const sId = validateUuid(sessionId)

  return request(
    'POST',
    () => apiPath('projects', pId, 'chapters', cId, 'reader-panels', sId, 'resume'),
    decodeReaderPanelDetail,
    {},
    { signal },
  )
}

export interface ReaderPanelRouteIdentity {
  projectId: string
  chapterId: string
  sessionId: string
}

export interface ReaderPanelPollOptions {
  maxAttempts: number
  intervalMs: number
  includeInitialReports?: boolean
  includeTranscript?: boolean
  onUpdate: (panel: ReaderPanelSessionDetail) => void
  onError?: (error: ApiError) => void
}

export type ReaderPanelPollResult = 'completed' | 'max_attempts' | 'cancelled' | 'failed'

type ReaderPanelLoader = (
  projectId: string,
  chapterId: string,
  sessionId: string,
  options?: GetReaderPanelOptions,
  signal?: AbortSignal,
) => Promise<ReaderPanelDetail>

const stoppingStatuses = new Set<ReaderPanelStatus>([
  'completed',
  'degraded_completed',
  'failed',
  'cancelled',
  'off',
])

function safeQueryError(error: unknown): ApiError {
  return error instanceof ApiError
    ? error
    : new ApiError(0, 'request_failed', genericErrorMessage)
}

function waitForNextAttempt(delayMs: number, signal: AbortSignal): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false)
  if (delayMs === 0) return Promise.resolve(true)
  return new Promise((resolve) => {
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve(true)
    }, delayMs)
    const onAbort = (): void => {
      window.clearTimeout(timer)
      resolve(false)
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

export class ReaderPanelPoller {
  private generation = 0
  private controller: AbortController | null = null
  private readonly loadPanel: ReaderPanelLoader

  constructor(loadPanel: ReaderPanelLoader = getReaderPanel) {
    this.loadPanel = loadPanel
  }

  cancel(): void {
    this.generation += 1
    this.controller?.abort()
    this.controller = null
  }

  private releaseController(generation: number, controller: AbortController): void {
    if (generation === this.generation && this.controller === controller) this.controller = null
  }

  async poll(
    identity: ReaderPanelRouteIdentity,
    options: ReaderPanelPollOptions,
  ): Promise<ReaderPanelPollResult> {
    if (
      !identity.projectId
      || !identity.chapterId
      || !identity.sessionId
      || !Number.isInteger(options.maxAttempts)
      || options.maxAttempts < 1
      || options.maxAttempts > 100
      || !Number.isInteger(options.intervalMs)
      || options.intervalMs < 0
      || options.intervalMs > 60_000
    ) {
      throw new ApiError(0, 'invalid_request', 'The reader panel query parameters are invalid.')
    }

    this.controller?.abort()
    const generation = ++this.generation
    const controller = new AbortController()
    this.controller = controller

    for (let attempt = 0; attempt < options.maxAttempts; attempt += 1) {
      let panel: ReaderPanelDetail
      try {
        panel = await this.loadPanel(
          identity.projectId,
          identity.chapterId,
          identity.sessionId,
          {
            include_initial_reports: options.includeInitialReports,
            include_transcript: options.includeTranscript,
          },
          controller.signal,
        )
      } catch (error: unknown) {
        if (generation !== this.generation || controller.signal.aborted) return 'cancelled'
        options.onError?.(safeQueryError(error))
        if (generation !== this.generation || controller.signal.aborted) return 'cancelled'
        this.releaseController(generation, controller)
        return 'failed'
      }

      if (generation !== this.generation || controller.signal.aborted) return 'cancelled'

      if (panel.is_noop || panel.session_id !== identity.sessionId) {
        options.onError?.(new ApiError(0, 'invalid_response', invalidResponseMessage))
        if (generation !== this.generation || controller.signal.aborted) return 'cancelled'
        this.releaseController(generation, controller)
        return 'failed'
      }

      options.onUpdate(panel)
      if (generation !== this.generation || controller.signal.aborted) return 'cancelled'

      if (stoppingStatuses.has(panel.status)) {
        this.releaseController(generation, controller)
        return 'completed'
      }

      if (attempt + 1 < options.maxAttempts) {
        const canContinue = await waitForNextAttempt(options.intervalMs, controller.signal)
        if (!canContinue || generation !== this.generation || controller.signal.aborted) {
          return 'cancelled'
        }
      }
    }

    this.releaseController(generation, controller)
    return 'max_attempts'
  }
}
