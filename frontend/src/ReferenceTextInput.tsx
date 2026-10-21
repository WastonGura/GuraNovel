import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, type FocusEventHandler, type KeyboardEventHandler, type PointerEventHandler, type Ref, type UIEventHandler } from 'react'
import type { OutlineComment } from './studioPreview'

export interface OutlineInputHandle {
  element?: HTMLElement
  value: string
  selectionStart: number
  selectionEnd: number
  setSelectionRange(start: number, end: number, direction?: 'forward' | 'backward' | 'none'): void
  replaceText?(text: string, start: number, end: number): void
  focus(options?: FocusOptions): void
  readonly clientWidth: number
  scrollLeft: number
  scrollTop: number
  getBoundingClientRect(): DOMRect
  addEventListener: HTMLElement['addEventListener']
  removeEventListener: HTMLElement['removeEventListener']
}

export interface ReferenceTextInputProps {
  ref?: Ref<OutlineInputHandle>
  value: string
  onChange(value: string): void
  onTyping?: () => void
  onKeyDown?: KeyboardEventHandler<HTMLElement>
  onPointerUp?: PointerEventHandler<HTMLElement>
  onKeyUp?: KeyboardEventHandler<HTMLElement>
  onScroll?: UIEventHandler<HTMLElement>
  onBlur?: FocusEventHandler<HTMLElement>
  label?: string
  className?: string
  maxLength?: number
  readOnly?: boolean
  animateText?: boolean
  comments?: OutlineComment[]
  activeCommentId?: string
  onOpenReference?(title: string): void
}

type SelectionRange = { start: number; end: number; backward?: boolean }
type EditSnapshot = { value: string; range: SelectionRange }
const references = (value: string) => [...value.matchAll(/\[\[([^[\]\r\n]+)\]\]/g)].map(match => ({ title: match[1], start: match.index!, end: match.index! + match[0].length }))

function canonical(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent || ''
  if (node instanceof HTMLElement) {
    if (node.dataset.referenceTitle !== undefined) return `[[${node.dataset.referenceTitle}]]`
    if (node.dataset.editorDecoration !== undefined) return ''
    if (node.tagName === 'BR') return '\n'
  }
  let result = '', previousBlock = false
  node.childNodes.forEach((child, index) => {
    const block = child instanceof HTMLElement && /^(DIV|P|LI)$/.test(child.tagName)
    if (index > 0 && (block || previousBlock) && !result.endsWith('\n')) result += '\n'
    result += canonical(child); previousBlock = block
  })
  return result
}

function read(root: HTMLElement): string {
  return root.childNodes.length === 1 && root.firstChild instanceof HTMLBRElement ? '' : canonical(root)
}

function tokenAt(root: HTMLElement, node: Node | null): HTMLElement | null {
  const element = node instanceof Element ? node : node?.parentElement
  const token = element?.closest<HTMLElement>('[data-reference-title]') || null
  return token && root.contains(token) ? token : null
}

function before(root: HTMLElement, node: Node): number {
  const range = document.createRange(); range.selectNodeContents(root); range.setEndBefore(node)
  return canonical(range.cloneContents()).length
}

function offset(root: HTMLElement, node: Node, position: number, edge: 'start' | 'end'): number {
  const token = tokenAt(root, node)
  if (token) return before(root, token) + (edge === 'end' ? canonical(token).length : 0)
  const range = document.createRange(); range.selectNodeContents(root); range.setEnd(node, position)
  return canonical(range.cloneContents()).length
}

function selected(root: HTMLElement, fallback: SelectionRange): SelectionRange {
  const selection = window.getSelection()
  if (!selection?.rangeCount) return fallback
  const range = selection.getRangeAt(0)
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return fallback
  const start = offset(root, range.startContainer, range.startOffset, 'start')
  const end = range.collapsed ? start : offset(root, range.endContainer, range.endOffset, 'end')
  return { start, end, backward: selection.anchorNode === range.endContainer && selection.anchorOffset === range.endOffset && !range.collapsed }
}

function snap(value: string, start: number, end: number): SelectionRange {
  const from = Math.max(0, Math.min(value.length, start)), to = Math.max(from, Math.min(value.length, end))
  let result = { start: from, end: to }
  for (const token of references(value)) {
    if (from === to && from > token.start && from < token.end) {
      const side = from - token.start <= token.end - from ? token.start : token.end
      result = { start: side, end: side }
    } else if (from !== to) {
      if (from > token.start && from < token.end) result.start = token.start
      if (to > token.start && to < token.end) result.end = token.end
    }
  }
  return result
}

