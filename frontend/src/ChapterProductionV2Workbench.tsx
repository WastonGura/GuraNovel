import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react'
import { ApiError } from './api/client'
import {
  finalizeChapterProduction,
  getChapterProductionRun,
  listChapterProductionRuns,
  reconcileChapterProduction,
  resolveChapterProductionAction,
  resumeChapterProduction,
  startChapterProductionV2,
  triggerChapterReview,
  type ChapterActionDecision,
  type ChapterFailureCode,
  type ChapterProductionState,
} from './api/chapterProductionV2Client'

export interface ChapterProductionV2WorkbenchProps {
  projectId: string
  chapterId: string
  initialWorkflowRunId?: string
  onStateChange?: (state: ChapterProductionState | null) => void
}

interface WorkbenchError {
  message: string
  retry?: boolean
}

function sanitizeFailureCode(code: ChapterFailureCode | null, failedFromStatus?: string | null): string {
  switch (code) {
    case 'reconciliation_required':
      return 'State reconciliation is required before chapter production can continue.'
    case 'document_commit_indeterminate':
      return 'Document commit outcome is indeterminate. Please reconcile state to recover.'
    case 'provider_unavailable':
      return 'AI drafting provider is temporarily unavailable. You may resume when available.'
    case 'provider_timeout':
      return 'AI drafting provider timed out. You may resume drafting.'
    case 'invalid_provider_output':
      return 'AI provider returned invalid structured output. You may resume drafting.'
    case 'persistence_unavailable':
      return 'Database persistence was temporarily unavailable. State reconciliation required.'
    case 'archive_unavailable':
      return 'Archive storage was temporarily unavailable. State reconciliation required.'
    default:
      return failedFromStatus
        ? `Chapter production failed during step: ${failedFromStatus}.`
        : 'An unexpected issue occurred during chapter production.'
  }
}

function safeWorkbenchError(error: unknown): WorkbenchError {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return {
        message: 'Chapter production state conflict or reconciliation required. Please reconcile state.',
        retry: true,
      }
    }
    if (error.status === 404) {
      return {
        message: 'Chapter production run or action was not found.',
        retry: false,
      }
    }
    if (error.status === 422) {
      return {
        message: 'The submitted revision decision failed validation. Please check your inputs.',
        retry: true,
      }
    }
    if (error.status === 503) {
      return {
        message: 'AI provider is temporarily unavailable or timed out.',
        retry: true,
      }
    }
    if (error.status === 500) {
      return {
        message: 'Commit outcome is indeterminate. State reconciliation is required.',
        retry: true,
      }
    }
    if (error.code === 'invalid_response') {
      return {
        message: 'The server returned an invalid production response.',
        retry: false,
      }
    }
  }
  return {
    message: 'Chapter production operation failed. Check your connection and try again.',
    retry: true,
  }
}

