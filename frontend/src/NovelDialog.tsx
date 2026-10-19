import type { ReactNode } from 'react'
import CreationDialog from './CreationDialog'
import type { SettingCollection } from './api/client'

import type { NovelFormValues } from './novelForm'
import NovelCollectionPicker from './NovelCollectionPicker'
import NovelCoverPicker from './NovelCoverPicker'
import StudioLabels from './StudioLabels'

export function NovelDialog({ titleId, closeLabel, busy, onClose, beforeClose, cover, onCoverChange, title, coverTitleId, children }: {
  titleId: string; closeLabel: string; busy: boolean; onClose: () => void
  beforeClose?: () => Promise<boolean>
  cover: string; onCoverChange: (cover: string) => void; title: string; coverTitleId?: string; children: ReactNode
}) {
  return <CreationDialog className="novel-dialog" titleId={titleId} closeLabel={closeLabel} busy={busy} onClose={onClose} beforeClose={beforeClose}>
    <div className="novel-dialog-layout">
      <aside className="novel-cover-column">
        <NovelCoverPicker source={cover} onChange={onCoverChange} disabled={busy} />
        {coverTitleId ? <h2 id={coverTitleId} className="novel-cover-caption">{title}</h2> : <p className="novel-cover-caption">{title || '未命名作品'}</p>}
      </aside>
      <div className="novel-dialog-main">{children}</div>
    </div>
  </CreationDialog>
}

export function NovelFields({ values, onChange, collections, busy, editing = false, errorId, status, summary }: {
  values: NovelFormValues; onChange: (patch: Partial<NovelFormValues>) => void
  collections: SettingCollection[]; busy: boolean; editing?: boolean; errorId?: string
  status?: ReactNode; summary?: ReactNode
}) {
  if (editing) return <fieldset className="novel-metadata-fields" disabled={busy}>
    <div className="novel-metadata-title-row">
      <label className="novel-title-field"><span>标题</span><input aria-label="编辑标题" value={values.title} onChange={event => onChange({ title: event.target.value })} required placeholder="未命名作品" aria-describedby={errorId} /></label>
      {status}
    </div>
    <div className="novel-properties"><div className="novel-statistics">{summary}</div>
    <div className="novel-tags-field" role="group" aria-label="作品标签">
      <div className="novel-property-label"><img src="/ui/studio/label.svg" alt="" /><span>标签</span></div>
      <StudioLabels labels={values.labels.split(/[,，]/).map(label => label.trim()).filter(Boolean)} onChange={labels => onChange({ labels: labels.join(', ') })} />
    </div>
    </div>
    <label className="novel-intro-field"><span>作品简介</span><textarea aria-label="编辑简介" value={values.introduction} onChange={event => onChange({ introduction: event.target.value })} placeholder="作品简介…" rows={5} /></label>
    <details className="novel-metadata-more">
      <summary>更多作品信息</summary>
      <div className="novel-collection-row">
      <div className="novel-collection-field"><span>设定集</span><NovelCollectionPicker value={values.collectionId} onChange={collectionId => onChange({ collectionId })} collections={collections} /></div>
      </div>
      <div className="creation-field-pair">
        <label><span>目标平台</span><input aria-label="编辑目标平台" value={values.platform} onChange={event => onChange({ platform: event.target.value })} placeholder="准备在哪里连载" /></label>
      </div>
    </details>
  </fieldset>
  return <fieldset className="creation-fields" disabled={busy}>
    <label>标题<input value={values.title} onChange={event => onChange({ title: event.target.value })} required placeholder="你的故事叫什么？" aria-describedby={errorId} /></label>
    <div className="novel-collection-field"><span>设定集</span><NovelCollectionPicker value={values.collectionId} onChange={collectionId => onChange({ collectionId })} collections={collections} creating /></div>
    <label>简介（可选）<textarea value={values.introduction} onChange={event => onChange({ introduction: event.target.value })} rows={3} placeholder="一句话，记下这个故事的起点…" /></label>
    <details className="creation-disclosure">
      <summary>更多作品信息</summary>
      <div className="creation-field-pair">
        <label>目标平台（可选）<input value={values.platform} onChange={event => onChange({ platform: event.target.value })} placeholder="准备在哪里连载" /></label>
      </div>
    </details>
  </fieldset>
}
