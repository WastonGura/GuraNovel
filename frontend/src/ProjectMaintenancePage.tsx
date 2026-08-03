import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ApiError,
  resolveProjectMaintenanceAction,
  startProjectMaintenance,
  type ProjectMaintenanceDecision,
  type ProjectMaintenanceRun,
  type ProjectMaintenanceScopeHint,
} from './api/client'
import { ProjectMaintenanceQuery } from './api/projectMaintenanceQuery'

const scopes: ReadonlyArray<{ value: ProjectMaintenanceScopeHint, label: string, groupLabel: string }> = [
  { value: 'chapter', label: 'Chapters', groupLabel: 'chapters' },
  { value: 'character', label: 'Characters', groupLabel: 'characters' },
  { value: 'world', label: 'World', groupLabel: 'world' },
  { value: 'outline', label: 'Outline', groupLabel: 'outline' },
  { value: 'foreshadowing', label: 'Foreshadowing', groupLabel: 'foreshadowing' },
  { value: 'timeline', label: 'Timeline', groupLabel: 'timeline' },
  { value: 'style', label: 'Style', groupLabel: 'style' },
]

const analysisLabels: Partial<Record<ProjectMaintenanceRun['status'], string>> = {
  CHANGE_REQUESTED: 'Preparing impact analysis',
  LORE_IMPACT_ANALYSIS: 'Reviewing story-world impact',
  CHIEF_EDITOR_IMPACT_ANALYSIS: 'Reviewing editorial impact',
  REVISION_PLAN: 'Building a safe revision plan',
}

type MaintenanceError = { message: string, retry: boolean }

function safeError(error: unknown): MaintenanceError {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return { message: 'This maintenance decision is stale. Reload the current gate and try again.', retry: true }
    }
    if (error.status === 404) {
      return {
        message: 'This maintenance request was not found. Return to the project workspace and start again if needed.',
        retry: false,
      }
    }
    if (error.status === 422) {
      return { message: 'This change request could not be analyzed. Review the request and try again.', retry: true }
    }
    if (error.code === 'invalid_response' || error.code === 'invalid_request') {
      return {
        message: 'Maintenance is in an invalid state. Return to the project workspace and try again later.',
        retry: false,
      }
    }
  }
  return { message: 'Maintenance could not be loaded. Check your connection and try again.', retry: true }
}

function FocusedError({ error, onRetry }: { error: MaintenanceError, onRetry?: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => { ref.current?.focus() }, [error.message])
  return (
    <div className="maintenance-error" role="alert" tabIndex={-1} ref={ref}>
      <p>{error.message}</p>
      {error.retry && onRetry && <button type="button" className="secondary-button" onClick={onRetry}>Try again</button>}
    </div>
  )
}

function MaintenanceStart() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [changeRequest, setChangeRequest] = useState('')
  const [selectedScopes, setSelectedScopes] = useState<ProjectMaintenanceScopeHint[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<MaintenanceError | null>(null)
  const submittingRef = useRef(false)
  const controllerRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      controllerRef.current?.abort()
    }
  }, [])

  function toggleScope(scope: ProjectMaintenanceScopeHint) {
    setSelectedScopes((current) => current.includes(scope)
      ? current.filter((value) => value !== scope)
      : [...current, scope])
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submittingRef.current) return
    const trimmedTitle = title.trim()
    const trimmedRequest = changeRequest.trim()
    if (!trimmedTitle || !trimmedRequest) {
      setError({ message: 'Add a title and change request before starting analysis.', retry: false })
      return
    }
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    const controller = new AbortController()
    controllerRef.current = controller
    try {
      const started = await startProjectMaintenance(projectId, {
        title: trimmedTitle,
        change_request: trimmedRequest,
        scope_hints: selectedScopes,
      }, controller.signal)
      if (mountedRef.current && !controller.signal.aborted) {
        navigate(`/projects/${projectId}/maintenance/${started.id}`)
      }
    } catch (caught: unknown) {
      if (mountedRef.current && !controller.signal.aborted) setError(safeError(caught))
    } finally {
      if (mountedRef.current) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  return (
    <section className="page maintenance-page" aria-labelledby="route-title">
      <Link className="back-link" to={`/projects/${projectId}`}>Back to project workspace</Link>
      <p className="eyebrow">Project maintenance</p>
      <h1 id="route-title">Plan a project change</h1>
      <p className="lede">Analyze the impact and review a revision plan before any project documents can change.</p>
      <form className="workspace-form maintenance-start" aria-label="Start project maintenance" onSubmit={submit}>
        <label>
          Change title
          <input required maxLength={512} value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label>
          Change request
          <textarea aria-label="Change request" required maxLength={4000} value={changeRequest} onChange={(event) => setChangeRequest(event.target.value)} />
          <span className="char-count">{changeRequest.length} / 4000</span>
        </label>
        <fieldset className="scope-fieldset">
          <legend>Scope hints (optional)</legend>
          <p className="muted">Select only the areas you already expect the change to affect.</p>
          <div className="scope-grid">
            {scopes.map((scope) => (
              <label key={scope.value}>
                <input
                  type="checkbox"
                  checked={selectedScopes.includes(scope.value)}
                  onChange={() => toggleScope(scope.value)}
                />
                {scope.label}
              </label>
            ))}
          </div>
        </fieldset>
        {error && <FocusedError error={error} />}
        <button type="submit" disabled={submitting}>{submitting ? 'Starting analysis…' : 'Analyze change'}</button>
      </form>
    </section>
  )
}

