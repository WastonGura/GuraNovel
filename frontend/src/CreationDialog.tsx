import { useEffect, useRef, useState, type ReactNode } from 'react'

export default function CreationDialog({ titleId, closeLabel, busy, onClose, children }: {
  titleId: string
  closeLabel: string
  busy: boolean
  onClose: () => void
  children: ReactNode
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [returnFocus] = useState(() => document.activeElement as HTMLElement | null)
  const backdropPress = useRef(false)

  useEffect(() => {
    const dialog = dialogRef.current!
    if (typeof dialog.showModal === 'function') dialog.showModal()
    else dialog.setAttribute('open', '')
    dialog.querySelector<HTMLInputElement>('input')?.focus()
    return () => {
      if (typeof dialog.close === 'function') dialog.close()
      returnFocus?.focus()
    }
  }, [returnFocus])

  return <dialog ref={dialogRef} className="dashboard-create-dialog creation-dialog" aria-labelledby={titleId}
    onCancel={event => { event.preventDefault(); if (!busy) onClose() }}
    onMouseDown={event => {
      const box = event.currentTarget.getBoundingClientRect()
      backdropPress.current = event.target === event.currentTarget && (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom)
    }}
    onClick={event => {
      const box = event.currentTarget.getBoundingClientRect()
      const outside = event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom
      if (backdropPress.current && outside && event.target === event.currentTarget && !busy) onClose()
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
    <button className="dashboard-dialog-close" type="button" onClick={onClose} disabled={busy} aria-label={closeLabel}>×</button>
    {children}
  </dialog>
}
