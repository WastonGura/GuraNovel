import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type HTMLAttributes } from 'react'

export type AssistantResizeEdge = 'left' | 'top' | 'corner'

type Size = { width: number; height: number }
type Options = { right?: number; bottom?: number; enabled?: boolean }
const storageKey = 'guranovel_assistant_size'
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, max))

function getViewport(): Size {
  if (typeof window === 'undefined') return { width: 1440, height: 900 }
  return { width: window.visualViewport?.width ?? window.innerWidth, height: window.visualViewport?.height ?? window.innerHeight }
}

function readPreference(): Size | null {
  try {
    const size = JSON.parse(localStorage.getItem(storageKey) || 'null')
    if (size && typeof size.width === 'number' && Number.isFinite(size.width) && size.width > 0
      && typeof size.height === 'number' && Number.isFinite(size.height) && size.height > 0) return size
  } catch { /* A blocked storage or malformed preference leaves the baseline usable. */ }
  return null
}

function persist(size: Size | null) {
  try {
    if (size) localStorage.setItem(storageKey, JSON.stringify(size))
    else localStorage.removeItem(storageKey)
  } catch { /* Resizing remains available when browser storage is blocked. */ }
}

/** Keeps a preferred size independently of viewport clamping, so it returns after a small window grows. */
export function useAssistantResize({ right, bottom, enabled = true }: Options = {}) {
  const [viewport, setViewport] = useState(getViewport)
  const [preference, setPreference] = useState<Size | null>(readPreference)
  const [isResizing, setResizing] = useState(false)
  const stopDrag = useRef<((cancel: boolean) => void) | null>(null)
  const availableWidth = Math.max(1, viewport.width - (right ?? clamp(viewport.width * .04427083, 18, 85)) - 18)
  const availableHeight = Math.max(1, viewport.height - (bottom ?? (viewport.width <= 760 ? 16 : clamp(viewport.height * .05092593, 16, 55))) - 24)
  const minWidth = Math.min(availableWidth, Math.max(1, Math.min(320, (viewport.height - 220) * .75, viewport.width - 36)))
  const minHeight = Math.min(availableHeight, minWidth * 4 / 3)
  // Keep the original usable minimum on a small window, while bounding expansion on larger screens.
  const maxWidth = Math.max(minWidth, Math.min(560, viewport.width * .45, availableWidth))
  const maxHeight = Math.max(minHeight, Math.min(720, viewport.height * .8, availableHeight))
  const width = clamp(preference?.width ?? minWidth, minWidth, maxWidth)
  const height = clamp(preference?.height ?? minHeight, minHeight, maxHeight)
  const latest = useRef({ preference, width, height, minWidth, minHeight, maxWidth, maxHeight })

  useLayoutEffect(() => { latest.current = { preference, width, height, minWidth, minHeight, maxWidth, maxHeight } }, [preference, width, height, minWidth, minHeight, maxWidth, maxHeight])
  useEffect(() => {
    const update = () => setViewport(getViewport())
    window.addEventListener('resize', update)
    window.visualViewport?.addEventListener('resize', update)
    return () => {
      window.removeEventListener('resize', update)
      window.visualViewport?.removeEventListener('resize', update)
    }
  }, [])
  useEffect(() => () => { stopDrag.current?.(true) }, [enabled])

  function update(size: Size | null, commit = true) {
    setPreference(size)
    const bounds = latest.current
    latest.current = {
      ...bounds, preference: size,
      width: clamp(size?.width ?? bounds.minWidth, bounds.minWidth, bounds.maxWidth),
      height: clamp(size?.height ?? bounds.minHeight, bounds.minHeight, bounds.maxHeight),
    }
    if (commit) persist(size)
  }

  function getHandleProps(edge: AssistantResizeEdge): HTMLAttributes<HTMLElement> {
    const label = edge === 'left' ? '调整 Gura 宽度' : edge === 'top' ? '调整 Gura 高度' : '调整 Gura 大小'
    return {
      role: edge === 'corner' ? 'button' : 'separator',
      tabIndex: enabled ? 0 : -1,
      'aria-label': label,
      'aria-disabled': !enabled,
      ...(edge === 'corner' ? {} : {
        'aria-orientation': edge === 'left' ? 'vertical' as const : 'horizontal' as const,
        'aria-valuemin': Math.round(edge === 'left' ? minWidth : minHeight),
        'aria-valuemax': Math.round(edge === 'left' ? maxWidth : maxHeight),
        'aria-valuenow': Math.round(edge === 'left' ? width : height),
      }),
      title: `${label}；方向键调整，Shift 加快，双击恢复默认`,
      onDoubleClick: () => { if (enabled && !stopDrag.current) update(null) },
      onKeyDown: event => {
        if (!enabled || stopDrag.current) return
        const horizontal = edge !== 'top' && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')
        const vertical = edge !== 'left' && (event.key === 'ArrowUp' || event.key === 'ArrowDown')
        if (!horizontal && !vertical) return
        event.preventDefault()
        const current = latest.current
        const amount = (event.shiftKey ? 40 : 10) * (event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? 1 : -1)
        update({
          width: horizontal ? clamp(current.width + amount, current.minWidth, current.maxWidth) : current.preference?.width ?? current.width,
          height: vertical ? clamp(current.height + amount, current.minHeight, current.maxHeight) : current.preference?.height ?? current.height,
        })
      },
      onPointerDown: event => {
        if (!enabled || event.button !== 0 || stopDrag.current) return
        event.preventDefault()
        const element = event.currentTarget
        const pointerId = event.pointerId
        const start = latest.current
        const startX = event.clientX, startY = event.clientY
        let draft = start.preference
        const move = (next: PointerEvent) => {
          if (next.pointerId !== pointerId) return
          next.preventDefault()
          const bounds = latest.current
          draft = {
            width: edge === 'top' ? start.preference?.width ?? start.width : clamp(start.width + startX - next.clientX, bounds.minWidth, bounds.maxWidth),
            height: edge === 'left' ? start.preference?.height ?? start.height : clamp(start.height + startY - next.clientY, bounds.minHeight, bounds.maxHeight),
          }
          update(draft, false)
        }
        const up = (next: PointerEvent) => { if (next.pointerId === pointerId) finish(false) }
        const cancel = (next: PointerEvent) => { if (next.pointerId === pointerId) finish(true) }
        const blur = () => finish(true)
        const escape = (next: KeyboardEvent) => { if (next.key === 'Escape') { next.preventDefault(); finish(true) } }
        const finish = (cancelled: boolean) => {
          window.removeEventListener('pointermove', move)
          window.removeEventListener('pointerup', up)
          window.removeEventListener('pointercancel', cancel)
          window.removeEventListener('keydown', escape)
          window.removeEventListener('blur', blur)
          element.removeEventListener('lostpointercapture', cancel)
          stopDrag.current = null
          try { if (element.hasPointerCapture?.(pointerId)) element.releasePointerCapture(pointerId) } catch { /* Detached handle. */ }
          setResizing(false)
          if (cancelled) update(start.preference, false)
          else persist(draft)
        }
        stopDrag.current = finish
        window.addEventListener('pointermove', move, { passive: false })
        window.addEventListener('pointerup', up)
        window.addEventListener('pointercancel', cancel)
        window.addEventListener('keydown', escape)
        window.addEventListener('blur', blur)
        element.addEventListener('lostpointercapture', cancel)
        try { element.setPointerCapture?.(pointerId) } catch { /* Window listeners still complete an uncaptured drag. */ }
        setResizing(true)
      },
    }
  }

  return {
    width, height, minWidth, minHeight, maxWidth, maxHeight, isResizing,
    style: { '--assistant-width': `${width}px`, '--assistant-height': `${height}px` } as CSSProperties,
    getHandleProps,
  }
}
