import { useEffect, useRef, useState, type FormEvent, type MouseEvent, type PointerEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  createProject,
  createSettingCollection,
  listChapters,
  listProjects,
  listSettingCollections,
  updateProject,
  type Chapter,
  type CreateProjectRequest,
  type Metadata,
  type Project,
  type SettingCollection,
} from './api/client'

const asset = (name: string) => `/ui/${name}`
const icon = (name: string) => asset(`icons/${name}.svg`)

function greeting() {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good Morning'
  if (hour < 18) return 'Good Afternoon'
  return 'Good Evening'
}

function wordCount(project: Project) {
  const count = project.metadata?.word_count
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

function ProjectForm({
  collections,
  onClose,
  onCreated,
}: {
  collections: SettingCollection[]
  onClose: () => void
  onCreated?: (project: Project) => void
}) {
  const navigate = useNavigate()
  const [slug, setSlug] = useState('')
  const [title, setTitle] = useState('')
  const [genre, setGenre] = useState('')
  const [platform, setPlatform] = useState('')
  const [selectedCollectionId, setSelectedCollectionId] = useState('new')
  const [introduction, setIntroduction] = useState('')
  const [labels, setLabels] = useState('')
  const [cover, setCover] = useState('')
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
      const payload: CreateProjectRequest = {
        slug: slug.trim(),
        title: title.trim(),
        genre: genre.trim() || null,
        target_platform: platform.trim() || null,
      }
      if (selectedCollectionId && selectedCollectionId !== 'new') {
        payload.setting_collection_id = selectedCollectionId
      }
      const metadata: Metadata = {}
      if (introduction.trim()) metadata.introduction = introduction.trim()
      if (cover.trim()) metadata.cover = cover.trim()
      const parsedLabels = labels.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
      if (parsedLabels.length > 0) metadata.labels = parsedLabels
      if (Object.keys(metadata).length > 0) {
        payload.metadata = metadata
      }

      const project = await createProject(payload)
      onCreated?.(project)
      navigate(`/projects/${encodeURIComponent(project.id)}/studio`)
    } catch {
      setError('Project could not be created. Try again.')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="dashboard-dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <div className="dashboard-create-dialog novel-create-dialog" role="dialog" aria-modal="true" aria-labelledby="create-project-title">
        <button className="dashboard-dialog-close" data-glow type="button" onClick={onClose} aria-label="Close create project dialog">×</button>
        <form className="workspace-form novel-create-form" onSubmit={submit} aria-label="Create project">
          <h2 id="create-project-title">Create project</h2>
          <div className="form-grid">
            <label>Slug<input autoFocus value={slug} onChange={(event) => setSlug(event.target.value)} required /></label>
            <label>Title<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label>
            <label>Genre (optional)<input value={genre} onChange={(event) => setGenre(event.target.value)} /></label>
            <label>Target platform (optional)<input value={platform} onChange={(event) => setPlatform(event.target.value)} /></label>
            <label className="form-field-full">
              Setting collection
              <select
                value={selectedCollectionId}
                onChange={(event) => setSelectedCollectionId(event.target.value)}
                aria-label="Setting collection"
              >
                <option value="new">同时创建空白设定集 (默认)</option>
                {collections.map((col) => (
                  <option key={col.id} value={col.id}>
                    {col.title} (r{col.revision})
                  </option>
                ))}
              </select>
            </label>
            <label className="form-field-full">
              Introduction (optional)
              <textarea
                value={introduction}
                onChange={(event) => setIntroduction(event.target.value)}
                rows={3}
                placeholder="作品简介…"
              />
            </label>
            <label>
              Labels (optional)
              <input
                value={labels}
                onChange={(event) => setLabels(event.target.value)}
                placeholder="标签，用逗号分隔"
              />
            </label>
            <label>
              Cover URL or preset (optional)
              <input
                value={cover}
                onChange={(event) => setCover(event.target.value)}
                placeholder="cover-1.jpg 或图片地址"
              />
            </label>
          </div>
          {error && <p className="notice" role="alert">{error}</p>}
          <button data-glow type="submit" disabled={pending}>{pending ? 'Creating project…' : 'Create project'}</button>
        </form>
      </div>
    </div>
  )
}

