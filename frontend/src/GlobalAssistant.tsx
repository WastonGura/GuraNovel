import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ButtonHTMLAttributes } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  getOrCreateGlobalAssistantConversation,
  getGlobalAssistantConversation,
  listGlobalAssistantConversations,
  sendGlobalAssistantMessage,
  listLegacyAssistantConversations,
  getLegacyAssistantConversation,
  type AssistantConversation,
  type AssistantConversationSummary,
  type AssistantPageContext,
  type LegacyAssistantScope,
  type AssistantMessage,
} from './api/studioAssistantClient'
import './studio.css'
import { ApiError } from './api/client'
import SendIcon from './AssistantSendIcon'
import { useAssistantResize } from './useAssistantResize'
import { assignUnassignedAssistantDraft, canonicalAssistantContext, matchesAssistantAcknowledgement, persistActiveAssistantConversation, persistAssistantCreationRequest, persistPendingAssistantMessage, readActiveAssistantConversation, readAssistantCreationRequest, readAssistantDraft, readPendingAssistantMessage, type PendingAssistantMessage } from './assistantRecovery'
import SettingActionIcon from './SettingActionIcon'
import { beginAssistantDelivery, subscribeAssistantDelivery } from './assistantDeliveries'

const assistantToolLabels: Record<string, string> = {
  get_project_summary: '工程状态概览',
  get_chapter_outline: '章节大纲',
  get_chapter_draft_summary: '正文草稿摘要',
  get_chapter_review_reports: '审阅报告与警告',
  list_chapter_restore_points: '历史还原点',
  get_software_guidance: '软件使用指南',
  create_chapter: '创建新章节',
  navigate_view: '视图导航',
  trigger_chapter_review: '发起章节审阅',
}

function SharkIcon() {
  return (
    <svg width="22" height="21" viewBox="0 0 22 21" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M16.3 12.8C14.3 9 14.8 4.2 18 1C10.6 1 4.60002 6.7 4.10002 14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M1.00003 13C1.60003 13.5 2.20003 14 3.50003 14C6.00003 14 6.00003 12 8.50003 12C11.1 12 10.9 14 13.5 14C16 14 16 12 18.5 12C19.8 12 20.4 12.5 21 13M1.00003 19C1.60003 19.5 2.20003 20 3.50003 20C6.00003 20 6.00003 18 8.50003 18C11.1 18 10.9 20 13.5 20C16 20 16 18 18.5 18C19.8 18 20.4 18.5 21 19" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function HideIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M4.425 21L3 19.575L7.6 15H5V13H11V19H9V16.4L4.425 21ZM13 11V5H15V7.6L19.575 3L21 4.425L16.4 9H19V11H13Z" fill="currentColor" />
    </svg>
  )
}


