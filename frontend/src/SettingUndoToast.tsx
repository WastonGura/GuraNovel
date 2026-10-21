import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useDialogMotion } from './useDialogMotion'
import './SettingTrash.css'

export interface SettingUndoOperation { id: string; ids: string[] }
export interface SettingUndoToastProps {
  operation: SettingUndoOperation | null
  onUndo: (ids: string[]) => Promise<string[]>
  onDismiss: () => void
  durationMs?: number
}

export default function SettingUndoToast(props: SettingUndoToastProps) {
  return props.operation ? <UndoNotice key={props.operation.id} {...props} operation={props.operation} /> : null
}

function UndoNotice({ operation, onUndo, onDismiss, durationMs = 7000 }: SettingUndoToastProps & { operation: SettingUndoOperation }) {
  const [remainingIds, setRemainingIds] = useState(operation.ids)
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const remainingTime = useRef(durationMs)
  const busy = useRef(false)
  const mounted = useRef(false)
  const { closing, requestClose, onAnimationEnd } = useDialogMotion(onDismiss, undefined, false, 'setting-toast-out')
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    if (hovered || focused || pending || error || closing) return
    const start = Date.now()
    const timer = window.setTimeout(() => { void requestClose() }, Math.max(0, remainingTime.current))
    return () => { window.clearTimeout(timer); remainingTime.current = Math.max(0, remainingTime.current - (Date.now() - start)) }
  }, [hovered, focused, pending, error, closing, requestClose])
  async function undo() {
    if (busy.current || closing) return
    busy.current = true
    setPending(true)
    setError('')
    try {
      const succeeded = new Set(await onUndo(remainingIds))
      if (!mounted.current) return
      const failed = remainingIds.filter(id => !succeeded.has(id))
      setRemainingIds(failed)
      if (failed.length) setError(`${failed.length} 项撤销结果未能确认，可重试或在回收站核对。`)
      else void requestClose()
    } catch {
      if (mounted.current) setError('撤销结果未能确认，请重试或在回收站核对。')
    } finally { busy.current = false; if (mounted.current) setPending(false) }
  }
  return createPortal(<div role="status" aria-live="polite" aria-atomic="true" className={`setting-undo-toast${closing ? ' is-closing' : ''}`} inert={closing} onAnimationEnd={onAnimationEnd}
    onPointerEnter={() => setHovered(true)} onPointerLeave={() => setHovered(false)} onFocus={() => setFocused(true)}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false) }}>
    <div><span>已移至回收站</span><span aria-hidden="true">·</span><button type="button" disabled={pending} onClick={() => { void undo() }}>{pending ? '正在撤销…' : error ? '重试撤销' : '撤销'}</button><button type="button" className="setting-undo-dismiss" aria-label="关闭删除提示" onClick={() => { void requestClose() }}>×</button></div>
    {error && <p role="alert">{error}</p>}
  </div>, document.body)
}
