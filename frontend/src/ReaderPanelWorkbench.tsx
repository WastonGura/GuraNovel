import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import {
  ReaderPanelPoller,
  cancelReaderPanel,
  getReaderPanel,
  resumeReaderPanel,
  startReaderPanel,
  type EditorialDecision,
  type PanelMode,
  type ReaderPanelDetail,
  type ReaderPanelIssue,
  type ReaderPanelSessionDetail,
  type ReaderPanelStartPayload,
  type ReaderPanelStatus,
} from './api/readerPanelClient'

export interface ReaderPanelWorkbenchProps {
  projectId: string
  chapterId: string
  documentId: string
  documentVersionId: string
  sessionId?: string
  onSessionStarted?: (sessionId: string) => void
}

// Exported for the exhaustive lifecycle-label contract test.
// eslint-disable-next-line react-refresh/only-export-components
export function readerPanelStatusLabel(status: ReaderPanelStatus): string {
  switch (status) {
    case 'created': return 'Initializing'
    case 'preparing': return 'Preparing readers'
    case 'independent_reading': return 'Independent reading'
    case 'initial_reports_locked': return 'Initial reports locked'
    case 'issue_extraction': return 'Extracting issues'
    case 'initial_balloting': return 'Initial balloting'
    case 'initial_ballots_locked': return 'Initial ballots locked'
    case 'discussing': return 'Panel discussion'
    case 'final_balloting': return 'Final balloting'
    case 'final_ballots_locked': return 'Final ballots locked'
    case 'report_generating': return 'Generating report'
    case 'completed': return 'Completed'
    case 'degraded_completed': return 'Completed with degraded coverage'
    case 'failed': return 'Failed'
    case 'cancelled': return 'Cancelled'
    case 'off': return 'Off — no panel run'
  }
}

const safeRequestError = 'Reader Panel request failed. Try again.'
const scopeError = 'Reader Panel data could not be verified for this document version.'

function sameId(left: string, right: string): boolean {
  return left.toLowerCase() === right.toLowerCase()
}

function matchesScope(
  panel: ReaderPanelDetail,
  identity: ReaderPanelWorkbenchProps,
  expectedSessionId?: string,
): boolean {
  return sameId(panel.project_id, identity.projectId)
    && sameId(panel.chapter_id, identity.chapterId)
    && sameId(panel.document_id, identity.documentId)
    && sameId(panel.document_version_id, identity.documentVersionId)
    && (!expectedSessionId || (!panel.is_noop && sameId(panel.session_id, expectedSessionId)))
}

function lineValues(value: string): string[] | null {
  const lines = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
  return lines.length <= 16 && lines.every((line) => line.length <= 256) ? lines : null
}

function isPanelMode(value: string): value is PanelMode {
  return value === 'off' || value === 'quick' || value === 'standard' || value === 'panel'
}

const modePresets = {
  quick: { maxBallotIssues: 3, maxDiscussionIssues: 2, maxRounds: 1, minReaders: 2, readerCount: 2 },
  standard: { maxBallotIssues: 6, maxDiscussionIssues: 4, maxRounds: 2, minReaders: 3, readerCount: 4 },
  panel: { maxBallotIssues: 8, maxDiscussionIssues: 6, maxRounds: 3, minReaders: 4, readerCount: 6 },
} as const

function validSettings(
  mode: PanelMode,
  maxBallotIssues: number,
  maxDiscussionIssues: number,
  maxRounds: number,
  minReaders: number,
): boolean {
  if (mode === 'off') return true
  return [maxBallotIssues, maxDiscussionIssues, maxRounds, minReaders].every(Number.isInteger)
    && maxBallotIssues >= 1 && maxBallotIssues <= 8
    && maxDiscussionIssues >= 0 && maxDiscussionIssues <= 6
    && maxRounds >= 0 && maxRounds <= 3
    && minReaders >= 1 && minReaders <= 6
    && maxDiscussionIssues <= maxBallotIssues
    && minReaders <= modePresets[mode].readerCount
}

