import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import {
  ApiError,
  createChapter,
  createProject,
  getChapter,
  getDocument,
  getProject,
  listChapters,
  listDocumentVersions,
  listProjects,
  readDocumentContent,
  readDocumentVersionContent,
  restoreDocument,
  resolveChapterProductionAction,
  startChapterProduction,
  writeDocument,
  type Chapter,
  type ChapterProductionAction,
  type ChapterProductionEvent,
  type ChapterProductionRun,
  type Document,
  type DocumentContent,
  type DocumentVersion,
  type Project,
} from './api/client'
import ConceptGate from './ConceptGate'
import ProjectCreationForm from './ProjectCreationForm'
import ProjectMaintenancePage from './ProjectMaintenancePage'

const requestError = 'This workspace could not be loaded. Try again.'

function LoadError({ children = requestError }: { children?: string }) {
  return <p className="notice" role="alert">{children}</p>
}

function ProjectForm() {
  const navigate = useNavigate()
  const [slug, setSlug] = useState('')
  const [title, setTitle] = useState('')
  const [genre, setGenre] = useState('')
  const [platform, setPlatform] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending) return
    if (!slug.trim() || !title.trim()) {
      setError('Slug and title are required.')
      return
    }
    setPending(true)
    setError(null)
    try {
      const project = await createProject({
        slug: slug.trim(), title: title.trim(), genre: genre.trim() || null, target_platform: platform.trim() || null,
      })
      navigate(`/projects/${project.id}`)
    } catch {
      setError('Project could not be created. Try again.')
    } finally {
      setPending(false)
    }
  }

  return (
    <form className="workspace-form" onSubmit={submit} aria-label="Create project">
      <h2>Create project</h2>
      <div className="form-grid">
        <label>Slug<input value={slug} onChange={(event) => setSlug(event.target.value)} required /></label>
        <label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label>
        <label>Genre (optional)<input value={genre} onChange={(event) => setGenre(event.target.value)} /></label>
        <label>Target platform (optional)<input value={platform} onChange={(event) => setPlatform(event.target.value)} /></label>
      </div>
      {error && <LoadError>{error}</LoadError>}
      <button type="submit" disabled={pending}>{pending ? 'Creating project…' : 'Create project'}</button>
    </form>
  )
}

function ProjectListPage() {
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    listProjects().then(
      (result) => { if (active) setProjects(result) },
      () => { if (active) setFailed(true) },
    )
    return () => { active = false }
  }, [])

  return (
    <section className="page" aria-labelledby="route-title">
      <p className="eyebrow">Drafting desk</p>
      <h1 id="route-title">Projects</h1>
      {projects === null && !failed && <p className="muted">Loading projects…</p>}
      {failed && <LoadError>Projects could not be loaded. Try again.</LoadError>}
      {projects?.length === 0 && <p className="muted">No projects yet. Create one to begin.</p>}
      {projects && projects.length > 0 && (
        <ul className="workspace-list" aria-label="Projects">
          {projects.map((project) => <li key={project.id}><Link to={`/projects/${project.id}`}>{project.title}</Link></li>)}
        </ul>
      )}
      <ProjectForm />
    </section>
  )
}

function ChapterForm({ projectId }: { projectId: string }) {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending) return
    setPending(true)
    setError(null)
    try {
      const chapter = await createChapter(projectId, { title: title.trim() || null })
      navigate(`/projects/${projectId}/chapters/${chapter.id}`)
    } catch {
      setError('Chapter could not be created. Try again.')
    } finally {
      setPending(false)
    }
  }

  return (
    <form className="workspace-form" onSubmit={submit} aria-label="Create chapter">
      <h2>Create chapter</h2>
      <label>Chapter title<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
      {error && <LoadError>{error}</LoadError>}
      <button type="submit" disabled={pending}>{pending ? 'Creating chapter…' : 'Create chapter'}</button>
    </form>
  )
}

