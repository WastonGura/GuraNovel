import { getApiBaseUrl } from '../config'

type JsonPrimitive = string | number | boolean | null
export type JsonValue = JsonPrimitive | { [key: string]: JsonValue } | JsonValue[]
export type Metadata = Record<string, JsonValue>

export interface Project {
  id: string
  slug: string
  title: string
  genre: string | null
  target_platform: string | null
  status: string
  workspace_root: string
  metadata: Metadata
  created_at: string
  updated_at: string
}

export interface CreateProjectRequest {
  slug: string
  title: string
  genre?: string | null
  target_platform?: string | null
  metadata?: Metadata
}

export interface Chapter {
  id: string
  project_id: string
  chapter_number: number
  title: string | null
  status: string
  current_outline_document_id: string | null
  current_draft_document_id: string | null
  final_document_id: string | null
  summary_document_id: string | null
  word_count: number
  metadata: Metadata
  created_at: string
  updated_at: string
}

export interface CreateChapterRequest {
  title?: string | null
  metadata?: Metadata
}

export interface ChapterProductionAction {
  id: string
  type: string
  status: string
  options: string[]
  default_option: string | null
  user_decision: string | null
}

export type ChapterProductionEventType =
  | 'production_started'
  | 'generation_provenance'
  | 'generation_output_stored'
  | 'fake_output_stored'
  | 'awaiting_approval'
  | 'approval_approved'
  | 'approval_rejected'

export type ChapterProductionEventPayload =
  | Record<never, never>
  | {
      provider_kind: string
      model_identifier: string
      prompt_template_version: string
      input_tokens?: number
      output_tokens?: number
    }
  | { outline_document_id: string }
  | { decision: 'approved' | 'rejected'; action_id: string }

export interface ChapterProductionEvent {
  event_type: ChapterProductionEventType
  node_name: string | null
  message: string | null
  payload: ChapterProductionEventPayload
}

export interface ChapterProductionRun {
  id: string
  type: string
  status: string
  current_node: string | null
  next_node: string | null
  awaiting_user: boolean
  actions: ChapterProductionAction[]
  events: ChapterProductionEvent[]
  outline_document_id: string | null
  draft_document_id: string | null
}

export interface ResolveChapterProductionActionRequest {
  decision: 'approved' | 'rejected'
}

export type DocumentType =
  | 'project_yaml'
  | 'pitch'
  | 'synopsis'
  | 'style_guide'
  | 'world_overview'
  | 'power_system'
  | 'factions'
  | 'geography'
  | 'history'
  | 'character_profile'
  | 'main_cast'
  | 'full_outline'
  | 'volume_outline'
  | 'first_30_chapters'
  | 'chapter_outline_options'
  | 'chapter_selected_outline'
  | 'chapter_draft'
  | 'chapter_final'
  | 'chapter_summary'
  | 'archive_update'
  | 'review_artifact'
  | 'foreshadowing'
  | 'unresolved_threads'
  | 'glossary'
  | 'maintenance_plan'
  | 'maintenance_report'

export type DocumentSource =
  | 'user'
  | 'concept_agent'
  | 'chief_editor_agent'
  | 'protagonist_agent'
  | 'worldbuilding_agent'
  | 'plot_architect_agent'
  | 'style_guide_agent'
  | 'outline_agent'
  | 'writer_agent'
  | 'editor_agent'
  | 'lore_agent'
  | 'archivist_agent'
  | 'system'

export interface DocumentVersion {
  id: string
  document_id: string
  version_number: number
  parent_version_id: string | null
  source: DocumentSource
  actor_user_id: string | null
  agent_role: string | null
  workflow_run_id: string | null
  content_hash: string
  byte_size: number
  word_count: number
  file_path: string
  change_summary: string | null
  created_at: string
}

export interface Document {
  id: string
  project_id: string
  chapter_id: string | null
  type: DocumentType
  title: string | null
  path: string
  current_version_id: string | null
  current_version: DocumentVersion | null
  created_at: string
  updated_at: string
}

export interface DocumentContent {
  document_id: string
  version_id: string
  content: string
}

export interface CreateDocumentRequest {
  project_id: string
  type: DocumentType
  path: string
  content: string
  title?: string | null
  source?: DocumentSource
  chapter_id?: string | null
  actor_user_id?: string | null
  agent_role?: string | null
  workflow_run_id?: string | null
  change_summary?: string | null
}

export interface WriteDocumentRequest {
  content: string
  expected_current_version_id: string
  source?: DocumentSource
  actor_user_id?: string | null
  agent_role?: string | null
  workflow_run_id?: string | null
  change_summary?: string | null
}

export interface RestoreDocumentRequest {
  expected_current_version_id: string
  source?: DocumentSource
  actor_user_id?: string | null
  agent_role?: string | null
  workflow_run_id?: string | null
  change_summary?: string | null
}

export type ProjectMaintenanceScopeHint =
  | 'chapter'
  | 'character'
  | 'world'
  | 'outline'
  | 'foreshadowing'
  | 'timeline'
  | 'style'

export type ProjectMaintenanceDecision = 'approve' | 'revise' | 'cancel' | 'accept_warning'

export type ProjectMaintenanceStatus =
  | 'CHANGE_REQUESTED'
  | 'LORE_IMPACT_ANALYSIS'
  | 'CHIEF_EDITOR_IMPACT_ANALYSIS'
  | 'REVISION_PLAN'
  | 'USER_CONFIRMATION'
  | 'APPLY_CHANGE'
  | 'CONSISTENCY_REVIEW'
  | 'PROJECT_UPDATED'
  | 'CANCELLED'

export interface StartProjectMaintenanceRequest {
  title: string
  change_request: string
  scope_hints?: ProjectMaintenanceScopeHint[]
}

export interface ProjectMaintenanceAffectedItem {
  id: string
  position: number
  type: ProjectMaintenanceScopeHint
  stable_reference: string
  impact_level: 'low' | 'medium' | 'high'
  reason: string
  document_id: string | null
  chapter_id: string | null
}

export interface ProjectMaintenanceRevisionOperation {
  id: string
  sequence: number
  operation: 'revise' | 'retain'
  document_id: string
  expected_version_id: string
  affected_item_ids: string[]
  instruction: string
}