function point(root: HTMLElement, wanted: number): { node: Node; offset: number } {
  let remaining = wanted
  function visit(parent: Node): { node: Node; offset: number } | null {
    for (let index = 0; index < parent.childNodes.length; index++) {
      const node = parent.childNodes[index]
      if (node instanceof HTMLElement && node.dataset.editorDecoration !== undefined) continue
      const length = canonical(node).length
      if (node.nodeType === Node.TEXT_NODE && remaining <= length) return { node, offset: remaining }
      if (node instanceof HTMLElement && (node.dataset.referenceTitle !== undefined || node.tagName === 'BR')) {
        if (remaining === 0) return { node: parent, offset: index }
        if (remaining <= length) return { node: parent, offset: index + 1 }
      } else if (remaining <= length) {
        const result = visit(node)
        if (result) return result
      }
      remaining -= length
    }
    return null
  }
  return visit(root) || { node: root, offset: root.childNodes.length }
}

function place(root: HTMLElement, range: SelectionRange): SelectionRange {
  const next = { ...snap(read(root), range.start, range.end), backward: range.backward }
  const start = point(root, next.start), end = point(root, next.end), selection = window.getSelection()
  if (!selection) return next
  const dom = document.createRange(); dom.setStart(start.node, start.offset); dom.setEnd(end.node, end.offset)
  selection.removeAllRanges(); selection.addRange(dom)
  if (next.backward && selection.extend) { selection.collapse(end.node, end.offset); selection.extend(start.node, start.offset) }
  return next
}

function decorate(element: HTMLElement, comment: OutlineComment | undefined, activeId?: string) {
  if (!comment) return
  element.dataset.commentId = comment.id
  element.classList.add('studio-outline-mark')
  if (comment.id === activeId) element.classList.add('is-active')
  element.style.setProperty('--comment-color', comment.color)
}

function renderSource(root: HTMLElement, value: string, comments: OutlineComment[] = [], activeId?: string) {
  const fragment = document.createDocumentFragment(), attached = comments.filter(comment => !comment.orphaned && comment.end > comment.start)
  function plain(start: number, end: number) {
    const boundaries = [...new Set([start, end, ...attached.flatMap(comment => [Math.max(start, Math.min(end, comment.start)), Math.max(start, Math.min(end, comment.end))])])].sort((a, b) => a - b)
    boundaries.slice(0, -1).forEach((from, index) => {
      const to = boundaries[index + 1], comment = attached.find(item => item.start <= from && item.end >= to)
      if (!comment) { fragment.append(document.createTextNode(value.slice(from, to))); return }
      const span = document.createElement('span'); decorate(span, comment, activeId); span.textContent = value.slice(from, to); fragment.append(span)
    })
  }
  let start = 0
  for (const reference of references(value)) {
    plain(start, reference.start)
    const token = document.createElement('span'); token.className = 'setting-reference-token'; token.dataset.referenceTitle = reference.title
    token.contentEditable = 'false'; token.setAttribute('contenteditable', 'false'); token.tabIndex = 0; token.setAttribute('role', 'link'); token.setAttribute('aria-label', `打开引用：${reference.title}`)
    token.append(document.createTextNode(reference.title))
    decorate(token, attached.find(comment => comment.start < reference.end && comment.end > reference.start), activeId)
    fragment.append(token); start = reference.end
  }
  plain(start, value.length)
  if (value.endsWith('\n')) {
    // A terminal text newline alone has no editable final line in Chromium.
    // Keep its caret line alive without adding a character to stored content.
    const end = document.createElement('br'); end.dataset.editorDecoration = 'end'; end.setAttribute('aria-hidden', 'true'); fragment.append(end)
  }
  root.replaceChildren(fragment)
}

function needsTokens(root: HTMLElement): boolean {
  if (references(read(root)).length !== root.querySelectorAll('[data-reference-title]').length) return true
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  while (walker.nextNode()) if (!tokenAt(root, walker.currentNode) && references(walker.currentNode.textContent || '').length) return true
  return [...root.children].some(child => child instanceof HTMLElement && child.dataset.editorDecoration === undefined && /^(DIV|P|BR)$/.test(child.tagName))
}