function AnalysisProgress({ run }: { run: ProjectMaintenanceRun | null }) {
  const label = run ? analysisLabels[run.status] : 'Loading maintenance analysis'
  return (
    <div className="analysis-progress" role="status">
      <span className="progress-mark" aria-hidden="true" />
      <div>
        <strong>{label ?? 'Preparing your decision gate'}</strong>
        <p>Only reviewed impact and plan details will appear here.</p>
      </div>
    </div>
  )
}

function AffectedItems({ run }: { run: ProjectMaintenanceRun }) {
  if (run.affected_items.length === 0) {
    return <p className="empty-state">No affected items have been identified yet.</p>
  }
  return (
    <section className="maintenance-section" aria-labelledby="affected-title">
      <div className="section-heading">
        <h2 id="affected-title">Affected items</h2>
        <span>{run.affected_items.length} reviewed</span>
      </div>
      <div className="affected-groups">
        {scopes.map((scope) => {
          const items = run.affected_items.filter((item) => item.type === scope.value)
          if (items.length === 0) return null
          return (
            <section className="affected-group" aria-label={`Affected ${scope.groupLabel}`} key={scope.value}>
              <h3>{scope.label}</h3>
              <ul>
                {items.map((item) => (
                  <li key={item.id}>
                    <div className="affected-title">
                      <code>{item.stable_reference}</code>
                      <span className={`impact-badge impact-${item.impact_level}`}>{item.impact_level[0].toUpperCase()}{item.impact_level.slice(1)} impact</span>
                    </div>
                    <p>{item.reason}</p>
                  </li>
                ))}
              </ul>
            </section>
          )
        })}
      </div>
    </section>
  )
}

function RevisionPlan({ run }: { run: ProjectMaintenanceRun }) {
  const plan = run.revision_plan
  if (!plan) return null
  const outcomeLabel = plan.review_outcome === 'passed'
    ? 'Ready for your decision'
    : plan.review_outcome === 'warning' ? 'Review warnings before deciding' : 'Revision required'
  return (
    <section className="maintenance-section revision-plan" aria-labelledby="plan-title">
      <div className="section-heading">
        <h2 id="plan-title">Revision plan</h2>
        <span className={`outcome-badge outcome-${plan.review_outcome}`}>{outcomeLabel}</span>
      </div>
      <p className="plan-summary">{plan.summary}</p>
      {plan.review_outcome === 'warning' && (
        <div className="gate-warnings"><h3>Advisory warning</h3><p>Review every operation carefully before making a decision.</p></div>
      )}
      {plan.review_outcome === 'blocking' && (
        <div className="gate-blocking"><h3>Blocking condition</h3><p>This plan cannot be approved. Request a revision or cancel the change.</p></div>
      )}
      <ol className="operation-list">
        {plan.operations.map((operation) => (
          <li key={operation.id}>
            <div className="operation-heading">
              <strong>{operation.operation === 'revise' ? 'Revise document' : 'Retain document'}</strong>
              <span>Step {operation.sequence}</span>
            </div>
            <p>{operation.instruction}</p>
            <p className="operation-reference">Target document {operation.document_id}</p>
            <p className="operation-reference">Expected version {operation.expected_version_id}</p>
          </li>
        ))}
      </ol>
      <aside className="rollback-guidance" aria-labelledby="rollback-title">
        <h3 id="rollback-title">Rollback guidance</h3>
        <p>No documents change from this screen until you approve. After a later apply, restore a prior document version from the document workspace if a change must be undone.</p>
      </aside>
    </section>
  )
}

