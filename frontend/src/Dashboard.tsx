import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import CreationDialog from './CreationDialog'
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
  if (hour < 12) return '早上好'
  if (hour < 18) return '下午好'
  return '晚上好'
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
  const [slugSuffix] = useState(() => crypto.randomUUID().slice(0, 8))
  const errorRef = useRef<HTMLParagraphElement>(null)
  const submitting = useRef(false)

  useEffect(() => { if (error) errorRef.current?.focus() }, [error])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current) return
    if (!title.trim()) {
      setError('请填写作品标题。')
      return
    }
    const titleSlug = title.normalize('NFKD').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 45)
    const effectiveSlug = slug.trim() || `${titleSlug || 'novel'}-${slugSuffix}`
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/i.test(effectiveSlug)) {
      setError('高级选项中的 Slug 请使用 1–64 个英文字母、数字、连字符或下划线，并以字母或数字开头。')
      return
    }
    submitting.current = true
    setPending(true)
    setError(null)
    try {
      const payload: CreateProjectRequest = {
        slug: effectiveSlug,
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
    } catch (cause) {
      const status = cause && typeof cause === 'object' && 'status' in cause ? cause.status : undefined
      setError(status === 409
        ? 'Slug 已被使用。请在高级选项中修改后重试；如果刚才的响应丢失，请先关闭弹窗检查书架。'
        : '创建失败，已保留填写内容。请检查网络后重试；如果响应丢失，请先关闭弹窗检查书架。')
    } finally {
      submitting.current = false
      setPending(false)
    }
  }

  return (
    <CreationDialog titleId="create-project-title" closeLabel="关闭创建作品" busy={pending} onClose={() => { if (!submitting.current) onClose() }}>
      <form className="creation-form" onSubmit={submit} aria-label="创建作品">
        <header className="creation-header">
          <h2 id="create-project-title">创建作品</h2>
        </header>
        <fieldset className="creation-fields" disabled={pending}>
          <label>标题<input value={title} onChange={event => setTitle(event.target.value)} required placeholder="你的故事叫什么？" aria-describedby={error ? 'create-project-error' : undefined} /></label>
          <label>设定集<select value={selectedCollectionId} onChange={event => setSelectedCollectionId(event.target.value)} aria-label="设定集">
            <option value="new">同时创建空白设定集 (默认)</option>
            {collections.map(col => <option key={col.id} value={col.id}>{col.title}</option>)}
          </select><span className="creation-field-hint">为这部作品新建，或沿用已有世界。</span></label>
          <label>简介（可选）<textarea value={introduction} onChange={event => setIntroduction(event.target.value)} rows={3} placeholder="一句话，记下这个故事的起点…" /></label>
          <details className="creation-disclosure">
            <summary>更多作品信息</summary>
            <div className="creation-field-pair">
              <label>题材（可选）<input value={genre} onChange={event => setGenre(event.target.value)} placeholder="如：日常、悬疑、幻想" /></label>
              <label>目标平台（可选）<input value={platform} onChange={event => setPlatform(event.target.value)} placeholder="准备在哪里连载" /></label>
            </div>
            <div className="creation-field-pair">
              <label>标签（可选）<input value={labels} onChange={event => setLabels(event.target.value)} placeholder="标签，用逗号分隔" /></label>
              <label>封面地址或预设（可选）<input value={cover} onChange={event => setCover(event.target.value)} placeholder="cover-1.jpg 或图片地址" /></label>
            </div>
          </details>
          <details className="creation-disclosure">
            <summary>高级选项：作品标识</summary>
            <p id="slug-help" className="creation-field-hint">自动生成内部标识。需要固定链接时可自行填写。</p>
            <label>Slug<input value={slug} onChange={event => setSlug(event.target.value)} aria-describedby="slug-help" /></label>
          </details>
        </fieldset>
        {error && <p ref={errorRef} id="create-project-error" className="creation-error" role="alert" tabIndex={-1}>{error}</p>}
        <footer className="creation-footer">
          <button className="creation-cancel" type="button" onClick={onClose} disabled={pending}>取消</button>
          <button className="creation-submit" type="submit" disabled={pending}>{pending ? '正在创建作品…' : '创建作品'}<span aria-hidden="true">↗</span></button>
        </footer>
      </form>
    </CreationDialog>
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

  const submitting = useRef(false)
  const errorRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => { if (error) errorRef.current?.focus() }, [error])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current) return
    if (!title.trim()) {
      setError('请填写标题。')
      return
    }
    submitting.current = true
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
      setError('设定集创建失败，已保留内容，请检查网络后重试。如响应丢失，请先关闭弹窗核对设定集列表。')
    } finally {
      submitting.current = false
      setPending(false)
    }
  }

  return (
    <CreationDialog titleId="create-collection-title" closeLabel="关闭新建设定集" busy={pending} onClose={() => { if (!submitting.current) onClose() }}>
      <form className="creation-form" onSubmit={submit} aria-label="新建设定集">
        <header className="creation-header">
          <h2 id="create-collection-title">新建设定集</h2>
        </header>
        <fieldset className="creation-fields" disabled={pending}>
          <label>标题<input value={title} onChange={event => setTitle(event.target.value)} required placeholder="给这个世界取个名字" /></label>
          <label>说明（可选）<textarea value={description} onChange={event => setDescription(event.target.value)} rows={4} placeholder="这个世界是什么样的？" /></label>
          <details className="creation-disclosure">
            <summary>高级选项：设定集标识</summary>
            <p id="collection-slug-help" className="creation-field-hint">留空自动生成；仅在需要固定标识时填写。</p>
            <label>设定集标识<input value={slug} onChange={event => setSlug(event.target.value)} aria-describedby="collection-slug-help" placeholder="留空自动生成" /></label>
          </details>
        </fieldset>
        {error && <p ref={errorRef} className="creation-error" role="alert" tabIndex={-1}>{error}</p>}
        <footer className="creation-footer">
          <button className="creation-cancel" type="button" onClick={onClose} disabled={pending}>取消</button>
          <button className="creation-submit" type="submit" disabled={pending}>{pending ? '正在创建设定集…' : '新建设定集'}<span aria-hidden="true">↗</span></button>
        </footer>
      </form>
    </CreationDialog>
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
  const { state } = useLocation()
  const retiredNotice = state?.retiredWorkspace === 'maintenance'
    ? '项目维护暂未提供操作入口。已有任务和历史数据仍保留。'
    : state?.retiredWorkspace === 'creation'
      ? '作品构思暂未提供操作入口。你可以打开章节继续创作，已有任务和历史数据仍保留。'
      : ''
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
      setSaveError('请填写标题。')
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
      setSaveError('资料保存失败，已保留修改，请检查网络后重试。')
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
        <button ref={closeButton} className="dashboard-dialog-close" type="button" onClick={() => setClosing(true)} aria-label="关闭作品详情">×</button>

        <div className="novel-detail-columns">
          {/* Left Column: Cover, Title & Meta */}
          <div className="novel-detail-left">
            <div className="novel-detail-cover" aria-hidden="true"><img src={artwork} alt="" /></div>
            <h2 id="novel-detail-title">{currentProject.title}</h2>
            <div className="novel-detail-badges">
              <span className="novel-badge">字数：{wordCount(currentProject)}</span>
              <span className="novel-badge">状态：{({ draft: '草稿', active: '创作中', completed: '已完成', archived: '已归档' } as Record<string, string>)[currentProject.status] || '状态待核对'}</span>
              {currentProject.genre && <span className="novel-badge">{currentProject.genre}</span>}
              {currentProject.target_platform && <span className="novel-badge">{currentProject.target_platform}</span>}
            </div>
            <div className="novel-detail-setting-block">
              {boundCollection ? (
                <button
                  className="novel-detail-setting-link"
                  type="button"
                  onClick={() => navigate(`/setting-collections/${encodeURIComponent(boundCollection.id)}`)}
                  aria-label={`Open setting collection ${boundCollection.title}`}
                >
                  <img src={icon('setting')} alt="" />
                  <span className="collection-link-title">{boundCollection.title}</span>
                </button>
              ) : (
                <span className="novel-no-setting">未关联设定集</span>
              )}
            </div>
            <button
              className="novel-edit-toggle"
              type="button"
              onClick={toggleEditing}
            >
              {editing ? '取消编辑' : '编辑资料'}
            </button>
          </div>

          {/* Middle Column: Introduction / Tags OR Inline Edit Form */}
          <div className="novel-detail-mid">
            {retiredNotice && <p className="muted" role="status">{retiredNotice}</p>}
            {editing ? (
              <form className="novel-inline-edit-form" onSubmit={handleSave} aria-label="编辑作品资料">
                <h3>编辑作品资料</h3>
                <label>
                  标题
                  <input
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    required
                    aria-label="编辑标题"
                  />
                </label>
                <label>
                  题材（可选）
                  <input
                    value={editGenre}
                    onChange={(e) => setEditGenre(e.target.value)}
                    aria-label="编辑题材"
                  />
                </label>
                <label>
                  目标平台（可选）
                  <input
                    value={editPlatform}
                    onChange={(e) => setEditPlatform(e.target.value)}
                    aria-label="编辑目标平台"
                  />
                </label>
                <label>
                  设定集
                  <select
                    value={editCollectionId}
                    onChange={(e) => setEditCollectionId(e.target.value)}
                    aria-label="编辑关联设定集"
                  >
                    <option value="">(无关联设定集)</option>
                    {collections.map((col) => (
                      <option key={col.id} value={col.id}>
                        {col.title}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  简介（可选）
                  <textarea
                    value={editIntro}
                    onChange={(e) => setEditIntro(e.target.value)}
                    rows={3}
                    aria-label="编辑简介"
                  />
                </label>
                <label>
                  标签（可选）
                  <input
                    value={editLabels}
                    onChange={(e) => setEditLabels(e.target.value)}
                    aria-label="编辑标签"
                  />
                </label>
                {saveError && <p className="notice" role="alert">{saveError}</p>}
                <div className="novel-edit-actions">
                  <button type="submit" disabled={saving}>
                    {saving ? '正在保存…' : '保存修改'}
                  </button>
                  <button type="button" onClick={() => setEditing(false)}>
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
                    <div className="novel-detail-tags" aria-label="作品标签">
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
              <button className="volume-toggle" type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
                <span className="volume-caret" aria-hidden="true">{expanded ? '⌄' : '›'}</span>
                <span>第一卷 …</span>
                <img src={icon('rename')} alt="" />
              </button>
              {chapters === null && !failed && <p className="detail-status" role="status">正在加载章节…</p>}
              {failed && <p className="detail-status notice" role="alert">章节加载失败，请关闭后重新打开作品。</p>}
              {expanded && chapters?.length === 0 && <p className="detail-status">暂无章节</p>}
              {expanded && chapters && chapters.length > 0 && <ul className="detail-chapter-list">
                {chapters.map((chapter) => <li key={chapter.id}>
                  <button type="button" onClick={() => navigate(`/projects/${encodeURIComponent(currentProject.id)}/studio/${encodeURIComponent(chapter.id)}`)}>
                    <span>第{chapter.chapter_number}话</span><span>{chapter.title || '未命名章节'}</span>
                    <img src={icon('rename')} alt="" />
                  </button>
                </li>)}
              </ul>}
            </div>
            <span className="novel-detail-rule" aria-hidden="true" />
            <button className="novel-create-button" type="button" onClick={() => navigate(`/projects/${encodeURIComponent(currentProject.id)}/studio`)}>开始创作</button>
          </div>
        </div>
      </section>
    </div>
  )
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

  useEffect(() => {
    const rows = [recentRow.current, novelsRow.current, collectionsRow.current].filter(row => row !== null)
    const updateCardEdges = () => {
      // Clip only the part of a moving card that has passed the shelf edge.
      // Fully visible cards retain their shadow bleed, including after paging back.
      const cards = rows.flatMap(row => {
        if (!row.clientWidth) return []
        const edge = row.parentElement!.getBoundingClientRect().left
        return Array.from(row.querySelectorAll<HTMLElement>('.project-card'), card => {
          const bounds = card.getBoundingClientRect()
          return { card, cut: Math.max(0, Math.min(bounds.width, edge - bounds.left)) }
        })
      })
      cards.forEach(({ card, cut }) => {
        card.toggleAttribute('data-carousel-clipped', cut > 0)
        if (cut > 0) card.style.setProperty('--carousel-cut', `${cut}px`)
        else card.style.removeProperty('--carousel-cut')
      })
    }
    updateCardEdges()
    rows.forEach(row => row.addEventListener('scroll', updateCardEdges, { passive: true }))
    window.addEventListener('resize', updateCardEdges)
    return () => {
      rows.forEach(row => row.removeEventListener('scroll', updateCardEdges))
      window.removeEventListener('resize', updateCardEdges)
    }
  }, [projects, collections])

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
    <div className="dashboard-canvas">
      <button className="dashboard-settings" type="button" disabled aria-label="全局设置（暂未提供）" title="全局设置暂未提供"><img src={icon('setting')} alt="" /></button>
      <h1 id="route-title" className="dashboard-greeting">{greeting()}</h1>
      <div className="dashboard-main" aria-labelledby="route-title">
        {/* Recent Novels */}
        <section className="dashboard-section dashboard-recent" aria-labelledby="recent-title">
          <h2 id="recent-title"><img src={icon('akar-icons_clock')} alt="" />最近创作</h2>
          <div className="dashboard-carousel">
            <div className={`dashboard-row recent-row${projects?.length === 0 ? ' is-empty' : ''}`} ref={recentRow} onScroll={(event) => setRecentIndex(Math.round(event.currentTarget.scrollLeft / 295))}>
              {projects === null && !failed && Array.from({ length: 4 }, (_, index) => <div className="dashboard-skeleton recent-card" key={index} />)}
              {recent.map((project) => <button className="recent-card project-card" type="button" key={project.id} onClick={() => setSelected(project)} aria-label={`Open ${project.title}`}>
                <span className="recent-art"><img src={artwork(project)} alt="" /></span>
                <span className="recent-info"><strong>{project.title}</strong><span><span>字数：{wordCount(project)}</span><time dateTime={project.updated_at}>{relativeTime(project.updated_at)}</time></span></span>
              </button>)}
              {projects?.length === 0 && <p className="dashboard-empty">还没有作品，新建一部开始创作。</p>}
            </div>
            {recent.length > 3 && <>
              <button className="carousel-arrow carousel-previous" type="button" disabled={recentIndex === 0} onClick={() => moveCarousel(recentRow.current, -1, 295, recentMaxIndex, setRecentIndex)} aria-label="查看更早作品"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
              <button className="carousel-arrow carousel-next" type="button" disabled={recentIndex === recentMaxIndex} onClick={() => moveCarousel(recentRow.current, 1, 295, recentMaxIndex, setRecentIndex)} aria-label="查看更多最近作品"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
            </>}
          </div>
        </section>

        {/* Novels */}
        <section className="dashboard-section dashboard-novels" aria-labelledby="novels-title">
          <div className="dashboard-section-heading">
            <h2 id="novels-title"><img src={icon('akar-icons_book')} alt="" />作品</h2>
            <button className="novel-add" type="button" onClick={() => setCreating(true)} aria-label="新建作品"><img src={icon('bx_message-square-add')} alt="" /></button>
          </div>
          <div className="dashboard-carousel">
            <div className={`dashboard-row novels-row${projects?.length === 0 ? ' is-empty' : ''}`} ref={novelsRow} onScroll={(event) => setNovelIndex(Math.round(event.currentTarget.scrollLeft / 171))}>
              {projects === null && !failed && Array.from({ length: 6 }, (_, index) => <div className="dashboard-skeleton novel-card" key={index} />)}
              {projects?.length === 0 && <button className="dashboard-empty-create" type="button" onClick={() => setCreating(true)} aria-label="创建第一部作品">
                <span className="empty-create-icon" aria-hidden="true">+</span>
                <span><strong>写下你的第一部作品</strong><span>从一个名字、一个念头开始。</span></span>
                <span className="empty-create-arrow" aria-hidden="true">↗</span>
              </button>}
              {projects?.map((project) => <button className="novel-card project-card" type="button" key={project.id} onClick={() => setSelected(project)} aria-label={`Open ${project.title}`}>
                <span className="novel-art"><img src={artwork(project)} alt="" /></span>
                <span className="novel-info"><strong>{project.title}</strong><span>字数：{wordCount(project)}</span><time dateTime={project.updated_at}>{relativeTime(project.updated_at)}</time></span>
              </button>)}
            </div>
            {(projects?.length ?? 0) > 5 && <>
              <button className="carousel-arrow carousel-previous" type="button" disabled={novelIndex === 0} onClick={() => moveCarousel(novelsRow.current, -1, 171, novelMaxIndex, setNovelIndex)} aria-label="Show previous novels"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
              <button className="carousel-arrow carousel-next" type="button" disabled={novelIndex === novelMaxIndex} onClick={() => moveCarousel(novelsRow.current, 1, 171, novelMaxIndex, setNovelIndex)} aria-label="Show more novels"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
            </>}
          </div>
        </section>

        {/* Setting Collections */}
        <section className="dashboard-section dashboard-collections" aria-labelledby="collections-title">
          <div className="dashboard-section-heading">
            <h2 id="collections-title"><img src={icon('setting')} alt="" />设定集</h2>
            <button className="collection-add" type="button" onClick={() => setCreatingCollection(true)} aria-label="新建设定集"><img src={icon('bx_message-square-add')} alt="" /></button>
          </div>
          <div className="dashboard-carousel">
            <div className={`dashboard-row collections-row${collections?.length === 0 ? ' is-empty' : ''}`} ref={collectionsRow} onScroll={(event) => setCollectionIndex(Math.round(event.currentTarget.scrollLeft / 220))}>
              {collections === null && !collectionsFailed && Array.from({ length: 4 }, (_, index) => <div className="dashboard-skeleton collection-card" key={index} />)}
              {collections?.map((col) => {
                const refCount = projects?.filter((p) => p.setting_collection_id === col.id).length ?? 0
                return (
                  <button
                    className="collection-card project-card"
                    type="button"
                    key={col.id}
                    onClick={() => navigate(`/setting-collections/${encodeURIComponent(col.id)}`)}
                    aria-label={`Open ${col.title} setting collection`}
                  >
                    <span className="collection-art">
                      <img src={collectionArtwork(col)} alt="" />
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
              {collections?.length === 0 && <p className="dashboard-empty">还没有设定集，可以新建或随作品自动创建。</p>}
            </div>
            {(collections?.length ?? 0) > 4 && <>
              <button className="carousel-arrow carousel-previous" type="button" disabled={collectionIndex === 0} onClick={() => moveCarousel(collectionsRow.current, -1, 220, collectionMaxIndex, setCollectionIndex)} aria-label="Show previous setting collections"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
              <button className="carousel-arrow carousel-next" type="button" disabled={collectionIndex === collectionMaxIndex} onClick={() => moveCarousel(collectionsRow.current, 1, 220, collectionMaxIndex, setCollectionIndex)} aria-label="Show more setting collections"><img src={icon('akar-icons_arrow-right')} alt="" /></button>
            </>}
          </div>
        </section>

        {projects === null && !failed && <span className="dashboard-sr-only" role="status">Loading projects…</span>}
        {failed && <p className="dashboard-load-error notice" role="alert">作品加载失败，请检查网络后刷新。</p>}

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
          {showResults && <div className="search-results" role="region" aria-label="搜索结果">
            {matches.map((project) => <button type="button" key={project.id} onClick={() => setSelected(project)}>{project.title}</button>)}
            {collectionMatches.map((col) => (
              <button
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
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} aria-label="搜索作品" />
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
