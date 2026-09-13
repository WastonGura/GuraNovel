import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react'

export const COMMENT_IDLE_MS = 8000

export function useFloatingComment(sheet: RefObject<HTMLDivElement | null>, enabled: boolean, ready: boolean, active: string) {
  const panel = useRef<HTMLDivElement>(null)
  const wake = useRef<() => void>(() => {})
  useLayoutEffect(() => {
    const box = panel.current, prose = sheet.current?.closest<HTMLElement>('.studio-prose')
    if (!enabled || !box || !prose) return
    const context = prose.closest('.studio-chapter-content')?.querySelector<HTMLElement>('.studio-context-wrap')
    const assistant = document.querySelector<HTMLElement>('.studio-assistant')
    const sidebar = document.querySelector<HTMLElement>('.studio-sidebar')
    let timer = 0, dragged = false, entrance: Animation | undefined
    let drag: { x: number; y: number; left: number; top: number } | null = null
    const grip = box.querySelector<HTMLElement>('.studio-comment-grip')!
    const place = (left: number, top: number) => {
      box.style.left = `${Math.max(12, Math.min(left, window.innerWidth - box.offsetWidth - 12))}px`
      box.style.top = `${Math.max(85, Math.min(top, window.innerHeight - box.offsetHeight - 65))}px`
    }
    const layout = () => {
      const width = window.innerWidth > 760 ? 230 : Math.max(140, window.innerWidth * .4)
      box.style.width = `${width}px`
      const beside = window.innerWidth > 760
      const assistantBounds = assistant?.classList.contains('is-open') ? assistant.getBoundingClientRect() : null
      const sidebarBounds = sidebar?.matches('.is-open, :has(.is-pinned)') ? sidebar.getBoundingClientRect() : null
      const obstacles = [assistantBounds, sidebarBounds].filter((rect): rect is DOMRect => !!rect && rect.width > 0)
      const side = obstacles.filter(rect => beside || rect.left > 40)
      const bottom = obstacles.filter(rect => !beside && rect.left <= 40)
      const rightEdge = Math.min(window.innerWidth - 12, ...side.map(rect => rect.left - 16))
      const bottomEdge = Math.min(window.innerHeight - 65, ...bottom.map(rect => rect.top - 16))
      box.style.maxHeight = `${Math.max(120, bottomEdge - 85)}px`
      const pageOffset = prose.closest<HTMLElement>('.studio-page-surface')?.getBoundingClientRect().left || 0
      // Reserve only the missing side gutter; the panel remains outside the scrolling text.
      // Old padding can exceed a newly narrowed container and inflate its measured width.
      prose.style.paddingRight = '0px'
      const bounds = prose.getBoundingClientRect()
      const gutter = Math.max(0, width + 28 - (rightEdge - (bounds.right - pageOffset)))
      // A narrow open directory takes priority over the floating editor; closing
      // it restores the comment without moving or discarding its contents.
      const occluded = !!sidebarBounds && (bounds.width - gutter < (beside ? 180 : 100) || bottomEdge < 205)
      box.dataset.occluded = String(occluded)
      box.inert = !ready || occluded || box.dataset.idle === 'true' || box.getAttribute('aria-busy') === 'true'
      prose.style.paddingRight = `${occluded ? 0 : gutter}px`
      if (context) context.style.paddingRight = prose.style.paddingRight
      const top = Math.max(115, (window.innerHeight - box.offsetHeight) / 2)
      if (!dragged) place(sheet.current!.getBoundingClientRect().right - pageOffset + 16, Math.min(top, bottomEdge - box.offsetHeight))
      else place(parseFloat(box.style.left), parseFloat(box.style.top))
    }
    const hide = () => {
      if (drag || box.dataset.commentDragging || box.getAttribute('aria-busy') === 'true') { timer = window.setTimeout(hide, COMMENT_IDLE_MS); return }
      if (box.contains(document.activeElement)) (document.activeElement as HTMLElement).blur()
      box.dataset.idle = 'true'; box.inert = true
    }
    const show = () => {
      if (!ready || box.dataset.occluded === 'true' || box.getAttribute('aria-busy') === 'true') return
      box.dataset.idle = 'false'; box.inert = false
      window.clearTimeout(timer); timer = window.setTimeout(hide, COMMENT_IDLE_MS)
    }
    wake.current = show
    const hover = (event: PointerEvent) => {
      const rect = box.getBoundingClientRect()
      if (event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom) show()
    }
    const down = (event: PointerEvent) => {
      if (event.button !== 0) return
      event.preventDefault(); show(); dragged = true
      const rect = box.getBoundingClientRect()
      drag = { x: event.clientX, y: event.clientY, left: rect.left, top: rect.top }
      grip.setPointerCapture(event.pointerId); box.dataset.dragging = 'true'
    }
    const move = (event: PointerEvent) => { if (drag) place(drag.left + event.clientX - drag.x, drag.top + event.clientY - drag.y) }
    const up = () => { drag = null; delete box.dataset.dragging; show() }
    const key = (event: KeyboardEvent) => {
      const offsets: Record<string, [number, number]> = { ArrowLeft: [-16, 0], ArrowRight: [16, 0], ArrowUp: [0, -16], ArrowDown: [0, 16] }
      if (event.key === 'Home') { event.preventDefault(); dragged = false; layout(); show() }
      if (!offsets[event.key]) return
      event.preventDefault(); dragged = true; show()
      const rect = box.getBoundingClientRect(), [x, y] = offsets[event.key]
      place(rect.left + x, rect.top + y)
    }
    layout()
    box.dataset.ready = String(ready)
    box.inert = !ready || box.dataset.occluded === 'true'
    if (ready) {
      show()
      if (!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) entrance = box.animate?.([{ opacity: 0, clipPath: 'inset(0 0 100% 0 round 18px)' }, { opacity: 1, clipPath: 'inset(0 round 18px)' }], { duration: 240, easing: 'ease-out' })
    }
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(layout)
    observer?.observe(prose)
    if (assistant) observer?.observe(assistant)
    if (sidebar) observer?.observe(sidebar)
    const visibility = new MutationObserver(layout)
    if (sidebar) visibility.observe(sidebar, { attributes: true, attributeFilter: ['class'], subtree: true })
    if (assistant) visibility.observe(assistant, { attributes: true, attributeFilter: ['class'] })
    window.addEventListener('resize', layout)
    document.addEventListener('pointermove', hover)
    box.addEventListener('input', show); box.addEventListener('keydown', show); box.addEventListener('focusin', show)
    grip.addEventListener('pointerdown', down); grip.addEventListener('pointermove', move)
    grip.addEventListener('pointerup', up); grip.addEventListener('lostpointercapture', up); grip.addEventListener('keydown', key)
    return () => {
      window.clearTimeout(timer); entrance?.cancel(); observer?.disconnect(); visibility.disconnect(); prose.style.removeProperty('padding-right'); wake.current = () => {}
      context?.style.removeProperty('padding-right')
      window.removeEventListener('resize', layout); document.removeEventListener('pointermove', hover)
      box.removeEventListener('input', show); box.removeEventListener('keydown', show); box.removeEventListener('focusin', show)
      grip.removeEventListener('pointerdown', down); grip.removeEventListener('pointermove', move)
      grip.removeEventListener('pointerup', up); grip.removeEventListener('lostpointercapture', up); grip.removeEventListener('keydown', key)
    }
  }, [panel, sheet, enabled, ready])
  useEffect(() => { wake.current() }, [active])
  return { panel, wake }
}
