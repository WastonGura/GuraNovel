import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Link, MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import Studio from './Studio'
import { draftRecoveryCopies } from './useDraftAutosave'
import { writeStudioDraft } from './api/client'
import { ApiError, approveStudioOutline, createChapter, getProject, listChapters, readDocumentContent, readStudioFeedback, submitStudioFeedback, writeDocument, writeStudioFeedback, type Chapter, type Project, type StudioFeedback } from './api/client'
import { getChapterProductionRun, getChapterProductionReviewReport, listChapterProductionRuns, resolveChapterProductionAction, triggerChapterReview, resumeChapterProduction, startChapterProductionV2, requestStudioFeedbackRevision, type ChapterProductionState, type ChapterProductionReviewReport } from './api/chapterProductionV2Client'

vi.mock('./api/client', async original => ({ ...await original<typeof import('./api/client')>(), writeStudioDraft: vi.fn(), approveStudioOutline: vi.fn(), createChapter: vi.fn(), getProject: vi.fn(), listChapters: vi.fn(), readDocumentContent: vi.fn(), readStudioFeedback: vi.fn(), submitStudioFeedback: vi.fn(), writeDocument: vi.fn(), writeStudioFeedback: vi.fn() }))
vi.mock('./api/chapterProductionV2Client', async original => ({ ...await original<typeof import('./api/chapterProductionV2Client')>(), getChapterProductionRun: vi.fn(), getChapterProductionReviewReport: vi.fn(), listChapterProductionRuns: vi.fn(), resolveChapterProductionAction: vi.fn(), triggerChapterReview: vi.fn(), resumeChapterProduction: vi.fn(), startChapterProductionV2: vi.fn(), requestStudioFeedbackRevision: vi.fn() }))
const project = (id: string) => ({ id, title: `Novel ${id}`, setting_collection_id: `sc-${id}`, metadata: {} }) as Project
const chapter = (id: string, number = 1) => ({ id, title: `Chapter ${id}`, chapter_number: number, metadata: {}, current_draft_document_id: `doc-${id}` }) as Chapter
function Navigation() {
  const location = useLocation(), navigate = useNavigate()
  return <><output data-testid="url">{location.pathname}{location.search}</output><output data-testid="state">{JSON.stringify(location.state)}</output><button onClick={() => navigate(-1)}>History back</button><button onClick={() => navigate(1)}>History forward</button><Link to="/projects/q/studio/a">Other novel</Link></>
}
function open(path: string) { return render(<MemoryRouter initialEntries={[path]}><Navigation /><Routes><Route path="/projects/:projectId/studio" element={<Studio />} /><Route path="/projects/:projectId/studio/:chapterId" element={<Studio />} /><Route path="/setting-collections/:settingCollectionId" element={<div>Setting Collection</div>} /><Route path="/" element={<div>Dashboard</div>} /></Routes></MemoryRouter>) }
beforeEach(() => {
  vi.resetAllMocks(); localStorage.clear(); sessionStorage.clear(); draftRecoveryCopies.clear()
  vi.mocked(listChapterProductionRuns).mockResolvedValue([])
  vi.mocked(writeStudioDraft).mockImplementation((_project, chapterId, payload) => writeDocument(`doc-${chapterId}`, payload))
  vi.mocked(getProject).mockImplementation(async id => project(id))
  vi.mocked(listChapters).mockResolvedValue([chapter('a'), chapter('b', 2)])
  const docContents = new Map<string, string>()
  vi.mocked(readDocumentContent).mockImplementation(async id => ({ document_id: id, version_id: versions.get(id) || `${id}-v1`, content: docContents.get(id) || `Text ${id}` }))
  const versions = new Map<string, string>()
  const feedbackData = new Map<string, { requirements: string; comments: StudioFeedback['comments']; revision: number }>()
  vi.mocked(readStudioFeedback).mockImplementation(async (_project, chapter_id, region) => {
    const data = feedbackData.get(`${chapter_id}:${region}`) || { requirements: '', comments: [], revision: 0 }
    return { chapter_id, region, document_id: `doc-${chapter_id}`, source_version_id: versions.get(`doc-${chapter_id}`) || `doc-${chapter_id}-v1`, revision: data.revision, comments: data.comments, requirements: data.requirements, read_only: false }
  })
  vi.mocked(writeStudioFeedback).mockImplementation(async (_project, chapter_id, region, payload) => {
    const data = feedbackData.get(`${chapter_id}:${region}`) || { requirements: '', comments: [], revision: 0 }
    const next = { requirements: payload.requirements ?? data.requirements, comments: payload.comments ?? data.comments, revision: (payload.expected_revision ?? data.revision) + 1 }
    feedbackData.set(`${chapter_id}:${region}`, next)
    return { chapter_id, region, document_id: `doc-${chapter_id}`, source_version_id: payload.expected_current_version_id, revision: next.revision, comments: next.comments, requirements: next.requirements, read_only: false }
  })
  vi.mocked(submitStudioFeedback).mockImplementation(async (_project, chapter_id, region, payload) => {
    const data = feedbackData.get(`${chapter_id}:${region}`) || { requirements: '', comments: [], revision: 0 }
    return { id: payload.request_id, chapter_id, region, document_id: `doc-${chapter_id}`, source_version_id: payload.expected_current_version_id, feedback_revision: data.revision + 1, comments: data.comments, requirements: data.requirements, created_at: '2026-09-13T00:00:00Z' }
  })
  vi.mocked(writeDocument).mockImplementation(async (id, payload) => {
    versions.set(id, 'saved-v2')
    if (payload && 'content' in payload && typeof payload.content === 'string') {
      docContents.set(id, payload.content)
    }
    return { id: 'saved-v2' } as Awaited<ReturnType<typeof writeDocument>>
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('restores actual review reports without treating missing reviewers as PASS', async () => {
  vi.mocked(listChapters).mockResolvedValue([chapter('a')])
  vi.mocked(listChapterProductionRuns).mockResolvedValue([{ workflow_run_id: 'run-a' }] as Awaited<ReturnType<typeof listChapterProductionRuns>>)
  vi.mocked(getChapterProductionRun).mockResolvedValue({ chapter_workflow_run_id: 'run-a', chapter_id: 'a', status: 'REVIEW_REVISION', awaiting_user: true,
    document_id: 'doc-a', document_version_id: 'doc-a-v1', editor_report_id: 'report-a', chief_editor_required: true } as ChapterProductionState)
  vi.mocked(getChapterProductionReviewReport).mockResolvedValue({ id: 'report-a', reviewer_role: 'editor_agent', summary: '时间线存在矛盾。',
    findings: [{ sequence: 1, severity: 'blocking', rationale: '先离开再抵达。', suggested_action: '把抵达放在离开之前。' }] } as ChapterProductionReviewReport)
  open('/projects/p/studio/a')
  const report = await screen.findByRole('region', { name: '大纲与审阅面板' })
  await screen.findByText('时间线存在矛盾。')
  expect(report).not.toHaveTextContent('示例报告')
  expect(screen.getAllByText('未审阅')).toHaveLength(2)
  expect(screen.queryByText('PASS')).not.toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: '交给 Agent 修改：先离开再抵达。' })).toBeChecked()
  expect(screen.getByRole('checkbox', { name: '交给 Agent 修改：先离开再抵达。' })).toBeDisabled()
  expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveAttribute('readonly')
  expect(getChapterProductionReviewReport).toHaveBeenCalledWith('p', 'a', 'run-a', 'report-a', { documentId: 'doc-a', versionId: 'doc-a-v1' }, undefined)
  fireEvent.click(screen.getByRole('button', { name: 'Draft' }))
  expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveAttribute('readonly')
  expect(screen.getByText('正文已进入审阅，请通过修改流程继续写作。')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Outline' }))
  await screen.findByRole('region', { name: '本章大纲' })
  fireEvent.click(screen.getByRole('button', { name: 'Review' }))
  await screen.findByText('时间线存在矛盾。')
})

it('rejects failed discovery, a different review version and a mismatched reviewer', async () => {
  vi.mocked(listChapterProductionRuns).mockRejectedValue(new Error('offline'))
  open('/projects/p/studio/a')
  await screen.findByRole('alert')
  expect(screen.queryByRole('textbox', { name: '章节正文' })).not.toBeInTheDocument()
  cleanup()
  vi.mocked(listChapterProductionRuns).mockResolvedValue([{ workflow_run_id: 'run-a' }] as Awaited<ReturnType<typeof listChapterProductionRuns>>)
  vi.mocked(getChapterProductionRun).mockResolvedValue({ chapter_workflow_run_id: 'run-a', chapter_id: 'a', status: 'REVISION_READY', document_id: 'doc-a', document_version_id: 'old-version' } as ChapterProductionState)
  open('/projects/p/studio/a')
  await screen.findByRole('alert')
  expect(screen.queryByText('PASS')).not.toBeInTheDocument()
  expect(getChapterProductionReviewReport).not.toHaveBeenCalled()
  cleanup()
  vi.mocked(listChapters).mockResolvedValue([chapter('a')])
  vi.mocked(getChapterProductionRun).mockResolvedValue({ chapter_workflow_run_id: 'run-a', chapter_id: 'a', status: 'REVISION_READY',
    document_id: 'doc-a', document_version_id: 'doc-a-v1', editor_report_id: 'report-a' } as ChapterProductionState)
  vi.mocked(getChapterProductionReviewReport).mockResolvedValue({ reviewer_role: 'lore_agent' } as ChapterProductionReviewReport)
  open('/projects/p/studio/a')
  await screen.findByRole('alert')
  expect(screen.queryByText('PASS')).not.toBeInTheDocument()
})

it('flushes edited prose before submission and recovers a lost acceptance response without another acceptance', async () => {
  vi.mocked(listChapters).mockResolvedValue([chapter('a')])
  vi.mocked(listChapterProductionRuns).mockResolvedValue([{ workflow_run_id: 'run-a' }] as Awaited<ReturnType<typeof listChapterProductionRuns>>)
  let body = 'Text doc-a', version = 'doc-a-v1'
  let state = { chapter_workflow_run_id: 'run-a', chapter_id: 'a', status: 'AUTHOR_REVISION', awaiting_user: true,
    action_kind: 'author_revision', action_request_id: 'author-action', document_id: 'doc-a', document_version_id: version,
    chief_editor_required: false } as ChapterProductionState
  vi.mocked(getChapterProductionRun).mockImplementation(async () => state)
  vi.mocked(readDocumentContent).mockImplementation(async () => ({ document_id: 'doc-a', version_id: version, content: body }))
  vi.mocked(writeDocument).mockImplementation(async (_id, payload) => {
    body = payload.content; version = 'saved-v2'
    return { id: version } as Awaited<ReturnType<typeof writeDocument>>
  })
  vi.mocked(resolveChapterProductionAction).mockImplementation(async () => {
    state = { ...state, status: 'EDITOR_REVIEW', awaiting_user: false, action_request_id: null, action_kind: null, document_version_id: version }
    throw new Error('successful acceptance response lost')
  })
  vi.mocked(triggerChapterReview).mockImplementation(async () => {
    state = { ...state, status: state.status === 'EDITOR_REVIEW' ? 'LORE_FINAL_REVIEW' : 'REVISION_READY' }
    return { workflow_run_id: 'run-a', draft_document_id: 'doc-a', draft_version_id: version, action_request_id: null }
  })
  open('/projects/p/studio/a')
  const prose = await screen.findByRole('textbox', { name: '章节正文' })
  await waitFor(() => expect(prose).not.toHaveAttribute('readonly'))
  fireEvent.change(prose, { target: { value: '点击提交前的最新正文' } })
  const submit = screen.getByRole('button', { name: '提交审阅' })
  fireEvent.click(submit); fireEvent.click(submit)
  await screen.findByRole('button', { name: '重试审阅' })
  expect(body).toBe('点击提交前的最新正文')
  expect(resolveChapterProductionAction).toHaveBeenCalledExactlyOnceWith('p', 'a', 'run-a', 'author-action',
    { decision: 'accept', expected_current_version_id: 'saved-v2' }, expect.any(AbortSignal))
  expect(prose).toHaveAttribute('readonly')
  fireEvent.click(screen.getByRole('button', { name: '重试审阅' }))
  await screen.findByText('本轮审阅已完成。')
  expect(resolveChapterProductionAction).toHaveBeenCalledTimes(1)
  expect(triggerChapterReview).toHaveBeenCalledTimes(2)
  expect(prose).toHaveValue('点击提交前的最新正文')
  expect(prose).toHaveAttribute('readonly')
})

it('does not accept a draft when the save queue fails', async () => {
  vi.mocked(writeDocument).mockRejectedValue(new Error('save unavailable'))
  open('/projects/p/studio/a')
  const prose = await screen.findByRole('textbox', { name: '章节正文' })
  await waitFor(() => expect(prose).not.toHaveAttribute('readonly'))
  fireEvent.change(prose, { target: { value: '不能丢掉的正文' } })
  fireEvent.click(screen.getByRole('button', { name: '提交审阅' }))
  await screen.findByText('正文或反馈尚未保存，请先重试保存。')
  expect(resolveChapterProductionAction).not.toHaveBeenCalled()
  expect(prose).toHaveValue('不能丢掉的正文')
  expect(prose).not.toHaveAttribute('readonly')
})

async function openOutline() {
  vi.mocked(listChapters).mockResolvedValue([{ ...chapter('a'), current_draft_document_id: null, current_outline_document_id: 'outline-a' }])
  vi.mocked(readStudioFeedback).mockImplementation(async (_project, chapter_id, region) => ({ chapter_id, region, document_id: region === 'outline' ? 'outline-a' : 'doc-a', source_version_id: region === 'outline' ? 'outline-a-v1' : 'doc-a-v1', revision: 0, comments: [], requirements: '', read_only: false }))
  open('/projects/p/studio/a')
  const confirm = await screen.findByRole('button', { name: '确认大纲并生成正文' })
  await waitFor(() => expect(confirm).toBeEnabled())
  return confirm
}

it('requires explicit warning consent again for each reviewer and preserves it across response-loss retry', async () => {
  vi.mocked(listChapters).mockResolvedValue([chapter('a')])
  vi.mocked(listChapterProductionRuns).mockResolvedValue([{ workflow_run_id: 'run-a' }] as Awaited<ReturnType<typeof listChapterProductionRuns>>)
  let state = { chapter_id: 'a', chapter_workflow_run_id: 'run-a', status: 'REVIEW_REVISION', awaiting_user: true,
    action_kind: 'review_warning', action_request_id: 'warning-editor', document_id: 'doc-a', document_version_id: 'doc-a-v1' } as ChapterProductionState
  vi.mocked(getChapterProductionRun).mockImplementation(async () => state)
  vi.mocked(resolveChapterProductionAction).mockImplementation(async () => {
    state = { ...state, status: 'LORE_FINAL_REVIEW', awaiting_user: false, action_request_id: null, action_kind: null }
    throw new Error('committed warning decision response lost')
  })
  vi.mocked(triggerChapterReview).mockImplementation(async () => {
    state = { ...state, status: 'REVIEW_REVISION', awaiting_user: true, action_kind: 'review_warning', action_request_id: 'warning-lore' }
    return { workflow_run_id: 'run-a', draft_document_id: 'doc-a', draft_version_id: 'doc-a-v1', action_request_id: 'warning-lore' }
  })
  open('/projects/p/studio/a')
  const accept = await screen.findByRole('button', { name: '接受当前警告并继续审阅' })
  expect(resolveChapterProductionAction).not.toHaveBeenCalled()
  fireEvent.click(accept); fireEvent.click(accept)
  fireEvent.click(await screen.findByRole('button', { name: '重试审阅' }))
  await screen.findByRole('button', { name: '接受当前警告并继续审阅' })
  expect(resolveChapterProductionAction).toHaveBeenCalledExactlyOnceWith('p', 'a', 'run-a', 'warning-editor', { decision: 'accept_warning' }, expect.any(AbortSignal))
  expect(triggerChapterReview).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveAttribute('readonly')
})

it('confirms the exact outline version and opens only the server author-gate draft', async () => {
  vi.mocked(listChapterProductionRuns).mockResolvedValue([])
  vi.mocked(startChapterProductionV2).mockResolvedValue({ workflow_run_id: 'run-a' } as Awaited<ReturnType<typeof startChapterProductionV2>>)
  vi.mocked(getChapterProductionRun).mockResolvedValue({ chapter_id: 'a', status: 'AUTHOR_REVISION', awaiting_user: true, document_id: 'doc-a', document_version_id: 'doc-a-v1' } as ChapterProductionState)
  const confirm = await openOutline()
  fireEvent.click(screen.getByRole('button', { name: 'Draft' }))
  expect(startChapterProductionV2).not.toHaveBeenCalled()
  expect(screen.queryByRole('textbox', { name: '章节正文' })).not.toBeInTheDocument()
  fireEvent.click(confirm); fireEvent.click(confirm)
  expect(await screen.findByRole('textbox', { name: '章节正文' })).toHaveValue('Text doc-a')
  expect(approveStudioOutline).toHaveBeenCalledWith('p', 'a', 'outline-a', 'outline-a-v1')
  expect(startChapterProductionV2).toHaveBeenCalledTimes(1)
  expect(writeDocument).not.toHaveBeenCalled()
})

it('does not start a workflow when discovery fails', async () => {
  vi.mocked(listChapterProductionRuns).mockRejectedValue(new Error('offline'))
  fireEvent.click(await openOutline())
  await screen.findByText(/正文生成未完成/)
  expect(approveStudioOutline).not.toHaveBeenCalled()
  expect(startChapterProductionV2).not.toHaveBeenCalled()
})

it('recovers an existing drafting run after a lost response instead of starting again', async () => {
  vi.mocked(listChapterProductionRuns).mockResolvedValue([{ workflow_run_id: 'run-a' }] as Awaited<ReturnType<typeof listChapterProductionRuns>>)
  vi.mocked(getChapterProductionRun)
    .mockResolvedValueOnce({ chapter_id: 'a', status: 'DRAFTING' } as ChapterProductionState)
    .mockResolvedValueOnce({ chapter_id: 'a', status: 'AUTHOR_REVISION', awaiting_user: true, document_id: 'doc-a', document_version_id: 'doc-a-v1' } as ChapterProductionState)
  fireEvent.click(await openOutline())
  expect(await screen.findByRole('textbox', { name: '章节正文' })).toHaveValue('Text doc-a')
  expect(resumeChapterProduction).toHaveBeenCalledWith('p', 'a', 'run-a')
  expect(approveStudioOutline).not.toHaveBeenCalled()
  expect(startChapterProductionV2).not.toHaveBeenCalled()
})

it('deep-links chapters, flushes before selection, and restores chapter/setting navigation with browser history', async () => {
  open('/projects/p/studio/a')
  const input = await screen.findByRole('textbox', { name: '章节正文' })
  expect(input).toHaveValue('Text doc-a')
  fireEvent.change(input, { target: { value: 'Keep chapter A edit' } })
  fireEvent.click(screen.getByRole('button', { name: '展开章节侧边栏' }))
  fireEvent.click(screen.getByRole('button', { name: '第2话 Chapter b' }))
  await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent('/projects/p/studio/b?stage=Draft'))
  expect(writeDocument).toHaveBeenCalledWith('doc-a', { content: 'Keep chapter A edit', expected_current_version_id: 'doc-a-v1' })
  expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('Text doc-b')
  fireEvent.click(screen.getByRole('button', { name: 'History back' }))
  await waitFor(() => expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('Keep chapter A edit'))
  fireEvent.click(screen.getByRole('button', { name: '设置' }))
  await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent('/setting-collections/sc-p'))
  expect(screen.getByTestId('state')).toHaveTextContent('returnTo')
  fireEvent.click(screen.getByRole('button', { name: 'History back' }))
  expect(await screen.findByRole('textbox', { name: '章节正文' })).toHaveValue('Keep chapter A edit')
  fireEvent.click(screen.getByRole('button', { name: 'History forward' }))
  await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent('/setting-collections/sc-p'))
})

