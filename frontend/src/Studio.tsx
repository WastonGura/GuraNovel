import { useEffect, useLayoutEffect, useImperativeHandle, useRef, useState, type ButtonHTMLAttributes, type CSSProperties, type ReactNode, type Ref } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { flushSync } from 'react-dom'
import { ApiError, approveStudioOutline, createChapter, getProject, listChapters, readDocumentContent, readDocumentVersionContent, readRestorePointFeedback, createRestorePoint, restorePoint, type Project } from './api/client'
import { getChapterProductionRun, listChapterProductionRuns, resumeChapterProduction, startChapterProductionV2 } from './api/chapterProductionV2Client'
import { advanceStudioReview, finalizeStudioChapter, loadStudioReview, reviewStages, reviseStudioReview, studioRevisionSelection, revisionRecoveryKey, studioFinalIsComplete, prepareStudioFeedbackRevision, reviseStudioFeedback, feedbackRevisionKey } from './studioProduction'
import { initialPreview, newStudioChapter, outlineOptions, previewIssues, previewProse, readerPersonas, readerStageKey, reviewers, stages, type Stage, type StudioChapter } from './studioPreview'
import StudioReader from './StudioReader'
import { useDraftAutosave } from './useDraftAutosave'
import OutlineEditor from './OutlineEditor'
import StudioSetting from './StudioSetting'
import StudioLabels from './StudioLabels'
import { ChapterArchiveRow } from './StudioArchives'
import { loadDraftArchives, archiveStorageKey, archiveTime, type DraftArchive } from './studioPreview'
import { useCommentDrag } from './useCommentDrag'
import { restoreOutlineComments, reanchorComments, reanchorFeedbackComments, type OutlineComment } from './studioPreview'
import './studio.css'
import { MotionFrame, TextSweep, StreamText } from './StudioMotion'
import GlobalAssistant from './GlobalAssistant'

const asset = (name: string) => name === 'setting' ? '/ui/icons/setting.svg' : `/ui/studio/${name}.svg${name === 'notification' ? '?solid=1' : ''}`
const storageKey = 'guranovel:studio-preview:v1'
const pages = ['Detail', 'Create', 'Setting'] as const
type Page = typeof pages[number]
type Focus = 'off' | 'auto' | 'manual'
type Notice = { id: string; chapterId: string; message: string; read: boolean }

function IconButton({ icon, label, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { icon: string; label: string }) {
  const variants = ['focus', 'show'].includes(icon) ? ['show', 'focus'] : ['pin', 'unpin'].includes(icon) ? ['pin', 'unpin'] : [icon]
  return <button type="button" data-icon={icon} className={`studio-icon ${variants.length > 1 ? 'has-state' : ''} ${['send', 'review', 'restorepoint', 'publish', 'check'].includes(icon) ? 'is-round-asset' : ''} ${className}`} title={label} aria-label={label} {...props}>
    <span className={`studio-icon-glyph${variants.length > 1 && icon === variants[1] ? ' is-slashed' : ''}`} aria-hidden="true">{variants.map((name, index) => <img key={name} className={`${name === icon ? 'is-current' : 'is-outgoing'}${variants.length > 1 ? index === 0 ? ' icon-base' : ' icon-slashed' : ''}`} src={asset(name)} alt="" />)}</span>
  </button>
}

function PageName({ page, direction }: { page: Page; direction: number }) {
  const previous = useRef(page)
  const labels = useRef<HTMLSpanElement>(null)
  useLayoutEffect(() => {
    const before = previous.current
    previous.current = page
    if (before === page || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    labels.current?.querySelectorAll<HTMLElement>('[data-page]').forEach(label => {
      const interrupted = label.getAnimations?.().some(animation => animation.playState === 'running')
      const from = interrupted ? getComputedStyle(label).transform : undefined
      label.getAnimations?.().forEach(animation => animation.cancel())
      if (label.dataset.page === before) label.animate?.([{ opacity: 1, transform: from || 'translateY(0)' }, { opacity: 1, transform: `translateY(${direction * 100}%)` }], { duration: 440, easing: 'cubic-bezier(.22, 1, .36, 1)' })
      if (label.dataset.page === page) label.animate?.([{ opacity: 1, transform: from || `translateY(${-direction * 100}%)` }, { opacity: 1, transform: 'translateY(0)' }], { duration: 440, easing: 'cubic-bezier(.22, 1, .36, 1)' })
    })
  }, [page, direction])
  return <strong aria-label={page}><span className="studio-page-name" ref={labels} aria-hidden="true">{pages.map(name => <span key={name} data-page={name} className={name === page ? 'is-current' : ''}>{name}</span>)}</span></strong>
}

function Pin({ pinned, onChange, label }: { pinned: boolean; onChange: () => void; label: string }) {
  return <IconButton icon={pinned ? 'pin' : 'unpin'} label={`${pinned ? '取消固定' : '固定'}${label}`} aria-pressed={pinned} onClick={onChange} />
}

function OutlineGeneration({ chapter, preview, refresh, onIdea, onGenerate, onChoose, onRefresh }: {
  chapter: StudioChapter; preview: boolean; refresh: number; onIdea: (idea: string) => void; onGenerate: () => void; onChoose: (text: string) => void; onRefresh: () => void
}) {
  const split = chapter.outlineStep === 'choose'
  return <div className={`studio-outline-generation${split ? ' is-split' : ''}`}>
    <div className="studio-outline-prompt"><h1><TextSweep text={split ? '选择大纲' : '想怎么写？'} /></h1></div>
    <div className="studio-outline-frame">
      <div className="studio-split-panels" aria-hidden="true"><i /><i /><i /></div>
      <div className="studio-outline-generator-content">
        <div className="studio-idea-card" data-text-inactive={split} aria-hidden={split} inert={split}><div className="studio-idea-copy" aria-hidden="true"><TextSweep text={chapter.idea || '你的大纲思路…'} changeKey="idea" /></div><textarea aria-label="大纲思路" placeholder="你的大纲思路…" value={chapter.idea} onChange={event => onIdea(event.target.value)} /><IconButton icon="send" label="生成大纲方案" disabled={!chapter.idea.trim() || !preview} onClick={onGenerate} /></div>
          {split && <div className="studio-outline-options">{outlineOptions.map((_, index) => {
            const option = outlineOptions[(index + refresh) % 3]
            return <button key={index} aria-label={`方案 0${index + 1}：${option.title}`} onClick={() => onChoose(option.text)}><small data-sweep-text>方案 0{index + 1}</small><h2><TextSweep text={option.title} /></h2><p><StreamText text={option.text} /></p><span data-sweep-text className="studio-option-action">选择这个方向</span></button>
          })}</div>}
      </div>
    </div>
    <div className="studio-outline-refresh-slot">{split && <button className="studio-refresh" onClick={onRefresh} title="切换预览方案排列"><img src={asset('refresh')} alt="" />换一组</button>}</div>
  </div>
}

function ChapterScroll({ children }: { children: ReactNode }) {
  const viewport = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const element = viewport.current
    if (!element) return
    const body = element.closest('.studio-body')
    let frame = 0
    const measure = () => element.style.setProperty('--chapter-gutter', `${Math.max(0, element.getBoundingClientRect().left - 8)}px`)
    // ResizeObserver misses a pure translation once the manuscript reaches max-width.
    const follow = () => {
      measure()
      frame = body?.getAnimations?.().some(animation => animation.playState === 'running' && 'transitionProperty' in animation && animation.transitionProperty === 'left') ? requestAnimationFrame(follow) : 0
    }
    const move = (event: Event) => {
      if (event.target !== body || (event as TransitionEvent).propertyName !== 'left') return
      cancelAnimationFrame(frame); follow()
    }
    follow()
    body?.addEventListener('transitionrun', move)
    body?.addEventListener('transitionend', move)
    body?.addEventListener('transitioncancel', move)
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(element)
    window.addEventListener('resize', measure)
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure); body?.removeEventListener('transitionrun', move); body?.removeEventListener('transitionend', move); body?.removeEventListener('transitioncancel', move); cancelAnimationFrame(frame) }
  }, [])
  return <div className="studio-manuscript-viewport" ref={viewport}>
    <div className="studio-chapter-scroll" tabIndex={0} role="region" aria-label="章节滚动区域"><div className="studio-chapter-content">{children}</div></div>
  </div>
}

function PublishDialog({ onClose, onConfirm, preview, busy, error }: { onClose: () => void; onConfirm: () => void; preview: boolean; busy: boolean; error?: string }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  return <dialog ref={dialog} className="studio-confirm" onCancel={event => { if (busy) event.preventDefault(); else onClose() }} onClick={(event) => { if (!busy && event.target === event.currentTarget) onClose() }}>
    <h2>{preview ? '确认发布这一章？' : '确认本地定稿？'}</h2><p>{preview ? '发布后正文将变为只读。' : '将当前审阅版本保存为只读终稿。'}</p>
    <p className="studio-muted">{preview ? '当前为交互预览，不会发布真实章节。' : '此操作仅在当前作品中定稿，不会发布到外部平台。'}</p>
    {error && <p role="alert">{error}</p>}
    <div className="studio-actions"><button disabled={busy} onClick={onClose}>再看一遍</button><button disabled={busy} aria-busy={busy} className="studio-primary" onClick={onConfirm}>{busy ? '正在定稿…' : preview ? '确认发布' : error ? '重试本地定稿' : '确认本地定稿'}</button></div>
  </dialog>
}

