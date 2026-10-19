import { useEffect, useId, useImperativeHandle, useRef, useState, type ReactNode, type Ref } from 'react'
import { createPortal } from 'react-dom'
import './PickerDialog.css'
import { useDialogMotion } from './useDialogMotion'

export interface PickerDialogHandle { close: () => void }

export default function PickerDialog({ ref, title, closeLabel = `关闭${title}`, className = '', meta, initialFocus = 'input', onClose, onClosing, children }: {
  ref?: Ref<PickerDialogHandle>; onClosing?: () => void; title: string; closeLabel?: string; className?: string; meta?: ReactNode; initialFocus?: string; onClose: () => void; children: ReactNode
}) {
  const titleId = useId()
  const dialog = useRef<HTMLDialogElement>(null)
  const [returnFocus] = useState(() => document.activeElement as HTMLElement | null)
  const backdropPress = useRef(false)
  const { closing, requestClose, onAnimationEnd } = useDialogMotion(onClose)
  function dismiss() { onClosing?.(); void requestClose() }
  useImperativeHandle(ref, () => ({ close: dismiss }))
  useEffect(() => {
    const element = dialog.current!
    element.showModal()
    const target = element.querySelector<HTMLElement>(initialFocus) ?? element.querySelector('button')
    target?.focus()
    return () => { element.close(); returnFocus?.focus() }
  }, [returnFocus, initialFocus])
  return createPortal(<dialog ref={dialog} className={`picker-dialog ${className}${closing ? ' is-closing' : ''}`} inert={closing} onAnimationEnd={onAnimationEnd} aria-labelledby={titleId}
    onKeyDown={event => event.stopPropagation()}
    onCancel={event => { event.preventDefault(); event.stopPropagation(); dismiss() }}
    onMouseDown={event => { backdropPress.current = event.target === event.currentTarget }}
    onClick={event => { if (backdropPress.current && event.target === event.currentTarget) dismiss(); backdropPress.current = false }}>
    <div><header><h2 id={titleId}>{title}</h2>{meta}<button type="button" aria-label={closeLabel} onClick={dismiss}>×</button></header>{children}</div>
  </dialog>, document.body)
}
