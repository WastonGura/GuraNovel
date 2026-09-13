import { useLayoutEffect, useRef } from 'react'
import { flushSync } from 'react-dom'

// Shared by the pending and submitted circles; only a completed drop changes data.
export function useCommentDrag(enabled: boolean, onOrder: (ids: string[]) => void) {
  const dots = useRef<HTMLDivElement>(null)
  const latest = useRef(onOrder)
  useLayoutEffect(() => { latest.current = onOrder })
  useLayoutEffect(() => {
    const root = dots.current
    if (!enabled || !root) return
    const frame = root.closest<HTMLElement>('.studio-outline-comment, .studio-context, [data-comment-frame]')!
    let cancel = () => {}, suppressClick = false
    const buttons = () => [...root.querySelectorAll<HTMLButtonElement>('button[data-comment-id]')]
    const down = (event: PointerEvent) => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>('button[data-comment-id]')
      if (!button || event.button !== 0) return
      cancel()
      suppressClick = false
      const items = buttons().map(element => ({ element, id: element.dataset.commentId!, rect: element.getBoundingClientRect(), transform: getComputedStyle(element).transform }))
      const ids = items.map(item => item.id), id = button.dataset.commentId!
      let order = ids, moving = false, ghost: HTMLElement | null = null, outside = false
      const animations = new Map<HTMLElement, Animation>()
      const position = (next: string[]) => {
        if (next.join('|') === order.join('|')) return
        order = next
        items.forEach(item => {
          if (item.id === id) return
          const slot = items[order.indexOf(item.id)]
          if (!slot) return
          const from = getComputedStyle(item.element).transform
          const to = `translate(${slot.rect.left - item.rect.left}px, ${slot.rect.top - item.rect.top}px) ${item.transform === 'none' ? '' : item.transform}`
          animations.get(item.element)?.cancel()
          const animation = item.element.animate?.([{ transform: from }, { transform: to }], { duration: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 180, easing: 'ease-out', fill: 'forwards' })
          if (animation) { animations.set(item.element, animation); void animation.finished.catch(() => {}) }
        })
      }
      const move = (pointer: PointerEvent) => {
        if (pointer.pointerId !== event.pointerId) return
        if (!moving && Math.hypot(pointer.clientX - event.clientX, pointer.clientY - event.clientY) < 4) return
        pointer.preventDefault()
        if (!moving) {
          moving = true; frame.dataset.commentDragging = 'true'
          items.forEach(item => item.element.getAnimations?.().forEach(animation => animation.cancel()))
          button.style.opacity = '0'
          ghost = document.createElement('i'); ghost.className = 'studio-comment-drag-ghost'
          ghost.style.background = getComputedStyle(button).getPropertyValue('--comment-color')
          document.body.append(ghost)
        }
        const bounds = frame.getBoundingClientRect()
        outside = pointer.clientX < bounds.left || pointer.clientX > bounds.right || pointer.clientY < bounds.top || pointer.clientY > bounds.bottom
        Object.assign(ghost!.style, { left: `${pointer.clientX - 6}px`, top: `${pointer.clientY - 6}px`, scale: outside ? '0' : '1.15', opacity: outside ? '0' : '1' })
        ghost!.dataset.outside = String(outside)
        const nearest = items.reduce((best, item, index) => {
          const distance = Math.hypot(pointer.clientX - item.rect.left - 12, pointer.clientY - item.rect.top - 12)
          return distance < best.distance ? { index, distance } : best
        }, { index: 0, distance: Infinity }).index
        const next = ids.filter(value => value !== id)
        if (!outside) next.splice(nearest, 0, id)
        position(next)
      }
      const finish = (commit: boolean) => {
        document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up)
        document.removeEventListener('pointercancel', abort); document.removeEventListener('keydown', escape)
        window.removeEventListener('blur', abort)
        const before = items.map(item => ({ ...item, rect: item.id === id && ghost && !outside ? ghost.getBoundingClientRect() : item.element.getBoundingClientRect() }))
        delete frame.dataset.commentDragging
        ghost?.remove(); button.style.removeProperty('opacity')
        animations.forEach(animation => animation.cancel())
        if (moving) suppressClick = true
        cancel = () => {}
        if (moving && commit) flushSync(() => latest.current(order))
        if (moving && !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) before.forEach(item => {
          if (!item.element.isConnected) return
          const rect = item.element.getBoundingClientRect(), to = getComputedStyle(item.element).transform
          const dx = item.rect.left + item.rect.width / 2 - rect.left - rect.width / 2, dy = item.rect.top + item.rect.height / 2 - rect.top - rect.height / 2
          item.element.animate?.([{ transform: `translate(${dx}px, ${dy}px) ${to === 'none' ? '' : to}` }, { transform: to }], { duration: 180, easing: 'ease-out' })
        })
      }
      const up = (pointer: PointerEvent) => { if (pointer.pointerId === event.pointerId) { if (moving) move(pointer); finish(true) } }
      const abort = () => finish(false)
      const escape = (key: KeyboardEvent) => { if (key.key === 'Escape') { key.preventDefault(); abort() } }
      cancel = abort
      document.addEventListener('pointermove', move, { passive: false }); document.addEventListener('pointerup', up)
      document.addEventListener('pointercancel', abort); document.addEventListener('keydown', escape); window.addEventListener('blur', abort)
    }
    const click = (event: MouseEvent) => { if (suppressClick) { event.preventDefault(); event.stopPropagation(); suppressClick = false } }
    const key = (event: KeyboardEvent) => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>('button[data-comment-id]')
      if (!button) return
      const ids = buttons().map(item => item.dataset.commentId!), index = ids.indexOf(button.dataset.commentId!)
      if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); latest.current(ids.filter((_, i) => i !== index)) }
      else if (event.altKey && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
        event.preventDefault()
        const next = Math.max(0, Math.min(ids.length - 1, index + (event.key === 'ArrowLeft' ? -1 : 1)))
        ids.splice(next, 0, ...ids.splice(index, 1)); latest.current(ids)
      }
    }
    root.addEventListener('pointerdown', down); root.addEventListener('click', click, true); root.addEventListener('keydown', key)
    return () => { cancel(); root.removeEventListener('pointerdown', down); root.removeEventListener('click', click, true); root.removeEventListener('keydown', key) }
  }, [enabled])
  return dots
}