it('does not show the previous project while another project loads or accept its stale response', async () => {
  let finish!: (value: Project) => void
  vi.mocked(getProject).mockImplementation(id => id === 'p' ? new Promise(resolve => { finish = resolve }) : Promise.resolve(project(id)))
  open('/projects/p/studio/a')
  fireEvent.click(screen.getByRole('link', { name: 'Other novel' }))
  expect(await screen.findByRole('textbox', { name: '章节正文' })).toHaveValue('Text doc-a')
  await act(async () => finish(project('p')))
  fireEvent.click(screen.getByRole('button', { name: '展开章节侧边栏' }))
  const directory = screen.getByRole('region', { name: '章节目录' })
  expect(within(directory).getByRole('heading', { name: 'Novel q' })).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Novel p' })).not.toBeInTheDocument()
})

it('keeps edits made just before browser Back pending until the server saves them on return', async () => {
  open('/projects/p/studio/a')
  await screen.findByRole('textbox', { name: '章节正文' })
  fireEvent.click(screen.getByRole('button', { name: '展开章节侧边栏' }))
  fireEvent.click(screen.getByRole('button', { name: '第2话 Chapter b' }))
  await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent('/studio/b'))
  fireEvent.change(screen.getByRole('textbox', { name: '章节正文' }), { target: { value: 'Pending B edit' } })
  fireEvent.click(screen.getByRole('button', { name: 'History back' }))
  await waitFor(() => expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('Text doc-a'))
  expect(JSON.parse(sessionStorage.getItem('guranovel:draft-recovery:doc-b')!)).toMatchObject({ text: 'Pending B edit' })
  fireEvent.click(screen.getByRole('button', { name: 'History forward' }))
  await waitFor(() => expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('Pending B edit'))
  await waitFor(() => expect(writeDocument).toHaveBeenCalledWith('doc-b', { content: 'Pending B edit', expected_current_version_id: 'doc-b-v1' }))
  expect(sessionStorage.getItem('guranovel:draft-recovery:doc-b')).toBeNull()
})

