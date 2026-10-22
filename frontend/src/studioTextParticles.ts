type Glyph = { text: string; x: number; y: number; width: number; height: number; font: string; color: string }
export type TextSnapshot = { width: number; height: number; glyphs: Glyph[] }
export const DISSOLVE_MS = 820

function visibleBounds(box: HTMLElement, surface: HTMLElement, origin: DOMRect, height = origin.height) {
  let left = origin.left, right = origin.right, top = origin.top, bottom = origin.top + height
  for (let parent: HTMLElement | null = surface; parent && parent !== box; parent = parent.parentElement) {
    const style = getComputedStyle(parent), bounds = parent.getBoundingClientRect()
    if (/auto|scroll|hidden|clip/.test(style.overflowX || style.overflow)) {
      left = Math.max(left, bounds.left); right = Math.min(right, bounds.right)
    }
    if (/auto|scroll|hidden|clip/.test(style.overflowY || style.overflow)) {
      top = Math.max(top, bounds.top); bottom = Math.min(bottom, bounds.bottom)
    }
  }
  return { left, right, top, bottom }
}

// Capture actual glyph positions, including browser wrapping and mixed Chinese/Latin fonts.
export function snapshotText(box: HTMLElement): TextSnapshot {
  const origin = box.getBoundingClientRect(), glyphs: Glyph[] = []
  const height = Math.max(origin.height, box.querySelector(':scope > .studio-motion-content')?.getBoundingClientRect().height || 0)
  box.querySelectorAll<HTMLElement>('[data-sweep-text]').forEach(surface => {
    if (surface.closest('[data-text-inactive="true"]')) return
    const { left, right, top, bottom } = visibleBounds(box, surface, origin, height)
    const walker = document.createTreeWalker(surface, NodeFilter.SHOW_TEXT)
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      const style = getComputedStyle(text.parentElement!)
      const range = document.createRange()
      for (const { segment, index } of new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(text.textContent || '')) {
        if (!segment.trim()) continue
        range.setStart(text, index); range.setEnd(text, index + segment.length)
        if (!range.getBoundingClientRect) continue
        const rect = range.getBoundingClientRect()
        if (!rect.width || rect.left < left - .5 || rect.right > right + .5 || rect.top < top - .5 || rect.bottom > bottom + .5) continue
        glyphs.push({ text: segment, x: rect.left - origin.left, y: rect.top - origin.top, width: rect.width, height: rect.height, font: style.font, color: style.color })
      }
    }
  })
  return { width: origin.width, height, glyphs }
}

function paint(snapshot: TextSnapshot) {
  const canvas = document.createElement('canvas')
  canvas.width = Math.ceil(snapshot.width) || 1; canvas.height = Math.ceil(snapshot.height) || 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) return null
  snapshot.glyphs.forEach(glyph => {
    ctx.font = glyph.font; ctx.fillStyle = glyph.color
    const metrics = ctx.measureText(glyph.text)
    ctx.fillText(glyph.text, glyph.x, glyph.y + glyph.height - (metrics.fontBoundingBoxDescent ?? glyph.height * .2))
  })
  return canvas
}

export function textMotionPhase(elapsed: number, hasOld: boolean) {
  return hasOld && elapsed < DISSOLVE_MS ? 'dissolve' : 'reveal'
}

