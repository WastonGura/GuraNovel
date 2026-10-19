import { useEffect, useEffectEvent, useImperativeHandle, useRef, useState, type CSSProperties, type ReactNode, type Ref } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { flushSync } from 'react-dom'
import { MotionFrame } from './StudioMotion'
import OutlineEditor from './OutlineEditor'
import { useCommentDrag } from './useCommentDrag'
import { reanchorComments, reanchorFeedbackComments, restoreOutlineComments, type OutlineComment } from './studioPreview'
import SettingGraph from './SettingGraph'
import SearchDialog from './SearchDialog'
import SettingConversation from './SettingConversation'
import SettingNoteList from './SettingNoteList'
import { applySettingChanges, categoryNames, emptySettingConversation, noteLinks, readSettingWorkspace, resolveNote, settingExampleReply, settingStorageKey, stagedSettingComments, type SettingCategory, type SettingChange, type SettingConversation as Conversation, type SettingNote } from './settingNotes'
import type { SettingCollection, SettingChangeProposal } from './api/client'
import './studioSetting.css'

export type SettingView = { id: string; category: SettingCategory; mode: 'note' | 'graph' | 'search' | 'chat' }

export interface StudioSettingProps {
  preview?: boolean
  hidden?: boolean
  activePage?: boolean
  ready?: boolean
  onTyping?: () => void
  ref?: Ref<{ flush: () => Promise<boolean> }>
  collection?: SettingCollection | null
  backendNotes?: SettingNote[]
  readOnly?: boolean
  referencingProjects?: { id: string; title: string }[]
  onSaveNoteContent?: (noteId: string, content: string, expectedVersionId?: string, comments?: OutlineComment[]) => Promise<{ versionId: string } | 'conflict' | false>
  onApplyProposal?: (proposal: SettingChangeProposal) => Promise<{ versionId: string; documentId: string } | 'conflict' | false>
  onRenameNote?: (noteId: string, newTitle: string) => Promise<boolean>
  onCreateNote?: (category: SettingCategory, title: string, tempId: string) => Promise<SettingNote | null>
  onDeleteNotes?: (noteIds: string[]) => Promise<boolean | string[]>
  onRestoreNotes?: (noteIds: string[]) => Promise<boolean | string[]>
  onViewChange?: (view: SettingView) => void
  onAssistantContext?: (context: { documentId?: string; versionId?: string }) => void
  initialView?: Partial<SettingView>
  initialConversation?: Conversation
  syncUrlParams?: boolean
  saveStatusText?: string
  defaultPinned?: boolean
  libraryFooter?: ReactNode
}

function LinkedText({ text, notes, references, comments, onOpen }: { text: string; notes: SettingNote[]; references: string[]; comments: OutlineComment[]; onOpen: (title: string) => void }) {
  const parts: ReactNode[] = []
  const source = (value: string, start: number) => {
    const boundaries = [...new Set([start, start + value.length, ...comments.flatMap(comment => [comment.start, comment.end]).filter(point => point > start && point < start + value.length)])].sort((a, b) => a - b)
    return boundaries.slice(0, -1).map((from, index) => {
      const to = boundaries[index + 1], comment = comments.find(comment => comment.start <= from && comment.end >= to && comment.start !== comment.end)
      return <span key={from} data-source-start={from} data-reading-comment={comment?.id} style={comment ? { textDecoration: `underline ${comment.color}`, textUnderlineOffset: '5px', cursor: 'pointer' } : undefined}>{value.slice(from - start, to - start)}</span>
    })
  }
  let last = 0
  for (const match of text.matchAll(/\[\[([^\]\n[]+)\]\]/g)) {
    const title = match[1].trim()
    parts.push(...source(text.slice(last, match.index), last))
    const target = resolveNote(notes, title)
    parts.push(title ? <button key={`link-${match.index}`} className={`setting-inline-link${target ? '' : ' is-missing'}`} aria-label={title} title={target ? `${title}：${target.body.replace(/\[\[|\]\]/g, '').slice(0, 100)}` : `创建引用条目：${title}`} onClick={event => { if (window.getSelection()?.isCollapsed !== false && !(event.target as HTMLElement).closest('[data-reading-comment]')) onOpen(title) }}>{source(title, match.index + 2 + match[1].indexOf(title))}<sup aria-hidden="true">{references.indexOf(title) + 1}</sup></button> : match[0])
    last = match.index + match[0].length
  }
  parts.push(...source(text.slice(last), last))
  return <>{parts}</>
}

