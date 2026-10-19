import { useEffect, useId, useRef, useState } from 'react'

/** Keep the complete text available; only offer expansion when the rendered lines overflow. */
export default function SearchDescription({ text, label = '简介' }: { text: string; label?: string }) {
  return <Description key={text} text={text} label={label} />
}

function Description({ text, label }: { text: string; label: string }) {
  const paragraph = useRef<HTMLParagraphElement>(null)
  const id = useId()
  const [expanded, setExpanded] = useState(false)
  const [overflow, setOverflow] = useState(false)
  useEffect(() => {
    const element = paragraph.current!
    const measure = () => { if (!expanded) setOverflow(element.scrollHeight > element.clientHeight + 1) }
    const frame = requestAnimationFrame(measure)
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(element)
    window.addEventListener('resize', measure)
    return () => { cancelAnimationFrame(frame); observer?.disconnect(); window.removeEventListener('resize', measure) }
  }, [expanded])
  return <section className="search-description" aria-label={label}>
    <h4>{label}</h4>
    <p ref={paragraph} id={id} className={`search-description-text${expanded ? ' is-expanded' : ''}`}>{text || `暂无${label}`}</p>
    {overflow && <button type="button" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : '展开'}{label}<span aria-hidden="true">{expanded ? ' −' : ' +'}</span></button>}
  </section>
}