it('reports invalid chapter links without falling back to a different chapter', async () => {
  open('/projects/p/studio/unknown')
  expect(await screen.findByRole('alert')).toHaveTextContent('此作品中未找到该章节')
  expect(screen.queryByRole('textbox', { name: '章节正文' })).not.toBeInTheDocument()
})

it('shows the final document readonly without attaching draft feedback to a different source', async () => {
  vi.mocked(listChapters).mockResolvedValue([{ ...chapter('a'), final_document_id: 'final-a' }])
  open('/projects/p/studio/a')
  const prose = await screen.findByRole('textbox', { name: '章节正文' })
  expect(prose).toHaveValue('Text final-a')
  expect(prose).toHaveAttribute('readonly')
  expect(readDocumentContent).toHaveBeenCalledWith('final-a')
  expect(readStudioFeedback).not.toHaveBeenCalled()
  expect(screen.queryByRole('group', { name: '已提交评论圆点' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '本地定稿' })).toBeDisabled()
  expect(screen.queryByRole('button', { name: '创建还原点' })).not.toBeInTheDocument()
  expect(writeDocument).not.toHaveBeenCalled()
})

it('restores unfinished finalization without claiming that a staged final document is completed', async () => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  vi.mocked(listChapters).mockResolvedValue([{ ...chapter('a'), final_document_id: 'staged-final' }])
  vi.mocked(listChapterProductionRuns).mockResolvedValue([{ workflow_run_id: 'run-a' }] as Awaited<ReturnType<typeof listChapterProductionRuns>>)
  vi.mocked(getChapterProductionRun).mockResolvedValue({ chapter_workflow_run_id: 'run-a', chapter_id: 'a', status: 'ARCHIVE_UPDATE',
    document_id: 'doc-a', document_version_id: 'doc-a-v1', editor_report_id: 'report-a', awaiting_user: false } as ChapterProductionState)
  vi.mocked(getChapterProductionReviewReport).mockResolvedValue({ id: 'report-a', reviewer_role: 'editor_agent', findings: [], summary: '已审阅' } as unknown as ChapterProductionReviewReport)
  open('/projects/p/studio/a')
  expect(await screen.findByRole('textbox', { name: '章节正文' })).toHaveValue('Text doc-a')
  expect(screen.queryByText('本章已在本地定稿')).not.toBeInTheDocument()
  expect(readDocumentContent).not.toHaveBeenCalledWith('staged-final')
  const finalize = screen.getByRole('button', { name: '本地定稿' })
  expect(finalize).toBeEnabled()
  fireEvent.click(finalize)
  expect(await screen.findByRole('button', { name: '重试本地定稿' })).toBeEnabled()
})