const decisionLabels: Record<'approve' | 'revise' | 'cancel', string> = {
  approve: 'Approve plan',
  revise: 'Request revision',
  cancel: 'Cancel change',
}

function ConfirmationActions({
  run,
  resolving,
  onDecision,
}: {
  run: ProjectMaintenanceRun,
  resolving: boolean,
  onDecision: (decision: ProjectMaintenanceDecision) => void,
}) {
  const pending = run.status === 'USER_CONFIRMATION' && run.awaiting_user ? run.pending_action : null
  if (!pending) return null
  const decisions = pending.allowed_decisions.filter(
    (decision): decision is 'approve' | 'revise' | 'cancel' => (
      decision === 'revise'
      || decision === 'cancel'
      || (decision === 'approve' && pending.review_outcome !== 'blocking')
    ),
  )
  return (
    <section className="decision-panel" aria-labelledby="decision-title">
      <h2 id="decision-title">Your decision</h2>
      <p>Choose one of the actions currently allowed by this maintenance gate.</p>
      <div className="decision-actions">
        {decisions.map((decision) => (
          <button
            type="button"
            className={decision === 'approve' ? undefined : 'secondary-button'}
            key={decision}
            disabled={resolving}
            onClick={() => onDecision(decision)}
          >
            {resolving ? 'Recording decision…' : decisionLabels[decision]}
          </button>
        ))}
      </div>
    </section>
  )
}

function MaintenanceGate() {
  const { projectId = '', workflowRunId = '' } = useParams()
  const navigate = useNavigate()
  const [run, setRun] = useState<ProjectMaintenanceRun | null>(null)
  const [error, setError] = useState<MaintenanceError | null>(null)
  const [retryGeneration, setRetryGeneration] = useState(0)
  const [resolving, setResolving] = useState(false)
  const resolvingRef = useRef(false)
  const resolveControllerRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(true)
  const headingRef = useRef<HTMLHeadingElement>(null)

  useLayoutEffect(() => {
    if (run?.status === 'USER_CONFIRMATION') headingRef.current?.focus()
  }, [run?.id, run?.status])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      resolveControllerRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    const query = new ProjectMaintenanceQuery()
    void query.poll(
      { projectId, workflowRunId },
      {
        maxAttempts: 30,
        intervalMs: 1_500,
        onUpdate: (updatedRun) => {
          setRun(updatedRun)
          setError(null)
          const consistencyWarning = updatedRun.status === 'USER_CONFIRMATION'
            && updatedRun.pending_action?.confirmation_kind === 'consistency_warning'
          if (consistencyWarning || ['APPLY_CHANGE', 'CONSISTENCY_REVIEW', 'PROJECT_UPDATED', 'CANCELLED'].includes(updatedRun.status)) {
            query.cancel()
            navigate(`/projects/${projectId}/maintenance/${updatedRun.id}/status`, { replace: true })
          }
        },
        onError: (caught) => setError(safeError(caught)),
      },
    ).catch((caught: unknown) => setError(safeError(caught)))
    return () => query.cancel()
  }, [navigate, projectId, retryGeneration, workflowRunId])

  async function decide(decision: ProjectMaintenanceDecision) {
    if (!run || resolvingRef.current) return
    const decisionRun = run
    resolvingRef.current = true
    setResolving(true)
    setError(null)
    setRun(null)
    const controller = new AbortController()
    resolveControllerRef.current = controller
    try {
      const resolved = await resolveProjectMaintenanceAction(projectId, decisionRun, decision, controller.signal)
      if (mountedRef.current && !controller.signal.aborted) {
        navigate(`/projects/${projectId}/maintenance/${resolved.id}/status`, { replace: true })
      }
    } catch (caught: unknown) {
      if (mountedRef.current && !controller.signal.aborted) setError(safeError(caught))
    } finally {
      if (mountedRef.current) {
        resolvingRef.current = false
        setResolving(false)
      }
    }
  }

  return (
    <section className="page maintenance-page" aria-labelledby="route-title">
      <Link className="back-link" to={`/projects/${projectId}`}>Back to project workspace</Link>
      <p className="eyebrow">Project maintenance</p>
      <h1 id="route-title" ref={headingRef} tabIndex={run?.status === 'USER_CONFIRMATION' ? -1 : undefined}>{run?.title ?? 'Maintenance analysis'}</h1>
      {error && <FocusedError error={error} onRetry={() => {
        setError(null)
        setRetryGeneration((value) => value + 1)
      }} />}
      {!error && (!run || run.status !== 'USER_CONFIRMATION') && <AnalysisProgress run={run} />}
      {run && <AffectedItems run={run} />}
      {run && <RevisionPlan run={run} />}
      {run && <ConfirmationActions run={run} resolving={resolving} onDecision={(decision) => void decide(decision)} />}
    </section>
  )
}

