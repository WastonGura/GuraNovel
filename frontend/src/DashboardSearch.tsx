import { useState } from 'react'
import type { Project, SettingCollection } from './api/client'
import SearchDialog from './SearchDialog'
import SearchRecordPreview from './SearchRecordPreview'
import { collectionSearchRecord, matchesSearchRecord, projectSearchRecord, type SearchRecord } from './searchRecords'

export default function DashboardSearch({ projects, collections, failed, collectionsFailed, onProject, onCollection, onClose }: {
  projects: Project[] | null; collections: SettingCollection[] | null; failed: boolean; collectionsFailed: boolean
  onProject: (project: Project) => void; onCollection: (id: string) => void; onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState('全部')
  const all: SearchRecord[] = [...(projects ?? []).map(projectSearchRecord), ...(collections ?? []).map(collectionSearchRecord)]
  const choices = all.filter(item => (kind === '全部' || item.kind === kind) && matchesSearchRecord(item, query))
  const pending = (kind !== '设定集' && projects === null && !failed) || (kind !== '作品' && collections === null && !collectionsFailed)
  const error = (kind !== '设定集' && failed) || (kind !== '作品' && collectionsFailed)
  return <SearchDialog title="搜索" query={query} onQuery={setQuery} inputLabel="搜索作品" placeholder="搜索作品、设定集…" items={choices}
    onClose={onClose} onSelect={item => { if (item.project) onProject(item.project); else if (item.collection) onCollection(item.collection.id) }}
    filters={<>{['全部', '作品', '设定集'].map(value => <button type="button" key={value} aria-pressed={value === kind} onClick={() => setKind(value)}>{value}</button>)}</>}
    emptyText={pending ? '正在加载…' : error ? '内容加载失败，请关闭后刷新页面重试。' : query.trim() ? '没有找到匹配的作品或设定集' : '还没有作品或设定集'}
    footer={<><span>{error ? '部分内容未能加载' : `${choices.length} 项`}</span><span>↑ ↓ 浏览 · Enter 打开 · Esc 关闭</span></>}
    renderPreview={item => item ? <SearchRecordPreview key={item.id} item={item} projects={projects ?? []} collections={collections ?? []} /> : <><img className="search-preview-icon" src="/ui/setting/search.svg" alt="" /><h3>寻找下一个故事</h3><p>搜索作品与设定集，在这里预览后打开。</p></>} />
}
