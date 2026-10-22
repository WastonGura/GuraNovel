import { useEffect, useEffectEvent, useImperativeHandle, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode, type Ref } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { flushSync } from 'react-dom'
import { TextMotionSurface } from './StudioMotion'
import OutlineEditor from './OutlineEditor'
import type { OutlineInputHandle } from './ReferenceTextInput'
import { useCommentDrag } from './useCommentDrag'
import { reanchorComments, reanchorFeedbackComments, restoreOutlineComments, type OutlineComment } from './studioPreview'
import SettingGraph from './SettingGraph'
import SearchDialog from './SearchDialog'
import SettingConversation from './SettingConversation'
import SettingNoteList from './SettingNoteList'
import SettingTrashDialog, { type SettingTrashEntry } from './SettingTrashDialog'
import SettingUndoToast from './SettingUndoToast'
import SettingCollaborationPanel from './SettingCollaborationPanel'
import SettingActionIcon from './SettingActionIcon'
import type { SettingCollaborationAcceptance } from './api/settingCollaborationClient'
import { readCreationDrafts, writeCreationDrafts, type CreationDraft } from './settingDrafts'
import { applySettingChanges, categoryNames, categoryFromDocument, emptySettingConversation, noteLinks, readSettingWorkspace, resolveNote, settingExampleReply, settingStorageKey, stagedSettingComments, type SettingCategory, type SettingChange, type SettingConversation as Conversation, type SettingNote } from './settingNotes'
import type { SettingCollection, SettingChangeProposal } from './api/client'
import './studioSetting.css'

export type SettingView = { id: string; category: SettingCategory; mode: 'note' | 'graph' | 'search' | 'chat' }

export interface SettingRestorePointStatus {
  documentId: string
  versionId: string
  title: string
  body: string
  commentsKey: string
}

export interface StudioSettingHandle {
  flush: () => Promise<boolean>
  getNoteSnapshot?: (documentId: string) => SettingNote | undefined
  replaceSavedNote?: (documentId: string, body: string, versionId: string, comments: OutlineComment[], expected?: Pick<SettingNote, 'body' | 'comments'>) => void
  openCollaboration?: () => void
}

export interface StudioSettingProps {
  preview?: boolean
  hidden?: boolean
  activePage?: boolean
  ready?: boolean
  onTyping?: () => void
  ref?: Ref<StudioSettingHandle>
  collection?: SettingCollection | null
  backendNotes?: SettingNote[]
  readOnly?: boolean
  referencingProjects?: { id: string; title: string }[]
  onSaveNoteContent?: (noteId: string, content: string, expectedVersionId?: string, comments?: OutlineComment[]) => Promise<{ versionId: string } | 'conflict' | false>
  onApplyProposal?: (proposal: SettingChangeProposal) => Promise<{ versionId: string; documentId: string } | 'conflict' | false>
  onRenameNote?: (noteId: string, newTitle: string) => Promise<boolean>
  onCreateNote?: (category: SettingCategory, title: string, tempId: string, body?: string, comments?: OutlineComment[]) => Promise<SettingNote | null>
  onDeleteNotes?: (noteIds: string[]) => Promise<boolean | string[]>
  onRestoreNotes?: (noteIds: string[]) => Promise<boolean | string[]>
  onLoadTrash?: () => Promise<SettingTrashEntry[]>
  onCollaborationApplied?: (result: SettingCollaborationAcceptance, stableId: string) => void
  onViewChange?: (view: SettingView) => void
  onAssistantContext?: (context: { documentId?: string; versionId?: string }) => void
  onOpenHistory?: (note: SettingNote) => void
  onCreateRestorePoint?: (note: SettingNote) => Promise<boolean>
  restorePointStatus?: SettingRestorePointStatus | null
  restorePointBusy?: boolean
  collaborationInMenu?: boolean
  initialView?: Partial<SettingView>
  initialConversation?: Conversation
  syncUrlParams?: boolean
  saveStatusText?: string
  defaultPinned?: boolean
  libraryFooter?: ReactNode
}