function humanize(value: string): string {
  return value.split('_').map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(' ')
}

function priorityLabel(value: EditorialDecision | null): string {
  return value ? humanize(value) : 'Not classified'
}

function FocusedAlert({ children }: { children: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => { ref.current?.focus() }, [children])
  return <div className="reader-panel-alert danger" role="alert" tabIndex={-1} ref={ref}>{children}</div>
}

function Evidence({ issue }: { issue: ReaderPanelIssue }) {
  return <ul className="reader-panel-evidence" aria-label={`Evidence for issue ${issue.issue_number}`}>
    {issue.evidence.map((evidence, index) => <li key={`${issue.issue_number}-${index}`}>
      <strong>Segments:</strong> {evidence.segment_ids.join(', ')} — {evidence.note}
    </li>)}
  </ul>
}

function IssueSummary({ issue }: { issue: ReaderPanelIssue }) {
  const unresolved = issue.consensus_class === null
    || issue.consensus_class === 'inconclusive'
    || issue.consensus_class === 'polarized'
  return <article className="reader-panel-issue">
    <h4>{issue.issue_number}. {issue.title}</h4>
    <p><strong>{humanize(issue.category)}:</strong> {issue.symptom}</p>
    <ul>{issue.root_cause_hypotheses.map((hypothesis) => <li key={hypothesis}>{hypothesis}</li>)}</ul>
    <div className="reader-panel-summaries">
      <section aria-label={`All readers summary for issue ${issue.issue_number}`}>
        <h5>All readers</h5>
        <dl>
          <div><dt>Consensus</dt><dd>{issue.consensus_class ? humanize(issue.consensus_class) : 'Not reached'}</dd></div>
          <div><dt>Priority</dt><dd>{priorityLabel(issue.recommended_priority)}</dd></div>
          <div><dt>Discussion status</dt><dd>{humanize(issue.discussion_status)}</dd></div>
        </dl>
      </section>
      <section aria-label={`Target audience summary for issue ${issue.issue_number}`}>
        <h5>Target audience</h5>
        <dl>
          <div><dt>Relevance</dt><dd>{humanize(issue.target_audience_relevance)}</dd></div>
          <div><dt>Minority risk</dt><dd>{issue.minority_risk ? 'High-risk signal' : 'No high-risk signal'}</dd></div>
        </dl>
      </section>
    </div>
    {issue.minority_risk && <div className="reader-panel-alert danger" role="alert">Minority high-risk signal — keep visible during editorial review.</div>}
    {unresolved && <p className="reader-panel-warning"><strong>Unresolved disagreement:</strong> this issue remains {issue.consensus_class ? humanize(issue.consensus_class).toLowerCase() : 'inconclusive'}.</p>}
    <Evidence issue={issue} />
  </article>
}

function InitialReaderSample({ panel }: { panel: ReaderPanelSessionDetail }) {
  if (!panel.initial_reports || panel.initial_reports.length === 0) return null
  return <section className="reader-panel-sample" aria-labelledby="reader-sample-title">
    <h4 id="reader-sample-title">Initial reader sample</h4>
    <ul>
      {panel.initial_reports.map((report, index) => <li key={index}>
        <p>{report.overall_reaction}</p>
        <p><strong>Continue reading:</strong> {humanize(report.continue_reading)} · <strong>Confidence:</strong> {humanize(report.confidence)}</p>
      </li>)}
    </ul>
  </section>
}