export interface ProjectMaintenanceRevisionPlan {
  id: string
  document_id: string
  version_id: string
  review_outcome: 'passed' | 'warning' | 'blocking'
  summary: string
  operations: ProjectMaintenanceRevisionOperation[]
}

export interface ProjectMaintenanceConsistencyDocument {
  document_id: string
  version_id: string
}

export interface ProjectMaintenanceConsistencyFinding {
  id: string
  sequence: number
  code: string
  severity: 'warning' | 'blocking'
  blocking: boolean
  affected_documents: ProjectMaintenanceConsistencyDocument[]
  suggested_corrective_action: string
}

export interface ProjectMaintenanceConsistencyReview {
  id: string
  outcome: 'clean' | 'warning' | 'blocking'
  findings: ProjectMaintenanceConsistencyFinding[]
}

export interface ProjectMaintenancePendingAction {
  id: string
  type: 'project_maintenance_revision_confirmation' | 'project_maintenance_consistency_warning'
  status: 'pending'
  confirmation_kind: 'revision_confirmation' | 'consistency_warning'
  review_outcome: 'passed' | 'warning' | 'blocking'
  allowed_decisions: ProjectMaintenanceDecision[]
}

export interface ProjectMaintenanceRun {
  id: string
  maintenance_change_id: string
  type: 'project_maintenance'
  status: ProjectMaintenanceStatus
  current_node: string
  next_node: null
  awaiting_user: boolean
  title: string
  change_request: string
  created_at: string
  updated_at: string
  completed_at: string | null
  affected_items: ProjectMaintenanceAffectedItem[]
  revision_plan: ProjectMaintenanceRevisionPlan | null
  consistency_review: ProjectMaintenanceConsistencyReview | null
  applied_document_version_ids: string[]
  pending_action: ProjectMaintenancePendingAction | null
}

export interface ProjectMaintenanceHistorySummary {
  id: string
  maintenance_change_id: string
  status: ProjectMaintenanceStatus
  title: string
  awaiting_user: boolean
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface ProjectMaintenanceListOptions {
  offset?: number
  limit?: number
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

const genericErrorMessage = 'The request could not be completed.'
const invalidResponseMessage = 'The server returned an invalid response.'
const eventTypes = new Set<ChapterProductionEventType>([
  'production_started',
  'generation_provenance',
  'generation_output_stored',
  'fake_output_stored',
  'awaiting_approval',
  'approval_approved',
  'approval_rejected',
])
const documentTypes = new Set<DocumentType>([
  'project_yaml', 'pitch', 'synopsis', 'style_guide', 'world_overview', 'power_system', 'factions',
  'geography', 'history', 'character_profile', 'main_cast', 'full_outline', 'volume_outline',
  'first_30_chapters', 'chapter_outline_options', 'chapter_selected_outline', 'chapter_draft',
  'chapter_final', 'chapter_summary', 'archive_update', 'review_artifact', 'foreshadowing',
  'unresolved_threads', 'glossary', 'maintenance_plan', 'maintenance_report',
])
const documentSources = new Set<DocumentSource>([
  'user', 'concept_agent', 'chief_editor_agent', 'protagonist_agent', 'worldbuilding_agent',
  'plot_architect_agent', 'style_guide_agent', 'outline_agent', 'writer_agent', 'editor_agent',
  'lore_agent', 'archivist_agent', 'system',
])

function invalidResponse(): ApiError {
  return new ApiError(0, 'invalid_response', invalidResponseMessage)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return true
  }
  if (Array.isArray(value)) return value.every(isJsonValue)
  return isRecord(value) && Object.values(value).every(isJsonValue)
}

function string(value: unknown): string {
  if (typeof value !== 'string') throw invalidResponse()
  return value
}

function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value)) throw invalidResponse()
  return value
}

function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw invalidResponse()
  return value
}

function nullableString(value: unknown): string | null {
  if (value === null) return null
  return string(value)
}

function metadata(value: unknown): Metadata {
  if (!isRecord(value) || !Object.values(value).every(isJsonValue)) throw invalidResponse()
  return value as Metadata
}

function object(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw invalidResponse()
  return value
}

function decodeProject(value: unknown): Project {
  const data = object(value)
  return {
    id: string(data.id), slug: string(data.slug), title: string(data.title), genre: nullableString(data.genre),
    target_platform: nullableString(data.target_platform), status: string(data.status),
    workspace_root: string(data.workspace_root), metadata: metadata(data.metadata),
    created_at: string(data.created_at), updated_at: string(data.updated_at),
  }
}

function decodeChapter(value: unknown): Chapter {
  const data = object(value)
  return {
    id: string(data.id), project_id: string(data.project_id), chapter_number: integer(data.chapter_number),
    title: nullableString(data.title), status: string(data.status),
    current_outline_document_id: nullableString(data.current_outline_document_id),
    current_draft_document_id: nullableString(data.current_draft_document_id),
    final_document_id: nullableString(data.final_document_id), summary_document_id: nullableString(data.summary_document_id),
    word_count: integer(data.word_count), metadata: metadata(data.metadata),
    created_at: string(data.created_at), updated_at: string(data.updated_at),
  }
}

function decodeAction(value: unknown): ChapterProductionAction {
  const data = object(value)
  if (!Array.isArray(data.options)) throw invalidResponse()
  return {
    id: string(data.id), type: string(data.type), status: string(data.status), options: data.options.map(string),
    default_option: nullableString(data.default_option), user_decision: nullableString(data.user_decision),
  }
}

function hasOnlyKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).every((key) => keys.includes(key))
}

