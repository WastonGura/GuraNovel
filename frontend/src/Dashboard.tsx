import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import CoverImage from './CoverImage'
import DashboardSearch from './DashboardSearch'
import CreationDialog, { DialogDismissButton } from './CreationDialog'
import { NovelDialog, NovelFields } from './NovelDialog'
import { novelCoverSource } from './novelCover'
import { useNovelMetadata } from './useNovelMetadata'
import { emptyNovelForm } from './novelForm'
import {
  createProject,
  createSettingCollection,
  listChapters,
  listProjects,
  listSettingCollections,
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
  const [values, setValues] = useState(emptyNovelForm)
  const { title, platform, collectionId: selectedCollectionId, introduction, cover } = values
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [generatedSlug] = useState(() => `novel-${crypto.randomUUID()}`)
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
    submitting.current = true
    setPending(true)
    setError(null)
    try {
      const payload: CreateProjectRequest = {
        slug: generatedSlug,
        title: title.trim(),
        target_platform: platform.trim() || null,
      }
      if (selectedCollectionId && selectedCollectionId !== 'new') {
        payload.setting_collection_id = selectedCollectionId
      }
      const metadata: Metadata = {}
      if (introduction.trim()) metadata.introduction = introduction.trim()
      if (cover.trim()) metadata.cover = cover.trim()
      if (Object.keys(metadata).length > 0) {
        payload.metadata = metadata
      }

      const project = await createProject(payload)
      onCreated?.(project)
      navigate(`/projects/${encodeURIComponent(project.id)}/studio`)
    } catch (cause) {
      const status = cause && typeof cause === 'object' && 'status' in cause ? cause.status : undefined
      setError(status === 409
        ? '创建请求可能已经完成，请先关闭窗口检查书架，再决定是否重新创建。'
        : '创建失败，已保留填写内容。请检查网络后重试；如果响应丢失，请先关闭弹窗检查书架。')
    } finally {
      submitting.current = false
      setPending(false)
    }
  }

  return (
    <NovelDialog titleId="create-project-title" closeLabel="关闭创建作品" busy={pending} onClose={() => { if (!submitting.current) onClose() }} cover={novelCoverSource(cover)} onCoverChange={cover => setValues(previous => ({ ...previous, cover }))} title={title}>
      <form className="novel-dialog-form" onSubmit={submit} aria-label="创建作品">
        <header className="novel-dialog-header"><h2 id="create-project-title">创建作品</h2></header>
        <div className="novel-panel-scroll">
          <NovelFields values={values} onChange={patch => setValues(previous => ({ ...previous, ...patch }))} collections={collections} busy={pending} errorId={error ? 'create-project-error' : undefined} />
          {error && <p ref={errorRef} id="create-project-error" className="creation-error" role="alert" tabIndex={-1}>{error}</p>}
        </div>
        <footer className="novel-dialog-footer novel-create-footer">
          <button className="novel-primary-action" type="submit" disabled={pending}><img src={icon('book-plus')} alt="" />{pending ? '正在创建作品…' : '创建作品'}</button>
        </footer>
      </form>
    </NovelDialog>
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
          <DialogDismissButton className="creation-cancel" disabled={pending}>取消</DialogDismissButton>
          <button className="creation-submit" type="submit" disabled={pending}>{pending ? '正在创建设定集…' : '新建设定集'}<span aria-hidden="true">↗</span></button>
        </footer>
      </form>
    </CreationDialog>
  )
}

