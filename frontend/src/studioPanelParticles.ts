import { DISSOLVE_MS } from './studioTextParticles'

// Fragment the actual panel, including its SVG icons, input, border and shadow.
// ponytail: at most 240 DOM fragments for this small panel; rasterize if larger surfaces need this effect.
export function dissolvePanel(panel: HTMLElement, complete: () => void) {
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || !panel.animate) {
    complete()
    return () => {}
  }
  const bounds = panel.getBoundingClientRect(), padding = 24
  const width = bounds.width + padding * 2, height = bounds.height + padding * 2
  const layer = document.createElement('div')
  layer.className = 'studio studio-panel-particles'
  layer.setAttribute('aria-hidden', 'true'); layer.inert = true
  Object.assign(layer.style, { left: `${bounds.left - padding}px`, top: `${bounds.top - padding}px`, width: `${width}px`, height: `${height}px` })
  const template = panel.cloneNode(true) as HTMLElement
  template.removeAttribute('id')
  template.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'))
  template.querySelectorAll('textarea').forEach((input, index) => { input.value = panel.querySelectorAll('textarea')[index].value })
  Object.assign(template.style, { position: 'absolute', left: `${padding}px`, top: `${padding}px`, right: 'auto', bottom: 'auto', margin: '0', width: `${bounds.width}px`, height: `${bounds.height}px`, transition: 'none' })
  const columns = 12, rows = Math.min(20, Math.ceil(height / (width / columns)))
  const animations: Animation[] = []
  document.body.append(layer)
  for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
    const fragment = document.createElement('div')
    Object.assign(fragment.style, { position: 'absolute', inset: '0', clipPath: `inset(${y / rows * 100}% ${100 - (x + 1) / columns * 100}% ${100 - (y + 1) / rows * 100}% ${x / columns * 100}%)` })
    fragment.append(template.cloneNode(true)); layer.append(fragment)
    const delay = Math.random() * 120
    animations.push(fragment.animate([
      { transform: 'translate(0, 0)', opacity: 1 },
      { transform: `translate(${12 + Math.random() * 42}px, ${-12 - Math.random() * 48}px)`, opacity: 0 },
    ], { duration: DISSOLVE_MS - delay, delay, easing: 'ease-out', fill: 'both' }))
  }
  panel.style.visibility = 'hidden'
  let cancelled = false
  void Promise.all(animations.map(animation => animation.finished)).then(() => {
    if (!cancelled) { layer.remove(); complete(); panel.style.removeProperty('visibility') }
  }).catch(() => {})
  return () => {
    cancelled = true; animations.forEach(animation => animation.cancel()); layer.remove(); panel.style.removeProperty('visibility')
  }
}
