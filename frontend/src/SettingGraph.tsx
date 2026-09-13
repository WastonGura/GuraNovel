import { useLayoutEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import { buildNoteGraph, type SettingNote } from './settingNotes'

type Point = { x: number; y: number; vx: number; vy: number }
type Camera = { x: number; y: number; scale: number }
const home: Camera = { x: 0, y: 0, scale: 1 }

export default function SettingGraph({ notes, onOpen }: { notes: SettingNote[]; onOpen: (title: string) => void }) {
  const [filter, setFilter] = useState('all')
  const [hovered, setHovered] = useState<string | null>(null)
  const [camera, setCamera] = useState(home)
  // ponytail: quadratic repulsion is bounded to 160 visible notes; use a spatial index for larger graphs.
  const graph = useMemo(() => {
    const result = buildNoteGraph(notes), nodes = result.nodes.filter(node => filter === 'all' || node.category === filter).slice(0, 160), ids = new Set(nodes.map(node => node.id))
    return { nodes, edges: result.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target)) }
  }, [notes, filter])
  const svg = useRef<SVGSVGElement>(null)
  const [viewBox, setViewBox] = useState('0 0 1000 650')
  const positions = useRef(new Map<string, Point>())
  const restart = useRef(() => {})
  const fixed = useRef<string | null>(null)
  const drag = useRef<{ pointer: number; id?: string; start: Point; origin: Point; camera: Camera; moved: boolean } | null>(null)
  const suppressClick = useRef(false)
  const neighbors = new Set(graph.edges.filter(edge => edge.source === hovered || edge.target === hovered).flatMap(edge => [edge.source, edge.target]))
  const degree = (id: string) => graph.edges.filter(edge => edge.source === id || edge.target === id).length

  useLayoutEffect(() => {
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      if (!width || !height) return
      const w = Math.max(240, Math.min(1000, width)), h = w * height / width
      setViewBox(`${500 - w / 2} ${325 - h / 2} ${w} ${h}`)
    })
    observer.observe(svg.current!)
    return () => observer.disconnect()
  }, [])

  useLayoutEffect(() => {
    const root = svg.current!
    const points = positions.current
    graph.nodes.forEach((node, index) => {
      if (!points.has(node.id)) {
        const angle = index * 2.399963, radius = 50 + Math.sqrt(index) * 48
        points.set(node.id, { x: 500 + Math.cos(angle) * radius, y: 300 + Math.sin(angle) * radius * .7, vx: 0, vy: 0 })
      }
    })
    const nodeElements = [...root.querySelectorAll<SVGGElement>('[data-note-id]')]
    const lines = [...root.querySelectorAll<SVGLineElement>('line')]
    const paint = () => {
      nodeElements.forEach(element => {
        const p = points.get(element.dataset.noteId!)!
        element.setAttribute('transform', `translate(${p.x} ${p.y})`)
      })
      lines.forEach((line, index) => {
        const edge = graph.edges[index], a = points.get(edge.source)!, b = points.get(edge.target)!
        line.setAttribute('x1', String(a.x)); line.setAttribute('y1', String(a.y))
        line.setAttribute('x2', String(b.x)); line.setAttribute('y2', String(b.y))
      })
    }
    const step = () => {
      const nodes = graph.nodes
      for (let i = 0; i < nodes.length; i++) {
        const a = points.get(nodes[i].id)!
        a.vx += (500 - a.x) * .002; a.vy += (300 - a.y) * .003
        for (let j = i + 1; j < nodes.length; j++) {
          const b = points.get(nodes[j].id)!, dx = a.x - b.x || .1, dy = a.y - b.y || .1
          const distance = Math.max(25, Math.hypot(dx, dy)), force = 1100 / (distance * distance)
          a.vx += dx / distance * force; a.vy += dy / distance * force
          b.vx -= dx / distance * force; b.vy -= dy / distance * force
        }
      }
      graph.edges.forEach(edge => {
        const a = points.get(edge.source)!, b = points.get(edge.target)!, dx = b.x - a.x, dy = b.y - a.y
        const distance = Math.max(1, Math.hypot(dx, dy)), force = (distance - 150) * .003
        a.vx += dx / distance * force; a.vy += dy / distance * force
        b.vx -= dx / distance * force; b.vy -= dy / distance * force
      })
      nodes.forEach(node => {
        const p = points.get(node.id)!
        if (node.id === fixed.current) { p.vx = 0; p.vy = 0; return }
        p.vx *= .84; p.vy *= .84
        p.x = Math.max(65, Math.min(935, p.x + p.vx)); p.y = Math.max(60, Math.min(565, p.y + p.vy))
      })
    }
    let frame = 0, remaining = 0
    const tick = () => { step(); paint(); if (--remaining > 0) frame = requestAnimationFrame(tick) }
    restart.current = () => {
      cancelAnimationFrame(frame)
      if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
        for (let i = 0; i < 120; i++) step()
        paint()
      } else { remaining = 180; frame = requestAnimationFrame(tick) }
    }
    paint(); restart.current()
    return () => { cancelAnimationFrame(frame); restart.current = () => {} }
  }, [graph])

  function localPoint(clientX: number, clientY: number): Point {
    const root = svg.current!, matrix = root.getScreenCTM()
    const p = matrix ? new DOMPoint(clientX, clientY).matrixTransform(matrix.inverse()) : { x: clientX, y: clientY }
    return { x: p.x, y: p.y, vx: 0, vy: 0 }
  }
  function zoom(factor: number, x = 500, y = 325) {
    setCamera(before => {
      const scale = Math.max(.45, Math.min(2.8, before.scale * factor))
      return { scale, x: x - (x - before.x) * scale / before.scale, y: y - (y - before.y) * scale / before.scale }
    })
  }
  useLayoutEffect(() => {
    const root = svg.current!
    const wheel = (event: WheelEvent) => {
      event.preventDefault()
      const p = localPoint(event.clientX, event.clientY)
      zoom(Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * .002), p.x, p.y)
    }
    root.addEventListener('wheel', wheel, { passive: false })
    return () => root.removeEventListener('wheel', wheel)
  }, [])

  function cancelDrag() {
    const state = drag.current
    if (!state) return
    if (state.id) positions.current.set(state.id, { ...state.origin })
    else setCamera(state.camera)
    fixed.current = null; drag.current = null; suppressClick.current = true
    svg.current?.releasePointerCapture?.(state.pointer); restart.current()
  }
  useLayoutEffect(() => {
    const cancel = () => cancelDrag()
    window.addEventListener('blur', cancel)
    return () => window.removeEventListener('blur', cancel)
  }, [])

  function pointerDown(event: PointerEvent<SVGSVGElement>) {
    if (event.button !== 0 || drag.current) return
    const id = (event.target as Element).closest<SVGGElement>('[data-note-id]')?.dataset.noteId
    const start = localPoint(event.clientX, event.clientY)
    drag.current = { pointer: event.pointerId, id, start, origin: { ...(id ? positions.current.get(id)! : start) }, camera, moved: false }
    fixed.current = id || null; suppressClick.current = false
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }
  function pointerMove(event: PointerEvent<SVGSVGElement>) {
    const state = drag.current
    if (!state || state.pointer !== event.pointerId) return
    const p = localPoint(event.clientX, event.clientY), dx = p.x - state.start.x, dy = p.y - state.start.y
    if (Math.hypot(dx, dy) > 3) state.moved = true
    if (state.id) {
      positions.current.set(state.id, { x: state.origin.x + dx / state.camera.scale, y: state.origin.y + dy / state.camera.scale, vx: 0, vy: 0 })
      restart.current()
    } else setCamera({ ...state.camera, x: state.camera.x + dx, y: state.camera.y + dy })
  }
  function pointerUp(event: PointerEvent<SVGSVGElement>) {
    const state = drag.current
    if (!state || state.pointer !== event.pointerId) return
    suppressClick.current = state.moved; drag.current = null; fixed.current = null
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    restart.current()
    if (state.id && !state.moved) onOpen(graph.nodes.find(node => node.id === state.id)!.title)
  }

  return <div className="setting-graph">
    <div className="setting-graph-top"><div><h1>关系图谱</h1><p>{graph.nodes.length} 个条目 <span>·</span> {graph.edges.length} 条关联</p></div>
      <div className="setting-graph-filters" role="group" aria-label="图谱范围">{[['all', '全部'], ['setting', '设定'], ['world', '世界观']].map(([key, label]) => <button key={key} aria-pressed={filter === key} onClick={() => { setFilter(key); setHovered(null); setCamera(home) }}>{label}</button>)}</div>
    </div>
    <svg ref={svg} className="setting-graph-canvas" viewBox={viewBox} tabIndex={0} role="group" aria-label="关系图谱画布" aria-describedby="setting-graph-help"
      onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={cancelDrag} onLostPointerCapture={cancelDrag}
      onKeyDown={event => {
        if (event.key === 'Escape') { cancelDrag(); setHovered(null); return }
        if (event.key === '+' || event.key === '=') { event.preventDefault(); zoom(1.2) }
        if (event.key === '-') { event.preventDefault(); zoom(1 / 1.2) }
        if (event.key === 'Home' || event.key === '0') { event.preventDefault(); setCamera(home) }
        if (event.key.startsWith('Arrow')) {
          event.preventDefault()
          const delta = event.shiftKey ? 70 : 25
          setCamera(value => ({ ...value, x: value.x + (event.key === 'ArrowRight' ? delta : event.key === 'ArrowLeft' ? -delta : 0), y: value.y + (event.key === 'ArrowDown' ? delta : event.key === 'ArrowUp' ? -delta : 0) }))
        }
      }}>
      <g transform={`translate(${camera.x} ${camera.y}) scale(${camera.scale})`}>
        {graph.edges.map(edge => <line key={`${edge.source}:${edge.target}`} className={hovered ? edge.source === hovered || edge.target === hovered ? 'is-connected' : 'is-muted' : ''} />)}
        {graph.nodes.map(node => <g key={node.id} data-note-id={node.id} role="button" tabIndex={0} aria-label={`${node.missing ? '创建引用条目' : '打开条目'}：${node.title}`}
          className={`setting-graph-node is-${node.category || 'missing'}${node.missing ? ' is-missing' : ''}${hovered && !neighbors.has(node.id) && node.id !== hovered ? ' is-muted' : ''}${hovered === node.id ? ' is-active' : ''}`}
          onPointerEnter={() => { if (!drag.current) setHovered(node.id) }} onPointerLeave={() => { if (!drag.current) setHovered(null) }} onFocus={() => setHovered(node.id)} onBlur={() => setHovered(null)}
          onClick={event => { if (event.detail === 0 && !suppressClick.current) onOpen(node.title) }}
          onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); onOpen(node.title) } }}>
          <circle className="setting-graph-hit" r="26" /><circle className="setting-graph-dot" r={6 + Math.min(6, degree(node.id) * 1.15)} />
          <text y={28 + Math.min(4, degree(node.id))} textAnchor="middle">{node.title}</text>
        </g>)}
      </g>
    </svg>
    {!graph.nodes.length && <p className="setting-graph-empty">写下第一条设定，再用 [[条目名]] 将它们连接起来。</p>}
    <div className="setting-graph-bottom"><p id="setting-graph-help">拖动节点或空白处 · 滚轮缩放 · 点击打开条目{notes.length > 160 && ' · 当前最多展示 160 条'}</p>
      <div className="setting-graph-zoom"><button aria-label="缩小图谱" onClick={() => zoom(1 / 1.2)}>−</button><button aria-label="图谱归位" title="Home 归位" onClick={() => setCamera(home)}>{Math.round(camera.scale * 100)}%</button><button aria-label="放大图谱" onClick={() => zoom(1.2)}>+</button></div>
    </div>
  </div>
}