function Report({ panel }: { panel: ReaderPanelSessionDetail }) {
  return <section className="reader-panel-report" aria-labelledby="reader-panel-report-title">
    <h3 id="reader-panel-report-title">Reader Panel report</h3>
    <p className="reader-panel-warning"><strong>Diagnostic recommendations are not approved edits.</strong> Reader Panel results never modify the chapter automatically.</p>
    <dl className="reader-panel-metadata">
      <div><dt>Readers</dt><dd>{panel.completed_readers} of {panel.planned_readers} completed · {panel.failed_readers} failed</dd></div>
      <div><dt>Mode</dt><dd>{humanize(panel.mode)}</dd></div>
      <div><dt>Source version</dt><dd><code>{panel.document_version_id}</code></dd></div>
      <div><dt>Source hash</dt><dd><code>{panel.source_hash ?? 'Unavailable'}</code></dd></div>
      <div><dt>Issues</dt><dd>{panel.issue_count}</dd></div>
      <div><dt>Ballots</dt><dd>{panel.initial_ballot_count} initial · {panel.final_ballot_count} final</dd></div>
    </dl>
    {panel.issues.length === 0
      ? <p className="muted">No notable reader issues were reported.</p>
      : <div className="reader-panel-issues">{panel.issues.map((issue) => <IssueSummary key={issue.issue_number} issue={issue} />)}</div>}
    <InitialReaderSample panel={panel} />
    {panel.review_report && <section className="reader-panel-handoff" role="region" aria-label="Editor handoff">
      <h4>Editor handoff</h4>
      <p>{panel.review_report.summary}</p>
      {panel.review_report.blocking_issues.length > 0 && <><h5>Blocking issues</h5><ul>{panel.review_report.blocking_issues.map((issue) => <li key={issue.issue_number}>{issue.issue_number}. {issue.title}</li>)}</ul></>}
      {panel.review_report.warnings.length > 0 && <><h5>Warnings</h5><ul>{panel.review_report.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></>}
      {panel.review_report.notes.length > 0 && <><h5>Notes</h5><ul>{panel.review_report.notes.map((note) => <li key={note}>{note}</li>)}</ul></>}
      {panel.review_report.suggested_actions.length > 0 && <><h5>Suggested actions for editor review</h5><ul>{panel.review_report.suggested_actions.map((action, index) => <li key={`${action.priority}-${index}`}>
        <strong>{priorityLabel(action.priority)} · {humanize(action.suggested_action)}</strong>: {action.instruction} (segments: {action.target_segment_ids.join(', ')})
      </li>)}</ul></>}
    </section>}
  </section>
}

export function ReaderPanelWorkbench(props: ReaderPanelWorkbenchProps) {
  const { projectId, chapterId, documentId, documentVersionId, sessionId, onSessionStarted } = props
  const [mode, setMode] = useState<PanelMode>('standard')
  const [maxBallotIssues, setMaxBallotIssues] = useState(6)
  const [maxDiscussionIssues, setMaxDiscussionIssues] = useState(4)
  const [maxRounds, setMaxRounds] = useState(2)
  const [minReaders, setMinReaders] = useState(3)
  const [testGoals, setTestGoals] = useState('')
  const [targetAudience, setTargetAudience] = useState('')
  const [panel, setPanel] = useState<ReaderPanelSessionDetail | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [monitoringPaused, setMonitoringPaused] = useState(false)
  const [pollRevision, setPollRevision] = useState(0)
  const mountedRef = useRef(false)
  const submittingRef = useRef(false)
  const controllerRef = useRef<AbortController | null>(null)
  const pollerRef = useRef<ReaderPanelPoller | null>(null)
  if (pollerRef.current === null) pollerRef.current = new ReaderPanelPoller(getReaderPanel)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      controllerRef.current?.abort()
      pollerRef.current?.cancel()
    }
  }, [])

  useEffect(() => {
    const poller = pollerRef.current
    if (!sessionId || !poller) return
    let active = true
    setPanel(null)
    setNotice(null)
    void poller.poll(
      { projectId, chapterId, sessionId },
      {
        maxAttempts: 100,
        intervalMs: 1500,
        includeInitialReports: true,
        includeTranscript: false,
        onUpdate: (nextPanel) => {
          if (!active) return
          if (!matchesScope(nextPanel, { projectId, chapterId, documentId, documentVersionId }, sessionId)) {
            poller.cancel()
            setPanel(null)
            setError(scopeError)
            return
          }
          setMonitoringPaused(false)
          setPanel(nextPanel)
        },
        onError: () => { if (active) setError(safeRequestError) },
      },
    ).then((result) => {
      if (active && result === 'max_attempts') setMonitoringPaused(true)
    })
    return () => {
      active = false
      poller.cancel()
    }
  }, [chapterId, documentId, documentVersionId, pollRevision, projectId, sessionId])

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submittingRef.current) return
    const goals = mode === 'off' ? [] : lineValues(testGoals)
    const audience = mode === 'off' ? [] : lineValues(targetAudience)
    if (!goals || !audience) {
      setError('Use at most 16 lines and 256 characters per goal or audience entry.')
      return
    }
    if (!validSettings(mode, maxBallotIssues, maxDiscussionIssues, maxRounds, minReaders)) {
      setError('Reader Panel settings are invalid. Check the allowed ranges for this mode.')
      return
    }
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    setNotice(null)
    const controller = new AbortController()
    controllerRef.current = controller
    try {
      const payload: ReaderPanelStartPayload = {
        document_id: documentId,
        document_version_id: documentVersionId,
        mode,
      }
      if (mode !== 'off') {
        payload.config_overrides = {
          max_ballot_issues: maxBallotIssues,
          max_discussion_issues: maxDiscussionIssues,
          max_rounds_per_issue: maxRounds,
          min_valid_readers: minReaders,
        }
        payload.test_goals = goals
        payload.target_audience = audience
      }
      const result = await startReaderPanel(projectId, chapterId, payload, controller.signal)
      if (!mountedRef.current || controller.signal.aborted) return
      if (!matchesScope(result, { projectId, chapterId, documentId, documentVersionId })) {
        setError(scopeError)
      } else if (result.is_noop) {
        setNotice('Reader Panel is off. No session was created and no reader run was started.')
      } else {
        onSessionStarted?.(result.session_id)
      }
    } catch {
      if (mountedRef.current && !controller.signal.aborted) setError(safeRequestError)
    } finally {
      if (mountedRef.current) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  async function operate(operation: 'cancel' | 'resume') {
    if (!sessionId || submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    const controller = new AbortController()
    controllerRef.current = controller
    pollerRef.current?.cancel()
    try {
      const result = operation === 'cancel'
        ? await cancelReaderPanel(projectId, chapterId, sessionId, controller.signal)
        : await resumeReaderPanel(projectId, chapterId, sessionId, controller.signal)
      if (!mountedRef.current || controller.signal.aborted) return
      if (!matchesScope(result, { projectId, chapterId, documentId, documentVersionId }, sessionId) || result.is_noop) {
        setPanel(null)
        setError(scopeError)
      } else {
        setMonitoringPaused(false)
        setPanel(result)
        if (operation === 'resume') setPollRevision((revision) => revision + 1)
      }
    } catch {
      if (mountedRef.current && !controller.signal.aborted) {
        setError(safeRequestError)
        setPollRevision((revision) => revision + 1)
      }
    } finally {
      if (mountedRef.current) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  if (!sessionId) {
    function selectMode(value: string) {
      if (!isPanelMode(value)) return
      setMode(value)
      if (value === 'off') return
      const preset = modePresets[value]
      setMaxBallotIssues(preset.maxBallotIssues)
      setMaxDiscussionIssues(preset.maxDiscussionIssues)
      setMaxRounds(preset.maxRounds)
      setMinReaders(preset.minReaders)
    }

    function updateNumber(value: number, update: (next: number) => void) {
      if (!Number.isNaN(value)) update(value)
    }

    return <section className="reader-panel" role="region" aria-labelledby="reader-panel-title">
      <h2 id="reader-panel-title">Reader Panel</h2>
      <p className="muted">Optional beta-reader diagnostics for this immutable document version.</p>
      <form className="reader-panel-form" onSubmit={start}>
        <label>Panel mode<select value={mode} onChange={(event) => selectMode(event.target.value)}>
          <option value="off">Off</option><option value="quick">Quick</option><option value="standard">Standard</option><option value="panel">Panel</option>
        </select></label>
        <fieldset className="reader-panel-controls" disabled={mode === 'off'}>
          <legend>Panel limits</legend>
          <label>Maximum ballot issues<input type="number" min="1" max="8" value={maxBallotIssues} onChange={(event) => updateNumber(event.currentTarget.valueAsNumber, setMaxBallotIssues)} required /></label>
          <label>Maximum discussion issues<input type="number" min="0" max="6" value={maxDiscussionIssues} onChange={(event) => updateNumber(event.currentTarget.valueAsNumber, setMaxDiscussionIssues)} required /></label>
          <label>Maximum rounds per issue<input type="number" min="0" max="3" value={maxRounds} onChange={(event) => updateNumber(event.currentTarget.valueAsNumber, setMaxRounds)} required /></label>
          <label>Minimum valid readers<input type="number" min="1" max="6" value={minReaders} onChange={(event) => updateNumber(event.currentTarget.valueAsNumber, setMinReaders)} required /></label>
        </fieldset>
        <label>Test goals<textarea rows={3} value={testGoals} onChange={(event) => setTestGoals(event.target.value)} placeholder="One bounded goal per line" /></label>
        <label>Target audience<textarea rows={3} value={targetAudience} onChange={(event) => setTargetAudience(event.target.value)} placeholder="One audience description per line" /></label>
        <button type="submit" disabled={submitting} aria-busy={submitting}>{submitting ? 'Submitting Reader Panel…' : mode === 'off' ? 'Skip Reader Panel' : 'Start Reader Panel'}</button>
      </form>
      {notice && <p className="reader-panel-notice" role="status" aria-live="polite">{notice}</p>}
      {error && <FocusedAlert>{error}</FocusedAlert>}
    </section>
  }

  return <section className="reader-panel" role="region" aria-labelledby="reader-panel-title">
    <h2 id="reader-panel-title">Reader Panel</h2>
    {!panel && !error && !monitoringPaused && <p role="status" aria-live="polite">Loading Reader Panel session…</p>}
    {monitoringPaused && <div className="reader-panel-notice" role="status" aria-live="polite">
      Monitoring paused after the bounded polling window.
      <button type="button" className="secondary-button" onClick={() => { setMonitoringPaused(false); setPollRevision((revision) => revision + 1) }}>Continue monitoring</button>
    </div>}
    {error && <FocusedAlert>{error}</FocusedAlert>}
    {panel && <>
      <div className={`reader-panel-status ${panel.status === 'degraded_completed' || panel.status === 'failed' || panel.status === 'cancelled' ? 'attention' : ''}`} role="status" aria-live="polite">
        <strong>Status:</strong> {readerPanelStatusLabel(panel.status)}
      </div>
      {panel.stale && <div className="reader-panel-alert danger" role="alert"><strong>Stale result:</strong> this panel is bound to an outdated source version. Do not apply it to the current chapter.</div>}
      {panel.status === 'degraded_completed' && <div className="reader-panel-alert warning" role="alert"><strong>Degraded result:</strong> {panel.degradation_reason || 'The panel completed with reduced reader coverage.'}</div>}
      {panel.status === 'failed' && <div className="reader-panel-alert danger" role="alert"><strong>Reader Panel failed.</strong> {panel.failure_reason || 'No diagnostic recommendation was approved or applied.'}</div>}
      {panel.status === 'cancelled' && <div className="reader-panel-alert warning" role="alert">Reader Panel was cancelled. Partial output is not an approved edit.</div>}
      <div className="reader-panel-actions" aria-label="Permitted Reader Panel actions">
        {panel.permitted_operations.includes('cancel') && <button type="button" className="secondary-button" disabled={submitting} onClick={() => void operate('cancel')}>Cancel Reader Panel</button>}
        {panel.permitted_operations.includes('resume') && <button type="button" disabled={submitting} onClick={() => void operate('resume')}>Resume Reader Panel</button>}
      </div>
      {(panel.status === 'completed' || panel.status === 'degraded_completed') && <Report panel={panel} />}
    </>}
  </section>
}
