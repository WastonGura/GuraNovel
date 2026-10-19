import { useEffect, useRef, useState } from 'react'
import { novelCoverSourceSet, novelDefaultCover } from './novelCover'

// Keep geometry fixed and retain the last decoded image while a replacement loads.
type CoverImageProps = {
  source: string; mode?: 'card' | 'detail' | 'picker'; priority?: boolean; widthHint?: number; frameRatio?: number
}
export default function CoverImage(props: CoverImageProps) {
  if (props.source === novelDefaultCover) return <span className="cover-image is-default" role="img" aria-label="默认封面" />
  return <LoadedCoverImage {...props} />
}
function LoadedCoverImage({ source, mode = 'card', priority = false, widthHint = 146, frameRatio }: CoverImageProps) {
  const container = useRef<HTMLSpanElement>(null)
  const [visible, setVisible] = useState(() => priority || typeof IntersectionObserver === 'undefined')
  const [box, setBox] = useState(() => typeof ResizeObserver === 'undefined' ? { width: widthHint, height: widthHint * 4 / 3 } : { width: 0, height: 0 })
  const [visual, setVisual] = useState<{ source: string; srcSet?: string; sizes: string; width: number; height: number } | null>(null)
  const [problem, setProblem] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    const element = container.current!
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect
      if (width > 0 && height > 0) setBox(previous => Math.round(width) === previous.width && Math.round(height) === previous.height ? previous : { width: Math.round(width), height: Math.round(height) })
    })
    observer?.observe(element)
    return () => observer?.disconnect()
  }, [])
  useEffect(() => {
    if (visible) return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect() }
    }, { rootMargin: '100px' })
    observer.observe(container.current!)
    return () => observer.disconnect()
  }, [visible])
  useEffect(() => {
    if (!visible || box.width === 0) return
    let active = true, fallback = false
    const image = new Image()
    const srcSet = novelCoverSourceSet(source)
    const sizes = `${box.width}px`
    image.decoding = 'async'
    image.fetchPriority = priority ? 'high' : 'auto'
    const fail = () => {
      if (!active) return
      if (srcSet && !fallback) { fallback = true; image.srcset = ''; image.src = source }
      else setProblem(source)
    }
    image.onerror = fail
    image.onload = async () => {
      try {
        await image.decode()
        if (active) { setVisual({ source, srcSet: fallback ? undefined : srcSet, sizes, width: image.naturalWidth, height: image.naturalHeight }); setProblem('') }
      } catch { fail() }
    }
    image.sizes = sizes
    if (srcSet) image.srcset = srcSet
    image.src = source
    return () => { active = false; image.onload = null; image.onerror = null }
  }, [visible, source, box.width, priority, retry])
  useEffect(() => {
    const reconnect = () => { if (problem === source) setRetry(value => value + 1) }
    window.addEventListener('online', reconnect)
    return () => window.removeEventListener('online', reconnect)
  }, [problem, source])
  const ratio = visual ? visual.width / visual.height : 0.75
  const containerRatio = frameRatio ?? (box.height ? box.width / box.height : 0.75)
  const mismatch = Math.max(ratio / containerRatio, containerRatio / ratio)
  const fit = mode !== 'card' && mismatch > 1.6 ? 'contain' : 'cover'
  const failed = problem === source
  return <span ref={container} className={`cover-image${visual ? ' has-image' : ''}${failed ? ' is-error' : ''}`} data-visible={visible} aria-busy={!failed && visual?.source !== source} data-fit={fit}
    title={failed ? (visual ? '新封面加载失败，暂时显示原图' : '封面暂不可用') : undefined}>
    {visual && <img src={visual.source} srcSet={visual.srcSet} sizes={visual.sizes} width={visual.width} height={visual.height} decoding="async" loading={priority ? 'eager' : 'lazy'} fetchPriority={priority ? 'high' : 'auto'} alt="" style={{ objectFit: fit, objectPosition: fit === 'cover' && ratio < 1 ? '50% 30%' : 'center' }} />}
    {failed && !visual && <span className="cover-image-error" role="img" aria-label="封面暂不可用">封面暂不可用</span>}
  </span>
}
