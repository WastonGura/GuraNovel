import { ApiError, readDocumentContent, readStudioFeedback, submitStudioFeedback, type FeedbackSubmitRequest } from './api/client'
import { getChapterProductionRun, getChapterProductionReviewReport, listChapterProductionRuns, reconcileChapterProduction,
  resolveChapterProductionAction, triggerChapterReview, requestReviewRevision, finalizeChapterProduction, requestStudioFeedbackRevision,
  type ReviewRevisionSelection, type ChapterProductionState } from './api/chapterProductionV2Client'
import { readerStageKey, reviewers, type StudioChapter } from './studioPreview'

export const reviewStages = ['EDITOR_REVIEW', 'CHIEF_FINAL_REVIEW', 'LORE_FINAL_REVIEW']
const invalid = () => new ApiError(409, 'production_changed', '章节状态或正文版本已变化，请重新加载核对。')
export const revisionRecoveryKey = (projectId: string, chapterId: string) => `guranovel:review-revision:${projectId}:${chapterId}`
export const feedbackRevisionKey = (projectId: string, chapterId: string) => `guranovel:feedback-revision:${projectId}:${chapterId}`
export type StudioFeedbackRequest = { runId: string; documentId: string; actionId: string; submission: FeedbackSubmitRequest }

export async function prepareStudioFeedbackRevision(projectId: string, chapter: StudioChapter, signal: AbortSignal): Promise<StudioFeedbackRequest> {
  const [state, feedback, saved] = await Promise.all([latestState(projectId, chapter.id, signal),
    readStudioFeedback(projectId, chapter.id, 'draft'), readDocumentContent(chapter.documentId!)])
  signal.throwIfAborted()
  if (!state || state.status !== 'AUTHOR_REVISION' || !state.awaiting_user || state.action_kind !== 'author_revision'
    || !state.action_request_id || state.chapter_workflow_run_id !== chapter.productionState?.chapter_workflow_run_id
    || state.document_id !== chapter.documentId || feedback.read_only || feedback.document_id !== chapter.documentId
    || feedback.source_version_id !== chapter.versionId || saved.version_id !== chapter.versionId
    || saved.document_id !== chapter.documentId || saved.content !== chapter.draft || feedback.requirements !== chapter.requirements) throw invalid()
  const comments = feedback.comments.filter(comment => comment.submitted)
  if (comments.some(comment => comment.orphaned && comment.text.trim()))
    throw new ApiError(422, 'orphaned_feedback', '评论已失去原文位置，请先移除该评论，再发送修改要求。')
  if (!feedback.requirements.trim() && !comments.some(comment => comment.text.trim()))
    throw new ApiError(422, 'empty_feedback', '请先填写写作要求或评论内容。')
  return { runId: state.chapter_workflow_run_id, documentId: chapter.documentId!, actionId: state.action_request_id,
    submission: { request_id: crypto.randomUUID(), expected_current_version_id: chapter.versionId!,
      expected_revision: feedback.revision, comment_ids: comments.map(comment => comment.id) } }
}

export async function reviseStudioFeedback(projectId: string, chapter: StudioChapter, pending: StudioFeedbackRequest,
  signal: AbortSignal): Promise<Partial<StudioChapter>> {
  const snapshot = await submitStudioFeedback(projectId, chapter.id, 'draft', pending.submission)
  signal.throwIfAborted()
  if (snapshot.id !== pending.submission.request_id || snapshot.document_id !== pending.documentId
    || snapshot.source_version_id !== pending.submission.expected_current_version_id) throw invalid()
  const result = await requestStudioFeedbackRevision(projectId, chapter.id, pending.runId, {
    submission_id: snapshot.id, document_id: pending.documentId, version_id: snapshot.source_version_id,
    action_request_id: pending.actionId }, signal)
  const [saved, state] = await Promise.all([readDocumentContent(pending.documentId),
    getChapterProductionRun(projectId, chapter.id, pending.runId, signal)])
  signal.throwIfAborted()
  if (result.workflow_run_id !== pending.runId || result.draft_document_id !== pending.documentId
    || result.draft_version_id === snapshot.source_version_id || saved.document_id !== pending.documentId
    || saved.version_id !== result.draft_version_id || state.document_version_id !== saved.version_id
    || state.chapter_id !== chapter.id || state.document_id !== pending.documentId || state.chapter_workflow_run_id !== pending.runId
    || state.status !== 'AUTHOR_REVISION' || !state.awaiting_user || state.action_kind !== 'author_revision') throw invalid()
  return { draft: saved.content, versionId: saved.version_id, stage: 'Draft', review: 'idle', completed: 0,
    issues: [], selected: [], reviewReports: [], productionState: state, productionStatus: state.status,
    productionError: undefined, feedbackRequest: undefined }
}

