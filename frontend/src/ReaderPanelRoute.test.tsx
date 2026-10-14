import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import * as documentApi from './api/client'
import * as panelApi from './api/readerPanelClient'
import * as productionApi from './api/chapterProductionV2Client'

const ids = {
  project: '11111111-1111-4111-8111-111111111111',
  chapter: '22222222-2222-4222-8222-222222222222',
  document: '33333333-3333-4333-8333-333333333333',
  version: '44444444-4444-4444-8444-444444444444',
  session: '55555555-5555-4555-8555-555555555555',
  workflow: '66666666-6666-4666-8666-666666666666',
}

const hash = 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789'
const startPath = `/projects/${ids.project}/chapters/${ids.chapter}/documents/${ids.document}/versions/${ids.version}/reader-panel`

function session(
  overrides: Partial<panelApi.ReaderPanelSessionDetail> = {},
): panelApi.ReaderPanelSessionDetail {
  return {
    is_noop: false,
    session_id: ids.session,
    workflow_run_id: ids.workflow,
    project_id: ids.project,
    chapter_id: ids.chapter,
    document_id: ids.document,
    document_version_id: ids.version,
    source_hash: hash,
    mode: 'standard',
    status: 'completed',
    stale: false,
    degradation_reason: null,
    failure_reason: null,
    planned_readers: 4,
    completed_readers: 4,
    failed_readers: 0,
    issue_count: 1,
    initial_ballot_count: 4,
    final_ballot_count: 4,
    discussion_message_count: 5,
    created_at: '2026-08-28T01:00:00Z',
    updated_at: '2026-08-28T01:02:00Z',
    completed_at: '2026-08-28T01:02:00Z',
    review_report: {
      summary: 'Keep the opening, but clarify the transition for editor review.',
      blocking_issues: [],
      warnings: ['One high-confidence minority risk remains.'],
      notes: ['No manuscript changes were made.'],
      suggested_actions: [{
        priority: 'manual_review',
        target_segment_ids: ['S002'],
        suggested_action: 'clarify',
        instruction: 'Consider one causal beat.',
      }],
    },
    issues: [{
      issue_number: 1,
      title: 'Abrupt transition',
      category: 'pacing',
      symptom: 'The scene changes before the cause is clear.',
      root_cause_hypotheses: ['The causal beat is compressed.'],
      evidence: [{ segment_ids: ['S002'], note: 'The transition begins here.' }],
      target_audience_relevance: 'high',
      minority_risk: true,
      discussion_status: 'closed',
      consensus_class: 'polarized',
      recommended_priority: 'manual_review',
    }],
    initial_reports: [],
    transcript: null,
    permitted_operations: [],
    ...overrides,
  }
}

function Location() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

function renderRoute(path = startPath) {
  return render(<MemoryRouter initialEntries={[path]}><App /><Location /></MemoryRouter>)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

beforeEach(() => {
  localStorage.clear(); sessionStorage.clear()
  vi.spyOn(documentApi, 'getProject').mockResolvedValue({
    id: ids.project, title: '读者验证作品', metadata: {},
  } as documentApi.Project)
  vi.spyOn(documentApi, 'listChapters').mockResolvedValue([{
    id: ids.chapter, chapter_number: 1, title: '读者验证章节', metadata: {},
    current_draft_document_id: ids.document, current_outline_document_id: null,
    final_document_id: null,
  } as documentApi.Chapter])
  vi.spyOn(documentApi, 'readDocumentContent').mockResolvedValue({
    document_id: ids.document, version_id: ids.version, content: '已通过审阅的正文。',
  })
  vi.spyOn(documentApi, 'readStudioFeedback').mockResolvedValue({
    chapter_id: ids.chapter, region: 'draft', document_id: ids.document,
    source_version_id: ids.version, revision: 0, comments: [], requirements: '', read_only: true,
  })
  vi.spyOn(productionApi, 'listChapterProductionRuns').mockResolvedValue([
    { workflow_run_id: ids.workflow },
  ] as Awaited<ReturnType<typeof productionApi.listChapterProductionRuns>>)
  vi.spyOn(productionApi, 'getChapterProductionRun').mockResolvedValue({
    chapter_workflow_run_id: ids.workflow, chapter_id: ids.chapter, status: 'REVISION_READY',
    document_id: ids.document, document_version_id: ids.version, awaiting_user: false,
  } as productionApi.ChapterProductionState)
  vi.spyOn(panelApi, 'listReaderPanels').mockResolvedValue([])
})

describe('retired Reader Panel links in Studio', () => {
  it('starts against the exact source and restores the explicit session without modifying the manuscript', async () => {
    const write = vi.spyOn(documentApi, 'writeDocument')
    const result = session({ reader_profile_ids: ['studio_plot', 'studio_world'] })
    vi.spyOn(panelApi, 'startReaderPanel').mockResolvedValue({
      ...result, status: 'independent_reading', completed_readers: 0, review_report: null,
    })
    vi.spyOn(panelApi, 'getReaderPanel').mockResolvedValue(result)
    renderRoute()
    const start = await screen.findByRole('button', { name: '开始阅读' })
    await waitFor(() => expect(screen.getByRole('button', { name: '邀请剧情党' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '邀请剧情党' }))
    fireEvent.click(screen.getByRole('button', { name: '邀请设定党' }))
    fireEvent.click(start)
    await screen.findByText('Keep the opening, but clarify the transition for editor review.')
    expect(panelApi.startReaderPanel).toHaveBeenCalledWith(
      ids.project, ids.chapter, expect.objectContaining({
        document_id: ids.document, document_version_id: ids.version,
        reader_profile_ids: ['studio_plot', 'studio_world'], mode: 'quick',
        idempotency_key: expect.any(String),
      }), expect.any(AbortSignal),
    )
    expect(screen.getByText('读者意见供作者参考，尚未修改正文。')).toBeInTheDocument()
    expect(write).not.toHaveBeenCalled()
    cleanup()
    vi.mocked(panelApi.listReaderPanels).mockClear()
    renderRoute(startPath + '/' + ids.session)
    await screen.findByText('Keep the opening, but clarify the transition for editor review.')
    const url = new URL(screen.getByTestId('location').textContent!, 'http://localhost')
    expect(url.pathname).toBe('/projects/' + ids.project + '/studio/' + ids.chapter)
    expect(url.searchParams.get('readerVersion')).toBe(ids.version)
    expect(url.searchParams.get('readerSession')).toBe(ids.session)
    expect(panelApi.listReaderPanels).not.toHaveBeenCalled()
    expect(panelApi.getReaderPanel).toHaveBeenLastCalledWith(
      ids.project, ids.chapter, ids.session, expect.any(Object), expect.any(AbortSignal),
    )
    expect(write).not.toHaveBeenCalled()
  })

  it('rejects a wrong-scope session without rendering or applying its report', async () => {
    const write = vi.spyOn(documentApi, 'writeDocument')
    vi.spyOn(panelApi, 'getReaderPanel').mockResolvedValue(session({
      document_version_id: '77777777-7777-4777-8777-777777777777',
      review_report: {
        summary: 'PRIVATE WRONG-SCOPE REPORT',
        blocking_issues: [], warnings: [], notes: [], suggested_actions: [],
      },
    }))
    renderRoute(startPath + '/' + ids.session)
    expect(await screen.findByRole('alert')).toHaveTextContent('读者会话加载失败')
    expect(screen.queryByText('PRIVATE WRONG-SCOPE REPORT')).not.toBeInTheDocument()
    expect(panelApi.listReaderPanels).not.toHaveBeenCalled()
    expect(write).not.toHaveBeenCalled()
  })
})
