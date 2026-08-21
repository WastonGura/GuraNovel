import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from './api/chapterProductionV2Client'
import { ChapterProductionV2Workbench } from './ChapterProductionV2Workbench'

const ids = {
  project: '11111111-1111-4111-8111-111111111111',
  chapter: '22222222-2222-4222-8222-222222222222',
  run: '33333333-3333-4333-8333-333333333333',
  action: '44444444-4444-4444-8444-444444444444',
  document: '55555555-5555-4555-8555-555555555555',
  version: '66666666-6666-4666-8666-666666666666',
  finalDoc: '77777777-7777-4777-8777-777777777777',
  finalVer: '88888888-8888-4888-8888-888888888888',
  report: '99999999-9999-4999-8999-999999999999',
  segment: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
}

const sampleHash = 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789'

function stateFixture(overrides: Partial<api.ChapterProductionState> = {}): api.ChapterProductionState {
  return {
    chapter_workflow_run_id: ids.run,
    chapter_id: ids.chapter,
    status: 'DRAFTING',
    current_node: 'drafting',
    awaiting_user: false,
    review_policy_version: 'chapter-quality-v1',
    chief_editor_required: true,
    document_id: ids.document,
    document_version_id: ids.version,
    content_hash: sampleHash,
    editor_report_id: null,
    chief_editor_report_id: null,
    lore_report_id: null,
    action_request_id: null,
    action_kind: null,
    failed_from_status: null,
    failure_code: null,
    ...overrides,
  }
}