// Reveal the real DOM so font rendering and layout stay unchanged at the end.
function revealRows(box: HTMLElement) {
  const rows: { surface: HTMLElement; top: number; bottom: number; left: number; right: number; y: number }[] = []
  const viewport = box.getBoundingClientRect()
  box.querySelectorAll<HTMLElement>('[data-sweep-text]').forEach(textSurface => {
    if (textSurface.closest('[data-text-inactive="true"]')) return
    // The input's layout mirror supplies line measurements, but the input itself is revealed.
    const surface = textSurface.matches('[data-text-editor]') ? textSurface
      : textSurface.closest('[data-text-input]')?.querySelector<HTMLInputElement | HTMLTextAreaElement>('input, textarea')
        || textSurface.closest('.studio-context-body.is-input, .studio-prose, .studio-idea-card')?.querySelector<HTMLTextAreaElement>('textarea') || textSurface
    const origin = surface.getBoundingClientRect()
    const clip = visibleBounds(box, surface, viewport)
    const walker = document.createTreeWalker(textSurface, NodeFilter.SHOW_TEXT)
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      const range = document.createRange()
      range.selectNodeContents(text)
      for (const rect of range.getClientRects?.() || []) {
        if (!rect.width || !rect.height || rect.bottom <= clip.top || rect.top >= clip.bottom || rect.right <= clip.left || rect.left >= clip.right) continue
        // Superscripts and inline links can have different tops on the same visual line.
        let row = rows.find(row => row.surface === surface && rect.top < origin.top + row.bottom - 2 && rect.bottom > origin.top + row.top + 2)
        if (!row) {
          row = { surface, top: rect.top - origin.top - 2, bottom: rect.bottom - origin.top + 2, left: rect.left - origin.left, right: rect.right - origin.left, y: rect.top }
          rows.push(row)
        }
        row.left = Math.min(row.left, rect.left - origin.left)
        row.right = Math.max(row.right, rect.right - origin.left)
        row.top = Math.min(row.top, rect.top - origin.top - 2)
        row.bottom = Math.max(row.bottom, rect.bottom - origin.top + 2)
        row.y = Math.min(row.y, rect.top)
      }
    }
  })
  return rows.sort((a, b) => a.y - b.y || a.left - b.left)
}

function opaqueBackground(surface: HTMLElement) {
  for (let element: HTMLElement | null = surface; element; element = element.parentElement) {
    const color = getComputedStyle(element).backgroundColor
    if (color.startsWith('rgb(') || /^rgba\([^)]*,\s*1\)$/.test(color) || /^color\([^/]+\)$/.test(color)) return color
  }
  return 'Canvas'
}

