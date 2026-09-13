import { beforeEach, expect, it, vi } from 'vitest'
import { readDocumentContent } from './api/client'
import { getChapterProductionRun, getChapterProductionReviewReport, listChapterProductionRuns, reconcileChapterProduction,
  resolveChapterProductionAction, triggerChapterReview, requestReviewRevision, finalizeChapterProduction, type ChapterProductionState, type ChapterProductionReviewReport } from './api/chapterProductionV2Client'
import { newStudioChapter } from './studioPreview'
import { advanceStudioReview, finalizeStudioChapter, reviseStudioReview, studioRevisionSelection } from './studioProduction'

vi.mock('./api/client', async original => ({ ...await original<typeof import('./api/client')>(), readDocumentContent: vi.fn() }))
vi.mock('./api/chapterProductionV2Client', async original => ({ ...await original<typeof import('./api/chapterProductionV2Client')>(),
  getChapterProductionRun: vi.fn(), getChapterProductionReviewReport: vi.fn(), listChapterProductionRuns: vi.fn(),
  reconcileChapterProduction: vi.fn(), resolveChapterProductionAction: vi.fn(), triggerChapterReview: vi.fn(), requestReviewRevision: vi.fn(), finalizeChapterProduction: vi.fn() }))

const chapter = { ...newStudioChapter('chapter', 1, '第一章', '第一卷'), documentId: 'doc', versionId: 'saved', draft: '保存后的正文' }
const state = (status: ChapterProductionState['status'], patch: Partial<ChapterProductionState> = {}): ChapterProductionState => ({
  chapter_id: 'chapter', chapter_workflow_run_id: 'run', status, document_id: 'doc', document_version_id: 'saved',
  awaiting_user: status === 'AUTHOR_REVISION', action_request_id: status === 'AUTHOR_REVISION' ? 'action' : null,
  action_kind: status === 'AUTHOR_REVISION' ? 'author_revision' : null, ...patch,
} as ChapterProductionState)
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(listChapterProductionRuns).mockResolvedValue([{ workflow_run_id: 'run' }] as Awaited<ReturnType<typeof listChapterProductionRuns>>)
  vi.mocked(readDocumentContent).mockResolvedValue({ document_id: 'doc', version_id: 'saved', content: chapter.draft })
})

it('binds final consent to the reviewed version and recovers a completed response loss', async () => {
  vi.mocked(getChapterProductionRun).mockResolvedValue(state('REVISION_READY'))
  const final = { workflow_run_id: 'run', final_document_id: 'final-doc', final_version_id: 'final-version' }
  vi.mocked(finalizeChapterProduction).mockImplementationOnce(async () => {
    vi.mocked(getChapterProductionRun).mockResolvedValue(state('COMPLETED'))
    throw new Error('Successful finalization response lost')
  }).mockResolvedValue(final)
  const current = { ...chapter, productionState: state('REVISION_READY') }
  const controller = new AbortController()
  await expect(finalizeStudioChapter('project', current, controller.signal)).rejects.toThrow()
  vi.mocked(readDocumentContent).mockImplementation(async id => ({ document_id: id, version_id: id === 'final-doc' ? 'final-version' : 'saved', content: chapter.draft }))
  const result = await finalizeStudioChapter('project', current, controller.signal)
  expect(finalizeChapterProduction).toHaveBeenLastCalledWith('project', 'chapter', 'run', controller.signal, 'saved')
  expect(result).toMatchObject({ stage: 'Final', published: true, documentId: 'final-doc', versionId: 'final-version', draft: chapter.draft, draftComments: [] })
})

it('does not finalize a different source than the author saw', async () => {
  vi.mocked(getChapterProductionRun).mockResolvedValue(state('REVISION_READY', { document_version_id: 'newer' }))
  await expect(finalizeStudioChapter('project', { ...chapter, productionState: state('REVISION_READY') }, new AbortController().signal)).rejects.toThrow()
  expect(finalizeChapterProduction).not.toHaveBeenCalled()
})

it('confirms the saved version once and executes reviewers sequentially, publishing each server state', async () => {
  for (const status of ['AUTHOR_REVISION', 'EDITOR_REVIEW', 'CHIEF_FINAL_REVIEW', 'LORE_FINAL_REVIEW', 'REVISION_READY'] as const) {
    vi.mocked(getChapterProductionRun).mockResolvedValueOnce(state(status))
  }
  const progress = vi.fn(), controller = new AbortController()
  await advanceStudioReview('project', chapter, progress, controller.signal)
  expect(resolveChapterProductionAction).toHaveBeenCalledExactlyOnceWith('project', 'chapter', 'run', 'action',
    { decision: 'accept', expected_current_version_id: 'saved' }, controller.signal)
  expect(triggerChapterReview).toHaveBeenCalledTimes(3)
  expect(progress.mock.calls.map(([patch]) => patch.productionStatus)).toEqual(['EDITOR_REVIEW', 'CHIEF_FINAL_REVIEW', 'LORE_FINAL_REVIEW', 'REVISION_READY'])
})