it('keeps browser-history edits pending when session storage cannot write a recovery copy', async () => {
  open('/projects/p/studio/a')
  await screen.findByRole('textbox', { name: '章节正文' })
  fireEvent.click(screen.getByRole('button', { name: '展开章节侧边栏' }))
  fireEvent.click(screen.getByRole('button', { name: '第2话 Chapter b' }))
  await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent('/studio/b'))
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('Storage full', 'QuotaExceededError') })
  fireEvent.change(screen.getByRole('textbox', { name: '章节正文' }), { target: { value: 'Keep this without session storage' } })
  fireEvent.click(screen.getByRole('button', { name: 'History back' }))
  await waitFor(() => expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('Text doc-a'))
  fireEvent.click(screen.getByRole('button', { name: 'History forward' }))
  await waitFor(() => expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('Keep this without session storage'))
  await waitFor(() => expect(writeDocument).toHaveBeenCalledWith('doc-b', { content: 'Keep this without session storage', expected_current_version_id: 'doc-b-v1' }))
})

it('creates a server chapter once, uses its assigned identity, and keeps the old workbench accessible', async () => {
  vi.mocked(listChapters).mockResolvedValue([])
  let finish!: (value: Chapter) => void
  vi.mocked(createChapter).mockImplementation(() => new Promise(resolve => { finish = resolve }))
  open('/projects/p/studio')
  const button = await screen.findByRole('button', { name: '新建章节' })
  fireEvent.click(button); fireEvent.click(button)
  await waitFor(() => expect(createChapter).toHaveBeenCalledTimes(1))
  expect(createChapter).toHaveBeenCalledWith('p', { title: '未命名章节', metadata: { volume: '第一卷' } })
  expect(button).toBeDisabled()
  expect(screen.getByRole('link', { name: '前往现有工作台' })).toHaveAttribute('href', '/projects/p')
  await act(async () => finish({ ...chapter('server-chapter', 42), current_draft_document_id: null }))
  expect(screen.getByTestId('url')).toHaveTextContent('/projects/p/studio/server-chapter?stage=Outline')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '展开章节侧边栏' }))
  expect(screen.getByRole('button', { name: '第42话 Chapter server-chapter' })).toBeInTheDocument()
})