// Canvas is only used for outgoing dust; incoming text keeps its original DOM.
export function animateTextChange(box: HTMLElement, old: TextSnapshot | null, delay = 0, revealAfter = 0) {
  if (typeof CanvasRenderingContext2D === 'undefined') return () => {}
  const source = old?.glyphs.length ? paint(old) : null
  const layer = document.createElement('canvas')
  layer.className = 'studio-text-particles'; layer.setAttribute('aria-hidden', 'true')
  // The overlay must not enlarge its host's scrollable area.
  layer.width = source?.width || 1
  layer.height = source?.height || 1
  const ctx = layer.getContext('2d')
  if (!ctx) return () => {}
  const tiles: { x: number; y: number; delay: number; dx: number; dy: number }[] = []
  let step = 2
  if (source) {
    const pixels = source.getContext('2d')!.getImageData(0, 0, source.width, source.height).data
    // ponytail: cap at about 6000 text fragments; use WebGL only if larger surfaces need it.
    const ink = pixels.reduce((count, alpha, i) => count + (i % 4 === 3 && alpha > 12 ? 1 : 0), 0)
    step = Math.max(2, Math.ceil(Math.sqrt(ink / 6000)))
    for (let y = 0; y < source.height; y += step) for (let x = 0; x < source.width; x += step) {
      let visible = false
      for (let yy = y; yy < Math.min(y + step, source.height) && !visible; yy++) for (let xx = x; xx < Math.min(x + step, source.width); xx++) if (pixels[(yy * source.width + xx) * 4 + 3]) { visible = true; break }
      if (visible) tiles.push({ x, y, delay: Math.random() * 160, dx: 12 + Math.random() * 46, dy: -12 - Math.random() * 45 })
    }
  }
  if (source) box.append(layer)
  const revealStart = Math.max(source ? DISSOLVE_MS : 0, revealAfter)
  box.dataset.textPhase = source ? 'dissolve' : 'waiting'
  box.querySelectorAll('.studio-stream').forEach(node => node.setAttribute('aria-busy', 'true'))
  if (box.matches('.studio-stream')) box.setAttribute('aria-busy', 'true')
  let frame = 0, started: number | null = null, ended = false
  let rows: ReturnType<typeof revealRows> = []
  const masked = new Set<HTMLElement>()
  // Masking editable text changes Chromium's LCD antialiasing to grayscale.
  // Cover the setting canvas instead, so completed lines keep their native glyphs.
  const nativeReveal = box.classList.contains('setting-document-motion')
  const cover = document.createElement('div')
  cover.className = 'studio-text-reveal'; cover.setAttribute('aria-hidden', 'true')
  Object.assign(cover.style, { inset: '0', pointerEvents: 'none', position: 'absolute', zIndex: '1' })
  let covers: { element: HTMLDivElement; background: string }[] = []
  const clear = (notify = true) => {
    if (ended) return
    ended = true; cancelAnimationFrame(frame); layer.remove(); cover.remove(); delete box.dataset.textPhase
    box.removeEventListener('scroll', interrupt, true)
    masked.forEach(surface => surface.style.removeProperty('mask'))
    box.querySelectorAll('.studio-stream').forEach(node => node.setAttribute('aria-busy', 'false'))
    if (box.matches('.studio-stream')) box.setAttribute('aria-busy', 'false')
    if (notify) box.dispatchEvent(new Event('studio:text-ready'))
  }
  const interrupt = () => clear(false)
  const tick = (now: number) => {
    started ??= now
    const elapsed = now - started - delay
    ctx.clearRect(0, 0, layer.width, layer.height)
    if (elapsed < 0) { frame = requestAnimationFrame(tick); return }
    const phase = textMotionPhase(elapsed, !!source)
    if (phase === 'dissolve' && source) {
      for (const tile of tiles) {
        const p = Math.max(0, Math.min(1, (elapsed - tile.delay) / (DISSOLVE_MS - 160)))
        ctx.globalAlpha = 1 - p
        ctx.drawImage(source, tile.x, tile.y, step, step, tile.x + tile.dx * p, tile.y + tile.dy * p * p, step * (1 - p * .4), step * (1 - p * .4))
      }
      ctx.globalAlpha = 1
    } else if (elapsed < revealStart) {
      layer.remove()
      box.dataset.textPhase = 'waiting'
    } else {
      if (box.dataset.textPhase !== 'reveal') {
        layer.remove()
        box.dataset.textPhase = 'reveal'; box.dispatchEvent(new Event('studio:text-ready'))
        rows = revealRows(box)
        if (nativeReveal) {
          box.addEventListener('scroll', interrupt, true)
          const origin = box.getBoundingClientRect()
          covers = rows.map(row => {
            const element = document.createElement('div'), bounds = row.surface.getBoundingClientRect()
            Object.assign(element.style, { position: 'absolute', left: `${bounds.left - origin.left + row.left}px`, top: `${bounds.top - origin.top + row.top}px`, width: `${row.right - row.left + 24}px`, height: `${row.bottom - row.top}px` })
            cover.append(element)
            return { element, background: opaqueBackground(row.surface) }
          })
          box.append(cover)
        } else rows.forEach(({ surface }) => { masked.add(surface); surface.style.mask = 'linear-gradient(transparent, transparent)' })
      }
      const revealTime = elapsed - revealStart, stagger = Math.min(90, 900 / Math.max(1, rows.length))
      const masks = new Map<HTMLElement, string[]>()
      rows.forEach((row, i) => {
        const p = Math.max(0, Math.min(1, (revealTime - i * stagger) / 300))
        const width = row.right - row.left + 24, edge = width * (1 - (1 - p) ** 2)
        if (nativeReveal) {
          covers[i].element.style.background = `linear-gradient(to right, transparent ${edge - 24}px, ${covers[i].background} ${edge}px)`
          return
        }
        const mask = `linear-gradient(to right, #000 ${edge - 24}px, transparent ${edge}px) ${row.left}px ${row.top}px / ${width}px ${row.bottom - row.top}px no-repeat`
        const layers = masks.get(row.surface) || []
        layers.push(mask); masks.set(row.surface, layers)
      })
      masks.forEach((layers, surface) => { surface.style.mask = layers.join(',') })
      if (revealTime >= Math.max(0, rows.length - 1) * stagger + 300) { clear(); return }
    }
    frame = requestAnimationFrame(tick)
  }
  frame = requestAnimationFrame(tick)
  return () => clear(false)
}
