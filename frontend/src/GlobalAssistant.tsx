import { useEffect, useLayoutEffect, useRef, useState, type ButtonHTMLAttributes } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  getOrCreateAssistantConversation,
  sendAssistantMessage,
  type AssistantMessage,
} from './api/studioAssistantClient'
import './studio.css'

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

function SendIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 56 56" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M33.0333 17.4667C32.7417 17.175 32.3208 17.0833 31.9375 17.225L17.8542 22.4125C17.4625 22.5542 17.1958 22.9167 17.175 23.3333C17.15 23.75 17.3792 24.1375 17.75 24.325L22.7792 26.8417L26.725 22.8958L27.6083 23.7792L23.6625 27.725L26.1792 32.7542C26.3542 33.1083 26.7167 33.3292 27.1083 33.3292H27.1667C27.5833 33.3042 27.9417 33.0375 28.0875 32.65L33.275 18.5625C33.4167 18.1792 33.325 17.7583 33.0333 17.4667Z" fill="currentColor" />
    </svg>
  )
}

function AssistantIconButton({ icon, label, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { icon: 'hide' | 'send'; label: string }) {
  return (
    <button
      type="button"
      data-icon={icon}
      className={`studio-icon is-round-asset ${className}`}
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
}

export default function GlobalAssistant({
  hidden = false,
  project,
  chapter,
  currentView: propCurrentView,
  preview: propPreview,
}: GlobalAssistantProps = {}) {
  const location = useLocation()
  const navigate = useNavigate()

  // Route context inference
  const projectMatch = location.pathname.match(/\/projects\/([^/]+)/)
  const chapterMatch = location.pathname.match(/\/chapters\/([^/]+)/) || location.pathname.match(/\/studio\/([^/]+)/)
  const isStudioPreview = location.pathname === '/preview/studio' || location.pathname === '/studio'

  const effectiveProjectId = project?.id || (projectMatch ? projectMatch[1] : null)
  const effectiveChapterId = chapter?.id || (chapterMatch ? chapterMatch[1] : null)
  const preview = propPreview !== undefined ? propPreview : (isStudioPreview || !effectiveProjectId)
  const isReal = !preview && Boolean(effectiveProjectId)

  const derivedView = propCurrentView || (() => {
    const p = location.pathname
    if (p === '/') return 'Dashboard'
    if (p.includes('/maintenance')) return 'Maintenance'
    if (p.includes('/creation')) return 'Creation'
    if (p.includes('/reader-panel')) return 'ReaderPanel'
    if (effectiveChapterId) return 'ChapterWorkspace'
    if (effectiveProjectId) return 'ProjectWorkspace'
    return 'General'
  })()

  // Session storage synchronization
  const [open, setOpen] = useState(() => {
    try {
      return sessionStorage.getItem('guranovel_assistant_open') === 'true'
    } catch {
      return false
    }
  })

  const [draft, setDraft] = useState(() => {
    try {
      return sessionStorage.getItem('guranovel_assistant_draft') || ''
    } catch {
      return ''
    }
  })

  const handleSetOpen = (nextOpen: boolean) => {
    setOpen(nextOpen)
    try {
      sessionStorage.setItem('guranovel_assistant_open', String(nextOpen))
    } catch {
      // ignore
    }
  }

  const retryMessageIdRef = useRef<string | null>(null)
  const lastSentTextRef = useRef<string | null>(null)

  const handleDraftChange = (text: string) => {
    if (lastSentTextRef.current !== null && text !== lastSentTextRef.current) {
      retryMessageIdRef.current = null
      lastSentTextRef.current = null
    }
    setDraft(text)
    try {
      sessionStorage.setItem('guranovel_assistant_draft', text)
    } catch {
      // ignore
    }
  }

  const [previewMessages, setPreviewMessages] = useState<string[]>([])
  const [realMessages, setRealMessages] = useState<AssistantMessage[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [pendingDraft, setPendingDraft] = useState<string | null>(null)
  const [error, setError] = useState('')

  const currentKey = isReal && open && effectiveProjectId ? `${effectiveProjectId}:${effectiveChapterId || ''}` : null
  const loading = isReal && open && currentKey !== null && loadedKey !== currentKey
  const input = useRef<HTMLTextAreaElement>(null)
  const launcher = useRef<HTMLButtonElement>(null)
  const history = useRef<HTMLDivElement>(null)
  const wasOpen = useRef(open)

  useLayoutEffect(() => {
    const previous = wasOpen.current
    wasOpen.current = open
    if (open) {
      input.current?.focus({ preventScroll: true })
      const timer = setTimeout(() => {
        input.current?.focus({ preventScroll: true })
      }, 200)
      return () => clearTimeout(timer)
    }
    if (previous) {
      launcher.current?.focus({ preventScroll: true })
      const timer = setTimeout(() => {
        launcher.current?.focus({ preventScroll: true })
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
    if (!isReal || !open || !effectiveProjectId) return
    let active = true
    const key = `${effectiveProjectId}:${effectiveChapterId || ''}`
    getOrCreateAssistantConversation(effectiveProjectId, effectiveChapterId || undefined)
      .then(conv => {
        if (!active) return
        setConversationId(conv.id)
        setRealMessages(conv.messages || [])
        setError('')
        setLoadedKey(key)
      })
      .catch(() => {
        if (!active) return
        setError('无法连接 Gura 助手服务')
        setLoadedKey(key)
      })
    return () => { active = false }
  }, [isReal, open, effectiveProjectId, effectiveChapterId])

  async function send() {
    if (!draft.trim()) return
    const text = draft.trim()
    if (!isReal || !effectiveProjectId) {
      setPreviewMessages(items => [...items, text])
      setDraft('')
      try {
        sessionStorage.setItem('guranovel_assistant_draft', '')
      } catch {
        // ignore
      }
      input.current?.focus({ preventScroll: true })
      return
    }

    if (sending) return
    setSending(true)
    setPendingDraft(text)
    setDraft('')
    try {
      sessionStorage.setItem('guranovel_assistant_draft', '')
    } catch {
      // ignore
    }
    setError('')
    try {
      let convId = conversationId
      if (!convId) {
        const conv = await getOrCreateAssistantConversation(effectiveProjectId, effectiveChapterId || undefined)
        convId = conv.id
        setConversationId(conv.id)
      }
      if (!retryMessageIdRef.current || text !== lastSentTextRef.current) {
        retryMessageIdRef.current = crypto.randomUUID()
        lastSentTextRef.current = text
      }
      const clientMessageId = retryMessageIdRef.current
      const res = await sendAssistantMessage(effectiveProjectId, convId, text, effectiveChapterId || undefined, derivedView, clientMessageId)
      setRealMessages(res.messages || [])
      retryMessageIdRef.current = null
      lastSentTextRef.current = null
    } catch {
      setError('发送失败，请稍后重试。')
      setDraft(text)
      try {
        sessionStorage.setItem('guranovel_assistant_draft', text)
      } catch {
        // ignore
      }
    } finally {
      setPendingDraft(null)
      setSending(false)
      input.current?.focus({ preventScroll: true })
    }
  }

  return (
    <aside
      className={`studio-assistant${open ? ' is-open' : ''}${hidden && !open ? ' is-hidden' : ''}`}
      aria-label="Gura 助手"
      onKeyDown={event => {
        if (open && event.key === 'Escape') {
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
        <SharkIcon />
      </button>
      <div
        id="studio-assistant-dialog"
        className="studio-assistant-dialog"
        role="dialog"
        aria-label="与 Gura 对话"
        aria-hidden={!open}
        inert={!open}
        onTransitionEnd={() => {
          if (open) input.current?.focus({ preventScroll: true })
        }}
      >
        <header>
          <AssistantIconButton icon="hide" label="收起助手" onClick={() => handleSetOpen(false)} />
          <span>Gura</span>
          <small>{!isReal ? '交互预览' : '只读助手'}</small>
        </header>
        <div className="studio-assistant-history" ref={history} role="log" aria-label="助手对话记录" tabIndex={0}>
          {!isReal ? (
            <>
              {!previewMessages.length && (
                <div className="studio-assistant-empty">
                  <SharkIcon />
                  <p>软件哪里不会用？</p>
                  <small>问我操作方法，或说说你想完成的事。</small>
                </div>
              )}
              {previewMessages.map((message, index) => (
                <div className="studio-assistant-message" key={index}>
                  <small>你 · 未发送</small>
                  <p>{message}</p>
                </div>
              ))}
            </>
          ) : (
            <>
              {loading && !realMessages.length && (
                <div className="studio-assistant-empty">
                  <p className="studio-muted">正在载入助手会话…</p>
                </div>
              )}
              {!loading && !realMessages.length && !pendingDraft && (
                <div className="studio-assistant-empty">
                  <SharkIcon />
                  <p>软件哪里不会用？</p>
                  <small>问我操作方法、大纲、审阅或还原点状态。</small>
                </div>
              )}
              {realMessages.map(msg => (
                <div className={`studio-assistant-message${msg.role === 'user' ? ' is-user' : ''}`} key={msg.id}>
                  <small>{msg.role === 'user' ? '你' : 'Gura'}</small>
                  <p>{msg.content}</p>
                  {msg.role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0 && (
                    <div className="studio-assistant-tools" aria-label="已调用的工具">
                      {msg.tool_calls.map(tool => (
                        <span key={tool.id} className="studio-assistant-tool-tag">
                          已查阅 {assistantToolLabels[tool.name] || tool.name}
                        </span>
                      ))}
                    </div>
                  )}
                  {msg.role === 'assistant' && msg.tool_results && msg.tool_results.length > 0 && (
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
              {pendingDraft && (
                <>
                  <div className="studio-assistant-message is-user">
                    <small>你</small>
                    <p>{pendingDraft}</p>
                  </div>
                  <div className="studio-assistant-message">
                    <small>Gura</small>
                    <p className="studio-muted">正在查阅工程状态并思考…</p>
                  </div>
                </>
              )}
              {error && (
                <div className="studio-assistant-message">
                  <small>系统提示</small>
                  <p className="studio-muted">{error}</p>
                </div>
              )}
            </>
          )}
        </div>
        <form className="studio-assistant-composer" onSubmit={event => { event.preventDefault(); void send() }}>
          <textarea
            ref={input}
            aria-label="给 Gura 的消息"
            aria-describedby="studio-assistant-note"
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
            <small id="studio-assistant-note">
              {!isReal ? '仅本次预览 · Agent 尚未接入' : '受限只读业务助手 · 无修改定稿权限'}
            </small>
            <AssistantIconButton
              icon="send"
              label={!isReal ? '发送给 Gura（仅预览）' : sending ? '正在思考…' : '发送给 Gura'}
              type="submit"
              disabled={!draft.trim() || sending}
            />
          </div>
        </form>
      </div>
    </aside>
  )
}