it('redirects legacy ?view=Detail to dashboard with project query', async () => {
  open('/projects/p/studio?view=Detail')
  await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent('/?project=p'))
})

it('redirects legacy ?view=Setting to setting collection workspace', async () => {
  open('/projects/p/studio?view=Setting')
  await waitFor(() => expect(screen.getByTestId('url')).toHaveTextContent('/setting-collections/sc-p'))
})

it('halts navigation and shows toast alert when autosave flush fails', async () => {
  open('/projects/p/studio/a')
  const input = await screen.findByRole('textbox', { name: '章节正文' })
  expect(input).toHaveValue('Text doc-a')
  fireEvent.change(input, { target: { value: 'Failing change' } })
  vi.mocked(writeDocument).mockRejectedValueOnce(new Error('Network error'))
  fireEvent.click(screen.getByRole('button', { name: '设置' }))
  await waitFor(() => expect(screen.getByText('正文尚未保存，已暂停切换。请先重试自动保存。')).toBeInTheDocument())
  expect(screen.getByTestId('url')).toHaveTextContent('/projects/p/studio/a')
  expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('Failing change')
})

it('reports missing project with an alert when project does not exist', async () => {
  vi.mocked(getProject).mockRejectedValue(new ApiError(404, 'not_found', 'Project not found'))
  open('/projects/unknown/studio')
  expect(await screen.findByRole('alert')).toHaveTextContent('未找到此作品，可能已被删除或无权访问。')
  expect(screen.getByRole('link', { name: '返回首页' })).toHaveAttribute('href', '/')
})

