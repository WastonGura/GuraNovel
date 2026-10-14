import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react'
import { ApiError, readChapterIdea, writeChapterIdea, type ChapterIdea, type ChapterIdeaWrite } from './api/client'

const owners = new Map<string, symbol>()
type Recovery = { text: string; revision: number; pending: ChapterIdeaWrite | null }
export type IdeaHandle = { flush: () => Promise<boolean>; changeFeedback: () => void; submitFeedback: () => Promise<boolean> }

export function useChapterIdea(projectId: string, chapterId: string, ref?: Ref<IdeaHandle>) {
  const key = `guranovel:chapter-idea:${projectId}:${chapterId}`
  const token = useRef(Symbol())
  const mounted = useRef(false)
  const [recovery] = useState<Recovery | null>(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem(key) || 'null')
      return value && typeof value.text === 'string' && Number.isInteger(value.revision) ? value : null
    } catch { return null }
  })
  const [text, setText] = useState(recovery?.text || '')
  const [status, setStatus] = useState('正在加载思路…')
  const [ready, setReady] = useState(false)
  const [readOnly, setReadOnly] = useState(false)
  const [revision, setRevision] = useState<number | undefined>(undefined)
  const state = useRef({ text, base: null as ChapterIdea | null, pending: recovery?.pending || null,
    load: null as Promise<boolean> | null, saving: null as Promise<boolean> | null, conflict: false })
  const report = (value: string) => { if (mounted.current) setStatus(value) }
  function retain() {
    if (owners.get(key) !== token.current) return
    const current = state.current
    try {
      if (current.base && current.text === current.base.text && !current.pending) sessionStorage.removeItem(key)
      else sessionStorage.setItem(key, JSON.stringify({ text: current.text, revision: current.pending?.expected_revision ?? current.base?.revision ?? recovery?.revision ?? 0, pending: current.pending }))
    } catch { report('本机恢复副本保存失败，请复制思路备份。') }
  }
  useEffect(() => {
    let active = true
    mounted.current = true; owners.set(key, token.current)
    const current = state.current
    current.load = readChapterIdea(projectId, chapterId).then(server => {
      if (!active) return false
      current.base = server
      if (recovery && recovery.text !== server.text && recovery.revision !== server.revision) {
        current.conflict = true; report('思路版本冲突，本地内容已保留，请复制后核对服务器版本。')
      } else {
        if (!recovery || recovery.text === server.text) { current.text = server.text; current.pending = null }
        report(current.text === server.text ? '思路已保存' : '思路待保存')
      }
      if (mounted.current) { setText(current.text); setReady(true); setReadOnly(server.read_only); setRevision(server.revision) }
      retain()
      return true
    }).catch(() => { if (active) report('思路加载失败，本地内容已保留，请重新加载。'); return false })
    return () => { active = false; mounted.current = false }
    // This hook belongs to a keyed project/chapter editor; recovery is one immutable mount snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, chapterId, key])

  async function flush(): Promise<boolean> {
    const current = state.current
    if (current.saving) return current.saving
    current.saving = (async () => {
      if (!await current.load || !current.base || current.conflict) return false
      try {
        while (current.text !== current.base.text || current.pending) {
          if (current.base.read_only) { report('当前思路只读，本地内容已保留。'); return false }
          current.pending ||= { request_id: crypto.randomUUID(), expected_revision: current.base.revision, text: current.text }
          retain(); report('正在保存思路…')
          const sent = current.pending
          const server = await writeChapterIdea(projectId, chapterId, sent)
          if (server.text !== sent.text || server.revision !== sent.expected_revision + 1) throw new ApiError(409, 'idea_changed', 'Idea changed')
          current.base = server; current.pending = null; retain()
          if (mounted.current) setRevision(server.revision)
        }
        report('思路已保存'); return true
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) current.conflict = true
        report(current.conflict ? '思路版本冲突，本地内容已保留，请复制后重新加载。' : '思路保存失败，本地内容已保留，可重试。')
        retain(); return false
      }
    })().finally(() => { current.saving = null })
    return current.saving
  }
  function change(value: string) {
    state.current.text = value; setText(value); report('思路待保存'); retain()
  }
  async function reloadServer() {
    const current = state.current
    if (current.saving || !window.confirm('放弃本地未保存的思路，使用服务器版本？请先复制需要保留的内容。')) return
    try {
      const server = await readChapterIdea(projectId, chapterId)
      current.text = server.text; current.base = server; current.pending = null; current.conflict = false
      if (mounted.current) { setText(server.text); setReady(true); setReadOnly(server.read_only); setRevision(server.revision) }
      retain(); report('思路已保存')
    } catch { report('思路加载失败，本地内容已保留，请重试。') }
  }
  useEffect(() => {
    if (!ready || readOnly) return
    const timer = window.setTimeout(() => { if (!state.current.conflict) void flush() }, 500)
    return () => window.clearTimeout(timer)
    // The save queue always reads current text and its authoritative revision.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, ready, readOnly])
  useImperativeHandle(ref, () => ({ flush, changeFeedback: () => {}, submitFeedback: async () => false }))
  return { text, status, ready, readOnly, revision, change, flush, reloadServer }
}