function SettingCollectionForm({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (collection: SettingCollection) => void
}) {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [slug, setSlug] = useState('')
  const [description, setDescription] = useState('')
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
    if (!title.trim()) {
      setError('Title is required.')
      return
    }
    setPending(true)
    setError(null)
    try {
      const collection = await createSettingCollection({
        title: title.trim(),
        slug: slug.trim() || undefined,
        description: description.trim() || undefined,
      })
      onCreated(collection)
      onClose()
      navigate(`/setting-collections/${encodeURIComponent(collection.id)}`)
    } catch {
      setError('Setting collection could not be created. Try again.')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="dashboard-dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <div className="dashboard-create-dialog" role="dialog" aria-modal="true" aria-labelledby="create-collection-title">
        <button className="dashboard-dialog-close" data-glow type="button" onClick={onClose} aria-label="Close create setting collection dialog">×</button>
        <form className="workspace-form" onSubmit={submit} aria-label="Create setting collection">
          <h2 id="create-collection-title">Create setting collection</h2>
          <div className="form-grid">
            <label>Title<input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} required /></label>
            <label>Slug (optional)<input value={slug} onChange={(event) => setSlug(event.target.value)} placeholder="留空自动生成" /></label>
            <label className="form-field-full">
              Description (optional)
              <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={2} placeholder="设定集说明…" />
            </label>
          </div>
          {error && <p className="notice" role="alert">{error}</p>}
          <button data-glow type="submit" disabled={pending}>{pending ? 'Creating collection…' : 'Create setting collection'}</button>
        </form>
      </div>
    </div>
  )
}