it('handles invalid chapter in preview without crashing', async () => {
  render(
    <MemoryRouter initialEntries={['/preview/studio/unknown']}>
      <Routes>
        <Route path="/preview/studio" element={<Studio />} />
        <Route path="/preview/studio/:chapterId" element={<Studio />} />
      </Routes>
    </MemoryRouter>,
  )
  expect(await screen.findByRole('alert')).toHaveTextContent('此作品中未找到该章节')
  expect(screen.getByRole('link', { name: '返回作品' })).toHaveAttribute('href', '/preview/studio')
})

it('submits formal Draft feedback revision, flushes prose and adopts the new candidate draft', async () => {
  vi.mocked(listChapters).mockResolvedValue([chapter('a')])
  vi.mocked(listChapterProductionRuns).mockResolvedValue([{ workflow_run_id: 'run-a' }] as Awaited<ReturnType<typeof listChapterProductionRuns>>)
  let runState = {
    chapter_workflow_run_id: 'run-a', chapter_id: 'a', status: 'AUTHOR_REVISION', awaiting_user: true,
    action_kind: 'author_revision', action_request_id: 'action-a', document_id: 'doc-a', document_version_id: 'doc-a-v1',
    chief_editor_required: false,
  } as ChapterProductionState
  vi.mocked(getChapterProductionRun).mockImplementation(async () => runState)
  vi.mocked(submitStudioFeedback).mockImplementation(async (_p, _c, _r, payload) => ({
    id: payload.request_id, chapter_id: 'a', region: 'draft', document_id: 'doc-a', source_version_id: 'doc-a-v1',
    feedback_revision: 1, comments: [], requirements: '让剧情更加紧凑', created_at: '2026-09-13T00:00:00Z',
  }))
  vi.mocked(requestStudioFeedbackRevision).mockImplementation(async () => {
    runState = { ...runState, document_version_id: 'doc-a-v2', action_request_id: 'action-b' }
    return { workflow_run_id: 'run-a', draft_document_id: 'doc-a', draft_version_id: 'doc-a-v2', action_request_id: 'action-b' }
  })
  vi.mocked(readDocumentContent).mockImplementation(async id => ({
    document_id: id, version_id: runState.document_version_id || 'doc-a-v1',
    content: runState.document_version_id === 'doc-a-v2' ? '修改后的紧凑正文' : '初始正文 Text doc-a',
  }))

  open('/projects/p/studio/a')
  const prose = await screen.findByRole('textbox', { name: '章节正文' })
  await waitFor(() => expect(prose).not.toHaveAttribute('readonly'))
  expect(prose).toHaveValue('初始正文 Text doc-a')

  const requirementsInput = screen.getByRole('textbox', { name: '给写作 Agent 的要求' })
  fireEvent.change(requirementsInput, { target: { value: '让剧情更加紧凑' } })

  const sendBtn = screen.getByRole('button', { name: '发送写作要求' })
  await waitFor(() => expect(sendBtn).not.toBeDisabled())
  fireEvent.click(sendBtn)

  await waitFor(() => expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('修改后的紧凑正文'))
  expect(submitStudioFeedback).toHaveBeenCalledWith('p', 'a', 'draft', expect.objectContaining({
    expected_current_version_id: 'doc-a-v1',
  }))
  expect(requestStudioFeedbackRevision).toHaveBeenCalledWith('p', 'a', 'run-a', expect.objectContaining({
    document_id: 'doc-a', version_id: 'doc-a-v1', action_request_id: 'action-a',
  }), expect.any(AbortSignal))
})