function AssistantIconButton({ icon, label, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { icon: 'hide' | 'send'; label: string }) {
  return (
    <button
      type="button"
      data-icon={icon}
      className={`studio-icon ${className}`}
      title={label}
      aria-label={label}
      {...props}
    >
      <span className="studio-icon-glyph" aria-hidden="true">
        {icon === 'hide' ? <HideIcon /> : <SendIcon />}
      </span>
    </button>
  )
}

export interface GlobalAssistantProps {
  hidden?: boolean
  project?: { id: string; title?: string } | null
  chapter?: { id?: string | null; stage?: string | null } | null
  currentView?: string | null
  preview?: boolean
  collection?: { id: string; title: string; documentId?: string; versionId?: string }
}

export default function GlobalAssistant(props: GlobalAssistantProps = {}) {
  const location = useLocation()
  const selectedProject = location.pathname === '/' ? new URLSearchParams(location.search).get('project') : null
  const queryProjectId = selectedProject && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(selectedProject) ? selectedProject : undefined
  const projectId = props.project?.id || location.pathname.match(/^\/projects\/([^/]+)(?:\/|$)/)?.[1] || queryProjectId
  const preview = /^\/preview\/studio(?:\/[^/]+)?$/.test(location.pathname)
  const [selection, setSelection] = useState<{ id: string | null; initial?: AssistantConversation; notice?: string }>(() => ({ id: readActiveAssistantConversation() }))
  const activate = useCallback((conversation: AssistantConversation | string, notice?: string) => {
    const id = typeof conversation === 'string' ? conversation : conversation.id
    persistActiveAssistantConversation(id)
    setSelection({ id, initial: typeof conversation === 'string' ? undefined : conversation, notice })
  }, [])
  return <AssistantSession key={preview ? 'preview' : selection.id || 'latest'} {...props}
    project={projectId ? { ...props.project, id: projectId } : null} preview={preview}
    activeConversationId={selection.id} initialConversation={selection.initial} initialNotice={selection.notice} onActivate={activate} />
}

interface AssistantSessionProps extends GlobalAssistantProps {
  activeConversationId: string | null
  initialConversation?: AssistantConversation
  initialNotice?: string
  onActivate: (conversation: AssistantConversation | string, notice?: string) => void
}

function AssistantSession({
  hidden = false,
  project,
  chapter,
  currentView: propCurrentView,
  preview: propPreview,
  collection,
  activeConversationId,
  initialConversation,
  initialNotice,
  onActivate,
}: AssistantSessionProps) {
  const location = useLocation()
  const navigate = useNavigate()

  const chapterMatch = location.pathname.match(/\/chapters\/([^/]+)/) || location.pathname.match(/\/studio\/([^/]+)/)
  const effectiveProjectId = project?.id
  const effectiveChapterId = chapter?.id || (chapterMatch ? chapterMatch[1] : null)
  const isReal = !propPreview
  const scope = !isReal ? 'preview' : `conversation:${activeConversationId || 'unassigned'}`
  const draftKey = `guranovel_assistant_draft:${scope}`
  const pendingKey = `guranovel_assistant_pending:${scope}`
  const [pendingRecovery] = useState(() => readPendingAssistantMessage(pendingKey, activeConversationId))

  const derivedView = propCurrentView || (() => {
    const p = location.pathname
    if (p === '/') return effectiveProjectId ? 'Detail' : 'home'
    if (p.includes('/maintenance')) return 'Maintenance'
    if (p.includes('/creation')) return 'Creation'
    if (p.includes('/reader-panel')) return 'ReaderPanel'
    if (effectiveChapterId) return 'ChapterWorkspace'
    if (effectiveProjectId) return 'ProjectWorkspace'
    return 'General'
  })()
  const pageContext: AssistantPageContext = canonicalAssistantContext(collection ? {
    kind: 'collection', collection_id: collection.id,
    ...(collection.documentId ? { document_id: collection.documentId } : {}),
    ...(collection.versionId ? { document_version_id: collection.versionId } : {}),
  } : effectiveProjectId ? {
    kind: 'project', project_id: effectiveProjectId,
    ...(effectiveChapterId ? { chapter_id: effectiveChapterId } : {}), current_view: derivedView,
  } : { kind: 'home', current_view: 'home' })
  const legacyScope: LegacyAssistantScope | null = collection ? { collection_id: collection.id }
    : effectiveProjectId ? { project_id: effectiveProjectId } : null
  const legacyKey = legacyScope ? JSON.stringify(legacyScope) : ''

  // Session storage synchronization
  const [open, setOpen] = useState(() => {
    try {
      return sessionStorage.getItem('guranovel_assistant_open') === 'true'
    } catch {
      return false
    }
  })
  const resize = useAssistantResize({ enabled: open })
  const wideOverlay = resize.width > window.innerWidth * .45
  const expandedOverlay = window.innerWidth <= 1000
    ? resize.height > Math.max(resize.minHeight, window.innerHeight * .56) || wideOverlay
    : wideOverlay
  useLayoutEffect(() => {
    const root = document.documentElement.style
    const previousWidth = root.getPropertyValue('--assistant-width'), previousHeight = root.getPropertyValue('--assistant-height')
    root.setProperty('--assistant-width', `${resize.width}px`)
    root.setProperty('--assistant-height', `${resize.height}px`)
    return () => {
      if (previousWidth) root.setProperty('--assistant-width', previousWidth); else root.removeProperty('--assistant-width')
      if (previousHeight) root.setProperty('--assistant-height', previousHeight); else root.removeProperty('--assistant-height')
    }
  }, [resize.width, resize.height])
  useLayoutEffect(() => {
    document.documentElement.toggleAttribute('data-assistant-resizing', resize.isResizing)
    return () => document.documentElement.removeAttribute('data-assistant-resizing')
  }, [resize.isResizing])

  const [initialDraft] = useState(() => readAssistantDraft(draftKey, pendingRecovery))
  const [draft, setDraft] = useState(initialDraft.text)
  const draftRecovered = useRef(initialDraft.recovered)

  const handleSetOpen = (nextOpen: boolean) => {
    setOpen(nextOpen)
    try {
      sessionStorage.setItem('guranovel_assistant_open', String(nextOpen))
    } catch {
      // ignore
    }
  }

  const [pending, setPending] = useState<PendingAssistantMessage | null>(pendingRecovery)
  const pendingRef = useRef(pendingRecovery)
  const sendingRef = useRef(false)
  const mounted = useRef(true)
  const draftRef = useRef(draft)
  const draftRevision = useRef(0)
  const openRef = useRef(open)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const updateDraft = useCallback((text: string, pendingOrigin?: string) => {
    draftRef.current = text
    draftRevision.current++
    draftRecovered.current = false
    setDraft(text)
    try {
      sessionStorage.setItem(draftKey, text)
      if (pendingOrigin) sessionStorage.setItem(`${draftKey}:origin`, pendingOrigin)
      else sessionStorage.removeItem(`${draftKey}:origin`)
    } catch { /* The live draft remains available. */ }
  }, [draftKey])

  const handleDraftChange = (text: string) => {
    updateDraft(text)
  }

  const [previewMessages, setPreviewMessages] = useState<string[]>(() => {
    try { const items = JSON.parse(sessionStorage.getItem('guranovel_assistant_preview_messages') || '[]'); return Array.isArray(items) ? items.filter(item => typeof item === 'string') : [] } catch { return [] }
  })
  const [realMessages, setRealMessages] = useState<AssistantMessage[]>(initialConversation?.messages || [])
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  const initialConsumed = useRef(false)
  const [notice] = useState(initialNotice)
  const [sending, setSending] = useState(false)
  const [pendingDraft, setPendingDraft] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [loadFailed, setLoadFailed] = useState(false)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyItems, setHistoryItems] = useState<AssistantConversationSummary[]>([])
  const [legacyItems, setLegacyItems] = useState<AssistantConversationSummary[]>([])
  const [loadedHistoryKey, setLoadedHistoryKey] = useState('')
  const [historyError, setHistoryError] = useState('')
  const [legacyError, setLegacyError] = useState('')
  const [historyAttempt, setHistoryAttempt] = useState(0)
  const [legacySelection, setLegacySelection] = useState<{ id: string; key: string } | null>(null)
  const [legacyConversation, setLegacyConversation] = useState<AssistantConversation | null>(null)
  const [loadedLegacyKey, setLoadedLegacyKey] = useState('')
  const [creating, setCreating] = useState(false)
  const creatingRef = useRef(false)
  const creationKey = `guranovel_assistant_new_conversation_request:${activeConversationId || 'unassigned'}`
  const creationId = useRef(readAssistantCreationRequest(creationKey))
  const viewingLegacy = Boolean(legacySelection && legacySelection.key === legacyKey)
  const historyRequestKey = `${historyAttempt}:${legacyKey}`
  const historyLoading = historyOpen && loadedHistoryKey !== historyRequestKey
  const legacyRequestKey = `${legacyKey}:${legacySelection?.id || ''}`
  const legacyLoading = viewingLegacy && loadedLegacyKey !== legacyRequestKey

  const currentKey = isReal && open ? scope : null
  const loading = isReal && open && currentKey !== null && loadedKey !== currentKey
  const input = useRef<HTMLTextAreaElement>(null)
  const launcher = useRef<HTMLButtonElement>(null)
  const history = useRef<HTMLDivElement>(null)
  const wasOpen = useRef(open)
  const wasBrowsing = useRef(false)
  const deliveryVersion = useRef(0)
  const lastDeliverySucceeded = useRef(false)
  const sentRevision = useRef(0)
  const displayMessages = viewingLegacy
    ? !legacyLoading && legacyConversation && legacyConversation.id === legacySelection?.id ? legacyConversation.messages : []
    : realMessages

  useLayoutEffect(() => {
    if (wasBrowsing.current && !historyOpen && !viewingLegacy && open) input.current?.focus({ preventScroll: true })
    wasBrowsing.current = historyOpen || viewingLegacy
  }, [historyOpen, viewingLegacy, open])

  useLayoutEffect(() => {
    openRef.current = open
    const previous = wasOpen.current
    wasOpen.current = open
    if (open) {
      input.current?.focus({ preventScroll: true })
      const openingFocus = document.activeElement
      const timer = setTimeout(() => {
        if (document.activeElement === openingFocus || document.activeElement === document.body) input.current?.focus({ preventScroll: true })
      }, 200)
      return () => clearTimeout(timer)
    }
    if (previous) {
      launcher.current?.focus({ preventScroll: true })
      const closingFocus = document.activeElement
      const timer = setTimeout(() => {
        // Respect a control the author focused after closing the assistant.
        if (document.activeElement === closingFocus || document.activeElement === document.body) launcher.current?.focus({ preventScroll: true })
      }, 50)
      return () => clearTimeout(timer)
    }
  }, [open])

  useLayoutEffect(() => {
    const textarea = input.current
    if (!textarea) return
    const resize = () => { textarea.style.height = '0px'; textarea.style.height = `${textarea.scrollHeight}px` }
    resize()
    let width = textarea.clientWidth
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => {
      if (width !== textarea.clientWidth) { width = textarea.clientWidth; resize() }
    })
    observer?.observe(textarea)
    return () => observer?.disconnect()
  }, [draft])

  useLayoutEffect(() => {
    const list = history.current
    if (list) list.scrollTop = list.scrollHeight
  }, [previewMessages, realMessages, pendingDraft, open])

  useEffect(() => {
    if (!isReal || !activeConversationId) return
    return subscribeAssistantDelivery(activeConversationId, delivery => {
      const request = delivery.request
      if (pendingRef.current?.id !== request.id) return
      if (delivery.status === 'sending') {
        sendingRef.current = true
        setSending(true)
        setPendingDraft(request.text)
        if (draftRecovered.current && draftRef.current === request.text) updateDraft('', request.id)
        sentRevision.current = draftRevision.current
        return
      }
      deliveryVersion.current++
      lastDeliverySucceeded.current = delivery.status === 'delivered'
      sendingRef.current = false
      setSending(false)
      setPendingDraft(null)
      const acknowledged = delivery.status === 'delivered'
      const rejectedVersion = delivery.status === 'failed' && request.context.kind === 'collection'
        && delivery.cause instanceof ApiError && delivery.cause.status === 409 && delivery.cause.code !== 'assistant_exchange_unconfirmed'
      if (acknowledged || rejectedVersion) {
        pendingRef.current = null
        setPending(null)
        try {
          if (readPendingAssistantMessage(pendingKey, activeConversationId)?.id === request.id) sessionStorage.removeItem(pendingKey)
        } catch { /* Reconcile the exact acknowledged request on the next load. */ }
      }
      if (delivery.status === 'delivered') {
        setRealMessages(delivery.conversation.messages || [])
        setLoadedKey(scope)
        setLoadFailed(false)
        setError('')
        if (draftRecovered.current && draftRef.current === request.text) updateDraft('')
        try { if (sessionStorage.getItem(`${draftKey}:origin`) === request.id) sessionStorage.removeItem(`${draftKey}:origin`) } catch { /* The confirmed journal has already been removed. */ }
      } else {
        setError(delivery.cause instanceof ApiError ? delivery.cause.message : '发送失败，请稍后重试。')
        if (draftRevision.current === sentRevision.current && !draftRef.current) updateDraft(request.text, request.id)
      }
      const focused = document.activeElement
      if (openRef.current && (focused === input.current || focused?.closest('.studio-assistant-composer'))) input.current?.focus({ preventScroll: true })
    })
  }, [isReal, activeConversationId, pendingKey, draftKey, scope, updateDraft])

  useEffect(() => {
    if (!isReal || !open) return
    let active = true
    const revision = draftRevision.current
    const deliveryAtLoad = deliveryVersion.current
    const load = initialConversation && !initialConsumed.current ? Promise.resolve(initialConversation)
      : activeConversationId ? getGlobalAssistantConversation(activeConversationId) : getOrCreateGlobalAssistantConversation()
    initialConsumed.current = true
    load
      .then(conv => {
        if (!active) return
        if (!activeConversationId) {
          const merged = assignUnassignedAssistantDraft(draftKey, conv.id, draftRef.current)
          onActivate(conv, merged ? '此前暂存的草稿已合并，请核对后发送。' : undefined)
          return
        }
        if (conv.id !== activeConversationId) throw new Error('会话响应与当前对话不一致。')
        if (deliveryAtLoad !== deliveryVersion.current) {
          setLoadedKey(scope)
          setLoadFailed(false)
          if (!lastDeliverySucceeded.current) setRealMessages(conv.messages || [])
          return
        }
        setRealMessages(conv.messages || [])
        const recovery = pendingRef.current
        if (recovery && conv.messages.some(message => matchesAssistantAcknowledgement(message, recovery))
          && pendingRef.current?.id === recovery.id) {
          pendingRef.current = null
          setPending(null)
          try {
            if (readPendingAssistantMessage(pendingKey, activeConversationId)?.id === recovery.id) sessionStorage.removeItem(pendingKey)
            if (draftRevision.current === revision && draftRecovered.current && draftRef.current === recovery.text) {
              draftRef.current = ''
              draftRecovered.current = false
              setDraft('')
              sessionStorage.setItem(draftKey, '')
            }
            if (sessionStorage.getItem(`${draftKey}:origin`) === recovery.id) sessionStorage.removeItem(`${draftKey}:origin`)
          } catch { /* The live conversation still shows the acknowledged message. */ }
        }
        setError('')
        setLoadFailed(false)
        setLoadedKey(scope)
      })
      .catch(cause => {
        if (!active || (deliveryAtLoad !== deliveryVersion.current && lastDeliverySucceeded.current)) return
        setError(cause instanceof Error && cause.message.startsWith('浏览器') ? cause.message : '无法连接 Gura 助手服务')
        setLoadFailed(true)
        setLoadedKey(scope)
      })
    return () => { active = false }
  }, [isReal, open, activeConversationId, initialConversation, onActivate, scope, pendingKey, draftKey, loadAttempt])

  useEffect(() => {
    if (!historyOpen || !isReal) return
    let active = true
    const prior = legacyKey ? listLegacyAssistantConversations(JSON.parse(legacyKey) as LegacyAssistantScope) : Promise.resolve([])
    Promise.allSettled([listGlobalAssistantConversations(), prior]).then(([current, earlier]) => {
      if (!active) return
      setHistoryItems(current.status === 'fulfilled' ? current.value : [])
      setHistoryError(current.status === 'fulfilled' ? '' : '无法载入历史对话，请稍后重试。')
      setLegacyItems(earlier.status === 'fulfilled' ? earlier.value : [])
      setLegacyError(earlier.status === 'fulfilled' ? '' : '无法载入早期页面对话，请稍后重试。')
      setLoadedHistoryKey(historyRequestKey)
    })
    return () => { active = false }
  }, [historyOpen, historyAttempt, historyRequestKey, isReal, legacyKey])

  useEffect(() => {
    if (!legacySelection || legacySelection.key !== legacyKey) return
    let active = true
    getLegacyAssistantConversation(JSON.parse(legacyKey) as LegacyAssistantScope, legacySelection.id).then(conversation => {
      if (active) { setLegacyConversation(conversation); setLegacyError(''); setLoadedLegacyKey(legacyRequestKey) }
    }).catch(() => { if (active) { setLegacyConversation(null); setLegacyError('无法载入早期页面对话，请稍后重试。'); setLoadedLegacyKey(legacyRequestKey) } })
    return () => { active = false }
  }, [legacySelection, legacyKey, legacyRequestKey])

  async function newConversation() {
    if (creatingRef.current || !isReal || loading) return
    if (!creationId.current && !realMessages.length && !pendingRef.current && !draftRef.current.trim() && !loadFailed && activeConversationId) {
      setHistoryOpen(false)
      setLegacySelection(null)
      input.current?.focus({ preventScroll: true })
      return
    }
    creatingRef.current = true
    setCreating(true)
    setError('')
    try {
      const id = creationId.current || crypto.randomUUID()
      persistAssistantCreationRequest(creationKey, id)
      creationId.current = id
      const conversation = await getOrCreateGlobalAssistantConversation(true, id)
      if (!mounted.current) return
      if (conversation.id !== id) throw new Error('新建对话的响应不匹配，请重试；原对话和草稿已保留。')
      onActivate(conversation)
      if (sessionStorage.getItem(creationKey) === id) sessionStorage.removeItem(creationKey)
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error && (cause.message.startsWith('浏览器') || cause.message.startsWith('新建对话的响应'))
        ? cause.message : '新建对话失败，请稍后重试；原对话和草稿已保留。')
    } finally {
      creatingRef.current = false
      if (mounted.current) setCreating(false)
    }
  }

  async function send(retryOriginal = false) {
    if (loading || loadFailed || sendingRef.current || creatingRef.current || historyOpen || viewingLegacy) return
    const original = pendingRef.current
    const text = original ? original.text : draftRef.current.trim()
    if (!text || original && !retryOriginal && draftRef.current.trim() !== original.text) return
    if (!isReal) {
      const items = [...previewMessages, text]
      setPreviewMessages(items)
      try { sessionStorage.setItem('guranovel_assistant_preview_messages', JSON.stringify(items)) } catch { /* Preview remains visibly unsent. */ }
      updateDraft('')
      input.current?.focus({ preventScroll: true })
      return
    }

    if (!activeConversationId) return
    const request: PendingAssistantMessage = original || {
      id: crypto.randomUUID(), text, conversationId: activeConversationId, context: { ...pageContext },
    }
    // Persist the complete original request before clearing text or starting any network operation.
    try {
      persistPendingAssistantMessage(pendingKey, request)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法保留消息，请稍后重试。')
      return
    }
    pendingRef.current = request
    setPending(request)
    sendingRef.current = true
    setSending(true)
    setPendingDraft(text)
    if (draftRef.current.trim() === text) updateDraft('', request.id)
    sentRevision.current = draftRevision.current
    setError('')
    await beginAssistantDelivery(request, () => sendGlobalAssistantMessage(request.conversationId, text, request.id, request.context))
  }

  return (
    <aside
      className={`studio-assistant${open ? ' is-open' : ''}${resize.isResizing ? ' is-resizing' : ''}${expandedOverlay ? ' is-expanded-overlay' : ''}${wideOverlay ? ' is-wide' : ''}${hidden && !open ? ' is-hidden' : ''}`}
      style={resize.style}
      aria-label="Gura 助手"
      onKeyDown={event => {
        if (open && event.key === 'Escape' && !resize.isResizing) {
          event.preventDefault()
          event.stopPropagation()
          handleSetOpen(false)
        }
      }}
    >
      <button
        ref={launcher}
        type="button"
        className="studio-assistant-launcher"
        aria-label="Gura"
        aria-expanded={open}
        aria-controls="studio-assistant-dialog"
        inert={open}
        onClick={() => handleSetOpen(true)}
      >
        <img className="gura-launcher-circle" src="/ui/studio/gura-circle.svg" alt="" />
        <span className="gura-launcher-icon" aria-hidden="true"><img className="gura-shark" src="/ui/studio/gura-shark.svg" alt="" /></span>
      </button>
      <div
        id="studio-assistant-dialog"
        className="studio-assistant-dialog"
        role="dialog"
        aria-label="与 Gura 对话"
        aria-hidden={!open}
        inert={!open}
        onTransitionEnd={event => {
          if (open && event.target === event.currentTarget && event.propertyName === 'opacity'
            && (document.activeElement === document.body || document.activeElement === launcher.current)) input.current?.focus({ preventScroll: true })
        }}
      >
        <header>
          <AssistantIconButton icon="hide" label="收起助手" onClick={() => handleSetOpen(false)} />
          <span>Gura</span>
          {!isReal && <small>交互预览</small>}
          {isReal && <div className="studio-assistant-header-actions">
            <button type="button" className="studio-icon setting-icon-button" data-icon="history" title="历史对话" aria-label="历史对话" aria-pressed={historyOpen || viewingLegacy}
              onClick={() => {
                const next = !(historyOpen || viewingLegacy)
                setLegacySelection(null)
                setHistoryOpen(next)
                if (next) setHistoryAttempt(value => value + 1)
              }}><SettingActionIcon name="history" /></button>
            <button type="button" className="studio-icon setting-icon-button" data-icon="create" title="新建对话" aria-label="新建对话" disabled={creating || loading}
              onClick={() => void newConversation()}><SettingActionIcon name="create" /></button>
          </div>}
        </header>
        {isReal && historyOpen && <section className="studio-assistant-history-pane" role="region" aria-label="历史对话">
          <h2>历史对话</h2>
          {historyLoading && <p className="studio-assistant-session-status" role="status">正在载入历史对话…</p>}
          {!historyLoading && historyError && <p className="studio-assistant-session-status" role="alert">{historyError}</p>}
          {!historyLoading && (historyError || legacyError) && <button type="button" className="studio-assistant-action-btn" onClick={() => setHistoryAttempt(value => value + 1)}>重试载入历史对话</button>}
          {!historyLoading && !historyError && !historyItems.length && <p className="studio-assistant-session-status">新的对话会显示在这里。</p>}
          <div className="studio-assistant-history-list">
            {!historyLoading && historyItems.map(item => <button type="button" className={`studio-assistant-history-item${item.id === activeConversationId ? ' is-current' : ''}`}
              key={item.id} aria-label={`继续对话：${item.title || '新对话'}`} aria-current={item.id === activeConversationId ? 'true' : undefined}
              onClick={() => { try { if (item.id === activeConversationId) { setHistoryOpen(false); setLegacySelection(null) } else onActivate(item.id) } catch (cause) { setHistoryError(cause instanceof Error ? cause.message : '无法打开对话。') } }}>
              <span className="studio-assistant-history-title">{item.title || '新对话'}{item.id === activeConversationId && <small>当前</small>}</span>
              <time className="studio-assistant-history-date" dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleString('zh-CN')}</time>
              {item.preview && <span className="studio-assistant-history-preview">{item.preview}</span>}
            </button>)}
          {!historyLoading && legacyKey && (legacyItems.length > 0 || legacyError) && <div className="studio-assistant-legacy-history">
            <h3 className="studio-assistant-history-group">早期页面对话</h3>
            {legacyError && <p className="studio-assistant-session-status" role="alert">{legacyError}</p>}
            <div>{legacyItems.map(item => <button type="button" className="studio-assistant-history-item" key={item.id}
              aria-label={`查看早期对话：${item.title || '早期对话'}`} onClick={() => { setLegacySelection({ id: item.id, key: legacyKey }); setHistoryOpen(false) }}>
              <span className="studio-assistant-history-title">{item.title || '早期对话'}<small>只读</small></span>
              <time className="studio-assistant-history-date" dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleString('zh-CN')}</time>
              {item.preview && <span className="studio-assistant-history-preview">{item.preview}</span>}
            </button>)}</div>
          </div>}
          </div>
        </section>}
        {(!historyOpen || !isReal) && <div className="studio-assistant-history" ref={history} role="log" aria-label="助手对话记录" tabIndex={0}>
          {!isReal ? (
            <>
              {!previewMessages.length && (
                <div className="studio-assistant-empty">
                  <SharkIcon />
                  <p>软件哪里不会用？</p>
                </div>
              )}
              {previewMessages.map((message, index) => (
                <div className="studio-assistant-message is-user" key={index}>
                  <small>你 · 未发送</small>
                  <p>{message}</p>
                </div>
              ))}
            </>
          ) : (
            <>
              {(viewingLegacy ? legacyLoading : loading) && !displayMessages.length && (
                <div className="studio-assistant-empty">
                  <p className="studio-muted">正在载入助手会话…</p>
                </div>
              )}
              {!viewingLegacy && !loading && !loadFailed && !realMessages.length && !pendingDraft && (
                <div className="studio-assistant-empty">
                  <SharkIcon />
                  <p>软件哪里不会用？</p>
                </div>
              )}
              {displayMessages.map(msg => (
                <div className={`studio-assistant-message${msg.role === 'user' ? ' is-user' : ''}`} key={msg.id}>
                  <small>{msg.role === 'user' ? '你' : 'Gura'}</small>
                  <p>{msg.content}</p>
                  {msg.role === 'assistant' && msg.tool_calls?.some(tool => 'name' in tool) && (
                    <div className="studio-assistant-tools" aria-label="已调用的工具">
                      {msg.tool_calls.filter(tool => 'name' in tool).map(tool => (
                        <span key={tool.id} className="studio-assistant-tool-tag">
                          已查阅 {assistantToolLabels[tool.name] || tool.name}
                        </span>
                      ))}
                    </div>
                  )}
                  {!viewingLegacy && msg.role === 'assistant' && msg.tool_results && msg.tool_results.length > 0 && (
                    <div className="studio-assistant-action" aria-label="快捷操作">
                      {msg.tool_results.map((tr, idx) => {
                        const targetUrl = (tr.result?.target_url || tr.result?.redirect_url) as string | undefined
                        if (!targetUrl) return null
                        let actionLabel = '点击前往'
                        if (tr.result?.label) {
                          actionLabel = `前往「${tr.result.label}」`
                        } else if (tr.result?.action === 'create_chapter') {
                          actionLabel = `前往第 ${tr.result.chapter_number ?? ''} 章`
                        } else if (tr.result?.action === 'trigger_review') {
                          actionLabel = '前往审阅工作台'
                        }
                        return (
                          <button
                            key={idx}
                            type="button"
                            className="studio-assistant-action-btn"
                            onClick={() => navigate(targetUrl)}
                          >
                            👉 {actionLabel}
                          </button>
                        )
                      })}
                    </div>
                  )}
                </div>
              ))}
              {!viewingLegacy && pendingDraft && (
                <>
                  <div className="studio-assistant-message is-user">
                    <small>你</small>
                    <p>{pendingDraft}</p>
                  </div>
                  <div className="studio-assistant-message">
                    <small>Gura</small>
                    <p className="studio-muted">{collection ? '正在查阅设定并思考…' : effectiveProjectId ? '正在查阅工程状态并思考…' : '正在查阅软件用法…'}</p>
                  </div>
                </>
              )}
              {(viewingLegacy ? legacyError : error) && (
                <div className="studio-assistant-message">
                  <small>系统提示</small>
                  <p className="studio-muted">{viewingLegacy ? legacyError : error}</p>
                  {!viewingLegacy && loadFailed && <button type="button" className="studio-assistant-action-btn" onClick={() => { setLoadedKey(null); setLoadFailed(false); setError(''); setLoadAttempt(value => value + 1) }}>重新载入会话</button>}
                </div>
              )}
              {!viewingLegacy && pending && !sending && !loading && !loadFailed && (
                <div className="studio-assistant-message">
                  <small>系统提示</small>
                  <p className="studio-muted">上一条消息尚未确认：{pending.text}</p>
                  <button type="button" className="studio-assistant-action-btn" onClick={() => void send(true)}>重试未确认的消息</button>
                </div>
              )}
              {!viewingLegacy && notice && <p className="studio-assistant-session-status">{notice}</p>}
            </>
          )}
        </div>}
        {viewingLegacy && !historyOpen && <div className="studio-assistant-legacy-footer"><span>早期页面对话 · 只读</span></div>}
        {creating && <p className="studio-assistant-session-status" role="status">正在新建对话…</p>}
        {historyOpen && error && <p className="studio-assistant-session-status" role="alert">{error}</p>}
        {!historyOpen && !viewingLegacy && <form className="studio-assistant-composer" onSubmit={event => { event.preventDefault(); void send() }}>
          <textarea
            ref={input}
            aria-label="给 Gura 的消息"
            aria-describedby={!isReal ? 'studio-assistant-note' : undefined}
            placeholder="询问用法，或描述要完成的操作…"
            rows={1}
            value={draft}
            onChange={event => handleDraftChange(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
                event.preventDefault()
                void send()
              }
            }}
          />
          <div>
            {!isReal && <small id="studio-assistant-note">预览消息不会发送</small>}
            <AssistantIconButton
              icon="send"
              label={!isReal ? '发送给 Gura（仅预览）' : sending ? '正在思考…' : '发送给 Gura'}
              type="submit"
              disabled={!draft.trim() || sending || creating || loading || loadFailed || Boolean(pending && draft.trim() !== pending.text)}
            />
          </div>
        </form>}
      </div>
      {open && <><div className="studio-assistant-resize is-left" {...resize.getHandleProps('left')} /><div className="studio-assistant-resize is-top" {...resize.getHandleProps('top')} /><div className="studio-assistant-resize is-corner" {...resize.getHandleProps('corner')}><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2 8 8 2M2 3 3 2" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg></div></>}
    </aside>
  )
}
