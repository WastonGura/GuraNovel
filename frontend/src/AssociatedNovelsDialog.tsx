import { useEffect, useState } from 'react'
import { listCollectionProjects, type Project } from './api/client'
import SearchDialog from './SearchDialog'
import SettingActionIcon from './SettingActionIcon'
import SearchRecordPreview from './SearchRecordPreview'
import { matchesSearchRecord, projectSearchRecord } from './searchRecords'

export interface AssociatedNovelsDialogProps {
  collectionId: string
  onBeforeOpen?: (project: Project) => Promise<boolean>
  onOpenProject: (project: Project) => void
  onProjectsLoaded?: (projects: Project[]) => void
  onClose: () => void
}

export default function AssociatedNovelsDialog(props: AssociatedNovelsDialogProps) {
  return <AssociatedNovelsSession key={props.collectionId} {...props} />
}

function AssociatedNovelsSession({ collectionId, onBeforeOpen, onOpenProject, onProjectsLoaded, onClose }: AssociatedNovelsDialogProps) {
  const [query, setQuery] = useState('')
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [openError, setOpenError] = useState('')
  useEffect(() => {
    let active = true
    void listCollectionProjects(collectionId).then(result => {
      if (!active) return
      setProjects(result)
      onProjectsLoaded?.(result)
    }).catch(() => { if (active) setFailed(true) })
    return () => { active = false }
  }, [collectionId, attempt, onProjectsLoaded])
  const choices = (projects ?? []).map(projectSearchRecord).filter(item => matchesSearchRecord(item, query))
  async function protectDocument(project: Project): Promise<boolean> {
    setOpenError('')
    try {
      const allowed = await onBeforeOpen?.(project) ?? true
      if (!allowed) setOpenError('当前文稿未能保存，请重试打开。')
      return allowed
    } catch {
      setOpenError('当前文稿未能保存，请重试打开。')
      return false
    }
  }
  return <SearchDialog title="关联作品" query={query} onQuery={setQuery} inputLabel="搜索关联作品" placeholder="搜索关联作品…"
    controlIcons={{ search: <SettingActionIcon name="search" />, close: <SettingActionIcon name="close" /> }}
    items={choices} onClose={onClose} onSelect={item => onOpenProject(item.project)} onBeforeSelect={item => protectDocument(item.project)}
    filters={failed && <button type="button" aria-label="重新加载关联作品" onClick={() => { setFailed(false); setAttempt(value => value + 1) }}>重新加载</button>}
    emptyText={failed ? '暂时无法读取关联作品' : projects === null ? '正在加载关联作品…' : query.trim() ? '没有找到匹配的关联作品' : '暂无关联作品'}
    footer={<><span role={failed || openError ? 'alert' : undefined}>{openError || (failed ? '关联作品加载失败，请重试。' : projects === null ? '正在加载…' : `${choices.length} 部作品`)}</span><span>↑ ↓ 浏览 · Enter 打开 · Esc 关闭</span></>}
    renderPreview={item => item ? <SearchRecordPreview key={item.id} item={item} /> : <><img className="search-preview-icon" src="/ui/icons/collection.svg" alt="" /><h3>同一个世界里的故事</h3><p>在这里预览关联作品，再打开作品资料。</p></>} />
}