async function reviewPatch(projectId: string, chapterId: string, documentId: string, versionId: string,
  state: ChapterProductionState, signal?: AbortSignal): Promise<Partial<StudioChapter>> {
  if (state.chapter_id !== chapterId) throw invalid()
  const production = { productionStatus: state.status, productionState: state, productionError: undefined }
  if (state.status === 'AUTHOR_REVISION' || state.status === 'CANCELLED') return production
  if (state.document_id !== documentId || state.document_version_id !== versionId) throw invalid()
  const slots = [[state.editor_report_id, 'editor_agent'], [state.chief_editor_report_id, 'chief_editor_agent'], [state.lore_report_id, 'lore_agent']] as const
  const reports = await Promise.all(slots.filter(([id]) => id).map(async ([id, role]) => {
    const report = await getChapterProductionReviewReport(projectId, chapterId, state.chapter_workflow_run_id, id!, { documentId, versionId }, signal)
    if (report.reviewer_role !== role) throw invalid()
    return report
  }))
  const names = { editor_agent: reviewers[0], chief_editor_agent: reviewers[1], lore_agent: reviewers[2] }
  const levels = { blocking: 'Block', warning: 'Warning', note: 'Suggestion' } as const
  const issues = reports.flatMap(report => report.findings.map(finding => ({
    id: `${report.id}:${finding.sequence}`, reviewer: names[report.reviewer_role], level: levels[finding.severity],
    title: finding.rationale, detail: finding.suggested_action, quote: '',
  })))
  return { ...production, stage: 'Review', reviewReports: reports, chiefEditorRequired: state.chief_editor_required,
    review: reports.length || state.awaiting_user || state.status === 'REVISION_READY' ? 'done' : 'running',
    completed: reports.length, issues, selected: issues.filter(issue => issue.level === 'Block').map(issue => issue.id) }
}

async function latestState(projectId: string, chapterId: string, signal?: AbortSignal) {
  const [latest] = await listChapterProductionRuns(projectId, chapterId, { limit: 1 }, signal)
  if (!latest) return null
  const state = await getChapterProductionRun(projectId, chapterId, latest.workflow_run_id, signal)
  if (state.chapter_id !== chapterId || state.chapter_workflow_run_id !== latest.workflow_run_id) throw invalid()
  return state
}

export async function studioFinalIsComplete(projectId: string, chapterId: string): Promise<boolean> {
  const state = await latestState(projectId, chapterId)
  if (!state || state.status === 'COMPLETED') return true
  if (state.status === 'ARCHIVE_UPDATE') return false
  throw invalid()
}

export async function loadStudioReview(projectId: string, chapterId: string, documentId: string, versionId: string): Promise<Partial<StudioChapter>> {
  const state = await latestState(projectId, chapterId)
  if (!state) return {}
  const patch = await reviewPatch(projectId, chapterId, documentId, versionId, state)
  const feedbackRequest: StudioFeedbackRequest | null = JSON.parse(sessionStorage.getItem(feedbackRevisionKey(projectId, chapterId)) || 'null')
  if (feedbackRequest) {
    if (feedbackRequest.runId !== state.chapter_workflow_run_id || feedbackRequest.documentId !== documentId) throw invalid()
    return { ...patch, stage: 'Draft', feedbackRequest, productionError: '上次反馈修改尚待核对，重试会恢复原请求。' }
  }
  const pending = JSON.parse(sessionStorage.getItem(revisionRecoveryKey(projectId, chapterId)) || 'null')
  if (pending) {
    if (pending.runId !== state.chapter_workflow_run_id || pending.selection?.document_id !== documentId) throw invalid()
    return { ...patch, revisionRequest: pending.selection,
      selected: pending.selection.version_id === versionId
        ? pending.selection.selected_findings.map((item: { report_id: string; sequence: number }) => `${item.report_id}:${item.sequence}`) : patch.selected,
      productionError: '上次选定问题的修改请求尚待核对，重试将恢复原请求的服务器结果。' }
  }
  if (state.status === 'ARCHIVE_UPDATE') return { ...patch, stage: 'Final', productionError: '上次本地定稿尚待核对，请重试完成原操作。' }
  return state.status === 'REVISION_READY' && sessionStorage.getItem(readerStageKey(chapterId, versionId)) === 'open'
    ? { ...patch, stage: 'Reader' } : patch
}