function MaintenanceHandoff() {
  const { projectId = '', workflowRunId = '' } = useParams()
  const navigate = useNavigate()
  const [run, setRun] = useState<ProjectMaintenanceRun | null>(null)
  const [error, setError] = useState<MaintenanceError | null>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    const query = new ProjectMaintenanceQuery()
    void query.poll(
      { projectId, workflowRunId },
      {
        maxAttempts: 1,
        intervalMs: 0,
        onUpdate: (updatedRun) => {
          if (
            updatedRun.status === 'USER_CONFIRMATION'
            && updatedRun.pending_action?.confirmation_kind === 'revision_confirmation'
          ) {
            query.cancel()
            navigate(`/projects/${projectId}/maintenance/${updatedRun.id}`, { replace: true })
            return
          }
          setRun(updatedRun)
          setError(null)
        },
        onError: (caught) => setError(safeError(caught)),
      },
    ).catch((caught: unknown) => setError(safeError(caught)))
    return () => query.cancel()
  }, [navigate, projectId, workflowRunId])

  useLayoutEffect(() => {
    if (run) headingRef.current?.focus()
  }, [run])

  const consistencyWarning = run?.status === 'USER_CONFIRMATION'
    && run.pending_action?.confirmation_kind === 'consistency_warning'
  const decisionRecorded = run && ['APPLY_CHANGE', 'CONSISTENCY_REVIEW', 'PROJECT_UPDATED', 'CANCELLED'].includes(run.status)
  return (
    <section className="page maintenance-page handoff-page" aria-labelledby="route-title">
      <p className="eyebrow">Project maintenance</p>
      {!run && !error && <><h1 id="route-title">Checking maintenance status</h1><p className="lede" role="status">Verifying the current workflow state.</p></>}
      {error && <><h1 id="route-title">Maintenance status unavailable</h1><FocusedError error={error} /></>}
      {run && <>
        <h1 id="route-title" ref={headingRef} tabIndex={-1}>{consistencyWarning
          ? 'Additional review is required'
          : decisionRecorded ? 'Decision recorded' : 'Maintenance analysis continues'}</h1>
        <p className="lede">{consistencyWarning
          ? 'The workflow reached a later consistency review gate.'
          : decisionRecorded
            ? 'Your decision was accepted and control has returned to the maintenance workflow.'
            : 'The workflow has not reached a confirmed post-decision state.'}</p>
        <div className="handoff-notice" role="status">
          <strong>{consistencyWarning ? 'Later workflow step' : decisionRecorded ? 'Confirmation complete' : 'Confirmation not verified'}</strong>
          <p>{consistencyWarning
            ? 'This confirmation screen does not offer later consistency decisions. Continue from the maintenance status experience when available.'
            : decisionRecorded
              ? 'This gate does not report apply progress or claim that project documents changed.'
              : 'No decision completion is claimed from this link. Return to the live gate to continue.'}</p>
        </div>
      </>}
      {run && !decisionRecorded && !consistencyWarning && <Link className="back-link" to={`/projects/${projectId}/maintenance/${workflowRunId}`}>Return to live gate</Link>}
      <Link className="back-link" to={`/projects/${projectId}`}>Return to project workspace</Link>
    </section>
  )
}

function MaintenanceGateRoute() {
  const { projectId = '', workflowRunId = '' } = useParams()
  return <MaintenanceGate key={`${projectId}:${workflowRunId}`} />
}

function MaintenanceStartRoute() {
  const { projectId = '' } = useParams()
  return <MaintenanceStart key={projectId} />
}

function MaintenanceHandoffRoute() {
  const { projectId = '', workflowRunId = '' } = useParams()
  return <MaintenanceHandoff key={`${projectId}:${workflowRunId}`} />
}

export default function ProjectMaintenancePage({ mode }: { mode: 'start' | 'gate' | 'handoff' }) {
  if (mode === 'start') return <MaintenanceStartRoute />
  if (mode === 'handoff') return <MaintenanceHandoffRoute />
  return <MaintenanceGateRoute />
}
