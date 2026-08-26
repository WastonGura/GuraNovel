import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api/readerPanelClient'
import {
  ReaderPanelWorkbench,
  readerPanelStatusLabel,
  type ReaderPanelWorkbenchProps,
} from './ReaderPanelWorkbench'

const ids = {
  project: '11111111-1111-4111-8111-111111111111',
  chapter: '22222222-2222-4222-8222-222222222222',
  document: '33333333-3333-4333-8333-333333333333',
  version: '44444444-4444-4444-8444-444444444444',
  session: '55555555-5555-4555-8555-555555555555',
  otherSession: '66666666-6666-4666-8666-666666666666',
  workflow: '77777777-7777-4777-8777-777777777777',
}

const hash = 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789'

const identity: ReaderPanelWorkbenchProps = {
  projectId: ids.project,
  chapterId: ids.chapter,
  documentId: ids.document,
  documentVersionId: ids.version,
}

function session(overrides: Partial<api.ReaderPanelSessionDetail> = {}): api.ReaderPanelSessionDetail {
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
    issue_count: 0,
    initial_ballot_count: 0,
    final_ballot_count: 0,
    discussion_message_count: 0,
    created_at: '2026-08-26T01:00:00Z',
    updated_at: '2026-08-26T01:02:00Z',
    completed_at: '2026-08-26T01:02:00Z',
    review_report: null,
    issues: [],
    initial_reports: null,
    transcript: null,
    permitted_operations: [],
    ...overrides,
  }
}