it.each(['review_warning', 'review_revision'] as const)('stops at %s without approving it or invoking another reviewer', async kind => {
  vi.mocked(getChapterProductionRun).mockResolvedValueOnce(state('EDITOR_REVIEW'))
    .mockResolvedValueOnce(state('REVIEW_REVISION', { awaiting_user: true, action_kind: kind, action_request_id: 'review-action', editor_report_id: 'report' }))
  vi.mocked(getChapterProductionReviewReport).mockResolvedValue({ id: 'report', reviewer_role: 'editor_agent', findings: [] } as unknown as ChapterProductionReviewReport)
  const progress = vi.fn()
  await advanceStudioReview('project', chapter, progress, new AbortController().signal)
  expect(resolveChapterProductionAction).not.toHaveBeenCalled()
  expect(triggerChapterReview).toHaveBeenCalledTimes(1)
  expect(progress.mock.lastCall?.[0].productionState.action_kind).toBe(kind)
})

it('recovers a lost successful acceptance response by reading state, without accepting a second time', async () => {
  vi.mocked(getChapterProductionRun).mockResolvedValueOnce(state('AUTHOR_REVISION'))
    .mockResolvedValueOnce(state('EDITOR_REVIEW')).mockResolvedValueOnce(state('LORE_FINAL_REVIEW')).mockResolvedValueOnce(state('REVISION_READY'))
  vi.mocked(resolveChapterProductionAction).mockRejectedValueOnce(new Error('response lost'))
  await expect(advanceStudioReview('project', chapter, vi.fn(), new AbortController().signal)).rejects.toThrow('response lost')
  await advanceStudioReview('project', chapter, vi.fn(), new AbortController().signal)
  expect(resolveChapterProductionAction).toHaveBeenCalledTimes(1)
  expect(triggerChapterReview).toHaveBeenCalledTimes(2)
})

it('rejects newer server prose before acceptance and stops after cancellation before the next reviewer', async () => {
  vi.mocked(getChapterProductionRun).mockResolvedValue(state('AUTHOR_REVISION'))
  vi.mocked(readDocumentContent).mockResolvedValueOnce({ document_id: 'doc', version_id: 'newer', content: '另一处编辑' })
  await expect(advanceStudioReview('project', chapter, vi.fn(), new AbortController().signal)).rejects.toThrow()
  expect(resolveChapterProductionAction).not.toHaveBeenCalled()
  vi.mocked(getChapterProductionRun).mockResolvedValue(state('EDITOR_REVIEW'))
  const controller = new AbortController()
  await expect(advanceStudioReview('project', chapter, () => controller.abort(), controller.signal)).rejects.toThrow()
  expect(triggerChapterReview).not.toHaveBeenCalled()
})

it('reconciles a failed review before retrying the remaining stages', async () => {
  vi.mocked(getChapterProductionRun).mockResolvedValueOnce(state('FAILED', { failed_from_status: 'LORE_FINAL_REVIEW' }))
    .mockResolvedValueOnce(state('REVISION_READY'))
  vi.mocked(reconcileChapterProduction).mockResolvedValue(state('LORE_FINAL_REVIEW'))
  await advanceStudioReview('project', chapter, vi.fn(), new AbortController().signal)
  expect(reconcileChapterProduction).toHaveBeenCalledTimes(1)
  expect(triggerChapterReview).toHaveBeenCalledTimes(1)
  expect(resolveChapterProductionAction).not.toHaveBeenCalled()
})

const warning = state('REVIEW_REVISION', { awaiting_user: true, action_kind: 'review_warning', action_request_id: 'warning-one' })