it('recovers a lost feedback revision response via retry button and preserves request identity', async () => {
  vi.mocked(listChapters).mockResolvedValue([chapter('a')])
  vi.mocked(listChapterProductionRuns).mockResolvedValue([{ workflow_run_id: 'run-a' }] as Awaited<ReturnType<typeof listChapterProductionRuns>>)
  let runState = {
    chapter_workflow_run_id: 'run-a', chapter_id: 'a', status: 'AUTHOR_REVISION', awaiting_user: true,
    action_kind: 'author_revision', action_request_id: 'action-a', document_id: 'doc-a', document_version_id: 'doc-a-v1',
    chief_editor_required: false,
  } as ChapterProductionState
  vi.mocked(getChapterProductionRun).mockImplementation(async () => runState)
  vi.mocked(submitStudioFeedback).mockImplementation(async (_p, _c, _r, payload) => ({
    id: payload.request_id, chapter_id: 'a', region: 'draft', document_id: 'doc-a', source_version_id: 'doc-a-v1',
    feedback_revision: 1, comments: [], requirements: '重试修改要求', created_at: '2026-09-13T00:00:00Z',
  }))
  vi.mocked(requestStudioFeedbackRevision)
    .mockRejectedValueOnce(new Error('Network loss during feedback revision'))
    .mockImplementationOnce(async () => {
      runState = { ...runState, document_version_id: 'doc-a-v2', action_request_id: 'action-b' }
      return { workflow_run_id: 'run-a', draft_document_id: 'doc-a', draft_version_id: 'doc-a-v2', action_request_id: 'action-b' }
    })
  vi.mocked(readDocumentContent).mockImplementation(async id => ({
    document_id: id, version_id: runState.document_version_id || 'doc-a-v1',
    content: runState.document_version_id === 'doc-a-v2' ? '重试成功的新正文' : '初始正文 Text doc-a',
  }))

  open('/projects/p/studio/a')
  await screen.findByRole('textbox', { name: '章节正文' })
  const requirementsInput = screen.getByRole('textbox', { name: '给写作 Agent 的要求' })
  fireEvent.change(requirementsInput, { target: { value: '重试修改要求' } })

  const sendBtn = screen.getByRole('button', { name: '发送写作要求' })
  await waitFor(() => expect(sendBtn).not.toBeDisabled())
  fireEvent.click(sendBtn)

  const retryBtn = await screen.findByRole('button', { name: '重试修改' })
  expect(requestStudioFeedbackRevision).toHaveBeenCalledTimes(1)

  fireEvent.click(retryBtn)
  await waitFor(() => expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('重试成功的新正文'))
  expect(requestStudioFeedbackRevision).toHaveBeenCalledTimes(2)
  const firstSubmission = vi.mocked(requestStudioFeedbackRevision).mock.calls[0][3]
  const secondSubmission = vi.mocked(requestStudioFeedbackRevision).mock.calls[1][3]
  expect(firstSubmission.submission_id).toBe(secondSubmission.submission_id)
})

