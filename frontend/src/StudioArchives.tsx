import { useEffect, useState } from 'react'
import { listRestorePoints } from './api/client'
import { archiveTime, type DraftArchive, type StudioChapter } from './studioPreview'

export function ChapterArchiveRow({ projectId, chapter, current, activeArchive, preview, local, revision, onChapter, onArchive }: {
  projectId?: string
  chapter: StudioChapter; current: boolean; activeArchive?: string; preview: boolean; local: DraftArchive[]; revision: number
  onChapter: () => void; onArchive: (archive: DraftArchive) => void
}) {
  const [open, setOpen] = useState(false)
  const [versions, setVersions] = useState<DraftArchive[]>([])
  const [status, setStatus] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!open || preview || !projectId) return
    let active = true
    listRestorePoints(projectId, chapter.id).then(items => {
      if (active) { setVersions(items.map(item => ({ id: item.id, documentId: item.document_id, versionId: item.version_id, createdAt: item.created_at, summary: item.summary }))); setStatus('') }
    }).catch(() => { if (active) setStatus('存档加载失败') })
    return () => { active = false }
  }, [open, preview, projectId, chapter.id, chapter.versionId, revision, retry])
  const entries = preview ? local : versions
  return <div className="studio-chapter-entry">
    <div className="studio-chapter-row">
      <button type="button" data-status={chapter.published ? 'closed' : 'ongoing'} aria-description={chapter.published ? '已结束' : chapter.review === 'running' ? '进行中，正在审阅' : '进行中'} aria-current={current && !activeArchive ? 'page' : undefined} onClick={onChapter}><i aria-hidden="true" /><span>第{chapter.number}话 {chapter.title}</span></button>
      <button type="button" className="studio-icon studio-archive-toggle" aria-label={`第${chapter.number}话的存档`} title="查看存档" aria-expanded={open} aria-controls={`archives-${chapter.id}`} onClick={() => { if (!open && !preview && chapter.documentId) setStatus('正在加载存档…'); setOpen(!open) }}><img src="/ui/studio/git-commit-outline.svg" alt="" /></button>
    </div>
    <div id={`archives-${chapter.id}`} className={`studio-chapter-archives${open ? ' is-open' : ''}`} inert={!open} aria-hidden={!open}>
      <div><div className="studio-archive-list" role="group" aria-label={`第${chapter.number}话存档列表`}>
        <button type="button" aria-current={current && !activeArchive ? 'true' : undefined} onClick={onChapter}>当前正文<small>{preview ? '本机自动保存' : '最新版本'}</small></button>
        {status ? <div className="studio-archive-status" role="status">{status}{status === '存档加载失败' && <button onClick={() => { setStatus('正在加载存档…'); setRetry(value => value + 1) }}>重试</button>}</div> : entries.length ? entries.map((entry, index) => <button type="button" key={entry.id} aria-current={current && activeArchive === entry.id ? 'true' : undefined} aria-label={`查看第${chapter.number}话存档：${entry.summary} ${archiveTime(entry.createdAt)}`} onClick={() => onArchive(entry)}><span>{entry.summary || `存档 ${entries.length - index}`}</span><small>{archiveTime(entry.createdAt)}</small></button>) : <p>暂无存档{preview && !chapter.published ? '，在 Draft 点击保存创建。' : '。'}</p>}
      </div></div>
    </div>
  </div>
}