function decodeEventPayload(eventType: ChapterProductionEventType, value: unknown): ChapterProductionEventPayload {
  const payload = object(value)
  if (eventType === 'production_started' || eventType === 'awaiting_approval') {
    if (Object.keys(payload).length !== 0) throw invalidResponse()
    return {}
  }
  if (eventType === 'generation_provenance') {
    if (!hasOnlyKeys(payload, ['provider_kind', 'model_identifier', 'prompt_template_version', 'input_tokens', 'output_tokens'])) throw invalidResponse()
    const decoded: {
      provider_kind: string
      model_identifier: string
      prompt_template_version: string
      input_tokens?: number
      output_tokens?: number
    } = {
      provider_kind: string(payload.provider_kind), model_identifier: string(payload.model_identifier),
      prompt_template_version: string(payload.prompt_template_version),
    }
    if (payload.input_tokens !== undefined) decoded.input_tokens = integer(payload.input_tokens)
    if (payload.output_tokens !== undefined) decoded.output_tokens = integer(payload.output_tokens)
    return decoded
  }
  if (eventType === 'generation_output_stored' || eventType === 'fake_output_stored') {
    if (!hasOnlyKeys(payload, ['outline_document_id'])) throw invalidResponse()
    return { outline_document_id: string(payload.outline_document_id) }
  }
  if (!hasOnlyKeys(payload, ['decision', 'action_id'])) throw invalidResponse()
  const decision = string(payload.decision)
  const expectedDecision = eventType === 'approval_approved' ? 'approved' : 'rejected'
  if (decision !== expectedDecision) throw invalidResponse()
  return { decision: expectedDecision, action_id: string(payload.action_id) }
}

function decodeEvent(value: unknown): ChapterProductionEvent {
  const data = object(value)
  const eventType = string(data.event_type)
  if (!eventTypes.has(eventType as ChapterProductionEventType)) throw invalidResponse()
  return {
    event_type: eventType as ChapterProductionEventType,
    node_name: nullableString(data.node_name),
    message: nullableString(data.message),
    payload: decodeEventPayload(eventType as ChapterProductionEventType, data.payload),
  }
}

function decodeRun(value: unknown): ChapterProductionRun {
  const data = object(value)
  if (!Array.isArray(data.actions) || !Array.isArray(data.events)) throw invalidResponse()
  return {
    id: string(data.id), type: string(data.type), status: string(data.status),
    current_node: nullableString(data.current_node), next_node: nullableString(data.next_node),
    awaiting_user: boolean(data.awaiting_user), actions: data.actions.map(decodeAction), events: data.events.map(decodeEvent),
    outline_document_id: nullableString(data.outline_document_id), draft_document_id: nullableString(data.draft_document_id),
  }
}

function decodeDocumentVersion(value: unknown): DocumentVersion {
  const data = object(value)
  const source = string(data.source)
  if (!documentSources.has(source as DocumentSource)) throw invalidResponse()
  return {
    id: string(data.id), document_id: string(data.document_id), version_number: integer(data.version_number),
    parent_version_id: nullableString(data.parent_version_id), source: source as DocumentSource,
    actor_user_id: nullableString(data.actor_user_id), agent_role: nullableString(data.agent_role),
    workflow_run_id: nullableString(data.workflow_run_id), content_hash: string(data.content_hash),
    byte_size: integer(data.byte_size), word_count: integer(data.word_count), file_path: string(data.file_path),
    change_summary: nullableString(data.change_summary), created_at: string(data.created_at),
  }
}

function decodeDocument(value: unknown): Document {
  const data = object(value)
  const type = string(data.type)
  if (!documentTypes.has(type as DocumentType)) throw invalidResponse()
  return {
    id: string(data.id), project_id: string(data.project_id), chapter_id: nullableString(data.chapter_id),
    type: type as DocumentType, title: nullableString(data.title), path: string(data.path),
    current_version_id: nullableString(data.current_version_id),
    current_version: data.current_version === null ? null : decodeDocumentVersion(data.current_version),
    created_at: string(data.created_at), updated_at: string(data.updated_at),
  }
}

function decodeDocumentContent(value: unknown): DocumentContent {
  const data = object(value)
  return { document_id: string(data.document_id), version_id: string(data.version_id), content: string(data.content) }
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
  return typeof value === 'string'
    && value.length > 0
    && value.length <= 500
    && !Array.from(value).some((character) => {
      const code = character.charCodeAt(0)
      return code < 32 || code === 127
    })
}

function errorFromEnvelope(status: number, value: unknown): ApiError {
  if (isRecord(value) && isRecord(value.error) && isSafeErrorCode(value.error.code) && isSafeErrorMessage(value.error.message)) {
    return new ApiError(status, value.error.code, value.error.message)
  }
  return new ApiError(status, 'request_failed', genericErrorMessage)
}

interface RequestOptions {
  signal?: AbortSignal
  decodeError?: (status: number, value: unknown) => ApiError
}

async function request<T>(
  method: 'GET' | 'POST' | 'PUT',
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
  if (!response.ok) throw (options.decodeError ?? errorFromEnvelope)(response.status, payload)
  return decode(payload)
}

export function listProjects(): Promise<Project[]> {
  return request('GET', () => apiPath('projects'), (value) => {
    if (!Array.isArray(value)) throw invalidResponse()
    return value.map(decodeProject)
  })
}

export function createProject(payload: CreateProjectRequest): Promise<Project> {
  return request('POST', () => apiPath('projects'), decodeProject, payload)
}

export function getProject(projectId: string): Promise<Project> {
  return request('GET', () => apiPath('projects', projectId), decodeProject)
}

export function listChapters(projectId: string): Promise<Chapter[]> {
  return request('GET', () => apiPath('projects', projectId, 'chapters'), (value) => {
    if (!Array.isArray(value)) throw invalidResponse()
    return value.map(decodeChapter)
  })
}

export function createChapter(projectId: string, payload: CreateChapterRequest): Promise<Chapter> {
  return request('POST', () => apiPath('projects', projectId, 'chapters'), decodeChapter, payload)
}

export function getChapter(projectId: string, chapterId: string): Promise<Chapter> {
  return request('GET', () => apiPath('projects', projectId, 'chapters', chapterId), decodeChapter)
}

export function startChapterProduction(projectId: string, chapterId: string): Promise<ChapterProductionRun> {
  return request('POST', () => apiPath('projects', projectId, 'chapters', chapterId, 'production-runs'), decodeRun)
}

export function getChapterProduction(projectId: string, chapterId: string, runId: string): Promise<ChapterProductionRun> {
  return request('GET', () => apiPath('projects', projectId, 'chapters', chapterId, 'production-runs', runId), decodeRun)
}

