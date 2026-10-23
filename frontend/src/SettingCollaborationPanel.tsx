import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError } from './api/client'
import { acceptSettingCollectionConversationProposal, discardSettingCollectionConversationProposal, getOrCreateSettingCollectionCollaboration, getSettingCollectionCollaboration, getSettingCollectionConversation, getSettingCollectionConversationAttempt, resumeSettingCollectionConversationAttempt, finishSettingCollectionConversationAttempt, sendSettingCollectionConversationMessage, listSettingCollectionConversations, createSettingCollectionConversation,
  type SettingCollaboration, type SettingCollaborationAcceptance, type SettingCollaborationAttempt, type SettingCollaborationMessage, type SettingCollectionCollaborationSend, type SettingConversationSummary } from './api/settingCollaborationClient'
import type { SettingNote } from './settingNotes'
import { StreamText } from './StudioMotion'
import { useDialogMotion } from './useDialogMotion'
import './SettingCollaborationPanel.css'
import AssistantSendIcon from './AssistantSendIcon'
import SettingActionIcon from './SettingActionIcon'

export interface SettingCollaborationPanelProps {
  collectionId: string
  notes: SettingNote[]
  feedback?: string
  readOnly?: boolean
  onPrepare: () => Promise<boolean>
  onApply: (result: SettingCollaborationAcceptance, base?: SettingNote) => boolean | Promise<boolean>
  onClose: () => void
  onSent?: (feedback: string) => void
}

type PendingSend = { id: string; text: string; draft: string; revision?: number; sent?: boolean; feedback?: string; payload?: SettingCollectionCollaborationSend }
type PendingAcceptance = { proposalId: string; base: SettingNote | null }
type Recovery = { draft: string; revision: number; hasDraft: boolean; pending: PendingSend | null; acceptance: PendingAcceptance | null; scrollTop: number }
const recoveryKey = (collectionId: string) => `guranovel:setting-agent:collection:${collectionId}`
const conversationKey = (collectionId: string, id: string) => `${recoveryKey(collectionId)}:conversation:${id}`
const unsentKey = (collectionId: string) => `${recoveryKey(collectionId)}:unsent-conversation`
const unsentExistingKey = (collectionId: string) => `${recoveryKey(collectionId)}:unsent-existing-conversation`
const activeKey = (collectionId: string) => `${recoveryKey(collectionId)}:active-conversation`
const createKey = (collectionId: string) => `${recoveryKey(collectionId)}:pending-create`
function storedSelection(collectionId: string): string | null {
  try { const id = sessionStorage.getItem(activeKey(collectionId)); return id && id.length <= 128 ? id : null } catch { return null }
}
function storedCreate(collectionId: string): string | null {
  const value = JSON.parse(sessionStorage.getItem(createKey(collectionId)) || 'null')
  if (value === null) return null
  if (typeof value.client_request_id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.client_request_id)) return value.client_request_id
  throw new Error('新建对话的恢复记录不完整，原对话和草稿已保留。')
}
function recover(key: string): Recovery {
  const empty: Recovery = { draft: '', revision: 0, hasDraft: false, pending: null, acceptance: null, scrollTop: 0 }
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (!value || typeof value !== 'object') return empty
    const draft = typeof value.draft === 'string' && value.draft.length <= 16000 ? value.draft : ''
    let pending: PendingSend | null = null
    const candidate = value.pending
    if (candidate && typeof candidate.id === 'string' && candidate.id.length > 0 && candidate.id.length <= 128 && typeof candidate.text === 'string' && candidate.text.trim() && candidate.text.length <= 16000) {
      if (!candidate.payload) pending = { id: candidate.id, text: candidate.text, draft: typeof candidate.draft === 'string' ? candidate.draft : candidate.text, ...(typeof candidate.feedback === 'string' ? { feedback: candidate.feedback } : {}) }
      else {
        const payload = candidate.payload
        if (payload.client_message_id === candidate.id && payload.content === candidate.text && Object.keys(payload).every(key => key === 'client_message_id' || key === 'content')) pending = { ...candidate, draft: typeof candidate.draft === 'string' ? candidate.draft : candidate.text }
      }
    }
    const acceptance = value.acceptance
    const base = acceptance?.base
    const validAcceptance = typeof acceptance?.proposalId === 'string' && (base === null || base && typeof base.id === 'string' && typeof base.documentId === 'string' && typeof base.versionId === 'string'
      && typeof base.body === 'string' && typeof base.title === 'string' && base.body.length <= 65536 && base.title.length <= 1000)
    if (pending) {
      pending.sent = candidate.sent === false ? false : !!candidate.payload
      pending.revision = Number.isSafeInteger(candidate.revision) && candidate.revision >= 0 ? candidate.revision : undefined
    }
    return { draft, revision: Number.isSafeInteger(value.revision) && value.revision >= 0 ? value.revision : 0, hasDraft: typeof value.draft === 'string', pending, acceptance: validAcceptance ? acceptance : null, scrollTop: Number.isFinite(value.scrollTop) && value.scrollTop >= 0 ? value.scrollTop : 0 }
  } catch { return empty }
}
function targetNote(notes: SettingNote[], message: SettingCollaborationMessage): SettingNote | undefined {
  return notes.find(note => !note.localDraft && (note.documentId || note.id) === message.proposal?.target_document_id)
}
function sameBase(note: SettingNote | undefined, message: SettingCollaborationMessage): boolean {
  if (message.proposal?.target_document_id === null) return true
  return !!note && (note.documentId || note.id) === message.proposal?.target_document_id && note.versionId === message.proposal?.base_version_id
    && note.body === message.base_content && note.title === message.base_title
}
function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : '无法连接设定 Agent，讨论和文稿已保留，可以重试。'
}
function matchesAttempt(attempt: SettingCollaborationAttempt, request: PendingSend): boolean {
  return attempt.client_message_id === request.id && attempt.content === request.text && attempt.request.client_message_id === request.id
    && attempt.request.content === request.text && Object.keys(attempt.request).every(key => key === 'client_message_id' || key === 'content')
}
function completed(result: SettingCollaboration, attempt: SettingCollaborationAttempt, request: PendingSend): boolean {
  if (attempt.status !== 'completed' || !matchesAttempt(attempt, request) || !attempt.has_result || !attempt.user_message_id || !attempt.assistant_message_id || attempt.user_message_id === attempt.assistant_message_id) return false
  const user = result.messages.find(message => message.id === attempt.user_message_id), assistant = result.messages.find(message => message.id === attempt.assistant_message_id)
  return result.messages.filter(message => message.attempt_id === attempt.id).length === 2
    && user?.role === 'user' && user.attempt_id === attempt.id && user.client_message_id === request.id && user.content === request.text
    && assistant?.role === 'assistant' && assistant.attempt_id === attempt.id && !!assistant.content.trim()
}

