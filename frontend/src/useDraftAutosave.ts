import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ApiError, decodeStudioFeedback, readStudioFeedback, submitStudioFeedback, writeDocument, writeStudioDraft, writeStudioFeedback,
  type FeedbackRegion, type FeedbackSubmitRequest, type FeedbackWriteRequest, type StudioFeedback } from './api/client'
import { reanchorFeedbackComments } from './studioPreview'

type Values = Pick<StudioFeedback, 'comments' | 'requirements'>
type FeedbackOptions = { projectId: string; chapterId: string; region: FeedbackRegion; onChange: (value: StudioFeedback) => void }
type PendingFeedback = { payload: FeedbackWriteRequest; text: string }
type PendingSubmission = { payload: FeedbackSubmitRequest; text: string }
type FeedbackRecovery = { base: StudioFeedback; values: Values; write?: PendingFeedback; submission?: PendingSubmission }
// Keep unsaved copies across editor remounts even when browser storage is full.
// Ownership prevents a departing editor's response from clearing newer edits.
export const draftRecoveryCopies = new Map<string, { owner: symbol; value?: string }>()
const warnPendingRecovery = (event: BeforeUnloadEvent) => {
  if ([...draftRecoveryCopies.values()].some(copy => copy.value)) { event.preventDefault(); event.returnValue = '' }
}
window.addEventListener('beforeunload', warnPendingRecovery)
if (import.meta.hot) import.meta.hot.dispose(() => window.removeEventListener('beforeunload', warnPendingRecovery))
const valuesOf = ({ comments, requirements }: Values): Values => ({ comments, requirements })
const same = (a: Values, b: Values) => a.requirements === b.requirements && a.comments.length === b.comments.length
  && a.comments.every((item, index) => {
    const other = b.comments[index]
    return item.id === other.id && item.start === other.start && item.end === other.end && item.quote === other.quote
      && item.color === other.color && item.text === other.text && !!item.submitted === !!other.submitted && !!item.orphaned === !!other.orphaned
  })

