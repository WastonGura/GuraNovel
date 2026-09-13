import { useEffect, useRef, useState } from 'react'
import { createPortal, flushSync } from 'react-dom'
import { MotionFrame } from './StudioMotion'
import { categoryNames, type SettingCategory, type SettingNote } from './settingNotes'

export default function SettingNoteList({ notes, category, activeId, disabled, onOpen, onOrder, onDelete }: {
  notes: SettingNote[]; category: SettingCategory; activeId?: string; disabled: boolean
  onOpen: (note: SettingNote) => void; onOrder: (ids: string[]) => void; onDelete: (ids: string[]) => void
}) {
  const root = useRef<HTMLDivElement>(null), cancel = useRef<() => void>(() => {}), suppressClick = useRef(false)
  const [selected, setSelected] = useState<string[]>([])
  const [menu, setMenu] = useState<{ ids: string[]; x: number; y: number; copyStatus?: 'done' | 'error' } | null>(null)
  const [deleting, setDeleting] = useState<string[]>([])
  const menuElement = useRef<HTMLDivElement>(null), confirmation = useRef<HTMLDialogElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)
  const [menuContext, setMenuContext] = useState(`${category}:${disabled}`)
  if (menuContext !== `${category}:${disabled}`) { setMenuContext(`${category}:${disabled}`); setMenu(null); setDeleting([]) }
  const ids = notes.map(note => note.id), chosen = selected.filter(id => ids.includes(id))
  useEffect(() => () => cancel.current(), [])
  useEffect(() => { cancel.current() }, [category, disabled])
  useEffect(() => {
    const clear = (event: MouseEvent) => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey) return
      if (suppressClick.current && root.current?.contains(event.target as Node)) { suppressClick.current = false; return }
      const target = event.target as HTMLElement
      if (!target.closest('button, input, textarea, a, [contenteditable], [role="dialog"], dialog, .setting-note-menu')) setSelected([])
    }
    document.addEventListener('click', clear)
    return () => document.removeEventListener('click', clear)
  }, [])
  useEffect(() => {
    if (!menu) return
    menuElement.current?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true })
    const dismiss = (event: PointerEvent) => { if (!menuElement.current?.contains(event.target as Node)) setMenu(null) }
    const blur = () => setMenu(null)
    document.addEventListener('pointerdown', dismiss); window.addEventListener('blur', blur)
    return () => { document.removeEventListener('pointerdown', dismiss); window.removeEventListener('blur', blur) }
  }, [menu])
  useEffect(() => { if (deleting.length) confirmation.current?.showModal() }, [deleting])
  function closeConfirmation() { confirmation.current?.close?.(); setDeleting([]); returnFocus.current?.focus({ preventScroll: true }) }
  function openMenu(target: HTMLElement, x?: number, y?: number) {
    if (disabled) return
    cancel.current()
    const button = target.closest<HTMLElement>('[data-note-id]')
    const next = button && !chosen.includes(button.dataset.noteId!) ? [button.dataset.noteId!] : chosen
    if (!next.length) return
    if (button) { if (!chosen.includes(button.dataset.noteId!)) setSelected([]); returnFocus.current = button }
    else returnFocus.current = root.current
    const rect = (button || root.current!).getBoundingClientRect()
    setMenu({ ids: next, x: Math.max(8, Math.min(x || rect.left, window.innerWidth - 188)), y: Math.max(8, Math.min(y || rect.bottom, window.innerHeight - 104)) })
  }
  function click(note: SettingNote) {
    if (suppressClick.current) { suppressClick.current = false; return }
    setSelected([]); onOpen(note)
  }
  return <>
    <div className="setting-selection-actions" style={{ visibility: chosen.length && !disabled ? 'visible' : 'hidden' }}><small>已选 {chosen.length} 项</small><button type="button" aria-label="取消条目选择" onClick={() => setSelected([])}>×</button></div>
    <div ref={root} className="setting-note-list" aria-label={`${categoryNames[category]}条目`} tabIndex={0} onContextMenu={event => { event.preventDefault(); event.stopPropagation(); openMenu(event.target as HTMLElement, event.clientX, event.clientY) }} onKeyDown={event => {
      if (disabled) return
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') { event.preventDefault(); setSelected(ids) }
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel.current(); setSelected([]) }
      if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) { event.preventDefault(); openMenu(event.target as HTMLElement) }
      if (event.altKey && ['ArrowUp', 'ArrowDown'].includes(event.key) && chosen.length) {
        event.preventDefault(); const rest = ids.filter(id => !chosen.includes(id)), start = Math.min(...chosen.map(id => ids.indexOf(id))), at = Math.max(0, Math.min(rest.length, start + (event.key === 'ArrowUp' ? -1 : 1)))
        onOrder([...rest.slice(0, at), ...ids.filter(id => chosen.includes(id)), ...rest.slice(at)])
      }
      if (['Delete', 'Backspace'].includes(event.key) && chosen.length) { event.preventDefault(); returnFocus.current = event.target as HTMLElement; setDeleting(chosen) }
    }} onDragStart={event => event.preventDefault()} onPointerDown={event => {
      if (disabled || event.button !== 0) return
      event.preventDefault()
      cancel.current(); suppressClick.current = false
      const list = root.current!, target = (event.target as HTMLElement).closest<HTMLElement>('[data-note-id]')
      const button = target && chosen.includes(target.dataset.noteId!) ? target : null
      ;(target || list).focus({ preventScroll: true })
      const items = [...list.querySelectorAll<HTMLElement>('[data-note-id]')].map(element => ({ element, id: element.dataset.noteId!, rect: element.getBoundingClientRect() }))
      if (!items.length) return
      const picked = button ? chosen.includes(button.dataset.noteId!) ? ids.filter(id => chosen.includes(id)) : [button.dataset.noteId!] : []
      const startX = event.clientX, startY = event.clientY, scroll = list.scrollTop
      let x = startX, y = startY, moving = false, order = ids, frame = 0, ghost: HTMLDivElement | null = null, box: HTMLDivElement | null = null
      const bounds = list.getBoundingClientRect(), reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      const animate = (node: HTMLElement, from: string, to: string) => {
        const animation = node.animate?.([{ transform: from }, { transform: to }], { duration: reduced ? 0 : 240, easing: 'cubic-bezier(.22, 1, .36, 1)' })
        if (animation) void animation.finished.catch(() => {})
      }
      const draw = () => {
        if (!moving) return
        const speed = y < bounds.top + 28 ? -7 : y > bounds.bottom - 28 ? 7 : 0
        if (speed) list.scrollTop += speed
        const scrolled = list.scrollTop - scroll
        if (ghost) {
          ghost.style.transform = `translate(${x - startX}px, ${y - startY}px)`
          const rest = items.filter(item => !picked.includes(item.id))
          const at = rest.filter(item => y > item.rect.top - scrolled + item.rect.height / 2).length
          order = [...rest.slice(0, at).map(item => item.id), ...picked, ...rest.slice(at).map(item => item.id)]
          items.forEach(item => {
            if (picked.includes(item.id)) return
            const slot = items[order.indexOf(item.id)]
            item.element.style.transform = `translateY(${slot.rect.top - item.rect.top}px)`
          })
        } else if (box) {
          const left = Math.max(bounds.left, Math.min(startX, x)), top = Math.max(bounds.top, Math.min(startY - scrolled, y))
          const right = Math.min(bounds.right, Math.max(startX, x)), bottom = Math.min(bounds.bottom, Math.max(startY - scrolled, y))
          Object.assign(box.style, { left: `${left}px`, top: `${top}px`, width: `${Math.max(0, right - left)}px`, height: `${Math.max(0, bottom - top)}px` })
          const next = items.filter(item => item.rect.left < right && item.rect.right > left && item.rect.top - scrolled < bottom && item.rect.bottom - scrolled > top).map(item => item.id)
          setSelected(previous => previous.join() === next.join() ? previous : next)
        }
        frame = requestAnimationFrame(draw)
      }
      const move = (pointer: PointerEvent) => {
        if (pointer.pointerId !== event.pointerId) return
        x = pointer.clientX; y = pointer.clientY
        if (moving || Math.hypot(x - startX, y - startY) < 5) return
        moving = true; list.dataset.selecting = 'true'; document.getSelection()?.removeAllRanges()
        if (button) {
          const held = items.find(item => item.id === button.dataset.noteId)!
          ghost = document.createElement('div'); ghost.className = 'setting-note-drag'; ghost.setAttribute('aria-hidden', 'true')
          Object.assign(ghost.style, { left: `${held.rect.left}px`, top: `${held.rect.top}px`, width: `${held.rect.width}px`, height: `${held.rect.height}px` })
          document.body.append(ghost)
          items.filter(item => picked.includes(item.id)).forEach((item, index) => {
            item.element.style.opacity = '0'
            const card = document.createElement('div'); card.className = 'setting-note-drag-card'
            card.style.zIndex = String(picked.length - index)
            card.style.transform = `translate(${index * 3}px, ${index * 3}px)`
            if (index === 0) card.textContent = picked.length === 1 ? button.textContent : `${picked.length} 个条目`
            ghost!.append(card)
            animate(card, `translate(${item.rect.left - held.rect.left}px, ${item.rect.top - held.rect.top}px)`, card.style.transform)
          })
        } else { box = document.createElement('div'); box.className = 'setting-note-marquee'; box.setAttribute('aria-hidden', 'true'); document.body.append(box) }
        draw()
      }
      const finish = (commit: boolean) => {
        cancelAnimationFrame(frame); document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', abort); document.removeEventListener('keydown', escape); window.removeEventListener('blur', abort)
        const before = items.map(item => ({ ...item, position: picked.includes(item.id) && ghost ? ghost.getBoundingClientRect() : item.element.getBoundingClientRect() }))
        ghost?.remove(); box?.remove(); delete list.dataset.selecting
        items.forEach(item => { item.element.style.removeProperty('opacity'); item.element.style.removeProperty('transform') })
        if (moving) suppressClick.current = true
        if (commit && moving && button && x >= bounds.left && x <= bounds.right && y >= bounds.top && y <= bounds.bottom) flushSync(() => onOrder(order))
        if (moving && button) before.forEach(item => { const after = item.element.getBoundingClientRect(); animate(item.element, `translate(${item.position.left - after.left}px, ${item.position.top - after.top}px)`, 'translate(0, 0)') })
        if (!commit && !button) setSelected(chosen)
        cancel.current = () => {}
      }
      const up = (pointer: PointerEvent) => { if (pointer.pointerId === event.pointerId) finish(true) }
      const abort = () => finish(false)
      const escape = (key: KeyboardEvent) => { if (key.key === 'Escape') { key.stopPropagation(); abort() } }
      cancel.current = abort
      document.addEventListener('pointermove', move); document.addEventListener('pointerup', up); document.addEventListener('pointercancel', abort); document.addEventListener('keydown', escape); window.addEventListener('blur', abort)
    }}>
      <MotionFrame className="setting-list-motion" textKey={category}>
        {notes.map(note => <button key={note.id} type="button" data-note-id={note.id} data-sweep-text aria-pressed={chosen.includes(note.id)} aria-current={activeId === note.id ? 'page' : undefined} aria-description="单击打开，拖动框选；拖动已选条目排序，Ctrl+A 全选，Alt 加上下方向键微调；右键或 Delete 确认删除所选" onClick={() => click(note)}>{note.title || '未命名'}</button>)}
        {!notes.length && <p className="setting-list-empty" data-sweep-text>还没有内容</p>}
      </MotionFrame>
    </div>
    {(menu || deleting.length > 0) && createPortal(<div className="studio setting-menu-overlay">
      {menu && <div ref={menuElement} className="setting-note-menu" role="menu" aria-label="条目操作" style={{ left: menu.x, top: menu.y }} onKeyDown={event => {
        if (event.key === 'Escape' || event.key === 'Tab') { event.stopPropagation(); setMenu(null); returnFocus.current?.focus({ preventScroll: true }) }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault(); event.stopPropagation()
          const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')], index = buttons.indexOf(document.activeElement as HTMLButtonElement)
          buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus()
        }
      }}><button type="button" role="menuitem" onClick={async () => {
        let copyStatus: 'done' | 'error' = 'done'
        try { await navigator.clipboard.writeText(notes.filter(note => menu.ids.includes(note.id)).map(note => note.title).join('\n')) }
        catch { copyStatus = 'error' }
        setMenu(current => current === menu ? { ...current, copyStatus } : current)
      }}><img src="/ui/setting/copy.svg" alt="" /><span aria-live="polite">{menu.copyStatus === 'done' ? '已复制名称' : menu.copyStatus === 'error' ? '复制失败，请重试' : menu.ids.length > 1 ? '复制所选条目名称' : '复制条目名称'}</span></button><button type="button" role="menuitem" onClick={() => { setDeleting(menu.ids); setMenu(null) }}><img src="/ui/setting/delete.svg" alt="" />删除{menu.ids.length > 1 ? ` ${menu.ids.length} 个条目` : '条目'}</button></div>}
      {!!deleting.length && <dialog ref={confirmation} className="studio-confirm setting-delete-confirm" aria-labelledby="setting-delete-title" aria-describedby="setting-delete-description" onCancel={event => { event.preventDefault(); closeConfirmation() }} onClick={event => { if (event.target === event.currentTarget) closeConfirmation() }}>
        <h2 id="setting-delete-title">删除{deleting.length > 1 ? `这 ${deleting.length} 个条目` : '这个条目'}？</h2>
        <p className="setting-delete-names">{notes.filter(note => deleting.includes(note.id)).map(note => note.title).join('、')}</p>
        <p id="setting-delete-description">正文中的引用文字会保留，删除后可以撤销。</p>
        <div className="studio-actions"><button type="button" autoFocus onClick={closeConfirmation}>取消</button><button type="button" onClick={() => { if (!disabled) onDelete(deleting); setSelected([]); returnFocus.current = root.current; closeConfirmation() }}>确认删除</button></div>
      </dialog>}
    </div>, document.body)}
  </>
}