function NovelDetails({
  project,
  artwork,
  collections,
  onClose,
  onProjectUpdated,
}: {
  project: Project
  artwork: string
  collections: SettingCollection[]
  onClose: () => void
  onProjectUpdated?: (updated: Project) => void
}) {
  const navigate = useNavigate()
  const closeButton = useRef<HTMLButtonElement>(null)
  const [overrideProject, setOverrideProject] = useState<Project | null>(null)
  const currentProject = overrideProject && overrideProject.id === project.id ? overrideProject : project
  const [chapters, setChapters] = useState<Chapter[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [expanded, setExpanded] = useState(true)
  const [closing, setClosing] = useState(false)

  // Edit mode state
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editGenre, setEditGenre] = useState('')
  const [editPlatform, setEditPlatform] = useState('')
  const [editCollectionId, setEditCollectionId] = useState('')
  const [editIntro, setEditIntro] = useState('')
  const [editLabels, setEditLabels] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  function toggleEditing() {
    if (!editing) {
      setEditTitle(currentProject.title)
      setEditGenre(currentProject.genre ?? '')
      setEditPlatform(currentProject.target_platform ?? '')
      setEditCollectionId(currentProject.setting_collection_id ?? '')
      setEditIntro(typeof currentProject.metadata?.introduction === 'string' ? currentProject.metadata.introduction : '')
      setEditLabels(Array.isArray(currentProject.metadata?.labels) ? currentProject.metadata.labels.join(', ') : '')
      setSaveError(null)
      setEditing(true)
    } else {
      setEditing(false)
    }
  }

  useEffect(() => {
    let active = true
    listChapters(currentProject.id).then(
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
  }, [currentProject.id])

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (saving) return
    if (!editTitle.trim()) {
      setSaveError('Title is required.')
      return
    }
    setSaving(true)
    setSaveError(null)
    try {
      const parsedLabels = editLabels.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
      const updatedMeta: Metadata = {
        ...currentProject.metadata,
        labels: parsedLabels,
      }
      if (editIntro.trim()) {
        updatedMeta.introduction = editIntro.trim()
      } else {
        delete updatedMeta.introduction
      }
      const updated = await updateProject(currentProject.id, {
        title: editTitle.trim(),
        genre: editGenre.trim() || null,
        target_platform: editPlatform.trim() || null,
        setting_collection_id: editCollectionId.trim() || null,
        metadata: updatedMeta,
      })
      setOverrideProject(updated)
      onProjectUpdated?.(updated)
      setEditing(false)
    } catch {
      setSaveError('Failed to save novel details. Try again.')
    } finally {
      setSaving(false)
    }
  }

  const boundCollection = collections.find((c) => c.id === currentProject.setting_collection_id)
  const displayIntro = typeof currentProject.metadata?.introduction === 'string' ? currentProject.metadata.introduction : ''
  const displayLabels = Array.isArray(currentProject.metadata?.labels)
    ? currentProject.metadata.labels.map(String)
    : []

  return (
    <div className={`dashboard-dialog-backdrop${closing ? ' is-closing' : ''}`} role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) setClosing(true)
    }}>
      <section className={`novel-detail${closing ? ' is-closing' : ''}`} role="dialog" aria-modal="true" aria-labelledby="novel-detail-title" onAnimationEnd={() => {
        if (closing) onClose()
      }}>
        <button ref={closeButton} className="dashboard-dialog-close" data-glow type="button" onClick={() => setClosing(true)} aria-label="Close novel details">×</button>

        <div className="novel-detail-columns">
          {/* Left Column: Cover, Title & Meta */}
          <div className="novel-detail-left">
            <div className="novel-detail-cover" aria-hidden="true"><img src={artwork} alt="" /></div>
            <h2 id="novel-detail-title">{currentProject.title}</h2>
            <div className="novel-detail-badges">
              <span className="novel-badge">字数：{wordCount(currentProject)}</span>
              <span className="novel-badge">状态：{currentProject.status === 'draft' ? '草稿' : currentProject.status}</span>
              {currentProject.genre && <span className="novel-badge">{currentProject.genre}</span>}
              {currentProject.target_platform && <span className="novel-badge">{currentProject.target_platform}</span>}
            </div>
            <div className="novel-detail-setting-block">
              {boundCollection ? (
                <button
                  className="novel-detail-setting-link"
                  data-glow
                  type="button"
                  onClick={() => navigate(`/setting-collections/${encodeURIComponent(boundCollection.id)}`)}
                  aria-label={`Open setting collection ${boundCollection.title}`}
                >
                  <img src={icon('setting')} alt="" />
                  <span className="collection-link-title">{boundCollection.title}</span>
                  <span className="collection-link-badge">r{boundCollection.revision}</span>
                </button>
              ) : (
                <span className="novel-no-setting">未关联设定集</span>
              )}
            </div>
            <button
              className="novel-edit-toggle"
              data-glow
              type="button"
              onClick={toggleEditing}
            >
              {editing ? '取消编辑' : '编辑资料'}
            </button>
          </div>

          {/* Middle Column: Introduction / Tags OR Inline Edit Form */}
          <div className="novel-detail-mid">
            {editing ? (
              <form className="novel-inline-edit-form" onSubmit={handleSave} aria-label="Edit novel form">
                <h3>编辑作品资料</h3>
                <label>
                  Title
                  <input
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    required
                    aria-label="Edit title"
                  />
                </label>
                <label>
                  Genre (optional)
                  <input
                    value={editGenre}
                    onChange={(e) => setEditGenre(e.target.value)}
                    aria-label="Edit genre"
                  />
                </label>
                <label>
                  Target platform (optional)
                  <input
                    value={editPlatform}
                    onChange={(e) => setEditPlatform(e.target.value)}
                    aria-label="Edit target platform"
                  />
                </label>
                <label>
                  Setting collection
                  <select
                    value={editCollectionId}
                    onChange={(e) => setEditCollectionId(e.target.value)}
                    aria-label="Edit setting collection"
                  >
                    <option value="">(无关联设定集)</option>
                    {collections.map((col) => (
                      <option key={col.id} value={col.id}>
                        {col.title} (r{col.revision})
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Introduction (optional)
                  <textarea
                    value={editIntro}
                    onChange={(e) => setEditIntro(e.target.value)}
                    rows={3}
                    aria-label="Edit introduction"
                  />
                </label>
                <label>
                  Labels (optional)
                  <input
                    value={editLabels}
                    onChange={(e) => setEditLabels(e.target.value)}
                    aria-label="Edit labels"
                  />
                </label>
                {saveError && <p className="notice" role="alert">{saveError}</p>}
                <div className="novel-edit-actions">
                  <button data-glow type="submit" disabled={saving}>
                    {saving ? 'Saving…' : 'Save changes'}
                  </button>
                  <button data-glow type="button" onClick={() => setEditing(false)}>
                    Cancel
                  </button>
                </div>
              </form>
            ) : (
              <div className="novel-detail-content">
                <div className="novel-detail-intro-section">
                  <h3>作品简介</h3>
                  <p className="novel-detail-intro">
                    {displayIntro || '暂无简介'}
                  </p>
                </div>
                {displayLabels.length > 0 && (
                  <div className="novel-detail-tags-section">
                    <h3>标签</h3>
                    <div className="novel-detail-tags" aria-label="Novel tags">
                      {displayLabels.map((lbl, idx) => (
                        <span key={idx} className="novel-tag">{lbl}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Right Column: Chapters */}
          <div className="novel-detail-right">
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
                  <button data-glow type="button" onClick={() => navigate(`/projects/${encodeURIComponent(currentProject.id)}/studio/${encodeURIComponent(chapter.id)}`)}>
                    <span>第{chapter.chapter_number}话</span><span>{chapter.title || '未命名章节'}</span>
                    <img src={icon('rename')} alt="重命名章节" />
                  </button>
                </li>)}
              </ul>}
            </div>
            <span className="novel-detail-rule" aria-hidden="true" />
            <button className="novel-create-button" data-glow type="button" onClick={() => navigate(`/projects/${encodeURIComponent(currentProject.id)}/studio`)}>create</button>
          </div>
        </div>
      </section>
    </div>
  )
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
  const collectionsRow = useRef<HTMLDivElement>(null)
  const searchShell = useRef<HTMLDivElement>(null)
  const searchHideTimer = useRef<number | null>(null)
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [collections, setCollections] = useState<SettingCollection[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [collectionsFailed, setCollectionsFailed] = useState(false)
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
  const [creatingCollection, setCreatingCollection] = useState(false)
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchHovered, setSearchHovered] = useState(false)
  const [searchLeaving, setSearchLeaving] = useState(false)
  const [recentIndex, setRecentIndex] = useState(0)
  const [novelIndex, setNovelIndex] = useState(0)
  const [collectionIndex, setCollectionIndex] = useState(0)
  const navigate = useNavigate()

  useEffect(() => {
    let active = true
    listProjects().then(
      (loaded) => { if (active) setProjects(loaded) },
      () => { if (active) setFailed(true) },
    )
    Promise.resolve(typeof listSettingCollections === 'function' ? (listSettingCollections() ?? []) : [])
      .then(
        (loaded) => { if (active) setCollections(Array.isArray(loaded) ? loaded : []) },
        () => { if (active) { setCollections([]); setCollectionsFailed(true) } },
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
  const collectionMatches = normalizedQuery
    ? collections?.filter((col) => [col.title, col.slug, col.description ?? ''].some((value) => value.toLocaleLowerCase().includes(normalizedQuery))) ?? []
    : []
  const showResults = (searching || searchHovered || searchLeaving) && Boolean(normalizedQuery)
  const moveCarousel = (row: HTMLDivElement | null, direction: -1 | 1, step: number, maxIndex: number, setIndex: (index: number) => void) => {
    if (!row) return
    const next = Math.max(0, Math.min(maxIndex, Math.round(row.scrollLeft / step) + direction))
    setIndex(next)
    row.scrollTo({ left: next * step, behavior: 'smooth' })
  }
  const artwork = (project: Project) => asset(`covers/cover-${Math.max(0, projects?.indexOf(project) ?? 0) % 8 + 1}.jpg`)
  const collectionArtwork = (col: SettingCollection) =>
    asset(`covers/cover-${Math.max(0, collections?.indexOf(col) ?? 0) % 8 + 1}.jpg`)
  const recentMaxIndex = Math.max(0, recent.length - 3)
  const novelMaxIndex = Math.max(0, (projects?.length ?? 0) - 5)
  const collectionMaxIndex = Math.max(0, (collections?.length ?? 0) - 4)
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

  return (
    <div className="dashboard-canvas" onPointerMove={trackGlow} onClickCapture={burstGlow} onAnimationEnd={(event) => {
      if (event.animationName === 'glow-burst') (event.target as HTMLElement).classList.remove('is-glow-burst')
    }}>
      <img className="dashboard-settings" src={icon('setting')} alt="Settings" />
      <h1 id="route-title" className="dashboard-greeting">{greeting()}</h1>
      <div className="dashboard-main" aria-labelledby="route-title">
        {/* Recent Novels */}
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

        {/* Novels */}
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

        {/* Setting Collections */}
        <section className="dashboard-section dashboard-collections" aria-labelledby="collections-title">
          <div className="dashboard-section-heading">
            <h2 id="collections-title"><img src={icon('setting')} alt="" />setting collections</h2>
            <button className="collection-add" data-glow type="button" onClick={() => setCreatingCollection(true)} aria-label="Create setting collection"><img src={icon('bx_message-square-add')} alt="" /></button>
          </div>
          <div className="dashboard-carousel">
            <div className="dashboard-row collections-row" ref={collectionsRow} onScroll={(event) => setCollectionIndex(Math.round(event.currentTarget.scrollLeft / 220))}>
              {collections === null && !collectionsFailed && Array.from({ length: 4 }, (_, index) => <div className="dashboard-skeleton collection-card" key={index} />)}
              {collections?.map((col) => {
                const refCount = projects?.filter((p) => p.setting_collection_id === col.id).length ?? 0
                return (
                  <button
                    className="collection-card project-card"
                    data-glow
                    type="button"
                    key={col.id}
                    onClick={() => navigate(`/setting-collections/${encodeURIComponent(col.id)}`)}
                    aria-label={`Open ${col.title} setting collection`}
                  >
                    <span className="collection-art">
                      <img src={collectionArtwork(col)} alt="" />
                      <span className="collection-badge">r{col.revision}</span>
                    </span>
                    <span className="collection-info">
                      <strong>{col.title}</strong>
                      <span className="collection-meta">
                        <span>关联小说 ({refCount})</span>
                        <time dateTime={col.updated_at}>{relativeTime(col.updated_at)}</time>
                      </span>
                    </span>
                  </button>
                )
              })}
              {collections?.length === 0 && <p className="dashboard-empty">No setting collections yet. Create one to begin.</p>}
            </div>
            {(collections?.length ?? 0) > 4 && <>
              <button className="carousel-arrow carousel-previous" data-glow type="button" disabled={collectionIndex === 0} onClick={() => moveCarousel(collectionsRow.current, -1, 220, collectionMaxIndex, setCollectionIndex)} aria-label="Show previous setting collections"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
              <button className="carousel-arrow carousel-next" data-glow type="button" disabled={collectionIndex === collectionMaxIndex} onClick={() => moveCarousel(collectionsRow.current, 1, 220, collectionMaxIndex, setCollectionIndex)} aria-label="Show more setting collections"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
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
            {collectionMatches.map((col) => (
              <button
                data-glow
                type="button"
                key={col.id}
                className="search-result-collection"
                onClick={() => navigate(`/setting-collections/${encodeURIComponent(col.id)}`)}
              >
                <span>{col.title}</span>
                <span className="search-result-tag">设定集</span>
              </button>
            ))}
            {matches.length === 0 && collectionMatches.length === 0 && <p>未找到小说</p>}
          </div>}
          <div className="search-shell" ref={searchShell} onAnimationEnd={(event) => {
            if (event.target === event.currentTarget && searchHovered && !searchShell.current?.matches(':hover')) setSearchHovered(false)
          }}>
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Search novels" />
            <img src={icon('Vector')} alt="" />
          </div>
        </div>
      </div>
      {selected && (
        <NovelDetails
          key={selected.id}
          project={selected}
          artwork={artwork(selected)}
          collections={collections ?? []}
          onClose={() => setSelected(null)}
          onProjectUpdated={(updated) => {
            setProjects((prev) => prev?.map((p) => (p.id === updated.id ? updated : p)) ?? [updated])
          }}
        />
      )}
      {selectedId && projects && !selected && <div className="dashboard-dialog-backdrop"><section className="dashboard-create-dialog" role="alert"><p>未找到此作品，可能已被删除或无权访问。</p><button onClick={() => setSelected(null)}>返回书架</button></section></div>}
      {creating && (
        <ProjectForm
          collections={collections ?? []}
          onClose={() => setCreating(false)}
          onCreated={(newProject) => {
            setProjects((prev) => (prev ? [newProject, ...prev] : [newProject]))
          }}
        />
      )}
      {creatingCollection && (
        <SettingCollectionForm
          onClose={() => setCreatingCollection(false)}
          onCreated={(newCol) => {
            setCollections((prev) => (prev ? [newCol, ...prev] : [newCol]))
          }}
        />
      )}
    </div>
  )
}
