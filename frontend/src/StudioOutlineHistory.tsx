import type { OutlineGeneration } from './api/client'

export default function StudioOutlineHistory({ batches, activeId, onSelect }: {
  batches: OutlineGeneration[]; activeId: string; onSelect: (id: string) => void
}) {
  const ordered = batches.filter(batch => batch.status === 'completed' && batch.candidates.length > 0).sort((a, b) => a.started_at.localeCompare(b.started_at))
  const index = ordered.findIndex(batch => batch.id === activeId)
  const previous = index > 0 ? ordered[index - 1] : undefined
  const next = index >= 0 ? ordered[index + 1] : undefined
  return <nav className="outline-history" aria-label="大纲历史批次">
    <button type="button" className="outline-history-arrow" aria-label="查看上一批大纲" title="查看上一批大纲"
      disabled={!previous} onClick={() => previous && onSelect(previous.id)}><span className="outline-history-chevron" aria-hidden="true" /></button>
    <span className="outline-history-count" role="status" aria-live="polite" aria-atomic="true">第 {index < 0 ? '—' : index + 1} / {ordered.length} 批</span>
    <button type="button" className="outline-history-arrow is-next" aria-label="查看下一批大纲" title="查看下一批大纲"
      disabled={!next} onClick={() => next && onSelect(next.id)}><span className="outline-history-chevron" aria-hidden="true" /></button>
  </nav>
}
