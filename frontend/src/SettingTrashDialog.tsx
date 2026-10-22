import { useEffect, useId, useRef, useState } from 'react'
import PickerDialog from './PickerDialog'
import SettingActionIcon from './SettingActionIcon'
import { categoryNames, type SettingCategory } from './settingNotes'
import './SettingTrash.css'

export interface SettingTrashEntry { id: string; title: string; category: SettingCategory; deletedAt?: string | null }
export interface SettingTrashDialogProps {
  collectionId: string
  readOnly?: boolean
  onLoad: () => Promise<SettingTrashEntry[]>
  onRestore: (entries: SettingTrashEntry[]) => Promise<string[]>
  onClose: () => void
}

export default function SettingTrashDialog(props: SettingTrashDialogProps) {
  return <TrashSession key={props.collectionId} {...props} />
}

function TrashSession({ readOnly = false, onLoad, onRestore, onClose }: SettingTrashDialogProps) {
  const [entries, setEntries] = useState<SettingTrashEntry[]>([])
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)
  const [restoreError, setRestoreError] = useState('')
  const [restoring, setRestoring] = useState(false)
  const [reload, setReload] = useState(0)
  const selectionId = useId()
  const busy = useRef(false)
  const mounted = useRef(false)
  const load = useRef(onLoad)
  useEffect(() => { load.current = onLoad }, [onLoad])
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    let active = true
    void load.current().then(items => {
      if (!active) return
      const unique = [...new Map(items.map(entry => [entry.id, entry])).values()]
      unique.sort((left, right) => (Date.parse(right.deletedAt ?? '') || 0) - (Date.parse(left.deletedAt ?? '') || 0))
      setEntries(unique)
      setSelected(previous => previous.filter(id => unique.some(entry => entry.id === id)))
      setLoading(false)
    }).catch(() => { if (active) { setLoadError(true); setLoading(false) } })
    return () => { active = false }
  }, [reload])
  const normalized = query.trim().toLocaleLowerCase()
  const visible = entries.filter(entry => `${entry.title} ${categoryNames[entry.category]}`.toLocaleLowerCase().includes(normalized))
  const selection = entries.filter(entry => selected.includes(entry.id))
  const hiddenCount = selection.filter(entry => !visible.some(item => item.id === entry.id)).length
  const allVisibleSelected = visible.length > 0 && visible.every(entry => selected.includes(entry.id))
  async function restore(items: SettingTrashEntry[]) {
    if (readOnly || busy.current || !items.length) return
    busy.current = true
    setRestoring(true)
    setRestoreError('')
    try {
      const succeeded = new Set(await onRestore(items))
      if (!mounted.current) return
      setEntries(previous => previous.filter(entry => !succeeded.has(entry.id)))
      const remaining = items.filter(entry => !succeeded.has(entry.id))
      setSelected(previous => [...new Set([...previous.filter(id => !succeeded.has(id)), ...remaining.map(entry => entry.id)])])
      if (remaining.length) setRestoreError(`${remaining.length} 项恢复结果未能确认，请重试核对。`)
    } catch {
      if (mounted.current) setRestoreError('恢复结果未能确认，请重试核对。')
    } finally {
      busy.current = false
      if (mounted.current) setRestoring(false)
    }
  }
  return <PickerDialog title="回收站" className="setting-trash-dialog" closeIcon={<SettingActionIcon name="close" />} onClose={onClose} meta={readOnly ? <span>已归档，只读</span> : undefined}>
    <form className="setting-trash-search" onSubmit={event => event.preventDefault()}>
      <span className="setting-trash-search-icon"><SettingActionIcon name="search" size={20} /></span>
      <input type="search" aria-label="搜索回收站" placeholder="搜索已删除的设定、世界观…" value={query} onChange={event => setQuery(event.target.value)} />
    </form>
    <div className="setting-trash-content" aria-busy={loading || restoring}>
      {loading ? <p className="setting-trash-empty" role="status">正在读取回收站…</p> : loadError ? <div className="setting-trash-empty" role="alert"><p>无法读取回收站，请检查连接后重试。</p><button type="button" onClick={() => { setLoading(true); setLoadError(false); setReload(value => value + 1) }} aria-label="重新加载回收站">重新加载</button></div>
        : !visible.length ? <p className="setting-trash-empty" role="status">{entries.length ? '没有匹配的条目' : '回收站是空的'}</p>
          : <>
            {!readOnly && <label className="setting-trash-select-all"><input type="checkbox" checked={allVisibleSelected} disabled={restoring} onChange={() => setSelected(previous => allVisibleSelected ? previous.filter(id => !visible.some(entry => entry.id === id)) : [...new Set([...previous, ...visible.map(entry => entry.id)])])} />选择{normalized ? '当前结果' : '全部'}</label>}
            <ul className="setting-trash-list" aria-label="已删除条目">{visible.map(entry => <li key={entry.id} aria-label={entry.title}>
              {!readOnly && <input type="checkbox" aria-label={`选择${entry.title}`} checked={selected.includes(entry.id)} disabled={restoring} onChange={() => setSelected(previous => previous.includes(entry.id) ? previous.filter(id => id !== entry.id) : [...previous, entry.id])} />}
              <span className="setting-trash-entry-icon"><SettingActionIcon name="note" /></span>
              <div className="setting-trash-copy"><strong>{entry.title}</strong><span>{categoryNames[entry.category]}<span aria-hidden="true"> · </span>{entry.deletedAt && Number.isFinite(Date.parse(entry.deletedAt)) ? <time dateTime={entry.deletedAt}>{new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(entry.deletedAt))} 删除</time> : '删除时间未知'}</span></div>
              {!readOnly && <button type="button" className="setting-trash-restore" disabled={restoring} aria-label={`恢复${entry.title}`} onClick={() => { void restore([entry]) }}>恢复</button>}
            </li>)}</ul>
          </>}
    </div>
    {restoreError && <p className="setting-trash-error" role="alert">{restoreError}</p>}
    <footer className="setting-trash-footer"><span id={selectionId}>{selection.length && !readOnly ? `已选 ${selection.length} 项${hiddenCount ? `（${hiddenCount} 项不在当前搜索中）` : ''}` : !loading && !loadError ? `${entries.length} 项` : ''}</span>
      {!readOnly && <button type="button" className="setting-trash-restore-selected" disabled={!selection.length || restoring || loading || loadError} aria-describedby={selectionId} onClick={() => { void restore(selection) }}>{restoring ? '正在恢复…' : `恢复所选 ${selection.length} 项`}</button>}
    </footer>
  </PickerDialog>
}