it('submits exact selected findings without inventing an action or segment and adopts server prose before sequential review', async () => {
  const source = { ...chapter, productionState: state('REVISION_READY'), reviewReports: [{ id: 'report', findings: [
    { sequence: 1, required: false }, { sequence: 2, required: false },
  ] }] as ChapterProductionReviewReport[] }
  const selection = studioRevisionSelection(source, ['report:2'])
  expect(selection).toMatchObject({ action_request_id: null, report_ids: ['report'], selected_findings: [{ report_id: 'report', sequence: 2 }] })
  expect(selection).not.toHaveProperty('target_segment_ids')
  vi.mocked(getChapterProductionRun).mockResolvedValueOnce(state('REVISION_READY'))
    .mockResolvedValueOnce(state('EDITOR_REVIEW', { document_version_id: 'revised' }))
    .mockResolvedValueOnce(state('EDITOR_REVIEW', { document_version_id: 'revised' }))
    .mockResolvedValueOnce(state('REVISION_READY', { document_version_id: 'revised' }))
  vi.mocked(requestReviewRevision).mockResolvedValue({ workflow_run_id: 'run', draft_document_id: 'doc', draft_version_id: 'revised', action_request_id: null })
  vi.mocked(readDocumentContent).mockResolvedValue({ document_id: 'doc', version_id: 'revised', content: '服务器修订正文' })
  const progress = vi.fn(), signal = new AbortController().signal
  await reviseStudioReview('project', source, selection, progress, signal)
  expect(requestReviewRevision).toHaveBeenCalledExactlyOnceWith('project', 'chapter', 'run', selection, signal)
  expect(progress.mock.calls[0][0]).toMatchObject({ draft: '服务器修订正文', versionId: 'revised', issues: [], reviewReports: [] })
  expect(resolveChapterProductionAction).not.toHaveBeenCalled()
})

it('rejects omission of a mandatory finding and selections outside the displayed reports', () => {
  const source = { ...chapter, productionState: warning, reviewReports: [{ id: 'report', findings: [
    { sequence: 1, required: true }, { sequence: 2, required: false },
  ] }] as ChapterProductionReviewReport[] }
  expect(() => studioRevisionSelection(source, ['report:2'])).toThrow()
  expect(() => studioRevisionSelection(source, ['report:1', 'foreign:2'])).toThrow()
})
it('accepts only the displayed warning and stops at the next reviewer warning', async () => {
  vi.mocked(getChapterProductionRun).mockResolvedValueOnce(warning)
    .mockResolvedValueOnce(state('CHIEF_FINAL_REVIEW'))
    .mockResolvedValueOnce({ ...warning, action_request_id: 'warning-two' })
  const progress = vi.fn(), signal = new AbortController().signal
  await advanceStudioReview('project', chapter, progress, signal, warning)
  expect(resolveChapterProductionAction).toHaveBeenCalledExactlyOnceWith('project', 'chapter', 'run', 'warning-one', { decision: 'accept_warning' }, signal)
  expect(triggerChapterReview).toHaveBeenCalledTimes(1)
  expect(progress.mock.lastCall?.[0].productionState.action_request_id).toBe('warning-two')
})

it.each(['review_warning', 'review_revision'] as const)('refreshes a changed %s gate without applying stale consent', async kind => {
  vi.mocked(getChapterProductionRun).mockResolvedValue({ ...warning, action_kind: kind, action_request_id: 'another-action' })
  const progress = vi.fn()
  await advanceStudioReview('project', chapter, progress, new AbortController().signal, warning)
  expect(resolveChapterProductionAction).not.toHaveBeenCalled()
  expect(triggerChapterReview).not.toHaveBeenCalled()
  expect(progress.mock.lastCall?.[0].productionState.action_request_id).toBe('another-action')
})

it('recovers a lost warning decision response without repeating consent', async () => {
  vi.mocked(getChapterProductionRun).mockResolvedValueOnce(warning)
    .mockResolvedValueOnce(state('LORE_FINAL_REVIEW')).mockResolvedValueOnce(state('REVISION_READY'))
  vi.mocked(resolveChapterProductionAction).mockRejectedValueOnce(new Error('response lost'))
  await expect(advanceStudioReview('project', chapter, vi.fn(), new AbortController().signal, warning)).rejects.toThrow('response lost')
  await advanceStudioReview('project', chapter, vi.fn(), new AbortController().signal)
  expect(resolveChapterProductionAction).toHaveBeenCalledTimes(1)
  expect(triggerChapterReview).toHaveBeenCalledTimes(1)
})

it('rejects warning consent from another run or version before any mutation', async () => {
  vi.mocked(getChapterProductionRun).mockResolvedValue(warning)
  for (const stale of [{ ...warning, chapter_workflow_run_id: 'old-run' }, { ...warning, document_version_id: 'old-version' }]) {
    await expect(advanceStudioReview('project', chapter, vi.fn(), new AbortController().signal, stale)).rejects.toThrow()
  }
  expect(resolveChapterProductionAction).not.toHaveBeenCalled()
  expect(triggerChapterReview).not.toHaveBeenCalled()
})