describe('ChapterProductionV2Workbench', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders not started state when no runs exist, and starts production on click', async () => {
    vi.spyOn(api, 'listChapterProductionRuns').mockResolvedValue([])
    vi.spyOn(api, 'startChapterProductionV2').mockResolvedValue({
      workflow_run_id: ids.run,
      action_request_id: ids.action,
      outline_document_id: ids.document,
      outline_version_id: ids.version,
      draft_document_id: ids.finalDoc,
      draft_version_id: ids.finalVer,
    })
    vi.spyOn(api, 'getChapterProductionRun').mockResolvedValue(
      stateFixture({ status: 'DRAFTING', current_node: 'drafting' }),
    )

    render(<ChapterProductionV2Workbench projectId={ids.project} chapterId={ids.chapter} />)

    expect(await screen.findByText('Chapter production (V2) has not started yet.')).toBeInTheDocument()
    const startButton = screen.getByRole('button', { name: 'Start chapter production (V2)' })
    expect(startButton).toBeInTheDocument()

    fireEvent.click(startButton)

    await waitFor(() => {
      expect(api.startChapterProductionV2).toHaveBeenCalledWith(ids.project, ids.chapter, expect.any(AbortSignal))
    })
    expect(await screen.findByText('Drafting chapter in progress…')).toBeInTheDocument()
  })

  it('handles AUTHOR_REVISION and accepts draft', async () => {
    const authorState = stateFixture({
      status: 'AUTHOR_REVISION',
      current_node: 'author_revision',
      awaiting_user: true,
      action_request_id: ids.action,
      action_kind: 'author_revision',
    })
    const afterAcceptState = stateFixture({
      status: 'EDITOR_REVIEW',
      current_node: 'editor_review',
      awaiting_user: false,
    })

    vi.spyOn(api, 'listChapterProductionRuns').mockResolvedValue([])
    vi.spyOn(api, 'getChapterProductionRun')
      .mockResolvedValueOnce(authorState)
      .mockResolvedValueOnce(afterAcceptState)
    vi.spyOn(api, 'resolveChapterProductionAction').mockResolvedValue({
      workflow_run_id: ids.run,
      draft_document_id: ids.document,
      draft_version_id: ids.version,
      action_request_id: null,
    })

    render(
      <ChapterProductionV2Workbench
        projectId={ids.project}
        chapterId={ids.chapter}
        initialWorkflowRunId={ids.run}
      />,
    )

    expect(await screen.findByRole('region', { name: 'Author revision required' })).toBeInTheDocument()
    expect(screen.getByText(ids.version)).toBeInTheDocument()

    const acceptButton = screen.getByRole('button', { name: 'Confirm accept draft' })
    fireEvent.click(acceptButton)

    await waitFor(() => {
      expect(api.resolveChapterProductionAction).toHaveBeenCalledWith(
        ids.project,
        ids.chapter,
        ids.run,
        ids.action,
        { decision: 'accept', feedback: undefined, target_segment_ids: undefined, content: undefined },
        expect.any(AbortSignal),
      )
    })
  })

  it('submits feedback revision with feedback text and segment IDs', async () => {
    const authorState = stateFixture({
      status: 'AUTHOR_REVISION',
      current_node: 'author_revision',
      awaiting_user: true,
      action_request_id: ids.action,
      action_kind: 'author_revision',
    })

    vi.spyOn(api, 'getChapterProductionRun').mockResolvedValue(authorState)
    vi.spyOn(api, 'resolveChapterProductionAction').mockResolvedValue({
      workflow_run_id: ids.run,
      draft_document_id: ids.document,
      draft_version_id: ids.version,
      action_request_id: null,
    })

    render(
      <ChapterProductionV2Workbench
        projectId={ids.project}
        chapterId={ids.chapter}
        initialWorkflowRunId={ids.run}
      />,
    )

    expect(await screen.findByRole('region', { name: 'Author revision required' })).toBeInTheDocument()

    // Switch tab to feedback
    const feedbackTab = screen.getByRole('tab', { name: 'Propose feedback revision' })
    fireEvent.click(feedbackTab)

    const textarea = screen.getByRole('textbox', { name: 'Revision feedback' })
    fireEvent.change(textarea, { target: { value: 'Expand the opening scene with more sensory details.' } })

    const segmentsInput = screen.getByRole('textbox', { name: 'Target segment IDs (optional, UUID list)' })
    fireEvent.change(segmentsInput, { target: { value: ids.segment } })

    const submitButton = screen.getByRole('button', { name: 'Submit feedback revision' })
    fireEvent.click(submitButton)

    await waitFor(() => {
      expect(api.resolveChapterProductionAction).toHaveBeenCalledWith(
        ids.project,
        ids.chapter,
        ids.run,
        ids.action,
        {
          decision: 'request_feedback_revision',
          feedback: 'Expand the opening scene with more sensory details.',
          target_segment_ids: [ids.segment],
          content: undefined,
        },
        expect.any(AbortSignal),
      )
    })
  })

  it('submits manual edit content', async () => {
    const authorState = stateFixture({
      status: 'AUTHOR_REVISION',
      current_node: 'author_revision',
      awaiting_user: true,
      action_request_id: ids.action,
      action_kind: 'author_revision',
    })

    vi.spyOn(api, 'getChapterProductionRun').mockResolvedValue(authorState)
    vi.spyOn(api, 'resolveChapterProductionAction').mockResolvedValue({
      workflow_run_id: ids.run,
      draft_document_id: ids.document,
      draft_version_id: ids.version,
      action_request_id: null,
    })

    render(
      <ChapterProductionV2Workbench
        projectId={ids.project}
        chapterId={ids.chapter}
        initialWorkflowRunId={ids.run}
      />,
    )

    expect(await screen.findByRole('region', { name: 'Author revision required' })).toBeInTheDocument()

    // Switch tab to manual edit
    const manualTab = screen.getByRole('tab', { name: 'Submit manual edit' })
    fireEvent.click(manualTab)

    const textarea = screen.getByRole('textbox', { name: 'Manual draft content' })
    fireEvent.change(textarea, { target: { value: '# Chapter 1\n\nDirect manual edit.' } })

    const submitButton = screen.getByRole('button', { name: 'Save manual edit' })
    fireEvent.click(submitButton)

    await waitFor(() => {
      expect(api.resolveChapterProductionAction).toHaveBeenCalledWith(
        ids.project,
        ids.chapter,
        ids.run,
        ids.action,
        {
          decision: 'submit_manual_edit',
          feedback: undefined,
          target_segment_ids: undefined,
          content: '# Chapter 1\n\nDirect manual edit.',
        },
        expect.any(AbortSignal),
      )
    })
  })

  it('triggers review when in review stage not awaiting user', async () => {
    const reviewState = stateFixture({
      status: 'EDITOR_REVIEW',
      current_node: 'editor_review',
      awaiting_user: false,
    })

    vi.spyOn(api, 'getChapterProductionRun').mockResolvedValue(reviewState)
    vi.spyOn(api, 'triggerChapterReview').mockResolvedValue({
      workflow_run_id: ids.run,
      draft_document_id: ids.document,
      draft_version_id: ids.version,
      action_request_id: ids.action,
    })

    render(
      <ChapterProductionV2Workbench
        projectId={ids.project}
        chapterId={ids.chapter}
        initialWorkflowRunId={ids.run}
      />,
    )

    const triggerBtn = await screen.findByRole('button', { name: 'Trigger chapter review' })
    fireEvent.click(triggerBtn)

    await waitFor(() => {
      expect(api.triggerChapterReview).toHaveBeenCalledWith(ids.project, ids.chapter, ids.run, expect.any(AbortSignal))
    })
  })

  it('handles review warning and allows proceed with warnings', async () => {
    const warningState = stateFixture({
      status: 'EDITOR_REVIEW',
      current_node: 'editor_review',
      awaiting_user: true,
      action_request_id: ids.action,
      action_kind: 'review_warning',
      editor_report_id: ids.report,
    })

    vi.spyOn(api, 'getChapterProductionRun').mockResolvedValue(warningState)
    vi.spyOn(api, 'resolveChapterProductionAction').mockResolvedValue({
      workflow_run_id: ids.run,
      draft_document_id: ids.document,
      draft_version_id: ids.version,
      action_request_id: null,
    })

    render(
      <ChapterProductionV2Workbench
        projectId={ids.project}
        chapterId={ids.chapter}
        initialWorkflowRunId={ids.run}
      />,
    )

    expect(await screen.findByText(/Review warnings found:/)).toBeInTheDocument()

    const proceedBtn = screen.getByRole('button', { name: 'Confirm proceed with warnings' })
    fireEvent.click(proceedBtn)

    await waitFor(() => {
      expect(api.resolveChapterProductionAction).toHaveBeenCalledWith(
        ids.project,
        ids.chapter,
        ids.run,
        ids.action,
        {
          decision: 'proceed_with_warnings',
          report_ids: undefined,
          target_segment_ids: undefined,
        },
        expect.any(AbortSignal),
      )
    })
  })

  it('handles blocking review findings and exposes request review revision without finalize', async () => {
    const blockingState = stateFixture({
      status: 'CHIEF_FINAL_REVIEW',
      current_node: 'chief_final_review',
      awaiting_user: true,
      action_request_id: ids.action,
      action_kind: 'review_revision',
      chief_editor_report_id: ids.report,
    })

    vi.spyOn(api, 'getChapterProductionRun').mockResolvedValue(blockingState)
    vi.spyOn(api, 'resolveChapterProductionAction').mockResolvedValue({
      workflow_run_id: ids.run,
      draft_document_id: ids.document,
      draft_version_id: ids.version,
      action_request_id: null,
    })

    render(
      <ChapterProductionV2Workbench
        projectId={ids.project}
        chapterId={ids.chapter}
        initialWorkflowRunId={ids.run}
      />,
    )

    expect(await screen.findByText(/Blocking review findings detected:/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Finalize chapter' })).not.toBeInTheDocument()

    const submitBtn = screen.getByRole('button', { name: 'Request review revision' })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(api.resolveChapterProductionAction).toHaveBeenCalledWith(
        ids.project,
        ids.chapter,
        ids.run,
        ids.action,
        {
          decision: 'request_review_revision',
          report_ids: [ids.report],
          target_segment_ids: [ids.version],
        },
        expect.any(AbortSignal),
      )
    })
  })

  it('finalizes chapter when in REVISION_READY state', async () => {
    const readyState = stateFixture({
      status: 'REVISION_READY',
      current_node: 'REVISION_READY',
      awaiting_user: false,
    })

    vi.spyOn(api, 'getChapterProductionRun').mockResolvedValue(readyState)
    vi.spyOn(api, 'finalizeChapterProduction').mockResolvedValue({
      workflow_run_id: ids.run,
      final_document_id: ids.finalDoc,
      final_version_id: ids.finalVer,
    })

    render(
      <ChapterProductionV2Workbench
        projectId={ids.project}
        chapterId={ids.chapter}
        initialWorkflowRunId={ids.run}
      />,
    )

    expect(await screen.findByRole('region', { name: 'Revision ready for finalization' })).toBeInTheDocument()

    const finalizeBtn = screen.getByRole('button', { name: 'Finalize chapter' })
    fireEvent.click(finalizeBtn)

    await waitFor(() => {
      expect(api.finalizeChapterProduction).toHaveBeenCalledWith(ids.project, ids.chapter, ids.run, expect.any(AbortSignal))
    })
  })

  it('renders completed state with final document info', async () => {
    const completedState = stateFixture({
      status: 'COMPLETED',
      current_node: 'completed',
      document_id: ids.finalDoc,
      document_version_id: ids.finalVer,
    })

    vi.spyOn(api, 'getChapterProductionRun').mockResolvedValue(completedState)

    render(
      <ChapterProductionV2Workbench
        projectId={ids.project}
        chapterId={ids.chapter}
        initialWorkflowRunId={ids.run}
      />,
    )

    expect(await screen.findByRole('region', { name: 'Chapter production completed' })).toBeInTheDocument()
    expect(screen.getByText(ids.finalDoc)).toBeInTheDocument()
    expect(screen.getAllByText(ids.finalVer).length).toBeGreaterThan(0)
  })

  it('handles FAILED state with reconciliation and resume buttons', async () => {
    const failedState = stateFixture({
      status: 'FAILED',
      current_node: 'failed',
      failure_code: 'reconciliation_required',
      failed_from_status: 'DRAFTING',
    })

    vi.spyOn(api, 'getChapterProductionRun').mockResolvedValue(failedState)
    vi.spyOn(api, 'reconcileChapterProduction').mockResolvedValue(
      stateFixture({ status: 'AUTHOR_REVISION', awaiting_user: true }),
    )

    render(
      <ChapterProductionV2Workbench
        projectId={ids.project}
        chapterId={ids.chapter}
        initialWorkflowRunId={ids.run}
      />,
    )

    expect(await screen.findByRole('alert', { name: 'Chapter production encountered an issue' })).toBeInTheDocument()
    expect(screen.getByText(/State reconciliation is required before chapter production can continue\./)).toBeInTheDocument()

    const reconcileBtn = screen.getByRole('button', { name: 'Reconcile state' })
    fireEvent.click(reconcileBtn)

    await waitFor(() => {
      expect(api.reconcileChapterProduction).toHaveBeenCalledWith(ids.project, ids.chapter, ids.run, expect.any(AbortSignal))
    })
  })

  it('handles provider timeout with resume drafting button', async () => {
    const failedState = stateFixture({
      status: 'FAILED',
      current_node: 'failed',
      failure_code: 'provider_timeout',
      failed_from_status: 'DRAFTING',
    })

    vi.spyOn(api, 'getChapterProductionRun').mockResolvedValue(failedState)
    vi.spyOn(api, 'resumeChapterProduction').mockResolvedValue({
      workflow_run_id: ids.run,
      action_request_id: ids.action,
      outline_document_id: ids.document,
      outline_version_id: ids.version,
      draft_document_id: ids.finalDoc,
      draft_version_id: ids.finalVer,
    })

    render(
      <ChapterProductionV2Workbench
        projectId={ids.project}
        chapterId={ids.chapter}
        initialWorkflowRunId={ids.run}
      />,
    )

    expect(await screen.findByRole('alert', { name: 'Chapter production encountered an issue' })).toBeInTheDocument()
    expect(screen.getByText(/AI drafting provider timed out\. You may resume drafting\./)).toBeInTheDocument()

    const resumeBtn = screen.getByRole('button', { name: 'Resume drafting' })
    fireEvent.click(resumeBtn)

    await waitFor(() => {
      expect(api.resumeChapterProduction).toHaveBeenCalledWith(ids.project, ids.chapter, ids.run, expect.any(AbortSignal))
    })
  })
})
