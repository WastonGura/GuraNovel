import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { listSettingDocumentRestorePoints, readDocumentVersionContent, type SettingDocumentRestorePoint } from './api/client'
import PickerDialog, { type PickerDialogHandle } from './PickerDialog'
import SettingActionIcon from './SettingActionIcon'
import { useSettingArchiveMessages } from './useSettingArchiveMessages'
import './SettingHistoryDialog.css'

export interface SettingHistoryDialogProps {
  documentId: string
  title: string
  readOnly?: boolean
  restoreBlockedReason?: string
  impact?: ReactNode
  onRestore: (point: SettingDocumentRestorePoint, content: string) => Promise<boolean>
  onClose: () => void
}
type Preview = { pointId: string; versionId: string; content: string }
const normalizedMessage = (value: string) => value.trim() || null
function timestamp(value: string) {
  return Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(Date.parse(value)) : '时间未知'
}
function orderedPoints(items: SettingDocumentRestorePoint[], documentId: string) {
  if (items.some(point => point.document_id !== documentId || point.version.document_id !== documentId || point.version_id !== point.version.id)) throw new Error('Invalid archive identity')
  return [...items].sort((left, right) => right.point_number - left.point_number)
}
export default function SettingHistoryDialog(props: SettingHistoryDialogProps) {
  return <HistorySession key={props.documentId} {...props} />
}
function HistorySession({ documentId, title, readOnly = false, restoreBlockedReason, impact, onRestore, onClose }: SettingHistoryDialogProps) {
  const [points, setPoints] = useState<SettingDocumentRestorePoint[]>([])
  const [selectedId, setSelectedId] = useState(''), selection = useRef('')
  const [listLoading, setListLoading] = useState(true), [listError, setListError] = useState(false), [listRetry, setListRetry] = useState(0)
  const [preview, setPreview] = useState<Preview | null>(null), [bodyError, setBodyError] = useState(''), [bodyRetry, setBodyRetry] = useState(0)
  const [confirmation, setConfirmation] = useState<{ point: SettingDocumentRestorePoint; content: string } | null>(null)
  const [restoring, setRestoring] = useState(false), [restoreFinished, setRestoreFinished] = useState(false), [restoreError, setRestoreError] = useState('')
  const [preparing, setPreparing] = useState(false)
  const dialog = useRef<PickerDialogHandle>(null), back = useRef<HTMLButtonElement>(null), listElement = useRef<HTMLElement>(null)
  const copy = useRef<HTMLDivElement>(null), messageFocused = useRef(false)
  const busy = useRef(false), locked = useRef(false), live = useRef(false), closing = useRef(false)
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cache = useRef(new Map<string, string>()), requests = useRef(new Map<string, Promise<string>>())
  const feedbackId = useId(), messageErrorId = useId()
  const messages = useSettingArchiveMessages(documentId, points, readOnly, updated => {
    setPoints(previous => previous.map(point => point.id === updated.id ? updated : point))
  })
  const selected = points.find(point => point.id === selectedId), versionId = selected?.version_id || ''
  const displayed = points.find(point => point.id === preview?.pointId && point.version_id === preview.versionId)
  const bodyReady = Boolean(selected && preview?.pointId === selected.id && preview.versionId === versionId && bodyError !== selected.id)
  const bodyFailed = Boolean(selected && bodyError === selected.id), bodyLoading = Boolean(selected && !bodyReady && !bodyFailed)
  const confirming = Boolean(confirmation), isBusy = restoring, selectionLocked = confirming || preparing
  const message = displayed ? messages.view(displayed) : null
  const blocked = readOnly || Boolean(restoreBlockedReason) || Boolean(selected?.matches_current)
  const feedback = restoreError || (!readOnly ? restoreBlockedReason : '')

  function clearHover() { if (hoverTimer.current) clearTimeout(hoverTimer.current); hoverTimer.current = null }
  useEffect(() => { live.current = true; return () => { live.current = false; clearHover() } }, [])
  useEffect(() => { if (confirming) back.current?.focus() }, [confirming])
  useEffect(() => { if (restoreFinished && !restoring) dialog.current?.close() }, [restoreFinished, restoring])
  useLayoutEffect(() => { if (copy.current) copy.current.scrollTop = 0 }, [preview?.pointId, preview?.versionId])
  useEffect(() => {
    let active = true
    void listSettingDocumentRestorePoints(documentId).then(items => {
      if (!active) return
      const ordered = orderedPoints(items, documentId)
      setPoints(ordered)
      const next = ordered.find(point => point.id === selection.current)?.id || ordered.find(point => point.matches_current)?.id || ordered[0]?.id || ''
      selection.current = next; setSelectedId(next); setListError(false); setListLoading(false)
    }).catch(() => { if (active) { setListError(true); setListLoading(false) } })
    return () => { active = false }
  }, [documentId, listRetry])
  useEffect(() => {
    if (!selectedId || !versionId) return
    let active = true
    let request = requests.current.get(versionId)
    const cached = cache.current.get(versionId)
    if (cached !== undefined) request = Promise.resolve(cached)
    if (!request) {
      request = readDocumentVersionContent(documentId, versionId).then(value => {
        if (value.document_id !== documentId || value.version_id !== versionId) throw new Error('Invalid archive content')
        cache.current.delete(versionId); cache.current.set(versionId, value.content)
        while (cache.current.size > 12) cache.current.delete(cache.current.keys().next().value!)
        return value.content
      }).finally(() => { requests.current.delete(versionId) })
      requests.current.set(versionId, request)
    }
    void request.then(content => {
      if (!active || closing.current) return
      setPreview({ pointId: selectedId, versionId, content }); setBodyError('')
    }).catch(() => { if (active && !closing.current) setBodyError(selectedId) })
    return () => { active = false }
  }, [documentId, selectedId, versionId, bodyRetry])

  function choose(pointId: string) {
    clearHover()
    if (busy.current || locked.current || closing.current || selection.current === pointId) return
    void messages.flush(selection.current)
    messageFocused.current = false
    selection.current = pointId; setSelectedId(pointId); setRestoreError(''); setBodyError('')
  }
  function hover(pointId: string, pointerType: string) {
    clearHover()
    if (!['mouse', 'pen'].includes(pointerType) || busy.current || locked.current || closing.current || messageFocused.current) return
    hoverTimer.current = setTimeout(() => { hoverTimer.current = null; choose(pointId) }, 100)
  }
  function navigateList(event: KeyboardEvent<HTMLElement>) {
    if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    if (busy.current || locked.current) return
    const index = points.findIndex(point => point.id === selectedId)
    const target = event.key === 'Home' ? 0 : event.key === 'End' ? points.length - 1 : Math.max(0, Math.min(points.length - 1, index + (event.key === 'ArrowUp' ? -1 : 1)))
    if (!points[target]) return
    choose(points[target].id); listElement.current?.querySelectorAll<HTMLButtonElement>('.setting-history-version')[target]?.focus()
  }
  async function beginConfirmation() {
    if (!selected || !preview || !bodyReady || blocked || busy.current || locked.current || closing.current) return
    const point = selected, content = preview.content
    clearHover(); locked.current = true; setPreparing(true); setRestoreError('')
    const synced = await messages.flushAll()
    if (!live.current || closing.current) return
    setPreparing(false)
    if (!synced) {
      locked.current = false; setRestoreError('说明尚未同步，请先处理同步问题后恢复。'); return
    }
    const currentMessage = normalizedMessage(messages.view(point).draft)
    setConfirmation({ point: (point.message ?? null) === currentMessage ? point : { ...point, message: currentMessage }, content })
  }
  async function restore() {
    if (!confirmation || !bodyReady || blocked || busy.current || closing.current) return
    busy.current = true; setRestoring(true); setRestoreError(''); clearHover()
    try {
      const restored = await onRestore(confirmation.point, confirmation.content)
      if (!live.current || closing.current) return
      if (restored) setRestoreFinished(true)
      else setRestoreError('恢复未能确认，所选存档和预览已保留。请核对当前内容后重试。')
    } catch (error) {
      if (live.current && !closing.current) setRestoreError(error instanceof Error && error.message.trim() ? error.message : '恢复未能确认，所选存档和预览已保留。请检查连接后重试。')
    } finally { busy.current = false; if (live.current && !closing.current) setRestoring(false) }
  }

  return <PickerDialog ref={dialog} title="历史存档" closeIcon={<SettingActionIcon name="close" />} className="setting-history-dialog" initialFocus=".setting-history-versions" busy={isBusy}
    meta={<span>{readOnly ? '已归档 · 只读' : !listLoading && !listError ? `${points.length} 个存档` : ''}</span>}
    onClosing={() => { closing.current = true; clearHover(); void messages.flushAll() }} onClose={onClose}>
    <div className="setting-history-layout">
      <nav ref={listElement} className="setting-history-versions" aria-label="手动存档列表" tabIndex={0} onKeyDown={navigateList} aria-busy={listLoading}>
        {listLoading && <p className="setting-history-state" role="status">正在读取存档…</p>}
        {listError && <div className="setting-history-state" role="alert"><p>存档列表读取失败，已有预览已保留。请重新加载后继续。</p><button className="setting-history-text-button" type="button" aria-label="重新加载存档" disabled={isBusy || selectionLocked || listLoading} onClick={() => { setListLoading(true); setListError(false); setListRetry(value => value + 1) }}>重新加载</button></div>}
        {!listLoading && !listError && !points.length && <p className="setting-history-state" role="status">还没有手动存档</p>}
        {!!points.length && <ol>{points.map(point => {
          const label = normalizedMessage(messages.view(point).draft)
          return <li key={point.id}><button className="setting-history-version" type="button" aria-label={`存档 ${point.point_number}`} aria-description={label || undefined} title={label || `存档 ${point.point_number}`} aria-pressed={selectedId === point.id} aria-disabled={selectionLocked || isBusy} disabled={isBusy}
          onPointerEnter={event => hover(point.id, event.pointerType)} onPointerLeave={clearHover} onFocus={() => choose(point.id)} onClick={() => choose(point.id)}>
          <span className="setting-history-version-heading"><strong>{label || `存档 ${point.point_number}`}</strong>{point.matches_current && <span className="setting-history-current">当前</span>}</span>
          <span className="setting-history-version-meta">{label && <span>#{point.point_number}</span>}<time dateTime={Number.isFinite(Date.parse(point.created_at)) ? point.created_at : undefined}>{timestamp(point.created_at)}</time></span>
        </button></li>})}</ol>}
      </nav>
      <section className="setting-history-detail" aria-label={confirming ? '确认恢复存档' : '存档预览'} data-preview-point={displayed?.id} data-preview-state={bodyFailed ? 'error' : bodyLoading ? 'loading' : displayed ? 'ready' : 'empty'} data-selection-locked={selectionLocked || undefined}>
        <div key={`${preview?.pointId || 'empty'}:${preview?.versionId || ''}`} className={`setting-history-preview-surface${bodyLoading || bodyFailed ? ' is-pending' : ''}`}>
          <div className="setting-history-document-heading"><span>{confirming ? '恢复存档' : '只读预览'}</span><h3>{displayed?.summary?.trim() || title || '未命名条目'}</h3>
            {displayed && <p>存档 {displayed.point_number}<span aria-hidden="true"> · </span>{timestamp(displayed.created_at)}</p>}
            {displayed && message && !confirming && <div className="setting-history-message">
              {readOnly ? <p aria-label="存档说明" className={displayed.message ? undefined : 'is-empty'}>{displayed.message || '未添加说明'}</p>
                : <div className="setting-history-message-editor"><input type="text" aria-label="存档说明" aria-describedby={message.error ? messageErrorId : undefined} aria-busy={message.pending} maxLength={160} value={message.draft} disabled={!bodyReady || isBusy || preparing} placeholder="添加存档说明…"
                  onFocus={() => { messageFocused.current = true; clearHover() }}
                  onBlur={() => { messageFocused.current = false; void messages.endInput(displayed) }}
                  onChange={event => messages.change(displayed, event.target.value)}
                  onCompositionStart={() => messages.composition(displayed, true)}
                  onCompositionEnd={event => { messages.change(displayed, event.currentTarget.value); messages.composition(displayed, false) }}
                  onKeyDown={event => {
                    if (event.nativeEvent.isComposing || event.keyCode === 229) return
                    if (event.key === 'Escape' || event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); event.currentTarget.blur(); void messages.endInput(displayed) }
                  }} />
                  {message.paused && message.latest !== undefined && <p className="setting-history-latest-message">最新说明：{message.latest || '未添加说明'}</p>}
                  {(message.error || message.paused) && <div className="setting-history-message-actions">
                    {message.conflict ? <button className="setting-history-text-button" type="button" disabled={message.pending} onClick={() => { void messages.readLatest(displayed.id) }}>读取最新说明</button>
                      : <button className="setting-history-text-button" type="button" disabled={message.pending} onClick={() => messages.retry(displayed.id)}>{message.paused ? '继续同步' : '重试同步'}</button>}
                  </div>}
                </div>}
            </div>}
          </div>
          {confirmation ? <div className="setting-history-confirm" tabIndex={0}><h4>将这个存档恢复为当前内容？</h4><p>恢复正文和评论，标题保持当前值；原有存档仍会保留。</p><p>未保存修改会先同步；共享设定影响后续创作，不改写旧章节。</p>{impact && <div className="setting-history-impact">{impact}</div>}</div>
            : <div ref={copy} className="setting-history-copy" role="region" aria-label="存档正文" tabIndex={0} aria-busy={bodyLoading}>
              {preview ? <p className="setting-history-plain">{preview.content || '此存档正文为空。'}</p> : <p className="setting-history-state">{listLoading ? '正在读取存档记录…' : listError ? '读取成功后可预览正文。' : bodyFailed ? '重新读取后可预览正文。' : selected ? '正在读取存档正文…' : '通过页面右上角的存档图标创建存档。'}</p>}
            </div>}
        </div>
        {bodyLoading && preview && <p className="setting-history-preview-status" role="status">正在读取存档 {selected?.point_number}，当前预览保留…</p>}
        {bodyFailed && <div className="setting-history-preview-status" role="alert"><p>存档 {selected?.point_number} 正文读取失败，当前预览已保留。</p><button type="button" className="setting-history-text-button" aria-label="重新读取存档正文" disabled={isBusy || selectionLocked} onClick={() => { setBodyError(''); setBodyRetry(value => value + 1) }}>重新读取</button></div>}
        {message?.error && !confirming && <p id={messageErrorId} className="setting-history-error" role="alert">{message.error}</p>}
        {feedback && <p id={feedbackId} className="setting-history-error" role="alert">{feedback}</p>}
      </section>
    </div>
    <footer className="setting-history-footer"><div>{confirming && <button ref={back} className="setting-history-text-button" type="button" disabled={isBusy} onClick={() => { setConfirmation(null); locked.current = false; setRestoreError('') }}>返回预览</button>}
      <button className="setting-history-restore" type="button" aria-describedby={feedback ? feedbackId : undefined} disabled={!bodyReady || blocked || isBusy || preparing || listLoading || listError}
        onClick={() => { if (confirming) void restore(); else void beginConfirmation() }}>{restoring ? '正在恢复…' : confirming ? '确认恢复' : '恢复此存档'}</button>
    </div></footer>
  </PickerDialog>
}