function NovelDetails({ project, artwork, collections, onClose, onProjectUpdated }: {
  project: Project; artwork: string; collections: SettingCollection[]
  onClose: () => void; onProjectUpdated?: (updated: Project) => void
}) {
  const navigate = useNavigate()
  const { state } = useLocation()
  const retiredNotice = state?.retiredWorkspace === 'maintenance'
    ? '项目维护暂未提供操作入口。已有任务和历史数据仍保留。'
    : state?.retiredWorkspace === 'creation'
      ? '作品构思暂未提供操作入口。你可以打开章节继续创作，已有任务和历史数据仍保留。' : ''
  const [overrideProject, setOverrideProject] = useState<Project | null>(null)
  const currentProject = overrideProject && overrideProject.id === project.id ? overrideProject : project
  const [chapters, setChapters] = useState<Chapter[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [expanded, setExpanded] = useState(true)
  const [editing, setEditing] = useState(false)
  const editButton = useRef<HTMLButtonElement>(null)
  const editForm = useRef<HTMLDivElement>(null)
  const metadata = useNovelMetadata(project, updated => { setOverrideProject(updated); onProjectUpdated?.(updated) })
  const { values, error: saveError } = metadata

  useEffect(() => {
    let active = true
    listChapters(currentProject.id).then(
      loaded => { if (active) setChapters(loaded.slice().sort((a, b) => b.chapter_number - a.chapter_number)) },
      () => { if (active) setFailed(true) },
    )
    return () => { active = false }
  }, [currentProject.id])
  useEffect(() => {
    if (editing) editForm.current?.querySelector<HTMLInputElement>('input')?.focus()
  }, [editing])

  function showDirectory() { setEditing(false); requestAnimationFrame(() => editButton.current?.focus()) }
  async function openWorkspace(path: string) { if (await metadata.flush()) navigate(path) }

  const status = ({ draft: '草稿', active: '创作中', completed: '已完成', archived: '已归档' } as Record<string, string>)[currentProject.status] || '状态待核对'

  return <NovelDialog titleId="novel-detail-title" coverTitleId="novel-detail-title" title={values.title || '未命名作品'} cover={novelCoverSource(values.cover, artwork)} onCoverChange={cover => metadata.change({ cover })}
    closeLabel="关闭作品详情" busy={false} beforeClose={async () => saveError || metadata.saving ? metadata.keepDraft() : await metadata.flush() || metadata.keepDraft()} onClose={onClose}>
    <section className="novel-view-panel" hidden={editing} aria-label="章节列表">
      <div className="novel-panel-scroll" tabIndex={0} aria-label="章节列表">
        {retiredNotice && <p className="detail-status" role="status">{retiredNotice}</p>}
        {chapters === null && !failed && <p className="detail-status" role="status">正在加载章节…</p>}
        {failed && <p className="detail-status" role="alert">章节加载失败，请关闭后重新打开作品。</p>}
        {chapters?.length === 0 && <div className="novel-directory-empty"><p>故事从第一章开始</p><span>还没有章节</span></div>}
        {!!chapters?.length && <>
          <button className="novel-volume-heading" type="button" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>第一卷<span aria-hidden="true">{expanded ? '−' : '+'}</span></button>
          {expanded && <ul className="novel-chapter-list">{chapters.map(chapter => <li key={chapter.id}><button type="button" onClick={() => { void openWorkspace(`/projects/${encodeURIComponent(currentProject.id)}/studio/${encodeURIComponent(chapter.id)}`) }}>
            <span>第{chapter.chapter_number}话</span><span>{chapter.title || '未命名章节'}</span><span aria-hidden="true">↗</span>
          </button></li>)}</ul>}
        </>}
      </div>
    </section>
    <section className="novel-view-panel" hidden={!editing} aria-label="编辑作品资料">
      <div className="novel-panel-scroll">
        {editing && <div ref={editForm} id="novel-metadata-form" role="form" aria-label="编辑作品资料">
          <NovelFields values={values} onChange={metadata.change} collections={collections} busy={false} editing errorId={saveError ? 'novel-save-error' : undefined}
            status={<span className="novel-status" aria-label={`状态：${status}`}>{status}</span>}
            summary={<><span aria-label={`字数：${wordCount(currentProject)}`}><img src="/ui/studio/size.svg" alt="" /><span>字数</span><strong>{wordCount(currentProject)}</strong></span><span aria-label={`章节：${chapters?.length ?? '—'}`}><img src="/ui/studio/chapter.svg" alt="" /><span>章节</span><strong>{chapters?.length ?? '—'}</strong></span></>}
            />
          </div>}

      </div>
    </section>
    {saveError && <div id="novel-save-error" className="novel-autosave-error" role="alert"><span>{saveError}</span><button className="novel-text-action" type="button" disabled={metadata.saving} onClick={() => { void metadata.flush() }}>重试同步</button><DialogDismissButton className="novel-text-action">保留修改并关闭</DialogDismissButton></div>}
    <footer className="novel-dialog-footer">
      {editing ? <button className="novel-text-action" type="button" onClick={showDirectory}>章节目录</button>
        : <button ref={editButton} className="novel-text-action" type="button" onClick={() => setEditing(true)}>编辑资料</button>}
      <button className="novel-primary-action" type="button" onClick={() => { void openWorkspace(`/projects/${encodeURIComponent(currentProject.id)}/studio`) }}><img src={icon('rename')} alt="" />开始创作</button>
    </footer>
  </NovelDialog>
}

export default function Dashboard() {
  const recentRow = useRef<HTMLDivElement>(null)
  const novelsRow = useRef<HTMLDivElement>(null)
  const collectionsRow = useRef<HTMLDivElement>(null)
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
  const [searching, setSearching] = useState(false)
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

  useEffect(() => {
    const openSearch = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !document.querySelector('dialog[open]')) {
        event.preventDefault(); setSearching(true)
      }
    }
    window.addEventListener('keydown', openSearch)
    return () => window.removeEventListener('keydown', openSearch)
  }, [])

  const recent = projects?.slice().sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 4) ?? []
  const moveCarousel = (row: HTMLDivElement | null, direction: -1 | 1, step: number, maxIndex: number, setIndex: (index: number) => void) => {
    if (!row) return
    const next = Math.max(0, Math.min(maxIndex, Math.round(row.scrollLeft / step) + direction))
    setIndex(next)
    row.scrollTo({ left: next * step, behavior: 'smooth' })
  }
  const artwork = (project: Project) => novelCoverSource(project.metadata.cover)
  const collectionArtwork = (col: SettingCollection) =>
    asset(`covers/cover-${Math.max(0, collections?.indexOf(col) ?? 0) % 8 + 1}.jpg`)
  const recentMaxIndex = Math.max(0, recent.length - 3)
  const novelMaxIndex = Math.max(0, (projects?.length ?? 0) - 5)
  const collectionMaxIndex = Math.max(0, (collections?.length ?? 0) - 4)

  return (
    <div className="dashboard-canvas">
      <nav className="dashboard-toolbar" aria-label="书架工具">
      <button className="dashboard-settings" type="button" disabled aria-label="全局设置（暂未提供）" title="全局设置暂未提供"><img src={icon('setting')} alt="" /></button>
      <button className="dashboard-search-trigger" type="button" aria-label="搜索" title="搜索 (Ctrl / ⌘ K)" aria-haspopup="dialog" onClick={() => setSearching(true)}><img src="/ui/setting/search.svg" alt="" /></button>
      </nav>
      <h1 id="route-title" className="dashboard-greeting">{greeting()}</h1>
      <div className="dashboard-main" aria-labelledby="route-title">
        {/* Recent Novels */}
        <section className="dashboard-section dashboard-recent" aria-labelledby="recent-title">
          <h2 id="recent-title"><img src={icon('akar-icons_clock')} alt="" />最近创作</h2>
          <div className="dashboard-carousel">
            <div className={`dashboard-row recent-row${projects?.length === 0 ? ' is-empty' : ''}`} ref={recentRow} onScroll={(event) => setRecentIndex(Math.round(event.currentTarget.scrollLeft / 295))}>
              {projects === null && !failed && Array.from({ length: 4 }, (_, index) => <div className="dashboard-skeleton recent-card" key={index} />)}
              {recent.map((project, index) => <button className="recent-card project-card" type="button" key={project.id} onClick={() => setSelected(project)} aria-label={`Open ${project.title}`}>
                <span className="recent-art"><CoverImage source={artwork(project)} priority={index === 0} widthHint={280} /></span>
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
            <button className="novel-add" type="button" onClick={() => setCreating(true)} aria-label="新建作品"><img src={icon('book-plus')} alt="" /></button>
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
                <span className="novel-art"><CoverImage source={artwork(project)} /></span>
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
            <h2 id="collections-title"><img src={icon('collection')} alt="" />设定集</h2>
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
                      <CoverImage source={collectionArtwork(col)} widthHint={220} />
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


      </div>
      {searching && <DashboardSearch projects={projects} collections={collections} failed={failed} collectionsFailed={collectionsFailed}
        onClose={() => setSearching(false)} onProject={setSelected} onCollection={id => navigate(`/setting-collections/${encodeURIComponent(id)}`)} />}
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
