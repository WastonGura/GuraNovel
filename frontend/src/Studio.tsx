import { useEffect, useImperativeHandle, useRef, useState, type ButtonHTMLAttributes, type Ref } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { flushSync } from 'react-dom'
import { getProject, listChapters, readDocumentContent, type Project } from './api/client'
import { initialPreview, newStudioChapter, outlineOptions, previewIssues, previewProse, readerPersonas, stages, type Stage, type StudioChapter } from './studioPreview'
import { useDraftAutosave } from './useDraftAutosave'
import './studio.css'

const asset = (name: string) => name === 'setting' ? '/ui/icons/setting.svg' : `/ui/studio/${name}.svg`
const storageKey = 'guranovel:studio-preview:v1'
type Page = 'Detail' | 'Create' | 'Setting'
type Focus = 'off' | 'auto' | 'manual'
type Notice = { id: string; chapterId: string; message: string; read: boolean }

function IconButton({ icon, label, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { icon: string; label: string }) {
  return <button type="button" className={`studio-icon ${['send', 'review', 'restorepoint', 'publish', 'check'].includes(icon) ? 'is-round-asset' : ''} ${className}`} title={label} aria-label={label} data-glow {...props}><img src={asset(icon)} alt="" /></button>
}

function Pin({ pinned, onChange, label }: { pinned: boolean; onChange: () => void; label: string }) {
  return <IconButton icon={pinned ? 'pin' : 'unpin'} label={`${pinned ? '取消固定' : '固定'}${label}`} aria-pressed={pinned} onClick={onChange} />
}

function PublishDialog({ onClose, onConfirm }: { onClose: () => void; onConfirm: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  return <dialog ref={dialog} className="studio-confirm" onCancel={onClose} onClick={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <h2>确认发布这一章？</h2><p>发布后正文将变为只读。</p><p className="studio-muted">当前为交互预览，不会发布真实章节。</p>
    <div className="studio-actions"><button onClick={onClose}>再看一遍</button><button className="studio-primary" onClick={onConfirm}>确认发布</button></div>
  </dialog>
}

type EditorHandle = { flush: () => Promise<boolean> }
function ProseEditor({ chapter, preview, onChange, onSaved, onTyping, ref, locate }: {
  chapter: StudioChapter; preview: boolean; onChange: (draft: string) => void; onSaved: (versionId: string, text: string) => void; onTyping: () => void; ref: Ref<EditorHandle>; locate: string
}) {
  const save = useDraftAutosave(chapter.documentId, chapter.versionId, chapter.draft, onSaved)
  const input = useRef<HTMLTextAreaElement>(null)
  const readonly = chapter.published || chapter.review === 'running' || chapter.stage !== 'Draft' || (!preview && !chapter.documentId)
  useImperativeHandle(ref, () => ({ flush: preview ? async () => true : save.flush }))
  useEffect(() => {
    if (!locate || !input.current) return
    const start = input.current.value.indexOf(locate)
    if (start >= 0) { input.current.focus(); input.current.setSelectionRange(start, start + locate.length) }
  }, [locate])
  return <div className="studio-prose">
    <textarea ref={input} aria-label="章节正文" readOnly={readonly} value={preview ? chapter.draft : save.text}
      placeholder="从第一句话开始…" spellCheck={false}
      onKeyDown={(event) => { if (!readonly && !event.ctrlKey && !event.metaKey && !event.altKey && (event.key.length === 1 || ['Enter', 'Backspace', 'Delete', 'Process'].includes(event.key))) onTyping() }}
      onCompositionStart={() => { if (!readonly) onTyping() }}
      onPaste={() => { if (!readonly) onTyping() }}
      onChange={(event) => { if (!preview) save.change(event.target.value); onChange(event.target.value) }} />
    {!readonly && <div className="studio-save-status" role="status">{preview ? '自动保存到本机 · 交互预览' : save.status}
      {!preview && save.status.includes('失败') && <button onClick={() => void save.flush()}>重试自动保存</button>}
      {!preview && save.status.includes('冲突') && <button onClick={save.reloadServer}>使用服务器版本</button>}
    </div>}
    {readonly && <p className="studio-save-status">{chapter.published ? '已发布 · 只读' : '当前阶段为只读预览'}</p>}
  </div>
}

export default function Studio() {
  const { projectId, chapterId } = useParams()
  const preview = !projectId
  const [loaded, setLoaded] = useState<{ title: string; chapters: StudioChapter[]; project?: Project } | null>(() => preview ? { title: '动量干涉：Momentum Zero', chapters: initialPreview() } : null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!projectId) return
    let active = true
    Promise.all([getProject(projectId), listChapters(projectId)]).then(async ([project, chapters]) => {
      const items = await Promise.all(chapters.map(async chapter => {
        const documentId = chapter.final_document_id || chapter.current_draft_document_id
        const [draft, outline] = await Promise.all([
          documentId ? readDocumentContent(documentId) : null,
          chapter.current_outline_document_id ? readDocumentContent(chapter.current_outline_document_id) : null,
        ])
        return { ...newStudioChapter(chapter.id, chapter.chapter_number, chapter.title || '未命名章节', typeof chapter.metadata.volume === 'string' ? chapter.metadata.volume : '第一卷'),
          draft: draft?.content || '', outline: outline?.content || '', documentId: draft?.document_id, versionId: draft?.version_id,
          stage: chapter.final_document_id ? 'Final' as const : draft ? 'Draft' as const : 'Outline' as const,
          published: Boolean(chapter.final_document_id), outlineStep: outline ? 'edit' as const : 'new' as const,
        }
      }))
      if (active) setLoaded({ title: project.title, chapters: items, project })
    }).catch(() => { if (active) setError('创作区加载失败，请重试。没有使用示例数据替代真实章节。') })
    return () => { active = false }
  }, [projectId])
  if (error) return <div className="studio-load"><p role="alert">{error}</p><button onClick={() => window.location.reload()}>重新加载</button><Link to="/">返回首页</Link></div>
  if (!loaded) return <div className="studio-load" role="status">正在打开创作区…</div>
  return <StudioWorkspace key={projectId || 'preview'} title={loaded.title} initial={loaded.chapters} initialId={chapterId} project={loaded.project} preview={preview} />
}

function StudioWorkspace({ title, initial, initialId, project, preview }: {
  title: string; initial: StudioChapter[]; initialId?: string; project?: Project; preview: boolean
}) {
  const navigate = useNavigate()
  const [chapters, setChapters] = useState(() => {
    if (!preview) return initial
    // Only restore editable text, never trust cached workflow/approval states.
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(storageKey) || 'null')
      if (saved && typeof saved === 'object') {
        const restored = initial.slice()
        for (const [id, value] of Object.entries(saved)) {
          const index = restored.findIndex(chapter => chapter.id === id)
          if (typeof value === 'string' && index >= 0) restored[index] = { ...restored[index], draft: value }
          else if (value && typeof value === 'object' && typeof value.draft === 'string') {
            if (index >= 0) restored[index] = { ...restored[index], draft: value.draft }
            else if (Number.isSafeInteger(value.number) && value.number > 0 && typeof value.title === 'string' && typeof value.volume === 'string' && typeof value.outline === 'string') {
              restored.push({ ...newStudioChapter(id, value.number, value.title, value.volume), draft: value.draft, outline: value.outline, outlineStep: 'edit', stage: 'Draft' })
            }
          }
        }
        return restored
      }
    } catch { /* An invalid preview cache must not prevent opening the editor. */ }
    return initial
  })
  const [selectedId, setSelectedId] = useState(initialId || chapters.at(-1)?.id || '')
  const [page, setPage] = useState<Page>('Create')
  const [focus, setFocus] = useState<Focus>('off')
  const [pins, setPins] = useState({ directory: false, stats: false, context: false })
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [contextOutline, setContextOutline] = useState(false)
  const [notifications, setNotifications] = useState<Notice[]>([])
  const [noticesOpen, setNoticesOpen] = useState(false)
  const [toast, setToast] = useState('')
  const [storageError, setStorageError] = useState('')
  const [publishing, setPublishing] = useState(false)
  const [locate, setLocate] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [volumes, setVolumes] = useState([...new Set(chapters.map(chapter => chapter.volume))])
  const [comment, setComment] = useState('')
  const [commentOpen, setCommentOpen] = useState(false)
  const [introduction, setIntroduction] = useState(typeof project?.metadata?.introduction === 'string' ? project.metadata.introduction : '')
  const [started] = useState(Date.now)
  const [seconds, setSeconds] = useState(0)
  const editor = useRef<EditorHandle>(null)
  const timers = useRef<number[]>([])
  const currentChapters = useRef(chapters)
  const root = useRef<HTMLDivElement>(null)
  const selected = chapters.find(chapter => chapter.id === selectedId)
  const unread = notifications.filter(notice => !notice.read).length
  const hidden = focus !== 'off'
  const writing = page === 'Create' && selected && ['Draft', 'Review', 'Final'].includes(selected.stage)

  useEffect(() => {
    const interval = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000)
    const pending = timers.current
    return () => { window.clearInterval(interval); pending.forEach(window.clearTimeout) }
  }, [started])

  useEffect(() => {
    const move = () => setFocus(value => value === 'auto' ? 'off' : value)
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setFocus('off'); setNoticesOpen(false); setCommentOpen(false) }
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('keydown', escape)
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('keydown', escape) }
  }, [])

  function update(id: string, patch: Partial<StudioChapter>) {
    const next = currentChapters.current.map(chapter => chapter.id === id ? { ...chapter, ...patch } : chapter)
    currentChapters.current = next
    setChapters(next)
    if (preview && patch.draft !== undefined) {
      try { localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(next.map(({ id, number, title, volume, outline, draft }) => [id, { number, title, volume, outline, draft }])))); setStorageError('') }
      catch { setStorageError('本机自动保存失败，请先复制正文备份。') }
    }
  }

  async function leave(action: () => void) {
    if (editor.current && !await editor.current.flush()) { setToast('正文尚未保存，已暂停切换。请先重试自动保存。'); return }
    const commit = () => { setFocus(value => value === 'auto' ? 'off' : value); setLocate(''); action() }
    if (document.startViewTransition && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      document.startViewTransition(() => flushSync(commit))
    } else commit()
  }
  function selectChapter(id: string) { void leave(() => { setSelectedId(id); setPage('Create'); setCommentOpen(false) }) }
  function changeStage(stage: Stage) {
    if (!selected || selected.published || stage === selected.stage) return
    if (!preview && stage !== 'Draft' && stage !== 'Outline') { setToast('新的审阅与发布流程尚待后端接入，请使用现有工作台。'); return }
    if (stage === 'Review') {
      if (selected.review === 'idle') startReview(selected.id)
      else void leave(() => update(selected.id, { stage }))
      return
    }
    if (stage === 'Draft' && !selected.outline.trim()) { setToast('请先确认本章大纲。'); return }
    if ((stage === 'Reader' || stage === 'Final') && (selected.review !== 'done' || selected.issues.some(issue => issue.level === 'Block'))) {
      setToast('审阅完成并处理 Block 问题后，才能进入下一阶段。'); return
    }
    void leave(() => update(selected.id, { stage }))
  }
  function startReview(id: string, revisedIds: string[] = []) {
    const chapter = currentChapters.current.find(item => item.id === id)
    if (!preview) { setToast('三方联合审阅尚待接口接入。当前不发起真实审阅。'); return }
    if (!chapter?.draft.trim() || chapter.review === 'running' || chapter.published) return
    void leave(() => {
      const issues = revisedIds.length ? chapter.issues.filter(issue => !revisedIds.includes(issue.id)) : previewIssues
      let draft = chapter.draft
      if (revisedIds.includes('timeline')) draft = draft.replace('雨是在黄昏停下来的。', '又过了几个小时，雨终于在黄昏停了下来。')
      if (revisedIds.includes('motivation')) draft = draft.replace('却让窗边的女孩停住了筷子。', '窗边的女孩认出了碎片上的刻痕，停住了筷子。')
      if (revisedIds.includes('ending')) draft = draft.replace('林远突然觉得，这个再平常不过的傍晚，也许并不只是一个傍晚。', '林远把金属碎片握回掌心，没有再看窗边。')
      update(id, { stage: 'Review', review: 'running', completed: 0, issues: [], selected: [], draft })
      ;[1400, 2700, 4000].forEach((delay, index) => timers.current.push(window.setTimeout(() => {
        update(id, { completed: index + 1, ...(index === 2 ? { review: 'done' as const, issues, selected: issues.filter(issue => issue.level === 'Block').map(issue => issue.id) } : {}) })
        if (index === 2) {
          setNotifications(previous => [...previous, { id: crypto.randomUUID(), chapterId: id, message: `第${chapter.number}话审阅完成${issues.length ? ` · ${issues.length} 个问题` : ' · 未发现问题'}（预览）`, read: false }])
        }
      }, delay)))
    })
  }
  function addChapter(volume: string) {
    if (!preview) { setToast('请在现有工作台新建章节；分卷接口暂未接入。'); return }
    void leave(() => {
      const number = Math.max(0, ...currentChapters.current.map(item => item.number)) + 1
      const chapter = newStudioChapter(crypto.randomUUID(), number, '未命名章节', volume)
      const next = [...currentChapters.current, chapter]
      currentChapters.current = next; setChapters(next); setSelectedId(chapter.id); setPage('Create')
    })
  }
  function publish() {
    if (!selected || !preview || selected.review !== 'done' || selected.issues.some(issue => issue.level === 'Block')) return
    update(selected.id, { published: true }); setPublishing(false); setToast('预览章节已发布，正文已锁定为只读。')
  }

  return <div ref={root} className={`studio${hidden ? ' is-focused' : ''}`} data-focus-mode={focus}
    onPointerMove={event => {
      // Proximity light follows the pointer, including just outside each target.
      root.current?.querySelectorAll<HTMLElement>('[data-glow]').forEach(target => {
        const rect = target.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top
        target.style.setProperty('--glow-x', `${x}px`); target.style.setProperty('--glow-y', `${y}px`)
        target.style.setProperty('--glow-opacity', x > -35 && x < rect.width + 35 && y > -35 && y < rect.height + 35 ? '.65' : '0')
      })
    }} onPointerLeave={() => root.current?.querySelectorAll<HTMLElement>('[data-glow]').forEach(target => target.style.setProperty('--glow-opacity', '0'))}
    onClickCapture={event => {
      const target = (event.target as HTMLElement).closest<HTMLElement>('[data-glow]')
      if (!target) return
      const rect = target.getBoundingClientRect()
      target.style.setProperty('--glow-x', `${event.detail ? event.clientX - rect.left : rect.width / 2}px`)
      target.style.setProperty('--glow-y', `${event.detail ? event.clientY - rect.top : rect.height / 2}px`)
      target.classList.remove('is-glow-burst'); void target.offsetWidth; target.classList.add('is-glow-burst')
    }} onAnimationEnd={event => { if (event.animationName === 'glow-burst') (event.target as HTMLElement).classList.remove('is-glow-burst') }}>
    <header className="studio-header">
      <div className="studio-tools">
        <IconButton icon="setting" label="设置" className={`studio-chrome${hidden ? ' is-hidden' : ''}`} onClick={() => void leave(() => setPage('Setting'))} />
        <IconButton icon={focus === 'manual' ? 'show' : 'focus'} label={focus === 'manual' ? '退出免打扰' : '手动免打扰'} aria-pressed={focus === 'manual'} onClick={() => { setFocus(value => value === 'manual' ? 'off' : 'manual'); setNoticesOpen(false) }} />
        <div className="studio-notification-button"><IconButton icon="notification" label={`通知${unread ? `，${unread} 条未读` : ''}`} aria-expanded={noticesOpen} onClick={() => { setNoticesOpen(!noticesOpen); if (!noticesOpen) setNotifications(items => items.map(item => ({ ...item, read: true }))) }} />{unread > 0 && <i />}</div>
      </div>
      <nav className={`studio-workflow studio-chrome${hidden || page !== 'Create' ? ' is-hidden' : ''}`} aria-label="创作阶段">
        {stages.map(stage => <button key={stage} aria-current={selected?.stage === stage ? 'step' : undefined} onClick={() => changeStage(stage)} disabled={!selected || selected.published} title={selected?.published ? '已发布章节不可修改' : stage}>{stage}<i /></button>)}
      </nav>
      <nav className={`studio-page-nav studio-chrome${hidden ? ' is-hidden' : ''}`} aria-label="小说页面">
        <IconButton icon="next" className="is-previous" label="上一个页面" disabled={page === 'Detail'} onClick={() => void leave(() => setPage(page === 'Setting' ? 'Create' : 'Detail'))} />
        <strong>{page}</strong>
        <IconButton icon="next" label="下一个页面" disabled={page === 'Setting'} onClick={() => void leave(() => setPage(page === 'Detail' ? 'Create' : 'Setting'))} />
      </nav>
    </header>
    {noticesOpen && <section className="studio-notifications" aria-label="通知列表"><h2>通知</h2>{!notifications.length && <p className="studio-muted">暂无通知</p>}{notifications.slice().reverse().map(notice => <button key={notice.id} onClick={() => { selectChapter(notice.chapterId); setNoticesOpen(false) }}>{notice.message}<small>查看本章报告</small></button>)}</section>}
    <div className="studio-announcer" role="status" aria-live="polite">{notifications.at(-1)?.message}</div>
    <section className={`studio-body${writing ? ' has-prose' : ''}`} aria-label={`${page} 工作区`}>
      {page === 'Setting' ? <div className="studio-setting" aria-label="Setting 留白" /> : page === 'Detail' ? <div className="studio-detail studio-enter">
        <div className="studio-detail-art">
          <img className="studio-detail-banner" src="/ui/studio/detail-banner.png" alt="小说横版封面" />
          <img className="studio-detail-cover" src="/ui/studio/detail-cover.png" alt="小说封面" />
        </div>
        <div className="studio-detail-information">
          <div className="studio-detail-title"><h1>{title}</h1><span className="studio-project-status"><i />{project?.status || 'Ongoing'}</span></div>
          <dl className="studio-detail-metadata">
            <div><dt><img src={asset('size')} alt="" />size</dt><dd>{chapters.reduce((total, chapter) => total + chapter.draft.replace(/\s/g, '').length, 0).toLocaleString()}</dd></div>
            <div><dt><img src={asset('chapter')} alt="" />chapter</dt><dd>{chapters.length}</dd></div>
            <div><dt><img src={asset('label')} alt="" />label</dt></div>
          </dl>
          <div className="studio-detail-labels">{(project?.genre ? [project.genre] : preview ? ['都市', '科幻'] : []).map(label => <span key={label}>{label}</span>)}<IconButton icon="add" label="添加标签（接口暂未接入）" disabled /></div>
          <textarea aria-label="小说简介" value={introduction} readOnly={!preview} onChange={event => setIntroduction(event.target.value)} placeholder="introduction…" />
        </div>
      </div> : !selected ? <div className="studio-empty"><h1>开始新的一章</h1><button onClick={() => addChapter(volumes.at(-1) || '第一卷')}>新建章节</button>{!preview && <Link to={`/projects/${project?.id}`}>前往现有工作台</Link>}</div> : <>
        {selected.stage === 'Outline' && <section className={`studio-outline studio-enter is-${selected.outlineStep}`} key={`${selected.id}-outline-${selected.outlineStep}`} aria-label="本章大纲">
          {selected.outlineStep === 'new' && <><h1>想怎么写？</h1><div className="studio-idea-card"><textarea aria-label="大纲思路" placeholder="你的大纲思路…" value={selected.idea} onChange={event => update(selected.id, { idea: event.target.value })} /><IconButton icon="send" label="生成大纲方案" disabled={!selected.idea.trim() || !preview} onClick={() => update(selected.id, { outlineStep: 'choose' })} /></div></>}
          {selected.outlineStep === 'choose' && <><div className="studio-outline-options">{outlineOptions.map((_, i) => { const option = outlineOptions[(i + refresh) % 3]; return <button data-glow key={option.title} onClick={() => update(selected.id, { outline: `${selected.idea}\n\n${option.text}`, outlineStep: 'edit' })}><small>方案 0{i + 1}</small><h2>{option.title}</h2><p>{option.text}</p><span>选择这个方向</span></button> })}</div><button className="studio-refresh" onClick={() => setRefresh(value => value + 1)} title="切换预览方案排列"><img src={asset('refresh')} alt="" />换一组</button></>}
          {selected.outlineStep === 'edit' && <div className="studio-outline-edit"><div className="studio-outline-sheet"><textarea aria-label="编辑大纲" readOnly={!preview} value={selected.outline} onChange={event => update(selected.id, { outline: event.target.value, review: 'idle', issues: [], selected: [] })} /><IconButton icon="check" label="确认大纲并进入正文" disabled={!selected.outline.trim() || !preview} onClick={() => void leave(() => update(selected.id, { stage: 'Draft', draft: selected.draft || previewProse }))} /></div><div className="studio-outline-comment"><span className="studio-comment-dots"><i /><i /><i /></span><label>给 Agent 的修改意见<textarea value={comment} onChange={event => setComment(event.target.value)} placeholder="哪里需要再调整？" /></label><IconButton icon="send" label="发送大纲修改意见" disabled={!comment.trim() || !preview} onClick={() => { update(selected.id, { outline: `${selected.outline}\n\n修改要求：${comment}` }); setComment(''); setToast('修改要求已附加到预览大纲，未调用真实 Agent。') }} /></div></div>}
          {!preview && <p className="studio-integration-note">大纲生成流程待接入。<Link to={`/projects/${project?.id}/chapters/${selected.id}`}>打开现有工作台</Link></p>}
        </section>}
        {writing && <article className="studio-manuscript" aria-label="章节创作">
          <h1>第{selected.number}话 <span>{selected.title}</span></h1>
          <div className={`studio-context-wrap${hidden && !pins.context ? ' is-hidden' : ''}`} inert={hidden && !pins.context}>
            <section className={`studio-context${selected.stage === 'Review' && selected.review === 'done' && !contextOutline ? ' is-report' : ''}`} aria-label="大纲与审阅面板">
              <div className="studio-context-heading"><h2>{contextOutline ? '本章大纲' : selected.stage === 'Review' ? '审阅' : selected.stage === 'Final' ? '本章大纲' : '给写作 Agent 的要求'}</h2><div className="studio-actions"><Pin label="大纲面板" pinned={pins.context} onChange={() => setPins(value => ({ ...value, context: !value.context }))} /><IconButton className={contextOutline ? 'is-lit' : ''} icon="bulb" label={contextOutline ? '显示要求或审阅' : '显示大纲'} aria-pressed={contextOutline} onClick={() => setContextOutline(!contextOutline)} /></div></div>
              {contextOutline || selected.stage === 'Final' ? <p className="studio-outline-text">{selected.outline || '暂无大纲'}</p> : selected.stage === 'Review' ? <>
                {selected.review === 'idle' && <button disabled={!preview} onClick={() => startReview(selected.id)}>开始审阅</button>}
                {selected.review !== 'idle' && <div className="studio-review-log" aria-label="审阅过程">{['Editor Reviewer', 'Chief Reviewer', 'Lore Reviewer'].map((name, i) => <p key={name}><i className={selected.completed > i ? 'is-done' : 'is-running'} />{name} 开始审阅</p>)}{['Editor Reviewer', 'Chief Reviewer', 'Lore Reviewer'].slice(0, selected.completed).map(name => <p key={`${name}-done`} className="studio-review-complete">{name} 完成审阅</p>)}</div>}
                {selected.review === 'done' && <div className="studio-report studio-enter" aria-label="审阅报告"><h3>审阅报告 <small>{selected.issues.length} 个问题 · 示例报告</small></h3><p className="studio-muted">勾选的问题将交给 Agent 修改，Block 为必选项。</p>{selected.issues.map(issue => <div className="studio-issue" key={issue.id}><input type="checkbox" aria-label={`交给 Agent 修改：${issue.title}`} checked={selected.selected.includes(issue.id)} disabled={issue.level === 'Block'} onChange={event => update(selected.id, { selected: event.target.checked ? [...selected.selected, issue.id] : selected.selected.filter(id => id !== issue.id) })} /><div><button className="studio-issue-title" onClick={() => setLocate(issue.quote)}><span className={`studio-severity is-${issue.level.toLowerCase()}`}>{issue.level}</span>{issue.title}</button><p>{issue.detail}</p></div></div>)}{selected.issues.length === 0 && <p>本轮示例审阅未发现问题。</p>}<div className="studio-actions"><button disabled={!selected.selected.length} onClick={() => startReview(selected.id, selected.selected)}>修改所选 {selected.selected.length} 项并重新审阅</button><button disabled={selected.issues.some(issue => issue.level === 'Block')} onClick={() => changeStage('Reader')}>进入读者环节</button></div></div>}
              </> : <><textarea aria-label="给写作 Agent 的要求" value={selected.requirements} placeholder="希望这一段怎样展开？" onChange={event => update(selected.id, { requirements: event.target.value })} /><IconButton icon="send" label="发送写作要求" disabled={!selected.requirements.trim()} onClick={() => setToast('写作要求已保留，Agent 修改接口尚未接入。')} /></>}
            </section>
          </div>
          <ProseEditor key={selected.id} ref={editor} chapter={selected} preview={preview} locate={locate} onSaved={(versionId, draft) => update(selected.id, { versionId, draft })} onTyping={() => setFocus(value => value === 'manual' ? value : 'auto')} onChange={draft => update(selected.id, { draft, review: 'idle', issues: [], selected: [] })} />
          <div className={`studio-manuscript-footer studio-chrome${hidden ? ' is-hidden' : ''}`}>
            {selected.stage === 'Draft' && <><button className="studio-comment-link" onClick={() => setCommentOpen(!commentOpen)}>添加批注</button><div className="studio-actions"><IconButton icon="restorepoint" label="创建还原点（后端暂未接入）" disabled /><IconButton icon="review" label="提交审阅" disabled={!selected.draft.trim()} onClick={() => startReview(selected.id)} /></div></>}
            {selected.stage === 'Final' && <><span className="studio-muted">{selected.published ? '本章已发布' : '最后确认后发布'}</span><IconButton icon="publish" label="发布章节" disabled={selected.published || !preview} onClick={() => setPublishing(true)} /></>}
          </div>
          {commentOpen && <div className="studio-comment-popover"><label>章节批注<textarea value={comment} onChange={event => setComment(event.target.value)} placeholder="写下你的想法…" /></label><button onClick={() => { update(selected.id, { requirements: `${selected.requirements}\n${comment}`.trim() }); setComment(''); setCommentOpen(false) }}>添加到修改要求</button><button onClick={() => setCommentOpen(false)}>关闭</button></div>}
        </article>}
        {selected.stage === 'Reader' && <section className={`studio-reader studio-enter${selected.discussion ? ' is-discussion' : ''}`} aria-label="读者环节" key={`${selected.id}-${selected.discussion}`}>
          {!selected.discussion ? <><h2>邀请读者</h2><div className="studio-reader-list">{readerPersonas.map(([id, name, description]) => <div className="studio-reader-person" key={id}><span className="studio-avatar" aria-hidden="true" /><div><strong>{name}</strong><small>{description}</small></div><button data-glow className={selected.readers.includes(id) ? 'is-invited' : ''} aria-pressed={selected.readers.includes(id)} aria-label={`${selected.readers.includes(id) ? '取消邀请' : '邀请'}${name}`} onClick={() => update(selected.id, { readers: selected.readers.includes(id) ? selected.readers.filter(value => value !== id) : [...selected.readers, id] })}>{selected.readers.includes(id) ? '取消' : '邀请'}</button></div>)}</div><div className="studio-reader-footer"><button onClick={() => changeStage('Final')}>跳过读者环节</button><button className="studio-primary" disabled={!selected.readers.length} onClick={() => update(selected.id, { discussion: true })}>开始阅读</button></div></> : <><div className="studio-reader-transcript"><div className="studio-message"><span className="studio-avatar" /><div><p>主持人 <time>刚刚 · 预览</time></p><button className="studio-file" onClick={() => changeStage('Final')}><img src={asset('chapter')} alt="" />第{selected.number}话 {selected.title}</button></div></div><div className="studio-message"><span className="studio-avatar" /><div><p>主持人 <time>刚刚 · 预览</time></p><span className="studio-accent">@全体成员</span><p>阅读这篇文章并给出意见。</p></div></div>{readerPersonas.filter(([id]) => selected.readers.includes(id)).map(([id, name, description]) => <div className="studio-message" key={id}><span className="studio-avatar" /><div><p>{name} <time>示例发言</time></p><p>我会{description.replace('关注', '重点看')}。这段食堂的日常很有画面感，期待接下来人物之间的关系。</p></div></div>)}</div><div className="studio-reader-footer"><span className="studio-muted">作者旁观 · 无需参与讨论</span><button onClick={() => changeStage('Final')}>结束旁观，进入终稿</button></div></>}
        </section>}
      </>}
    </section>
    <aside className={`studio-sidebar${sidebarOpen ? ' is-open' : ''}`} aria-label="章节侧边栏" onPointerEnter={() => { if (!hidden) setSidebarOpen(true) }} onPointerLeave={() => setSidebarOpen(false)} onFocus={() => { if (!hidden) setSidebarOpen(true) }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setSidebarOpen(false) }}>
      <button className="studio-sidebar-trigger" aria-label="展开章节侧边栏" disabled={hidden} onClick={() => setSidebarOpen(true)}><span /></button>
      <section className={`studio-stats studio-floating${pins.stats ? ' is-pinned' : ''}${hidden && !pins.stats ? ' is-hidden' : ''}`} inert={(hidden || !sidebarOpen) && !pins.stats} aria-label="创作统计"><div><img src={asset('time')} alt="时间" />{Math.floor(seconds / 3600)}h {Math.floor(seconds / 60) % 60}m {seconds % 60}s</div><div><img src={asset('font')} alt="字数" />{(selected?.draft.replace(/\s/g, '').length || 0).toLocaleString()} 字</div><Pin label="统计面板" pinned={pins.stats} onChange={() => setPins(value => ({ ...value, stats: !value.stats }))} /></section>
      <section className={`studio-directory studio-floating${pins.directory ? ' is-pinned' : ''}${hidden && !pins.directory ? ' is-hidden' : ''}`} inert={(hidden || !sidebarOpen) && !pins.directory} aria-label="章节目录"><div className="studio-directory-tools"><Pin label="章节目录" pinned={pins.directory} onChange={() => setPins(value => ({ ...value, directory: !value.directory }))} /></div><div className="studio-directory-title"><h2>{title}</h2><IconButton icon="add" label="新建卷" disabled={!preview} onClick={() => setVolumes(items => [...items, `第${items.length + 1}卷`])} /></div><div className="studio-directory-scroll">{volumes.slice().reverse().map(volume => <details key={volume} open><summary><span>{volume}</span><IconButton icon="add" label={`在${volume}新建章节`} onClick={event => { event.preventDefault(); addChapter(volume) }} /></summary>{chapters.filter(chapter => chapter.volume === volume).slice().sort((a, b) => b.number - a.number).map(chapter => <button data-glow key={chapter.id} aria-current={selectedId === chapter.id ? 'page' : undefined} onClick={() => selectChapter(chapter.id)}><i className={chapter.review === 'running' ? 'is-running' : ''} /><span>第{chapter.number}话 {chapter.title}</span><small>{chapter.review === 'running' ? '审阅中' : chapter.published ? '已发布' : ''}</small></button>)}</details>)}</div></section>
    </aside>
    <footer className={`studio-bottom studio-chrome${hidden ? ' is-hidden' : ''}`}><button onClick={() => void leave(() => navigate('/'))}>返回书架</button><span>{preview ? '交互预览 · Agent 流程为示例' : '真实章节 · 新流程待接入'}</span>{!preview && selected && <button onClick={() => void leave(() => navigate(`/projects/${project?.id}/chapters/${selected.id}`))}>现有工作台</button>}</footer>
    <IconButton icon="shark" label="Gura" className={`studio-gura studio-chrome${hidden ? ' is-hidden' : ''}`} onClick={() => setToast('Gura 助手交互尚未设计。')} />
    {(toast || storageError) && <div className="studio-toast" role={storageError ? 'alert' : 'status'}>{storageError || toast}<button aria-label="关闭提示" onClick={() => { setToast(''); setStorageError('') }}>×</button></div>}
    {publishing && <PublishDialog onClose={() => setPublishing(false)} onConfirm={publish} />}
  </div>
}