function ProjectWorkspace() {
  const { projectId = '' } = useParams()
  const [project, setProject] = useState<Project | null>(null)
  const [chapters, setChapters] = useState<Chapter[] | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    Promise.all([getProject(projectId), listChapters(projectId)]).then(
      ([loadedProject, loadedChapters]) => {
        if (!active) return
        setProject(loadedProject)
        setChapters(loadedChapters)
      },
      () => { if (active) setFailed(true) },
    )
    return () => { active = false }
  }, [projectId])

  if (failed) return <section className="page"><LoadError /></section>
  if (!project || !chapters) return <section className="page"><p className="muted">Loading project…</p></section>
  return (
    <section className="page" aria-labelledby="route-title">
      <p className="eyebrow">Project workspace</p>
      <h1 id="route-title">{project.title}</h1>
      <dl className="metadata">
        <div><dt>Status</dt><dd>{project.status}</dd></div>
        {project.genre && <div><dt>Genre</dt><dd>{project.genre}</dd></div>}
        {project.target_platform && <div><dt>Target platform</dt><dd>{project.target_platform}</dd></div>}
      </dl>
      <Link to={`/projects/${projectId}/creation/start`}>开始构思</Link>
      <Link to={`/projects/${projectId}/maintenance/start`}>Project maintenance</Link>
      <section aria-labelledby="chapters-title">
        <h2 id="chapters-title">Chapters</h2>
        {chapters.length === 0 ? <p className="muted">No chapters yet.</p> : (
          <ul className="workspace-list">
            {chapters.map((chapter) => (
              <li key={chapter.id}><Link to={`/projects/${projectId}/chapters/${chapter.id}`}>Chapter {chapter.chapter_number}</Link><span>{chapter.title || 'Untitled chapter'} · {chapter.status}</span></li>
            ))}
          </ul>
        )}
      </section>
      <ChapterForm projectId={projectId} />
    </section>
  )
}

function ChapterWorkspace() {
  const { projectId = '', chapterId = '' } = useParams()

  return <ChapterWorkspaceContent key={`${projectId}:${chapterId}`} projectId={projectId} chapterId={chapterId} />
}

function ChapterWorkspaceContent({ projectId, chapterId }: { projectId: string, chapterId: string }) {
  const [project, setProject] = useState<Project | null>(null)
  const [chapter, setChapter] = useState<Chapter | null>(null)
  const [failed, setFailed] = useState(false)
  const [run, setRun] = useState<ChapterProductionRun | null>(null)
  const [starting, setStarting] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [productionError, setProductionError] = useState<string | null>(null)
  const startingRef = useRef(false)
  const resolvingRef = useRef(false)
  const mountedRef = useRef(false)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    let active = true
    Promise.all([getProject(projectId), getChapter(projectId, chapterId)]).then(
      ([loadedProject, loadedChapter]) => {
        if (!active) return
        setProject(loadedProject)
        setChapter(loadedChapter)
      },
      () => { if (active) setFailed(true) },
    )
    return () => {
      active = false
    }
  }, [chapterId, projectId])

  async function startProduction() {
    if (startingRef.current || run) return
    startingRef.current = true
    setStarting(true)
    setProductionError(null)
    try {
      const startedRun = await startChapterProduction(projectId, chapterId)
      if (mountedRef.current) {
        setRun(startedRun)
        setChapter((currentChapter) => currentChapter && {
          ...currentChapter,
          current_outline_document_id: startedRun.outline_document_id,
          current_draft_document_id: startedRun.draft_document_id,
        })
      }
    } catch {
      if (mountedRef.current) setProductionError('Chapter production could not be started. Try again.')
    } finally {
      if (mountedRef.current) {
        startingRef.current = false
        setStarting(false)
      }
    }
  }

  async function resolveApproval(action: ChapterProductionAction, decision: 'approved' | 'rejected') {
    if (!run || resolvingRef.current) return
    resolvingRef.current = true
    setResolving(true)
    setProductionError(null)
    try {
      const resolvedRun = await resolveChapterProductionAction(projectId, chapterId, run.id, action.id, { decision })
      if (mountedRef.current) setRun(resolvedRun)
    } catch {
      if (mountedRef.current) setProductionError('Chapter production approval could not be resolved. Try again.')
    } finally {
      if (mountedRef.current) {
        resolvingRef.current = false
        setResolving(false)
      }
    }
  }

  if (failed) return <section className="page"><LoadError /></section>
  if (!project || !chapter) return <section className="page"><p className="muted">Loading chapter…</p></section>
  return (
    <section className="page" aria-labelledby="route-title">
      <Link className="back-link" to={`/projects/${projectId}`}>Back to {project.title}</Link>
      <p className="eyebrow">Chapter {chapter.chapter_number}</p>
      <h1 id="route-title">{chapter.title || 'Untitled chapter'}</h1>
      <p className="muted">Status: {chapter.status}</p>
      <ChapterDocuments chapter={chapter} />
      <section className="production-workspace" aria-labelledby="production-title">
        <h2 id="production-title">Chapter production</h2>
        {!run && <p className="muted">Chapter production has not started yet.</p>}
        <button type="button" onClick={startProduction} disabled={starting || run !== null}>
          {starting ? 'Starting chapter production…' : 'Start chapter production'}
        </button>
        {productionError && <LoadError>{productionError}</LoadError>}
        {run && <ProductionRun run={run} resolving={resolving} onResolve={resolveApproval} />}
      </section>
    </section>
  )
}

