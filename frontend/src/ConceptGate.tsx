import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ApiError,
  getProjectCreationRun,
  resolveProjectCreationAction,
  type ProjectCreationConceptOption,
  type ProjectCreationRun,
} from './api/client'
import ConceptSelection from './ConceptSelection'

// ── Sub-components ─────────────────────────────────────────────────

function SkeletonCard() {
  return (
    <div className="concept-card skeleton" aria-hidden>
      <div className="skeleton-line skeleton-title" />
      <div className="skeleton-line skeleton-text" />
      <div className="skeleton-line skeleton-text short" />
      <div className="skeleton-tags">
        <div className="skeleton-tag" />
        <div className="skeleton-tag" />
      </div>
    </div>
  )
}

function ConceptCard({ option }: { option: ProjectCreationConceptOption }) {
  return (
    <article className="concept-card display">
      <h3 className="concept-title">{option.title}</h3>
      <p className="concept-logline">{option.logline}</p>
      <p className="concept-premise">{option.premise}</p>
      <div className="concept-genres" aria-label="类型标签">
        {option.genres.map((genre) => (
          <span key={genre} className="genre-tag">{genre}</span>
        ))}
      </div>
    </article>
  )
}

function SeverityBadge({ severity }: { severity: string }) {
  const map: Record<string, { label: string; className: string }> = {
    clean: { label: '审查通过', className: 'badge-success' },
    warning: { label: '有建议', className: 'badge-warning' },
    blocking: { label: '需要修改', className: 'badge-danger' },
  }
  const entry = map[severity]
  if (!entry) return null
  return <span className={`severity-badge ${entry.className}`} role="status">{entry.label}</span>
}

function LoadingView() {
  return (
    <section className="concept-gate" aria-busy="true">
      <p className="muted">加载审核关卡…</p>
      <div className="concept-grid">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    </section>
  )
}

function ErrorView({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <section className="concept-gate">
      <p className="notice" role="alert">{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="secondary-button">
          重试
        </button>
      )}
    </section>
  )
}

