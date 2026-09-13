import { useLayoutEffect, useRef, type ReactNode, type RefObject } from 'react'
import { animateTextChange, snapshotText, type TextSnapshot } from './studioTextParticles'

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// The measured content stays natural-sized; the outer box owns actual layout height.
// Keep chrome outside that measurement so its bottom anchor follows every frame.
export function MotionFrame({ children, chrome, className = '', label, textKey }: { children: ReactNode; chrome?: ReactNode; className?: string; label?: string; textKey?: string }) {
  const frame = useRef<HTMLElement>(null)
  useTextMotion(frame, textKey)
  const content = useRef<HTMLDivElement>(null)
  const resize = useRef<() => void>(() => {})
  const controls = useRef(new Map<HTMLElement, boolean>())
  const pending = useRef(new Set<HTMLElement>())
  const heightAnimation = useRef<Animation | null>(null)

  useLayoutEffect(() => {
    const box = frame.current!, live = content.current!
    let target = 0
    const reveal = () => {
      delete box.dataset.resizing
      pending.current.forEach(control => {
        if (control.isConnected && control.dataset.visible !== 'false') {
          control.inert = false
          control.style.removeProperty('opacity')
          control.animate?.([{ opacity: 0, scale: '.86' }, { opacity: 1, scale: '1' }], { duration: 180, easing: 'ease-out' })
        }
      })
      pending.current.clear()
    }
    resize.current = () => {
      if (box.dataset.textPhase === 'dissolve') return
      const next = live.getBoundingClientRect().height + box.offsetHeight - box.clientHeight
      if (Math.abs(next - target) < .5) return
      const from = target ? box.getBoundingClientRect().height : next
      target = next
      heightAnimation.current?.cancel()
      box.style.height = `${next}px`
      if (Math.abs(from - next) < .5 || reducedMotion() || !box.animate) { reveal(); return }
      box.dataset.resizing = 'true'
      const animation = box.animate([{ height: `${from}px` }, { height: `${next}px` }], { duration: 440, easing: 'cubic-bezier(0, 0, .58, 1)' })
      heightAnimation.current = animation
      void animation.finished.then(() => {
        if (heightAnimation.current === animation) { heightAnimation.current = null; reveal() }
      }).catch(() => {})
    }
    resize.current()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => resize.current())
    observer?.observe(live)
    const onTextReady = () => { resize.current(); if (!box.dataset.resizing) reveal() }
    box.addEventListener('studio:text-ready', onTextReady)
    return () => { observer?.disconnect(); heightAnimation.current?.cancel(); box.removeEventListener('studio:text-ready', onTextReady) }
  }, [])

  useLayoutEffect(() => {
    resize.current()
    const box = frame.current!
    const next = new Map<HTMLElement, boolean>()
    box.querySelectorAll<HTMLElement>('[data-motion-control]').forEach(control => {
      const visible = control.dataset.visible !== 'false'
      const before = controls.current.get(control)
      next.set(control, visible)
      if (before === visible || (!controls.current.size && !box.dataset.resizing)) return
      control.getAnimations?.().forEach(animation => animation.cancel())
      pending.current.delete(control)
      control.style.removeProperty('opacity')
      control.inert = !visible
      if (reducedMotion()) return
      if (visible && (box.dataset.resizing || box.dataset.textPhase === 'dissolve')) {
        control.inert = true
        control.style.opacity = '0'
        pending.current.add(control)
      } else if (visible) {
        control.animate?.([{ opacity: 0, scale: '.86' }, { opacity: 1, scale: '1' }], { duration: 180, easing: 'ease-out' })
      } else if (before) {
        control.animate?.([{ opacity: 1, scale: '1', visibility: 'visible' }, { opacity: 0, scale: '.86', visibility: 'visible' }], { duration: 140, easing: 'ease-in' })
      }
    })
    controls.current = next
  })

  return <section ref={frame} data-sweep-group={textKey === undefined ? undefined : ''} className={`studio-motion-frame ${className}`} aria-label={label}>
    <div ref={content} className="studio-motion-content">{children}</div>
    {chrome}
  </section>
}

// Keep the latest layout while asynchronous review messages arrive during a transition.
function useTextMotion(frame: RefObject<HTMLElement | null>, key?: string) {
  const previous = useRef<{ key: string; signature: string; snapshot: TextSnapshot } | null>(null)
  const stop = useRef<() => void>(() => {})
  useLayoutEffect(() => {
    const box = frame.current!
    if (key === undefined || box.parentElement?.closest('[data-sweep-group]')) return
    const signature = [...box.querySelectorAll('[data-sweep-text]')].map(node => node.textContent).join('|') + box.clientWidth
    const before = previous.current
    const snapshot = before?.signature === signature ? before.snapshot : snapshotText(box)
    previous.current = { key, signature, snapshot }
    if (!before || before.key === key) return
    stop.current()
    if (reducedMotion()) return
    stop.current = animateTextChange(box, before.snapshot)
  })
  useLayoutEffect(() => () => stop.current(), [])
}
export function TextSweep({ text, changeKey = text }: { text: string; changeKey?: string }) {
  const frame = useRef<HTMLSpanElement>(null)
  useTextMotion(frame, changeKey)
  return <span ref={frame} className="studio-text-sweep"><span data-sweep-text className="studio-text-live">{text.split('\n').map((line, index) => <span className="studio-text-line" key={index}>{line || '\u00a0'}</span>)}</span></span>
}

// Preview output arrives as whole text, then reveals along its real wrapped lines.
export function StreamText({ text, delay = 480, startedAt }: { text: string; delay?: number; startedAt?: number }) {
  const frame = useRef<HTMLSpanElement>(null)
  useLayoutEffect(() => {
    const box = frame.current!
    if (reducedMotion() || box.parentElement?.closest('[data-sweep-group]') || (startedAt !== undefined && Date.now() - startedAt > delay + 1500)) return
    return animateTextChange(box, null, Math.max(0, delay - (startedAt === undefined ? 0 : Date.now() - startedAt)))
  }, [text, delay, startedAt])
  return <span ref={frame} className="studio-stream" aria-busy="false">
    <span className="studio-announcer">{text}</span>
    <span data-sweep-text aria-hidden="true">{text}</span>
    <span className="studio-stream-wait" aria-hidden="true">正在生成<span>…</span></span>
  </span>
}