const documentRoles = [
  ['current_outline_document_id', 'Outline'],
  ['current_draft_document_id', 'Draft'],
  ['final_document_id', 'Final'],
  ['summary_document_id', 'Summary'],
] as const

type DocumentRole = typeof documentRoles[number][0]

function ChapterDocuments({ chapter }: { chapter: Chapter }) {
  const documents = documentRoles.flatMap(([field, label]) => {
    const id = chapter[field as DocumentRole]
    return id ? [{ id, label }] : []
  })
  const [selectedId, setSelectedId] = useState(documents[0]?.id ?? '')

  if (documents.length === 0) return <section className="document-workspace" aria-labelledby="documents-title"><h2 id="documents-title">Documents</h2><p className="muted">No server documents are available for this chapter.</p></section>
  const selected = documents.find((item) => item.id === selectedId) ?? documents[0]
  return <section className="document-workspace" aria-labelledby="documents-title">
    <h2 id="documents-title">Documents</h2>
    <div className="document-tabs" aria-label="Available documents">
      {documents.map((item) => <button type="button" className="secondary-button" key={item.id} onClick={() => setSelectedId(item.id)} aria-pressed={item.id === selected.id}>{item.label}</button>)}
    </div>
    <DocumentEditor key={selected.id} documentId={selected.id} fallbackTitle={selected.label} />
  </section>
}