function limited(text: string, length: number): string {
  let end = Math.max(0, length)
  if (end < text.length && /[\uD800-\uDBFF]/.test(text[end - 1] || '') && /[\uDC00-\uDFFF]/.test(text[end] || '')) end--
  for (const token of references(text)) if (token.start < end && token.end > end) end = token.start
  return text.slice(0, end)
}

export default function ReferenceTextInput({ ref, value, onChange, onTyping, onKeyDown, onPointerUp, onKeyUp, onScroll, onBlur, label, className, readOnly = false, animateText, maxLength, comments = [], activeCommentId, onOpenReference }: ReferenceTextInputProps) {
  const root = useRef<HTMLDivElement>(null), composing = useRef(false), stable = useRef(value)
  const retained = useRef<HTMLDivElement>(null)
  const selection = useRef<SelectionRange>({ start: 0, end: 0 }), marks = useRef('')
  const undo = useRef<EditSnapshot[]>([]), redo = useRef<EditSnapshot[]>([]), pendingEdit = useRef<EditSnapshot | null>(null)
  const lastEdit = useRef<{ type: string; time: number; after: EditSnapshot } | null>(null)
  const bind = useCallback((element: HTMLDivElement | null) => {
    if (!element && root.current) selection.current = selected(root.current, selection.current)
    if (element) retained.current = element
    root.current = element
  }, [])
  const signature = JSON.stringify([comments, activeCommentId])
  function remember() { if (root.current) selection.current = selected(root.current, selection.current); return selection.current }
  function resetHistory() { undo.current = []; redo.current = []; pendingEdit.current = null; lastEdit.current = null }
  function snapshot(): EditSnapshot { return { value: stable.current, range: { ...remember() } } }
  function recordEdit(before: EditSnapshot, after: EditSnapshot, type: string) {
    pendingEdit.current = null
    if (before.value === after.value) return
    const previous = lastEdit.current, now = Date.now()
    const groupable = ['insertText', 'deleteContentBackward', 'deleteContentForward'].includes(type)
    const contiguous = groupable && previous?.type === type && now - previous.time < 1000
      && previous.after.value === before.value && before.range.start === before.range.end
      && previous.after.range.start === before.range.start && previous.after.range.end === before.range.end
    if (!contiguous) { undo.current.push(before); if (undo.current.length > 100) undo.current.shift() }
    redo.current = []
    lastEdit.current = groupable ? { type, time: now, after } : null
  }
  function restoreHistory(forward: boolean) {
    const element = root.current
    if (!element || readOnly || composing.current) return
    const source = forward ? redo.current : undo.current, target = forward ? undo.current : redo.current
    const next = source.pop()
    pendingEdit.current = null; lastEdit.current = null
    if (!next) { normalize(stable.current, selection.current, true); return }
    target.push(snapshot())
    element.focus({ preventScroll: true }); normalize(next.value, next.range, true)
    selection.current = place(element, next.range); onTyping?.(); emit(next.value)
  }
  function normalize(next: string, range: SelectionRange, force = false) {
    const element = root.current
    if (!element) return
    if (force || read(element) !== next || needsTokens(element) || marks.current !== signature) {
      const focused = element.contains(document.activeElement) || element === document.activeElement
      renderSource(element, next, comments, activeCommentId); marks.current = signature
      if (focused || window.getSelection()?.anchorNode && element.contains(window.getSelection()!.anchorNode)) selection.current = place(element, range)
      else selection.current = snap(next, range.start, range.end)
    }
  }
  useLayoutEffect(() => {
    if (composing.current) return
    if (stable.current !== value) resetHistory()
    const current = root.current
    if (current) {
      const focused = current.contains(document.activeElement)
      const range = selected(current, selection.current)
      if (read(current) !== value || needsTokens(current) || marks.current !== signature) {
        renderSource(current, value, comments, activeCommentId); marks.current = signature
        if (focused) selection.current = place(current, range)
        else selection.current = snap(value, range.start, range.end)
      }
    }
    stable.current = value
  }, [value, comments, activeCommentId, signature])
  useEffect(() => {
    const changed = () => { if (root.current) selection.current = selected(root.current, selection.current) }
    document.addEventListener('selectionchange', changed)
    return () => document.removeEventListener('selectionchange', changed)
  }, [])
  useLayoutEffect(() => {
    const element = root.current
    return () => { if (element) selection.current = selected(element, selection.current) }
  }, [])
  useImperativeHandle(ref, () => ({
    get element() { return retained.current! },
    get value() { return retained.current ? read(retained.current) : stable.current }, set value(next: string) { if (root.current) { resetHistory(); renderSource(root.current, next, comments, activeCommentId); stable.current = next } },
    get selectionStart() { return remember().start }, set selectionStart(next: number) { if (root.current) selection.current = place(root.current, { start: next, end: Math.max(next, remember().end) }) },
    get selectionEnd() { return remember().end }, set selectionEnd(next: number) { if (root.current) selection.current = place(root.current, { start: Math.min(next, remember().start), end: next }) },
    setSelectionRange(start: number, end: number, direction?: 'forward' | 'backward' | 'none') { if (root.current) selection.current = place(root.current, { start, end, backward: direction === 'backward' }) },
    replaceText(text: string, start: number, end: number) { replace(text, { start, end }, 'insertReference') },
    focus(options?: FocusOptions) { const element = root.current; if (element) { const range = remember(); element.focus(options); selection.current = place(element, range) } },
    get clientWidth() { return root.current?.clientWidth || 0 },
    get scrollLeft() { return root.current?.scrollLeft || 0 }, set scrollLeft(next: number) { if (root.current) root.current.scrollLeft = next },
    get scrollTop() { return root.current?.scrollTop || 0 }, set scrollTop(next: number) { if (root.current) root.current.scrollTop = next },
    getBoundingClientRect() { return root.current?.getBoundingClientRect() || new DOMRect() },
    addEventListener(type: string, listener: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions) { root.current?.addEventListener(type, listener, options) },
    removeEventListener(type: string, listener: EventListenerOrEventListenerObject, options?: boolean | EventListenerOptions) { root.current?.removeEventListener(type, listener, options) },
  }))
  function emit(next: string) { const changed = stable.current !== next; stable.current = next; if (changed) onChange(next) }
  function replace(text: string, range = remember(), type = 'replace') {
    const element = root.current
    if (!element || readOnly) return
    const current = read(element), before = { value: current, range: { ...remember() } }, actual = snap(current, range.start, range.end)
    const insertion = maxLength === undefined ? text : limited(text, maxLength - current.length + actual.end - actual.start)
    const next = current.slice(0, actual.start) + insertion + current.slice(actual.end), caret = actual.start + insertion.length
    const after = { start: caret, end: caret }
    recordEdit(before, { value: next, range: after }, type)
    normalize(next, after, true); selection.current = place(element, after); emit(next)
  }
  function atomicDelete(backward: boolean, focusedToken?: HTMLElement | null): boolean {
    const element = root.current
    if (!element) return false
    const current = read(element), range = remember(), tokens = references(current)
    if (focusedToken) { const start = before(element, focusedToken); replace('', { start, end: start + canonical(focusedToken).length }); return true }
    if (range.start !== range.end && tokens.some(token => token.start < range.end && token.end > range.start)) { replace('', range); return true }
    const token = tokens.find(token => backward ? token.end === range.start : token.start === range.start)
    if (range.start === range.end && token) { replace('', token); return true }
    return false
  }
  function acceptInput(type = '') {
    const element = root.current
    if (!element || composing.current) return
    if (type === 'historyUndo' || type === 'historyRedo') { restoreHistory(type === 'historyRedo'); return }
    const next = read(element), range = remember()
    const before = pendingEdit.current || { value: stable.current, range: selection.current }
    if (readOnly || maxLength !== undefined && next.length > maxLength && next.length > stable.current.length) {
      pendingEdit.current = null; normalize(stable.current, before.range, true); return
    }
    recordEdit(before, { value: next, range }, type); normalize(next, range); emit(next)
  }
  // Native beforeinput also covers browser-menu undo and editing without a keydown.
  // Keep all edits in one canonical history: DOM normalization invalidates native undo.
  useLayoutEffect(() => {
    const element = root.current
    if (!element) return
    const beforeInput = (native: InputEvent) => {
      if (readOnly || composing.current || native.isComposing || native.defaultPrevented) return
      if (native.inputType === 'historyUndo' || native.inputType === 'historyRedo') {
        if (native.cancelable) { native.preventDefault(); restoreHistory(native.inputType === 'historyRedo') }
        return
      }
      pendingEdit.current = snapshot()
      if (native.inputType === 'insertParagraph' || native.inputType === 'insertLineBreak') { native.preventDefault(); onTyping?.(); replace('\n') }
      else if (native.inputType === 'deleteContentBackward' || native.inputType === 'deleteContentForward') {
        if (atomicDelete(native.inputType === 'deleteContentBackward')) native.preventDefault()
      }
    }
    element.addEventListener('beforeinput', beforeInput)
    return () => element.removeEventListener('beforeinput', beforeInput)
  })
  return <div ref={bind} className={`setting-reference-editor ${className || ''}`} role="textbox" aria-label={label} aria-multiline="true" aria-readonly={readOnly || undefined}
    contentEditable={!readOnly} suppressContentEditableWarning spellCheck={false} tabIndex={0} data-text-editor="" data-sweep-text={animateText ? '' : undefined}
    data-placeholder="从第一句话开始，输入 @ 引用条目…"
    onInput={event => acceptInput((event.nativeEvent as InputEvent).inputType)} onCompositionStart={() => { if (!readOnly) { pendingEdit.current = snapshot(); composing.current = true; onTyping?.() } }}
    onCompositionEnd={() => { composing.current = false; acceptInput('insertCompositionText') }}
    onKeyDown={event => {
      onKeyDown?.(event)
      if (event.defaultPrevented || composing.current || event.nativeEvent.isComposing) return
      const token = tokenAt(event.currentTarget, event.target as Node)
      if (token && event.key === 'Enter') { event.preventDefault(); onOpenReference?.(token.dataset.referenceTitle!); return }
      if (readOnly) return
      const key = event.key.toLowerCase()
      if ((event.ctrlKey || event.metaKey) && !event.altKey && (key === 'z' || key === 'y')) { event.preventDefault(); restoreHistory(key === 'y' || event.shiftKey); return }
      if (!event.ctrlKey && !event.metaKey && !event.altKey && (event.key.length === 1 || ['Enter', 'Backspace', 'Delete', 'Process'].includes(event.key))) onTyping?.()
      if (event.key === 'Enter') { event.preventDefault(); replace('\n') }
      else if ((event.key === 'Backspace' || event.key === 'Delete') && atomicDelete(event.key === 'Backspace', token)) event.preventDefault()
    }}
    onClick={event => {
      const token = tokenAt(event.currentTarget, event.target as Node)
      if (!token) return
      if (event.ctrlKey || event.metaKey) { event.preventDefault(); onOpenReference?.(token.dataset.referenceTitle!); return }
      if (window.getSelection()?.isCollapsed === false) return
      const start = before(event.currentTarget, token), bounds = token.getBoundingClientRect(), caret = start + (event.clientX > bounds.left + bounds.width / 2 ? canonical(token).length : 0)
      event.currentTarget.focus({ preventScroll: true }); selection.current = place(event.currentTarget, { start: caret, end: caret })
    }}
    onPointerUp={event => { remember(); onPointerUp?.(event) }} onKeyUp={event => { remember(); onKeyUp?.(event) }}
    onScroll={onScroll} onBlur={event => { remember(); onBlur?.(event) }}
    onPaste={event => { event.preventDefault(); if (!readOnly) { onTyping?.(); replace(event.clipboardData.getData('text/plain').replace(/\r\n?/g, '\n'), remember(), 'insertFromPaste') } }}
    onCopy={event => { const range = remember(); if (range.start !== range.end) { event.preventDefault(); event.clipboardData.setData('text/plain', read(event.currentTarget).slice(range.start, range.end)) } }}
    onCut={event => { const range = remember(); if (range.start !== range.end) { event.preventDefault(); event.clipboardData.setData('text/plain', read(event.currentTarget).slice(range.start, range.end)); if (!readOnly) { onTyping?.(); replace('', range, 'deleteByCut') } } }}
    onDrop={event => { event.preventDefault(); if (!readOnly) { onTyping?.(); replace(event.dataTransfer.getData('text/plain').replace(/\r\n?/g, '\n'), remember(), 'insertFromDrop') } }} />
}
