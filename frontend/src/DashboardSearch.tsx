import { useState } from 'react'
import type { Project, SettingCollection } from './api/client'
import CoverImage from './CoverImage'
import SearchDescription from './SearchDescription'
import SearchTags from './SearchTags'
import { novelCoverSource } from './novelCover'
import SearchDialog, { type SearchChoice } from './SearchDialog'

interface Choice extends SearchChoice { project?: Project; collection?: SettingCollection; introduction?: string }
export default function DashboardSearch({ projects, collections, failed, collectionsFailed, onProject, onCollection, onClose }: {
  projects: Project[] | null; collections: SettingCollection[] | null; failed: boolean; collectionsFailed: boolean
  onProject: (project: Project) => void; onCollection: (id: string) => void; onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState('全部')
  const all: Choice[] = [
    ...(projects ?? []).map(project => ({ id: `project:${project.id}`, title: project.title,
      introduction: typeof project.metadata.introduction === 'string' ? project.metadata.introduction : '',
      labels: Array.isArray(project.metadata.labels) ? [...new Set(project.metadata.labels.filter((label): label is string => typeof label === 'string').map(label => label.trim()).filter(Boolean))] : [],
      kind: '作品', project, cover: novelCoverSource(project.metadata.cover) })),
    ...(collections ?? []).map(collection => ({ id: `collection:${collection.id}`, title: collection.title,
      description: collection.description, kind: '设定集', collection, icon: '/ui/icons/collection.svg' })),
  ]
  const choices = all.filter(item => (kind === '全部' || item.kind === kind) && `${item.title} ${item.description ?? ''} ${item.introduction ?? ''} ${item.labels?.join(' ') ?? ''} ${item.project?.slug ?? item.collection?.slug ?? ''} ${item.project?.genre ?? ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  const pending = (kind !== '设定集' && projects === null && !failed) || (kind !== '作品' && collections === null && !collectionsFailed)
  const error = (kind !== '设定集' && failed) || (kind !== '作品' && collectionsFailed)
  return <SearchDialog title="搜索" query={query} onQuery={setQuery} inputLabel="搜索作品" placeholder="搜索作品、设定集…" items={choices}
    onClose={onClose} onSelect={item => { if (item.project) onProject(item.project); else if (item.collection) onCollection(item.collection.id) }}
    filters={<>{['全部', '作品', '设定集'].map(value => <button type="button" key={value} aria-pressed={value === kind} onClick={() => setKind(value)}>{value}</button>)}</>}
    emptyText={pending ? '正在加载…' : error ? '内容加载失败，请关闭后刷新页面重试。' : query.trim() ? '没有找到匹配的作品或设定集' : '还没有作品或设定集'}
    footer={<><span>{error ? '部分内容未能加载' : `${choices.length} 项`}</span><span>↑ ↓ 浏览 · Enter 打开 · Esc 关闭</span></>}
    renderPreview={item => item ? <RecordPreview key={item.id} item={item} projects={projects ?? []} collections={collections ?? []} /> : <><img className="search-preview-icon" src="/ui/setting/search.svg" alt="" /><h3>寻找下一个故事</h3><p>搜索作品与设定集，在这里预览后打开。</p></>} />
}


function RecordPreview({ item, projects, collections }: { item: Choice; projects: Project[]; collections: SettingCollection[] }) {
  const project = item.project
  const bound = project && collections.find(collection => collection.id === project.setting_collection_id)
  const related = item.collection ? projects.filter(work => work.setting_collection_id === item.collection!.id) : []
  const count = project?.metadata.word_count
  const status = project && ({ draft: '草稿', active: '创作中', completed: '已完成', archived: '已归档' } as Record<string, string>)[project.status]
  return <article className="search-record-preview">
    <div className={`search-record-heading${project ? '' : ' is-collection'}`}>
      {project && item.cover ? <div className="search-preview-cover"><CoverImage source={item.cover} widthHint={112} mode="detail" /></div>
        : <div className="search-collection-mark"><img src="/ui/icons/collection.svg" alt="" /></div>}
      <div className="search-record-title"><span className="search-preview-kind">{item.kind}</span><h3>{item.title}</h3>
        {project && <SearchTags labels={item.labels ?? []} />}
        {project && (status || typeof count === 'number') && <p className="search-record-meta">{status && <span>{status}</span>}{typeof count === 'number' && Number.isFinite(count) && count >= 0 && <span>{count.toLocaleString('zh-CN')} 字</span>}</p>}
      </div>
    </div>
    <SearchDescription text={project ? item.introduction ?? '' : item.description ?? ''} label={project ? '简介' : '说明'} />
    {bound && <div className="search-record-relation"><h4>设定集</h4><p><img src="/ui/icons/collection.svg" alt="" />{bound.title}</p></div>}
    {item.collection && <div className="search-record-relation"><h4>关联作品 <span>{related.length}</span></h4>
      {related.length ? <ul>{related.map(work => <li key={work.id}>{work.title}</li>)}</ul> : <p>暂无关联作品</p>}
    </div>}
  </article>
}
