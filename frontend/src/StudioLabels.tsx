import { useRef, useState } from 'react'

const limit = 8
const commonLabels = ['都市', '科幻', '奇幻', '玄幻', '仙侠', '悬疑', '推理', '历史', '武侠', '言情', '校园', '冒险', '日常', '治愈', '群像', '成长', '末世', '赛博朋克']
const normalize = (value: string) => value.trim().replace(/\s+/g, ' ')

export default function StudioLabels({ projectKey, initial }: { projectKey: string; initial: string[] }) {
  const storageKey = `guranovel:studio-labels:${projectKey}`
  const [labels, setLabels] = useState<string[]>(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(storageKey) || 'null')
      if (Array.isArray(saved) && saved.every(label => typeof label === 'string' && label.trim())) return [...new Set(saved)]
    } catch { /* Invalid local data must not prevent opening the novel. */ }
    return initial
  })
  const [query, setQuery] = useState('')
  const [message, setMessage] = useState('')
  const dialog = useRef<HTMLDialogElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const launcher = useRef<HTMLButtonElement>(null)
  const contains = (label: string) => labels.some(item => item.toLocaleLowerCase() === label.toLocaleLowerCase())
  const candidates = commonLabels.filter(label => label.toLocaleLowerCase().includes(normalize(query).toLocaleLowerCase()))

  function save(next: string[]) {
    try { localStorage.setItem(storageKey, JSON.stringify(next)) }
    catch { setMessage('本机保存失败，请重试。'); return false }
    setLabels(next)
    return true
  }
  function add(value: string) {
    const label = normalize(value)
    if (!label) { setMessage('请输入标签名称。'); return }
    if ([...label].length > 16) { setMessage('标签名称最多 16 个字。'); return }
    if (contains(label)) { setMessage('已添加这个标签。'); return }
    if (labels.length >= limit) { setMessage(`最多添加 ${limit} 个标签，请先移除一个。`); return }
    if (save([...labels, label])) { setQuery(''); setMessage(`已添加「${label}」`); input.current?.focus() }
  }

  return <>
    <div className="studio-detail-labels" role="group" aria-label="小说标签">
      {labels.map(label => <button className="studio-label" type="button" key={label} aria-label={`删除标签：${label}`} title={`删除「${label}」`} onClick={event => {
        const next = event.currentTarget.nextElementSibling as HTMLButtonElement | null
        if (save(labels.filter(item => item !== label))) { setMessage(`已删除「${label}」`); next?.focus() }
      }}><span className="studio-label-name" aria-hidden="true">{label}</span><span className="studio-label-remove" aria-hidden="true">×</span></button>)}
      <button ref={launcher} className="studio-label studio-label-add" type="button" aria-label="添加标签" aria-haspopup="dialog" title={labels.length >= limit ? `已达 ${limit} 个标签上限` : '添加标签'} onClick={() => {
        setQuery(''); setMessage(''); dialog.current?.showModal(); input.current?.focus()
      }}><img src="/ui/studio/add.svg" alt="" /></button>
    </div>
    <span className={message === '本机保存失败，请重试。' ? 'studio-label-error' : 'studio-announcer'} role="status">{message}</span>
    <dialog ref={dialog} className="studio-label-dialog" aria-labelledby="studio-label-title" onClose={() => launcher.current?.focus()} onClick={event => { if (event.target === event.currentTarget) dialog.current?.close() }}>
      <div>
        <header><h2 id="studio-label-title">添加标签</h2><span>{labels.length} / {limit}</span><button type="button" aria-label="关闭标签选择" onClick={() => dialog.current?.close()}>×</button></header>
        <form onSubmit={event => { event.preventDefault(); add(query) }}>
          <input ref={input} aria-label="搜索或输入标签" placeholder="搜索标签，或输入后按 Enter 添加" value={query} onChange={event => { setQuery(event.target.value); setMessage('') }} onKeyDown={event => { if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.keyCode === 229)) event.preventDefault() }} />
        </form>
        <p className="studio-label-hint">{labels.length >= limit ? '已达上限，请先关闭此框并移除一个标签。' : '最多 8 个标签 · 每个最多 16 个字'}</p>
        <div className="studio-label-options" role="group" aria-label="常用标签">
          {candidates.map(label => <button type="button" className="studio-label" key={label} disabled={contains(label) || labels.length >= limit} onClick={() => add(label)}>{label}{contains(label) && <span aria-label="已添加"> ✓</span>}</button>)}
          {!candidates.length && <p>没有匹配的常用标签，按 Enter 添加自定义标签。</p>}
        </div>
        <p className="studio-label-feedback" role="status">{message}</p>
        <footer>标签保存到本机 · 尚未同步到服务器</footer>
      </div>
    </dialog>
  </>
}
