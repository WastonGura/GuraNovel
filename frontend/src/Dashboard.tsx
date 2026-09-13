import { useEffect, useRef, useState, type FormEvent, type MouseEvent, type PointerEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { createProject, listChapters, listProjects, type Chapter, type Project } from './api/client'

const asset = (name: string) => `/ui/${name}`
const icon = (name: string) => asset(`icons/${name}.svg`)

function greeting() {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good Morning'
  if (hour < 18) return 'Good Afternoon'
  return 'Good Evening'
}

function wordCount(project: Project) {
  const count = project.metadata.word_count
  return typeof count === 'number' && Number.isFinite(count) && count >= 0
    ? new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 1 }).format(count)
    : '—'
}

function relativeTime(timestamp: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(timestamp)) / 60_000))
  if (!Number.isFinite(minutes) || minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}小时前`
  return `${Math.floor(hours / 24)}天前`
}

function ProjectForm({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const [slug, setSlug] = useState('')
  const [title, setTitle] = useState('')
  const [genre, setGenre] = useState('')
  const [platform, setPlatform] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const closeOnEscape = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

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
      navigate(`/projects/${encodeURIComponent(project.id)}/studio?view=Detail`)
    } catch {
      setError('Project could not be created. Try again.')
    } finally {
      setPending(false)
    }
  }

  return <div className="dashboard-dialog-backdrop" role="presentation" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <div className="dashboard-create-dialog" role="dialog" aria-modal="true" aria-labelledby="create-project-title">
      <button className="dashboard-dialog-close" data-glow type="button" onClick={onClose} aria-label="Close create project dialog">×</button>
      <form className="workspace-form" onSubmit={submit} aria-label="Create project">
        <h2 id="create-project-title">Create project</h2>
        <div className="form-grid">
          <label>Slug<input autoFocus value={slug} onChange={(event) => setSlug(event.target.value)} required /></label>
          <label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label>
          <label>Genre (optional)<input value={genre} onChange={(event) => setGenre(event.target.value)} /></label>
          <label>Target platform (optional)<input value={platform} onChange={(event) => setPlatform(event.target.value)} /></label>
        </div>
        {error && <p className="notice" role="alert">{error}</p>}
        <button data-glow type="submit" disabled={pending}>{pending ? 'Creating project…' : 'Create project'}</button>
      </form>
    </div>
  </div>
}

function NovelDetails({ project, artwork, onClose }: { project: Project, artwork: string, onClose: () => void }) {
  const navigate = useNavigate()
  const closeButton = useRef<HTMLButtonElement>(null)
  const [chapters, setChapters] = useState<Chapter[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [expanded, setExpanded] = useState(true)
  const [closing, setClosing] = useState(false)

  useEffect(() => {
    let active = true
    listChapters(project.id).then(
      (loaded) => { if (active) setChapters(loaded.slice().sort((a, b) => b.chapter_number - a.chapter_number)) },
      () => { if (active) setFailed(true) },
    )
    const closeOnEscape = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') setClosing(true) }
    window.addEventListener('keydown', closeOnEscape)
    closeButton.current?.focus()
    return () => {
      active = false
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [project.id])

  return <div className={`dashboard-dialog-backdrop${closing ? ' is-closing' : ''}`} role="presentation" onMouseDown={(event) => {
    if (event.target === event.currentTarget) setClosing(true)
  }}>
    <section className={`novel-detail${closing ? ' is-closing' : ''}`} role="dialog" aria-modal="true" aria-labelledby="novel-detail-title" onAnimationEnd={() => {
      if (closing) onClose()
    }}>
      <button ref={closeButton} className="dashboard-dialog-close" data-glow type="button" onClick={() => setClosing(true)} aria-label="Close novel details">×</button>
      <div className="novel-detail-cover" aria-hidden="true"><img src={artwork} alt="" /></div>
      <h2 id="novel-detail-title">{project.title}</h2>
      <div className="novel-detail-chapters">
        <button className="volume-toggle" data-glow type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
          <span className="volume-caret" aria-hidden="true">{expanded ? '⌄' : '›'}</span>
          <span>第一卷 …</span>
          <img src={icon('rename')} alt="重命名卷" />
        </button>
        {chapters === null && !failed && <p className="detail-status" role="status">Loading chapters…</p>}
        {failed && <p className="detail-status notice" role="alert">Chapters could not be loaded. Try again.</p>}
        {expanded && chapters?.length === 0 && <p className="detail-status">暂无章节</p>}
        {expanded && chapters && chapters.length > 0 && <ul className="detail-chapter-list">
          {chapters.map((chapter) => <li key={chapter.id}>
            <button data-glow type="button" onClick={() => navigate(`/projects/${encodeURIComponent(project.id)}/studio/${encodeURIComponent(chapter.id)}?view=Create`)}>
              <span>第{chapter.chapter_number}话</span><span>{chapter.title || '未命名章节'}</span>
              <img src={icon('rename')} alt="重命名章节" />
            </button>
          </li>)}
        </ul>}
      </div>
      <span className="novel-detail-rule" aria-hidden="true" />
      <button className="novel-create-button" data-glow type="button" onClick={() => navigate(`/projects/${encodeURIComponent(project.id)}/studio?view=Create`)}>create</button>
    </section>
  </div>
}

function trackGlow(event: PointerEvent<HTMLDivElement>) {
  const target = (event.target as HTMLElement).closest<HTMLElement>('[data-glow]')
  if (!target || !event.currentTarget.contains(target)) return
  const bounds = target.getBoundingClientRect()
  target.style.setProperty('--glow-x', `${event.clientX - bounds.left}px`)
  target.style.setProperty('--glow-y', `${event.clientY - bounds.top}px`)
}

function burstGlow(event: MouseEvent<HTMLDivElement>) {
  const target = (event.target as HTMLElement).closest<HTMLElement>('[data-glow]')
  if (!target || !event.currentTarget.contains(target)) return
  const bounds = target.getBoundingClientRect()
  target.style.setProperty('--glow-x', `${event.detail ? event.clientX - bounds.left : bounds.width / 2}px`)
  target.style.setProperty('--glow-y', `${event.detail ? event.clientY - bounds.top : bounds.height / 2}px`)
  target.classList.remove('is-glow-burst')
  void target.offsetWidth
  target.classList.add('is-glow-burst')
}

export default function Dashboard() {
  const recentRow = useRef<HTMLDivElement>(null)
  const novelsRow = useRef<HTMLDivElement>(null)
  const searchShell = useRef<HTMLDivElement>(null)
  const searchHideTimer = useRef<number | null>(null)
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedId = searchParams.get('project')
  const selected = projects?.find(project => project.id === selectedId)
  const setSelected = (project: Project | null) => {
    const next = new URLSearchParams(searchParams)
    if (project) next.set('project', project.id)
    else next.delete('project')
    setSearchParams(next)
  }
  const [creating, setCreating] = useState(false)
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchHovered, setSearchHovered] = useState(false)
  const [searchLeaving, setSearchLeaving] = useState(false)
  const [recentIndex, setRecentIndex] = useState(0)
  const [novelIndex, setNovelIndex] = useState(0)

  useEffect(() => {
    let active = true
    listProjects().then(
      (loaded) => { if (active) setProjects(loaded) },
      () => { if (active) setFailed(true) },
    )
    return () => { active = false }
  }, [])

  useEffect(() => () => {
    if (searchHideTimer.current !== null) window.clearTimeout(searchHideTimer.current)
  }, [])

  const recent = projects?.slice().sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 4) ?? []
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const matches = normalizedQuery
    ? projects?.filter((project) => [project.title, project.slug, project.genre ?? ''].some((value) => value.toLocaleLowerCase().includes(normalizedQuery))) ?? []
    : []
  const showResults = (searching || searchHovered || searchLeaving) && Boolean(normalizedQuery)
  const moveCarousel = (row: HTMLDivElement | null, direction: -1 | 1, step: number, maxIndex: number, setIndex: (index: number) => void) => {
    if (!row) return
    const next = Math.max(0, Math.min(maxIndex, Math.round(row.scrollLeft / step) + direction))
    setIndex(next)
    row.scrollTo({ left: next * step, behavior: 'smooth' })
  }
  const artwork = (project: Project) => asset(`covers/cover-${Math.max(0, projects?.indexOf(project) ?? 0) % 8 + 1}.jpg`)
  const recentMaxIndex = Math.max(0, recent.length - 3)
  const novelMaxIndex = Math.max(0, (projects?.length ?? 0) - 5)
  const stopSearchHide = () => {
    if (searchHideTimer.current !== null) window.clearTimeout(searchHideTimer.current)
    searchHideTimer.current = null
  }
  const revealSearch = () => {
    stopSearchHide()
    setSearchLeaving(false)
    setSearchHovered(true)
  }
  const concealSearch = () => {
    const shell = searchShell.current
    if (shell) shell.style.setProperty('--search-exit-angle', getComputedStyle(shell, '::before').getPropertyValue('--search-angle').trim() || '0deg')
    stopSearchHide()
    setSearchHovered(false)
    setSearchLeaving(true)
    searchHideTimer.current = window.setTimeout(() => {
      setSearchLeaving(false)
      searchHideTimer.current = null
    }, 480)
  }

  return <div className="dashboard-canvas" onPointerMove={trackGlow} onClickCapture={burstGlow} onAnimationEnd={(event) => {
    if (event.animationName === 'glow-burst') (event.target as HTMLElement).classList.remove('is-glow-burst')
  }}>
    <img className="dashboard-settings" src={icon('setting')} alt="Settings" />
    <h1 id="route-title" className="dashboard-greeting">{greeting()}</h1>
    <div className="dashboard-main" aria-labelledby="route-title">
      <section className="dashboard-section dashboard-recent" aria-labelledby="recent-title">
        <h2 id="recent-title"><img src={icon('akar-icons_clock')} alt="" />recent</h2>
        <div className="dashboard-carousel">
          <div className="dashboard-row recent-row" ref={recentRow} onScroll={(event) => setRecentIndex(Math.round(event.currentTarget.scrollLeft / 295))}>
            {projects === null && !failed && Array.from({ length: 4 }, (_, index) => <div className="dashboard-skeleton recent-card" key={index} />)}
            {recent.map((project) => <button className="recent-card project-card" data-glow type="button" key={project.id} onClick={() => setSelected(project)} aria-label={`Open ${project.title}`}>
              <span className="recent-art"><img src={artwork(project)} alt="" /></span>
              <span className="recent-info"><strong>{project.title}</strong><span><span>字数：{wordCount(project)}</span><time dateTime={project.updated_at}>{relativeTime(project.updated_at)}</time></span></span>
            </button>)}
            {projects?.length === 0 && <p className="dashboard-empty">No projects yet. Create one to begin.</p>}
          </div>
          {recent.length > 3 && <>
            <button className="carousel-arrow carousel-previous" data-glow type="button" disabled={recentIndex === 0} onClick={() => moveCarousel(recentRow.current, -1, 295, recentMaxIndex, setRecentIndex)} aria-label="Show previous recent novels"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
            <button className="carousel-arrow carousel-next" data-glow type="button" disabled={recentIndex === recentMaxIndex} onClick={() => moveCarousel(recentRow.current, 1, 295, recentMaxIndex, setRecentIndex)} aria-label="Show more recent novels"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
          </>}
        </div>
      </section>

      <section className="dashboard-section dashboard-novels" aria-labelledby="novels-title">
        <div className="dashboard-section-heading">
          <h2 id="novels-title"><img src={icon('akar-icons_book')} alt="" />novels</h2>
          <button className="novel-add" data-glow type="button" onClick={() => setCreating(true)} aria-label="Create novel"><img src={icon('bx_message-square-add')} alt="" /></button>
        </div>
        <div className="dashboard-carousel">
          <div className="dashboard-row novels-row" ref={novelsRow} onScroll={(event) => setNovelIndex(Math.round(event.currentTarget.scrollLeft / 171))}>
            {projects === null && !failed && Array.from({ length: 6 }, (_, index) => <div className="dashboard-skeleton novel-card" key={index} />)}
            {projects?.map((project) => <button className="novel-card project-card" data-glow type="button" key={project.id} onClick={() => setSelected(project)} aria-label={`Open ${project.title}`}>
              <span className="novel-art"><img src={artwork(project)} alt="" /></span>
              <span className="novel-info"><strong>{project.title}</strong><span>字数：{wordCount(project)}</span><time dateTime={project.updated_at}>{relativeTime(project.updated_at)}</time></span>
            </button>)}
          </div>
          {(projects?.length ?? 0) > 5 && <>
            <button className="carousel-arrow carousel-previous" data-glow type="button" disabled={novelIndex === 0} onClick={() => moveCarousel(novelsRow.current, -1, 171, novelMaxIndex, setNovelIndex)} aria-label="Show previous novels"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
            <button className="carousel-arrow carousel-next" data-glow type="button" disabled={novelIndex === novelMaxIndex} onClick={() => moveCarousel(novelsRow.current, 1, 171, novelMaxIndex, setNovelIndex)} aria-label="Show more novels"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
          </>}
        </div>
      </section>

      {projects === null && !failed && <span className="dashboard-sr-only" role="status">Loading projects…</span>}
      {failed && <p className="dashboard-load-error notice" role="alert">Projects could not be loaded. Try again.</p>}

      <div className={`dashboard-search${searching ? ' is-active' : ''}${searchHovered ? ' is-hovered' : ''}${searchLeaving ? ' is-leaving' : ''}${showResults ? ' has-results' : ''}`}
        onPointerEnter={() => { if (!searching) revealSearch() }}
        onPointerLeave={() => { if (!searching) concealSearch() }}
        onPointerOut={() => { if (!searching && !searchShell.current?.matches(':hover')) concealSearch() }}
        onFocus={() => {
          stopSearchHide()
          setSearchHovered(false)
          setSearchLeaving(false)
          setSearching(true)
        }} onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          setSearching(false)
          if (searchShell.current?.matches(':hover')) revealSearch()
          else concealSearch()
        }
      }}>
        {showResults && <div className="search-results" role="region" aria-label="Search results">
          {matches.map((project) => <button data-glow type="button" key={project.id} onClick={() => setSelected(project)}>{project.title}</button>)}
          {matches.length === 0 && <p>未找到小说</p>}
        </div>}
        <div className="search-shell" ref={searchShell} onAnimationEnd={(event) => {
          if (event.target === event.currentTarget && searchHovered && !searchShell.current?.matches(':hover')) setSearchHovered(false)
        }}>
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Search novels" />
          <img src={icon('Vector')} alt="" />
        </div>
      </div>
    </div>
    {selected && <NovelDetails key={selected.id} project={selected} artwork={artwork(selected)} onClose={() => setSelected(null)} />}
    {selectedId && projects && !selected && <div className="dashboard-dialog-backdrop"><section className="dashboard-create-dialog" role="alert"><p>未找到此作品，可能已被删除或无权访问。</p><button onClick={() => setSelected(null)}>返回书架</button></section></div>}
    {creating && <ProjectForm onClose={() => setCreating(false)} />}
  </div>
}