export function studioRevisionSelection(chapter: StudioChapter, ids: string[]): ReviewRevisionSelection {
  const state = chapter.productionState
  if (!state || !chapter.documentId || !chapter.versionId || state.document_version_id !== chapter.versionId
    || !(state.awaiting_user && ['review_revision', 'review_warning'].includes(state.action_kind || '')
      || state.status === 'REVISION_READY')) throw invalid()
  const reports = chapter.reviewReports || []
  const findings = reports.flatMap(report => report.findings.map(finding => ({ report_id: report.id, sequence: finding.sequence,
    id: `${report.id}:${finding.sequence}`, required: finding.required })))
  if (!ids.length || new Set(ids).size !== ids.length || ids.some(id => !findings.some(item => item.id === id))
    || findings.some(item => item.required && !ids.includes(item.id))) throw invalid()
  return { request_id: crypto.randomUUID(), document_id: chapter.documentId, version_id: chapter.versionId,
    action_request_id: state.action_request_id, report_ids: reports.map(report => report.id),
    selected_findings: findings.filter(item => ids.includes(item.id)).map(({ report_id, sequence }) => ({ report_id, sequence })) }
}

export async function reviseStudioReview(projectId: string, chapter: StudioChapter, selection: ReviewRevisionSelection,
  onProgress: (patch: Partial<StudioChapter>) => void, signal: AbortSignal): Promise<void> {
  const state = await latestState(projectId, chapter.id, signal)
  if (!state || state.chapter_workflow_run_id !== chapter.productionState?.chapter_workflow_run_id
    || state.document_id !== selection.document_id) throw invalid()
  const result = await requestReviewRevision(projectId, chapter.id, state.chapter_workflow_run_id, selection, signal)
  signal.throwIfAborted()
  const saved = await readDocumentContent(selection.document_id)
  signal.throwIfAborted()
  if (result.workflow_run_id !== state.chapter_workflow_run_id || result.draft_document_id !== selection.document_id
    || saved.document_id !== selection.document_id || saved.version_id !== result.draft_version_id) throw invalid()
  const next = { ...chapter, draft: saved.content, versionId: saved.version_id }
  const current = await getChapterProductionRun(projectId, chapter.id, state.chapter_workflow_run_id, signal)
  if (current.chapter_workflow_run_id !== state.chapter_workflow_run_id) throw invalid()
  const patch = await reviewPatch(projectId, chapter.id, selection.document_id, saved.version_id, current, signal)
  signal.throwIfAborted()
  onProgress({ ...patch, draft: saved.content, versionId: saved.version_id })
  await advanceStudioReview(projectId, next, onProgress, signal)
}

