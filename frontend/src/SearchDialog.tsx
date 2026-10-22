import { useEffect, useRef, useState, type ReactNode } from 'react'
import PickerDialog, { type PickerDialogHandle } from './PickerDialog'
import './SearchDialog.css'
import CoverImage from './CoverImage'
import SearchTags from './SearchTags'

export interface SearchChoice {
  id: string
  title: string
  description?: string | null
  kind?: string
  icon?: ReactNode
  disabled?: boolean
  selected?: boolean
  cover?: string
  labels?: string[]
}

/** Shared search surface: keyboard results, an independent preview and animated dismissal. */
export default function SearchDialog<T extends SearchChoice>({ title, closeLabel, query, onQuery, inputLabel, placeholder,
  inputType = 'search', items, onSelect, onClose, renderPreview, filters, footer, emptyText = '没有找到匹配的内容',
  resultsLabel = '搜索结果', dismissOnSelect = true, onSubmitQuery, onBeforeSelect, meta, controlIcons }: {
  title: string; closeLabel?: string; query: string; onQuery: (query: string) => void; inputLabel: string; placeholder: string
  inputType?: 'search' | 'text'; items: T[]; onSelect: (item: T) => void; onClose: () => void
  renderPreview: (item: T | undefined) => ReactNode; filters?: ReactNode; footer?: ReactNode; emptyText?: string
  resultsLabel?: string; dismissOnSelect?: boolean; onSubmitQuery?: () => void; meta?: ReactNode
  onBeforeSelect?: (item: T) => boolean | Promise<boolean>
  controlIcons?: { search: ReactNode; close: ReactNode }
}) {
  const picker = useRef<PickerDialogHandle>(null)
  const frame = useRef<HTMLDivElement>(null)
  const [activeId, setActiveId] = useState('')
  const [selectingId, setSelectingId] = useState('')
  const active = items.find(item => item.id === activeId) ?? items[0]
  const pending = useRef<T | null>(null)
  const selecting = useRef(false)
  const closing = useRef(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  async function select(item: T) {
    if (item.disabled || selecting.current || closing.current) return
    if (onBeforeSelect) {
      selecting.current = true
      setSelectingId(item.id)
      const focused = document.activeElement as HTMLElement | null
      let allowed = false
      try { allowed = await onBeforeSelect(item) } catch { /* The caller retains its document and exposes the failure. */ }
      if (!alive.current || closing.current) return
      selecting.current = false
      setSelectingId('')
      if (!allowed) { focused?.focus(); return }
    }
    if (dismissOnSelect) { pending.current = item; picker.current?.close() }
    else { onSelect(item); frame.current?.querySelector('input')?.focus() }
  }
  return <PickerDialog ref={picker} title={title} closeLabel={closeLabel} closeIcon={controlIcons?.close} className="search-dialog" meta={meta}
    onClosing={() => { closing.current = true }}
    onClose={() => { onClose(); if (pending.current) onSelect(pending.current) }}>
    <div ref={frame} className="search-window" onKeyDown={event => {
      if (event.nativeEvent.isComposing || event.keyCode === 229) return
      const input = frame.current?.querySelector('input')
      const buttons = Array.from(frame.current?.querySelectorAll<HTMLButtonElement>('.search-choice:not(:disabled)') ?? [])
      const index = buttons.indexOf(event.target as HTMLButtonElement)
      if (event.target !== input && index < 0) return
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        const next = index + (event.key === 'ArrowDown' ? 1 : -1)
        if (next < 0) input?.focus()
        else buttons[Math.min(next, buttons.length - 1)]?.focus()
      }
    }}>
      <form className="search-window-input" onSubmit={event => {
        event.preventDefault(); event.stopPropagation()
        if (onSubmitQuery) onSubmitQuery()
        else if (active) select(active)
      }}>
        {controlIcons ? <span className="setting-search-symbol">{controlIcons.search}</span> : <img src="/ui/setting/search.svg" alt="" />}
        <input type={inputType} aria-label={inputLabel} placeholder={placeholder} value={query} autoComplete="off"
          onChange={event => { setActiveId(''); onQuery(event.target.value) }} onKeyDown={event => {
            if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.keyCode === 229)) event.preventDefault()
          }} />
        {query && <button type="button" className={controlIcons ? 'setting-icon-button' : undefined} aria-label="清空搜索" title={controlIcons ? '清空搜索' : undefined} onClick={() => { setActiveId(''); onQuery(''); frame.current?.querySelector('input')?.focus() }}>{controlIcons?.close ?? '×'}</button>}
      </form>
      {filters && <div className="search-window-filters">{filters}</div>}
      <div className="search-window-body">
        <div className="search-window-results" role="group" aria-label={resultsLabel} aria-busy={!!selectingId}>
          {items.map(item => <button type="button" key={item.id} className={`search-choice${active?.id === item.id ? ' is-active' : ''}`}
            aria-label={item.title} disabled={item.disabled} aria-pressed={item.selected}
            aria-disabled={item.disabled || !!selectingId} aria-busy={selectingId === item.id}
            onPointerEnter={() => setActiveId(item.id)} onFocus={() => setActiveId(item.id)} onClick={() => select(item)}>
            <span className={`search-choice-leading${item.cover ? ' is-cover' : ''}`}>{item.cover ? <CoverImage source={item.cover} widthHint={40} mode="picker" /> : item.icon && typeof item.icon !== 'string' ? item.icon : <img src={typeof item.icon === 'string' ? item.icon : '/ui/icons/akar-icons_book.svg'} alt="" />}</span>
            <span className="search-choice-copy"><strong>{item.title}</strong>{item.labels?.length ? <SearchTags labels={item.labels} limit={3} /> : item.description && <small>{item.description}</small>}</span>
            <span className="search-choice-kind" aria-hidden="true">{item.selected ? '✓' : item.kind}</span>
          </button>)}
          {!items.length && <p className="search-window-empty" role="status">{emptyText}</p>}
        </div>
        <section key={active?.id ?? 'empty'} className="search-window-preview" aria-label="预览">{renderPreview(active)}</section>
      </div>
      <footer className="search-window-footer">{footer ?? <><span>↑ ↓ 浏览 · Enter 打开</span><span>Esc 关闭</span></>}</footer>
    </div>
  </PickerDialog>
}
