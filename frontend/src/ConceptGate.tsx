import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ApiError,
  getProjectCreationRun,
  readDocumentContent,
  type ProjectCreationRun,
} from './api/client'

// ── Concept parsing ────────────────────────────────────────────────

interface ConceptOption {
  id: string
  title: string
  logline: string
  premise: string
  genres: string[]
}

const CONCEPT_REGEX =
  /^## Option `(?<id>[a-z][a-z0-9-]{0,63})`: (?<title>[^\n]+)\n\n(?<logline>[^\n]+)\n\n(?<premise>[^\n]+)\n\nGenres: (?<genres>[^\n]+)$/gm

function parseConceptMarkdown(content: string): ConceptOption[] {
  const options: ConceptOption[] = []
  let match: RegExpExecArray | null
  while ((match = CONCEPT_REGEX.exec(content)) !== null) {
    const groups = match.groups as { id: string; title: string; logline: string; premise: string; genres: string }
    options.push({
      id: groups.id,
      title: groups.title,
      logline: groups.logline,
      premise: groups.premise,
      genres: groups.genres.split(',').map((g) => g.trim()).filter(Boolean),
    })
  }
  return options
}

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

function ConceptCard({ option }: { option: ConceptOption }) {
  return (
    <article className="concept-card">
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
      <Link className="back-link" to="/">返回项目列表</Link>
    </section>
  )
}

function GateView({ concepts, severity }: { concepts: ConceptOption[]; severity: string | null }) {
  return (
    <section className="concept-gate">
      <div className="gate-header">
        <p className="eyebrow">概念审核关卡</p>
        <h1>概念方案</h1>
        {severity && <SeverityBadge severity={severity} />}
      </div>

      {severity === 'blocking' && (
        <div className="gate-blocking" role="alert">
          <h2>需要修改</h2>
          <p className="muted">首席编辑认为当前概念方案存在问题，需要重新生成。</p>
        </div>
      )}

      {severity === 'warning' && (
        <div className="gate-warnings" role="alert">
          <h2>有建议</h2>
          <p className="muted">审核通过，但首席编辑提供了改进建议。</p>
        </div>
      )}

      <div className="concept-grid" aria-label="概念方案列表">
        {concepts.map((option) => (
          <ConceptCard key={option.id} option={option} />
        ))}
      </div>
    </section>
  )
}

// ── Main component ─────────────────────────────────────────────────

const requestError = '无法加载审核关卡，请检查网络后重试。'
const notFoundError = '工作流未找到'

export default function ConceptGate({ projectId, workflowRunId }: { projectId: string; workflowRunId: string }) {
  const [run, setRun] = useState<ProjectCreationRun | null>(null)
  const [concepts, setConcepts] = useState<ConceptOption[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showRetry, setShowRetry] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const mountedRef = useRef(false)
  const fetchGateRef = useRef<() => Promise<void>>()

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

  const fetchGateState = useCallback(async () => {
    try {
      const fetched = await getProjectCreationRun(projectId, workflowRunId)
      if (!mountedRef.current) return
      setRun(fetched)
      setLoading(false)
      setError(null)
      setShowRetry(false)

      const docId = fetched.pending_action?.concept_document_id
      if (docId) {
        try {
          const content = await readDocumentContent(docId)
          if (mountedRef.current) {
            setConcepts(parseConceptMarkdown(content.content))
          }
        } catch {
          // concepts will remain null; cards won't render
        }
      }

      stopPolling()
      if (
        fetched.awaiting_user &&
        fetched.pending_action?.type === 'project_creation_concept_regeneration'
      ) {
        pollRef.current = setInterval(() => {
          void fetchGateRef.current?.()
        }, 5000)
      }
    } catch (caught: unknown) {
      if (!mountedRef.current) return
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
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    setError(null)
    setRun(null)
    setConcepts(null)
    stopPolling()
    void fetchGateState()
    return () => { stopPolling() }
  }, [projectId, workflowRunId]) // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <LoadingView />
  if (error) return <ErrorView message={error} onRetry={showRetry ? () => { setLoading(true); void fetchGateState() } : undefined} />

  const severity = run?.pending_action?.review_severity ?? null

  return (
    <GateView
      concepts={concepts ?? []}
      severity={severity}
    />
  )
}