it('restores pending feedback revision upon reload and completes revision via retry', async () => {
  vi.mocked(listChapters).mockResolvedValue([chapter('a')])
  vi.mocked(listChapterProductionRuns).mockResolvedValue([{ workflow_run_id: 'run-a' }] as Awaited<ReturnType<typeof listChapterProductionRuns>>)
  let runState = {
    chapter_workflow_run_id: 'run-a', chapter_id: 'a', status: 'AUTHOR_REVISION', awaiting_user: true,
    action_kind: 'author_revision', action_request_id: 'action-a', document_id: 'doc-a', document_version_id: 'doc-a-v1',
    chief_editor_required: false,
  } as ChapterProductionState
  vi.mocked(getChapterProductionRun).mockImplementation(async () => runState)
  vi.mocked(submitStudioFeedback).mockImplementation(async (_p, _c, _r, payload) => ({
    id: payload.request_id, chapter_id: 'a', region: 'draft', document_id: 'doc-a', source_version_id: 'doc-a-v1',
    feedback_revision: 1, comments: [], requirements: '恢复待核对要求', created_at: '2026-09-13T00:00:00Z',
  }))
  vi.mocked(requestStudioFeedbackRevision).mockImplementation(async () => {
    runState = { ...runState, document_version_id: 'doc-a-v2', action_request_id: 'action-b' }
    return { workflow_run_id: 'run-a', draft_document_id: 'doc-a', draft_version_id: 'doc-a-v2', action_request_id: 'action-b' }
  })
  vi.mocked(readDocumentContent).mockImplementation(async id => ({
    document_id: id, version_id: runState.document_version_id || 'doc-a-v1',
    content: runState.document_version_id === 'doc-a-v2' ? '恢复后成功的新正文' : '初始正文 Text doc-a',
  }))

  sessionStorage.setItem('guranovel:feedback-revision:p:a', JSON.stringify({
    runId: 'run-a', documentId: 'doc-a', actionId: 'action-a',
    submission: { request_id: 'sub-recovered', expected_current_version_id: 'doc-a-v1', expected_revision: 0, comment_ids: [] },
  }))

  open('/projects/p/studio/a')
  expect(await screen.findByRole('alert')).toHaveTextContent('上次反馈修改尚待核对，重试会恢复原请求。')
  const retryBtn = screen.getByRole('button', { name: '重试修改' })
  fireEvent.click(retryBtn)

  await waitFor(() => expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('恢复后成功的新正文'))
  expect(sessionStorage.getItem('guranovel:feedback-revision:p:a')).toBeNull()
})
