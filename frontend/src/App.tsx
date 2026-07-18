import { useEffect, useState, type FormEvent } from 'react'
import { Link, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import {
  createChapter,
  createProject,
  getChapter,
  getProject,
  listChapters,
  listProjects,
  type Chapter,
  type Project,
} from './api/client'

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
  const [project, setProject] = useState<Project | null>(null)
  const [chapter, setChapter] = useState<Chapter | null>(null)
  const [failed, setFailed] = useState(false)

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
    return () => { active = false }
  }, [chapterId, projectId])

  if (failed) return <section className="page"><LoadError /></section>
  if (!project || !chapter) return <section className="page"><p className="muted">Loading chapter…</p></section>
  return (
    <section className="page" aria-labelledby="route-title">
      <Link className="back-link" to={`/projects/${projectId}`}>Back to {project.title}</Link>
      <p className="eyebrow">Chapter {chapter.chapter_number}</p>
      <h1 id="route-title">{chapter.title || 'Untitled chapter'}</h1>
      <p className="muted">Status: {chapter.status}</p>
      <section className="placeholder" aria-labelledby="next-title"><h2 id="next-title">Chapter workspace</h2><p className="muted">Drafting and review tools will appear here in a future workspace update.</p></section>
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
        <main><Routes><Route path="/" element={<ProjectListPage />} /><Route path="/projects/:projectId" element={<ProjectWorkspace />} /><Route path="/projects/:projectId/chapters/:chapterId" element={<ChapterWorkspace />} /><Route path="*" element={<NotFound />} /></Routes></main>
      </div>
    </div>
  )
}