export function resolveChapterProductionAction(
  projectId: string, chapterId: string, runId: string, actionId: string, payload: ResolveChapterProductionActionRequest,
): Promise<ChapterProductionRun> {
  return request('POST', () => apiPath('projects', projectId, 'chapters', chapterId, 'production-runs', runId, 'actions', actionId, 'resolve'), decodeRun, payload)
}

export function createDocument(payload: CreateDocumentRequest): Promise<Document> {
  return request('POST', () => apiPath('documents'), decodeDocument, payload)
}

export function getDocument(documentId: string): Promise<Document> {
  return request('GET', () => apiPath('documents', documentId), decodeDocument)
}

export function readDocumentContent(documentId: string): Promise<DocumentContent> {
  return request('GET', () => apiPath('documents', documentId, 'content'), decodeDocumentContent)
}

export function listDocumentVersions(documentId: string): Promise<DocumentVersion[]> {
  return request('GET', () => apiPath('documents', documentId, 'versions'), (value) => {
    if (!Array.isArray(value)) throw invalidResponse()
    return value.map(decodeDocumentVersion)
  })
}

export function readDocumentVersionContent(documentId: string, versionId: string): Promise<DocumentContent> {
  return request('GET', () => apiPath('documents', documentId, 'versions', versionId, 'content'), decodeDocumentContent)
}

export function writeDocument(documentId: string, payload: WriteDocumentRequest): Promise<DocumentVersion> {
  return request('PUT', () => apiPath('documents', documentId, 'content'), decodeDocumentVersion, payload)
}

export function restoreDocument(
  documentId: string, versionId: string, payload: RestoreDocumentRequest,
): Promise<DocumentVersion> {
  return request('POST', () => apiPath('documents', documentId, 'versions', versionId, 'restore'), decodeDocumentVersion, payload)
}

export interface ProjectCreationConceptOption {
  id: string
  title: string
  logline: string
  premise: string
  genres: string[]
}

export interface ProjectCreationBlockingIssue {
  code: string
  message: string
}

export interface ProjectCreationPendingAction {
  id: string
  type: string
  status: string
  allowed_decisions: string[]
  review_severity: string | null
  blocking_issues: ProjectCreationBlockingIssue[]
  concept_options: ProjectCreationConceptOption[]
}

export interface ProjectCreationStarted {
  id: string
  status: string
  pending_action: ProjectCreationPendingAction
}

export interface StartProjectCreationRequest {
  user_seed: string
  target_platform?: string | null
  preferred_genres?: string[]
  disliked_elements?: string[]
  style_preference?: string | null
}

export interface ProjectCreationRun {
  id: string
  type: string
  status: string
  current_node: string | null
  next_node: string | null
  awaiting_user: boolean
  pending_action: ProjectCreationPendingAction | null
}

function unicodeCodePointLength(value: string): number {
  return Array.from(value).length
}

function decodeProjectCreationBlockingIssue(value: unknown): ProjectCreationBlockingIssue {
  const data = object(value)
  const code = string(data.code)
  const message = string(data.message)
  if (
    !/^[a-z][a-z0-9_-]{0,63}$/.test(code)
    || unicodeCodePointLength(message) < 1
    || unicodeCodePointLength(message) > 500
  ) throw invalidResponse()
  return { code, message }
}

function decodeProjectCreationConceptOption(value: unknown): ProjectCreationConceptOption {
  const data = object(value)
  const id = string(data.id)
  const title = string(data.title)
  const logline = string(data.logline)
  const premise = string(data.premise)
  if (
    !/^[a-z][a-z0-9-]{0,63}$/.test(id)
    || !title || unicodeCodePointLength(title) > 160 || /[\r\n]/.test(title)
    || !logline || unicodeCodePointLength(logline) > 600 || /[\r\n]/.test(logline)
    || !premise || unicodeCodePointLength(premise) > 2000 || /[\r\n]/.test(premise)
    || !Array.isArray(data.genres)
    || data.genres.length < 1
    || data.genres.length > 6
  ) {
    throw invalidResponse()
  }
  const genres = data.genres.map(string)
  if (genres.some((genre) => !genre || unicodeCodePointLength(genre) > 256 || /[,\r\n]/.test(genre))) {
    throw invalidResponse()
  }
  return { id, title, logline, premise, genres }
}

function decodeProjectCreationPendingAction(value: unknown): ProjectCreationPendingAction {
  const data = object(value)
  if (
    !Array.isArray(data.allowed_decisions)
    || !Array.isArray(data.blocking_issues)
    || !Array.isArray(data.concept_options)
    || data.blocking_issues.length > 12
    || data.concept_options.length > 5
  ) throw invalidResponse()
  return {
    id: string(data.id), type: string(data.type), status: string(data.status),
    allowed_decisions: data.allowed_decisions.map(string),
    review_severity: nullableString(data.review_severity),
    blocking_issues: data.blocking_issues.map(decodeProjectCreationBlockingIssue),
    concept_options: data.concept_options.map(decodeProjectCreationConceptOption),
  }
}

function decodeProjectCreationStarted(value: unknown): ProjectCreationStarted {
  const data = object(value)
  return {
    id: string(data.id),
    status: string(data.status),
    pending_action: decodeProjectCreationPendingAction(data.pending_action),
  }
}

export function startProjectCreation(
  projectId: string,
  payload: StartProjectCreationRequest,
): Promise<ProjectCreationStarted> {
  return request('POST', () => apiPath('projects', projectId, 'creation', 'start'), decodeProjectCreationStarted, payload as unknown as Record<string, unknown>)
}

function decodeProjectCreationRun(value: unknown): ProjectCreationRun {
  const data = object(value)
  return {
    id: string(data.id), type: string(data.type), status: string(data.status),
    current_node: nullableString(data.current_node), next_node: nullableString(data.next_node),
    awaiting_user: boolean(data.awaiting_user),
    pending_action: data.pending_action === null ? null : decodeProjectCreationPendingAction(data.pending_action),
  }
}