export default function SettingCollaborationPanel(props: SettingCollaborationPanelProps) {
  return <CollaborationManager key={props.collectionId} {...props} />
}

type SessionSnapshot = { loaded: boolean; loading: boolean; empty: boolean; hasDraft: boolean; pending: boolean }
type Selection = { key: string; id: string | null; local?: boolean; focusComposer?: boolean }
function CollaborationManager(props: SettingCollaborationPanelProps) {
  const { collectionId, readOnly = false } = props
  const [selection, setSelection] = useState<Selection>(() => { const id = storedSelection(collectionId); return id === 'draft' ? { id: null, key: 'draft', local: true } : { id, key: id ?? 'default' } })
  const [returnTarget] = useState(() => document.activeElement instanceof HTMLElement ? document.activeElement : null)
  const selected = useRef(selection), snapshot = useRef<SessionSnapshot | null>(null), live = useRef(true), historyGeneration = useRef(0)
  const [historyOpen, setHistoryOpen] = useState(false), [items, setItems] = useState<SettingConversationSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false), [historyLoaded, setHistoryLoaded] = useState(false), [historyError, setHistoryError] = useState(''), [historyRetry, setHistoryRetry] = useState(0)
  const [nextOffset, setNextOffset] = useState<number | null>(null), [historyScrolled, setHistoryScrolled] = useState(false)
  const [selectionError, setSelectionError] = useState(''), [sessionLoading, setSessionLoading] = useState(!selection.local)
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  useEffect(() => {
    if (!historyOpen) return
    const token = ++historyGeneration.current
    void listSettingCollectionConversations(collectionId).then(result => {
      if (!live.current || token !== historyGeneration.current) return
      setItems(result.items); setNextOffset(result.next_offset); setHistoryLoaded(true)
    }).catch(problem => { if (live.current && token === historyGeneration.current) setHistoryError(errorMessage(problem)) })
      .finally(() => { if (live.current && token === historyGeneration.current) setHistoryLoading(false) })
    return () => { historyGeneration.current += 1 }
  }, [collectionId, historyOpen, historyRetry])
  function toggleHistory() {
    if (!historyOpen) { setHistoryLoading(true); setHistoryError(''); setHistoryScrolled(false) }
    setHistoryOpen(value => !value)
  }
  function saveSelection(id: string) {
    sessionStorage.setItem(activeKey(collectionId), id)
    if (sessionStorage.getItem(activeKey(collectionId)) !== id) throw new Error('浏览器未能保存选中的对话，原对话和草稿已保留。')
  }
  function activate(id: string) {
    saveSelection(id)
    if (selected.current.id !== id) {
      const next = { key: id, id, focusComposer: true }
      selected.current = next; snapshot.current = null; setSessionLoading(true); setSelection(next)
    }
    setHistoryOpen(false)
  }
  function newConversation() {
    if (readOnly || sessionLoading) return
    try {
      setSelectionError('')
      const unresolvedCreate = sessionStorage.getItem(createKey(collectionId)) !== null
      if (selected.current.local || !unresolvedCreate && snapshot.current?.loaded && snapshot.current.empty && selected.current.id && !snapshot.current.pending) { setHistoryOpen(false); return }
      const existing = sessionStorage.getItem(unsentExistingKey(collectionId))
      if (!unresolvedCreate && existing && !recover(unsentKey(collectionId)).hasDraft) { activate(existing); return }
      saveSelection('draft')
      const next = { key: `draft:${crypto.randomUUID()}`, id: null, local: true, focusComposer: true }
      selected.current = next; snapshot.current = null; setSessionLoading(false); setSelection(next); setHistoryOpen(false)
    } catch { setSelectionError('浏览器未能保存新对话草稿，原对话和输入已保留。') }
  }
  async function moreHistory() {
    if (historyLoading || nextOffset === null) return
    const token = historyGeneration.current
    setHistoryLoading(true); setHistoryError('')
    try {
      const result = await listSettingCollectionConversations(collectionId, nextOffset)
      if (!live.current || token !== historyGeneration.current) return
      setItems(previous => [...previous, ...result.items.filter(item => !previous.some(existing => existing.id === item.id))]); setNextOffset(result.next_offset)
    } catch (problem) { if (live.current && token === historyGeneration.current) setHistoryError(errorMessage(problem)) }
    finally { if (live.current && token === historyGeneration.current) setHistoryLoading(false) }
  }
  const historyPane = <section className="setting-collaboration-sessions" role="region" aria-label="设定 Agent 历史对话">
    <h3>历史对话</h3>
    <div className={`setting-collaboration-session-list${historyScrolled ? ' has-scrolled' : ''}`} onScroll={event => setHistoryScrolled(event.currentTarget.scrollTop > 0)}>
      {items.map(item => <button type="button" className="setting-collaboration-session-item" key={item.id} aria-label={`继续对话：${item.title}`} aria-current={item.id === selection.id ? 'true' : undefined}
        onClick={() => { try { activate(item.id) } catch { setSelectionError('浏览器未能保存选中的对话，原对话和草稿已保留。') } }}>
        <span className="setting-collaboration-session-title">{item.title}{item.id === selection.id && <small>当前对话</small>}</span>
        <time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</time>
        <span className="setting-collaboration-session-preview">{item.preview || (item.message_count === 0 ? '尚未开始讨论' : '')}</span>
      </button>)}
      {historyLoading && <p role="status">正在载入历史对话…</p>}
      {historyError && <div role="alert"><p>{historyError}</p><button type="button" onClick={() => { setHistoryLoading(true); setHistoryError(''); setHistoryRetry(value => value + 1) }}>重试载入历史对话</button></div>}
      {!historyLoading && !historyError && historyLoaded && items.length === 0 && <p>开始讨论后，对话会显示在这里。</p>}
      {nextOffset !== null && !historyLoading && <button type="button" className="setting-collaboration-more" onClick={() => { void moreHistory() }}>加载更多对话</button>}
    </div>
  </section>
  const toolbar = <div className="setting-collaboration-toolbar">
    <button type="button" className="setting-icon-button" data-icon="history" title="历史对话" aria-label="历史对话" aria-pressed={historyOpen} onClick={toggleHistory}><SettingActionIcon name="history" /></button>
    <button type="button" className="setting-icon-button" data-icon="create" title="新建对话" aria-label="新建对话" disabled={readOnly || sessionLoading} onClick={newConversation}><SettingActionIcon name="create" /></button>
  </div>
  return <CollaborationSession key={selection.key} {...props} conversationId={selection.id} localDraft={!!selection.local} focusComposer={selection.focusComposer ?? true} returnTarget={returnTarget} historyOpen={historyOpen} historyPane={historyPane} toolbar={toolbar} selectionError={selectionError}
    onBound={id => { if (selected.current.key === selection.key) { saveSelection(id); const next = { ...selected.current, id, local: false }; selected.current = next; setSelection(next) } }}
    onSnapshot={value => { if (selected.current.key === selection.key) {
      snapshot.current = value; setSessionLoading(value.loading)
      if (selected.current.id && value.loaded) {
        try {
          const current = sessionStorage.getItem(unsentExistingKey(collectionId))
          if (value.empty && value.hasDraft && !value.pending && !current) sessionStorage.setItem(unsentExistingKey(collectionId), selected.current.id)
          else if ((!value.empty || value.pending) && current === selected.current.id) sessionStorage.removeItem(unsentExistingKey(collectionId))
        } catch { setSelectionError('未发送草稿的入口无法暂存，原稿已保留；请检查浏览器存储。') }
      }
    } }} />
}