// One queue owns the source version and feedback revision. Finish a feedback
// retry before a later prose write can change the version it references.
export function useDraftAutosave(documentId: string | undefined, versionId: string | undefined, initial: string,
  onSaved?: (versionId: string, text: string) => void, options?: { readOnly?: boolean; feedback?: FeedbackOptions }) {
  const recoveryKey = 'guranovel:draft-recovery:' + documentId
  const owner = useRef(Symbol())
  const [recovery] = useState(() => {
    if (documentId) {
      try {
        const value = JSON.parse(draftRecoveryCopies.get(recoveryKey)?.value || sessionStorage.getItem(recoveryKey) || 'null')
        if (value && typeof value.text === 'string' && typeof value.versionId === 'string') {
          let feedback: FeedbackRecovery | undefined
          if (value.feedback) {
            const base = decodeStudioFeedback(value.feedback.base)
            const values = valuesOf(decodeStudioFeedback({ ...base, ...value.feedback.values }))
            feedback = { base, values, write: value.feedback.write, submission: value.feedback.submission }
            for (const pending of [feedback.write, feedback.submission]) {
              if (pending && (typeof pending.text !== 'string' || typeof pending.payload?.request_id !== 'string'
                || typeof pending.payload.expected_current_version_id !== 'string' || !Number.isInteger(pending.payload.expected_revision))) throw new Error('Invalid recovery request')
            }
            if (feedback.write) decodeStudioFeedback({ ...base, ...feedback.write.payload })
            if (feedback.submission && (!Array.isArray(feedback.submission.payload.comment_ids)
              || !feedback.submission.payload.comment_ids.every(id => typeof id === 'string'))) throw new Error('Invalid submission recovery')
          }
          return { text: value.text as string, versionId: value.versionId as string, pending: true, feedback,
            sourceSaved: value.sourceSaved === true && value.text === initial && value.versionId === versionId }
        }
      } catch { return { text: initial, versionId, pending: false, invalid: true } }
    }
    return { text: initial, versionId, pending: false }
  })
  const conflict = recovery.versionId !== versionId && recovery.text !== initial
  const [text, setText] = useState(recovery.text)
  const [feedback, setFeedback] = useState<StudioFeedback | null>(null)
  const [status, setStatus] = useState(recovery.invalid ? '恢复副本无法读取，请先备份后重新加载。' : conflict
    ? '恢复的正文与服务器版本冲突，请先复制备份。' : recovery.pending ? '等待自动保存…' : options?.feedback ? '正在加载评论…' : '已自动保存')
  const current = useRef({ text: recovery.text, saved: recovery.pending && !recovery.sourceSaved ? null as string | null : initial, versionId,
    pending: null as Promise<boolean> | null, submitting: null as Promise<boolean> | null, error: Boolean(conflict || recovery.invalid), conflict: Boolean(conflict || recovery.invalid),
    base: null as StudioFeedback | null, values: null as Values | null, load: null as Promise<boolean> | null,
    write: recovery.feedback?.write, submission: recovery.feedback?.submission,
  })
  const callbacks = useRef({ onSaved, options })
  useLayoutEffect(() => { callbacks.current = { onSaved, options } })
  const mounted = useRef(true)
  const [changeTick, setChangeTick] = useState(0)
  const target = options?.feedback

  useLayoutEffect(() => {
    if (!documentId) return
    const sessionOwner = owner.current, state = current.current
    const previous = draftRecoveryCopies.get(recoveryKey)
    draftRecoveryCopies.set(recoveryKey, { owner: sessionOwner, value: previous?.value })
    return () => {
      const copy = draftRecoveryCopies.get(recoveryKey)
      if (copy?.owner === sessionOwner && !copy.value && !state.pending) draftRecoveryCopies.delete(recoveryKey)
    }
  }, [documentId, recoveryKey])

  function publishFeedback() {
    const state = current.current
    if (!mounted.current || !state.base || !state.values) return
    const next = { ...state.base, ...state.values }
    setFeedback(next); callbacks.current.options?.feedback?.onChange(next)
  }
  function dirty() {
    const state = current.current
    return state.text !== state.saved || !!state.write || !!state.submission
      || !!(state.base && state.values && !same(state.values, valuesOf(state.base)))
  }
  function retain() {
    if (!documentId || recovery.invalid) return
    const state = current.current
    const copy = draftRecoveryCopies.get(recoveryKey)
    if (copy?.owner !== owner.current) return
    copy.value = dirty() || state.conflict ? JSON.stringify({ text: state.text, versionId: state.versionId, sourceSaved: state.text === state.saved,
      feedback: state.base && state.values ? { base: state.base, values: state.values, write: state.write, submission: state.submission } : recovery.feedback,
    }) : undefined
    try {
      if (copy.value) sessionStorage.setItem(recoveryKey, copy.value)
      else sessionStorage.removeItem(recoveryKey)
    } catch { if (mounted.current) setStatus('本地恢复副本不可用，请保持页面打开并重试保存。') }
    if (!copy.value && !mounted.current) draftRecoveryCopies.delete(recoveryKey)
  }
  function fail(error: unknown) {
    const state = current.current
    state.error = true
    state.conflict ||= error instanceof ApiError && error.status === 409
    const local = recovery.feedback
    if (state.conflict && !state.base && local && local.base.document_id === documentId
      && local.base.chapter_id === target?.chapterId && local.base.region === target.region) {
      state.base = { ...local.base, read_only: true }; state.values = local.values
      publishFeedback()
    }
    if (mounted.current) setStatus(state.conflict ? '版本冲突：本地内容已保留，请复制备份后重新加载。'
      : '自动保存失败：内容仍在当前页面，请重试。')
    retain()
    return false
  }
  function applyFeedback(server: StudioFeedback, sourceText: string) {
    const state = current.current
    if (server.document_id !== documentId || server.source_version_id !== state.versionId || server.chapter_id !== target?.chapterId || server.region !== target.region) throw new ApiError(409, 'source_changed', 'Feedback source changed')
    const anchored = new Map(reanchorFeedbackComments(server.comments, sourceText, state.text).map(item => [item.id, item]))
    state.values = { requirements: state.values!.requirements, comments: state.values!.comments.map(item => {
      const saved = anchored.get(item.id)
      return saved ? { ...item, start: saved.start, end: saved.end, quote: saved.quote, orphaned: saved.orphaned, submitted: saved.submitted } : item
    }) }
    state.base = server
    publishFeedback()
  }
  async function loadFeedback() {
    if (!target) return true
    const state = current.current
    if (state.base) return true
    if (state.load) return state.load
    state.load = (async () => {
      try {
        let server = await readStudioFeedback(target.projectId, target.chapterId, target.region)
        if (server.chapter_id !== target.chapterId || server.region !== target.region || server.document_id !== documentId
          || server.source_version_id !== state.versionId) throw new ApiError(409, 'source_changed', 'Feedback source changed')
        const local = state.versionId === recovery.versionId ? recovery.feedback : undefined
        if (local && (local.base.chapter_id !== target.chapterId || local.base.region !== target.region || local.base.document_id !== documentId)) throw new ApiError(409, 'scope_changed', 'Recovery scope changed')
        if (state.write) {
          server = await writeStudioFeedback(target.projectId, target.chapterId, target.region, state.write.payload)
        }
        if (state.submission) {
          const submitted = await submitStudioFeedback(target.projectId, target.chapterId, target.region, state.submission.payload)
          server = await readStudioFeedback(target.projectId, target.chapterId, target.region)
          if (server.revision !== submitted.feedback_revision) throw new ApiError(409, 'feedback_changed', 'Feedback changed after submission')
        } else if (local && !local.write && local.base.revision !== server.revision && !same(local.values, valuesOf(server))) {
          throw new ApiError(409, 'feedback_changed', 'Feedback changed after recovery')
        }
        state.values = local?.values || { requirements: server.requirements, comments: reanchorFeedbackComments(server.comments, initial, state.text) }
        if (local?.write || local?.submission) applyFeedback(server, initial)
        else { state.base = server; publishFeedback() }
        state.write = undefined; state.submission = undefined
        if (mounted.current && !state.error) setStatus(dirty() ? '等待自动保存…' : '已自动保存')
        return true
      } catch (error) { return fail(error) }
      finally { state.load = null }
    })()
    return state.load
  }

  function change(value: string) {
    if (callbacks.current.options?.readOnly || current.current.base?.read_only) return
    const state = current.current
    if (state.values) state.values = { ...state.values, comments: reanchorFeedbackComments(state.values.comments, state.text, value) }
    state.text = value; setText(value); publishFeedback(); if (!state.error) setStatus('等待自动保存…'); retain(); setChangeTick(value => value + 1)
  }
  function changeFeedback(patch: Partial<Values>) {
    const state = current.current
    if (!state.base || !state.values || state.base.read_only || callbacks.current.options?.readOnly) return
    state.values = { ...state.values, ...patch, comments: (patch.comments || state.values.comments).map(item => ({ ...item, submitted: item.submitted === true, orphaned: item.orphaned === true })) }
    publishFeedback(); if (!state.error) setStatus('等待自动保存…'); retain(); setChangeTick(value => value + 1)
  }

  async function saveQueue(): Promise<boolean> {
    const state = current.current
    if (state.pending) return state.pending
    if (state.conflict) return false
    state.pending = (async () => {
      if (!await loadFeedback()) return false
      if (!dirty()) { state.error = false; if (mounted.current) setStatus('已自动保存'); retain(); return true }
      if (!documentId || !state.versionId || callbacks.current.options?.readOnly || state.base?.read_only) {
        if (mounted.current) setStatus('当前章节为只读，本地修改已保留。')
        return false
      }
      state.error = false
      if (mounted.current) setStatus('正在自动保存…')
      try {
        while (dirty() && !state.submission) {
          if (callbacks.current.options?.readOnly || state.base?.read_only) throw new ApiError(409, 'read_only', 'Chapter became readonly')
          if (state.write && target) {
            const sent = state.write
            const saved = await writeStudioFeedback(target.projectId, target.chapterId, target.region, sent.payload)
            applyFeedback(saved, sent.text); state.write = undefined; retain()
            continue
          }
          const content = state.text, values = state.values
          if (content !== state.saved) {
            const payload: { content: string; expected_current_version_id: string } = { content, expected_current_version_id: state.versionId! }
            const saved = target?.region === 'draft'
              ? await writeStudioDraft(target.projectId, target.chapterId, payload)
              : await writeDocument(documentId, payload)
            state.versionId = saved.id; state.saved = content
            if (mounted.current) callbacks.current.onSaved?.(saved.id, state.text)
          }
          if (target && values && state.base && (!same(values, valuesOf(state.base)) || (values.comments.length > 0 && state.base.source_version_id !== state.versionId))) {
            state.write = { text: content, payload: { request_id: crypto.randomUUID(), expected_current_version_id: state.versionId!, expected_revision: state.base.revision, ...values } }
          }
          retain()
        }
        if (mounted.current) setStatus('已自动保存')
        return true
      } catch (error) { return fail(error) }
    })().finally(() => { state.pending = null })
    return state.pending
  }
  async function flush(): Promise<boolean> {
    const state = current.current
    return state.submitting || (state.submission ? submit(state.submission.payload.comment_ids) : saveQueue())
  }
  async function submit(commentIds: string[], animate?: () => Promise<void>): Promise<boolean> {
    const state = current.current
    if (state.submitting) return state.submitting
    if (!target || state.conflict || callbacks.current.options?.readOnly || state.base?.read_only) return false
    state.submitting = (async () => {
      if (!await saveQueue() || !state.base || !state.values) return false
      try {
        if (!state.submission) state.submission = { text: state.saved!, payload: { request_id: crypto.randomUUID(), expected_current_version_id: state.versionId!, expected_revision: state.base.revision, comment_ids: commentIds } }
        retain()
        const pending = state.submission
        const saved = await submitStudioFeedback(target.projectId, target.chapterId, target.region, pending.payload)
        // Keep the panel visible until acknowledgement, then use its existing motion.
        await animate?.()
        const ids = new Set(saved.comments.map(item => item.id))
        const server = { ...state.base, source_version_id: saved.source_version_id, revision: saved.feedback_revision,
          comments: state.base.comments.map(item => ids.has(item.id) ? { ...item, submitted: true } : item) }
        applyFeedback(server, pending.text); state.submission = undefined; retain()
        return await saveQueue()
      } catch (error) { return fail(error) }
    })().finally(() => { state.submitting = null })
    return state.submitting
  }
  function reloadServer() {
    if (!documentId || current.current.pending || current.current.submitting || !window.confirm('放弃本章未保存的本地正文、评论和要求，重新加载服务器内容？请先复制需要保留的内容。')) return
    try { sessionStorage.removeItem(recoveryKey) }
    catch { setStatus('无法清除恢复副本，请先复制内容备份。'); return }
    draftRecoveryCopies.delete(recoveryKey)
    current.current.saved = current.current.text
    window.location.reload()
  }
  useEffect(() => {
    const state = current.current
    if (!versionId || versionId === state.versionId) return
    if (dirty() || state.pending || state.submitting || state.load || state.conflict) {
      fail(new ApiError(409, 'source_changed', 'Local changes retained after a server revision'))
      return
    }
    state.versionId = versionId; state.text = initial; state.saved = initial
    state.base = null; state.values = null; state.error = false
    setText(initial); setFeedback(null); retain()
    if (target) void loadFeedback()
    // Only adopt an external version when the local queue is clean. Own saves already update state.versionId.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionId])
  useEffect(() => {
    mounted.current = true
    if (target) void loadFeedback()
    return () => { mounted.current = false }
    // Editor identity is keyed by document/chapter; loading is shared under StrictMode.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    const timer = window.setTimeout(() => { if (!current.current.error && !current.current.submitting) void saveQueue() }, 700)
    return () => window.clearTimeout(timer)
    // The queue reads current text, callbacks and feedback, never a render snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, changeTick])
  return { text, change, feedback, changeFeedback, submit, status, flush, reloadServer }
}