export function getProjectCreationRun(projectId: string, workflowRunId: string): Promise<ProjectCreationRun> {
  return request('GET', () => apiPath('projects', projectId, 'creation', workflowRunId), decodeProjectCreationRun)
}

export type ResolveProjectCreationActionRequest =
  | { decision: 'select'; option_id: string }
  | { decision: 'fuse'; fused_concept: string }
  | { decision: 'regenerate' }
  | { decision: 'feedback'; feedback: string }

export interface ResolveProjectCreationActionResponse {
  status: string
}

export function resolveProjectCreationAction(
  projectId: string,
  workflowRunId: string,
  actionId: string,
  body: ResolveProjectCreationActionRequest,
): Promise<ResolveProjectCreationActionResponse> {
  return request(
    'POST',
    () => apiPath('projects', projectId, 'creation', workflowRunId, 'actions', actionId, 'resolve'),
    (value) => {
      const data = object(value)
      return { status: string(data.status) }
    },
    body,
  )
}

const maintenanceScopeHints = new Set<ProjectMaintenanceScopeHint>([
  'chapter', 'character', 'world', 'outline', 'foreshadowing', 'timeline', 'style',
])
const maintenanceDecisions = new Set<ProjectMaintenanceDecision>([
  'approve', 'revise', 'cancel', 'accept_warning',
])
const maintenanceNodes: Record<ProjectMaintenanceStatus, string> = {
  CHANGE_REQUESTED: 'user_change_request',
  LORE_IMPACT_ANALYSIS: 'lore_impact_analysis',
  CHIEF_EDITOR_IMPACT_ANALYSIS: 'chief_editor_impact_review',
  REVISION_PLAN: 'revision_plan',
  USER_CONFIRMATION: 'user_confirm_revision',
  APPLY_CHANGE: 'apply_revision',
  CONSISTENCY_REVIEW: 'consistency_review',
  PROJECT_UPDATED: 'project_updated',
  CANCELLED: 'cancelled',
}
const maintenanceStatuses = new Set<ProjectMaintenanceStatus>(
  Object.keys(maintenanceNodes) as ProjectMaintenanceStatus[],
)
const maintenanceUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const maintenanceSafeCode = /^[a-z][a-z0-9_]{0,63}$/
const maintenanceStableReference = /^(chapter|character|world|outline|foreshadowing|timeline|style)\/[a-z0-9][a-z0-9_-]{0,63}$/
const maintenanceWindowsDrive = /(?:^|[^a-z0-9])[a-z]:/i
const maintenanceWindowsDevice = /(?:^|[^a-z0-9])(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:$|[^a-z0-9])/i
const maintenanceEncodedPath = /%(?:[0-9a-f]{2}|u[0-9a-f]{4})/i
const maintenanceExternalUri = /(?:^|[^a-z0-9])(?:[a-z][a-z0-9+.-]*):(?=\S)/i
const maintenanceDottedToken = /(?:^|[^a-z0-9])(?:[a-z0-9_-][a-z0-9_-]*(?:\.[a-z0-9_-]+)*\.[a-z]{2,63})(?:$|[^a-z0-9])/i
const maintenanceCredential = /(?:\b(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|password|passwd|secret|token)\b\s*[:=]\s*\S+|\bsk-[a-z0-9_-]{8,}\b)/i

function exactObject(value: unknown, fields: readonly string[]): Record<string, unknown> {
  const data = object(value)
  const keys = Object.keys(data)
  if (keys.length !== fields.length || fields.some((field) => !Object.hasOwn(data, field))) {
    throw invalidResponse()
  }
  return data
}

function maintenanceString(value: unknown, minimum: number, maximum: number, multiline = true): string {
  const decoded = string(value)
  const length = unicodeCodePointLength(decoded)
  if (
    length < minimum
    || length > maximum
    || decoded !== decoded.trim()
    || decoded.includes('\0')
    || (!multiline && /[\r\n]/.test(decoded))
  ) throw invalidResponse()
  return decoded
}

function maintenanceProviderText(value: unknown, maximum: number): string {
  const decoded = maintenanceString(value, 1, maximum)
  if (
    /[/\\~?#]/.test(decoded)
    || decoded.includes('..')
    || maintenanceWindowsDrive.test(decoded)
    || maintenanceWindowsDevice.test(decoded)
    || maintenanceEncodedPath.test(decoded)
    || maintenanceExternalUri.test(decoded)
    || maintenanceDottedToken.test(decoded)
    || maintenanceCredential.test(decoded)
  ) throw invalidResponse()
  return decoded
}

function maintenanceId(value: unknown): string {
  const decoded = string(value)
  if (!maintenanceUuid.test(decoded) || /^0{8}-0{4}-0{4}-0{4}-0{12}$/.test(decoded)) throw invalidResponse()
  return decoded
}

function maintenanceDate(value: unknown): string {
  const decoded = string(value)
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(decoded) || !Number.isFinite(Date.parse(decoded))) {
    throw invalidResponse()
  }
  return decoded
}

function maintenanceEnum<T extends string>(value: unknown, allowed: ReadonlySet<T>): T {
  const decoded = string(value) as T
  if (!allowed.has(decoded)) throw invalidResponse()
  return decoded
}

function maintenanceArray(value: unknown, maximum: number): unknown[] {
  if (!Array.isArray(value) || value.length > maximum) throw invalidResponse()
  return value
}

function unique(values: readonly string[]): boolean {
  return new Set(values).size === values.length
}

function decodeMaintenanceAffectedItem(value: unknown): ProjectMaintenanceAffectedItem {
  const data = exactObject(value, [
    'id', 'position', 'type', 'stable_reference', 'impact_level', 'reason', 'document_id', 'chapter_id',
  ])
  const type = maintenanceEnum(data.type, maintenanceScopeHints)
  const stableReference = string(data.stable_reference)
  const position = integer(data.position)
  const impactLevel = maintenanceEnum(data.impact_level, new Set(['low', 'medium', 'high'] as const))
  if (
    position < 0
    || position > 255
    || !maintenanceStableReference.test(stableReference)
    || !stableReference.startsWith(`${type}/`)
  ) throw invalidResponse()
  return {
    id: maintenanceId(data.id),
    position,
    type,
    stable_reference: stableReference,
    impact_level: impactLevel,
    reason: maintenanceProviderText(data.reason, 1000),
    document_id: data.document_id === null ? null : maintenanceId(data.document_id),
    chapter_id: data.chapter_id === null ? null : maintenanceId(data.chapter_id),
  }
}