type EditorHandle = { flush: () => Promise<boolean> }
type StudioEditorHandle = EditorHandle & {
  changeFeedback: (patch: { comments?: OutlineComment[]; requirements?: string }) => void
  submitFeedback: (ids: string[]) => Promise<boolean>
}
function SubmittedComments({ comments, readOnly, onOrder, activeId, onSelect, archive = false }: { comments: OutlineComment[]; readOnly: boolean; onOrder: (ids: string[]) => void; activeId?: string; onSelect: (id: string) => void; archive?: boolean }) {
  const dots = useCommentDrag(!readOnly, onOrder)
  return <div className="studio-submitted-comments">
    <div ref={dots} className="studio-comment-dots studio-submitted-dots" role="group" aria-label={archive ? '存档评论圆点' : '已提交评论圆点'}>
      {comments.map(comment => <button key={comment.id} type="button" data-comment-id={comment.id} aria-pressed={comment.id === activeId} aria-label={`查看${archive ? '存档' : '已提交'}评论：${comment.quote}`} title={`${comment.quote} · 再次点击返回写作要求`}
        aria-description={readOnly ? '只读评论' : '拖动排序；拖出修改框松手移除；Alt 加左右方向键排序，Delete 移除'}
        style={{ '--comment-color': comment.color } as CSSProperties} onClick={() => onSelect(comment.id)} />)}
    </div>
  </div>
}
function ProseEditor({ chapter, projectId, preview, archive, busy, onChange, onCommentsChange, onFeedbackChange, onSend, onSaved, onTyping, ref, locate, ready, visible, onSubmittedSelect }: {
  chapter: StudioChapter; projectId?: string; preview: boolean; onChange: (draft: string, comments: OutlineComment[]) => void; onCommentsChange: (comments: OutlineComment[]) => void; onSend: () => void; onSaved: (versionId: string, text: string) => void; onTyping: () => void; ref: Ref<StudioEditorHandle>; locate: string
  onFeedbackChange: (comments: OutlineComment[], requirements: string, readOnly: boolean) => void
  ready: boolean; visible: boolean; onSubmittedSelect: (id: string) => void
  archive?: DraftArchive
  busy: boolean
}) {
  const workflowReadonly = !preview && (!!chapter.productionError || !!chapter.productionStatus && !['AUTHOR_REVISION', 'CANCELLED'].includes(chapter.productionStatus))
  const save = useDraftAutosave(chapter.documentId, chapter.versionId, chapter.draft, onSaved, {
    readOnly: workflowReadonly || chapter.published || chapter.review === 'running' || chapter.stage !== 'Draft',
    feedback: projectId && chapter.documentId && !chapter.published ? { projectId, chapterId: chapter.id, region: 'draft',
      onChange: value => onFeedbackChange(value.comments, value.requirements, value.read_only) } : undefined,
  })
  useLayoutEffect(() => {
    if (!preview) onFeedbackChange(chapter.draftComments || [], chapter.requirements, true)
    // Reset the parent controls while this keyed editor loads server feedback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const input = useRef<HTMLTextAreaElement>(null)
  const readonly = workflowReadonly || Boolean(archive) || busy || chapter.published || chapter.review === 'running' || chapter.stage !== 'Draft' || (!preview && !chapter.documentId)
  const text = archive?.draft ?? (preview ? chapter.draft : save.text)
  useLayoutEffect(() => {
    const textarea = input.current
    if (!textarea) return
    textarea.style.height = '0px'
    textarea.style.height = `${textarea.scrollHeight}px`
  }, [text])
  useEffect(() => {
    const textarea = input.current
    if (!textarea || typeof ResizeObserver === 'undefined') return
    let width = textarea.clientWidth
    const observer = new ResizeObserver(() => {
      if (textarea.clientWidth === width) return
      width = textarea.clientWidth
      textarea.style.height = '0px'; textarea.style.height = `${textarea.scrollHeight}px`
    })
    observer.observe(textarea)
    return () => observer.disconnect()
  }, [])
  useImperativeHandle(ref, () => ({ flush: preview ? async () => true : save.flush, changeFeedback: save.changeFeedback, submitFeedback: ids => save.submit(ids) }))
  useEffect(() => {
    if (!locate || !input.current) return
    const start = input.current.value.indexOf(locate)
    if (start >= 0) { input.current.focus(); input.current.setSelectionRange(start, start + locate.length) }
  }, [locate])
  return <div className="studio-prose">
    <OutlineEditor outline={text} comments={archive ? archive.comments || [] : preview ? chapter.draftComments || [] : save.feedback?.comments || chapter.draftComments || []} readOnly={readonly || (!preview && (!save.feedback || save.feedback.read_only))} draftInput={{ ref: input, onTyping, ready, visible, onSubmittedSelect, animateText: true }}
      onChange={(value, comments) => { if (preview) onChange(value, comments); else { save.change(value); onChange(value, reanchorFeedbackComments(save.feedback?.comments || [], text, value)) } }} onCommentsChange={preview ? onCommentsChange : comments => save.changeFeedback({ comments })} onSend={onSend}
      prepareSend={preview ? undefined : animate => save.submit((save.feedback?.comments || []).filter(comment => !comment.submitted).map(comment => comment.id), animate)} />
    {!archive && <div className="studio-save-status" role="status">{preview ? '自动保存到本机 · 交互预览' : save.status}
      {!preview && save.status.includes('失败') && <button onClick={() => void save.flush()}>重试自动保存</button>}
      {!preview && save.status.includes('冲突') && <button onClick={save.reloadServer}>使用服务器版本</button>}
    </div>}
    {readonly && <p className="studio-save-status">{archive ? '存档只读查看 · 当前正文已保留' : chapter.published ? preview ? '已发布 · 只读' : '已在本地定稿 · 只读' : preview ? '当前阶段为只读预览' : '当前正文为只读'}</p>}
  </div>
}

function PersistedOutline({ chapter, projectId, onChange, onSend, ref }: {
  chapter: StudioChapter; projectId: string; onChange: (patch: Partial<StudioChapter>) => void; onSend: () => void; ref: Ref<StudioEditorHandle>
}) {
  const [starting, setStarting] = useState(false)
  const [startMessage, setStartMessage] = useState('')
  const startPending = useRef(false)
  const mounted = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const save = useDraftAutosave(chapter.outlineDocumentId, chapter.outlineVersionId, chapter.outline, undefined, {
    readOnly: chapter.published,
    feedback: { projectId, chapterId: chapter.id, region: 'outline', onChange: value => onChange({ outlineComments: value.comments }) },
  })
  useImperativeHandle(ref, () => ({ flush: save.flush, changeFeedback: save.changeFeedback, submitFeedback: ids => save.submit(ids) }))
  async function confirmAndDraft() {
    if (startPending.current || !chapter.outlineDocumentId || !chapter.outlineVersionId) return
    startPending.current = true; setStarting(true); setStartMessage('正在保存并确认大纲…')
    try {
      if (!await save.flush()) { setStartMessage('大纲反馈尚未保存，请先处理保存失败或冲突。'); return }
      // A failed discovery request must never be mistaken for an empty run list.
      const runs = await listChapterProductionRuns(projectId, chapter.id, { limit: 1 })
      if (!mounted.current) return
      let runId: string
      if (runs.length) {
        runId = runs[0].workflow_run_id
        const state = await getChapterProductionRun(projectId, chapter.id, runId)
        if (!mounted.current) return
        if (state.status === 'DRAFTING' || (state.status === 'FAILED' && state.failed_from_status === 'DRAFTING')) {
          setStartMessage('正在恢复正文生成…')
          await resumeChapterProduction(projectId, chapter.id, runId)
        }
      } else {
        await approveStudioOutline(projectId, chapter.id, chapter.outlineDocumentId, chapter.outlineVersionId)
        if (!mounted.current) return
        setStartMessage('大纲已确认，正在生成正文…')
        runId = (await startChapterProductionV2(projectId, chapter.id)).workflow_run_id
      }
      const state = await getChapterProductionRun(projectId, chapter.id, runId)
      if (state.chapter_id !== chapter.id || state.status !== 'AUTHOR_REVISION' || !state.awaiting_user || !state.document_id || !state.document_version_id) {
        throw new Error('The existing workflow is not at the author gate.')
      }
      const draft = await readDocumentContent(state.document_id)
      if (draft.document_id !== state.document_id || draft.version_id !== state.document_version_id) throw new Error('The draft changed during loading.')
      if (mounted.current) onChange({ stage: 'Draft', productionStatus: state.status, draft: draft.content, documentId: draft.document_id, versionId: draft.version_id })
    } catch (error) {
      if (mounted.current) setStartMessage(error instanceof ApiError && error.status === 409
        ? '大纲或流程状态已变化，请重新加载后确认。'
        : '正文生成未完成。可再次点击确认恢复原任务，或在现有工作台查看状态。')
    } finally {
      startPending.current = false
      if (mounted.current) setStarting(false)
    }
  }
  return <>
    <OutlineEditor outline={save.text} comments={save.feedback?.comments || []} readOnly={starting || !save.feedback || save.feedback.read_only || chapter.published} textReadOnly
      onChange={() => {}} onCommentsChange={comments => save.changeFeedback({ comments })} onSend={onSend}
      prepareSend={() => save.submit((save.feedback?.comments || []).map(comment => comment.id))}
      confirm={<IconButton icon="check" label="确认大纲并生成正文" disabled={starting || !save.feedback || chapter.published || !save.text.trim()} onClick={() => void confirmAndDraft()} />} />
    {startMessage && <p className="studio-integration-note" role="status">{startMessage}</p>}
    <div className="studio-save-status" role="status">{save.status}
      {save.status.includes('失败') && <button onClick={() => void save.flush()}>重试自动保存</button>}
      {save.status.includes('冲突') && <button onClick={save.reloadServer}>使用服务器版本</button>}
    </div>
  </>
}

export default function Studio() {
  const { projectId } = useParams()
  return <StudioLoader key={projectId || 'preview'} />
}

function StudioLoader() {
  const { projectId, chapterId } = useParams()
  const preview = !projectId
  const [loaded, setLoaded] = useState<{ title: string; chapters: StudioChapter[]; project?: Project } | null>(() => preview ? { title: '动量干涉：Momentum Zero', chapters: initialPreview() } : null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!projectId) return
    let active = true
    Promise.all([getProject(projectId), listChapters(projectId)]).then(async ([project, chapters]) => {
      const items = await Promise.all(chapters.map(async chapter => {
        const finalReady = !!chapter.final_document_id && await studioFinalIsComplete(projectId, chapter.id)
        const documentId = finalReady ? chapter.final_document_id : chapter.current_draft_document_id
        const [draft, outline] = await Promise.all([
          documentId ? readDocumentContent(documentId) : null,
          chapter.current_outline_document_id ? readDocumentContent(chapter.current_outline_document_id) : null,
        ])
        return { ...newStudioChapter(chapter.id, chapter.chapter_number, chapter.title || '未命名章节', typeof chapter.metadata.volume === 'string' ? chapter.metadata.volume : '第一卷'),
          draft: draft?.content || '', outline: outline?.content || '', documentId: draft?.document_id, versionId: draft?.version_id,
          outlineDocumentId: outline?.document_id, outlineVersionId: outline?.version_id,
          stage: finalReady ? 'Final' as const : draft ? 'Draft' as const : 'Outline' as const,
          published: finalReady, outlineStep: outline ? 'edit' as const : 'new' as const,
          ...(!finalReady && draft ? await loadStudioReview(projectId, chapter.id, draft.document_id, draft.version_id) : {}),
        }
      }))
      if (active) setLoaded({ title: project.title, chapters: items, project })
    }).catch((caught: unknown) => {
      if (active) {
        if (caught instanceof ApiError && caught.status === 404) {
          setError('未找到此作品，可能已被删除或无权访问。')
        } else {
          setError('创作区加载失败，请重试。没有使用示例数据替代真实章节。')
        }
      }
    })
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
  const location = useLocation()
  const requestedView = new URLSearchParams(location.search).get('view')
  const routeView: Page = pages.includes(requestedView as Page) ? requestedView as Page : 'Create'
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
            if (index >= 0) {
              const outline = typeof value.outline === 'string' ? value.outline : restored[index].outline
              restored[index] = { ...restored[index], draft: value.draft, outline, draftComments: restoreOutlineComments('draftComments' in value ? value.draftComments : undefined, value.draft), outlineComments: restoreOutlineComments('outlineComments' in value ? value.outlineComments : undefined, outline), ...(typeof value.outline === 'string' ? { outlineStep: 'edit' as const } : {}) }
            }
            else if (Number.isSafeInteger(value.number) && value.number > 0 && typeof value.title === 'string' && typeof value.volume === 'string' && typeof value.outline === 'string') {
              restored.push({ ...newStudioChapter(id, value.number, value.title, value.volume), draft: value.draft, outline: value.outline, draftComments: restoreOutlineComments('draftComments' in value ? value.draftComments : undefined, value.draft), outlineComments: restoreOutlineComments('outlineComments' in value ? value.outlineComments : undefined, value.outline), outlineStep: 'edit', stage: 'Draft' })
            }
          }
        }
        return restored
      }
    } catch { /* An invalid preview cache must not prevent opening the editor. */ }
    return initial
  })
  const [localSelectedId, setSelectedId] = useState(initialId || chapters.at(-1)?.id || '')
  const selectedId = preview ? localSelectedId : initialId || chapters.at(-1)?.id || ''
  const [archives, setArchives] = useState(loadDraftArchives)
  const [archive, setArchive] = useState<(DraftArchive & { chapterId: string }) | null>(null)
  const [archiveRevision, setArchiveRevision] = useState(0)
  const [archiveBusy, setArchiveBusy] = useState(false)
  const [productionBusyId, setProductionBusyId] = useState('')
  const productionPending = useRef<AbortController | null>(null)
  useEffect(() => () => { productionPending.current?.abort(); productionPending.current = null }, [])
  const archiveSaving = useRef(false)
  const archiveRequest = useRef<{ key: string; id: string } | null>(null)
  const [page, setPage] = useState<Page>(routeView)
  const [routeSelection, setRouteSelection] = useState({ key: location.key, chapter: initialId })
  const [pageDirection, setPageDirection] = useState(1)
  if (routeSelection.key !== location.key) {
    setRouteSelection({ key: location.key, chapter: initialId })
    if (page !== routeView) { setPageDirection(pages.indexOf(routeView) > pages.indexOf(page) ? 1 : -1); setPage(routeView) }
    if (routeSelection.chapter !== initialId) { setSelectedId(initialId || chapters.at(-1)?.id || ''); if (archive?.chapterId !== initialId) setArchive(null) }
  }
  const [pageReady, setPageReady] = useState(true)
  const pageSurfaces = useRef<HTMLDivElement>(null)
  const previousPage = useRef<Page>(routeView)
  const requestedPage = useRef<Page>(routeView)
  const transition = useRef<ViewTransition | null>(null)
  const navigation = useRef(0)
  const [commentRequest, setCommentRequest] = useState<{ id: string } | null>(null)
  const [focus, setFocus] = useState<Focus>('off')
  const [pins, setPins] = useState({ directory: false, stats: false, context: false })
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [contextOutline, setContextOutline] = useState(false)
  const [notifications, setNotifications] = useState<Notice[]>([])
  const [noticesOpen, setNoticesOpen] = useState(false)
  const [toast, setToast] = useState('')
  const [blockedAttempt, setBlockedAttempt] = useState(0)
  const workflow = useRef<HTMLElement>(null)
  const blockedMotion = useRef<Animation[]>([])
  useEffect(() => {
    if (!/^(已加入待修改列表|已移出待修改列表|对应原文：|审阅完成并处理 Block)/.test(toast)) return
    const timer = window.setTimeout(() => setToast(''), 2400)
    return () => window.clearTimeout(timer)
  }, [toast, blockedAttempt])
  const [storageError, setStorageError] = useState('')
  const [publishing, setPublishing] = useState(false)
  const [locate, setLocate] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [volumes, setVolumes] = useState([...new Set(chapters.map(chapter => chapter.volume))])
  const [introduction, setIntroduction] = useState(typeof project?.metadata?.introduction === 'string' ? project.metadata.introduction : '')
  const [started] = useState(Date.now)
  const [seconds, setSeconds] = useState(0)
  const editor = useRef<StudioEditorHandle>(null)
  const settingEditor = useRef<EditorHandle>(null)
  const modificationPanel = useRef<HTMLDivElement>(null)
  const timers = useRef<number[]>([])
  const currentChapters = useRef(chapters)
  const creatingChapter = useRef(false)
  const [chapterPending, setChapterPending] = useState(false)
  const selected = chapters.find(chapter => chapter.id === selectedId)
  useLayoutEffect(() => () => { blockedMotion.current.forEach(animation => animation.cancel()); blockedMotion.current = [] }, [selected?.stage, page])
  const unread = notifications.filter(notice => !notice.read).length
  const hidden = focus !== 'off'
  const writing = selected && (archive || ['Draft', 'Review', 'Final'].includes(selected.stage))
  const showingOutline = !archive && (contextOutline || selected?.stage === 'Final')
  const activeComment = archive ? archive.comments?.find(comment => comment.id === commentRequest?.id) : showingOutline || selected?.stage !== 'Draft' ? undefined : selected.draftComments?.find(comment => comment.submitted && comment.id === commentRequest?.id)
  const reportVisible = !archive && !showingOutline && selected?.stage === 'Review' && selected.review === 'done'
  const sendVisible = !archive && !showingOutline && (selected?.stage === 'Draft' || selected?.stage === 'Review')
  const contextMode = activeComment ? `comment-${archive?.id || 'current'}-${activeComment.id}` : archive ? `archive-${archive.id}` : showingOutline ? 'outline' : selected?.stage || ''
  const contextInput = Boolean(activeComment || contextMode === 'Draft')
  const currentReviewer = ({ EDITOR_REVIEW: reviewers[0], CHIEF_FINAL_REVIEW: reviewers[1], LORE_FINAL_REVIEW: reviewers[2] } as Record<string, string>)[selected?.productionStatus || '']
  const contextText = activeComment ? activeComment.text : archive ? archive.requirements || (archive.feedbackAvailable ? '此存档没有通用写作要求。' : '此历史存档未记录写作要求和评论。') : showingOutline ? selected?.outline || '暂无大纲' : selected?.stage === 'Review'
    ? !preview ? selected.productionStatus === 'REVISION_READY' ? '本轮审阅已完成。' : selected.productionState?.awaiting_user ? '审阅暂停，等待你处理本轮问题。'
      : selected.productionStatus === 'FAILED' ? '审阅中断，可重试恢复当前流程。' : currentReviewer ? `${currentReviewer} ${productionBusyId === selected.id ? '审阅中…' : '等待继续审阅。'}` : '正在读取审阅状态…'
      : selected.review === 'idle' ? '' : [...['Editor Reviewer', 'Chief Reviewer', 'Lore Reviewer'].map(name => `${name} 开始审阅`), ...['Editor Reviewer', 'Chief Reviewer', 'Lore Reviewer'].slice(0, selected.completed).map(name => `${name} 完成审阅`)].join('\n')
    : selected?.requirements || '希望这一段怎样展开？'

  useEffect(() => {
    const interval = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000)
    const pending = timers.current
    return () => { window.clearInterval(interval); pending.forEach(window.clearTimeout) }
  }, [started])

  useEffect(() => {
    const move = () => setFocus(value => value === 'auto' ? 'off' : value)
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setFocus('off'); setNoticesOpen(false) }
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('keydown', escape)
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('keydown', escape) }
  }, [])

  function update(id: string, patch: Partial<StudioChapter>) {
    const next = currentChapters.current.map(chapter => chapter.id === id ? { ...chapter, ...(patch.draft !== undefined ? { draftComments: reanchorComments(chapter.draftComments || [], chapter.draft, patch.draft) } : {}), ...patch } : chapter)
    currentChapters.current = next
    setChapters(next)
    if (preview && (patch.draft !== undefined || patch.outline !== undefined || patch.outlineComments !== undefined || patch.draftComments !== undefined)) {
      try { localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(next.map(({ id, number, title, volume, outline, draft, outlineComments, draftComments }) => [id, { number, title, volume, outline, draft, outlineComments, draftComments }])))); setStorageError('') }
      catch { setStorageError('本机自动保存失败，请先复制大纲、评论和正文备份。') }
    }
  }
  function updateDraftFeedback(patch: { comments?: OutlineComment[]; requirements?: string }) {
    if (!selected) return
    if (preview) update(selected.id, { ...(patch.comments ? { draftComments: patch.comments } : {}), ...(patch.requirements !== undefined ? { requirements: patch.requirements } : {}) })
    else editor.current?.changeFeedback(patch)
  }

  async function leave(action: () => void, direction?: number) {
    if (archiveSaving.current) { setToast('正在保存存档，请稍候。'); return }
    const request = ++navigation.current
    const currentEditor = page === 'Setting' ? settingEditor.current : page === 'Create' ? editor.current : null
    if (currentEditor && !await currentEditor.flush()) { requestedPage.current = page; setToast(`${page === 'Setting' ? '设定' : '正文'}尚未保存，已暂停切换。请先重试自动保存。`); return }
    if (request !== navigation.current) return
    const commit = () => { if (request !== navigation.current) return; setFocus(value => value === 'auto' ? 'off' : value); setLocate(''); setCommentRequest(null); if (direction) setPageDirection(direction); action() }
    if (direction) { transition.current?.skipTransition(); transition.current = null; flushSync(() => { setPageReady(false); commit() }); return }
    if (!writing && document.startViewTransition && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      transition.current?.skipTransition()
      flushSync(() => setPageReady(false))
      const current = document.startViewTransition(() => flushSync(commit))
      transition.current = current
      void current.finished.catch(() => {}).then(() => { if (transition.current === current) { transition.current = null; setPageReady(true) } })
    } else { transition.current?.skipTransition(); transition.current = null; setPageReady(true); commit() }
  }
  function navigateStudio(nextPage: Page, chapterId = selectedId) {
    const search = new URLSearchParams(location.search)
    search.set('view', nextPage)
    const pathname = preview ? '/preview/studio' : `/projects/${encodeURIComponent(project!.id)}/studio${chapterId ? `/${encodeURIComponent(chapterId)}` : ''}`
    if (pathname !== location.pathname || search.toString() !== location.search.slice(1)) navigate({ pathname, search: search.toString() }, { state: location.state })
  }
  function keepLocalArchive(chapter: StudioChapter, summary: string) {
    const entry: DraftArchive = { id: crypto.randomUUID(), createdAt: new Date().toISOString(), summary, draft: chapter.draft, comments: chapter.draftComments || [], requirements: chapter.requirements, feedbackAvailable: true }
    const next = { ...archives, [chapter.id]: [entry, ...(archives[chapter.id] || [])] }
    localStorage.setItem(archiveStorageKey, JSON.stringify(next))
    setArchives(next)
  }
  async function saveArchive(restore = false) {
    if (productionPending.current) { setToast('正在提交或审阅，暂时不能更改存档。'); return }
    if (!selected || selected.published || selected.review === 'running' || archiveSaving.current || (restore && !archive)) return
    archiveSaving.current = true; setArchiveBusy(true)
    ++navigation.current
    try {
      if (editor.current && !await editor.current.flush()) { setToast('正文尚未保存，请先重试自动保存。'); return }
      const chapter = currentChapters.current.find(item => item.id === selected.id)!
      if (preview) {
        keepLocalArchive(chapter, restore ? '恢复前自动备份' : '手动存档')
        if (restore) update(chapter.id, { draft: archive!.draft!, draftComments: archive!.comments || [], requirements: archive!.requirements ?? chapter.requirements, stage: 'Draft', review: 'idle', issues: [], selected: [] })
      } else {
        if (!chapter.documentId || !chapter.versionId) throw new Error('missing document')
        const key = `${chapter.id}:${chapter.versionId}:${restore ? archive!.id : 'save'}`
        if (archiveRequest.current?.key !== key) archiveRequest.current = { key, id: crypto.randomUUID() }
        const payload = { request_id: archiveRequest.current.id, expected_current_version_id: chapter.versionId }
        if (restore) {
          const version = await restorePoint(project!.id, chapter.id, archive!.id, payload)
          update(chapter.id, { versionId: version.id, draft: archive!.draft!, ...(archive!.feedbackAvailable ? { draftComments: archive!.comments || [], requirements: archive!.requirements || '' } : {}), stage: 'Draft', review: 'idle', issues: [], selected: [] })
        } else await createRestorePoint(project!.id, chapter.id, payload)
        archiveRequest.current = null
      }
      setArchiveRevision(value => value + 1)
      if (restore) setArchive(null)
      setToast(restore ? '已从存档继续写作，原正文仍保留在历史版本中。' : preview ? '已创建本机存档。' : '已创建存档。')
    } catch { setToast('存档操作失败，当前正文仍保留，请重试。') }
    finally { archiveSaving.current = false; setArchiveBusy(false) }
  }
  function viewArchive(chapter: StudioChapter, entry: DraftArchive) {
    void leave(() => {
      const request = navigation.current
      setToast('正在读取存档…')
      const content = preview ? Promise.resolve(entry) : entry.documentId && entry.versionId ? Promise.all([
        readDocumentVersionContent(entry.documentId, entry.versionId), readRestorePointFeedback(project!.id, chapter.id, entry.id),
      ]).then(([body, feedback]) => {
        if (feedback.document_id !== body.document_id || feedback.source_version_id !== body.version_id) throw new Error('archive source mismatch')
        return { ...entry, draft: body.content, comments: feedback.comments, requirements: feedback.requirements, feedbackAvailable: feedback.available }
      }) : Promise.reject(new Error('missing document'))
      void content.then(loaded => {
        if (request !== navigation.current) return
        setSelectedId(chapter.id); setArchive({ ...loaded, chapterId: chapter.id }); setPage('Create'); navigateStudio('Create', chapter.id); setPageReady(true); setToast('')
      }).catch(() => { if (request === navigation.current) setToast('无法读取存档，当前正文未改变，请重试。') })
    })
  }
  function goPage(next: Page, direction = pages.indexOf(next) > pages.indexOf(page) ? 1 : -1) {
    requestedPage.current = next
    void leave(() => { setPage(next); navigateStudio(next); if (next === page) setPageReady(true) }, direction)
  }
  useLayoutEffect(() => {
    const before = previousPage.current
    previousPage.current = page
    requestedPage.current = page
    let incoming: Animation | undefined
    pageSurfaces.current?.querySelectorAll<HTMLElement>('[data-studio-page]').forEach(surface => {
      const running = surface.getAnimations?.().some(animation => animation.playState === 'running')
      const from = running ? getComputedStyle(surface).transform : surface.dataset.studioPage === before ? 'translateX(0)' : `translateX(${pageDirection * 100}vw)`
      surface.getAnimations?.().forEach(animation => animation.cancel())
      if (before === page || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || !surface.animate) return
      if (surface.dataset.studioPage !== page && surface.dataset.studioPage !== before && !running) return
      const animation = surface.animate([{ opacity: 1, visibility: 'visible', transform: from }, { opacity: 1, visibility: 'visible', transform: surface.dataset.studioPage === page ? 'translateX(0)' : `translateX(${-pageDirection * 100}vw)` }], { duration: 540, easing: 'cubic-bezier(.22, 1, .36, 1)' })
      void animation.finished.catch(() => {})
      if (surface.dataset.studioPage === page) incoming = animation
    })
    if (incoming) void incoming.finished.then(() => setPageReady(true)).catch(() => {})
    else setPageReady(true)
  }, [page, pageDirection])
  function selectChapter(id: string) { void leave(() => { setArchive(null); setSelectedId(id); setPage('Create'); navigateStudio('Create', id) }) }
  function rejectStage(stage: Stage) {
    setToast('审阅完成并处理 Block 问题后，才能进入下一阶段。')
    setBlockedAttempt(value => value + 1)
    const line = workflow.current?.querySelector<HTMLElement>('.studio-workflow-progress')
    const dot = workflow.current?.querySelector<HTMLElement>('[aria-current="step"] i')
    if (!selected || !line || !dot || !line.animate || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const start = stages.indexOf(selected.stage) / (stages.length - 1)
    const attempt = start + Math.min(1 / (stages.length - 1), stages.indexOf(stage) / (stages.length - 1) - start) * .86
    const from = [getComputedStyle(line).transform, getComputedStyle(dot).transform]
    blockedMotion.current.forEach(animation => animation.cancel())
    const stops = [`scaleX(${attempt})`, `translateX(${(attempt - start) * line.offsetWidth}px)`]
    const ends = [`scaleX(${start})`, 'translateX(0)']
    blockedMotion.current = [line, dot].map((node, index) => node.animate([
      { transform: from[index], easing: 'cubic-bezier(.2, 0, .2, 1)' },
      { transform: stops[index], offset: .45, easing: 'cubic-bezier(.22, 1, .36, 1)' },
      { transform: ends[index] },
    ], { duration: 680 }))
    blockedMotion.current.forEach(animation => { void animation.finished.catch(() => {}) })
  }
  async function runFormalReview(id: string, warning?: StudioChapter['productionState'], revisedIds?: string[]) {
    if (productionPending.current || archiveSaving.current || !project || id !== selectedId) return
    const controller = new AbortController()
    productionPending.current = controller; setProductionBusyId(id)
    const origin = navigation.current
    try {
      if (!editor.current || !await editor.current.flush()) { setToast('正文或反馈尚未保存，请先重试保存。'); return }
      if (origin !== navigation.current || controller.signal.aborted) return
      const chapter = currentChapters.current.find(item => item.id === id)
      if (!chapter) return
      const progress = (patch: Partial<StudioChapter>) => {
        if (!controller.signal.aborted) update(id, patch)
      }
      const revision = chapter.revisionRequest || (revisedIds ? studioRevisionSelection(chapter, revisedIds) : undefined)
      if (revision) {
        sessionStorage.setItem(revisionRecoveryKey(project.id, id), JSON.stringify({ runId: chapter.productionState?.chapter_workflow_run_id, selection: revision }))
        update(id, { revisionRequest: revision })
        await reviseStudioReview(project.id, chapter, revision, progress, controller.signal)
        sessionStorage.removeItem(revisionRecoveryKey(project.id, id))
        progress({ revisionRequest: undefined })
      } else await advanceStudioReview(project.id, chapter, progress, controller.signal, warning)
    } catch (error) {
      if (!controller.signal.aborted) update(id, { productionError: error instanceof ApiError && error.status === 409
        ? '章节版本或流程已变化，请重试读取服务器状态；如仍冲突，请重新加载核对。'
        : '提交或审阅未完成。重试会先核对服务器进度，不会重新创建流程。' })
    } finally {
      if (productionPending.current === controller) { productionPending.current = null; setProductionBusyId('') }
    }
  }
  async function runFormalFeedback(id: string) {
    if (productionPending.current || archiveSaving.current || !project || id !== selectedId) return
    const controller = new AbortController()
    productionPending.current = controller; setProductionBusyId(id)
    const origin = navigation.current
    try {
      if (!editor.current || !await editor.current.flush()) { setToast('正文或反馈尚未保存，请先重试保存。'); return }
      if (origin !== navigation.current || controller.signal.aborted) return
      const chapter = currentChapters.current.find(item => item.id === id)
      if (!chapter) return
      let request = chapter.feedbackRequest
      if (!request) {
        try {
          request = await prepareStudioFeedbackRevision(project.id, chapter, controller.signal)
        } catch (valError: unknown) {
          if (!controller.signal.aborted) {
            setToast(valError instanceof Error && valError.message ? valError.message : '反馈前置校验未通过，请检查评论位置。')
          }
          return
        }
      }
      sessionStorage.setItem(feedbackRevisionKey(project.id, id), JSON.stringify(request))
      update(id, { feedbackRequest: request, productionError: undefined })
      const patch = await reviseStudioFeedback(project.id, chapter, request, controller.signal)
      sessionStorage.removeItem(feedbackRevisionKey(project.id, id))
      update(id, patch)
      setToast('已根据反馈生成新稿，请核对正文。')
    } catch (error) {
      if (!controller.signal.aborted) update(id, { productionError: error instanceof ApiError && error.status === 409
        ? '章节版本或流程已变化，请重试读取服务器状态；如仍冲突，请重新加载核对。'
        : error instanceof ApiError && error.message
          ? error.message
          : '反馈修改未完成。重试会先核对服务器进度，不会重复创建流程。' })
    } finally {
      if (productionPending.current === controller) { productionPending.current = null; setProductionBusyId('') }
    }
  }
  function startReview(id: string, revisedIds: string[] = []) {
    const chapter = currentChapters.current.find(item => item.id === id)
    if (!preview) {
      void runFormalReview(id, undefined, revisedIds.length ? revisedIds : undefined); return
    }
    if (!chapter?.draft.trim() || chapter.review === 'running' || chapter.published) return
    void leave(() => {
      const issues = revisedIds.length ? chapter.issues.filter(issue => !revisedIds.includes(issue.id)) : previewIssues
      let draft = chapter.draft
      if (revisedIds.includes('timeline')) draft = draft.replace('雨是在黄昏停下来的。', '又过了几个小时，雨终于在黄昏停了下来。')
      if (revisedIds.includes('motivation')) draft = draft.replace('却让窗边的女孩停住了筷子。', '窗边的女孩认出了碎片上的刻痕，停住了筷子。')
      if (revisedIds.includes('ending')) draft = draft.replace('林远突然觉得，这个再平常不过的傍晚，也许并不只是一个傍晚。', '林远把金属碎片握回掌心，没有再看窗边。')
      update(id, { stage: 'Review', review: 'running', completed: 0, issues: [], selected: [], draft })
      ;[1400, 2700, 4000].forEach((delay, index) => timers.current.push(window.setTimeout(() => {
        update(id, { completed: index + 1, ...(index === 2 ? { review: 'done' as const, reviewFinishedAt: Date.now(), issues, selected: issues.filter(issue => issue.level === 'Block').map(issue => issue.id) } : {}) })
        if (index === 2) {
          setNotifications(previous => [...previous, { id: crypto.randomUUID(), chapterId: id, message: `第${chapter.number}话审阅完成${issues.length ? ` · ${issues.length} 个问题` : ' · 未发现问题'}（预览）`, read: false }])
        }
      }, delay)))
    })
  }
  function changeStage(stage: Stage) {
    if (archiveSaving.current) return
    if (archive) setArchive(null)
    if (!selected || selected.published || stage === selected.stage) return
    if (!preview && selected.productionStatus && !['AUTHOR_REVISION', 'CANCELLED'].includes(selected.productionStatus)) {
      if (stage === 'Review') { void leave(() => { sessionStorage.removeItem(readerStageKey(selected.id, selected.versionId!)); update(selected.id, { stage }) }); return }
      if (stage === 'Draft') { setToast('正文已进入审阅，请通过修改流程继续写作。'); return }
    }
    if (!preview && (stage === 'Reader' || stage === 'Final')) {
      if (selected.productionState?.status !== 'REVISION_READY' || selected.productionState.awaiting_user
        || selected.revisionRequest || selected.productionError) { rejectStage(stage); return }
      void leave(() => {
        if (stage === 'Reader') sessionStorage.setItem(readerStageKey(selected.id, selected.versionId!), 'open')
        else sessionStorage.removeItem(readerStageKey(selected.id, selected.versionId!))
        update(selected.id, { stage })
      }); return
    }
    if (!preview && selected.versionId) sessionStorage.removeItem(readerStageKey(selected.id, selected.versionId))
    if (!preview && stage === 'Draft' && !selected.documentId) { setToast('请先确认大纲并生成正文。'); return }
    if (stage === 'Review') {
      if (selected.review === 'idle') startReview(selected.id)
      else void leave(() => update(selected.id, { stage }))
      return
    }
    if (stage === 'Draft' && !selected.outline.trim()) { setToast('请先确认本章大纲。'); return }
    if ((stage === 'Reader' || stage === 'Final') && (selected.review !== 'done' || selected.issues.some(issue => issue.level === 'Block'))) {
      rejectStage(stage); return
    }
    void leave(() => update(selected.id, { stage }))
  }
  useEffect(() => {
    const rawStage = new URLSearchParams(location.search).get('stage')
    if (!rawStage) return
    const matched = stages.find(s => s.toLowerCase() === rawStage.toLowerCase())
    if (matched && selected && selected.stage !== matched && !selected.published) {
      const timer = window.setTimeout(() => {
        changeStage(matched)
      }, 0)
      return () => window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.search, selected?.id, selected?.stage, selected?.published])
  function addChapter(volume: string) {
    if (creatingChapter.current) return
    void leave(() => {
      if (creatingChapter.current) return
      creatingChapter.current = true; setChapterPending(true)
      const request = navigation.current
      const creation = preview
        ? Promise.resolve(newStudioChapter(crypto.randomUUID(), Math.max(0, ...currentChapters.current.map(item => item.number)) + 1, '未命名章节', volume))
        : createChapter(project!.id, { title: '未命名章节', metadata: { volume } }).then(chapter => newStudioChapter(chapter.id, chapter.chapter_number, chapter.title || '未命名章节', typeof chapter.metadata.volume === 'string' ? chapter.metadata.volume : volume))
      void creation.then(chapter => {
        const next = [...currentChapters.current, chapter]
        currentChapters.current = next; setChapters(next)
        setVolumes(items => items.includes(chapter.volume) ? items : [...items, chapter.volume])
        if (preview) update(chapter.id, { draft: chapter.draft })
        if (request === navigation.current) { setArchive(null); setSelectedId(chapter.id); setPage('Create'); navigateStudio('Create', chapter.id) }
      }).catch(() => setToast('新建章节失败，请重试。')).finally(() => { creatingChapter.current = false; setChapterPending(false) })
    })
  }
  async function publish() {
    if (!selected || productionPending.current || archiveSaving.current || selected.review !== 'done' || selected.issues.some(issue => issue.level === 'Block')) return
    if (preview) { update(selected.id, { published: true }); setPublishing(false); setToast('预览章节已发布，正文已锁定为只读。'); return }
    const controller = new AbortController()
    productionPending.current = controller; setProductionBusyId(selected.id)
    try {
      if (!editor.current || !await editor.current.flush()) { update(selected.id, { productionError: '正文或反馈尚未保存，请先保存后再定稿。' }); return }
      controller.signal.throwIfAborted()
      const patch = await finalizeStudioChapter(project!.id, selected, controller.signal)
      update(selected.id, patch); setPublishing(false); setToast('本章已在本地定稿。')
    } catch {
      if (!controller.signal.aborted) update(selected.id, { productionError: '本地定稿尚未核对完成。重试会读取服务器结果，不会重复创建终稿。' })
    } finally {
      if (productionPending.current === controller) { productionPending.current = null; setProductionBusyId('') }
    }
  }

  if (initialId && !chapters.some(chapter => chapter.id === initialId)) return <div className="studio-load"><p role="alert">此作品中未找到该章节，可能已被删除或链接有误。</p><Link to={preview ? '/preview/studio' : `/projects/${encodeURIComponent(project!.id)}/studio`}>返回作品</Link></div>
  return <div className={`studio${hidden ? ' is-focused' : ''}${page === 'Setting' ? ' is-setting' : ''}`} data-focus-mode={focus}
    onClickCapture={event => {
      const target = (event.target as HTMLElement).closest<HTMLButtonElement>('.studio-icon')
      if (!target || target.disabled || target.classList.contains('has-state') || target.closest('.studio-page-nav')) return
      target.classList.remove('is-pressing'); void target.offsetWidth; target.classList.add('is-pressing')
    }} onAnimationEnd={event => { if (event.animationName === 'studio-icon-press') (event.target as HTMLElement).closest('.studio-icon')?.classList.remove('is-pressing') }}>
    <header className="studio-header">
      <div className="studio-tools">
        <IconButton icon="home" label="返回主页" className={`studio-chrome${hidden ? ' is-hidden' : ''}`} onClick={() => void leave(() => navigate('/'))} />
        <IconButton icon="setting" label="设置" className={`studio-chrome${hidden ? ' is-hidden' : ''}`} onClick={() => goPage('Setting')} />
        <IconButton icon={focus === 'manual' ? 'focus' : 'show'} label={focus === 'manual' ? '退出免打扰' : '手动免打扰'} aria-pressed={focus === 'manual'} onClick={() => { setFocus(value => value === 'manual' ? 'off' : 'manual'); setNoticesOpen(false) }} />
        <div className={`studio-notification-button${unread ? ' has-unread' : ''}`}><IconButton icon="notification" label={`通知${unread ? `，${unread} 条未读` : ''}`} aria-expanded={noticesOpen} onClick={() => { setNoticesOpen(!noticesOpen); if (!noticesOpen) setNotifications(items => items.map(item => ({ ...item, read: true }))) }} /></div>
      </div>
      <nav ref={workflow} className={`studio-workflow studio-chrome${hidden || page !== 'Create' ? ' is-hidden' : ''}`} style={{ '--workflow-progress': selected ? stages.indexOf(selected.stage) / (stages.length - 1) : 0 } as CSSProperties} aria-label="创作阶段">
        <span className="studio-workflow-progress" aria-hidden="true" />
        {stages.map(stage => <button key={stage} aria-current={selected?.stage === stage ? 'step' : undefined} onClick={() => changeStage(stage)} disabled={!selected || selected.published} title={selected?.published ? preview ? '已发布章节不可修改' : '已定稿章节不可修改' : stage}>{stage}<i /></button>)}
      </nav>
      <nav className={`studio-page-nav studio-chrome${hidden ? ' is-hidden' : ''}`} aria-label="小说页面">
        <IconButton icon="page-arrow" className="is-previous" label="上一个页面" onClick={() => goPage(pages[(pages.indexOf(requestedPage.current) - 1 + pages.length) % pages.length], -1)} />
        <PageName page={page} direction={pageDirection} />
        <IconButton icon="page-arrow" label="下一个页面" onClick={() => goPage(pages[(pages.indexOf(requestedPage.current) + 1) % pages.length], 1)} />
      </nav>
    </header>
    {noticesOpen && <section className="studio-notifications" aria-label="通知列表"><h2>通知</h2>{!notifications.length && <p className="studio-muted">暂无通知</p>}{notifications.slice().reverse().map(notice => <button key={notice.id} onClick={() => { selectChapter(notice.chapterId); setNoticesOpen(false) }}>{notice.message}<small>查看本章报告</small></button>)}</section>}
    <div className="studio-announcer" role="status" aria-live="polite">{notifications.at(-1)?.message}</div>
    <div className="studio-page-viewport" ref={pageSurfaces}>
      <div className={`studio-page-surface is-setting${page === 'Setting' ? ' is-current' : ''}`} data-studio-page="Setting" inert={page !== 'Setting'} aria-hidden={page !== 'Setting'}><section className="studio-body" aria-label="Setting 工作区"><StudioSetting ref={settingEditor} preview={preview} hidden={hidden} activePage={page === 'Setting'} ready={pageReady} onTyping={() => setFocus(value => value === 'off' ? 'auto' : value)} /></section></div>
      <div className={`studio-page-surface${page === 'Detail' ? ' is-current' : ''}`} data-studio-page="Detail" inert={page !== 'Detail'} aria-hidden={page !== 'Detail'}><section className="studio-body" aria-label="Detail 工作区"><div className="studio-detail">
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
          <StudioLabels projectKey={project?.id || 'preview'} initial={project?.genre ? [project.genre] : preview ? ['都市', '科幻'] : []} />
          <textarea aria-label="小说简介" value={introduction} readOnly={!preview} onChange={event => setIntroduction(event.target.value)} placeholder="introduction…" />
        </div>
      </div></section></div>
      <div className={`studio-page-surface${page === 'Create' ? ' is-current' : ''}`} data-studio-page="Create" inert={page !== 'Create'} aria-hidden={page !== 'Create'}><section className={`studio-body${writing ? ' has-prose' : ''}`} aria-label="Create 工作区">{!selected ? <div className="studio-empty"><h1>开始新的一章</h1><button disabled={chapterPending} onClick={() => addChapter(volumes.at(-1) || '第一卷')}>新建章节</button>{!preview && <Link to={`/projects/${project?.id}`}>前往现有工作台</Link>}</div> : <>
        {!archive && selected.stage === 'Outline' && <section className={`studio-outline is-${selected.outlineStep}`} key={selected.id} aria-label="本章大纲">
          <MotionFrame className="studio-outline-flow" textKey={`${selected.outlineStep}-${refresh}`}>
          {selected.outlineStep !== 'edit' && <OutlineGeneration chapter={selected} preview={preview} refresh={refresh} onIdea={idea => update(selected.id, { idea })} onGenerate={() => update(selected.id, { outlineStep: 'choose' })} onChoose={text => update(selected.id, { outline: `${selected.idea}\n\n${text}`, outlineStep: 'edit' })} onRefresh={() => setRefresh(value => value + 1)} />}
          {selected.outlineStep === 'edit' && (preview ? <OutlineEditor outline={selected.outline} comments={selected.outlineComments || []} readOnly={false}
            onChange={(outline, outlineComments) => update(selected.id, { outline, outlineComments, review: 'idle', issues: [], selected: [] })}
            onCommentsChange={outlineComments => update(selected.id, { outlineComments })}
            onSend={() => setToast('评论已保留在所选文本上；当前为交互预览，未调用真实 Agent。')}
            confirm={<IconButton icon="check" label="确认大纲并进入正文" disabled={!selected.outline.trim()} onClick={() => void leave(() => update(selected.id, { stage: 'Draft', draft: selected.draft || previewProse }))} />} />
            : <PersistedOutline key={selected.id} ref={editor} chapter={selected} projectId={project!.id} onChange={patch => patch.stage ? void leave(() => update(selected.id, patch)) : update(selected.id, patch)} onSend={() => setToast('大纲反馈已保存，Agent 修改流程尚未接入。')} />)}
          </MotionFrame>
          {!preview && <p className="studio-integration-note">{selected.outlineStep === 'edit' ? '确认当前大纲后生成正文。' : '大纲讨论流程待接入。'}<Link to={`/projects/${project?.id}/chapters/${selected.id}`}>打开现有工作台</Link></p>}
        </section>}
        {writing && <article className="studio-manuscript" aria-label="章节创作">
          <ChapterScroll key={selected.id}>
          <h1>第{selected.number}话 <span>{selected.title}</span></h1>
          <div ref={modificationPanel} className={`studio-context-wrap${pins.context ? ' is-pinned' : ''}`}>
            <MotionFrame textKey={`${contextMode}-${selected.review}`} className={`studio-context${selected.stage === 'Review' && !showingOutline ? ' is-review' : ''}${reportVisible ? ' is-report' : ''}`} label="大纲与审阅面板" chrome={<>
              <div className="studio-context-tools studio-actions"><Pin label="大纲面板" pinned={pins.context} onChange={() => setPins(value => ({ ...value, context: !value.context }))} /><IconButton className={`studio-bulb${showingOutline ? ' is-lit' : ''}`} icon="bulb" label={contextOutline ? '显示要求或审阅' : '显示大纲'} aria-pressed={showingOutline} disabled={Boolean(archive) || selected.stage === 'Final'} onClick={() => setContextOutline(!contextOutline)} /></div>
              <div className="studio-context-footer studio-actions">
                <span data-motion-control data-visible={reportVisible} aria-hidden={!reportVisible} inert={!reportVisible}><IconButton icon="review" label="进入读者环节" disabled={selected.issues.some(issue => issue.level === 'Block')} onClick={() => changeStage('Reader')} /></span>
                <span data-motion-control data-visible={sendVisible} aria-hidden={!sendVisible} inert={!sendVisible}><IconButton icon="send" label={selected.stage === 'Review' ? `修改所选 ${selected.selected.length} 项并重新审阅` : '发送写作要求'} disabled={Boolean(productionBusyId) || Boolean(selected.productionError) || (selected.stage === 'Review' ? !selected.selected.length : (!preview && selected.feedbackReadOnly !== false) || !selected.requirements.trim() && !selected.draftComments?.some(comment => comment.submitted))} onClick={() => {
                  if (selected.stage === 'Review') startReview(selected.id, selected.selected)
                  else if (preview) setToast('写作要求与已提交评论已保留，Agent 修改接口尚未接入。')
                  else void runFormalFeedback(selected.id)
                }} /></span>
              </div>
              {(archive ? !!archive.comments?.length : selected.stage === 'Draft' && !!selected.draftComments?.some(comment => comment.submitted)) && <SubmittedComments key={archive?.id || selected.id} archive={Boolean(archive)} activeId={activeComment?.id} comments={archive ? archive.comments || [] : selected.draftComments!.filter(comment => comment.submitted)} readOnly={Boolean(archive) || productionBusyId === selected.id || Boolean(selected.productionError) || (!preview && selected.feedbackReadOnly !== false) || selected.published}
                onSelect={id => { setContextOutline(false); setCommentRequest(activeComment?.id === id ? null : { id }) }}
                onOrder={ids => updateDraftFeedback({ comments: [...(selected.draftComments || []).filter(comment => !comment.submitted), ...ids.map(id => selected.draftComments!.find(comment => comment.id === id)!)] })} />}
            </>}>
              <div className="studio-context-heading"><h2><TextSweep text={archive ? activeComment ? '存档评论' : '存档写作要求' : showingOutline ? '本章大纲' : selected.stage === 'Review' ? '审阅' : '给写作 Agent 的要求'} /></h2></div>
              {!preview && !showingOutline && <div>
                {productionBusyId === selected.id && <p role="status" className="studio-muted">{selected.stage === 'Draft' ? (selected.feedbackRequest ? '正在根据要求修改正文…' : '正在保存并提交正文…') : '正在执行当前审阅…'}</p>}
                {selected.productionError && <p role="alert">{selected.productionError}</p>}
                {!selected.productionError && selected.productionState?.awaiting_user && selected.productionState.action_kind === 'review_warning'
                  && <button disabled={Boolean(productionBusyId)} onClick={() => void runFormalReview(selected.id, selected.productionState)}>接受当前警告并继续审阅</button>}
                {selected.stage === 'Draft' && (selected.feedbackRequest || selected.productionError) && (
                  <button disabled={Boolean(productionBusyId)} onClick={() => void (selected.feedbackRequest ? runFormalFeedback(selected.id) : runFormalReview(selected.id))}>{selected.feedbackRequest ? '重试修改' : '重试审阅'}</button>
                )}
                {selected.stage === 'Review' && (selected.productionError || (!selected.productionState?.awaiting_user
                  && (reviewStages.includes(selected.productionStatus || '') || selected.productionStatus === 'FAILED')))
                  && <button disabled={Boolean(productionBusyId)} onClick={() => void runFormalReview(selected.id)}>{selected.productionError || selected.productionStatus === 'FAILED' ? '重试审阅' : '继续审阅'}</button>}
              </div>}
              <div className={`studio-context-body${contextInput ? ' is-input' : ''}`}>
              <p className={contextMode === 'Review' ? 'studio-review-log' : 'studio-outline-text'} aria-label={contextMode === 'Review' ? '审阅过程' : undefined} aria-hidden={contextInput}><TextSweep text={contextText} changeKey={`${contextMode}-${selected.completed}`} /></p>
              {showingOutline || (archive && !activeComment) ? null : selected.stage === 'Review' && !activeComment ? <>
                {selected.review === 'idle' && <button disabled={!preview} onClick={() => startReview(selected.id)}>开始审阅</button>}
                {selected.review === 'done' && <div className="studio-report" aria-label="审阅报告">
                  <h3 data-sweep-text>审阅报告 <small>{selected.issues.length} 个问题{preview ? ' · 示例报告' : ''}</small></h3>
                  <p data-sweep-text className="studio-muted">勾选的问题将交给 Agent 修改，Block 为必选项。</p>
                  {reviewers.map(reviewer => {
                    const issues = selected.issues.filter(issue => issue.reviewer === reviewer)
                    const role = ({ 'Editor Reviewer': 'editor_agent', 'Chief Reviewer': 'chief_editor_agent', 'Lore Reviewer': 'lore_agent' } as const)[reviewer]
                    const report = selected.reviewReports?.find(item => item.reviewer_role === role)
                    const highest = !preview && !report ? reviewer === 'Chief Reviewer' && selected.chiefEditorRequired === false ? '未启用'
                      : reviewer === currentReviewer ? productionBusyId === selected.id ? '审阅中' : '待审阅' : '未审阅'
                      : ['Block', 'Warning', 'Suggestion'].find(level => issues.some(issue => issue.level === level)) || 'PASS'
                    return <details className="studio-review-group" key={`${selected.id}-${reviewer}`} open>
                      <summary><img src="/ui/studio/ooui-collapse.svg" alt="" /><span data-sweep-text>{reviewer}</span><span data-sweep-text className={`studio-severity is-${highest.toLowerCase()}`}>{highest}</span></summary>
                      {report && <p data-sweep-text className="studio-muted">{report.summary}</p>}
                      {issues.map(issue => <div className="studio-issue" key={issue.id}>
                        <input type="checkbox" aria-label={`交给 Agent 修改：${issue.title}`} checked={selected.selected.includes(issue.id)} disabled={issue.level === 'Block' || productionBusyId === selected.id || Boolean(selected.productionError) || Boolean(selected.revisionRequest)} onChange={event => {
                          update(selected.id, { selected: event.target.checked ? [...selected.selected, issue.id] : selected.selected.filter(id => id !== issue.id) })
                          setToast(`${event.target.checked ? '已加入待修改列表' : '已移出待修改列表'}：${issue.title}`)
                        }} />
                        <div>
                          <button className="studio-issue-title" disabled={!issue.quote} onClick={() => { setLocate(issue.quote); setToast(`对应原文：${issue.quote}`) }}><span data-sweep-text>{issue.level} · {issue.title}</span></button>
                          <p><StreamText text={issue.detail} startedAt={selected.reviewFinishedAt ?? 0} /></p>
                        </div>
                      </div>)}
                      {!issues.length && (preview || report) && <p data-sweep-text className="studio-muted">本轮审阅未发现问题。</p>}
                    </details>
                  })}
                </div>}
              </> : <textarea maxLength={activeComment ? 4000 : 8000} aria-label={activeComment ? archive ? '存档评论' : '已提交评论' : '给写作 Agent 的要求'} value={activeComment ? activeComment.text : selected.requirements} readOnly={Boolean(archive) || productionBusyId === selected.id || Boolean(selected.productionError) || (!preview && selected.feedbackReadOnly !== false) || selected.published || selected.stage !== 'Draft'} placeholder="希望这一段怎样展开？" onChange={event => updateDraftFeedback(activeComment
                ? { comments: selected.draftComments?.map(comment => comment.id === activeComment.id ? { ...comment, text: event.target.value } : comment) }
                : { requirements: event.target.value })} />}
              </div>
            </MotionFrame>
          </div>
          <MotionFrame className="studio-prose-transition" textKey={archive?.id || 'current'}>
          <ProseEditor key={`${selected.id}-${selected.documentId}-${archiveRevision}`} ref={editor} chapter={selected} projectId={project?.id} preview={preview} archive={archive || undefined} busy={archiveBusy || productionBusyId === selected.id} locate={locate} ready={pageReady} visible={page === 'Create'} onFeedbackChange={(draftComments, requirements, feedbackReadOnly) => update(selected.id, { draftComments, requirements, feedbackReadOnly })} onSubmittedSelect={id => {
            setContextOutline(false); setCommentRequest({ id })
            modificationPanel.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })
          }} onSaved={(versionId, draft) => update(selected.id, { versionId, draft })} onTyping={() => setFocus(value => value === 'manual' ? value : 'auto')} onChange={(draft, draftComments) => update(selected.id, { draft, draftComments, review: 'idle', issues: [], selected: [] })}
            onCommentsChange={draftComments => update(selected.id, { draftComments })} onSend={() => {
              const chapter = currentChapters.current.find(chapter => chapter.id === selected.id)
              if (!chapter || chapter.stage !== 'Draft' || chapter.review === 'running') return
              if (preview) update(chapter.id, { draftComments: chapter.draftComments?.map(comment => ({ ...comment, submitted: true })) })
              else setToast('评论已加入待处理要求。')
              setCommentRequest({ id: chapter.draftComments?.find(comment => preview ? !comment.submitted : comment.submitted)?.id || '' })
              setContextOutline(false)
              requestAnimationFrame(() => {
                modificationPanel.current?.scrollIntoView?.({ block: 'start', behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
              })
            }} />
          </MotionFrame>
          </ChapterScroll>
          <div className={`studio-manuscript-footer studio-chrome${hidden ? ' is-hidden' : ''}`}>
            {archive ? <><span className="studio-muted">{archive.summary} · {archiveTime(archive.createdAt)}</span><div className="studio-actions"><button disabled={archiveBusy} onClick={() => selectChapter(selected.id)}>返回当前正文</button><button disabled={archiveBusy || selected.published || selected.review === 'running'} onClick={() => void saveArchive(true)}>从此存档继续写作</button></div></> : selected.stage === 'Draft' && <><span className="studio-muted">选中正文添加评论</span><div className="studio-actions"><IconButton icon="restorepoint" label="创建还原点" disabled={archiveBusy || Boolean(productionBusyId) || selected.published || !selected.draft.trim() || (!preview && !selected.documentId)} onClick={() => void saveArchive()} /><IconButton icon="review" label="提交审阅" disabled={archiveBusy || Boolean(productionBusyId) || !selected.draft.trim()} onClick={() => startReview(selected.id)} /></div></>}
            {!archive && selected.stage === 'Final' && <><span className="studio-muted">{preview ? selected.published ? '本章已发布' : '最后确认后发布' : selected.published ? '本章已在本地定稿' : '等待作者确认本地定稿'}</span><IconButton icon="publish" label={preview ? '发布章节' : '本地定稿'} disabled={selected.published || Boolean(productionBusyId)} onClick={() => setPublishing(true)} /></>}
          </div>
        </article>}
        {!archive && selected.stage === 'Reader' && !preview && <StudioReader key={`${selected.id}:${selected.versionId}`} projectId={project!.id} chapter={selected} onChange={patch => update(selected.id, patch)} onFinal={() => changeStage('Final')} />}
        {!archive && selected.stage === 'Reader' && preview && <section className={`studio-reader studio-enter${selected.discussion ? ' is-discussion' : ''}`} aria-label="读者环节" key={`${selected.id}-${selected.discussion}`}>
          {!selected.discussion ? <><h2>邀请读者</h2><div className="studio-reader-list">{readerPersonas.map(([id, name, description]) => <div className="studio-reader-person" key={id}><span className="studio-avatar" aria-hidden="true" /><div><strong>{name}</strong><small>{description}</small></div><button className={selected.readers.includes(id) ? 'is-invited' : ''} aria-pressed={selected.readers.includes(id)} aria-label={`${selected.readers.includes(id) ? '取消邀请' : '邀请'}${name}`} onClick={() => update(selected.id, { readers: selected.readers.includes(id) ? selected.readers.filter(value => value !== id) : [...selected.readers, id] })}>{selected.readers.includes(id) ? '取消' : '邀请'}</button></div>)}</div><div className="studio-reader-footer"><button onClick={() => changeStage('Final')}>跳过读者环节</button><button className="studio-primary" disabled={!selected.readers.length} onClick={() => update(selected.id, { discussion: true })}>开始阅读</button></div></> : <><div className="studio-reader-transcript"><div className="studio-message"><span className="studio-avatar" /><div><p>主持人 <time>刚刚 · 预览</time></p><button className="studio-file" onClick={() => changeStage('Final')}><img src={asset('chapter')} alt="" />第{selected.number}话 {selected.title}</button></div></div><div className="studio-message"><span className="studio-avatar" /><div><p>主持人 <time>刚刚 · 预览</time></p><span className="studio-accent">@全体成员</span><p>阅读这篇文章并给出意见。</p></div></div>{readerPersonas.filter(([id]) => selected.readers.includes(id)).map(([id, name, description]) => <div className="studio-message" key={id}><span className="studio-avatar" /><div><p>{name} <time>示例发言</time></p><p>我会{description.replace('关注', '重点看')}。这段食堂的日常很有画面感，期待接下来人物之间的关系。</p></div></div>)}</div><div className="studio-reader-footer"><span className="studio-muted">作者旁观 · 无需参与讨论</span><button onClick={() => changeStage('Final')}>结束旁观，进入终稿</button></div></>}
        </section>}
      </>}
    </section></div></div>
    {page === 'Create' && <aside className={`studio-sidebar${sidebarOpen ? ' is-open' : ''}`} aria-label="章节侧边栏" onPointerEnter={() => { if (!hidden) setSidebarOpen(true) }} onPointerLeave={() => setSidebarOpen(false)} onFocus={() => { if (!hidden) setSidebarOpen(true) }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setSidebarOpen(false) }}>
      <button className="studio-sidebar-trigger" aria-label="展开章节侧边栏" disabled={hidden} onClick={() => setSidebarOpen(true)}><span /></button>
      <section className={`studio-stats studio-floating${pins.stats ? ' is-pinned' : ''}${hidden && !pins.stats ? ' is-hidden' : ''}`} inert={(hidden || !sidebarOpen) && !pins.stats} aria-label="创作统计"><div><img src={asset('time')} alt="时间" />{Math.floor(seconds / 3600)}h {Math.floor(seconds / 60) % 60}m {seconds % 60}s</div><div><img src={asset('font')} alt="字数" />{(selected?.draft.replace(/\s/g, '').length || 0).toLocaleString()} 字</div><Pin label="统计面板" pinned={pins.stats} onChange={() => setPins(value => ({ ...value, stats: !value.stats }))} /></section>
      <section className={`studio-directory studio-floating${pins.directory ? ' is-pinned' : ''}${hidden && !pins.directory ? ' is-hidden' : ''}`} inert={(hidden || !sidebarOpen) && !pins.directory} aria-label="章节目录"><div className="studio-directory-tools"><Pin label="章节目录" pinned={pins.directory} onChange={() => setPins(value => ({ ...value, directory: !value.directory }))} /></div><div className="studio-directory-title"><h2>{title}</h2><IconButton icon="add" label="新建卷" disabled={!preview} onClick={() => setVolumes(items => [...items, `第${items.length + 1}卷`])} /></div><div className="studio-directory-scroll">{volumes.slice().reverse().map(volume => <details key={volume} open><summary><span>{volume}</span><IconButton icon="add" label={`在${volume}新建章节`} disabled={chapterPending} onClick={event => { event.preventDefault(); addChapter(volume) }} /></summary>{chapters.filter(chapter => chapter.volume === volume).slice().sort((a, b) => b.number - a.number).map(chapter => <ChapterArchiveRow projectId={project?.id} key={chapter.id} chapter={chapter} current={selectedId === chapter.id} activeArchive={archive?.chapterId === chapter.id ? archive.id : undefined} preview={preview} local={archives[chapter.id] || []} revision={archiveRevision} onChapter={() => selectChapter(chapter.id)} onArchive={entry => viewArchive(chapter, entry)} />)}</details>)}</div></section>
    </aside>}
    <footer className={`studio-bottom studio-chrome${hidden ? ' is-hidden' : ''}`}><button onClick={() => void leave(() => navigate('/'))}>返回书架</button><span>{preview ? '交互预览 · Agent 流程为示例' : '真实章节 · 流程已接通'}</span>{!preview && selected && <button onClick={() => void leave(() => navigate(`/projects/${project?.id}/chapters/${selected.id}`))}>现有工作台</button>}</footer>
    <GlobalAssistant hidden={hidden} project={project} chapter={selected} currentView={selected?.stage || page} preview={preview} />
    {(toast || storageError) && <div className="studio-toast" role={storageError ? 'alert' : 'status'}>{storageError || toast}<button aria-label="关闭提示" onClick={() => { setToast(''); setStorageError('') }}>×</button></div>}
    {publishing && <PublishDialog preview={preview} busy={Boolean(productionBusyId)} error={selected?.productionError} onClose={() => setPublishing(false)} onConfirm={() => void publish()} />}
  </div>
}