function LinkedText({ text, notes, comments, onOpen }: { text: string; notes: SettingNote[]; comments: OutlineComment[]; onOpen: (title: string) => void }) {
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
    parts.push(title ? <button key={`link-${match.index}`} className={`setting-inline-link${target ? '' : ' is-missing'}`} aria-label={title} title={target ? `${title}：${target.body.replace(/\[\[|\]\]/g, '').slice(0, 100)}` : `创建引用条目：${title}`} onClick={event => { if (window.getSelection()?.isCollapsed !== false && !(event.target as HTMLElement).closest('[data-reading-comment]')) onOpen(title) }}>{source(title, match.index + 2 + match[1].indexOf(title))}</button> : match[0])
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
  onLoadTrash,
  onCollaborationApplied,
  onViewChange,
  onAssistantContext,
  onOpenHistory,
  onCreateRestorePoint,
  restorePointStatus,
  restorePointBusy = false,
  collaborationInMenu = false,
  initialView,
  initialConversation,
  syncUrlParams = false,
  saveStatusText,
  defaultPinned,
  libraryFooter,
}: StudioSettingProps) {
  const directEditing = !preview
  const draftScope = collection?.id || 'studio'
  const [creationDrafts] = useState(() => readCreationDrafts(draftScope))
  const [pendingCreations, setPendingCreations] = useState(creationDrafts)
  const creationRequests = useRef(new Map(creationDrafts.flatMap(draft => draft.request ? [[draft.note.id, draft.request] as const] : [])))
  const creationJournal = useRef<CreationDraft[]>(creationDrafts)
  function journalCreation(note: SettingNote, request?: SettingNote) {
    const entry = { note, request: request || creationRequests.current.get(note.id) }
    const next = [...creationJournal.current.filter(item => item.note.id !== note.id), entry]
    try { writeCreationDrafts(draftScope, next); creationJournal.current = next; setPendingCreations(next) }
    catch { setError('草稿备份失败，请保持页面打开并复制内容。') }
  }
  function clearCreation(id: string) {
    creationJournal.current = creationJournal.current.filter(item => item.note.id !== id)
    setPendingCreations(creationJournal.current)
    writeCreationDrafts(draftScope, creationJournal.current)
    creationRequests.current.delete(id)
  }
  const location = useLocation(), navigate = useNavigate()
  const searchParams = new URLSearchParams(location.search)
  const initialCategoryParam = searchParams.get('category') as SettingCategory | null
  const initialNoteParam = searchParams.get('note')
  const initialModeParam = searchParams.get('mode') as SettingView['mode'] | null

  const [initial] = useState(() => {
    if (backendNotes) {
      const restored = backendNotes.filter(n => !creationDrafts.some(draft => draft.note.id === n.id || draft.note.id === n.creationRequestId || draft.note.documentId === n.id || draft.note.documentId && draft.note.documentId === n.documentId)).map(n => {
        try {
          const recoveredTitle = sessionStorage.getItem(`guranovel:setting-title-recovery:${n.documentId || n.id}`)
          if (recoveredTitle !== null) n = { ...n, title: recoveredTitle }
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
      return { notes: [...restored, ...creationDrafts.map(draft => draft.note)], conversation: initialConversation || emptySettingConversation(), error: '' }
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
  const [selectedId, setSelectedId] = useState(() => {
    const requested = initialNoteParam || location.state?.settingView?.id || initialView?.id
    return initial.notes.find(note => note.id === requested || note.documentId === requested || note.creationRequestId === requested)?.id
      || initial.notes.find(note => note.category === (initialCategoryParam || initialView?.category || 'setting'))?.id || initial.notes[0]?.id || ''
  })
  const [mode, setMode] = useState<SettingView['mode']>(
    !preview && initialModeParam === 'chat' ? 'note' : initialModeParam || location.state?.settingView?.mode || initialView?.mode || 'note'
  )
  const [manualEditing, setEditing] = useState(false)
  const editing = directEditing || manualEditing
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
      else if (titleWrites.current.get(id) === write) {
        if (sessionStorage.getItem(`guranovel:setting-title-recovery:${id}`)?.trim() === title) sessionStorage.removeItem(`guranovel:setting-title-recovery:${id}`)
        setError(value => value === '标题尚未保存，请保持页面打开并重试。' ? '' : value)
      }
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
  const [trashOpen, setTrashOpen] = useState(false)
  const [undoOperation, setUndoOperation] = useState<{ id: string; ids: string[] } | null>(null)
  const [collaborationOpen, setCollaborationOpen] = useState(false)
  const previousCollaborationOpen = useRef(false)
  const [sentFeedback, setSentFeedback] = useState<Record<string, string>>(() => {
    try {
      const value: unknown = JSON.parse(sessionStorage.getItem(`guranovel:setting-feedback-sent:${draftScope}`) || '{}')
      return value && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === 'string')) : {}
    }
    catch { return {} }
  })
  const mutationPending = useRef(false)
  const [mutationBusy, setMutationBusy] = useState(false)
  const noteInput = useRef<OutlineInputHandle>(null)
  const documentScroll = useRef<HTMLDivElement>(null)
  const editorBookmarks = useRef(new Map<string, { start: number; end: number; scrollTop: number }>())
  const titleInput = useRef<HTMLInputElement>(null)
  const titleTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>())

  const [prevBackendNotes, setPrevBackendNotes] = useState(backendNotes)
  if (backendNotes && backendNotes !== prevBackendNotes) {
    setPrevBackendNotes(backendNotes)
    const restored = backendNotes.filter(n => !pendingCreations.some(draft => draft.note.id === n.id || draft.note.id === n.creationRequestId || draft.note.documentId === n.id || draft.note.documentId && draft.note.documentId === n.documentId)).map(n => {
      const local = notes.find(item => item.id === n.id)
      if (local && sessionStorage.getItem(`guranovel:setting-draft-recovery:${n.id}`) !== null) return local
      const titleRecovery = sessionStorage.getItem(`guranovel:setting-title-recovery:${n.documentId || n.id}`)
      if (titleRecovery !== null) n = { ...n, title: titleRecovery }
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
    const merged = [...restored, ...pendingCreations.map(draft => draft.note)]
    setNotes(merged)
    if (merged.length > 0) {
      if (!selectedId || !merged.some(n => n.id === selectedId)) {
        const initialTarget = initialNoteParam && merged.find(n => n.id === initialNoteParam)
        if (initialTarget) {
          setSelectedId(initialTarget.id)
        } else {
          const cat = initialCategoryParam || category
          const found = merged.find(n => n.category === cat) || merged[0]
          setSelectedId(found.id)
        }
      }
    }
  }

  useEffect(() => {
    currentNotes.current = notes
  }, [notes])

  const active = notes.find(note => note.id === selectedId)
  const activePointSaved = Boolean(active && restorePointStatus && active.documentId === restorePointStatus.documentId
    && active.versionId === restorePointStatus.versionId && active.title === restorePointStatus.title && active.body === restorePointStatus.body
    && JSON.stringify(active.comments || []) === restorePointStatus.commentsKey)
  useLayoutEffect(() => {
    const returning = previousCollaborationOpen.current && !collaborationOpen
    previousCollaborationOpen.current = collaborationOpen
    if (returning) noteInput.current?.focus({ preventScroll: true })
  }, [collaborationOpen])
  useLayoutEffect(() => {
    const input = noteInput.current, scroll = documentScroll.current, id = active?.id
    if (!input || !scroll || !id || displayMode !== 'note') return
    const saved = editorBookmarks.current.get(id)
    if (saved) { input.setSelectionRange(saved.start, saved.end); scroll.scrollTop = saved.scrollTop }
    else scroll.scrollTop = 0
    const bookmarks = editorBookmarks.current
    return () => { bookmarks.set(id, { start: input.selectionStart, end: input.selectionEnd, scrollTop: scroll.scrollTop }) }
  }, [active?.id, displayMode])
  const canonicalizeNote = useEffectEvent(() => {
    if (!syncUrlParams || !active?.documentId) return
    const params = new URLSearchParams(location.search)
    if (params.get('note') === active.documentId) return
    params.set('note', active.documentId)
    navigate({ pathname: location.pathname, search: `?${params}` }, { replace: true, state: { ...location.state, settingView: { id: active.id, category, mode } } })
  })
  useEffect(() => { canonicalizeNote() }, [active?.documentId])
  useEffect(() => { onAssistantContext?.({ documentId: active?.documentId, versionId: active?.versionId }) }, [onAssistantContext, active?.documentId, active?.versionId])
  const readOnly = explicitReadOnly !== undefined ? explicitReadOnly : ((!preview && !onSaveNoteContent && !onApplyProposal) || Boolean(initial.error))
  const shown = notes.filter(note => note.category === category)
  const results = notes.filter(note => `${note.title}\n${note.body}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  const sidebarVisible = pinned || (sidebarOpen && !hidden)
  const references = active ? noteLinks(active.body) : []
  const feedbackKey = active?.documentId || active?.id || ''
  const feedbackSignature = (active?.comments || []).filter(comment => !comment.submitted).map(comment => `引用：${comment.quote}\n修改意见：${comment.text}`).join('\n\n')
  const stagedIds = [...(conversation.stagedComments || []).filter(item => item.noteId === active?.id).map(item => item.commentId), ...(feedbackSignature && sentFeedback[feedbackKey] === feedbackSignature ? (active?.comments || []).map(comment => comment.id) : [])]
  const collectionFeedback = notes.flatMap(note => {
    const key = note.documentId || note.id
    const signature = (note.comments || []).filter(comment => !comment.submitted).map(comment => `引用：${comment.quote}\n修改意见：${comment.text}`).join('\n\n')
    return signature && sentFeedback[key] !== signature ? [{ key, signature, text: `条目：${note.title || `未命名${categoryNames[note.category]}`}\n${signature}` }] : []
  })
  const submitted = active?.comments?.filter(comment => comment.submitted || stagedIds.includes(comment.id)) || []

  function rememberView(next: SettingView) {
    const current = { id: selectedId, category, mode }
    if (JSON.stringify(current) === JSON.stringify(next)) return
    if (syncUrlParams) {
      const params = new URLSearchParams(location.search)
      if (next.id) params.set('note', currentNotes.current.find(note => note.id === next.id)?.documentId || next.id); else params.delete('note')
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
  const [initialSavedSnapshots] = useState(() => new Map((backendNotes || initial.notes).map(note => [note.id, JSON.stringify([note.body, note.comments || []])])))
  const savedSnapshots = useRef(initialSavedSnapshots)
  const saveQueues = useRef<Map<string, NoteSaveQueue>>(new Map(initial.notes.map(note => [note.id, { timer: null, inFlight: false, lastSavedBody: initialSavedSnapshots.get(note.id) ?? null, waiters: [] }])))
  useEffect(() => {
    const timers = titleTimers.current
    if (onRenameNote) for (const note of initial.notes) {
      const title = sessionStorage.getItem(`guranovel:setting-title-recovery:${note.documentId || note.id}`)
      if (title?.trim()) enqueueTitle(note.documentId || note.id, title.trim())
    }
    const warn = (event: BeforeUnloadEvent) => {
      if ([...saveQueues.current.values()].some(queue => queue.timer || queue.inFlight) || creationJournal.current.length
        || currentNotes.current.some(note => JSON.stringify([note.body, note.comments || []]) !== savedSnapshots.current.get(note.id))) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', warn)
    return () => { window.removeEventListener('beforeunload', warn); timers.forEach(clearTimeout) }
  // Recovery is loaded once for this editor session.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
      q = { timer: null, inFlight: false, lastSavedBody: savedSnapshots.current.get(id) ?? null, waiters: [] }
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
    if (note.localDraft && !note.title.trim() && !note.body.trim()) return true
    if (note.localDraft && /[\]\n[]/.test(note.title)) { setError('标题不能含方括号或换行，草稿已保留。'); return false }
    const snapshot = JSON.stringify([note.body, note.comments || []])
    if (q.lastSavedBody === snapshot) {
      try {
        sessionStorage.removeItem(`guranovel:setting-draft-recovery:${id}`)
        sessionStorage.removeItem(`guranovel:setting-feedback-recovery:${id}`)
      } catch (_err: unknown) {
        void _err
      }
      if (creationJournal.current.some(item => item.note.id === id) && ![...titleWrites.current.values()].some(write => write.success !== true)) clearCreation(id)
      return true
    }

    q.inFlight = true
    const bodyToSave = note.body
    const expectedVersion = note.versionId
    const finishSuperseded = async () => {
      q!.inFlight = false
      const waiters = q!.waiters.splice(0)
      const saved = await runSave(id)
      waiters.forEach(resolve => resolve(saved))
      return saved
    }

    try {
      if (note.localDraft && onCreateNote) {
        const request = creationRequests.current.get(id) || { ...note, title: note.title.trim() || `未命名${categoryNames[note.category]}` }
        creationRequests.current.set(id, request)
        journalCreation(note, request)
        const created = await onCreateNote(request.category, request.title, id, request.body, request.comments || [])
        if (!created?.versionId) throw new Error('create failed')
        const latest = currentNotes.current.find(n => n.id === id) || note
        const updated = { ...latest, documentId: created.documentId || created.id, versionId: created.versionId, localDraft: false }
        currentNotes.current = currentNotes.current.map(n => n.id === id ? updated : n)
        setNotes(currentNotes.current)
        if (latest.title.trim() && latest.title !== request.title && onRenameNote) enqueueTitle(updated.documentId!, latest.title.trim())
        q.lastSavedBody = JSON.stringify([request.body, request.comments || []])
        savedSnapshots.current.set(id, q.lastSavedBody)
        q.inFlight = false
        journalCreation(updated, request)
        const saved = await runSave(id)
        const waiters = q.waiters.splice(0); waiters.forEach(resolve => resolve(saved))
        return saved
      }
      const documentId = note.documentId || id
      const res = await onSaveNoteContent(documentId, bodyToSave, expectedVersion, note.comments || [])
      // Explicit replacement or Agent adoption retires the old queue. A delayed
      // acknowledgement must not restore its older version or clear new recovery.
      if (saveQueues.current.get(id) !== q) return finishSuperseded()
      if (res === 'conflict') {
        q.inFlight = false
        const w = q.waiters
        q.waiters = []
        w.forEach(resolve => resolve(false))
        return false
      } else if (res && typeof res === 'object' && res.versionId) {
        q.lastSavedBody = snapshot
        savedSnapshots.current.set(id, snapshot)
        const updated = currentNotes.current.map(n => n.id === id ? { ...n, versionId: res.versionId } : n)
        currentNotes.current = updated
        setNotes(updated)
        q.inFlight = false

        const latestNote = currentNotes.current.find(n => n.id === id)
        if (latestNote && JSON.stringify([latestNote.body, latestNote.comments || []]) !== snapshot) {
          sessionStorage.setItem(`guranovel:setting-feedback-recovery:${id}`, JSON.stringify({ body: latestNote.body, comments: latestNote.comments || [], versionId: res.versionId }))
          return runSave(id)
        } else {
          try {
            sessionStorage.removeItem(`guranovel:setting-draft-recovery:${id}`)
            sessionStorage.removeItem(`guranovel:setting-feedback-recovery:${id}`)
          } catch (_err: unknown) {
            void _err
          }
          setError('')
          if (creationJournal.current.some(item => item.note.id === id)) clearCreation(id)
          const w = q.waiters
          q.waiters = []
          w.forEach(resolve => resolve(true))
          return true
        }
      } else {
        setError('内容尚未保存，草稿已保留，请重试。')
        q.inFlight = false
        const w = q.waiters
        q.waiters = []
        w.forEach(resolve => resolve(false))
        return false
      }
    } catch {
      if (saveQueues.current.get(id) !== q) return finishSuperseded()
      setError('内容尚未保存，草稿已保留，请重试。')
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
      q = { timer: null, inFlight: false, lastSavedBody: savedSnapshots.current.get(id) ?? null, waiters: [] }
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
        if (changed.localDraft || creationJournal.current.some(item => item.note.id === changed.id)) {
          if (changed.title.trim() || changed.body.trim()) journalCreation(changed)
        }
        if (!before || before.body === changed.body && before.comments === changed.comments && (!changed.localDraft || before.title === changed.title)) continue
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
      setDeleted({ items: items.filter(item => succeeded.includes(item.note.id)), staged })
      setUndoOperation({ id: crypto.randomUUID(), ids: succeeded })
      if (succeeded.includes(selectedId)) { setSelectedId(next.find(note => note.category === category)?.id || ''); setEditing(false) }
      setError(succeeded.length === ids.length ? '' : '部分删除失败或结果尚未确认，条目仍保留，可重试核对。')
    }
    if (onDeleteNotes) {
      mutationPending.current = true; setMutationBusy(true)
      void (async () => {
        try {
          if (!await flushAll()) { setError('内容尚未保存，已暂停删除。'); return }
          const remoteIds = ids.map(id => currentNotes.current.find(note => note.id === id)?.documentId || id)
          const result = await onDeleteNotes(remoteIds)
          commit(result === true ? ids : Array.isArray(result) ? ids.filter((_, index) => result.includes(remoteIds[index])) : [])
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
  async function restoreEntries(ids: string[]): Promise<string[]> {
    if (readOnly || mutationPending.current) return []
    mutationPending.current = true; setMutationBusy(true)
    try {
      const remoteIds = ids.map(id => deleted?.items.find(item => item.note.id === id)?.note.documentId || id)
      const result = onRestoreNotes ? await onRestoreNotes(remoteIds) : preview ? remoteIds : []
      const succeeded = result === true ? ids : Array.isArray(result) ? ids.filter((_, index) => result.includes(remoteIds[index])) : []
      if (deleted) {
        const next = [...currentNotes.current]
        deleted.items.filter(item => succeeded.includes(item.note.id)).forEach(({ note, index }) => { if (!next.some(item => item.id === note.id || item.documentId && item.documentId === note.documentId)) next.splice(Math.min(index, next.length), 0, note) })
        persist(next, currentConversation.current, true)
      }
      setDeleted(previous => previous ? { ...previous, items: previous.items.filter(item => !succeeded.includes(item.note.id)) } : null)
      return succeeded
    } finally { mutationPending.current = false; setMutationBusy(false) }
  }
  function create(title?: string) {
    if (readOnly) return
    if (editing && !titleCommit()) return
    const current = currentNotes.current
    let name = title || (directEditing ? '' : `未命名${categoryNames[category]}`), suffix = 2
    while (name && current.some(note => note.title === name)) name = `${title || `未命名${categoryNames[category]}`} ${suffix++}`
    const tempId = crypto.randomUUID()
    const note: SettingNote = { id: tempId, category, title: name, body: '', comments: [], localDraft: directEditing && Boolean(onCreateNote) }
    if (onCreateNote && !directEditing) {
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
    if (!persist([...current.filter(item => !(item.localDraft && !item.title.trim() && !item.body.trim())), note])) return
    rememberView({ id: note.id, category, mode: 'note' })
    setSelectedId(note.id); setMode('note'); setEditing(true); setDraftTitle(name); setQuery('')
    if (directEditing) { setCollaborationOpen(false); if (name) { journalCreation(note); enqueueSave(note.id) }; queueMicrotask(() => titleInput.current?.focus()) }
  }
  function openTitle(title: string) {
    const target = resolveNote(notes, title)
    if (target) { choose(target); setQuery('') }
    else if (notes.some(note => note.title.trim() === title.trim())) setError('存在同名条目，请先区分标题后再打开引用。')
    else create(title)
  }
  function titleCommit() {
    if (!active || readOnly) return true
    if (directEditing) return true
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
  function updateDirectTitle(value: string) {
    if (!active || readOnly) return
    const old = active.title
    persist(currentNotes.current.map(note => {
      const body = old.trim() && value.trim() && !/[\]\n[]/.test(value) ? note.body.replace(/\[\[([^\]\n[]+)\]\]/g, (match, title: string) => title.trim() === old.trim() ? `[[${value.trim()}]]` : match) : note.body
      return { ...note, title: note.id === active.id ? value : note.title, body, comments: body === note.body ? note.comments : reanchorComments(note.comments || [], note.body, body) }
    }))
    if (active.localDraft) return
    const previous = titleTimers.current.get(active.id)
    if (previous) clearTimeout(previous)
    const id = active.id, documentId = active.documentId || id
    try { sessionStorage.setItem(`guranovel:setting-title-recovery:${documentId}`, value) }
    catch { setError('标题备份失败，请保持页面打开并复制内容。') }
    titleTimers.current.set(id, setTimeout(() => {
      titleTimers.current.delete(id)
      if (!value.trim() || /[\]\n[]/.test(value)) { setError('请输入标题，标题不能含方括号或换行。'); return }
      if (onRenameNote) enqueueTitle(documentId, value.trim())
    }, 400))
  }
  const restoreView = useEffectEvent(() => {
    const view = location.state?.settingView as SettingView | undefined
    if (!view || (view.id === selectedId && view.category === category && view.mode === mode)) return
    if ((editing && !titleCommit()) || (!editing && !readOnly && !persist(currentNotes.current))) {
      navigate(location, { replace: true, state: { ...location.state, settingView: { id: selectedId, category, mode } } })
      return
    }
    const note = currentNotes.current.find(note => note.id === view.id || note.documentId === view.id || note.creationRequestId === view.id)
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
    if (directEditing) {
      if (displayMode !== 'note') { rememberView({ id: selectedId, category, mode: 'note' }); setMode('note') }
      setCollaborationOpen(true)
      return
    }
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
    if (directEditing) { setCollaborationOpen(true); return }
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
  async function flushAll(): Promise<boolean> {
    if (editing && !titleCommit()) return false
    if (readOnly) return true
    for (const note of currentNotes.current) {
      if (note.localDraft || sessionStorage.getItem(`guranovel:setting-title-recovery:${note.documentId || note.id}`) !== null) {
        if ((note.title.trim() || !note.localDraft) && (!note.title.trim() || /[\]\n[]/.test(note.title) || currentNotes.current.some(other => other.id !== note.id && other.title.trim() === note.title.trim()))) {
          setError('标题不能为空、重复或含方括号与换行，草稿已保留。'); return false
        }
      }
    }
    for (const [id, timer] of titleTimers.current) {
      clearTimeout(timer)
      const note = currentNotes.current.find(note => note.id === id)
      if (note && onRenameNote) {
        if (!note.title.trim() || /[\]\n[]/.test(note.title)) { setError('请输入标题，标题不能含方括号或换行。'); return false }
        enqueueTitle(note.documentId || id, note.title.trim())
      }
    }
    titleTimers.current.clear()
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
  }
  function getNoteSnapshot(documentId: string) {
    return currentNotes.current.find(note => note.id === documentId || note.documentId === documentId)
  }
  function replaceSavedNote(documentId: string, body: string, versionId: string, comments: OutlineComment[], expected?: Pick<SettingNote, 'body' | 'comments'>) {
    const target = currentNotes.current.find(note => note.id === documentId || note.documentId === documentId)
    if (!target) return
    const newerWriting = expected && JSON.stringify([target.body, target.comments || []]) !== JSON.stringify([expected.body, expected.comments || []])
    const next = { ...target, documentId, body: newerWriting ? target.body : body, versionId, comments: newerWriting ? target.comments || [] : comments, localDraft: false }
    const queue = saveQueues.current.get(target.id)
    if (queue?.timer) clearTimeout(queue.timer)
    saveQueues.current.delete(target.id)
    savedSnapshots.current.set(target.id, JSON.stringify([body, comments]))
    currentNotes.current = currentNotes.current.map(note => note.id === target.id ? next : note)
    setNotes(currentNotes.current)
    // An explicit conflict choice supersedes both the editing recovery copy and
    // any first-creation journal still retaining a newer, previously failed save.
    clearCreation(target.id)
    for (const id of new Set([target.id, documentId])) {
      sessionStorage.removeItem(`guranovel:setting-draft-recovery:${id}`)
      sessionStorage.removeItem(`guranovel:setting-feedback-recovery:${id}`)
    }
    if (newerWriting) {
      sessionStorage.setItem(`guranovel:setting-draft-recovery:${target.id}`, next.body)
      sessionStorage.setItem(`guranovel:setting-feedback-recovery:${target.id}`, JSON.stringify({ body: next.body, comments: next.comments, versionId }))
      enqueueSave(target.id)
    }
    setError('')
  }
  useImperativeHandle(ref, () => ({ flush: flushAll, getNoteSnapshot, replaceSavedNote, openCollaboration: discussNote }))

  async function prepareCollaboration(): Promise<boolean> {
    // The main Agent reads the collection. Opening or sending must not manufacture
    // an empty target document; only actual author writing is flushed here.
    return await flushAll()
  }
  function applyCollaboration(result: SettingCollaborationAcceptance, base?: SettingNote): boolean {
    if (base && result.document.id !== (base.documentId || base.id)) return false
    const current = currentNotes.current.find(note => note.documentId === result.document.id || note.id === result.document.id || base && note.id === base.id)
    if (current && current.versionId === result.document.current_version_id && current.body === result.content && current.title === result.document.title) return true
    if (base ? !current || current.body !== base.body || current.title !== base.title || current.versionId !== base.versionId : current) return false
    const category = categoryFromDocument(result.document)
    const next: SettingNote = { ...current, id: current?.id || result.document.id, documentId: result.document.id, title: result.document.title || '未命名设定', category,
      versionId: result.document.current_version_id || undefined, body: result.content, comments: reanchorFeedbackComments(current?.comments || [], current?.body || '', result.content) }
    const queue = saveQueues.current.get(next.id)
    if (queue?.timer) clearTimeout(queue.timer)
    saveQueues.current.delete(next.id)
    savedSnapshots.current.set(next.id, JSON.stringify([next.body, next.comments || []]))
    currentNotes.current = current ? currentNotes.current.map(note => note.id === current.id ? next : note) : [...currentNotes.current, next]
    setNotes(currentNotes.current)
    sessionStorage.removeItem(`guranovel:setting-draft-recovery:${next.id}`)
    sessionStorage.removeItem(`guranovel:setting-feedback-recovery:${next.id}`)
    onCollaborationApplied?.(result, next.id)
    setSelectedId(next.id); setCategory(next.category); setMode('note')
    rememberView({ id: next.id, category: next.category, mode: 'note' })
    return true
  }

  const sidebarStatus = saveStatusText ?? (collection?.status === 'archived' ? '已归档 · 只读' : preview ? '交互预览' : '')
  return <div className="studio-setting" aria-label="作品设定">
    <div className="setting-sidebar" onPointerEnter={() => { if (!hidden) setSidebarOpen(true) }} onPointerLeave={() => setSidebarOpen(false)} onFocus={() => { if (!hidden) setSidebarOpen(true) }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setSidebarOpen(false) }} onKeyDown={event => { if (event.key === 'Escape' && !pinned) { event.currentTarget.querySelector<HTMLButtonElement>('.setting-sidebar-trigger')?.focus(); setSidebarOpen(false) } }}>
    <button className="setting-sidebar-trigger" aria-label="展开设定侧边栏" aria-expanded={sidebarVisible} aria-controls="setting-library" disabled={hidden && !pinned} onClick={() => setSidebarOpen(true)}><span /></button>
    <aside id="setting-library" className={`setting-library${sidebarVisible ? ' is-open' : ''}`} aria-label="设定侧边栏" inert={!sidebarVisible}>
      <div className="setting-library-top">
        <div className="setting-categories" role="group" aria-label="设定分类" data-category={category}>
          <span aria-hidden="true" />{(['setting', 'world'] as const).map(value => <button key={value} aria-pressed={category === value} onClick={() => changeCategory(value)}>{categoryNames[value]}</button>)}
        </div>
        <button className="studio-icon setting-icon-button" aria-label={pinned ? '取消固定设定侧边栏' : '固定设定侧边栏'} title={pinned ? '取消固定侧边栏' : '固定侧边栏'} aria-pressed={pinned} onClick={() => setPinned(!pinned)}><SettingActionIcon name="pin" /></button>
      </div>
      <div className="setting-library-actions">
        <div className="setting-new-row"><button className="setting-new setting-sidebar-action" aria-pressed={mode === 'chat'} onClick={() => { setSweep(value => value + 1); if (directEditing) create(); else showMode('chat') }} disabled={readOnly}>{sweep > 0 && <span key={sweep} className="setting-action-sweep" aria-hidden="true" />}<SettingActionIcon name="create" />新建内容</button>{!directEditing && <button className="setting-manual-new setting-icon-button" aria-label="手动新建条目" title="手动新建条目" disabled={readOnly} onClick={() => create()}><SettingActionIcon name="plus" /></button>}</div>
        <button className={`setting-sidebar-action${mode === 'graph' ? ' is-active' : ''}`} aria-pressed={mode === 'graph'} onClick={() => showMode('graph')}><SettingActionIcon name="graph" />关系图谱</button>
        <button className={`setting-sidebar-action${mode === 'search' ? ' is-active' : ''}`} aria-pressed={mode === 'search'} onClick={() => showMode('search')}><SettingActionIcon name="search" />搜索内容</button>
      </div>
      <SettingNoteList notes={shown.map(note => note.title ? note : { ...note, title: `未命名${categoryNames[note.category]}` })} category={category} activeId={displayMode === 'note' ? active?.id : undefined} disabled={readOnly || !activePage} onOpen={choose} onOrder={orderNotes} onDelete={deleteNotes} />
      {mutationBusy && <p role="status">正在同步删除或恢复…</p>}
      {!directEditing && deleted && <div className="setting-delete-undo" role="status"><small>已删除 {deleted.items.length} 项，正文引用保留</small><button type="button" disabled={mutationBusy} onClick={undoDelete}>撤销删除</button></div>}
      {sidebarStatus && <small className="setting-local-note" role={saveStatusText ? 'status' : undefined}>{sidebarStatus}</small>}
      {(libraryFooter || onLoadTrash) && <div className="setting-library-footer">{libraryFooter}{onLoadTrash && <button type="button" className="setting-trash-launcher setting-icon-button" aria-label="回收站" title="回收站" onClick={() => setTrashOpen(true)}><SettingActionIcon name="trash" /></button>}</div>}
    </aside>
    </div>
    {directEditing && <SettingUndoToast operation={undoOperation} onUndo={restoreEntries} onDismiss={() => setUndoOperation(null)} />}
    {trashOpen && onLoadTrash && <SettingTrashDialog collectionId={collection?.id || draftScope} readOnly={readOnly} onLoad={onLoadTrash} onRestore={entries => restoreEntries(entries.map(entry => entry.id))} onClose={() => setTrashOpen(false)} />}
    {mode === 'search' && activePage && !hidden && <SearchDialog title="搜索内容" query={query} onQuery={setQuery}
      inputType="text" inputLabel="搜索设定内容" placeholder="搜索设定与世界观中的标题、正文…"
      items={results.map(note => {
        const body = note.body.replace(/\[\[|\]\]/g, ''), start = Math.max(0, body.toLocaleLowerCase().indexOf(query.trim().toLocaleLowerCase()) - 30)
        return { ...note, description: `${start > 0 ? '…' : ''}${body.slice(start, start + 140)}${body.length > start + 140 ? '…' : ''}`, kind: categoryNames[note.category], icon: <SettingActionIcon name="note" /> }
      })}
      onClose={() => { rememberView({ id: selectedId, category, mode: searchReturnMode }); setMode(searchReturnMode) }} onSelect={choose}
      emptyText="没有找到相关条目"
      controlIcons={{ search: <SettingActionIcon name="search" />, close: <SettingActionIcon name="close" /> }}
      renderPreview={note => note ? <><span className="search-preview-icon setting-search-entry"><SettingActionIcon name="note" /></span><h3>{note.title}</h3><span className="search-preview-kind">{categoryNames[note.category]}</span><p>{note.body.replace(/\[\[|\]\]/g, '') || '暂无正文'}</p></> : <><h3>寻找世界的细节</h3><p>搜索设定与世界观中的标题和正文。</p></>} />}
    {displayMode !== 'chat' && displayMode !== 'graph' && (active || directEditing && collection && !collaborationInMenu) && !(directEditing && collaborationOpen) && <div className="setting-editor-tools">
      {onOpenHistory && active && <button type="button" className="setting-icon-button" aria-label="历史存档" disabled={!active.documentId || mutationBusy || restorePointBusy} title={!active.documentId ? '保存条目后可查看历史存档' : '历史存档'} onClick={() => onOpenHistory(active)}><SettingActionIcon name="history" /></button>}
      {onCreateRestorePoint && active && !readOnly && <button type="button" className="setting-save-point setting-icon-button" data-busy={restorePointBusy} data-state={activePointSaved ? 'success' : undefined} aria-label={restorePointBusy ? '正在存档…' : '存档当前内容'} aria-description={activePointSaved ? '当前内容已存档' : undefined} title={restorePointBusy ? '正在存档…' : '存档当前内容'} aria-busy={restorePointBusy} disabled={!active.documentId || mutationBusy || restorePointBusy} onClick={() => { void onCreateRestorePoint(active) }}>
        <SettingActionIcon name="save" />
      </button>}
      {!collaborationInMenu && <button disabled={readOnly && !directEditing} onClick={discussNote}>{directEditing ? '与设定 Agent 协作' : '交给 Agent 讨论'}</button>}
      {!directEditing && active && <button disabled={readOnly} onClick={() => { if (editing && !titleCommit()) return; if (!editing) setDraftTitle(active.title); setEditing(!editing) }}>{editing ? '阅读' : '编辑'}</button>}
    </div>}
    <div className={`setting-main${displayMode === 'chat' ? ' is-conversation' : chatVisited ? ' has-conversation-return' : ''}${directEditing && collaborationOpen && displayMode === 'note' ? ' is-generation' : ''}`}>
      {chatVisited && displayMode !== 'chat' && <button className="setting-return-chat" onClick={() => showMode('chat')}>返回设定对话</button>}
      <SettingConversation conversation={conversation} notes={notes} visible={displayMode === 'chat' && activePage} disabled={readOnly} referencingProjects={referencingProjects} preview={preview} onPatch={patchConversation} onSend={sendMessage} onCommentChange={(noteId, commentId, text) => {
        if (!readOnly) persist(currentNotes.current.map(note => note.id === noteId ? { ...note, comments: note.comments?.map(comment => comment.id === commentId ? { ...comment, text } : comment) } : note))
      }} onExample={() => patchConversation({ messages: [...currentConversation.current.messages, settingExampleReply(currentNotes.current, currentConversation.current)] })} onAccept={acceptProposals} onChange={changeProposal} onOpen={choose} onScroll={scrollTop => { currentConversation.current = { ...currentConversation.current, scrollTop } }} />
      {displayMode === 'chat' ? null : displayMode === 'graph' ? <SettingGraph notes={notes} onOpen={openTitle} /> : <TextMotionSurface className={`setting-document-motion${directEditing && collaborationOpen ? ' is-hidden' : ''}`} motionKey={directEditing && collaborationOpen ? undefined : `${category}:${active?.id || 'empty'}`} interruptOnInput>
        <div className="setting-document-scroll" ref={documentScroll} hidden={directEditing && collaborationOpen}>
          {active ? <><div className="setting-document">
            <h1 data-sweep-text={editing && !readOnly ? undefined : ''} aria-label={active.title || `未命名${categoryNames[active.category]}`}>{editing && !readOnly ? <span className="setting-title-input" data-text-input><span className="setting-title-mirror" data-sweep-text aria-hidden="true">{directEditing ? active.title : draftTitle}</span><input ref={titleInput} readOnly={mutationBusy} autoFocus={active.localDraft} aria-label="设定标题" placeholder={`未命名${categoryNames[active.category]}`} maxLength={100} value={directEditing ? active.title : draftTitle} onChange={event => directEditing ? updateDirectTitle(event.target.value) : setDraftTitle(event.target.value)} onScroll={event => { const mirror = event.currentTarget.previousElementSibling as HTMLElement | null; if (mirror) mirror.style.textIndent = `${-event.currentTarget.scrollLeft}px` }} onBlur={titleCommit} /></span> : active.title}</h1>
            {!!submitted.length && <div className="setting-note-comments" data-comment-frame><small>{stagedIds.length ? '评论暂存中' : '已加入对话'}</small><div ref={commentDots} className="studio-comment-dots studio-submitted-dots" role="group" aria-label="条目已发送评论">
              {submitted.map(comment => <button key={comment.id} type="button" data-comment-id={comment.id} disabled={readOnly} style={{ '--comment-color': comment.color } as CSSProperties} aria-label={`查看条目评论讨论：${comment.quote}`} title={`${comment.quote} · ${comment.text}`} aria-description="点击进入设定对话；拖动排序，拖出此栏删除；Alt 加左右方向键排序，Delete 移除" onClick={discussNote} />)}
            </div></div>}
            <div className={`studio-prose setting-note-prose${editing ? '' : ' is-reading'}`} key={active.id}><OutlineEditor outline={active.body} comments={active.comments || []} readOnly={readOnly || mutationBusy}
              draftInput={{ ref: noteInput, onTyping, ready: activePage && ready, visible: activePage, label: '设定正文', className: 'setting-note-editor', maxLength: 30000, sendLabel: '暂存评论并继续讨论', sendEffect: 'handoff', stagedIds, onSubmittedSelect: discussNote, linkOptions: notes.filter(note => note.id !== active.id).map(note => note.title), onOpenReference: openTitle, animateText: true, reading: !editing ? <div className="setting-note-body" data-sweep-text><LinkedText text={active.body || '还没有正文，点击编辑开始书写。'} notes={notes} comments={active.comments || []} onOpen={openTitle} /></div> : undefined }}
              onChange={(body, comments) => { updateNoteComments(comments, body); onTyping() }} onCommentsChange={updateNoteComments} onSend={stageNoteComments} />
            </div>
          </div>
          {!!references.length && <section className="setting-backlinks" aria-label="引用条目"><h2>引用条目 <span>{references.length}</span></h2>{references.map(title => {
            const note = resolveNote(notes, title)
            return <button key={title} onClick={() => openTitle(title)}>{title}<small>{note ? categoryNames[note.category] : '未解析'}</small></button>
          })}</section>}</> : <div className="setting-empty"><h1 data-sweep-text>{categoryNames[category]}</h1><p data-sweep-text>{preview ? '从一段对话开始，也可以直接新建条目。' : readOnly ? '暂无可查看的设定。' : '从左侧新建条目，开始完善设定。'}</p>{preview && <button disabled={readOnly} onClick={() => showMode('chat')}>新建内容</button>}</div>}
        </div>
      </TextMotionSurface>}
      {directEditing && collaborationOpen && collection && displayMode === 'note' && <SettingCollaborationPanel key={collection.id} collectionId={collection.id} notes={notes} readOnly={readOnly} onPrepare={prepareCollaboration} onApply={applyCollaboration}
        feedback={collectionFeedback.map(item => item.text).join('\n\n')}
        onSent={feedback => { const next = { ...sentFeedback }; for (const item of collectionFeedback) if (feedback.includes(item.text)) next[item.key] = item.signature; setSentFeedback(next); try { sessionStorage.setItem(`guranovel:setting-feedback-sent:${draftScope}`, JSON.stringify(next)) } catch { /* The durable Agent history still contains the submitted feedback. */ } }}
        onClose={() => setCollaborationOpen(false)} />}
      {error && <p className="setting-error" role="alert">{error}</p>}
    </div>
  </div>
}