export async function finalizeStudioChapter(projectId: string, chapter: StudioChapter, signal: AbortSignal): Promise<Partial<StudioChapter>> {
  const state = await latestState(projectId, chapter.id, signal)
  if (!state || state.chapter_workflow_run_id !== chapter.productionState?.chapter_workflow_run_id
    || state.document_id !== chapter.documentId || state.document_version_id !== chapter.versionId
    || state.awaiting_user || !['REVISION_READY', 'ARCHIVE_UPDATE', 'COMPLETED'].includes(state.status)) throw invalid()
  const saved = await readDocumentContent(chapter.documentId!)
  signal.throwIfAborted()
  if (saved.document_id !== chapter.documentId || saved.version_id !== chapter.versionId || saved.content !== chapter.draft) throw invalid()
  const result = await finalizeChapterProduction(projectId, chapter.id, state.chapter_workflow_run_id, signal, chapter.versionId)
  const [final, current] = await Promise.all([readDocumentContent(result.final_document_id),
    getChapterProductionRun(projectId, chapter.id, state.chapter_workflow_run_id, signal)])
  signal.throwIfAborted()
  if (result.workflow_run_id !== state.chapter_workflow_run_id || final.document_id !== result.final_document_id
    || final.version_id !== result.final_version_id || final.content !== saved.content
    || current.chapter_workflow_run_id !== state.chapter_workflow_run_id || current.status !== 'COMPLETED') throw invalid()
  return { stage: 'Final', published: true, documentId: final.document_id, versionId: final.version_id, draft: final.content,
    productionStatus: current.status, productionState: current, productionError: undefined, draftComments: [], requirements: '' }
}

export async function advanceStudioReview(projectId: string, chapter: StudioChapter,
  onProgress: (patch: Partial<StudioChapter>) => void, signal: AbortSignal, warning?: ChapterProductionState): Promise<void> {
  const { id: chapterId, documentId, versionId, draft } = chapter
  if (!documentId || !versionId || !draft.trim()) throw invalid()
  let state = await latestState(projectId, chapterId, signal)
  if (!state) throw new ApiError(409, 'workflow_missing', '此章节没有可继续的创作流程，请先在现有工作台核对。')
  const runId = state.chapter_workflow_run_id
  const readState = async () => {
    const next = await getChapterProductionRun(projectId, chapterId, runId, signal)
    if (next.chapter_id !== chapterId || next.chapter_workflow_run_id !== runId) throw invalid()
    return next
  }
  const reportProgress = async () => {
    const patch = await reviewPatch(projectId, chapterId, documentId, versionId, state!, signal)
    signal.throwIfAborted()
    onProgress(patch)
  }
  const saved = await readDocumentContent(documentId)
  signal.throwIfAborted()
  if (saved.document_id !== documentId || saved.version_id !== versionId || saved.content !== draft || state.document_id !== documentId) throw invalid()
  if (warning) {
    if (!warning.awaiting_user || warning.action_kind !== 'review_warning' || !warning.action_request_id
      || warning.chapter_id !== chapterId || warning.chapter_workflow_run_id !== runId
      || warning.document_id !== documentId || warning.document_version_id !== versionId
      || state.document_version_id !== versionId || state.status === 'AUTHOR_REVISION') throw invalid()
    // Consent belongs to the displayed action, never to a newer warning discovered during retry.
    await reportProgress()
    if (state.awaiting_user) {
      if (state.action_request_id !== warning.action_request_id || state.action_kind !== 'review_warning') return
      await resolveChapterProductionAction(projectId, chapterId, runId, warning.action_request_id,
        { decision: 'accept_warning' }, signal)
      state = await readState()
    }
  }
  if (state.status === 'AUTHOR_REVISION') {
    if (!state.awaiting_user || state.action_kind !== 'author_revision' || !state.action_request_id) throw invalid()
    await resolveChapterProductionAction(projectId, chapterId, runId, state.action_request_id,
      { decision: 'accept', expected_current_version_id: versionId }, signal)
    state = await readState()
  } else if (state.status === 'FAILED' && state.failed_from_status && reviewStages.includes(state.failed_from_status)) {
    state = await reconcileChapterProduction(projectId, chapterId, runId, signal)
    if (state.chapter_workflow_run_id !== runId) throw invalid()
  }
  await reportProgress()
  // At most Editor, optional Chief, and Lore for one immutable version; never auto-resolve a user gate.
  for (let step = 0; step < 3 && !state.awaiting_user && reviewStages.includes(state.status); step++) {
    signal.throwIfAborted()
    const previous = state.status
    await triggerChapterReview(projectId, chapterId, runId, signal)
    state = await readState()
    await reportProgress()
    if (state.status === previous && !state.awaiting_user) throw invalid()
  }
  if (!state.awaiting_user && state.status !== 'REVISION_READY') throw invalid()
}
