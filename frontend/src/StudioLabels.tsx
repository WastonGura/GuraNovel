import { useState } from 'react'
import SearchDialog from './SearchDialog'
import './StudioLabels.css'

const limit = 8
const commonLabels = ['都市', '科幻', '奇幻', '玄幻', '仙侠', '悬疑', '推理', '历史', '武侠', '言情', '校园', '冒险', '日常', '治愈', '群像', '成长', '末世', '赛博朋克']
const normalize = (value: string) => value.trim().replace(/\s+/g, ' ')

export default function StudioLabels({ labels, onChange }: { labels: string[]; onChange: (labels: string[]) => void }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [message, setMessage] = useState('')
  const contains = (label: string) => labels.some(item => item.toLocaleLowerCase() === label.toLocaleLowerCase())
  const candidates = commonLabels.filter(label => label.toLocaleLowerCase().includes(normalize(query).toLocaleLowerCase()))

  function add(value: string) {
    const label = normalize(value)
    if (!label) { setMessage('请输入标签名称。'); return }
    if ([...label].length > 16) { setMessage('标签名称最多 16 个字。'); return }
    if (contains(label)) { setMessage('已添加这个标签。'); return }
    if (labels.length >= limit) { setMessage(`最多添加 ${limit} 个标签，请先移除一个。`); return }
    onChange([...labels, label]); setQuery(''); setMessage(`已添加「${label}」`)
  }

  return <>
    <div className="studio-detail-labels" role="group" aria-label="小说标签">
      {labels.map(label => <button className="studio-label" type="button" key={label} aria-label={`删除标签：${label}`} title={`删除「${label}」`} onClick={event => {
        const next = event.currentTarget.nextElementSibling as HTMLButtonElement | null
        onChange(labels.filter(item => item !== label)); setMessage(`已删除「${label}」`); next?.focus()
      }}><span className="studio-label-name" aria-hidden="true">{label}</span><span className="studio-label-remove" aria-hidden="true">×</span></button>)}
      <button className="studio-label studio-label-add" type="button" aria-label="添加标签" aria-haspopup="dialog" title={labels.length >= limit ? `已达 ${limit} 个标签上限` : '添加标签'} onClick={() => {
        setQuery(''); setMessage(''); setOpen(true)
      }}><img src="/ui/studio/add.svg" alt="" /></button>
    </div>
    <span className="studio-announcer" role="status">{message}</span>
    {open && <SearchDialog title="添加标签" closeLabel="关闭标签选择" meta={<span>{labels.length} / {limit}</span>} onClose={() => setOpen(false)}
      query={query} onQuery={value => { setQuery(value); setMessage('') }} inputType="text" inputLabel="搜索或输入标签" placeholder="搜索标签，或输入后按 Enter 添加"
      items={candidates.map(label => ({ id: label, title: label, icon: '/ui/studio/label.svg', selected: contains(label), disabled: contains(label) || labels.length >= limit }))}
      resultsLabel="常用标签" dismissOnSelect={false} onSelect={item => add(item.title)} onSubmitQuery={() => add(query)}
      emptyText="没有匹配的常用标签，按 Enter 添加自定义标签。"
      footer={<><p role="status">{message || (labels.length >= limit ? '已达上限，请先关闭此框并移除一个标签。' : '最多 8 个标签 · 每个最多 16 个字')}</p><span>Esc 关闭</span></>}
      renderPreview={() => <><img className="search-preview-icon" src="/ui/studio/label.svg" alt="" /><h3>故事的关键词</h3><p>已选标签</p><div className="search-label-selection">{labels.map(label => <span key={label}>{label}</span>)}</div><p>选择常用标签，也可以输入自己的关键词，按 Enter 添加。</p></>} />}
  </>
}
