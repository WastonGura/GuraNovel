import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { commentColors as colors, commentLimit, reanchorComments, type OutlineComment } from './studioPreview'
import { dissolvePanel } from './studioPanelParticles'
import { useFloatingComment } from './useFloatingComment'
import { useCommentDrag } from './useCommentDrag'

const tint = (color: string) => ({ '--comment-color': color }) as CSSProperties

export default function OutlineEditor({ outline, comments, readOnly, textReadOnly = false, onChange, onCommentsChange, onSend, prepareSend, confirm, draftInput }: {
  outline: string; comments: OutlineComment[]; readOnly: boolean; textReadOnly?: boolean
  prepareSend?: (animate?: () => Promise<void>) => Promise<boolean>
  onChange: (outline: string, comments: OutlineComment[]) => void
  onCommentsChange: (comments: OutlineComment[]) => void; onSend: (panel?: HTMLElement) => void; confirm?: ReactNode
  draftInput?: { ref: RefObject<HTMLTextAreaElement | null>; onTyping: () => void; ready?: boolean; visible?: boolean; onSubmittedSelect?: (id: string) => void; label?: string; className?: string; maxLength?: number; sendLabel?: string; sendEffect?: 'handoff'; stagedIds?: string[]; reading?: ReactNode; linkOptions?: string[]; animateText?: boolean }
}) {
  const outlineInput = useRef<HTMLTextAreaElement>(null)
  const input = draftInput?.ref || outlineInput
  const mirror = useRef<HTMLDivElement>(null)
  const commentInput = useRef<HTMLTextAreaElement>(null)
  const motions = useRef<Animation[]>([])
  const stopDissolve = useRef<() => void>(() => {})
  const [sending, setSending] = useState(false)
  const [limitMessage, setLimitMessage] = useState('')
  const focusComment = useRef(false)
  const commentNav = useRef<HTMLDivElement>(null)
  const [slots, setSlots] = useState(5)
  const sheet = useRef<HTMLDivElement>(null)
  const selectionAction = useRef<HTMLButtonElement>(null)
  const readingRoot = useRef<HTMLDivElement>(null)
  const [link, setLink] = useState<{ start: number; end: number; query: string } | null>(null)
  const [linkIndex, setLinkIndex] = useState(0)
  const completion = useRef<HTMLDivElement>(null)
  const linkSearch = useRef<HTMLInputElement>(null)
  const linkMatches = draftInput?.linkOptions?.filter(title => title.toLocaleLowerCase().includes(link?.query.toLocaleLowerCase() || '')).slice(0, 8) || []
  function suggestLinks(force = false) {
    const textarea = input.current
    if (!textarea || !draftInput?.linkOptions || readOnly) return
    const end = textarea.selectionStart, prefix = textarea.value.slice(0, end)
    const match = /(?:@|\[\[)([^\n[\]@]*)$/.exec(prefix)
    setLink(match ? { start: match.index, end, query: match[1] } : force ? { start: end, end, query: '' } : null); setLinkIndex(0)
  }
  function insertLink(title: string) {
    if (!link) return
    const suffix = outline.slice(link.end).startsWith(']]') ? link.end + 2 : link.end
    const value = `${outline.slice(0, link.start)}[[${title}]]${outline.slice(suffix)}`
    onChange(value, reanchorComments(comments, outline, value)); setLink(null)
    requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(link.start + title.length + 4, link.start + title.length + 4) })
  }
  function linkKeyDown(event: React.KeyboardEvent) {
    if (!link || event.nativeEvent.isComposing) return false
    if (!['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape'].includes(event.key)) return false
    event.preventDefault(); event.stopPropagation()
    if (event.key === 'Escape') { setLink(null); input.current?.focus({ preventScroll: true }) }
    else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const next = Math.max(-1, Math.min(linkMatches.length - 1, linkIndex + (event.key === 'ArrowDown' ? 1 : -1)))
      setLinkIndex(next)
      if (next === -1) linkSearch.current?.focus({ preventScroll: true })
    } else if (linkMatches[linkIndex]) insertLink(linkMatches[linkIndex])
    return true
  }
  useLayoutEffect(() => {
    const panel = completion.current
    if (!panel || !link) return
    const row = linkIndex < 0 ? linkSearch.current : panel.querySelector<HTMLElement>(`[data-link-index="${linkIndex}"]`)
    if (!row) return
    const bounds = panel.getBoundingClientRect(), rect = row.getBoundingClientRect()
    if (rect.top < bounds.top + 8) panel.scrollTop -= bounds.top + 8 - rect.top
    else if (rect.bottom > bounds.bottom - 8) panel.scrollTop += rect.bottom - bounds.bottom + 8
  }, [link, linkIndex])
  useEffect(() => {
    if (!link) return
    const dismiss = (event: PointerEvent) => {
      if (!completion.current?.contains(event.target as Node) && event.target !== input.current && !(event.target as HTMLElement).closest('.setting-insert-link')) setLink(null)
    }
    document.addEventListener('pointerdown', dismiss)
    return () => document.removeEventListener('pointerdown', dismiss)
  }, [link, input])
  useLayoutEffect(() => {
    if (!link || !completion.current || !mirror.current || !input.current) return
    const walker = document.createTreeWalker(mirror.current, NodeFilter.SHOW_TEXT)
    let offset = link.end, node = walker.nextNode()
    while (node && offset > (node.textContent?.length || 0)) { offset -= node.textContent?.length || 0; node = walker.nextNode() }
    let rect = input.current.getBoundingClientRect()
    if (node) { const range = document.createRange(); range.setStart(node, offset); range.collapse(true); rect = range.getBoundingClientRect?.() || rect }
    Object.assign(completion.current.style, { left: `${Math.max(12, Math.min(rect.left, input.current.getBoundingClientRect().right - 320, window.innerWidth - 332))}px`, top: `${Math.max(85, Math.min(rect.bottom + 8, window.innerHeight - 320))}px` })
  }, [link, outline, input, draftInput?.visible, draftInput?.ready])
  const [selection, setSelection] = useState<{ start: number; end: number; x: number; y: number } | null>(null)
  const pending = draftInput ? comments.filter(comment => !comment.submitted && !draftInput.stagedIds?.includes(comment.id)) : comments
  const isDraft = Boolean(draftInput)
  const isReading = Boolean(draftInput?.reading)
  const active = pending[0]
  const panelVisible = !draftInput || (!readOnly && pending.length > 0 && draftInput.visible !== false)
  const dots = useCommentDrag(panelVisible && !readOnly && !sending, ids => onCommentsChange([
    ...ids.map(id => pending.find(comment => comment.id === id)!),
    ...comments.filter(comment => !pending.some(item => item.id === comment.id)),
  ]))
  const rows = Math.ceil(Math.max(0, pending.length - 1) / slots)
  const { panel, wake: wakePanel } = useFloatingComment(sheet, Boolean(draftInput && panelVisible), draftInput?.ready !== false, active?.id || '')
  const [hits, setHits] = useState<{ id: string; left: number; top: number; width: number }[]>([])
  const position = (index: number) => ({ x: index ? (index - 1) % slots * 24 : 0, y: index ? Math.floor((index - 1) / slots) * 26 : rows * 26 })
  const transform = (index: number) => { const { x, y } = position(index); return `translate(${x}px, ${y}px)` }

  useEffect(() => () => { stopDissolve.current(); motions.current.forEach(animation => animation.cancel()) }, [])

  useLayoutEffect(() => {
    const layer = mirror.current
    if (!isDraft || !layer) return
    const measure = () => {
      const origin = layer.parentElement!.getBoundingClientRect()
      const next: typeof hits = []
      layer.querySelectorAll<HTMLElement>('[data-comment-id]').forEach(mark => {
        const range = document.createRange(); range.selectNodeContents(mark)
        for (const rect of range.getClientRects?.() || []) if (rect.width) next.push({ id: mark.dataset.commentId!, left: rect.left - origin.left, top: rect.bottom - origin.top - 1, width: rect.width })
      })
      setHits(before => JSON.stringify(before) === JSON.stringify(next) ? before : next)
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(layer); input.current?.addEventListener('scroll', measure)
    const textarea = input.current
    return () => { observer?.disconnect(); textarea?.removeEventListener('scroll', measure) }
  }, [outline, comments, input, isDraft, isReading])

  useEffect(() => {
    const nav = commentNav.current
    if (!nav || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => setSlots(Math.max(2, Math.floor(nav.clientWidth / 24))))
    observer.observe(nav)
    return () => observer.disconnect()
  }, [panelVisible])

  useEffect(() => {
    if (focusComment.current && commentInput.current) { commentInput.current.focus(); focusComment.current = false }
  }, [comments])

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (event.target !== input.current && !selectionAction.current?.contains(event.target as Node)) setSelection(null)
    }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setSelection(null) }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', escape) }
  }, [input, isReading])

  useEffect(() => {
    const textarea = input.current, layer = mirror.current
    if (!textarea || !layer) return
    const sync = () => {
      layer.style.width = `${textarea.clientWidth}px`
      layer.style.transform = `translate(${-textarea.scrollLeft}px, ${-textarea.scrollTop}px)`
    }
    sync()
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(sync) : null
    observer?.observe(textarea)
    textarea.addEventListener('scroll', sync)
    return () => { observer?.disconnect(); textarea.removeEventListener('scroll', sync) }
  }, [input, isReading])

  function selectComment(index: number) {
    wakePanel.current()
    if (!index) return
    const next = [pending[index], ...comments.filter(comment => comment.id !== pending[index].id)]
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const animations: Animation[] = []
    const buttons = [...(dots.current?.querySelectorAll<HTMLButtonElement>('button') || [])]
    const interrupted = motions.current.some(animation => animation.playState === 'running' || animation.pending)
    const starts = buttons.map((button, index) => interrupted ? getComputedStyle(button).transform : transform(index))
    motions.current.forEach(animation => animation.cancel()); motions.current = []
    if (!reduced) buttons.forEach((button, oldIndex) => {
      if (!button.animate || (!interrupted && oldIndex > index)) return
      const from = starts[oldIndex]
      const to = transform(oldIndex === index ? 0 : oldIndex < index ? oldIndex + 1 : oldIndex)
      const middle = oldIndex === index ? `translate(${position(oldIndex).x}px, ${rows * 26}px)` : oldIndex === 0 ? from : to
      animations.push(button.animate([
        { transform: from },
        ...(!interrupted && index > 1 ? [{ transform: middle, offset: .48 }] : []),
        { transform: to },
      ], { duration: interrupted || index === 1 ? 320 : 560, easing: 'cubic-bezier(.4, 0, .2, 1)' }))
    })
    motions.current = animations
    animations.forEach(animation => { void animation.finished.catch(() => {}) })
    onCommentsChange(next)
  }

  function offerComment(point?: { clientX: number; clientY: number }) {
    const textarea = input.current
    const bounds = sheet.current?.getBoundingClientRect()
    if (!bounds || readOnly || sending) return
    let start = textarea?.selectionStart || 0, end = textarea?.selectionEnd || 0
    if (draftInput?.reading) {
      const selected = window.getSelection()
      if (!selected?.rangeCount || selected.isCollapsed || !readingRoot.current?.contains(selected.getRangeAt(0).commonAncestorContainer)) { setSelection(null); return }
      const range = selected.getRangeAt(0), parts: { start: number; end: number }[] = []
      readingRoot.current.querySelectorAll<HTMLElement>('[data-source-start]').forEach(span => {
        if (!range.intersectsNode(span)) return
        const part = document.createRange(); part.selectNodeContents(span)
        if (span.contains(range.startContainer)) part.setStart(range.startContainer, range.startOffset)
        if (span.contains(range.endContainer)) part.setEnd(range.endContainer, range.endOffset)
        if (!part.toString()) return
        const prefix = document.createRange(); prefix.selectNodeContents(span); prefix.setEnd(part.startContainer, part.startOffset)
        const from = Number(span.dataset.sourceStart) + prefix.toString().length
        parts.push({ start: from, end: from + part.toString().length })
      })
      if (!parts.length) return
      start = parts[0].start; end = parts.at(-1)!.end
    } else if (!textarea) return
    if (!outline.slice(start, end).trim()) { setSelection(null); return }
    let x = point ? point.clientX - bounds.left : 26, y = point ? point.clientY - bounds.top + 12 : 60
    if (!point && mirror.current) {
      const walker = document.createTreeWalker(mirror.current, NodeFilter.SHOW_TEXT)
      let remaining = end, node = walker.nextNode()
      while (node && remaining > (node.textContent?.length || 0)) { remaining -= node.textContent?.length || 0; node = walker.nextNode() }
      if (node) {
        const range = document.createRange()
        range.setStart(node, remaining); range.collapse(true)
        const rect = range.getBoundingClientRect?.()
        if (rect) { x = rect.left - bounds.left; y = rect.bottom - bounds.top + 8 }
      }
    }
    setSelection({ start, end, x: Math.max(8, Math.min(x, bounds.width - 76)), y: Math.max(8, Math.min(y, bounds.height - 48)) })
  }

  function annotateSelection() {
    if (!selection || readOnly || sending) return
    focusComment.current = true
    wakePanel.current()
    const { start, end } = selection
    const quote = outline.slice(start, end)
    if (quote.length > 8192) { setLimitMessage('一次评论最多引用 8192 个字符，请缩小选区。'); setSelection(null); return }
    const existing = pending.findIndex(comment => comment.start === start && comment.end === end)
    if (existing >= 0) selectComment(existing)
    else {
      const unused = colors.filter(color => !comments.some(comment => comment.color === color))
      if (comments.length >= commentLimit || !unused.length) { setLimitMessage(`评论上限为 ${commentLimit} 条，请先移除不再需要的评论。`); setSelection(null); return }
      setLimitMessage('')
      onCommentsChange([{ id: crypto.randomUUID(), start, end, quote, color: unused[crypto.getRandomValues(new Uint32Array(1))[0] % unused.length], text: '' }, ...comments])
    }
    setSelection(null)
    commentInput.current?.focus()
  }

  const boundaries = [...new Set([0, outline.length, ...comments.flatMap(comment => [comment.start, comment.end])])].sort((a, b) => a - b)
  const commentPanel = panelVisible && <div ref={panel} className={`studio-outline-comment${draftInput ? ' studio-floating-comment' : ''}`} style={tint(active?.color || '#c7c7c7')} inert={sending} aria-busy={sending}>
      {draftInput && <button type="button" className="studio-comment-grip" aria-label="拖动评论框" title="拖动移动；方向键微调，Home 归位"><span /></button>}
      <div className="studio-comment-dots-viewport" ref={commentNav}>
        <div className="studio-comment-dots" ref={dots} role="group" aria-label={draftInput ? '正文评论' : '大纲评论'} style={{ height: pending.length ? (rows + 1) * 26 : 0 }}>
          {pending.map((comment, index) => <button type="button" key={comment.id} data-comment-id={comment.id} aria-pressed={index === 0}
            aria-description="拖动排序；拖出评论框松手移除；Alt 加左右方向键排序，Delete 移除"
            aria-label={`评论：${comment.quote}`} title={comment.quote} onClick={() => selectComment(index)}
            style={{ ...tint(comment.color), transform: transform(index) }} />)}
        </div>
      </div>
      <label>给 Agent 的修改意见
        <textarea ref={commentInput} maxLength={4000} value={active?.text || ''} disabled={!active || readOnly}
          onChange={event => onCommentsChange(comments.map(comment => comment.id === active?.id ? { ...comment, text: event.target.value } : comment))}
          placeholder={active ? '哪里需要再调整？' : '选中左侧大纲文本，添加评论…'} />
      </label>
      {active?.start === active?.end && active && <small>{active.orphaned ? '原文位置已变化，评论仍保留。' : '对应原文已删除，评论仍保留。'}</small>}
      <small className="studio-comment-count">{comments.length}/{commentLimit}</small>
      <button type="button" className="studio-icon is-round-asset" aria-label={draftInput?.sendLabel || (draftInput ? '发送正文修改意见' : '发送大纲修改意见')} title={draftInput?.sendLabel || (draftInput ? '发送正文修改意见' : '发送大纲修改意见')}
        disabled={!active?.text.trim() || readOnly || sending} onClick={() => {
          if (prepareSend) {
            if (pending.some(comment => !comment.text.trim())) { setLimitMessage('请先填写每条待提交评论，或移除空白评论。'); return }
            setLimitMessage('')
            setSending(true)
            const animate = draftInput ? () => new Promise<void>(resolve => {
              if (!panel.current) { resolve(); return }
              const cancel = dissolvePanel(panel.current, resolve)
              stopDissolve.current = () => { cancel(); resolve() }
            }) : undefined
            void prepareSend(animate).then(saved => { if (saved) onSend(); else setLimitMessage('评论尚未提交，请先重试保存。') })
              .catch(() => setLimitMessage('评论提交失败，内容仍保留。')).finally(() => setSending(false))
            return
          }
          if (!draftInput) { onSend(); return }
          if (pending.some(comment => !comment.text.trim())) { setLimitMessage('请先填写每条待提交评论，或移除空白评论。'); return }
          if (draftInput.sendEffect === 'handoff') { onSend(panel.current!); return }
          setSending(true)
          stopDissolve.current = dissolvePanel(panel.current!, () => { onSend(); setSending(false) })
        }}><span className="studio-icon-glyph" aria-hidden="true"><img src="/ui/studio/send.svg" alt="" /></span></button>
    </div>
  return <div className={`studio-outline-edit${draftInput ? ' is-draft' : ''}`}>
    <div className="studio-outline-sheet" ref={sheet}>
      <div className="studio-annotated-outline">
        {!draftInput?.reading && <div className="studio-outline-mirror" data-sweep-text={draftInput?.animateText ? '' : undefined} aria-hidden="true" ref={mirror}>
          {boundaries.slice(0, -1).map((start, index) => {
            const end = boundaries[index + 1]
            const attached = comments.filter(comment => comment.start <= start && comment.end >= end && comment.start !== comment.end)
            const comment = attached[0]
            return <span key={start} data-comment-id={comment?.id} className={comment ? `studio-outline-mark${comment.id === active?.id ? ' is-active' : ''}` : undefined}
              style={comment ? tint(comment.color) : undefined}>{outline.slice(start, end)}
              {comment && comment.end === end && comment.text.trim() && <i className="studio-outline-comment-marker" />}
            </span>
          })}
          {'\n'}
        </div>}
        {draftInput && !draftInput.reading && <div className="studio-comment-hitlayer">{hits.map((hit, index) => {
          const comment = comments.find(comment => comment.id === hit.id)!
          if (!comment) return null
          return <button key={`${hit.id}-${index}`} type="button" aria-label={`打开评论：${comment.quote}`} title={comment.text || comment.quote} style={{ left: hit.left, top: hit.top, width: hit.width }} onPointerDown={event => event.preventDefault()} onClick={() => {
            if (comment.submitted || readOnly || draftInput.stagedIds?.includes(comment.id)) draftInput.onSubmittedSelect?.(comment.id)
            else if (!readOnly) { selectComment(pending.findIndex(item => item.id === comment.id)); commentInput.current?.focus({ preventScroll: true }) }
          }} />
        })}</div>}
        {draftInput?.reading ? <div ref={readingRoot} className="studio-reading-outline" tabIndex={0} onPointerUp={event => offerComment(event)} onKeyUp={event => { if (event.key === 'Shift' || event.shiftKey) offerComment() }} onClick={event => {
          if (!window.getSelection()?.isCollapsed) return
          const id = (event.target as HTMLElement).closest<HTMLElement>('[data-reading-comment]')?.dataset.readingComment
          if (!id) return
          const index = pending.findIndex(comment => comment.id === id)
          if (index < 0) draftInput.onSubmittedSelect?.(id)
          else { selectComment(index); commentInput.current?.focus({ preventScroll: true }) }
        }}>{draftInput.reading}</div> : <textarea ref={input} className={draftInput?.className} maxLength={draftInput?.maxLength} aria-label={draftInput?.label || (draftInput ? '章节正文' : '编辑大纲')} placeholder={draftInput ? '从第一句话开始…' : undefined} spellCheck={false} readOnly={readOnly || textReadOnly} value={outline}
          onKeyDown={event => {
            if (event.nativeEvent.isComposing) return
            if (event.ctrlKey && event.code === 'Space' && draftInput?.linkOptions) { event.preventDefault(); suggestLinks(true); return }
            if (linkKeyDown(event)) return
            if (draftInput && !readOnly && !event.ctrlKey && !event.metaKey && !event.altKey && (event.key.length === 1 || ['Enter', 'Backspace', 'Delete', 'Process'].includes(event.key))) draftInput.onTyping()
          }}
          onCompositionStart={() => { if (!readOnly) draftInput?.onTyping() }} onPaste={() => { if (!readOnly) draftInput?.onTyping() }}
          onPointerUp={event => offerComment(event)} onKeyUp={event => { if (event.key === 'Shift' || event.shiftKey) offerComment() }}
          onBlur={event => { if (event.relatedTarget !== selectionAction.current) setSelection(null) }}
          onScroll={() => setSelection(null)}
          onChange={event => { setSelection(null); onChange(event.target.value, reanchorComments(comments, outline, event.target.value)); if (!(event.nativeEvent as InputEvent).isComposing) suggestLinks() }} />}
        {draftInput?.linkOptions && !draftInput.reading && <button className="setting-insert-link" type="button" disabled={readOnly} onClick={() => suggestLinks(true)}>引用条目 <small>@ / Ctrl+Space</small></button>}
        {link && draftInput?.visible !== false && draftInput?.ready !== false && createPortal(<div className="studio studio-link-overlay"><div ref={completion} className="setting-link-completion" role="listbox" aria-label="引用条目建议" onKeyDown={linkKeyDown}><input ref={linkSearch} aria-label="检索引用条目" placeholder="检索条目…" value={link.query} onFocus={() => setLinkIndex(-1)} onChange={event => { setLink({ ...link, query: event.target.value }); setLinkIndex(-1) }} />{linkMatches.map((title, index) => <button key={title} type="button" role="option" data-link-index={index} aria-selected={index === linkIndex} onPointerDown={event => event.preventDefault()} onClick={() => insertLink(title)}>{title}</button>)}{!linkMatches.length && <small>没有找到相关条目</small>}</div></div>, document.body)}
      </div>
      {selection && <button ref={selectionAction} type="button" className="studio-selection-action" style={{ left: selection.x, top: selection.y }}
        onPointerDown={event => event.preventDefault()} onClick={annotateSelection}>评论</button>}
      {confirm}
      {limitMessage && <div role="status" className="studio-comment-limit">{limitMessage}<button onClick={() => setLimitMessage('')} aria-label="关闭评论提示">×</button></div>}
    </div>
    {draftInput ? createPortal(<div className="studio studio-comment-overlay">{commentPanel}</div>, document.body) : commentPanel}
  </div>
}
