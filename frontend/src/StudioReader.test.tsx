import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import StudioReader from './StudioReader'
import { newStudioChapter } from './studioPreview'
import { readDocumentContent } from './api/client'
import { getChapterProductionRun, type ChapterProductionState } from './api/chapterProductionV2Client'
import { getReaderPanel, listReaderPanels, resumeReaderPanel, startReaderPanel, type ReaderPanelSessionDetail } from './api/readerPanelClient'

vi.mock('./api/client', async original => ({ ...await original<typeof import('./api/client')>(), readDocumentContent: vi.fn() }))
vi.mock('./api/chapterProductionV2Client', () => ({ getChapterProductionRun: vi.fn() }))
vi.mock('./api/readerPanelClient', () => ({ getReaderPanel: vi.fn(), listReaderPanels: vi.fn(), resumeReaderPanel: vi.fn(), startReaderPanel: vi.fn(), cancelReaderPanel: vi.fn() }))
const chapter = { ...newStudioChapter('chapter', 1, '第一章'), documentId: 'doc', versionId: 'v1', draft: '服务器正文', readers: ['plot', 'world'],
  productionState: { chapter_workflow_run_id: 'run' } as ChapterProductionState }
const panel = { is_noop: false, session_id: 'panel', workflow_run_id: 'panel-run', project_id: 'project', chapter_id: 'chapter',
  document_id: 'doc', document_version_id: 'v1', reader_profile_ids: ['studio_plot', 'studio_world'], simulated: true,
  status: 'independent_reading', stale: false, planned_readers: 2, completed_readers: 0, initial_reports: [], transcript: [],
  source_hash: 'a'.repeat(64), mode: 'quick', degradation_reason: null, failure_reason: null,
  failed_readers: 0, issue_count: 0, initial_ballot_count: 0, final_ballot_count: 0, discussion_message_count: 0,
  created_at: null, updated_at: null, completed_at: null, review_report: null, issues: [],
  permitted_operations: ['cancel', 'resume'] } satisfies ReaderPanelSessionDetail
const open = () => render(<StudioReader projectId="project" chapter={chapter} onChange={vi.fn()} onFinal={vi.fn()} />)
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear()
  vi.mocked(listReaderPanels).mockResolvedValue([])
  vi.mocked(readDocumentContent).mockResolvedValue({ document_id: 'doc', version_id: 'v1', content: chapter.draft })
  vi.mocked(getChapterProductionRun).mockResolvedValue({ status: 'REVISION_READY', awaiting_user: false, document_id: 'doc', document_version_id: 'v1' } as ChapterProductionState)
  vi.mocked(getReaderPanel).mockResolvedValue(panel)
})
afterEach(cleanup)

it('recovers the identical invitation after a lost response and reload, then shows actual named reports', async () => {
  vi.mocked(startReaderPanel).mockRejectedValueOnce(new Error('response lost')).mockResolvedValue(panel)
  open()
  const start = await screen.findByRole('button', { name: '开始阅读' })
  await waitFor(() => expect(start).toBeEnabled())
  fireEvent.click(start)
  await screen.findByRole('button', { name: '重试阅读请求' })
  const request = vi.mocked(startReaderPanel).mock.calls[0][2]
  expect(request.reader_profile_ids).toEqual(['studio_plot', 'studio_world'])
  cleanup()
  const done = { ...panel, status: 'completed', completed_readers: 2, permitted_operations: [],
    initial_reports: [{ reader_profile_id: 'studio_world', overall_reaction: '真实保存的设定反馈', concerns: [] }],
    review_report: { summary: '服务器阅读总结', blocking_issues: [], warnings: [] } } as unknown as ReaderPanelSessionDetail
  vi.mocked(listReaderPanels).mockResolvedValue([panel])
  vi.mocked(resumeReaderPanel).mockImplementation(async () => { vi.mocked(getReaderPanel).mockResolvedValue(done); return done })
  open()
  const retry = await screen.findByRole('button', { name: '重试阅读请求' })
  fireEvent.click(retry)
  await screen.findByText('服务器阅读总结')
  expect(vi.mocked(startReaderPanel).mock.calls[1][2]).toEqual(request)
  expect(resumeReaderPanel).toHaveBeenCalledTimes(1)
  expect(screen.getByText('真实保存的设定反馈').parentElement).toHaveTextContent('设定党')
  expect(screen.getByText('当前为模拟阅读结果')).toBeInTheDocument()
  expect(screen.queryByText('示例发言')).not.toBeInTheDocument()
  expect(sessionStorage.length).toBe(0)
})

it('rejects a changed source before creating or resuming readers', async () => {
  vi.mocked(getChapterProductionRun).mockResolvedValue({ status: 'REVIEW_REVISION', awaiting_user: true } as ChapterProductionState)
  open()
  const start = await screen.findByRole('button', { name: '开始阅读' })
  await waitFor(() => expect(start).toBeEnabled())
  fireEvent.click(start)
  await screen.findByRole('alert')
  expect(startReaderPanel).not.toHaveBeenCalled()
  expect(resumeReaderPanel).not.toHaveBeenCalled()
})