function decodeMaintenanceOperation(value: unknown): ProjectMaintenanceRevisionOperation {
  const data = exactObject(value, [
    'id', 'sequence', 'operation', 'document_id', 'expected_version_id', 'affected_item_ids', 'instruction',
  ])
  const affectedItemIds = maintenanceArray(data.affected_item_ids, 64).map(maintenanceId)
  const sequence = integer(data.sequence)
  if (sequence < 1 || sequence > 128 || affectedItemIds.length === 0 || !unique(affectedItemIds)) throw invalidResponse()
  return {
    id: maintenanceId(data.id),
    sequence,
    operation: maintenanceEnum(data.operation, new Set(['revise', 'retain'] as const)),
    document_id: maintenanceId(data.document_id),
    expected_version_id: maintenanceId(data.expected_version_id),
    affected_item_ids: affectedItemIds,
    instruction: maintenanceProviderText(data.instruction, 1000),
  }
}

function decodeMaintenancePlan(value: unknown): ProjectMaintenanceRevisionPlan {
  const data = exactObject(value, ['id', 'document_id', 'version_id', 'review_outcome', 'summary', 'operations'])
  const operations = maintenanceArray(data.operations, 128).map(decodeMaintenanceOperation)
  if (
    operations.length === 0
    || !unique(operations.map((operation) => operation.id))
    || !unique(operations.map((operation) => operation.document_id))
    || operations.some((operation, index) => operation.sequence !== index + 1)
  ) throw invalidResponse()
  return {
    id: maintenanceId(data.id),
    document_id: maintenanceId(data.document_id),
    version_id: maintenanceId(data.version_id),
    review_outcome: maintenanceEnum(data.review_outcome, new Set(['passed', 'warning', 'blocking'] as const)),
    summary: maintenanceProviderText(data.summary, 2000),
    operations,
  }
}

function decodeMaintenanceConsistencyDocument(value: unknown): ProjectMaintenanceConsistencyDocument {
  const data = exactObject(value, ['document_id', 'version_id'])
  return { document_id: maintenanceId(data.document_id), version_id: maintenanceId(data.version_id) }
}

function decodeMaintenanceFinding(value: unknown): ProjectMaintenanceConsistencyFinding {
  const data = exactObject(value, [
    'id', 'sequence', 'code', 'severity', 'blocking', 'affected_documents', 'suggested_corrective_action',
  ])
  const sequence = integer(data.sequence)
  const code = string(data.code)
  const severity = maintenanceEnum(data.severity, new Set(['warning', 'blocking'] as const))
  const blocking = boolean(data.blocking)
  const affectedDocuments = maintenanceArray(data.affected_documents, 128).map(decodeMaintenanceConsistencyDocument)
  const identities = affectedDocuments.map((item) => `${item.document_id}:${item.version_id}`)
  if (
    sequence < 1
    || sequence > 128
    || !maintenanceSafeCode.test(code)
    || affectedDocuments.length === 0
    || !unique(identities)
    || blocking !== (severity === 'blocking')
  ) throw invalidResponse()
  return {
    id: maintenanceId(data.id),
    sequence,
    code,
    severity,
    blocking,
    affected_documents: affectedDocuments,
    suggested_corrective_action: maintenanceProviderText(data.suggested_corrective_action, 1000),
  }
}

function decodeMaintenanceReview(value: unknown): ProjectMaintenanceConsistencyReview {
  const data = exactObject(value, ['id', 'outcome', 'findings'])
  const outcome = maintenanceEnum(data.outcome, new Set(['clean', 'warning', 'blocking'] as const))
  const findings = maintenanceArray(data.findings, 128).map(decodeMaintenanceFinding)
  if (
    !unique(findings.map((finding) => finding.id))
    || findings.some((finding, index) => finding.sequence !== index + 1)
    || (outcome === 'clean' && findings.length !== 0)
    || (outcome === 'warning' && (findings.length === 0 || findings.some((finding) => finding.blocking)))
    || (outcome === 'blocking' && !findings.some((finding) => finding.blocking))
  ) throw invalidResponse()
  return { id: maintenanceId(data.id), outcome, findings }
}

function decodeMaintenancePendingAction(value: unknown): ProjectMaintenancePendingAction {
  const data = exactObject(value, [
    'id', 'type', 'status', 'confirmation_kind', 'review_outcome', 'allowed_decisions',
  ])
  const allowedDecisions = maintenanceArray(data.allowed_decisions, 4).map(
    (decision) => maintenanceEnum(decision, maintenanceDecisions),
  )
  if (allowedDecisions.length === 0 || !unique(allowedDecisions)) throw invalidResponse()
  const type = maintenanceEnum(
    data.type,
    new Set(['project_maintenance_revision_confirmation', 'project_maintenance_consistency_warning'] as const),
  )
  const confirmationKind = maintenanceEnum(
    data.confirmation_kind,
    new Set(['revision_confirmation', 'consistency_warning'] as const),
  )
  if (
    (type === 'project_maintenance_revision_confirmation') !== (confirmationKind === 'revision_confirmation')
    || data.status !== 'pending'
  ) throw invalidResponse()
  return {
    id: maintenanceId(data.id),
    type,
    status: 'pending',
    confirmation_kind: confirmationKind,
    review_outcome: maintenanceEnum(data.review_outcome, new Set(['passed', 'warning', 'blocking'] as const)),
    allowed_decisions: allowedDecisions,
  }
}

function sameValues(actual: readonly string[], expected: readonly string[]): boolean {
  return actual.length === expected.length && actual.every((value, index) => value === expected[index])
}