function noop(): api.ReaderPanelNoOpDetail {
  return {
    is_noop: true,
    session_id: null,
    workflow_run_id: null,
    project_id: ids.project,
    chapter_id: ids.chapter,
    document_id: ids.document,
    document_version_id: ids.version,
    source_hash: null,
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
    created_at: null,
    updated_at: null,
    completed_at: null,
    review_report: null,
    issues: [],
    initial_reports: null,
    transcript: null,
    permitted_operations: [],
  }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ReaderPanelWorkbench', () => {
  it('maps every lifecycle status to a human label without hiding ballot or discussion phases', () => {
    const expected: Record<api.ReaderPanelStatus, string> = {
      created: 'Initializing',
      preparing: 'Preparing readers',
      independent_reading: 'Independent reading',
      initial_reports_locked: 'Initial reports locked',
      issue_extraction: 'Extracting issues',
      initial_balloting: 'Initial balloting',
      initial_ballots_locked: 'Initial ballots locked',
      discussing: 'Panel discussion',
      final_balloting: 'Final balloting',
      final_ballots_locked: 'Final ballots locked',
      report_generating: 'Generating report',
      completed: 'Completed',
      degraded_completed: 'Completed with degraded coverage',
      failed: 'Failed',
      cancelled: 'Cancelled',
      off: 'Off — no panel run',
    }

    for (const [status, label] of Object.entries(expected)) {
      expect(readerPanelStatusLabel(status as api.ReaderPanelStatus)).toBe(label)
    }
  })

  it('starts once with bounded native controls and navigates only to the returned scoped session', async () => {
    let resolveStart!: (value: api.ReaderPanelDetail) => void
    vi.spyOn(api, 'startReaderPanel').mockReturnValue(new Promise((resolve) => { resolveStart = resolve }))
    const onSessionStarted = vi.fn()
    render(<ReaderPanelWorkbench {...identity} onSessionStarted={onSessionStarted} />)

    expect(screen.getByRole('region', { name: 'Reader Panel' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Panel mode'), { target: { value: 'quick' } })
    fireEvent.change(screen.getByLabelText('Maximum ballot issues'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('Maximum discussion issues'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('Maximum rounds per issue'), { target: { value: '1' } })
    fireEvent.change(screen.getByLabelText('Minimum valid readers'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('Test goals'), { target: { value: 'Opening clarity\nPacing' } })
    fireEvent.change(screen.getByLabelText('Target audience'), { target: { value: 'Mystery readers' } })
    const start = screen.getByRole('button', { name: 'Start Reader Panel' })
    fireEvent.click(start)
    fireEvent.click(start)

    expect(api.startReaderPanel).toHaveBeenCalledTimes(1)
    expect(api.startReaderPanel).toHaveBeenCalledWith(ids.project, ids.chapter, {
      document_id: ids.document,
      document_version_id: ids.version,
      mode: 'quick',
      config_overrides: {
        max_ballot_issues: 3,
        max_discussion_issues: 2,
        max_rounds_per_issue: 1,
        min_valid_readers: 2,
      },
      test_goals: ['Opening clarity', 'Pacing'],
      target_audience: ['Mystery readers'],
    }, expect.any(AbortSignal))
    expect(start).toBeDisabled()

    resolveStart(session())
    await waitFor(() => expect(onSessionStarted).toHaveBeenCalledWith(ids.session))
  })

  it('loads the exact bounded preset whenever the panel mode changes', () => {
    render(<ReaderPanelWorkbench {...identity} />)

    expect(screen.getByLabelText('Maximum ballot issues')).toHaveValue(6)
    expect(screen.getByLabelText('Maximum discussion issues')).toHaveValue(4)
    expect(screen.getByLabelText('Maximum rounds per issue')).toHaveValue(2)
    expect(screen.getByLabelText('Minimum valid readers')).toHaveValue(3)

    fireEvent.change(screen.getByLabelText('Panel mode'), { target: { value: 'quick' } })
    expect(screen.getByLabelText('Maximum ballot issues')).toHaveValue(3)
    expect(screen.getByLabelText('Maximum discussion issues')).toHaveValue(2)
    expect(screen.getByLabelText('Maximum rounds per issue')).toHaveValue(1)
    expect(screen.getByLabelText('Minimum valid readers')).toHaveValue(2)

    fireEvent.change(screen.getByLabelText('Panel mode'), { target: { value: 'panel' } })
    expect(screen.getByLabelText('Maximum ballot issues')).toHaveValue(8)
    expect(screen.getByLabelText('Maximum discussion issues')).toHaveValue(6)
    expect(screen.getByLabelText('Maximum rounds per issue')).toHaveValue(3)
    expect(screen.getByLabelText('Minimum valid readers')).toHaveValue(4)
  })

  it('rejects invalid non-off overrides programmatically with a fixed safe error', () => {
    const startSpy = vi.spyOn(api, 'startReaderPanel')
    render(<ReaderPanelWorkbench {...identity} />)
    fireEvent.change(screen.getByLabelText('Panel mode'), { target: { value: 'quick' } })
    fireEvent.change(screen.getByLabelText('Maximum ballot issues'), { target: { value: '2.5' } })
    fireEvent.change(screen.getByLabelText('Maximum discussion issues'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('Minimum valid readers'), { target: { value: '3' } })

    fireEvent.submit(screen.getByRole('button', { name: 'Start Reader Panel' }).closest('form')!)

    expect(startSpy).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Reader Panel settings are invalid. Check the allowed ranges for this mode.')
  })

  it('shows an explicit off no-op and does not create a session route', async () => {
    vi.spyOn(api, 'startReaderPanel').mockResolvedValue(noop())
    const onSessionStarted = vi.fn()
    render(<ReaderPanelWorkbench {...identity} onSessionStarted={onSessionStarted} />)

    fireEvent.change(screen.getByLabelText('Panel mode'), { target: { value: 'off' } })
    fireEvent.change(screen.getByLabelText('Maximum ballot issues'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Minimum valid readers'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Skip Reader Panel' }))

    expect(api.startReaderPanel).toHaveBeenCalledWith(ids.project, ids.chapter, {
      document_id: ids.document,
      document_version_id: ids.version,
      mode: 'off',
    }, expect.any(AbortSignal))
    expect(await screen.findByRole('status')).toHaveTextContent('Reader Panel is off. No session was created')
    expect(onSessionStarted).not.toHaveBeenCalled()
  })

  it('loads a deep-linked session, renders report handoff, classifications, risks, and evidence', async () => {
    vi.spyOn(api, 'getReaderPanel').mockResolvedValue(session({
      review_report: {
        summary: 'The opening lands, but the clue needs a clearer setup.',
        blocking_issues: [{ issue_number: 1, title: 'Clue is missed' }],
        warnings: ['Keep the reveal timing.'],
        notes: ['Check the target audience response.'],
        suggested_actions: [{
          priority: 'experiment',
          target_segment_ids: ['scene_2'],
          suggested_action: 'clarify',
          instruction: 'Test a more concrete clue.',
        }],
      },
      initial_reports: [{
        overall_reaction: 'The reveal is compelling.',
        continue_reading: 'yes',
        confidence: 'high',
        strengths: [],
        reactions: [],
        concerns: [],
      }],
      issues: [{
        issue_number: 1,
        title: 'Clue is missed',
        category: 'comprehension',
        symptom: 'Readers did not connect the object to the reveal.',
        root_cause_hypotheses: ['The setup is too subtle.'],
        evidence: [{ segment_ids: ['scene_2'], note: 'Readers skipped this clue.' }],
        target_audience_relevance: 'high',
        minority_risk: true,
        discussion_status: 'closed',
        consensus_class: 'polarized',
        recommended_priority: 'manual_review',
      }],
    }))

    render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)

    expect(await screen.findByRole('heading', { name: 'Reader Panel report' })).toBeInTheDocument()
    expect(screen.getByText('All readers')).toBeInTheDocument()
    expect(screen.getByText(/Polarized/)).toBeInTheDocument()
    expect(screen.getByText('Manual Review')).toBeInTheDocument()
    expect(screen.getByText('Target audience')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Minority high-risk signal')
    expect(screen.getByRole('list', { name: 'Evidence for issue 1' })).toHaveTextContent('scene_2')
    expect(screen.getByText('Discussion status').parentElement).toHaveTextContent('Closed')
    expect(screen.getByText(/Unresolved disagreement/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Initial reader sample' })).toBeInTheDocument()
    expect(screen.getByText('The reveal is compelling.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Editor handoff' })).toHaveTextContent('Test a more concrete clue.')
    expect(screen.getByText(/Diagnostic recommendations are not approved edits/)).toBeInTheDocument()
    expect(screen.getByText(/Source version/).parentElement).toHaveTextContent(ids.version)
    expect(screen.getByText(/Source hash/).parentElement).toHaveTextContent(hash)
    expect(screen.getByText('Readers').parentElement).toHaveTextContent('4 of 4 completed')
  })

  it('keeps stale and degraded results visibly distinct from ordinary completion', async () => {
    vi.spyOn(api, 'getReaderPanel').mockResolvedValue(session({
      status: 'degraded_completed',
      stale: true,
      degradation_reason: 'Reader quorum completed with one failed reader.',
      planned_readers: 4,
      completed_readers: 3,
      failed_readers: 1,
    }))

    render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)

    expect(await screen.findByText('Completed with degraded coverage')).toBeInTheDocument()
    const alerts = screen.getAllByRole('alert')
    expect(alerts.some((alert) => alert.textContent?.includes('outdated source version'))).toBe(true)
    expect(alerts.some((alert) => alert.textContent?.includes('Degraded result'))).toBe(true)
    expect(alerts.some((alert) => alert.textContent?.includes('Reader quorum completed with one failed reader.'))).toBe(true)
  })

  it('fails closed on route scope mismatch and never presents returned data', async () => {
    vi.spyOn(api, 'getReaderPanel').mockResolvedValue(session({
      document_version_id: '88888888-8888-4888-8888-888888888888',
      review_report: {
        summary: 'PRIVATE WRONG VERSION REPORT', blocking_issues: [], warnings: [], notes: [], suggested_actions: [],
      },
    }))

    render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Reader Panel data could not be verified for this document version.')
    expect(screen.queryByText('PRIVATE WRONG VERSION REPORT')).not.toBeInTheDocument()
  })

  it('offers cancel and resume only when permitted and submits each action once', async () => {
    vi.spyOn(api, 'getReaderPanel')
      .mockResolvedValueOnce(session({ status: 'failed', permitted_operations: ['resume'] }))
      .mockResolvedValue(session({ status: 'created', permitted_operations: ['cancel'] }))
    let resolveResume!: (value: api.ReaderPanelDetail) => void
    vi.spyOn(api, 'resumeReaderPanel').mockReturnValue(new Promise((resolve) => { resolveResume = resolve }))
    vi.spyOn(api, 'cancelReaderPanel').mockResolvedValue(session({ status: 'cancelled' }))
    render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)

    const resume = await screen.findByRole('button', { name: 'Resume Reader Panel' })
    expect(screen.queryByRole('button', { name: 'Cancel Reader Panel' })).not.toBeInTheDocument()
    fireEvent.click(resume)
    fireEvent.click(resume)
    expect(api.resumeReaderPanel).toHaveBeenCalledTimes(1)
    expect(api.cancelReaderPanel).not.toHaveBeenCalled()
    expect(resume).toBeDisabled()

    resolveResume(session({ status: 'created', permitted_operations: ['cancel'] }))
    const cancel = await screen.findByRole('button', { name: 'Cancel Reader Panel' })
    fireEvent.click(cancel)
    fireEvent.click(cancel)
    expect(api.cancelReaderPanel).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Cancelled')).toBeInTheDocument()
  })

  it('cancels current polling before an action and restarts polling after resume success', async () => {
    const cancelPoll = vi.spyOn(api.ReaderPanelPoller.prototype, 'cancel')
    vi.spyOn(api, 'getReaderPanel')
      .mockResolvedValueOnce(session({ status: 'failed', permitted_operations: ['resume'] }))
      .mockResolvedValue(session({ status: 'created', permitted_operations: ['cancel'] }))
    vi.spyOn(api, 'resumeReaderPanel').mockResolvedValue(session({ status: 'created', permitted_operations: ['cancel'] }))
    render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Resume Reader Panel' }))

    await waitFor(() => expect(api.getReaderPanel).toHaveBeenCalledTimes(2))
    expect(cancelPoll).toHaveBeenCalled()
    expect(cancelPoll.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.resumeReaderPanel).mock.invocationCallOrder[0])
  })

  it('does not let a deferred poll response overwrite a successful cancel action', async () => {
    let resolveSecondGet!: (value: api.ReaderPanelDetail) => void
    vi.spyOn(api, 'getReaderPanel')
      .mockResolvedValueOnce(session({ status: 'created', permitted_operations: ['cancel'] }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecondGet = resolve }))
    vi.spyOn(api, 'cancelReaderPanel').mockResolvedValue(session({
      status: 'cancelled',
      permitted_operations: [],
    }))
    render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)

    const cancel = await screen.findByRole('button', { name: 'Cancel Reader Panel' })
    await waitFor(() => expect(api.getReaderPanel).toHaveBeenCalledTimes(2), { timeout: 2500 })
    fireEvent.click(cancel)
    expect(await screen.findByText('Cancelled')).toBeInTheDocument()

    resolveSecondGet(session({ status: 'created', permitted_operations: ['cancel'] }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Cancel Reader Panel' })).not.toBeInTheDocument())
    expect(screen.getByText('Cancelled')).toBeInTheDocument()
  })

  it('restores polling after an action failure and keeps the error fixed and safe', async () => {
    vi.spyOn(api, 'getReaderPanel').mockResolvedValue(session({ status: 'failed', permitted_operations: ['resume'] }))
    vi.spyOn(api, 'resumeReaderPanel').mockRejectedValue(new Error('credential /private/provider/path'))
    render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Resume Reader Panel' }))

    expect(await screen.findByText('Reader Panel request failed. Try again.')).toBeInTheDocument()
    await waitFor(() => expect(api.getReaderPanel).toHaveBeenCalledTimes(2))
    expect(screen.queryByText(/credential|private\/provider/)).not.toBeInTheDocument()
  })

  it('makes a max-attempts polling pause explicitly recoverable', async () => {
    const poll = vi.spyOn(api.ReaderPanelPoller.prototype, 'poll').mockResolvedValue('max_attempts')
    render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)

    const continueButton = await screen.findByRole('button', { name: 'Continue monitoring' })
    expect(screen.getByRole('status')).toHaveTextContent('Monitoring paused after the bounded polling window.')
    fireEvent.click(continueButton)
    await waitFor(() => expect(poll).toHaveBeenCalledTimes(2))
  })

  it('clears a paused-monitoring prompt when a successful cancel reaches a terminal state', async () => {
    vi.spyOn(api.ReaderPanelPoller.prototype, 'poll').mockImplementation(async (_identity, options) => {
      options.onUpdate(session({ status: 'created', permitted_operations: ['cancel'] }))
      return 'max_attempts'
    })
    vi.spyOn(api, 'cancelReaderPanel').mockResolvedValue(session({
      status: 'cancelled',
      permitted_operations: [],
    }))
    render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)

    const continueButton = await screen.findByRole('button', { name: 'Continue monitoring' })
    expect(continueButton).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel Reader Panel' }))

    expect(await screen.findByText('Cancelled')).toBeInTheDocument()
    expect(screen.queryByText('Monitoring paused after the bounded polling window.')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Continue monitoring' })).not.toBeInTheDocument()
  })

  it('shows a decoded failure reason without exposing thrown request errors', async () => {
    vi.spyOn(api, 'getReaderPanel').mockResolvedValue(session({
      status: 'failed',
      failure_reason: 'Reader quorum was not reached.',
    }))
    render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Reader quorum was not reached.')
  })

  it('drops late responses after route identity changes and after unmount', async () => {
    let resolveFirst!: (value: api.ReaderPanelDetail) => void
    let resolveUnmounted!: (value: api.ReaderPanelDetail) => void
    vi.spyOn(api, 'getReaderPanel').mockImplementation((_project, _chapter, sessionId) => {
      if (sessionId === ids.session) return new Promise((resolve) => { resolveFirst = resolve })
      if (sessionId === ids.otherSession) return Promise.resolve(session({ session_id: ids.otherSession, status: 'cancelled' }))
      return new Promise((resolve) => { resolveUnmounted = resolve })
    })

    const view = render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)
    view.rerender(<ReaderPanelWorkbench {...identity} sessionId={ids.otherSession} />)
    expect(await screen.findByText('Cancelled')).toBeInTheDocument()
    resolveFirst(session({ status: 'completed', review_report: {
      summary: 'LATE ROUTE REPORT', blocking_issues: [], warnings: [], notes: [], suggested_actions: [],
    } }))
    await waitFor(() => expect(screen.queryByText('LATE ROUTE REPORT')).not.toBeInTheDocument())

    view.rerender(<ReaderPanelWorkbench {...identity} sessionId="99999999-9999-4999-8999-999999999999" />)
    view.unmount()
    resolveUnmounted(session({ session_id: '99999999-9999-4999-8999-999999999999' }))
    await Promise.resolve()
  })

  it('uses fixed safe errors and keeps transcripts hidden by default', async () => {
    const view = render(<ReaderPanelWorkbench {...identity} />)
    vi.spyOn(api, 'startReaderPanel').mockRejectedValue(new Error('provider https://secret.example /private/key credential'))
    fireEvent.click(screen.getByRole('button', { name: 'Start Reader Panel' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Reader Panel request failed. Try again.')
    expect(screen.queryByText(/secret\.example|private\/key|credential/)).not.toBeInTheDocument()

    view.unmount()
    vi.spyOn(api, 'getReaderPanel').mockResolvedValue(session({
      transcript: [{
        issue_id: ids.workflow,
        round_number: 1,
        turn_number: 1,
        speaker_type: 'reader',
        stance: 'support',
        claim: 'PRIVATE TRANSCRIPT CLAIM',
        evidence: [],
        concession: null,
        proposed_action: null,
        novelty: 'new_evidence',
        created_at: null,
      }],
    }))
    render(<ReaderPanelWorkbench {...identity} sessionId={ids.session} />)
    await screen.findByText('Completed')
    expect(api.getReaderPanel).toHaveBeenCalledWith(ids.project, ids.chapter, ids.session, {
      include_initial_reports: true,
      include_transcript: false,
    }, expect.any(AbortSignal))
    expect(screen.queryByText('PRIVATE TRANSCRIPT CLAIM')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /transcript/i })).not.toBeInTheDocument()
  })
})
