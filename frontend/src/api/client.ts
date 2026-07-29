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

async function request<T>(
  method: 'GET' | 'POST' | 'PUT',
  path: () => string,
  decode: (value: unknown) => T,
  body?: object,
): Promise<T> {
  let response: Response
  try {
    response = await fetch(path(), {
      method,
      credentials: 'same-origin',
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
  if (!response.ok) throw errorFromEnvelope(response.status, payload)
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

export interface ProjectCreationPendingAction {
  id: string
  type: string
  status: string
  allowed_decisions: string[]
  review_severity: string | null
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
    || !Array.isArray(data.concept_options)
    || data.concept_options.length > 5
  ) throw invalidResponse()
  return {
    id: string(data.id), type: string(data.type), status: string(data.status),
    allowed_decisions: data.allowed_decisions.map(string),
    review_severity: nullableString(data.review_severity),
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