function validateMaintenanceGate(run: ProjectMaintenanceRun): void {
  const pending = run.pending_action
  if (run.status !== 'USER_CONFIRMATION') {
    if (run.awaiting_user || pending !== null) throw invalidResponse()
    return
  }
  if (!run.awaiting_user || pending === null || run.revision_plan === null) throw invalidResponse()
  let expected: ProjectMaintenanceDecision[]
  if (pending.confirmation_kind === 'consistency_warning') {
    if (pending.review_outcome !== 'warning' || run.consistency_review?.outcome !== 'warning') throw invalidResponse()
    expected = ['accept_warning', 'revise']
  } else if (pending.review_outcome === 'blocking') {
    if (pending.review_outcome !== run.revision_plan.review_outcome) throw invalidResponse()
    expected = run.applied_document_version_ids.length === 0 ? ['revise', 'cancel'] : ['revise']
  } else {
    if (pending.review_outcome !== run.revision_plan.review_outcome) throw invalidResponse()
    expected = run.applied_document_version_ids.length === 0 ? ['approve', 'revise', 'cancel'] : ['approve', 'revise']
  }
  if (!sameValues(pending.allowed_decisions, expected)) throw invalidResponse()
}

function validateMaintenanceLifecycle(run: ProjectMaintenanceRun): void {
  const hasApplied = run.applied_document_version_ids.length > 0
  const review = run.consistency_review
  const planOutcome = run.revision_plan?.review_outcome
  if (run.status === 'CHANGE_REQUESTED') {
    if (run.affected_items.length > 0 || run.revision_plan !== null || hasApplied || review !== null) throw invalidResponse()
    return
  }
  if (run.status === 'LORE_IMPACT_ANALYSIS' || run.status === 'CHIEF_EDITOR_IMPACT_ANALYSIS') {
    if (run.revision_plan !== null || hasApplied || review !== null) throw invalidResponse()
    return
  }
  if (run.status === 'REVISION_PLAN') {
    if (hasApplied !== (review !== null) || review?.outcome === 'clean') throw invalidResponse()
    return
  }
  if (run.status === 'USER_CONFIRMATION') {
    const confirmationKind = run.pending_action?.confirmation_kind
    if (confirmationKind === 'revision_confirmation') {
      if (hasApplied !== (review !== null) || review?.outcome === 'clean') throw invalidResponse()
    }
    return
  }
  if (run.status === 'APPLY_CHANGE') {
    if (review !== null || planOutcome === 'blocking') throw invalidResponse()
    return
  }
  if (run.status === 'CONSISTENCY_REVIEW') {
    if (!hasApplied || planOutcome === 'blocking') throw invalidResponse()
    return
  }
  if (run.status === 'PROJECT_UPDATED') {
    if (!hasApplied || review === null || review.outcome === 'blocking' || planOutcome === 'blocking') {
      throw invalidResponse()
    }
    return
  }
  if (hasApplied || review !== null) throw invalidResponse()
}

function decodeProjectMaintenanceRun(value: unknown): ProjectMaintenanceRun {
  const data = exactObject(value, [
    'id', 'maintenance_change_id', 'type', 'status', 'current_node', 'next_node', 'awaiting_user', 'title',
    'change_request', 'created_at', 'updated_at', 'completed_at', 'affected_items', 'revision_plan',
    'consistency_review', 'applied_document_version_ids', 'pending_action',
  ])
  const status = maintenanceEnum(data.status, maintenanceStatuses)
  const affectedItems = maintenanceArray(data.affected_items, 256).map(decodeMaintenanceAffectedItem)
  const appliedIds = maintenanceArray(data.applied_document_version_ids, 128).map(maintenanceId)
  const run: ProjectMaintenanceRun = {
    id: maintenanceId(data.id),
    maintenance_change_id: maintenanceId(data.maintenance_change_id),
    type: data.type === 'project_maintenance' ? data.type : (() => { throw invalidResponse() })(),
    status,
    current_node: string(data.current_node),
    next_node: data.next_node === null ? null : (() => { throw invalidResponse() })(),
    awaiting_user: boolean(data.awaiting_user),
    title: maintenanceString(data.title, 1, 512, false),
    change_request: maintenanceString(data.change_request, 1, 4000),
    created_at: maintenanceDate(data.created_at),
    updated_at: maintenanceDate(data.updated_at),
    completed_at: data.completed_at === null ? null : maintenanceDate(data.completed_at),
    affected_items: affectedItems,
    revision_plan: data.revision_plan === null ? null : decodeMaintenancePlan(data.revision_plan),
    consistency_review: data.consistency_review === null ? null : decodeMaintenanceReview(data.consistency_review),
    applied_document_version_ids: appliedIds,
    pending_action: data.pending_action === null ? null : decodeMaintenancePendingAction(data.pending_action),
  }
  const terminal = status === 'PROJECT_UPDATED' || status === 'CANCELLED'
  const createdTime = Date.parse(run.created_at)
  const updatedTime = Date.parse(run.updated_at)
  const planRequired = new Set<ProjectMaintenanceStatus>([
    'USER_CONFIRMATION', 'APPLY_CHANGE', 'CONSISTENCY_REVIEW', 'PROJECT_UPDATED', 'CANCELLED',
  ]).has(status)
  const reviewedVersionIds = run.consistency_review?.findings.flatMap(
    (finding) => finding.affected_documents.map((document) => document.version_id),
  ) ?? []
  const operationAffectedIds = run.revision_plan?.operations.flatMap(
    (operation) => operation.affected_item_ids,
  ) ?? []
  const operationAffectedSet = new Set(operationAffectedIds)
  const affectedById = new Map(affectedItems.map((item) => [item.id, item]))
  if (
    run.current_node !== maintenanceNodes[status]
    || terminal !== (run.completed_at !== null)
    || updatedTime < createdTime
    || (planRequired && run.revision_plan === null)
    || (['CHANGE_REQUESTED', 'LORE_IMPACT_ANALYSIS', 'CHIEF_EDITOR_IMPACT_ANALYSIS'].includes(status) && run.revision_plan !== null)
    || (run.consistency_review !== null && appliedIds.length === 0)
    || reviewedVersionIds.some((versionId) => !appliedIds.includes(versionId))
    || !unique(affectedItems.map((item) => item.id))
    || !unique(affectedItems.map((item) => item.stable_reference))
    || affectedItems.some((item, index) => item.position !== index)
    || !unique(appliedIds)
    || (run.revision_plan !== null && (
      operationAffectedSet.size !== affectedItems.length
      || operationAffectedIds.some((affectedId) => !affectedById.has(affectedId))
      || run.revision_plan.operations.some((operation) => operation.affected_item_ids.some(
        (affectedId) => {
          const documentId = affectedById.get(affectedId)?.document_id
          return documentId !== null && documentId !== operation.document_id
        },
      ))
    ))
    || (status === 'PROJECT_UPDATED' && (run.revision_plan === null || run.consistency_review === null || appliedIds.length === 0))
    || (status === 'CANCELLED' && (run.consistency_review !== null || appliedIds.length !== 0))
  ) throw invalidResponse()
  validateMaintenanceLifecycle(run)
  validateMaintenanceGate(run)
  return run
}