function DocumentEditor({ documentId, fallbackTitle }: { documentId: string, fallbackTitle: string }) {
  const [document, setDocument] = useState<Document | null>(null)
  const [content, setContent] = useState<DocumentContent | null>(null)
  const [versions, setVersions] = useState<DocumentVersion[]>([])
  const [currentVersionId, setCurrentVersionId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [selectedVersion, setSelectedVersion] = useState<DocumentVersion | null>(null)
  const [selectedContent, setSelectedContent] = useState<DocumentContent | null>(null)
  const mountedRef = useRef(false)
  const versionSelectionRequestRef = useRef(0)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    let active = true
    Promise.all([getDocument(documentId), readDocumentContent(documentId), listDocumentVersions(documentId)]).then(
      ([loadedDocument, loadedContent, loadedVersions]) => {
        if (!active) return
        setDocument(loadedDocument)
        setContent(loadedContent)
        setVersions(loadedVersions)
        setCurrentVersionId(loadedContent.version_id)
      },
      () => { if (active) setError('Document could not be loaded. Try again.') },
    )
    return () => { active = false }
  }, [documentId])

  async function selectVersion(version: DocumentVersion) {
    const requestId = ++versionSelectionRequestRef.current
    setError(null)
    try {
      const versionContent = await readDocumentVersionContent(documentId, version.id)
      if (!mountedRef.current || requestId !== versionSelectionRequestRef.current) return
      setSelectedVersion(version)
      setSelectedContent(versionContent)
    } catch {
      if (mountedRef.current && requestId === versionSelectionRequestRef.current) setError('Document version could not be loaded. Try again.')
    }
  }

  async function save() {
    if (!content || !content.version_id || saving) return
    setSaving(true)
    setError(null)
    try {
      const saved = await writeDocument(documentId, { content: content.content, expected_current_version_id: content.version_id })
      if (!mountedRef.current) return
      setCurrentVersionId(saved.id)
      setContent((previous) => previous && { ...previous, version_id: saved.id })
      setVersions((previous) => [...previous, saved])
    } catch (caught: unknown) {
      if (!mountedRef.current) return
      setError(caught instanceof ApiError && caught.status === 409 && caught.code === 'document_version_conflict'
        ? 'This document changed on the server. Your local draft was kept.'
        : 'Document could not be saved. Try again.')
    } finally { if (mountedRef.current) setSaving(false) }
  }

  async function restore() {
    if (!selectedVersion || !currentVersionId || saving) return
    setSaving(true)
    setError(null)
    try {
      const restored = await restoreDocument(documentId, selectedVersion.id, { expected_current_version_id: currentVersionId })
      if (!mountedRef.current) return
      setCurrentVersionId(restored.id)
      setVersions((previous) => [...previous, restored])
      if (selectedContent) setContent({ document_id: documentId, version_id: restored.id, content: selectedContent.content })
    } catch {
      if (mountedRef.current) setError('Document version could not be restored. Try again.')
    } finally { if (mountedRef.current) setSaving(false) }
  }

  if (error && !document && !content) return <LoadError>{error}</LoadError>
  if (!document || !content) return <p className="muted">Loading document…</p>
  const title = document.title || fallbackTitle
  return <div className="document-editor">
    <label>{title}<textarea value={content.content} onChange={(event) => setContent({ ...content, content: event.target.value })} /></label>
    <button type="button" onClick={save} disabled={saving || !content.version_id}>{saving ? 'Saving document…' : 'Save document'}</button>
    {error && <LoadError>{error}</LoadError>}
    <section aria-labelledby="versions-title">
      <h3 id="versions-title">Versions</h3>
      <div className="version-list">{versions.map((version) => <button type="button" className="secondary-button" key={version.id} onClick={() => void selectVersion(version)}>Version {version.version_number}</button>)}</div>
      {selectedVersion && selectedContent && <div className="version-preview" role="region" aria-label="Immutable version details">
        <p>Viewing immutable server version {selectedVersion.version_number}.</p>
        <dl className="metadata">
          <div><dt>Version</dt><dd>{selectedVersion.version_number}</dd></div>
          <div><dt>Created</dt><dd>{selectedVersion.created_at}</dd></div>
          <div><dt>Source</dt><dd>{selectedVersion.source}</dd></div>
          <div><dt>Change summary</dt><dd>{selectedVersion.change_summary || 'No change summary provided.'}</dd></div>
        </dl>
        <pre>{selectedContent.content}</pre>
        <button type="button" onClick={restore} disabled={saving || !currentVersionId}>Restore version {selectedVersion.version_number}</button>
      </div>}
    </section>
  </div>
}

function isPendingApproval(action: ChapterProductionAction): boolean {
  return action.type === 'chapter_production_approval'
    && action.status === 'pending'
    && action.options.length === 2
    && action.options.includes('approved')
    && action.options.includes('rejected')
}

function safeTokenCount(value: number | undefined): number | null {
  return value !== undefined && Number.isInteger(value) && value >= 0 && value <= 1_000_000_000 ? value : null
}

function EventDetails({ event }: { event: ChapterProductionEvent }) {
  if (event.event_type === 'generation_provenance') {
    const payload = event.payload as Extract<ChapterProductionEvent['payload'], { provider_kind: string }>
    const inputTokens = safeTokenCount(payload.input_tokens)
    const outputTokens = safeTokenCount(payload.output_tokens)
    return <>
      <p>Provider: {payload.provider_kind} · Model: {payload.model_identifier} · Template: {payload.prompt_template_version}</p>
      {(inputTokens !== null || outputTokens !== null) && <p>Tokens: {inputTokens !== null ? `input ${inputTokens}` : 'input unavailable'} · {outputTokens !== null ? `output ${outputTokens}` : 'output unavailable'}</p>}
    </>
  }
  if (event.event_type === 'generation_output_stored' || event.event_type === 'fake_output_stored') {
    const payload = event.payload as Extract<ChapterProductionEvent['payload'], { outline_document_id: string }>
    return <p>Outline document: {payload.outline_document_id}</p>
  }
  if (event.event_type === 'approval_approved' || event.event_type === 'approval_rejected') {
    const payload = event.payload as Extract<ChapterProductionEvent['payload'], { decision: 'approved' | 'rejected' }>
    return <p>Decision: {payload.decision} · Action: {payload.action_id}</p>
  }
  return null
}

