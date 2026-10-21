import type { Project, SettingCollection } from './api/client'
import CoverImage from './CoverImage'
import SearchDescription from './SearchDescription'
import SearchTags from './SearchTags'
import { projectStatusLabel, type SearchRecord } from './searchRecords'

export default function SearchRecordPreview({ item, projects = [], collections = [] }: { item: SearchRecord; projects?: Project[]; collections?: SettingCollection[] }) {
  const project = item.project
  const bound = project && collections.find(collection => collection.id === project.setting_collection_id)
  const related = item.collection ? projects.filter(work => work.setting_collection_id === item.collection!.id) : []
  const count = project?.metadata.word_count
  const status = project && projectStatusLabel(project.status)
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
