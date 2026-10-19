import { useContext, useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { useDialogMotion } from './useDialogMotion'
import { DialogDismissContext } from './dialogDismiss'

export function DialogDismissButton(props: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onClick'>) {
  const dismiss = useContext(DialogDismissContext)
  if (!dismiss) throw new Error('DialogDismissButton requires a dialog')
  return <button type="button" {...props} onClick={dismiss} />
}

export default function CreationDialog({ titleId, closeLabel, busy, onClose, beforeClose, children, className = '' }: {
  titleId: string
  closeLabel: string
  busy: boolean
  onClose: () => void
  beforeClose?: () => Promise<boolean>
  children: ReactNode
  className?: string
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [returnFocus] = useState(() => document.activeElement as HTMLElement | null)
  const backdropPress = useRef(false)
  const { closing, requestClose, onAnimationEnd } = useDialogMotion(onClose, beforeClose, busy)

  useEffect(() => {
    const dialog = dialogRef.current!
    if (typeof dialog.showModal === 'function') dialog.showModal()
    else dialog.setAttribute('open', '')
    const initialFocus = dialog.querySelector<HTMLInputElement>('input') ?? dialog.querySelector<HTMLButtonElement>('.dashboard-dialog-close')
    initialFocus?.focus()
    return () => {
      if (typeof dialog.close === 'function') dialog.close()
      returnFocus?.focus()
    }
  }, [returnFocus])

  return <DialogDismissContext.Provider value={requestClose}><dialog ref={dialogRef} className={`dashboard-create-dialog creation-dialog ${className}${closing ? ' is-closing' : ''}`} aria-labelledby={titleId} inert={closing}
    onAnimationEnd={onAnimationEnd}
    onCancel={event => { event.preventDefault(); requestClose() }}
    onMouseDown={event => {
      const box = event.currentTarget.getBoundingClientRect()
      backdropPress.current = event.target === event.currentTarget && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)
    }}
    onClick={event => {
      const box = event.currentTarget.getBoundingClientRect()
      const outside = event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom
      if (backdropPress.current && outside && event.target === event.currentTarget) requestClose()
      backdropPress.current = false
    }}
    onKeyDown={event => {
      if (event.key !== 'Tab') return
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, a[href], [tabindex="0"]'))
        .filter(control => !control.closest('fieldset:disabled') && control.getClientRects().length > 0)
      const target = event.shiftKey ? controls.at(-1) : controls[0]
      const boundary = event.shiftKey ? controls[0] : controls.at(-1)
      if (document.activeElement === boundary && target) { event.preventDefault(); target.focus() }
    }}>
    <button className="dashboard-dialog-close" type="button" onClick={requestClose} disabled={busy} aria-label={closeLabel}>×</button>
    {children}
  </dialog></DialogDismissContext.Provider>
}
