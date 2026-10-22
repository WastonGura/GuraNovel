import { useEffect, useEffectEvent, useId, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { useDialogMotion } from './useDialogMotion'
import SettingActionIcon from './SettingActionIcon'
import './SettingLifecycle.css'

export default function SettingCollectionMenu({ disabled, onCollaborate, onArchive }: { disabled: boolean; onCollaborate: () => void; onArchive?: () => void }) {
  const trigger = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const menuId = useId()
  return <>
    <button ref={trigger} type="button" className="setting-collection-menu-trigger setting-icon-button" aria-label="设定集操作" title="设定集操作" aria-haspopup="menu" aria-controls={open ? menuId : undefined} aria-expanded={open} disabled={disabled}
      onClick={() => { if (!open) setOpen(true) }} onKeyDown={event => {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setOpen(true) }
      }}>
      <SettingActionIcon name="more" />
    </button>
    {open && createPortal(<div className="studio setting-menu-overlay"><CollectionActions id={menuId} trigger={trigger} valid={!disabled} onClose={() => setOpen(false)} onCollaborate={onCollaborate} onArchive={onArchive} /></div>, document.body)}
  </>
}

function CollectionActions({ id, trigger, valid, onClose, onCollaborate, onArchive }: {
  id: string; trigger: RefObject<HTMLButtonElement | null>; valid: boolean; onClose: () => void; onCollaborate: () => void; onArchive?: () => void
}) {
  const element = useRef<HTMLDivElement>(null)
  const requested = useRef(false), afterClose = useRef<(() => void) | undefined>(undefined), restore = useRef(false)
  const motion = useDialogMotion(() => {
    onClose()
    if (!valid) return
    if ((restore.current || afterClose.current) && trigger.current?.isConnected) trigger.current.focus({ preventScroll: true })
    afterClose.current?.()
  }, undefined, false, 'setting-menu-out')
  function close(after?: () => void, restoreFocus = false) {
    if (requested.current) return
    requested.current = true; afterClose.current = after; restore.current = restoreFocus
    void motion.requestClose()
  }
  const dismiss = useEffectEvent(() => close())
  useLayoutEffect(() => {
    const place = () => {
      if (!element.current || !trigger.current) return
      const anchor = trigger.current.getBoundingClientRect(), width = element.current.offsetWidth, height = element.current.offsetHeight
      const left = Math.max(8, Math.min(anchor.right - width, window.innerWidth - width - 8))
      const top = anchor.bottom + 8 + height <= window.innerHeight - 8 ? anchor.bottom + 8 : Math.max(8, anchor.top - height - 8)
      element.current.style.left = `${left}px`; element.current.style.top = `${top}px`
    }
    place()
    element.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus({ preventScroll: true })
    window.addEventListener('resize', place)
    return () => window.removeEventListener('resize', place)
  }, [trigger])
  useEffect(() => {
    const outside = (event: PointerEvent | FocusEvent) => {
      if (!element.current?.contains(event.target as Node)) dismiss()
    }
    const scroll = (event: Event) => { if (!element.current?.contains(event.target as Node)) dismiss() }
    const blur = () => dismiss()
    document.addEventListener('pointerdown', outside); document.addEventListener('focusin', outside)
    document.addEventListener('scroll', scroll, true); window.addEventListener('blur', blur)
    return () => {
      document.removeEventListener('pointerdown', outside); document.removeEventListener('focusin', outside)
      document.removeEventListener('scroll', scroll, true); window.removeEventListener('blur', blur)
    }
  }, [])
  useEffect(() => {
    if (!valid) { afterClose.current = undefined; restore.current = false; dismiss() }
  }, [valid])
  return <div ref={element} id={id} role="menu" aria-label="设定集操作" className={`setting-collection-menu${motion.closing ? ' is-closing' : ''}`} inert={motion.closing || !valid} onAnimationEnd={motion.onAnimationEnd} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(undefined, true) }
    if (event.key === 'Tab') { event.stopPropagation(); trigger.current?.focus({ preventScroll: true }); close() }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault(); event.stopPropagation()
      const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')]
      const index = items.indexOf(document.activeElement as HTMLButtonElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
      items[next]?.focus()
    }
  }}>
    <button type="button" role="menuitem" className="setting-menu-item" onClick={() => close(onCollaborate, true)}>
      <SettingActionIcon name="agent" size={18} />
      与设定 Agent 协作
    </button>
    {onArchive && <button type="button" role="menuitem" className="setting-menu-item" onClick={() => close(onArchive, true)}>
      <SettingActionIcon name="archive" size={18} />
      归档设定集
    </button>}
  </div>
}
