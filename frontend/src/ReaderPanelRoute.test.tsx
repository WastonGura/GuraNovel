import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import * as documentApi from './api/client'
import * as panelApi from './api/readerPanelClient'

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
  return <output data-testid="location">{useLocation().pathname}</output>
}

function renderRoute(path = startPath) {
  return render(<MemoryRouter initialEntries={[path]}><App /><Location /></MemoryRouter>)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('Reader Panel App route', () => {
  it('preserves exact IDs from start through the session deep link and editor-only report', async () => {
    const write = vi.spyOn(documentApi, 'writeDocument')
    vi.spyOn(panelApi, 'startReaderPanel').mockResolvedValue(session({
      status: 'independent_reading',
      completed_readers: 0,
      issue_count: 0,
      initial_ballot_count: 0,
      final_ballot_count: 0,
      discussion_message_count: 0,
      completed_at: null,
      review_report: null,
      issues: [],
      permitted_operations: ['cancel'],
    }))
    vi.spyOn(panelApi, 'getReaderPanel').mockResolvedValue(session())

    renderRoute()
    fireEvent.click(screen.getByRole('button', { name: 'Start Reader Panel' }))

    expect(panelApi.startReaderPanel).toHaveBeenCalledWith(
      ids.project,
      ids.chapter,
      {
        document_id: ids.document,
        document_version_id: ids.version,
        mode: 'standard',
        config_overrides: {
          max_ballot_issues: 6,
          max_discussion_issues: 4,
          max_rounds_per_issue: 2,
          min_valid_readers: 3,
        },
        test_goals: [],
        target_audience: [],
      },
      expect.any(AbortSignal),
    )
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(
      `${startPath}/${ids.session}`,
    ))
    expect(await screen.findByRole('heading', { name: 'Reader Panel report' })).toBeInTheDocument()
    expect(screen.getByText(ids.version)).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'All readers summary for issue 1' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Target audience summary for issue 1' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Editor handoff' })).toHaveTextContent(
      'Keep the opening, but clarify the transition for editor review.',
    )
    expect(screen.getByText(/results never modify the chapter automatically/i)).toBeInTheDocument()
    expect(write).not.toHaveBeenCalled()
  })

  it('fails closed on a deep-linked scope mismatch without rendering or applying the report', async () => {
    const write = vi.spyOn(documentApi, 'writeDocument')
    vi.spyOn(panelApi, 'getReaderPanel').mockResolvedValue(session({
      document_version_id: '77777777-7777-4777-8777-777777777777',
      review_report: {
        summary: 'PRIVATE WRONG-SCOPE REPORT',
        blocking_issues: [], warnings: [], notes: [], suggested_actions: [],
      },
    }))

    renderRoute(`${startPath}/${ids.session}`)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Reader Panel data could not be verified for this document version.',
    )
    expect(screen.queryByText('PRIVATE WRONG-SCOPE REPORT')).not.toBeInTheDocument()
    expect(write).not.toHaveBeenCalled()
  })
})
