import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ApiError } from './api/client'
import { acceptSettingCollectionCollaborationProposal, discardSettingCollectionCollaborationProposal, getOrCreateSettingCollectionCollaboration, sendSettingCollectionCollaborationMessage,
  type SettingCollaboration, type SettingCollaborationAcceptance, type SettingCollaborationMessage, type SettingCollectionCollaborationSend } from './api/settingCollaborationClient'
import type { SettingNote } from './settingNotes'
import { StreamText } from './StudioMotion'
import { useDialogMotion } from './useDialogMotion'
import './SettingCollaborationPanel.css'
import AssistantSendIcon from './AssistantSendIcon'

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

type PendingSend = { id: string; text: string; draft: string; feedback?: string; payload?: SettingCollectionCollaborationSend }
type PendingAcceptance = { proposalId: string; base: SettingNote | null }
type Recovery = { draft: string; pending: PendingSend | null; acceptance: PendingAcceptance | null; scrollTop: number }
const recoveryKey = (collectionId: string) => `guranovel:setting-agent:collection:${collectionId}`
function recover(key: string): Recovery {
  const empty: Recovery = { draft: '', pending: null, acceptance: null, scrollTop: 0 }
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
    return { draft, pending, acceptance: validAcceptance ? acceptance : null, scrollTop: Number.isFinite(value.scrollTop) && value.scrollTop >= 0 ? value.scrollTop : 0 }
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

export default function SettingCollaborationPanel(props: SettingCollaborationPanelProps) {
  return <CollaborationSession key={props.collectionId} {...props} />
}

function CollaborationSession({ collectionId, notes, feedback, readOnly = false, onPrepare, onApply, onClose, onSent }: SettingCollaborationPanelProps) {
  const [initial] = useState(() => recover(recoveryKey(collectionId)))
  const key = useRef(recoveryKey(collectionId)), notesRef = useRef(notes)
  useLayoutEffect(() => { notesRef.current = notes }, [notes])
  const draftRef = useRef(initial.draft), pendingRef = useRef(initial.pending), acceptanceRef = useRef(initial.acceptance), scrollRef = useRef(initial.scrollTop)
  const [draft, setDraft] = useState(initial.draft), [pending, setPending] = useState(initial.pending), [acceptance, setAcceptance] = useState(initial.acceptance)
  const [conversation, setConversation] = useState<SettingCollaboration | null>(null)
  const [loading, setLoading] = useState(true), [attempt, setAttempt] = useState(0)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [comparing, setComparing] = useState<string[]>([]), [staleIds, setStaleIds] = useState<string[]>([])
  const busyRef = useRef(false), live = useRef(true), input = useRef<HTMLTextAreaElement>(null), history = useRef<HTMLDivElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null), restoreFocus = useRef(false), firstHistory = useRef(true)
  const sentCallback = useRef(onSent)
  useLayoutEffect(() => { sentCallback.current = onSent }, [onSent])
  const { closing, requestClose, onAnimationEnd } = useDialogMotion(onClose, undefined, false, 'setting-collaboration-out')
  const agentName = '设定集 Agent'
  const hasMessages = Boolean(conversation?.messages.length)
  const persist = useCallback(() => {
    try { sessionStorage.setItem(key.current, JSON.stringify({ draft: draftRef.current, pending: pendingRef.current, acceptance: acceptanceRef.current, scrollTop: scrollRef.current })); return true }
    catch { return false }
  }, [])
  const receive = useCallback((result: SettingCollaboration) => {
    setConversation(result)
    const request = pendingRef.current
    if (request && result.messages.some(message => message.role === 'user' && message.client_message_id === request.id)) {
      if (draftRef.current === request.draft) { draftRef.current = ''; setDraft('') }
      pendingRef.current = null; setPending(null); persist()
      if (request.feedback) sentCallback.current?.(request.feedback)
    }
  }, [persist])
  useEffect(() => {
    live.current = true
    return () => { live.current = false; if (restoreFocus.current && returnFocus.current?.isConnected) returnFocus.current.focus({ preventScroll: true }) }
  }, [])
  useLayoutEffect(() => { returnFocus.current = document.activeElement as HTMLElement | null; input.current?.focus({ preventScroll: true }) }, [])
  useEffect(() => {
    let active = true
    void getOrCreateSettingCollectionCollaboration(collectionId).then(result => {
      if (!active) return
      receive(result); setError(''); setLoading(false)
    }).catch(problem => { if (active) { setError(errorMessage(problem)); setLoading(false) } })
    return () => { active = false }
  }, [collectionId, attempt, receive])
  useLayoutEffect(() => {
    if (!conversation || !history.current) return
    history.current.scrollTop = firstHistory.current ? scrollRef.current : history.current.scrollHeight
    firstHistory.current = false
  }, [conversation])

  function changeDraft(text: string) { draftRef.current = text; setDraft(text); persist() }
  function finishBusy() { busyRef.current = false; if (live.current) setBusy(false) }
  function close() { restoreFocus.current = true; persist(); void requestClose() }
  async function prepareNotes() {
    try { return await onPrepare() }
    catch { throw new ApiError(0, 'setting_prepare_failed', '设定集草稿未能保存，请先处理保存问题后重试。') }
  }
  async function send(retry = false) {
    if (busyRef.current || readOnly || closing || loading || (!retry && pendingRef.current)) return
    const text = [draftRef.current.trim(), feedback?.trim() ? `作者评论：\n${feedback.trim()}` : ''].filter(Boolean).join('\n\n')
    const request: PendingSend = retry && pendingRef.current ? pendingRef.current : { id: crypto.randomUUID(), text, draft: draftRef.current, ...(feedback?.trim() ? { feedback } : {}) }
    if (!request.text) return
    if (request.text.length > 16000) { setError('消息与评论合计不能超过 16000 字。'); return }
    busyRef.current = true; setBusy(true); setError('')
    pendingRef.current = request; setPending(request)
    if (!persist()) { setError('待发送消息无法暂存，请检查浏览器存储后重试。'); finishBusy(); return }
    try {
      if (!request.payload) {
        if (!await prepareNotes()) throw new ApiError(0, 'setting_prepare_failed', '设定集草稿未能保存，请先处理保存问题后重试。')
        request.payload = { client_message_id: request.id, content: request.text }
        if (!persist()) throw new ApiError(0, 'setting_recovery_failed', '待发送消息无法暂存，请检查浏览器存储后重试。')
      }
      const current = await getOrCreateSettingCollectionCollaboration(collectionId)
      if (current.messages.some(message => message.role === 'user' && message.client_message_id === request.id)) {
        if (live.current) receive(current)
        return
      }
      const result = await sendSettingCollectionCollaborationMessage(collectionId, request.payload)
      if (live.current) receive(result)
    } catch (problem) {
      if (!live.current) return
      if (problem instanceof ApiError && problem.status === 409) { pendingRef.current = null; setPending(null); persist() }
      setError(errorMessage(problem))
    } finally { finishBusy() }
  }
  async function adopt(message: SettingCollaborationMessage, retry = false) {
    if (busyRef.current || readOnly || !message.proposal || closing) return
    busyRef.current = true; setBusy(true); setError('')
    try {
      const checking = retry && acceptanceRef.current?.proposalId === message.proposal.id
      if (!checking && !await prepareNotes()) { setError('设定集草稿未能保存，请重试。'); return }
      const base = checking ? acceptanceRef.current?.base || undefined : targetNote(notesRef.current, message)
      if (!retry && !sameBase(base, message)) {
        setStaleIds(ids => [...new Set([...ids, message.id])]); setComparing(ids => [...new Set([...ids, message.id])])
        setError('目标条目的正文或标题已更新，已保留当前文稿和建议。请基于设定集的最新内容重新讨论。'); return
      }
      acceptanceRef.current = { proposalId: message.proposal.id, base: base || null }; setAcceptance(acceptanceRef.current)
      if (!persist()) { setError('采纳请求无法暂存，请检查浏览器存储后重试。'); return }
      const result = await acceptSettingCollectionCollaborationProposal(collectionId, message.proposal.id, base?.versionId || null, base?.body || '', base?.title || '')
      if (!live.current) return
      if (result.document.setting_collection_id !== collectionId || message.proposal.target_document_id && result.document.id !== message.proposal.target_document_id) {
        throw new ApiError(0, 'invalid_response', '采纳结果与目标条目不一致，文稿已保留，请检查采纳结果。')
      }
      receive(result.conversation)
      const applied = await onApply(result, base)
      if (!live.current) return
      acceptanceRef.current = null; setAcceptance(null); persist()
      if (!applied) setError('建议已在服务器采纳；你刚输入的文稿已保留，请重新比较后加载已采纳版本。')
    } catch (problem) {
      if (!live.current) return
      if (problem instanceof ApiError && problem.status === 409) { acceptanceRef.current = null; setAcceptance(null); persist() }
      setError(errorMessage(problem))
    } finally { finishBusy() }
  }
  async function discard(message: SettingCollaborationMessage) {
    if (busyRef.current || readOnly || !message.proposal) return
    busyRef.current = true; setBusy(true); setError('')
    try { const result = await discardSettingCollectionCollaborationProposal(collectionId, message.proposal.id); if (live.current) receive(result) }
    catch (problem) { if (live.current) setError(errorMessage(problem)) }
    finally { finishBusy() }
  }
  function discussCurrent(message: SettingCollaborationMessage) {
    changeDraft(draftRef.current.trim() || `请基于设定集的最新内容重新比较「${message.proposal?.title}」的上一条建议，保留我新写的内容，再提出修改建议。`)
    input.current?.focus({ preventScroll: true })
  }

  return <aside className={`setting-collaboration-panel${!hasMessages && !loading ? ' is-empty' : ''}${closing ? ' is-closing' : ''}`} aria-label="设定 Agent 协作" inert={closing} onAnimationEnd={onAnimationEnd}
    onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close() } }}>
    <header><button type="button" aria-label="关闭设定协作" onClick={close}>返回文稿</button></header>
    <div className="setting-collaboration-history" ref={history} role="log" aria-label="设定 Agent 讨论记录" tabIndex={0} onScroll={event => { scrollRef.current = event.currentTarget.scrollTop; persist() }}>
      {loading ? <p className="setting-collaboration-empty" role="status">正在读取讨论…</p> : !hasMessages && <div className="setting-collaboration-empty"><h3>一起构建这个世界</h3><p>从角色、地点或世界规则开始，逐步完善设定集。</p></div>}
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
    {error && <div className="setting-collaboration-feedback" role="alert"><p>{error}</p>{!conversation && !loading && <button type="button" onClick={() => { setLoading(true); setAttempt(value => value + 1) }}>重新连接</button>}</div>}
    <form className="setting-collaboration-composer" onSubmit={event => { event.preventDefault(); void send() }}>
      {feedback?.trim() && <details className="setting-collaboration-comment-context"><summary>待讨论的作者评论</summary><p>{feedback}</p></details>}
      <textarea ref={input} rows={2} aria-label="给设定 Agent 的消息" placeholder={readOnly ? '已归档，只能查看讨论' : '描述想新增或完善的设定…'} value={draft} maxLength={16000} disabled={readOnly}
        onChange={event => changeDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void send() } }} />
      <footer>{pending && !busy ? <button type="button" className="setting-collaboration-retry" disabled={readOnly || loading} onClick={() => { void send(true) }}>重试上一条</button> : <span role="status">{busy ? '正在处理…' : ''}</span>}<button type="submit" className="setting-collaboration-send" aria-label="发送给设定 Agent" disabled={readOnly || busy || loading || !!pending || (!draft.trim() && !feedback?.trim())}><AssistantSendIcon /></button></footer>
    </form>
  </aside>
}