function FocusedError({ error, onRetry }: { error: WorkbenchError; onRetry?: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    ref.current?.focus()
  }, [error.message])

  return (
    <div className="maintenance-error" role="alert" tabIndex={-1} ref={ref}>
      <p>{error.message}</p>
      {error.retry && onRetry && (
        <button type="button" className="secondary-button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function parseUuidList(input: string): string[] {
  const matches = input.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi)
  return matches ? matches.map((id) => id.toLowerCase()) : []
}

export function ChapterProductionV2Workbench({
  projectId,
  chapterId,
  initialWorkflowRunId,
  onStateChange,
}: ChapterProductionV2WorkbenchProps) {
  const [workflowRunId, setWorkflowRunId] = useState<string | null>(initialWorkflowRunId ?? null)
  const [state, setState] = useState<ChapterProductionState | null>(null)
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<WorkbenchError | null>(null)

  // Sub-views / Form States for Author Revision
  const [authorActionType, setAuthorActionType] = useState<'accept' | 'feedback' | 'manual'>('accept')
  const [feedbackInput, setFeedbackInput] = useState('')
  const [manualContentInput, setManualContentInput] = useState('')
  const [targetSegmentsInput, setTargetSegmentsInput] = useState('')

  // Sub-views for Review Warning
  const [reviewActionType, setReviewActionType] = useState<'proceed' | 'revision'>('proceed')

  const mountedRef = useRef(true)
  const submittingRef = useRef(false)
  const controllerRef = useRef<AbortController | null>(null)
  const pollingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      if (controllerRef.current) {
        controllerRef.current.abort()
      }
      if (pollingTimerRef.current) {
        clearTimeout(pollingTimerRef.current)
      }
    }
  }, [])

  // Sync state changes upward
  useEffect(() => {
    onStateChange?.(state)
  }, [state, onStateChange])

  // Initial load
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    controllerRef.current = controller

    async function loadInitial() {
      if (!UUID_REGEX.test(projectId) || !UUID_REGEX.test(chapterId)) {
        setLoading(false)
        setState(null)
        return
      }

      setLoading(true)
      setError(null)
      try {
        let runIdToFetch = initialWorkflowRunId ?? null
        if (!runIdToFetch) {
          try {
            const runs = await listChapterProductionRuns(projectId, chapterId, { limit: 1 }, controller.signal)
            if (runs.length > 0) {
              runIdToFetch = runs[0].workflow_run_id
            }
          } catch {
            // Initial run discovery fallback
            runIdToFetch = null
          }
        }

        if (runIdToFetch) {
          if (!active) return
          setWorkflowRunId(runIdToFetch)
          const runState = await getChapterProductionRun(projectId, chapterId, runIdToFetch, controller.signal)
          if (active && mountedRef.current) {
            setState(runState)
          }
        } else {
          if (active && mountedRef.current) {
            setState(null)
          }
        }
      } catch (caught: unknown) {
        if (active && mountedRef.current && !controller.signal.aborted) {
          setError(safeWorkbenchError(caught))
        }
      } finally {
        if (active && mountedRef.current) {
          setLoading(false)
        }
      }
    }

    void loadInitial()

    return () => {
      active = false
      controller.abort()
    }
  }, [projectId, chapterId, initialWorkflowRunId])

  // Polling for in-progress states
  useEffect(() => {
    if (!workflowRunId || !state) return
    const isTerminal = state.status === 'COMPLETED' || state.status === 'CANCELLED' || state.status === 'FAILED'
    const needsPolling = !isTerminal && !state.awaiting_user

    if (!needsPolling) return

    let cancelled = false

    const poll = async () => {
      try {
        const nextState = await getChapterProductionRun(projectId, chapterId, workflowRunId)
        if (!cancelled && mountedRef.current) {
          setState(nextState)
          if (!nextState.awaiting_user && nextState.status !== 'COMPLETED' && nextState.status !== 'CANCELLED' && nextState.status !== 'FAILED') {
            pollingTimerRef.current = setTimeout(poll, 1500)
          }
        }
      } catch {
        // Suppress temporary polling errors or retry
        if (!cancelled && mountedRef.current) {
          pollingTimerRef.current = setTimeout(poll, 3000)
        }
      }
    }

    pollingTimerRef.current = setTimeout(poll, 1500)

    return () => {
      cancelled = true
      if (pollingTimerRef.current) {
        clearTimeout(pollingTimerRef.current)
      }
    }
  }, [projectId, chapterId, workflowRunId, state])

  // Actions
  async function handleStart() {
    if (submittingRef.current || loading) return
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    const controller = new AbortController()
    controllerRef.current = controller

    try {
      const started = await startChapterProductionV2(projectId, chapterId, controller.signal)
      if (mountedRef.current && !controller.signal.aborted) {
        setWorkflowRunId(started.workflow_run_id)
        const runState = await getChapterProductionRun(projectId, chapterId, started.workflow_run_id, controller.signal)
        if (mountedRef.current) {
          setState(runState)
        }
      }
    } catch (caught: unknown) {
      if (mountedRef.current && !controller.signal.aborted) {
        setError(safeWorkbenchError(caught))
      }
    } finally {
      if (mountedRef.current) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  async function handleResolveAuthorAction(event?: FormEvent) {
    if (event) event.preventDefault()
    if (!workflowRunId || !state?.action_request_id || submittingRef.current) return

    let decision: ChapterActionDecision = 'accept'
    let feedback: string | undefined
    let targetSegmentIds: string[] | undefined
    let content: string | undefined

    if (authorActionType === 'feedback') {
      decision = 'request_feedback_revision'
      const trimmed = feedbackInput.trim()
      if (!trimmed) {
        setError({ message: 'Please provide revision feedback before submitting.', retry: false })
        return
      }
      feedback = trimmed
      const parsedSegments = parseUuidList(targetSegmentsInput)
      targetSegmentIds = parsedSegments.length > 0 ? parsedSegments : (state.document_version_id ? [state.document_version_id] : [])
    } else if (authorActionType === 'manual') {
      decision = 'submit_manual_edit'
      const trimmed = manualContentInput.trim()
      if (!trimmed) {
        setError({ message: 'Please provide the edited draft content.', retry: false })
        return
      }
      content = trimmed
    }

    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    const controller = new AbortController()
    controllerRef.current = controller

    try {
      await resolveChapterProductionAction(
        projectId,
        chapterId,
        workflowRunId,
        state.action_request_id,
        {
          decision,
          feedback,
          target_segment_ids: targetSegmentIds,
          content,
        },
        controller.signal,
      )

      if (mountedRef.current && !controller.signal.aborted) {
        const nextState = await getChapterProductionRun(projectId, chapterId, workflowRunId, controller.signal)
        if (mountedRef.current) {
          setState(nextState)
          setFeedbackInput('')
          setManualContentInput('')
          setTargetSegmentsInput('')
        }
      }
    } catch (caught: unknown) {
      if (mountedRef.current && !controller.signal.aborted) {
        setError(safeWorkbenchError(caught))
      }
    } finally {
      if (mountedRef.current) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  async function handleTriggerReview() {
    if (!workflowRunId || submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    const controller = new AbortController()
    controllerRef.current = controller

    try {
      await triggerChapterReview(projectId, chapterId, workflowRunId, controller.signal)
      if (mountedRef.current && !controller.signal.aborted) {
        const nextState = await getChapterProductionRun(projectId, chapterId, workflowRunId, controller.signal)
        if (mountedRef.current) {
          setState(nextState)
        }
      }
    } catch (caught: unknown) {
      if (mountedRef.current && !controller.signal.aborted) {
        setError(safeWorkbenchError(caught))
      }
    } finally {
      if (mountedRef.current) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  async function handleResolveReviewAction(event?: FormEvent) {
    if (event) event.preventDefault()
    if (!workflowRunId || !state?.action_request_id || submittingRef.current) return

    const isBlocking = state.action_kind === 'review_revision'
    let decision: ChapterActionDecision = 'proceed_with_warnings'

    const availableReports: string[] = [
      state.editor_report_id,
      state.chief_editor_report_id,
      state.lore_report_id,
    ].filter((id): id is string => id !== null && id !== undefined)

    let reportIds: string[] | undefined
    let targetSegmentIds: string[] | undefined

    if (isBlocking || reviewActionType === 'revision') {
      decision = 'request_review_revision'
      reportIds = availableReports.length > 0 ? availableReports : (state.document_version_id ? [state.document_version_id] : [])
      const parsedSegments = parseUuidList(targetSegmentsInput)
      targetSegmentIds = parsedSegments.length > 0 ? parsedSegments : (state.document_version_id ? [state.document_version_id] : [])
    }

    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    const controller = new AbortController()
    controllerRef.current = controller

    try {
      await resolveChapterProductionAction(
        projectId,
        chapterId,
        workflowRunId,
        state.action_request_id,
        {
          decision,
          report_ids: reportIds,
          target_segment_ids: targetSegmentIds,
        },
        controller.signal,
      )

      if (mountedRef.current && !controller.signal.aborted) {
        const nextState = await getChapterProductionRun(projectId, chapterId, workflowRunId, controller.signal)
        if (mountedRef.current) {
          setState(nextState)
          setTargetSegmentsInput('')
        }
      }
    } catch (caught: unknown) {
      if (mountedRef.current && !controller.signal.aborted) {
        setError(safeWorkbenchError(caught))
      }
    } finally {
      if (mountedRef.current) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  async function handleFinalize() {
    if (!workflowRunId || submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    const controller = new AbortController()
    controllerRef.current = controller

    try {
      await finalizeChapterProduction(projectId, chapterId, workflowRunId, controller.signal)
      if (mountedRef.current && !controller.signal.aborted) {
        const nextState = await getChapterProductionRun(projectId, chapterId, workflowRunId, controller.signal)
        if (mountedRef.current) {
          setState(nextState)
        }
      }
    } catch (caught: unknown) {
      if (mountedRef.current && !controller.signal.aborted) {
        setError(safeWorkbenchError(caught))
      }
    } finally {
      if (mountedRef.current) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  async function handleReconcile() {
    if (!workflowRunId || submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    const controller = new AbortController()
    controllerRef.current = controller

    try {
      const recoveredState = await reconcileChapterProduction(projectId, chapterId, workflowRunId, controller.signal)
      if (mountedRef.current && !controller.signal.aborted) {
        setState(recoveredState)
      }
    } catch (caught: unknown) {
      if (mountedRef.current && !controller.signal.aborted) {
        setError(safeWorkbenchError(caught))
      }
    } finally {
      if (mountedRef.current) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  async function handleResume() {
    if (!workflowRunId || submittingRef.current) return
    submittingRef.current = true
    setSubmitting(true)
    setError(null)
    const controller = new AbortController()
    controllerRef.current = controller

    try {
      await resumeChapterProduction(projectId, chapterId, workflowRunId, controller.signal)
      if (mountedRef.current && !controller.signal.aborted) {
        const nextState = await getChapterProductionRun(projectId, chapterId, workflowRunId, controller.signal)
        if (mountedRef.current) {
          setState(nextState)
        }
      }
    } catch (caught: unknown) {
      if (mountedRef.current && !controller.signal.aborted) {
        setError(safeWorkbenchError(caught))
      }
    } finally {
      if (mountedRef.current) {
        submittingRef.current = false
        setSubmitting(false)
      }
    }
  }

  if (loading && !state) {
    return (
      <section className="production-workspace" aria-labelledby="production-workbench-title">
        <h2 id="production-workbench-title">Chapter production (V2)</h2>
        <p className="muted">Loading chapter production state…</p>
      </section>
    )
  }

  // Not started state
  if (!state) {
    return (
      <section className="production-workspace" aria-labelledby="production-workbench-title">
        <h2 id="production-workbench-title">Chapter production (V2)</h2>
        <p className="muted">Chapter production (V2) has not started yet.</p>
        <button
          type="button"
          onClick={handleStart}
          disabled={submitting || loading}
          aria-busy={submitting}
        >
          {submitting ? 'Starting chapter production (V2)…' : 'Start chapter production (V2)'}
        </button>
        {error && <FocusedError error={error} onRetry={handleStart} />}
      </section>
    )
  }

  const {
    status,
    current_node,
    awaiting_user,
    document_id,
    document_version_id,
    content_hash,
    action_kind,
    failure_code,
    failed_from_status,
  } = state

  const isFailed = status === 'FAILED' || failure_code !== null
  const isAuthorRevision = status === 'AUTHOR_REVISION' && awaiting_user
  const isReviewStage = (status === 'EDITOR_REVIEW' || status === 'CHIEF_FINAL_REVIEW' || status === 'LORE_FINAL_REVIEW')
  const isRevisionReady = status === 'REVISION_READY'
  const isCompleted = status === 'COMPLETED'
  const isProgressing = !isFailed && !awaiting_user && !isRevisionReady && !isCompleted

  return (
    <section className="production-workspace" aria-labelledby="production-workbench-title">
      <h2 id="production-workbench-title">Chapter production (V2)</h2>

      {/* Production Status summary */}
      <dl className="production-status" aria-label="Chapter production summary">
        <div>
          <dt>Status</dt>
          <dd>
            <span
              className={
                status === 'COMPLETED'
                  ? 'impact-badge outcome-passed'
                  : isFailed
                  ? 'impact-badge outcome-blocking'
                  : awaiting_user
                  ? 'impact-badge outcome-warning'
                  : 'history-status'
              }
            >
              {status}
            </span>
          </dd>
        </div>
        <div>
          <dt>Current step</dt>
          <dd>{current_node || 'None'}</dd>
        </div>
        {document_version_id && (
          <div>
            <dt>Draft version</dt>
            <dd>{document_version_id}</dd>
          </div>
        )}
        {content_hash && (
          <div>
            <dt>Content hash</dt>
            <dd><code>{content_hash.slice(0, 16)}…</code></dd>
          </div>
        )}
      </dl>

      {error && <FocusedError error={error} />}

      {/* 1. Progressing / Polling State */}
      {isProgressing && (
        <div className="analysis-progress" role="region" aria-labelledby="progress-title" aria-live="polite">
          <div className="progress-mark" aria-hidden="true" />
          <div>
            <h3 id="progress-title" style={{ margin: 0 }}>
              {status === 'DRAFTING'
                ? 'Drafting chapter in progress…'
                : status === 'REVIEW_REVISION'
                ? 'Applying review revision…'
                : status === 'ARCHIVE_UPDATE'
                ? 'Updating chapter archive…'
                : 'Processing next workflow step…'}
            </h3>
            <p className="muted">The server is executing the automated step. This view updates automatically.</p>
          </div>
        </div>
      )}

      {/* 2. Author Revision State */}
      {isAuthorRevision && (
        <section className="approval-panel" role="region" aria-labelledby="author-revision-title">
          <h3 id="author-revision-title">Author revision required</h3>
          <p className="muted">
            Initial draft has been generated. Inspect the draft details and select your revision decision.
          </p>

          <div className="version-preview" role="region" aria-label="Current draft details">
            <dl className="metadata">
              <div>
                <dt>Document ID</dt>
                <dd><code>{document_id}</code></dd>
              </div>
              <div>
                <dt>Version ID</dt>
                <dd><code>{document_version_id}</code></dd>
              </div>
              {content_hash && (
                <div>
                  <dt>Content SHA-256</dt>
                  <dd><code>{content_hash}</code></dd>
                </div>
              )}
            </dl>
          </div>

          {/* Mode Tabs */}
          <div className="document-tabs" role="tablist" aria-label="Author revision options">
            <button
              type="button"
              role="tab"
              className={authorActionType === 'accept' ? 'primary-button' : 'secondary-button'}
              onClick={() => setAuthorActionType('accept')}
              aria-selected={authorActionType === 'accept'}
            >
              Accept draft
            </button>
            <button
              type="button"
              role="tab"
              className={authorActionType === 'feedback' ? 'primary-button' : 'secondary-button'}
              onClick={() => setAuthorActionType('feedback')}
              aria-selected={authorActionType === 'feedback'}
            >
              Propose feedback revision
            </button>
            <button
              type="button"
              role="tab"
              className={authorActionType === 'manual' ? 'primary-button' : 'secondary-button'}
              onClick={() => setAuthorActionType('manual')}
              aria-selected={authorActionType === 'manual'}
            >
              Submit manual edit
            </button>
          </div>

          {/* Accept Mode */}
          {authorActionType === 'accept' && (
            <div className="approval-actions" style={{ marginTop: '16px' }}>
              <button
                type="button"
                onClick={() => void handleResolveAuthorAction()}
                disabled={submitting}
                aria-busy={submitting}
              >
                {submitting ? 'Accepting draft…' : 'Confirm accept draft'}
              </button>
            </div>
          )}

          {/* Feedback Mode */}
          {authorActionType === 'feedback' && (
            <form onSubmit={handleResolveAuthorAction} className="workspace-form" aria-label="Feedback revision form" style={{ marginTop: '16px' }}>
              <label>
                Revision feedback
                <textarea
                  required
                  aria-label="Revision feedback"
                  placeholder="Provide concrete instructions for what the writer agent should revise…"
                  value={feedbackInput}
                  onChange={(e) => setFeedbackInput(e.target.value)}
                />
              </label>
              <label>
                Target segment IDs (optional, UUID list)
                <input
                  type="text"
                  placeholder="e.g. 11111111-1111-4111-8111-111111111111"
                  value={targetSegmentsInput}
                  onChange={(e) => setTargetSegmentsInput(e.target.value)}
                />
              </label>
              <button type="submit" disabled={submitting || !feedbackInput.trim()} aria-busy={submitting}>
                {submitting ? 'Submitting feedback revision…' : 'Submit feedback revision'}
              </button>
            </form>
          )}

          {/* Manual Edit Mode */}
          {authorActionType === 'manual' && (
            <form onSubmit={handleResolveAuthorAction} className="workspace-form" aria-label="Manual edit form" style={{ marginTop: '16px' }}>
              <label>
                Manual draft content
                <textarea
                  required
                  aria-label="Manual draft content"
                  placeholder="Edit or replace the complete draft markdown here…"
                  value={manualContentInput}
                  onChange={(e) => setManualContentInput(e.target.value)}
                />
              </label>
              <button type="submit" disabled={submitting || !manualContentInput.trim()} aria-busy={submitting}>
                {submitting ? 'Saving manual edit…' : 'Save manual edit'}
              </button>
            </form>
          )}
        </section>
      )}

      {/* 3. Review Stage State */}
      {isReviewStage && (
        <section className="approval-panel" role="region" aria-labelledby="review-stage-title">
          <h3 id="review-stage-title">
            {status === 'EDITOR_REVIEW'
              ? 'Editor review stage'
              : status === 'CHIEF_FINAL_REVIEW'
              ? 'Chief editor final review'
              : 'Lore & continuity final review'}
          </h3>

          {!awaiting_user && (
            <div>
              <p className="muted">Review has not been executed for this stage yet.</p>
              <button
                type="button"
                onClick={handleTriggerReview}
                disabled={submitting}
                aria-busy={submitting}
              >
                {submitting ? 'Triggering review…' : 'Trigger chapter review'}
              </button>
            </div>
          )}

          {awaiting_user && (
            <div>
              {action_kind === 'review_revision' ? (
                <div className="maintenance-error" role="alert" style={{ marginBottom: '16px' }}>
                  <p><strong>Blocking review findings detected:</strong> Revisions are required before finalization.</p>
                </div>
              ) : (
                <div className="handoff-notice" role="region" style={{ marginBottom: '16px' }}>
                  <p><strong>Review warnings found:</strong> You can proceed with warnings or request revision.</p>
                </div>
              )}

              {/* Distinguish warning vs blocking actions */}
              {action_kind === 'review_warning' && (
                <div className="document-tabs" role="tablist" aria-label="Review warning options" style={{ marginBottom: '16px' }}>
                  <button
                    type="button"
                    role="tab"
                    className={reviewActionType === 'proceed' ? 'primary-button' : 'secondary-button'}
                    onClick={() => setReviewActionType('proceed')}
                    aria-selected={reviewActionType === 'proceed'}
                  >
                    Proceed with warnings
                  </button>
                  <button
                    type="button"
                    role="tab"
                    className={reviewActionType === 'revision' ? 'primary-button' : 'secondary-button'}
                    onClick={() => setReviewActionType('revision')}
                    aria-selected={reviewActionType === 'revision'}
                  >
                    Request revision
                  </button>
                </div>
              )}

              {(action_kind === 'review_warning' && reviewActionType === 'proceed') ? (
                <div className="approval-actions">
                  <button
                    type="button"
                    onClick={() => void handleResolveReviewAction()}
                    disabled={submitting}
                    aria-busy={submitting}
                  >
                    {submitting ? 'Confirming proceed with warnings…' : 'Confirm proceed with warnings'}
                  </button>
                </div>
              ) : (
                <form onSubmit={handleResolveReviewAction} className="workspace-form" aria-label="Review revision form">
                  <label>
                    Target segment IDs (optional, UUID list)
                    <input
                      type="text"
                      placeholder="e.g. 11111111-1111-4111-8111-111111111111"
                      value={targetSegmentsInput}
                      onChange={(e) => setTargetSegmentsInput(e.target.value)}
                    />
                  </label>
                  <button type="submit" disabled={submitting} aria-busy={submitting}>
                    {submitting ? 'Submitting review revision…' : 'Request review revision'}
                  </button>
                </form>
              )}
            </div>
          )}
        </section>
      )}

      {/* 4. Revision Ready State */}
      {isRevisionReady && (
        <section className="approval-panel" role="region" aria-labelledby="revision-ready-title">
          <h3 id="revision-ready-title">Revision ready for finalization</h3>
          <p className="muted">All review criteria have passed or were accepted. The chapter is ready to finalize.</p>
          <div className="version-preview" role="region" aria-label="Final version details" style={{ marginBottom: '16px' }}>
            <dl className="metadata">
              <div>
                <dt>Reviewed version</dt>
                <dd><code>{document_version_id}</code></dd>
              </div>
              {content_hash && (
                <div>
                  <dt>Content hash</dt>
                  <dd><code>{content_hash}</code></dd>
                </div>
              )}
            </dl>
          </div>
          <button
            type="button"
            onClick={handleFinalize}
            disabled={submitting}
            aria-busy={submitting}
          >
            {submitting ? 'Finalizing chapter…' : 'Finalize chapter'}
          </button>
        </section>
      )}

      {/* 5. Completed State */}
      {isCompleted && (
        <section className="approval-panel" role="region" aria-labelledby="completed-title">
          <h3 id="completed-title">Chapter production completed</h3>
          <p className="muted">This chapter has been produced and finalized into the canonical story archive.</p>
          <div className="version-preview" role="region" aria-label="Final document details">
            <dl className="metadata">
              <div>
                <dt>Final document</dt>
                <dd><code>{document_id}</code></dd>
              </div>
              <div>
                <dt>Final version</dt>
                <dd><code>{document_version_id}</code></dd>
              </div>
            </dl>
          </div>
        </section>
      )}

      {/* 6. Failed / Recovery State */}
      {isFailed && (
        <div className="maintenance-error" role="alert" aria-labelledby="failure-title">
          <h3 id="failure-title" style={{ margin: 0 }}>Chapter production encountered an issue</h3>
          <p>{sanitizeFailureCode(failure_code, failed_from_status)}</p>

          <div className="decision-actions" style={{ marginTop: '8px' }}>
            {(failure_code === 'reconciliation_required' ||
              failure_code === 'document_commit_indeterminate' ||
              failure_code === 'persistence_unavailable' ||
              failure_code === 'archive_unavailable') && (
              <button
                type="button"
                onClick={handleReconcile}
                disabled={submitting}
                aria-busy={submitting}
              >
                {submitting ? 'Reconciling state…' : 'Reconcile state'}
              </button>
            )}

            {(status === 'FAILED' ||
              failure_code === 'provider_unavailable' ||
              failure_code === 'provider_timeout' ||
              failure_code === 'invalid_provider_output') && (
              <button
                type="button"
                className="secondary-button"
                onClick={handleResume}
                disabled={submitting}
                aria-busy={submitting}
              >
                {submitting ? 'Resuming drafting…' : 'Resume drafting'}
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