interface SessionProps extends SettingCollaborationPanelProps {
  conversationId: string | null
  localDraft: boolean
  focusComposer: boolean
  returnTarget: HTMLElement | null
  toolbar: ReactNode
  historyOpen: boolean
  historyPane: ReactNode
  selectionError: string
  onBound: (id: string) => void
  onSnapshot: (value: SessionSnapshot) => void
}
function CollaborationSession({ collectionId, conversationId, localDraft, focusComposer, returnTarget, toolbar, historyOpen, historyPane, selectionError, onBound, onSnapshot, notes, feedback, readOnly = false, onPrepare, onApply, onClose, onSent }: SessionProps) {
  const [initialId] = useState(conversationId), [initialLocal] = useState(localDraft)
  const [initial] = useState(() => recover(localDraft ? unsentKey(collectionId) : conversationId ? conversationKey(collectionId, conversationId) : recoveryKey(collectionId)))
  const key = useRef(localDraft ? unsentKey(collectionId) : conversationId ? conversationKey(collectionId, conversationId) : recoveryKey(collectionId)), boundId = useRef(conversationId), hasStoredDraft = useRef(initial.hasDraft), notesRef = useRef(notes)
  const bindCallback = useRef(onBound), snapshotCallback = useRef(onSnapshot)
  const historyView = useRef(historyOpen)
  useLayoutEffect(() => { bindCallback.current = onBound; snapshotCallback.current = onSnapshot; historyView.current = historyOpen }, [onBound, onSnapshot, historyOpen])
  useLayoutEffect(() => { notesRef.current = notes }, [notes])
  const draftRef = useRef(initial.draft), revision = useRef(initial.revision), pendingRef = useRef(initial.pending), acceptanceRef = useRef(initial.acceptance), scrollRef = useRef(initial.scrollTop)
  const [draft, setDraft] = useState(initial.draft), [pending, setPending] = useState(initial.pending), [acceptance, setAcceptance] = useState(initial.acceptance)
  const [conversation, setConversation] = useState<SettingCollaboration | null>(null)
  const [scrolled, setScrolled] = useState(initial.scrollTop > 0), stickToBottom = useRef(true)
  const [loading, setLoading] = useState(!initialLocal), [attempt, setAttempt] = useState(0)
  const [error, setError] = useState(''), [work, setWork] = useState<'send' | 'recovery' | 'proposal' | null>(null)
  const busy = work !== null
  const [currentAttempt, setCurrentAttempt] = useState<SettingCollaborationAttempt | null>(null), [notice, setNotice] = useState('')
  const [comparing, setComparing] = useState<string[]>([]), [staleIds, setStaleIds] = useState<string[]>([])
  const working = useRef<'send' | 'recovery' | 'proposal' | null>(null)
  const busyRef = useRef(false), live = useRef(true), operation = useRef(0), closingRef = useRef(false), input = useRef<HTMLTextAreaElement>(null), history = useRef<HTMLDivElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null), restoreFocus = useRef(false), firstHistory = useRef(true)
  const [shouldFocus] = useState(focusComposer)
  const sentCallback = useRef(onSent)
  useLayoutEffect(() => { sentCallback.current = onSent }, [onSent])
  const { closing, requestClose, onAnimationEnd } = useDialogMotion(onClose, undefined, false, 'setting-collaboration-out')
  const agentName = '设定集 Agent'
  const hasMessages = Boolean(conversation?.messages.length)
  const persist = useCallback(() => {
    try {
      const value = JSON.stringify({ draft: draftRef.current, revision: revision.current, pending: pendingRef.current, acceptance: acceptanceRef.current, scrollTop: scrollRef.current })
      sessionStorage.setItem(key.current, value); return sessionStorage.getItem(key.current) === value
    }
    catch { return false }
  }, [])
  const receive = useCallback((result: SettingCollaboration) => {
    if (result.setting_collection_id !== collectionId || result.document_id !== null || boundId.current && result.id !== boundId.current) throw new ApiError(0, 'invalid_response', '回复与当前对话不一致，草稿已保留，请重新检查。')
    const target = conversationKey(collectionId, result.id)
    let retiredKey: string | null = null
    if (key.current !== target) {
      if (!initialLocal && !result.is_default) throw new ApiError(0, 'invalid_response', '原有讨论的身份不一致，草稿已保留。')
      const existing = sessionStorage.getItem(target)
      if (existing !== null) {
        const saved = recover(target)
        const source = { draft: draftRef.current, revision: revision.current, pending: pendingRef.current, acceptance: acceptanceRef.current }
        const destination = { draft: saved.draft, revision: saved.revision, pending: saved.pending, acceptance: saved.acceptance }
        if ((hasStoredDraft.current || pendingRef.current || acceptanceRef.current) && JSON.stringify(source) !== JSON.stringify(destination)) {
          throw new ApiError(0, 'setting_recovery_conflict', '发现两份不同的本地恢复记录，均已保留且未合并。请复制当前输入后，从历史对话重新打开当前对话。')
        }
        draftRef.current = saved.draft; revision.current = saved.revision; hasStoredDraft.current = saved.hasDraft; setDraft(saved.draft)
        pendingRef.current = saved.pending; setPending(saved.pending)
        acceptanceRef.current = saved.acceptance; setAcceptance(saved.acceptance)
        scrollRef.current = saved.scrollTop; setScrolled(saved.scrollTop > 0)
      }
      const previous = key.current
      key.current = target
      if (!persist()) { key.current = previous; throw new ApiError(0, 'setting_recovery_failed', '对话草稿无法暂存，原稿已保留；请检查浏览器存储。') }
      retiredKey = previous
    }
    boundId.current = result.id; bindCallback.current(result.id)
    if (retiredKey) {
      sessionStorage.removeItem(retiredKey)
      if (sessionStorage.getItem(retiredKey) !== null) throw new ApiError(0, 'setting_recovery_failed', '旧草稿尚未完成迁移，输入已保留；请检查浏览器存储后继续发送。')
    }
    setConversation(result)
    let request = pendingRef.current
    if (!request) {
      const unfinished = (result.attempts ?? []).filter(item => item.status !== 'completed' && item.status !== 'finished')
        .sort((left, right) => left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id))[0]
      if (unfinished) {
        const restored = !hasStoredDraft.current && revision.current === 0 && !draftRef.current
        if (restored) { draftRef.current = unfinished.content; setDraft(unfinished.content) }
        request = { id: unfinished.client_message_id, text: unfinished.content, draft: draftRef.current, ...(restored ? { revision: revision.current } : {}),
          sent: true, payload: { client_message_id: unfinished.client_message_id, content: unfinished.content } }
        pendingRef.current = request; setPending(request); persist()
      }
    }
    const matched = request ? (result.attempts ?? []).find(item => matchesAttempt(item, request!)) : undefined
    setCurrentAttempt(matched ?? null)
    if (request && matched && completed(result, matched, request)) {
      if (request.revision !== undefined && revision.current === request.revision && draftRef.current === request.draft) { draftRef.current = ''; setDraft('') }
      pendingRef.current = null; setPending(null); setCurrentAttempt(null); persist(); setNotice('回复已完成。')
      if (request.feedback) sentCallback.current?.(request.feedback)
    } else if (request && matched?.status === 'finished') {
      pendingRef.current = null; setPending(null); setCurrentAttempt(null); persist()
      setNotice('已结束等待，输入已保留。再次发送会创建新请求；结束等待不代表取消调用。')
    }
  }, [collectionId, initialLocal, persist])
  useEffect(() => {
    live.current = true
    return () => {
      live.current = false
      const focused = document.activeElement
      if (restoreFocus.current && returnFocus.current?.isConnected && (focused === document.body || focused === document.documentElement || !focused?.isConnected)) returnFocus.current.focus({ preventScroll: true })
    }
  }, [])
  useLayoutEffect(() => { returnFocus.current = returnTarget; if (shouldFocus) input.current?.focus({ preventScroll: true }) }, [returnTarget, shouldFocus])
  useEffect(() => {
    if (initialLocal) return
    let active = true
    const token = operation.current
    void (initialId ? getSettingCollectionConversation(collectionId, initialId) : getSettingCollectionCollaboration(collectionId)).catch(problem => {
      if (!initialId && problem instanceof ApiError && problem.status === 404 && active && token === operation.current) return getOrCreateSettingCollectionCollaboration(collectionId)
      throw problem
    }).then(result => {
      if (!active || token !== operation.current) return
      receive(result); setError(''); setLoading(false)
    }).catch(problem => { if (active && token === operation.current) { setError(errorMessage(problem)); setLoading(false) } })
    return () => { active = false }
  }, [collectionId, attempt, initialId, initialLocal, receive])
  useEffect(() => { snapshotCallback.current({ loaded: !!conversation || initialLocal, loading, empty: !hasMessages && !conversation?.attempts.length, hasDraft: !!draft, pending: !!pending || !!acceptance }) }, [conversation, initialLocal, loading, hasMessages, draft, pending, acceptance])
  useLayoutEffect(() => {
    if (!conversation || !history.current || historyOpen) return
    if (firstHistory.current) history.current.scrollTop = scrollRef.current
    else if (stickToBottom.current) history.current.scrollTop = history.current.scrollHeight
    firstHistory.current = false
  }, [conversation, historyOpen])

  function changeDraft(text: string) { hasStoredDraft.current = true; revision.current += 1; draftRef.current = text; setDraft(text); persist() }
  function finishBusy(token = operation.current) { if (token !== operation.current) return; busyRef.current = false; working.current = null; if (live.current) setWork(null) }
  function close() { closingRef.current = true; restoreFocus.current = true; persist(); void requestClose() }
  async function prepareNotes() {
    try { return await onPrepare() }
    catch { throw new ApiError(0, 'setting_prepare_failed', '设定集草稿未能保存，请先处理保存问题后重试。') }
  }
  async function send(retry = false) {
    if (busyRef.current || readOnly || closingRef.current || loading || !boundId.current && !initialLocal || (!retry && pendingRef.current) || (retry && pendingRef.current?.sent)) return
    const text = [draftRef.current.trim(), feedback?.trim() ? `作者评论：\n${feedback.trim()}` : ''].filter(Boolean).join('\n\n')
    const request: PendingSend = retry && pendingRef.current ? pendingRef.current : { id: crypto.randomUUID(), text, draft: draftRef.current, revision: revision.current, sent: false, ...(feedback?.trim() ? { feedback } : {}) }
    if (!request.text) return
    if (request.text.length > 16000) { setError('消息与评论合计不能超过 16000 字。'); return }
    const token = ++operation.current
    busyRef.current = true; working.current = 'send'; setWork('send'); setError(''); setNotice('')
    pendingRef.current = request; setPending(request); setCurrentAttempt(null)
    if (!persist()) { setError('待发送消息无法暂存，请检查浏览器存储后重试。'); finishBusy(token); return }
    let resolvingCreate = false
    try {
      if (!await prepareNotes()) throw new ApiError(0, 'setting_prepare_failed', '设定集草稿未能保存，请先处理保存问题后重试。')
      if (!live.current || closingRef.current || token !== operation.current) return
      request.payload = { client_message_id: request.id, content: request.text }
      if (!persist()) throw new ApiError(0, 'setting_recovery_failed', '待发送消息无法暂存，请检查浏览器存储后继续发送。')
      let acknowledgedCreate = conversation?.client_request_id ?? null
      if (!boundId.current) {
        resolvingCreate = true
        let requestId = storedCreate(collectionId)
        if (!requestId) {
          requestId = crypto.randomUUID()
          sessionStorage.setItem(createKey(collectionId), JSON.stringify({ client_request_id: requestId }))
          if (storedCreate(collectionId) !== requestId) throw new ApiError(0, 'setting_recovery_failed', '新对话请求无法暂存，输入已保留。')
        }
        const created = await createSettingCollectionConversation(collectionId, requestId)
        if (!live.current || closingRef.current || token !== operation.current) return
        if (created.client_request_id !== requestId || created.is_default || created.setting_collection_id !== collectionId || created.document_id !== null) throw new ApiError(0, 'invalid_response', '新建对话的响应不匹配，原请求和草稿已保留。')
        receive(created); acknowledgedCreate = requestId
        const focused = document.activeElement
        if (!historyView.current && (focused === document.body || focused === document.documentElement || !focused?.isConnected || focused instanceof HTMLElement && !!focused.closest('.setting-collaboration-panel'))) input.current?.focus({ preventScroll: true })
      }
      if (acknowledgedCreate && storedCreate(collectionId) === acknowledgedCreate) {
        sessionStorage.removeItem(createKey(collectionId))
        if (sessionStorage.getItem(createKey(collectionId)) !== null) throw new ApiError(0, 'setting_recovery_failed', '新对话的恢复记录尚未确认，输入已保留。')
      }
      resolvingCreate = false
      if (!live.current || closingRef.current || token !== operation.current || pendingRef.current?.id !== request.id) return
      request.sent = true
      if (!persist()) { request.sent = false; throw new ApiError(0, 'setting_recovery_failed', '待发送消息无法暂存，请检查浏览器存储后重试。') }
      setPending({ ...request })
      const result = await sendSettingCollectionConversationMessage(collectionId, boundId.current!, request.payload)
      if (live.current && token === operation.current) { receive(result); setLoading(false) }
    } catch (problem) {
      if (!live.current || token !== operation.current) return
      if (problem instanceof ApiError && problem.code === 'setting_agent_attempt_pending') {
        // The server guarantees this new identity was not claimed or dispatched.
        pendingRef.current = null; setPending(null); persist()
        try { const result = await getSettingCollectionConversation(collectionId, boundId.current!); if (live.current && token === operation.current) receive(result) } catch { /* Keep the original, useful rejection visible. */ }
      }
      if (live.current && token === operation.current) setError(`${errorMessage(problem)}${resolvingCreate ? ' 点击“继续发送”会检查同一次创建，消息尚未发送。' : ''}`)
    } finally { finishBusy(token) }
  }
  async function recoverAttempt(action: 'inspect' | 'resume' | 'finish') {
    const request = pendingRef.current
    if (!request || readOnly || closingRef.current || busyRef.current && working.current !== 'send') return
    if (action === 'resume' && !currentAttempt) { if (!request.sent) await send(true); return }
    if (!boundId.current) {
      if (action === 'finish' && !request.sent) { pendingRef.current = null; setPending(null); persist(); setNotice('消息尚未发送，输入已保留。') }
      return
    }
    const token = ++operation.current
    busyRef.current = true; working.current = action === 'resume' && currentAttempt?.status === 'prepared' ? 'send' : 'recovery'; setWork(working.current); setError(''); setNotice('')
    // A prepared attempt may become dispatched even when its response is lost.
    if (action === 'resume' && currentAttempt?.status === 'prepared') setCurrentAttempt(null)
    try {
      if (action === 'inspect' && currentAttempt) {
        const result = await getSettingCollectionConversationAttempt(collectionId, boundId.current!, currentAttempt.id)
        if (!live.current || token !== operation.current) return
        if (!matchesAttempt(result, request)) throw new ApiError(0, 'invalid_response', '请求状态与原消息不一致，输入已保留，请重新检查。')
        if (result.status !== 'completed' && result.status !== 'finished') { setCurrentAttempt(result); return }
      }
      const result = action === 'finish'
        ? await finishSettingCollectionConversationAttempt(collectionId, boundId.current!, request.payload ?? { client_message_id: request.id, content: request.text })
        : action === 'resume' && currentAttempt
          ? await resumeSettingCollectionConversationAttempt(collectionId, boundId.current!, currentAttempt.id)
          : await getSettingCollectionConversation(collectionId, boundId.current!)
      if (!live.current || token !== operation.current) return
      receive(result); setLoading(false)
      if (action === 'finish' && pendingRef.current) setError('结束等待尚未确认，原请求和输入已保留；请检查结果。')
      else if (result.attempts?.some(item => matchesAttempt(item, request) && item.status === 'completed') && pendingRef.current) setError('完成记录缺少匹配的完整回复，输入已保留；请检查结果。')
    } catch (problem) { if (live.current && token === operation.current) { setError(errorMessage(problem)); setLoading(false) } }
    finally { finishBusy(token) }
  }
  async function adopt(message: SettingCollaborationMessage, retry = false) {
    if (busyRef.current || readOnly || !message.proposal || closingRef.current || !boundId.current) return
    const token = ++operation.current
    busyRef.current = true; working.current = 'proposal'; setWork('proposal'); setError('')
    try {
      const checking = retry && acceptanceRef.current?.proposalId === message.proposal.id
      if (!checking && !await prepareNotes()) { setError('设定集草稿未能保存，请重试。'); return }
      if (!live.current || closingRef.current || token !== operation.current) return
      const base = checking ? acceptanceRef.current?.base || undefined : targetNote(notesRef.current, message)
      if (!retry && !sameBase(base, message)) {
        setStaleIds(ids => [...new Set([...ids, message.id])]); setComparing(ids => [...new Set([...ids, message.id])])
        setError('目标条目的正文或标题已更新，已保留当前文稿和建议。请基于设定集的最新内容重新讨论。'); return
      }
      acceptanceRef.current = { proposalId: message.proposal.id, base: base || null }; setAcceptance(acceptanceRef.current)
      if (!persist()) { setError('采纳请求无法暂存，请检查浏览器存储后重试。'); return }
      const result = await acceptSettingCollectionConversationProposal(collectionId, boundId.current!, message.proposal.id, base?.versionId || null, base?.body || '', base?.title || '')
      if (!live.current || token !== operation.current) return
      if (result.document.setting_collection_id !== collectionId || message.proposal.target_document_id && result.document.id !== message.proposal.target_document_id) {
        throw new ApiError(0, 'invalid_response', '采纳结果与目标条目不一致，文稿已保留，请检查采纳结果。')
      }
      receive(result.conversation)
      const applied = await onApply(result, base)
      if (!live.current || token !== operation.current) return
      acceptanceRef.current = null; setAcceptance(null); persist()
      if (!applied) setError('建议已在服务器采纳；你刚输入的文稿已保留，请重新比较后加载已采纳版本。')
    } catch (problem) {
      if (!live.current || token !== operation.current) return
      if (problem instanceof ApiError && problem.status === 409) { acceptanceRef.current = null; setAcceptance(null); persist() }
      setError(errorMessage(problem))
    } finally { finishBusy(token) }
  }
  async function discard(message: SettingCollaborationMessage) {
    if (busyRef.current || readOnly || !message.proposal || closingRef.current || !boundId.current) return
    const token = ++operation.current
    busyRef.current = true; working.current = 'proposal'; setWork('proposal'); setError('')
    try { const result = await discardSettingCollectionConversationProposal(collectionId, boundId.current!, message.proposal.id); if (live.current && token === operation.current) receive(result) }
    catch (problem) { if (live.current && token === operation.current) setError(errorMessage(problem)) }
    finally { finishBusy(token) }
  }
  function discussCurrent(message: SettingCollaborationMessage) {
    changeDraft(draftRef.current.trim() || `请基于设定集的最新内容重新比较「${message.proposal?.title}」的上一条建议，保留我新写的内容，再提出修改建议。`)
    input.current?.focus({ preventScroll: true })
  }
  const attemptStatus = pending ? !pending.sent ? '消息尚未发送，可继续发送。'
    : currentAttempt?.status === 'prepared' ? '请求已保存，尚未调用设定 Agent。'
      : currentAttempt?.status === 'result_saved' ? '结果已保存，可恢复完整回复，无需重新生成。'
        : currentAttempt?.status === 'completed' ? '完成记录缺少完整配对，结果尚未确认。'
          : busy && work === 'send' ? '设定 Agent 正在生成，离开后可检查结果。'
            : '请求已发出，结果尚未确认；请检查结果或结束等待。' : notice
  const recoveryBusy = busy && work !== 'send'

  return <aside className={`setting-collaboration-panel${(conversation || initialLocal) && !hasMessages && !loading ? ' is-empty' : ''}${historyOpen ? ' is-history' : ''}${closing ? ' is-closing' : ''}`} aria-label="设定 Agent 协作" inert={closing} onAnimationEnd={onAnimationEnd}
    onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close() } }}>
    <header>{toolbar}<button type="button" aria-label="关闭设定协作" onClick={close}>返回文稿</button></header>
    {(selectionError || historyOpen && error) && <div className="setting-collaboration-feedback" role="alert"><p>{selectionError || error}</p></div>}
    {historyOpen && historyPane}
    <div className="setting-collaboration-session" hidden={historyOpen}>
    <div className={`setting-collaboration-history${scrolled ? ' has-scrolled' : ''}`} ref={history} role="log" aria-label="设定 Agent 讨论记录" tabIndex={0} onScroll={event => {
      const element = event.currentTarget
      scrollRef.current = element.scrollTop; stickToBottom.current = element.scrollHeight - element.clientHeight - element.scrollTop < 48
      setScrolled(element.scrollTop > 0); persist()
    }}>
      {loading ? <p className="setting-collaboration-empty" role="status">正在读取讨论…</p> : (conversation || initialLocal) && !hasMessages && <div className="setting-collaboration-empty"><h3>一起构建这个世界</h3><p>从角色、地点或世界规则开始，逐步完善设定集。</p></div>}
      {conversation?.messages.map(message => {
        const current = targetNote(notes, message)
        const proposal = message.proposal, expanded = comparing.includes(message.id), stale = !!proposal && (!sameBase(current, message) || staleIds.includes(message.id))
        const uncertain = !!proposal && acceptance?.proposalId === proposal.id
        return <article key={message.id} className={`setting-collaboration-message is-${message.role}`}><small>{message.role === 'user' ? '你' : agentName}</small>
          {message.role === 'assistant' ? <StreamText text={message.content} delay={0} startedAt={Date.parse(message.created_at)} /> : <p>{message.content}</p>}
          {proposal && <div className="setting-collaboration-proposal"><div className="setting-collaboration-proposal-heading"><span>{message.proposal_status === 'accepted' ? '已采纳' : message.proposal_status === 'rejected' ? '未采用' : proposal.target_document_id === null ? '新建条目' : '修改条目'} · {proposal.title}</span><button type="button" aria-expanded={expanded} onClick={() => setComparing(ids => expanded ? ids.filter(id => id !== message.id) : [...ids, message.id])}>{expanded ? '收起比较' : '比较建议'}</button></div>
            {proposal.reason && <p className="setting-collaboration-reason">{proposal.reason}</p>}
            {expanded && <div className="setting-collaboration-comparison">{proposal.target_document_id !== null && <section><h4>讨论时的原文</h4><p>{message.base_content || '暂无正文'}</p></section>}{stale && <section><h4>当前文稿</h4><p>{current ? current.body || '暂无正文' : '目标条目已不可用。'}</p></section>}<section><h4>建议正文</h4><p>{proposal.proposed_content}</p></section></div>}
            {message.proposal_status === 'pending' && <>{stale && <p className="setting-collaboration-stale">目标条目已更新，请基于设定集的最新内容重新讨论。</p>}<div className="setting-collaboration-proposal-actions"><button type="button" disabled={readOnly || busy || uncertain} onClick={() => { void discard(message) }}>暂不采用</button>{stale ? <button type="button" disabled={readOnly || busy} onClick={() => discussCurrent(message)}>基于最新设定集重新讨论</button> : <button type="button" disabled={readOnly || busy || !expanded || uncertain} onClick={() => { void adopt(message) }}>采纳建议</button>}</div></>}
            {uncertain && <button type="button" className="setting-collaboration-retry" disabled={readOnly || busy} onClick={() => { void adopt(message, true) }}>检查采纳结果</button>}
          </div>}
        </article>
      })}
    </div>
    {(error || attemptStatus) && <div className="setting-collaboration-feedback" role={error ? 'alert' : 'status'}><p>{error || attemptStatus}</p>{!initialLocal && !conversation && !loading && <button type="button" disabled={busy} onClick={() => { setLoading(true); setAttempt(value => value + 1) }}>重新连接</button>}</div>}
    <form className="setting-collaboration-composer" onSubmit={event => { event.preventDefault(); void send() }}>
      {feedback?.trim() && <details className="setting-collaboration-comment-context"><summary>待讨论的作者评论</summary><p>{feedback}</p></details>}
      <textarea ref={input} rows={2} aria-label="给设定 Agent 的消息" placeholder={readOnly ? '已归档，只能查看讨论' : '描述想新增或完善的设定…'} value={draft} maxLength={16000} disabled={readOnly}
        onChange={event => changeDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void send() } }} />
      <footer>{pending ? <div>
        {(!pending.sent || currentAttempt?.status === 'prepared' || currentAttempt?.status === 'result_saved') && <button type="button" className="setting-collaboration-retry" disabled={readOnly || busy || loading} onClick={() => { void recoverAttempt('resume') }}>{currentAttempt?.status === 'result_saved' ? '恢复结果' : '继续发送'}</button>}
        {pending.sent && <button type="button" className="setting-collaboration-retry" disabled={readOnly || recoveryBusy || loading} onClick={() => { void recoverAttempt('inspect') }}>检查结果</button>}
        <button type="button" className="setting-collaboration-retry" disabled={readOnly || recoveryBusy || loading || busy && !pending.sent} onClick={() => { void recoverAttempt('finish') }}>结束等待</button>
      </div> : <span role="status">{busy ? '正在处理…' : ''}</span>}<button type="submit" className="setting-collaboration-send" aria-label="发送给设定 Agent" disabled={readOnly || busy || loading || !conversation && !initialLocal || !!pending || (!draft.trim() && !feedback?.trim())}><AssistantSendIcon /></button></footer>
    </form>
    </div>
  </aside>
}