function maintenanceErrorFromEnvelope(status: number, value: unknown): ApiError {
  const envelopeCode = isRecord(value) && isRecord(value.error) && isSafeErrorCode(value.error.code)
    ? value.error.code
    : 'request_failed'
  if (status === 404) return new ApiError(status, 'not_found', 'The project maintenance run was not found.')
  if (status === 409) {
    const code = envelopeCode === 'workflow_state_error' ? envelopeCode : 'conflict'
    return new ApiError(status, code, 'The project maintenance state changed. Refresh and try again.')
  }
  if (status === 422) {
    const allowed = new Set(['validation_error', 'agent_output_invalid', 'provider_invalid_output', 'maintenance_change_invalid'])
    const code = allowed.has(envelopeCode) ? envelopeCode : 'validation_error'
    return new ApiError(status, code, 'The project maintenance request could not be processed.')
  }
  return new ApiError(status, 'request_failed', genericErrorMessage)
}

function maintenanceRequestOptions(signal?: AbortSignal): RequestOptions {
  return { ...(signal === undefined ? {} : { signal }), decodeError: maintenanceErrorFromEnvelope }
}

function invalidMaintenanceRequest(): ApiError {
  return new ApiError(0, 'invalid_request', 'The project maintenance request is invalid.')
}

function startMaintenanceBody(payload: StartProjectMaintenanceRequest): StartProjectMaintenanceRequest {
  const title = typeof payload.title === 'string' ? payload.title.trim() : ''
  const changeRequest = typeof payload.change_request === 'string' ? payload.change_request.trim() : ''
  const scopeHints = payload.scope_hints === undefined ? [] : payload.scope_hints
  if (
    unicodeCodePointLength(title) < 1
    || unicodeCodePointLength(title) > 512
    || /[\r\n]/.test(title)
    || unicodeCodePointLength(changeRequest) < 1
    || unicodeCodePointLength(changeRequest) > 4000
    || !Array.isArray(scopeHints)
    || scopeHints.length > 7
    || !scopeHints.every((hint) => maintenanceScopeHints.has(hint))
    || !unique(scopeHints)
  ) throw invalidMaintenanceRequest()
  return { title, change_request: changeRequest, scope_hints: [...scopeHints] }
}

function decodeExpectedMaintenanceRun(value: unknown, expectedRunId?: string): ProjectMaintenanceRun {
  const run = decodeProjectMaintenanceRun(value)
  if (expectedRunId !== undefined && run.id !== expectedRunId) throw invalidResponse()
  return run
}

export function startProjectMaintenance(
  projectId: string,
  payload: StartProjectMaintenanceRequest,
  signal?: AbortSignal,
): Promise<ProjectMaintenanceRun> {
  const body = startMaintenanceBody(payload)
  return request(
    'POST',
    () => apiPath('projects', projectId, 'maintenance', 'start'),
    decodeProjectMaintenanceRun,
    body,
    maintenanceRequestOptions(signal),
  )
}

export function getProjectMaintenanceRun(
  projectId: string,
  workflowRunId: string,
  signal?: AbortSignal,
): Promise<ProjectMaintenanceRun> {
  return request(
    'GET',
    () => apiPath('projects', projectId, 'maintenance', workflowRunId),
    (value) => decodeExpectedMaintenanceRun(value, workflowRunId),
    undefined,
    maintenanceRequestOptions(signal),
  )
}

export function listProjectMaintenanceRuns(
  projectId: string,
  options: ProjectMaintenanceListOptions = {},
  signal?: AbortSignal,
): Promise<ProjectMaintenanceHistorySummary[]> {
  const offset = options.offset ?? 0
  const limit = options.limit ?? 20
  if (!Number.isInteger(offset) || offset < 0 || offset > 10_000 || !Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw invalidMaintenanceRequest()
  }
  return request(
    'GET',
    () => `${apiPath('projects', projectId, 'maintenance')}?offset=${offset}&limit=${limit}`,
    (value) => maintenanceArray(value, limit).map((item) => {
      const run = decodeProjectMaintenanceRun(item)
      return {
        id: run.id,
        maintenance_change_id: run.maintenance_change_id,
        status: run.status,
        title: run.title,
        awaiting_user: run.awaiting_user,
        created_at: run.created_at,
        updated_at: run.updated_at,
        completed_at: run.completed_at,
      }
    }),
    undefined,
    maintenanceRequestOptions(signal),
  )
}

export async function resolveProjectMaintenanceAction(
  projectId: string,
  run: ProjectMaintenanceRun,
  decision: ProjectMaintenanceDecision,
  signal?: AbortSignal,
): Promise<ProjectMaintenanceRun> {
  const decodedRun = decodeProjectMaintenanceRun(run)
  const pending = decodedRun.status === 'USER_CONFIRMATION' && decodedRun.awaiting_user
    ? decodedRun.pending_action
    : null
  if (pending === null || !pending.allowed_decisions.includes(decision)) throw invalidMaintenanceRequest()
  return request(
    'POST',
    () => apiPath('projects', projectId, 'maintenance', decodedRun.id, 'actions', pending.id, 'resolve'),
    (value) => decodeExpectedMaintenanceRun(value, decodedRun.id),
    { decision },
    maintenanceRequestOptions(signal),
  )
}