function RegenerationControls({
  projectId,
  workflowRunId,
  actionId,
  allowedDecisions,
  onResolved,
}: {
  projectId: string
  workflowRunId: string
  actionId: string
  allowedDecisions: string[]
  onResolved: () => Promise<void>
}) {
  const [feedback, setFeedback] = useState('')
  const [resolving, setResolving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const canRegenerate = allowedDecisions.includes('regenerate')
  const canSendFeedback = allowedDecisions.includes('feedback')
  const normalizedFeedback = feedback.trim()
  const feedbackLength = Array.from(normalizedFeedback).length
  const feedbackIsValid = feedbackLength >= 1 && feedbackLength <= 1000

  async function resolve(body: { decision: 'regenerate' } | { decision: 'feedback'; feedback: string }) {
    if (resolving) return
    setResolving(true)
    setError(null)
    try {
      await resolveProjectCreationAction(projectId, workflowRunId, actionId, body)
      await onResolved()
    } catch {
      setError('操作未完成，请稍后重试。')
    } finally {
      setResolving(false)
    }
  }

  return (
    <div className="regeneration-controls">
      {error && <p className="notice" role="alert">{error}</p>}
      {resolving && <p className="muted" role="status">正在生成新的概念…</p>}
      {canRegenerate && (
        <button type="button" onClick={() => void resolve({ decision: 'regenerate' })} disabled={resolving}>
          重新生成概念
        </button>
      )}
      {canSendFeedback && (
        <form onSubmit={(event) => {
          event.preventDefault()
          if (feedbackIsValid) void resolve({ decision: 'feedback', feedback: normalizedFeedback })
        }}>
          <label>
            给编辑的反馈
            <textarea
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              rows={3}
              disabled={resolving}
            />
          </label>
          <span className="muted">{feedbackLength} / 1000</span>
          <button type="submit" disabled={resolving || !feedbackIsValid}>
            提交反馈并重新生成
          </button>
        </form>
      )}
    </div>
  )
}

function GateView({
  run,
  projectId,
  onResolved,
  onRegenerationResolved,
}: {
  run: ProjectCreationRun
  projectId: string
  onResolved: () => void
  onRegenerationResolved: () => Promise<void>
}) {
  const action = run.pending_action
  const concepts = action?.concept_options ?? []
  const canChoose = action?.type === 'project_creation_concept_selection'
    && action.status === 'pending'
    && (action.allowed_decisions.includes('select') || action.allowed_decisions.includes('fuse'))
  const severity = action?.review_severity ?? null

  return (
    <section className="concept-gate">
      <div className="gate-header">
        <p className="eyebrow">概念审核关卡</p>
        <h1>概念方案</h1>
        {severity && <SeverityBadge severity={severity} />}
      </div>

      {severity === 'blocking' && action && (
        <div className="gate-blocking" role="alert">
          <h2>需要修改</h2>
          <p className="muted">首席编辑认为当前概念方案存在问题，需要重新生成。</p>
          {action.blocking_issues.length > 0 && (
            <ul aria-label="需要处理的问题">
              {action.blocking_issues.map((issue) => (
                <li key={issue.code}><strong>{issue.code}</strong>：{issue.message}</li>
              ))}
            </ul>
          )}
          <RegenerationControls
            projectId={projectId}
            workflowRunId={run.id}
            actionId={action.id}
            allowedDecisions={action.allowed_decisions}
            onResolved={onRegenerationResolved}
          />
        </div>
      )}

      {severity === 'warning' && (
        <div className="gate-warnings" role="alert">
          <h2>有建议</h2>
          <p className="muted">审核通过，但首席编辑提供了改进建议。</p>
        </div>
      )}

      {canChoose && action ? (
        <ConceptSelection
          projectId={projectId}
          workflowRunId={run.id}
          actionId={action.id}
          allowedDecisions={action.allowed_decisions}
          options={concepts}
          onResolved={onResolved}
        />
      ) : (
        <div className="concept-grid" aria-label="概念方案列表">
          {concepts.map((option) => (
            <ConceptCard key={option.id} option={option} />
          ))}
        </div>
      )}
    </section>
  )
}

// ── Main component ─────────────────────────────────────────────────

const requestError = '无法加载审核关卡，请检查网络后重试。'
const notFoundError = '工作流未找到'

function hasExactDecisions(actual: readonly string[], expected: readonly string[]): boolean {
  return actual.length === expected.length && expected.every((decision) => actual.includes(decision))
}

function hasUsableConceptOptions(options: readonly ProjectCreationConceptOption[]): boolean {
  return options.length > 0 && new Set(options.map((option) => option.id)).size === options.length
}

function isRenderableGateState(run: ProjectCreationRun, expectedRunId: string): boolean {
  const action = run.pending_action
  if (
    run.id !== expectedRunId
    || run.type !== 'project_creation'
    || run.next_node !== null
    || !run.awaiting_user
    || !action
    || action.status !== 'pending'
  ) {
    return false
  }

  if (
    run.status === 'concept_options'
    && run.current_node === 'concept_review'
    && action.type === 'project_creation_concept_selection'
  ) {
    return (
      hasExactDecisions(action.allowed_decisions, ['select', 'fuse'])
      && (action.review_severity === 'clean' || action.review_severity === 'warning')
      && action.blocking_issues.length === 0
      && hasUsableConceptOptions(action.concept_options)
    )
  }

  if (
    run.status === 'revision_required'
    && run.current_node === 'concept_revision'
    && action.type === 'project_creation_concept_regeneration'
  ) {
    return (
      hasExactDecisions(action.allowed_decisions, ['regenerate', 'feedback'])
      && action.review_severity === 'blocking'
      && action.blocking_issues.length > 0
      && hasUsableConceptOptions(action.concept_options)
    )
  }

  return false
}

export default function ConceptGate({ projectId, workflowRunId }: { projectId: string; workflowRunId: string }) {
  const navigate = useNavigate()
  const [run, setRun] = useState<ProjectCreationRun | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showRetry, setShowRetry] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fetchEpochRef = useRef(0)
  const routeEpochRef = useRef(0)
  const mountedRef = useRef(false)
  const fetchGateRef = useRef<((expectedRouteEpoch?: number) => Promise<void>) | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const fetchGateState = useCallback(async (expectedRouteEpoch = routeEpochRef.current) => {
    if (expectedRouteEpoch !== routeEpochRef.current) return
    const fetchEpoch = ++fetchEpochRef.current
    try {
      const fetched = await getProjectCreationRun(projectId, workflowRunId)
      if (
        !mountedRef.current
        || expectedRouteEpoch !== routeEpochRef.current
        || fetchEpoch !== fetchEpochRef.current
      ) return
      stopPolling()
      const renderable = isRenderableGateState(fetched, workflowRunId)
      setRun(fetched)
      setLoading(false)
      setError(null)
      setShowRetry(false)

      if (
        renderable
        && fetched.pending_action?.type === 'project_creation_concept_regeneration'
      ) {
        pollRef.current = setInterval(() => {
          void fetchGateRef.current?.(expectedRouteEpoch)
        }, 5000)
      }
    } catch (caught: unknown) {
      if (
        !mountedRef.current
        || expectedRouteEpoch !== routeEpochRef.current
        || fetchEpoch !== fetchEpochRef.current
      ) return
      stopPolling()
      setLoading(false)
      if (caught instanceof ApiError && caught.status === 404) {
        setError(notFoundError)
        setShowRetry(false)
      } else {
        setError(requestError)
        setShowRetry(true)
      }
    }
  }, [projectId, workflowRunId, stopPolling])

  // Keep fetchGateRef current so the setInterval closure always calls the latest version
  useEffect(() => {
    fetchGateRef.current = fetchGateState
  })

  useEffect(() => {
    const routeEpoch = ++routeEpochRef.current
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    setError(null)
    setRun(null)
    stopPolling()
    void fetchGateState(routeEpoch)
    return () => {
      routeEpochRef.current += 1
      fetchEpochRef.current += 1
      stopPolling()
    }
  }, [projectId, workflowRunId]) // eslint-disable-line react-hooks/exhaustive-deps

  const renderedRouteEpoch = routeEpochRef.current

  if (loading) return <LoadingView />
  if (error) return <ErrorView message={error} onRetry={showRetry ? () => { setLoading(true); void fetchGateState() } : undefined} />

  if (!run) return <ErrorView message={requestError} onRetry={() => { setLoading(true); void fetchGateState() }} />

  if (!isRenderableGateState(run, workflowRunId)) {
    return <ErrorView message="工作流状态无法继续，请返回项目。" />
  }

  return (
    <GateView
      run={run}
      projectId={projectId}
      onResolved={() => navigate(`/projects/${projectId}`)}
      onRegenerationResolved={() => fetchGateState(renderedRouteEpoch)}
    />
  )
}