export default function StudioSetting({
  preview = true,
  hidden = false,
  activePage = true,
  ready = true,
  onTyping = () => {},
  ref,
  collection,
  backendNotes,
  readOnly: explicitReadOnly,
  referencingProjects,
  onSaveNoteContent,
  onApplyProposal,
  onRenameNote,
  onCreateNote,
  onDeleteNotes,
  onRestoreNotes,
  onViewChange,
  onAssistantContext,
  initialView,
  initialConversation,
  syncUrlParams = false,
  saveStatusText,
  defaultPinned,
  libraryFooter,
}: StudioSettingProps) {
  const location = useLocation(), navigate = useNavigate()
  const searchParams = new URLSearchParams(location.search)
  const initialCategoryParam = searchParams.get('category') as SettingCategory | null
  const initialNoteParam = searchParams.get('note')
  const initialModeParam = searchParams.get('mode') as SettingView['mode'] | null

  const [initial] = useState(() => {
    if (backendNotes) {
      const restored = backendNotes.map(n => {
        try {
          const feedback = JSON.parse(sessionStorage.getItem(`guranovel:setting-feedback-recovery:${n.id}`) || 'null')
          if (feedback && typeof feedback.body === 'string' && typeof feedback.versionId === 'string') {
            return { ...n, body: feedback.body as string, versionId: feedback.versionId as string,
              comments: restoreOutlineComments(feedback.comments, feedback.body) }
          }
          const draft = sessionStorage.getItem(`guranovel:setting-draft-recovery:${n.id}`)
          if (draft !== null && draft !== n.body) {
            return { ...n, body: draft }
          }
        } catch (_err: unknown) {
          void _err
        }
        return n
      })
      return { notes: restored, conversation: initialConversation || emptySettingConversation(), error: '' }
    }
    if (!preview) return { notes: [] as SettingNote[], conversation: initialConversation || emptySettingConversation(), error: '' }
    try {
      const stored = readSettingWorkspace()
      return { notes: stored.notes, conversation: initialConversation || stored.conversation, error: '' }
    }
    catch { return { notes: [] as SettingNote[], conversation: initialConversation || emptySettingConversation(), error: '本机设定未能读取，原始数据已保留。请先备份后重试。' } }
  })
  const [notes, setNotes] = useState(initial.notes)
  const currentNotes = useRef(initial.notes)
  const [conversation, setConversation] = useState(initial.conversation)
  const currentConversation = useRef(initial.conversation)
  const [chatVisited, setChatVisited] = useState(Boolean(initial.conversation.messages.length || initial.conversation.draft || initial.conversation.stagedComments?.length))
  const transfer = useRef<ViewTransition | null>(null)
  useEffect(() => () => { transfer.current?.skipTransition(); document.documentElement.removeAttribute('data-setting-transfer') }, [])
  const [category, setCategory] = useState<SettingCategory>(
    initialCategoryParam || location.state?.settingView?.category || initialView?.category || 'setting'
  )
  const [selectedId, setSelectedId] = useState(
    initialNoteParam || location.state?.settingView?.id || initialView?.id || initial.notes[0]?.id || ''
  )
  const [mode, setMode] = useState<SettingView['mode']>(
    initialModeParam || location.state?.settingView?.mode || initialView?.mode || 'note'
  )
  const [editing, setEditing] = useState(false)
  const [searchReturnMode, setSearchReturnMode] = useState<SettingView['mode']>(mode === 'search' ? 'note' : mode)
  const displayMode = mode === 'search' ? searchReturnMode : mode
  const [draftTitle, setDraftTitle] = useState('')
  const titleWrites = useRef(new Map<string, { title: string; success: boolean | null; promise: Promise<boolean> }>())
  function enqueueTitle(id: string, title: string) {
    const previous = titleWrites.current.get(id)
    if (previous?.title === title && previous.success !== false) return
    const write = { title, success: null as boolean | null, promise: Promise.resolve(false) }
    write.promise = (previous?.promise ?? Promise.resolve(true)).then(() => onRenameNote!(id, title)).then(success => {
      write.success = success
      if (!success) setError('标题尚未保存，请保持页面打开并重试。')
      else if (titleWrites.current.get(id) === write) setError(value => value === '标题尚未保存，请保持页面打开并重试。' ? '' : value)
      return success
    }).catch(() => { write.success = false; setError('标题尚未保存，请保持页面打开并重试。'); return false })
    titleWrites.current.set(id, write)
  }
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if ([...titleWrites.current.values()].some(write => write.success !== true)) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [])
  const [query, setQuery] = useState('')
  const [pinned, setPinned] = useState(defaultPinned ?? false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sweep, setSweep] = useState(0)
  const [error, setError] = useState(initial.error)
  const [deleted, setDeleted] = useState<{ items: { note: SettingNote; index: number }[]; staged: NonNullable<Conversation['stagedComments']> } | null>(null)
  const mutationPending = useRef(false)
  const [mutationBusy, setMutationBusy] = useState(false)
  const noteInput = useRef<HTMLTextAreaElement>(null)

  const [prevBackendNotes, setPrevBackendNotes] = useState(backendNotes)
  if (backendNotes && backendNotes !== prevBackendNotes) {
    setPrevBackendNotes(backendNotes)
    const restored = backendNotes.map(n => {
      try {
        const draft = sessionStorage.getItem(`guranovel:setting-draft-recovery:${n.id}`)
        if (draft !== null && draft !== n.body) {
          return { ...n, body: draft }
        }
      } catch (_err: unknown) {
        void _err
      }
      return n
    })
    setNotes(restored)
    if (restored.length > 0) {
      if (!selectedId || !restored.some(n => n.id === selectedId)) {
        const initialTarget = initialNoteParam && restored.find(n => n.id === initialNoteParam)
        if (initialTarget) {
          setSelectedId(initialTarget.id)
        } else {
          const cat = initialCategoryParam || category
          const found = restored.find(n => n.category === cat) || restored[0]
          setSelectedId(found.id)
        }
      }
    }
  }

  useEffect(() => {
    currentNotes.current = notes
  }, [notes])

  const active = notes.find(note => note.id === selectedId)
  useEffect(() => { onAssistantContext?.({ documentId: active?.documentId, versionId: active?.versionId }) }, [onAssistantContext, active?.documentId, active?.versionId])
  const readOnly = explicitReadOnly !== undefined ? explicitReadOnly : ((!preview && !onSaveNoteContent && !onApplyProposal) || Boolean(initial.error))
  const shown = notes.filter(note => note.category === category)
  const results = notes.filter(note => `${note.title}\n${note.body}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  const sidebarVisible = pinned || (sidebarOpen && !hidden)
  const references = active ? noteLinks(active.body) : []
  const stagedIds = (conversation.stagedComments || []).filter(item => item.noteId === active?.id).map(item => item.commentId)
  const submitted = active?.comments?.filter(comment => comment.submitted || stagedIds.includes(comment.id)) || []

  function rememberView(next: SettingView) {
    const current = { id: selectedId, category, mode }
    if (JSON.stringify(current) === JSON.stringify(next)) return
    if (syncUrlParams) {
      const params = new URLSearchParams(location.search)
      if (next.id) params.set('note', next.id); else params.delete('note')
      params.set('category', next.category)
      params.set('mode', next.mode)
      navigate({ pathname: location.pathname, search: `?${params.toString()}` }, {
        replace: false,
        state: { ...location.state, settingView: next },
      })
    } else {
      navigate(location, { replace: true, state: { ...location.state, settingView: current } })
      navigate(location, { state: { ...location.state, settingView: next } })
    }
    if (onViewChange) onViewChange(next)
  }
  interface NoteSaveQueue {
    timer: ReturnType<typeof setTimeout> | null
    inFlight: boolean
    lastSavedBody: string | null
    waiters: Array<(success: boolean) => void>
  }
  const saveQueues = useRef<Map<string, NoteSaveQueue>>(new Map())

  useEffect(() => {
    const queues = saveQueues.current
    return () => {
      queues.forEach(q => {
        if (q.timer) clearTimeout(q.timer)
      })
    }
  }, [])

  async function runSave(id: string): Promise<boolean> {
    if (!onSaveNoteContent) return true
    let q = saveQueues.current.get(id)
    if (!q) {
      q = { timer: null, inFlight: false, lastSavedBody: null, waiters: [] }
      saveQueues.current.set(id, q)
    }
    if (q.timer) {
      clearTimeout(q.timer)
      q.timer = null
    }
    if (q.inFlight) {
      return new Promise<boolean>(resolve => q!.waiters.push(resolve))
    }
    const note = currentNotes.current.find(n => n.id === id)
    if (!note) return true
    const snapshot = JSON.stringify([note.body, note.comments || []])
    if (q.lastSavedBody === snapshot) {
      try {
        sessionStorage.removeItem(`guranovel:setting-draft-recovery:${id}`)
        sessionStorage.removeItem(`guranovel:setting-feedback-recovery:${id}`)
      } catch (_err: unknown) {
        void _err
      }
      return true
    }

    q.inFlight = true
    const bodyToSave = note.body
    const expectedVersion = note.versionId

    try {
      const res = note.comments === undefined ? await onSaveNoteContent(id, bodyToSave, expectedVersion)
        : await onSaveNoteContent(id, bodyToSave, expectedVersion, note.comments)
      if (res === 'conflict') {
        q.inFlight = false
        const w = q.waiters
        q.waiters = []
        w.forEach(resolve => resolve(false))
        return false
      } else if (res && typeof res === 'object' && res.versionId) {
        q.lastSavedBody = snapshot
        const updated = currentNotes.current.map(n => n.id === id ? { ...n, versionId: res.versionId } : n)
        currentNotes.current = updated
        setNotes(updated)
        q.inFlight = false

        const latestNote = currentNotes.current.find(n => n.id === id)
        if (latestNote && JSON.stringify([latestNote.body, latestNote.comments || []]) !== snapshot) {
          return runSave(id)
        } else {
          try {
            sessionStorage.removeItem(`guranovel:setting-draft-recovery:${id}`)
            sessionStorage.removeItem(`guranovel:setting-feedback-recovery:${id}`)
          } catch (_err: unknown) {
            void _err
          }
          setError('')
          const w = q.waiters
          q.waiters = []
          w.forEach(resolve => resolve(true))
          return true
        }
      } else {
        q.inFlight = false
        const w = q.waiters
        q.waiters = []
        w.forEach(resolve => resolve(false))
        return false
      }
    } catch {
      q.inFlight = false
      const w = q.waiters
      q.waiters = []
      w.forEach(resolve => resolve(false))
      return false
    }
  }

  function enqueueSave(id: string, debounceMs = 400) {
    let q = saveQueues.current.get(id)
    if (!q) {
      q = { timer: null, inFlight: false, lastSavedBody: null, waiters: [] }
      saveQueues.current.set(id, q)
    }
    if (q.timer) {
      clearTimeout(q.timer)
      q.timer = null
    }
    q.timer = setTimeout(() => {
      q!.timer = null
      void runSave(id)
    }, debounceMs)
  }

  function persist(next: SettingNote[], chat = currentConversation.current, requireSave = false) {
    if (readOnly) return false
    let stored = false
    if (onSaveNoteContent) {
      const previous = currentNotes.current
      currentNotes.current = next
      currentConversation.current = chat
      setNotes(next)
      setConversation(chat)
      for (const changed of next) {
        const before = previous.find(note => note.id === changed.id)
        if (!before || before.body === changed.body && before.comments === changed.comments) continue
        try {
          sessionStorage.setItem(`guranovel:setting-draft-recovery:${changed.id}`, changed.body)
          sessionStorage.setItem(`guranovel:setting-feedback-recovery:${changed.id}`, JSON.stringify({
            body: changed.body, comments: changed.comments || [], versionId: changed.versionId,
          }))
        } catch (_err: unknown) {
          void _err
        }
        enqueueSave(changed.id, 400)
      }
      return true
    }
    try { localStorage.setItem(settingStorageKey, JSON.stringify({ notes: next, conversation: chat })); setError(''); stored = true }
    catch { setError('本机保存失败，编辑仍保留在当前页，请先复制备份。'); if (requireSave) return false }
    currentNotes.current = next
    currentConversation.current = chat
    setNotes(next)
    setConversation(chat)
    return stored
  }
  function choose(note: SettingNote) {
    if (editing && !titleCommit()) return
    if (!editing && !readOnly && !persist(currentNotes.current)) return
    rememberView({ id: note.id, category: note.category, mode: 'note' })
    setSelectedId(note.id); setCategory(note.category); setMode('note'); setEditing(false)
  }
  function orderNotes(ids: string[]) {
    if (readOnly || (editing && !titleCommit())) return
    const ordered = ids.map(id => currentNotes.current.find(note => note.id === id)!), rest = [...ordered]
    persist(currentNotes.current.map(note => ids.includes(note.id) ? rest.shift()! : note), currentConversation.current, true)
  }
  function deleteNotes(ids: string[]) {
    if (readOnly || mutationPending.current || (editing && !titleCommit())) return
    const items = currentNotes.current.flatMap((note, index) => ids.includes(note.id) ? [{ note, index }] : [])
    if (!items.length) return
    const commit = (succeeded: string[]) => {
      if (!succeeded.length) { setError('删除失败，条目仍保留，请重试。'); return }
      const chat = currentConversation.current, staged = (chat.stagedComments || []).filter(item => succeeded.includes(item.noteId))
      const next = currentNotes.current.filter(note => !succeeded.includes(note.id))
      if (!persist(next, { ...chat, contextId: succeeded.includes(chat.contextId || '') ? null : chat.contextId, stagedComments: chat.stagedComments?.filter(item => !succeeded.includes(item.noteId)) }, true)) return
      setDeleted(previous => ({ items: [...(previous?.items || []), ...items.filter(item => succeeded.includes(item.note.id))], staged: [...(previous?.staged || []), ...staged] }))
      if (succeeded.includes(selectedId)) { setSelectedId(next.find(note => note.category === category)?.id || ''); setEditing(false) }
      setError(succeeded.length === ids.length ? '' : '部分删除失败，失败条目仍保留，可重试。')
    }
    if (onDeleteNotes) {
      mutationPending.current = true; setMutationBusy(true)
      void (async () => {
        try {
          for (const id of ids) { if (!await runSave(id)) { setError('内容尚未保存，已暂停删除。'); return } }
          const result = await onDeleteNotes(ids)
          commit(result === true ? ids : Array.isArray(result) ? result.filter(id => ids.includes(id)) : [])
        } catch { setError('删除失败，条目仍保留，请重试。') }
        finally { mutationPending.current = false; setMutationBusy(false) }
      })()
    } else if (preview) commit(ids)
    else setError('删除功能尚未接入。')
  }
  function undoDelete() {
    if (!deleted || readOnly || mutationPending.current) return
    const commit = (succeeded: string[]) => {
      if (!succeeded.length) { setError('恢复失败，已保留撤销操作，请重试。'); return }
      const next = [...currentNotes.current]
      deleted.items.filter(item => succeeded.includes(item.note.id)).forEach(({ note, index }) => { if (!next.some(item => item.id === note.id)) next.splice(Math.min(index, next.length), 0, note) })
      if (persist(next, { ...currentConversation.current, stagedComments: [...(currentConversation.current.stagedComments || []), ...deleted.staged.filter(item => succeeded.includes(item.noteId))] }, true)) {
        const remaining = deleted.items.filter(item => !succeeded.includes(item.note.id))
        setDeleted(remaining.length ? { items: remaining, staged: deleted.staged.filter(item => !succeeded.includes(item.noteId)) } : null)
        setError(remaining.length ? '部分恢复失败，可再次撤销。' : '')
      }
    }
    const ids = deleted.items.map(item => item.note.id)
    if (onRestoreNotes) {
      mutationPending.current = true; setMutationBusy(true)
      void onRestoreNotes(ids).then(result => commit(result === true ? ids : Array.isArray(result) ? result.filter(id => ids.includes(id)) : []))
        .catch(() => setError('恢复失败，已保留撤销操作，请重试。'))
        .finally(() => { mutationPending.current = false; setMutationBusy(false) })
    } else if (preview) commit(ids)
    else setError('恢复失败，服务端恢复功能尚未接入。')
  }
  function create(title?: string) {
    if (readOnly) return
    if (editing && !titleCommit()) return
    const current = currentNotes.current
    let name = title || `未命名${categoryNames[category]}`, suffix = 2
    while (current.some(note => note.title === name)) name = `${title || `未命名${categoryNames[category]}`} ${suffix++}`
    const tempId = crypto.randomUUID()
    const note: SettingNote = { id: tempId, category, title: name, body: '' }
    if (onCreateNote) {
      void onCreateNote(category, name, tempId).then(createdNote => {
        if (createdNote) {
          const updated = currentNotes.current.map(n => n.id === tempId ? { ...n, id: createdNote.id, documentId: createdNote.id, versionId: createdNote.versionId } : n)
          currentNotes.current = updated
          setNotes(updated)
          setSelectedId((prev: string) => prev === tempId ? createdNote.id : prev)
          if (selectedId === tempId) {
            rememberView({ id: createdNote.id, category, mode: 'note' })
          }
        }
      })
    }
    if (!persist([...current, note])) return
    rememberView({ id: note.id, category, mode: 'note' })
    setSelectedId(note.id); setMode('note'); setEditing(true); setDraftTitle(name); setQuery('')
  }
  function openTitle(title: string) {
    const target = resolveNote(notes, title)
    if (target) { choose(target); setQuery('') }
    else if (notes.some(note => note.title.trim() === title.trim())) setError('存在同名条目，请先区分标题后再打开引用。')
    else create(title)
  }
  function titleCommit() {
    if (!active || readOnly) return true
    const nextTitle = draftTitle.trim()
    if (!nextTitle || /[\]\n[]/.test(nextTitle)) { setError('请输入标题，标题不能含方括号或换行。'); return false }
    if (notes.some(note => note.id !== active.id && note.title === nextTitle)) { setError('已有同名条目，请使用不同的标题。'); return false }
    if (onRenameNote && (active.title !== nextTitle || titleWrites.current.get(active.id)?.success === false)) {
      enqueueTitle(active.id, nextTitle)
    }
    // Keep existing wiki references valid when the referenced note is renamed.
    return persist(notes.map(note => {
      const body = note.body.replace(/\[\[([^\]\n[]+)\]\]/g, (match, title: string) => title.trim() === active.title.trim() ? `[[${nextTitle}]]` : match)
      return { ...note, title: note.id === active.id ? nextTitle : note.title, body, ...(note.comments ? { comments: reanchorComments(note.comments, note.body, body) } : {}) }
    }))
  }
  const restoreView = useEffectEvent(() => {
    const view = location.state?.settingView as SettingView | undefined
    if (!view || (view.id === selectedId && view.category === category && view.mode === mode)) return
    if ((editing && !titleCommit()) || (!editing && !readOnly && !persist(currentNotes.current))) {
      navigate(location, { replace: true, state: { ...location.state, settingView: { id: selectedId, category, mode } } })
      return
    }
    const note = currentNotes.current.find(note => note.id === view.id)
    setSelectedId(note?.id || currentNotes.current.find(note => note.category === view.category)?.id || '')
    setCategory(view.category); setMode(view.mode); setEditing(false)
    if (view.mode === 'chat') setChatVisited(true)
  })
  useEffect(() => { restoreView() }, [location.key])

  function changeCategory(next: SettingCategory) {
    if (editing && !titleCommit()) return
    rememberView({ id: mode === 'note' ? notes.find(note => note.category === next)?.id || '' : selectedId, category: next, mode })
    setCategory(next); setQuery(''); setEditing(false)
    if (mode === 'note') setSelectedId(notes.find(note => note.category === next)?.id || '')
  }
  function showMode(next: 'graph' | 'search' | 'chat') {
    if (editing && !titleCommit()) return
    if (!editing && !readOnly && !persist(currentNotes.current)) return
    if (next === 'search' && mode !== 'search') setSearchReturnMode(mode)
    rememberView({ id: selectedId, category, mode: next })
    setMode(next); setEditing(false)
    if (next === 'chat') setChatVisited(true)
  }
  function patchConversation(patch: Partial<Conversation>) {
    persist(currentNotes.current, { ...currentConversation.current, ...patch })
  }
  function sendMessage() {
    const chat = currentConversation.current, text = chat.draft.trim()
    const staged = stagedSettingComments(currentNotes.current, chat)
    if (readOnly || (!text && !staged.length) || staged.some(({ comment }) => !comment.text.trim())) return
    const context = currentNotes.current.find(note => note.id === chat.contextId)
    const message = [text ? context ? `关于「${context.title}」\n${text}` : text : '', ...staged.map(({ note, comment }) => `关于「${note.title}」的评论\n引用${comment.start === comment.end ? '（原文已删除）' : ''}：${comment.quote}\n修改意见：${comment.text}`)].filter(Boolean).join('\n\n')
    persist(currentNotes.current.map(note => ({ ...note, ...(note.comments ? { comments: note.comments.map(comment => staged.some(item => item.note.id === note.id && item.comment.id === comment.id) ? { ...comment, submitted: true } : comment) } : {}) })),
      { ...chat, draft: '', stagedComments: [], messages: [...chat.messages, { id: crypto.randomUUID(), role: 'user', text: message, changes: [] }] }, true)
  }
  function changeProposal(id: string, patch: Partial<SettingChange>) {
    patchConversation({ messages: currentConversation.current.messages.map(message => ({ ...message, changes: message.changes.map(change => change.id === id && change.status === 'pending' ? { ...change, ...patch } : change) })) })
  }
  async function acceptProposals(ids: string[]) {
    if (readOnly) return
    const chat = currentConversation.current
    const changes = chat.messages.flatMap(message => message.changes).filter(change => ids.includes(change.id) && change.status === 'pending')
    if (!changes.length) return

    if (onApplyProposal && collection) {
      let workingNotes = [...currentNotes.current]
      const acceptedIds: string[] = []
      const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

      for (const change of changes) {
        const proposalId = uuidRegex.test(change.id) ? change.id : crypto.randomUUID()
        const targetDocId = (change.noteId && uuidRegex.test(change.noteId)) ? change.noteId : null
        const baseVerId = (change.baseVersionId && uuidRegex.test(change.baseVersionId)) ? change.baseVersionId : null
        const proposal: SettingChangeProposal = {
          id: proposalId,
          setting_collection_id: collection.id,
          target_document_id: targetDocId,
          base_version_id: baseVerId,
          title: change.title,
          category: change.category,
          proposed_content: change.body,
          reason: change.reason?.trim() || '设定变更提案',
          source_task: change.sourceTask ? {
            workflow_run_id: change.sourceTask.workflowRunId,
            chapter_id: change.sourceTask.chapterId,
            agent_role: change.sourceTask.agentRole,
            novel_title: change.sourceTask.novelTitle,
          } : undefined,
          status: 'pending',
        }
        const res = await onApplyProposal(proposal)
        if (res === 'conflict') {
          setError(`「${change.title}」已发生冲突，请重新获取最新版本或人工合并。`)
          continue
        } else if (res && typeof res === 'object' && res.versionId) {
          const docId = res.documentId || change.noteId
          const existing = workingNotes.find(n => n.id === docId || n.id === change.noteId)
          if (existing) {
            workingNotes = workingNotes.map(n => (n.id === existing.id) ? { ...n, id: docId, documentId: docId, body: change.body, versionId: res.versionId } : n)
          } else {
            workingNotes.push({
              id: docId,
              documentId: docId,
              title: change.title,
              body: change.body,
              category: change.category,
              versionId: res.versionId,
              comments: [],
            })
          }
          acceptedIds.push(change.id)
        } else {
          setError(`「${change.title}」应用提案失败，请稍后重试。`)
        }
      }

      if (acceptedIds.length > 0) {
        persist(workingNotes, {
          ...chat,
          messages: chat.messages.map(message => ({
            ...message,
            changes: message.changes.map(change => acceptedIds.includes(change.id) ? { ...change, status: 'accepted' } : change),
          })),
        })
      }
      return
    }

    if (!onSaveNoteContent) {
      try {
        const next = applySettingChanges(currentNotes.current, changes)
        persist(next, { ...chat, messages: chat.messages.map(message => ({ ...message, changes: message.changes.map(change => ids.includes(change.id) && change.status === 'pending' ? { ...change, status: 'accepted' } : change) })) })
      } catch (cause) { setError(cause instanceof Error ? cause.message : '提案未能应用，请重试。') }
      return
    }

    let workingNotes = [...currentNotes.current]
    const acceptedIds: string[] = []

    for (const change of changes) {
      const existing = workingNotes.find(n => n.id === change.noteId)
      if (existing || change.before) {
        const targetId = existing?.id || change.before?.id || change.noteId
        const expectedVersion = change.baseVersionId || change.before?.versionId || existing?.versionId
        const res = await onSaveNoteContent(targetId, change.body, expectedVersion)
        if (res === 'conflict') {
          setError(`「${change.title}」已发生变化，请重新生成或人工合并。`)
          continue
        } else if (res && typeof res === 'object' && res.versionId) {
          workingNotes = workingNotes.map(n => n.id === targetId ? { ...n, body: change.body, versionId: res.versionId } : n)
          acceptedIds.push(change.id)
        } else {
          setError(`「${change.title}」保存失败，请稍后重试。`)
        }
      } else {
        if (onCreateNote) {
          const created = await onCreateNote(change.category, change.title, change.noteId)
          if (created) {
            let finalVersionId = created.versionId
            let saveFailed = false
            if (change.body.trim()) {
              const saveRes = await onSaveNoteContent(created.id, change.body, created.versionId)
              if (saveRes && typeof saveRes === 'object' && saveRes.versionId) {
                finalVersionId = saveRes.versionId
              } else {
                saveFailed = true
                if (saveRes === 'conflict') {
                  setError(`「${change.title}」保存正文时发生版本冲突。`)
                } else {
                  setError(`「${change.title}」正文保存失败。`)
                }
              }
            }
            if (!saveFailed) {
              workingNotes = workingNotes.map(n => n.id === created.id ? { ...n, body: change.body, versionId: finalVersionId } : n)
              acceptedIds.push(change.id)
            }
          } else {
            setError(`创建「${change.title}」失败，请稍后重试。`)
          }
        }
      }
    }

    if (acceptedIds.length > 0) {
      persist(workingNotes, {
        ...chat,
        messages: chat.messages.map(message => ({
          ...message,
          changes: message.changes.map(change => acceptedIds.includes(change.id) ? { ...change, status: 'accepted' } : change),
        })),
      })
    }
  }
  function discussNote() {
    if (!active || (editing && !titleCommit())) return
    if (!persist(currentNotes.current, { ...currentConversation.current, contextId: active.id })) return
    setMode('chat'); setEditing(false); setChatVisited(true)
  }
  function updateNoteComments(comments: OutlineComment[], body?: string) {
    if (!active || readOnly) return
    if (body !== undefined && body !== active.body) comments = reanchorFeedbackComments(active.comments || [], active.body, body)
    persist(currentNotes.current.map(note => note.id === active.id ? { ...note, body: body ?? note.body, comments } : note),
      { ...currentConversation.current, stagedComments: currentConversation.current.stagedComments?.filter(item => item.noteId !== active.id || comments.some(comment => comment.id === item.commentId)) })
  }
  function stageNoteComments(panel?: HTMLElement) {
    if (!active || readOnly || transfer.current || (editing && !titleCommit())) return
    const note = currentNotes.current.find(note => note.id === active.id)!
    const pending = note.comments?.filter(comment => !comment.submitted && !stagedIds.includes(comment.id)) || []
    if (!pending.length || pending.some(comment => !comment.text.trim())) return
    const chat = currentConversation.current
    const commit = () => {
      if (!persist(currentNotes.current, { ...chat, contextId: note.id, stagedComments: [...(chat.stagedComments || []), ...pending.map(comment => ({ noteId: note.id, commentId: comment.id }))] }, true)) return
      setMode('chat'); setEditing(false); setChatVisited(true)
    }
    if (!panel || !document.startViewTransition || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) { commit(); return }
    panel.style.viewTransitionName = 'setting-comment-transfer'
    document.documentElement.setAttribute('data-setting-transfer', '')
    const motion = document.startViewTransition(() => flushSync(commit))
    transfer.current = motion
    void motion.finished.catch(() => {}).then(() => {
      if (transfer.current !== motion) return
      panel.style.removeProperty('view-transition-name')
      document.documentElement.removeAttribute('data-setting-transfer'); transfer.current = null
    })
  }

  const commentDots = useCommentDrag(!readOnly && mode === 'note' && submitted.length > 0, ids => updateNoteComments([
    ...(active?.comments || []).filter(comment => !submitted.some(item => item.id === comment.id)), ...ids.map(id => submitted.find(comment => comment.id === id)!),
  ]))
  useImperativeHandle(ref, () => ({ flush: async () => {
    if (editing && !titleCommit()) return false
    if (readOnly) return true
    for (const [id, write] of titleWrites.current) if (write.success === false) enqueueTitle(id, write.title)
    if (!(await Promise.all([...titleWrites.current.values()].map(write => write.promise))).every(Boolean)) return false
    if (onSaveNoteContent) {
      const ids = Array.from(saveQueues.current.keys())
      const results = await Promise.all(
        ids.map(async (id) => {
          const q = saveQueues.current.get(id)
          if (q?.timer) {
            clearTimeout(q.timer)
            q.timer = null
          }
          return runSave(id)
        })
      )
      return results.every(Boolean)
    }
    return persist(currentNotes.current)
  } }))

  const sidebarStatus = saveStatusText ?? (collection?.status === 'archived' ? '已归档 · 只读' : preview ? '交互预览' : '')
  return <div className="studio-setting" aria-label="作品设定">
    <div className="setting-sidebar" onPointerEnter={() => { if (!hidden) setSidebarOpen(true) }} onPointerLeave={() => setSidebarOpen(false)} onFocus={() => { if (!hidden) setSidebarOpen(true) }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setSidebarOpen(false) }} onKeyDown={event => { if (event.key === 'Escape' && !pinned) { event.currentTarget.querySelector<HTMLButtonElement>('.setting-sidebar-trigger')?.focus(); setSidebarOpen(false) } }}>
    <button className="setting-sidebar-trigger" aria-label="展开设定侧边栏" aria-expanded={sidebarVisible} aria-controls="setting-library" disabled={hidden && !pinned} onClick={() => setSidebarOpen(true)}><span /></button>
    <aside id="setting-library" className={`setting-library${sidebarVisible ? ' is-open' : ''}`} aria-label="设定侧边栏" inert={!sidebarVisible}>
      <div className="setting-library-top">
        <div className="setting-categories" role="group" aria-label="设定分类" data-category={category}>
          <span aria-hidden="true" />{(['setting', 'world'] as const).map(value => <button key={value} aria-pressed={category === value} onClick={() => changeCategory(value)}>{categoryNames[value]}</button>)}
        </div>
        <button className="studio-icon" aria-label={pinned ? '取消固定设定侧边栏' : '固定设定侧边栏'} aria-pressed={pinned} onClick={() => setPinned(!pinned)}><img src={`/ui/studio/${pinned ? 'pin' : 'unpin'}.svg`} alt="" /></button>
      </div>
      <div className="setting-library-actions">
        <div className="setting-new-row"><button className="setting-new" aria-pressed={mode === 'chat'} onClick={() => { setSweep(value => value + 1); showMode('chat') }} disabled={readOnly}>{sweep > 0 && <span key={sweep} className="setting-action-sweep" aria-hidden="true" />}<img src="/ui/setting/append.svg" alt="" />新建内容</button><button className="setting-manual-new" aria-label="手动新建条目" title="手动新建条目" disabled={readOnly} onClick={() => create()}><img src="/ui/studio/add.svg" alt="" /></button></div>
        <button className={mode === 'graph' ? 'is-active' : ''} aria-pressed={mode === 'graph'} onClick={() => showMode('graph')}><img src="/ui/setting/commit.svg" alt="" />关系图谱</button>
        <button className={mode === 'search' ? 'is-active' : ''} aria-pressed={mode === 'search'} onClick={() => showMode('search')}><img src="/ui/setting/search.svg" alt="" />搜索内容</button>
      </div>
      <SettingNoteList notes={shown} category={category} activeId={displayMode === 'note' ? active?.id : undefined} disabled={readOnly || !activePage} onOpen={choose} onOrder={orderNotes} onDelete={deleteNotes} />
      {mutationBusy && <p role="status">正在同步删除或恢复…</p>}
      {deleted && <div className="setting-delete-undo" role="status"><small>已删除 {deleted.items.length} 项，正文引用保留</small><button type="button" disabled={mutationBusy} onClick={undoDelete}>撤销删除</button></div>}
      {sidebarStatus && <small className="setting-local-note" role={saveStatusText ? 'status' : undefined}>{sidebarStatus}</small>}
      {libraryFooter && <div className="setting-library-footer">{libraryFooter}</div>}
    </aside>
    </div>
    {mode === 'search' && activePage && !hidden && <SearchDialog title="搜索内容" query={query} onQuery={setQuery}
      inputType="text" inputLabel="搜索设定内容" placeholder="搜索设定与世界观中的标题、正文…"
      items={results.map(note => {
        const body = note.body.replace(/\[\[|\]\]/g, ''), start = Math.max(0, body.toLocaleLowerCase().indexOf(query.trim().toLocaleLowerCase()) - 30)
        return { ...note, description: `${start > 0 ? '…' : ''}${body.slice(start, start + 140)}${body.length > start + 140 ? '…' : ''}`, kind: categoryNames[note.category], icon: '/ui/setting/append.svg' }
      })}
      onClose={() => { rememberView({ id: selectedId, category, mode: searchReturnMode }); setMode(searchReturnMode) }} onSelect={choose}
      emptyText="没有找到相关条目"
      renderPreview={note => note ? <><img className="search-preview-icon" src="/ui/setting/append.svg" alt="" /><h3>{note.title}</h3><span className="search-preview-kind">{categoryNames[note.category]}</span><p>{note.body.replace(/\[\[|\]\]/g, '') || '暂无正文'}</p></> : <><h3>寻找世界的细节</h3><p>搜索设定与世界观中的标题和正文。</p></>} />}
    {displayMode !== 'chat' && displayMode !== 'graph' && active && <div className="setting-editor-tools"><button disabled={readOnly} onClick={discussNote}>交给 Agent 讨论</button><button disabled={readOnly} onClick={() => { if (editing && !titleCommit()) return; if (!editing) setDraftTitle(active.title); setEditing(!editing) }}>{editing ? '阅读' : '编辑'}</button></div>}
    <div className={`setting-main${displayMode === 'chat' ? ' is-conversation' : chatVisited ? ' has-conversation-return' : ''}`}>
      {chatVisited && displayMode !== 'chat' && <button className="setting-return-chat" onClick={() => showMode('chat')}>返回设定对话</button>}
      <SettingConversation conversation={conversation} notes={notes} visible={displayMode === 'chat' && activePage} disabled={readOnly} referencingProjects={referencingProjects} preview={preview} onPatch={patchConversation} onSend={sendMessage} onCommentChange={(noteId, commentId, text) => {
        if (!readOnly) persist(currentNotes.current.map(note => note.id === noteId ? { ...note, comments: note.comments?.map(comment => comment.id === commentId ? { ...comment, text } : comment) } : note))
      }} onExample={() => patchConversation({ messages: [...currentConversation.current.messages, settingExampleReply(currentNotes.current, currentConversation.current)] })} onAccept={acceptProposals} onChange={changeProposal} onOpen={choose} onScroll={scrollTop => { currentConversation.current = { ...currentConversation.current, scrollTop } }} />
      {displayMode === 'chat' ? null : displayMode === 'graph' ? <SettingGraph notes={notes} onOpen={openTitle} /> : active ? <>
        <div className="setting-document-scroll">
          <MotionFrame className="setting-document" textKey={active.id}>
            <h1 data-sweep-text>{editing ? <input aria-label="设定标题" maxLength={100} value={draftTitle} onChange={event => setDraftTitle(event.target.value)} onBlur={titleCommit} /> : active.title}</h1>
            {!!submitted.length && <div className="setting-note-comments" data-comment-frame><small>{stagedIds.length ? '评论暂存中' : '已加入对话'}</small><div ref={commentDots} className="studio-comment-dots studio-submitted-dots" role="group" aria-label="条目已发送评论">
              {submitted.map(comment => <button key={comment.id} type="button" data-comment-id={comment.id} disabled={readOnly} style={{ '--comment-color': comment.color } as CSSProperties} aria-label={`查看条目评论讨论：${comment.quote}`} title={`${comment.quote} · ${comment.text}`} aria-description="点击进入设定对话；拖动排序，拖出此栏删除；Alt 加左右方向键排序，Delete 移除" onClick={discussNote} />)}
            </div></div>}
            <div className={`studio-prose setting-note-prose${editing ? '' : ' is-reading'}`} key={active.id}><OutlineEditor outline={active.body} comments={active.comments || []} readOnly={readOnly}
              draftInput={{ ref: noteInput, onTyping, ready: activePage && ready, visible: activePage, label: '设定正文', className: 'setting-note-editor', maxLength: 30000, sendLabel: '暂存评论并继续讨论', sendEffect: 'handoff', stagedIds, onSubmittedSelect: discussNote, linkOptions: notes.filter(note => note.id !== active.id).map(note => note.title), reading: !editing ? <div className="setting-note-body" data-sweep-text><LinkedText text={active.body || '还没有正文，点击编辑开始书写。'} notes={notes} references={noteLinks(active.body)} comments={active.comments || []} onOpen={openTitle} /></div> : undefined }}
              onChange={(body, comments) => { updateNoteComments(comments, body); onTyping() }} onCommentsChange={updateNoteComments} onSend={stageNoteComments} />
            </div>
          </MotionFrame>
          {!!references.length && <section className="setting-backlinks" aria-label="引用条目"><h2>引用条目 <span>{references.length}</span></h2>{references.map(title => {
            const note = resolveNote(notes, title)
            return <button key={title} onClick={() => openTitle(title)}>{title}<small>{note ? categoryNames[note.category] : '未解析'}</small></button>
          })}</section>}
        </div>
      </> : <div className="setting-empty"><h1>{categoryNames[category]}</h1><p>{preview ? '从一段对话开始，也可以直接新建条目。' : readOnly ? '暂无可查看的设定。' : '从左侧新建条目，开始完善设定。'}</p>{preview && <button disabled={readOnly} onClick={() => showMode('chat')}>新建内容</button>}</div>}
      {error && <p className="setting-error" role="alert">{error}</p>}
    </div>
  </div>
}
