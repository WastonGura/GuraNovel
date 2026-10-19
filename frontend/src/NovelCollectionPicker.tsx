import { useState } from 'react'
import type { SettingCollection } from './api/client'
import SearchDialog from './SearchDialog'
import SearchDescription from './SearchDescription'

export default function NovelCollectionPicker({ value, onChange, collections, creating = false }: {
  value: string; onChange: (id: string) => void; collections: SettingCollection[]; creating?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const selected = collections.find(item => item.id === value)
  const special = creating ? { id: 'new', title: '同时创建空白设定集', description: '为这部作品创建一个新世界' }
    : null
  const choices = [...(special ? [special] : []), ...collections].filter(item => `${item.title} ${item.description ?? ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  return <>
    <button className="novel-collection-picker" type="button" aria-label="选择设定集" aria-haspopup="dialog" onClick={() => { setQuery(''); setOpen(true) }}>
      <img src="/ui/icons/collection.svg" alt="" /><span>{selected?.title ?? (special && value === special.id ? special.title : '选择设定集')}</span>
    </button>
    {open && <SearchDialog title="选择设定集" query={query} onQuery={setQuery} inputType="text" inputLabel="搜索设定集" placeholder="搜索设定集…"
      items={choices.map(item => ({ ...item, icon: '/ui/icons/collection.svg', selected: value === item.id }))}
      onSelect={item => onChange(item.id)} onClose={() => setOpen(false)} emptyText="没有找到匹配的设定集"
      renderPreview={item => item ? <article className="search-record-preview"><div className="search-record-heading is-collection"><div className="search-collection-mark"><img src="/ui/icons/collection.svg" alt="" /></div><div className="search-record-title"><span className="search-preview-kind">设定集</span><h3>{item.title}</h3></div></div><SearchDescription key={item.id} text={item.description ?? ''} label="说明" /></article> : <><h3>寻找一个世界</h3><p>搜索已有设定集，预览后选择。</p></>} />}
  </>
}
