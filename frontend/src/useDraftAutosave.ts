import { useEffect, useRef, useState } from 'react'
import { ApiError, writeDocument } from './api/client'

// Serialize saves so each write uses the version returned by the previous write.
export function useDraftAutosave(documentId: string | undefined, versionId: string | undefined, initial: string, onSaved?: (versionId: string, text: string) => void) {
  const recoveryKey = `guranovel:draft-recovery:${documentId}`
  const [recovery] = useState(() => {
    if (documentId) {
      try {
        const value = JSON.parse(sessionStorage.getItem(recoveryKey) || 'null')
        if (value && typeof value.text === 'string' && typeof value.versionId === 'string') return value as { text: string; versionId: string }
      } catch { /* Keep the server version if recovery data is unavailable or invalid. */ }
    }
    return { text: initial, versionId }
  })
  const conflict = recovery.versionId !== versionId && recovery.text !== initial
  const [text, setText] = useState(recovery.text)
  const [status, setStatus] = useState(conflict ? '恢复的正文与服务器版本冲突，请先复制备份。' : '已自动保存')
  const current = useRef({ text: recovery.text, saved: initial, versionId, pending: null as Promise<boolean> | null, error: conflict, conflict })
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  function change(value: string) {
    current.current.text = value
    setText(value)
    setStatus('等待自动保存…')
    if (documentId) {
      try { sessionStorage.setItem(recoveryKey, JSON.stringify({ text: value, versionId: current.current.versionId })) }
      catch { setStatus('本地恢复副本不可用，正在等待服务器自动保存…') }
    }
  }

  async function flush(): Promise<boolean> {
    const state = current.current
    if (state.pending) return state.pending
    if (state.text === state.saved) return true
    if (state.conflict) { setStatus('恢复的正文与服务器版本冲突，请先复制备份。'); return false }
    if (!documentId || !state.versionId) return false
    state.error = false
    state.pending = (async () => {
      if (mounted.current) setStatus('正在自动保存…')
      try {
        while (state.text !== state.saved) {
          const content = state.text
          const saved = await writeDocument(documentId, { content, expected_current_version_id: state.versionId! })
          state.versionId = saved.id
          state.saved = content
          if (mounted.current) onSaved?.(saved.id, state.text)
          try {
            if (state.text === content) sessionStorage.removeItem(recoveryKey)
            else sessionStorage.setItem(recoveryKey, JSON.stringify({ text: state.text, versionId: saved.id }))
          } catch { /* Server save succeeded; do not report it as a failed server write. */ }
        }
        if (mounted.current) setStatus('已自动保存')
        return true
      } catch (error) {
        state.error = true
        state.conflict = error instanceof ApiError && error.status === 409
        if (mounted.current) setStatus(error instanceof ApiError && error.status === 409
          ? '版本冲突：本地正文已保留，请复制备份后重新加载。'
          : '自动保存失败：正文仍在当前页面，请重试。')
        return false
      } finally { state.pending = null }
    })()
    return state.pending
  }

  function reloadServer() {
    if (!documentId || current.current.pending || !window.confirm('放弃本章未保存的本地更改，重新加载服务器正文？请先复制需要保留的内容。')) return
    try { sessionStorage.removeItem(recoveryKey) }
    catch { setStatus('无法清除恢复副本，请先复制正文备份。'); return }
    current.current.saved = current.current.text
    window.location.reload()
  }

  useEffect(() => {
    const timer = window.setTimeout(() => { if (!current.current.error) void flush() }, 700)
    return () => window.clearTimeout(timer)
    // flush only reads the current queue; editor is keyed by document/version at load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text])

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (current.current.text !== current.current.saved) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [])

  return { text, change, status, flush, reloadServer }
}