function ProductionRun({ run, resolving, onResolve }: { run: ChapterProductionRun, resolving: boolean, onResolve: (action: ChapterProductionAction, decision: 'approved' | 'rejected') => void }) {
  const action = run.awaiting_user ? run.actions.find(isPendingApproval) : undefined
  return (
    <div className="production-run">
      <dl className="production-status">
        <div><dt>Run status</dt><dd>{run.status}</dd></div>
        <div><dt>Current node</dt><dd>{run.current_node || 'None'}</dd></div>
        <div><dt>Next node</dt><dd>{run.next_node || 'None'}</dd></div>
      </dl>
      <section aria-labelledby="timeline-title">
        <h3 id="timeline-title">Timeline</h3>
        <ol className="production-timeline">
          {run.events.map((event, index) => <li key={`${event.event_type}-${index}`}>
            <p>{event.message || 'Workflow event recorded.'}</p>
            {event.node_name && <p className="muted">Node: {event.node_name}</p>}
            <EventDetails event={event} />
          </li>)}
        </ol>
      </section>
      {action && <section className="approval-panel" aria-labelledby="approval-title">
        <h3 id="approval-title">Approval required</h3>
        <p className="muted">Choose how this production run should continue.</p>
        <div className="approval-actions">
          <button type="button" onClick={() => onResolve(action, 'approved')} disabled={resolving}>Approve</button>
          <button type="button" className="secondary-button" onClick={() => onResolve(action, 'rejected')} disabled={resolving}>Reject</button>
        </div>
      </section>}
    </div>
  )
}

function ConceptGatePage() {
  const { projectId = '', workflowRunId = '' } = useParams()

  return (
    <section className="page" aria-labelledby="route-title">
      <Link className="back-link" to={`/projects/${projectId}`}>返回项目</Link>
      <ConceptGate projectId={projectId} workflowRunId={workflowRunId} />
    </section>
  )
}

function ProjectCreationPage() {
  const { projectId = '' } = useParams()
  return (
    <section className="page" aria-labelledby="route-title">
      <h1 id="route-title">开始创作</h1>
      <p className="muted">描述你的创作灵感，开始智能创作流程。</p>
      <ProjectCreationForm projectId={projectId} />
    </section>
  )
}

function NotFound() {
  return <section className="page" aria-labelledby="route-title"><h1 id="route-title">Page not found</h1><p className="muted">The requested workspace does not exist.</p><Link to="/">Return to projects</Link></section>
}

export default function App() {
  return (
    <div className="app-shell">
      <header className="topbar" aria-label="GuraNovel workbench"><Link className="wordmark" to="/">GuraNovel</Link><span className="workspace-name">Creative workbench</span></header>
      <div className="workspace">
        <nav aria-label="Workbench navigation"><Link to="/">Projects</Link><span>Approvals</span><span>Documents</span></nav>
        <main><Routes><Route path="/" element={<ProjectListPage />} /><Route path="/projects/:projectId" element={<ProjectWorkspace />} /><Route path="/projects/:projectId/chapters/:chapterId" element={<ChapterWorkspace />} /><Route path="/projects/:projectId/creation/start" element={<ProjectCreationPage />} /><Route path="/projects/:projectId/creation/:workflowRunId/gate" element={<ConceptGatePage />} /><Route path="/projects/:projectId/maintenance/start" element={<ProjectMaintenancePage mode="start" />} /><Route path="/projects/:projectId/maintenance/:workflowRunId/status" element={<ProjectMaintenancePage mode="handoff" />} /><Route path="/projects/:projectId/maintenance/:workflowRunId" element={<ProjectMaintenancePage mode="gate" />} /><Route path="*" element={<NotFound />} /></Routes></main>
      </div>
    </div>
  )
}
