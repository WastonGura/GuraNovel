import { createRef, useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ReferenceTextInput, { type OutlineInputHandle } from './ReferenceTextInput'

afterEach(cleanup)

describe('editable setting references', () => {
  it('displays plain atomic titles while retaining canonical source', () => {
    const ref = createRef<OutlineInputHandle>()
    render(<ReferenceTextInput ref={ref} value="前 [[苍港]] 后" onChange={() => {}} label="设定正文" animateText />)
    const input = screen.getByRole('textbox', { name: '设定正文' })
    const token = input.querySelector('[data-reference-title="苍港"]')
    expect(token).toHaveAttribute('contenteditable', 'false')
    expect(input).toHaveTextContent('前 苍港 后')
    expect(input.querySelector('sup')).toBeNull()
    expect(input.textContent).not.toContain('[[')
    expect(ref.current?.value).toBe('前 [[苍港]] 后')
    expect(input).toHaveAttribute('data-text-editor')
    expect(input).toHaveAttribute('data-sweep-text')
  })

  it('maps UTF-16 selections across emoji and atomic references to the stored source', () => {
    const ref = createRef<OutlineInputHandle>()
    render(<ReferenceTextInput ref={ref} value="A🌊[[港]]Z" onChange={() => {}} label="设定正文" />)
    ref.current!.setSelectionRange(3, 8)
    expect(ref.current!.selectionStart).toBe(3)
    expect(ref.current!.selectionEnd).toBe(8)
    expect(ref.current!.value.slice(ref.current!.selectionStart, ref.current!.selectionEnd)).toBe('[[港]]')
  })

  it('copies canonical plain text from the visible title tokens', () => {
    const ref = createRef<OutlineInputHandle>(), setData = vi.fn()
    render(<ReferenceTextInput ref={ref} value="A🌊[[港]]Z" onChange={() => {}} label="设定正文" />)
    ref.current!.setSelectionRange(1, 8)
    fireEvent.copy(screen.getByRole('textbox'), { clipboardData: { setData } })
    expect(setData).toHaveBeenCalledWith('text/plain', '🌊[[港]]')
  })

  it.each([['Backspace', 8], ['Delete', 3]])('deletes an entire token with %s while preserving adjacent emoji', (key, caret) => {
    const ref = createRef<OutlineInputHandle>(), changed = vi.fn()
    render(<ReferenceTextInput ref={ref} value="A🌊[[港]]Z" onChange={changed} label="设定正文" />)
    ref.current!.setSelectionRange(caret as number, caret as number)
    fireEvent.keyDown(screen.getByRole('textbox'), { key })
    expect(changed).toHaveBeenLastCalledWith('A🌊Z')
    expect(ref.current!.value).toBe('A🌊Z')
    expect(ref.current!.selectionStart).toBe(3)
  })

  it('pastes only plain text and tokenizes references without inserting clipboard HTML', () => {
    const ref = createRef<OutlineInputHandle>(), changed = vi.fn()
    render(<ReferenceTextInput ref={ref} value="起点 " onChange={changed} label="设定正文" />)
    ref.current!.setSelectionRange(3, 3)
    fireEvent.paste(screen.getByRole('textbox'), { clipboardData: { getData: (type: string) => type === 'text/plain' ? '[[苍港]]\r\n<b>原文</b>' : '<img src="invalid">' } })
    expect(changed).toHaveBeenLastCalledWith('起点 [[苍港]]\n<b>原文</b>')
    expect(ref.current!.value).toBe('起点 [[苍港]]\n<b>原文</b>')
    expect(screen.getByRole('textbox').querySelector('img,b')).toBeNull()
    expect(screen.getByRole('textbox').querySelector('[data-reference-title]')).not.toBeNull()
  })

  it('keeps @ completion as native text and emits ordinary input from the same DOM node', () => {
    const changed = vi.fn()
    render(<ControlledReferenceInput initial="旧句" onChanged={changed} />)
    const input = screen.getByRole('textbox')
    input.textContent = '旧句 @苍'
    fireEvent.input(input)
    expect(changed).toHaveBeenCalledWith('旧句 @苍')
    expect(screen.getByRole('textbox')).toBe(input)
    expect(input).toHaveTextContent('@苍')
    expect(input.querySelector('[data-reference-title]')).toBeNull()
  })

  it('does not tokenize or publish an unfinished IME composition and commits it once', () => {
    const ref = createRef<OutlineInputHandle>(), changed = vi.fn(), typing = vi.fn()
    render(<ReferenceTextInput ref={ref} value="" onChange={changed} onTyping={typing} label="设定正文" />)
    const input = screen.getByRole('textbox')
    fireEvent.compositionStart(input)
    input.textContent = '中文[[苍港]]'
    fireEvent.input(input)
    expect(changed).not.toHaveBeenCalled()
    expect(input.querySelector('[data-reference-title]')).toBeNull()
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(ref.current!.value).toBe('中文[[苍港]]')
    fireEvent.compositionEnd(input)
    fireEvent.input(input)
    expect(changed).toHaveBeenCalledExactlyOnceWith('中文[[苍港]]')
    expect(typing).toHaveBeenCalled()
    expect(input.querySelector('[data-reference-title="苍港"]')).not.toBeNull()
  })

  it('inserts a real newline rather than browser HTML blocks', () => {
    const ref = createRef<OutlineInputHandle>(), changed = vi.fn()
    render(<ReferenceTextInput ref={ref} value="两句" onChange={changed} label="设定正文" />)
    ref.current!.setSelectionRange(1, 1)
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    expect(changed).toHaveBeenLastCalledWith('两\n句')
    expect(ref.current!.value).toBe('两\n句')
    expect(screen.getByRole('textbox').querySelector('div,p,br')).toBeNull()
  })

  it('keeps a terminal blank line editable without storing the caret decoration', () => {
    const ref = createRef<OutlineInputHandle>(), changed = vi.fn()
    render(<ReferenceTextInput ref={ref} value="看[[港]]。" onChange={changed} label="设定正文" />)
    const input = screen.getByRole('textbox')
    input.focus(); ref.current!.setSelectionRange(7, 7)
    fireEvent.keyDown(input, { key: 'Enter' })
    const native = window.getSelection()!.getRangeAt(0)
    const typed = document.createTextNode('下一段。'); native.insertNode(typed); native.setStartAfter(typed); native.collapse(true)
    fireEvent.input(input)
    expect(ref.current!.value).toBe('看[[港]]。\n下一段。')
    expect(changed).toHaveBeenLastCalledWith('看[[港]]。\n下一段。')
    expect(ref.current!.selectionStart).toBe('看[[港]]。\n下一段。'.length)
    expect(input.textContent).not.toContain('[[')
  })

  it('uses modifier click and focused Enter for navigation while a plain click keeps editing', () => {
    const open = vi.fn()
    render(<ReferenceTextInput value="[[苍港]]" onChange={() => {}} onOpenReference={open} label="设定正文" />)
    const token = screen.getByRole('link', { name: '打开引用：苍港' })
    fireEvent.click(token)
    expect(open).not.toHaveBeenCalled()
    fireEvent.click(token, { ctrlKey: true })
    fireEvent.click(token, { metaKey: true })
    token.focus()
    fireEvent.keyDown(token, { key: 'Enter' })
    expect(open).toHaveBeenCalledTimes(3)
    expect(open).toHaveBeenLastCalledWith('苍港')
  })

  it('preserves canonical selection when external text adds a reference and when focus leaves or the editor unmounts', () => {
    const ref = createRef<OutlineInputHandle>()
    const view = render(<ReferenceTextInput ref={ref} value="🌊Hello" onChange={() => {}} label="设定正文" />)
    const input = screen.getByRole('textbox')
    input.focus(); ref.current!.setSelectionRange(2, 7)
    view.rerender(<ReferenceTextInput ref={ref} value="🌊Hello[[港]]" onChange={() => {}} label="设定正文" />)
    expect(ref.current!.selectionStart).toBe(2)
    expect(ref.current!.selectionEnd).toBe(7)
    const handle = ref.current!
    window.getSelection()?.removeAllRanges()
    expect(handle.selectionStart).toBe(2)
    expect(handle.selectionEnd).toBe(7)
    view.unmount()
    expect(handle.selectionStart).toBe(2)
    expect(handle.selectionEnd).toBe(7)
  })

  it('captures a fresh native selection before unmount even without selectionchange or getter reads', () => {
    const ref = createRef<OutlineInputHandle>()
    const view = render(<ReferenceTextInput ref={ref} value="原生书签范围" onChange={() => {}} label="设定正文" />)
    const input = screen.getByRole('textbox'), handle = ref.current!, range = document.createRange()
    range.setStart(input.firstChild!, 3); range.setEnd(input.firstChild!, 6)
    window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range)
    view.unmount()
    expect(handle.selectionStart).toBe(3)
    expect(handle.selectionEnd).toBe(6)
    expect(handle.value).toBe('原生书签范围')
    expect(handle.element).toBe(input)
  })

  it('expands partial canonical token selections for cut without leaving bracket fragments', () => {
    const ref = createRef<OutlineInputHandle>(), changed = vi.fn(), setData = vi.fn()
    render(<ReferenceTextInput ref={ref} value="A[[港]]B" onChange={changed} label="设定正文" />)
    ref.current!.setSelectionRange(2, 4)
    fireEvent.cut(screen.getByRole('textbox'), { clipboardData: { setData } })
    expect(setData).toHaveBeenCalledWith('text/plain', '[[港]]')
    expect(changed).toHaveBeenLastCalledWith('AB')
    expect(ref.current!.value).toBe('AB')
    expect(ref.current!.selectionStart).toBe(1)
  })

  it('renders comment marks against canonical offsets, including atomic references', () => {
    const comments = [{ id: 'plain', start: 0, end: 2, quote: '前文', text: 'Keep', color: '#aabbcc' },
      { id: 'reference', start: 3, end: 8, quote: '[[港]]', text: 'Check', color: '#ddeeff' }]
    render(<ReferenceTextInput value="前文 [[港]] 后" onChange={() => {}} label="设定正文" comments={comments} activeCommentId="reference" />)
    const input = screen.getByRole('textbox')
    expect(input.querySelector('[data-comment-id="plain"]')).toHaveTextContent('前文')
    const reference = input.querySelector('[data-reference-title="港"]')
    expect(reference).toHaveAttribute('data-comment-id', 'reference')
    expect(reference).toHaveClass('studio-outline-mark', 'is-active')
    expect(reference).toHaveStyle('--comment-color: #ddeeff')
  })

  it('enforces UTF-16 limits without splitting emoji or allowing oversized native input', () => {
    const ref = createRef<OutlineInputHandle>(), changed = vi.fn()
    render(<ReferenceTextInput ref={ref} value="A" onChange={changed} label="设定正文" maxLength={2} />)
    ref.current!.setSelectionRange(1, 1)
    fireEvent.paste(screen.getByRole('textbox'), { clipboardData: { getData: () => '🌊B' } })
    expect(ref.current!.value).toBe('A')
    expect(changed).not.toHaveBeenCalled()
    screen.getByRole('textbox').textContent = 'ABC'
    fireEvent.input(screen.getByRole('textbox'))
    expect(ref.current!.value).toBe('A')
  })

  it('keeps read-only documents immutable but allows reference navigation', () => {
    const ref = createRef<OutlineInputHandle>(), changed = vi.fn(), open = vi.fn()
    render(<ReferenceTextInput ref={ref} value="[[港]]" onChange={changed} onOpenReference={open} readOnly label="设定正文" />)
    const input = screen.getByRole('textbox')
    expect(input).toHaveAttribute('contenteditable', 'false')
    ref.current!.setSelectionRange(0, 5)
    fireEvent.keyDown(input, { key: 'Backspace' })
    fireEvent.paste(input, { clipboardData: { getData: () => 'replace' } })
    expect(ref.current!.value).toBe('[[港]]')
    expect(changed).not.toHaveBeenCalled()
    input.textContent = 'cannot edit'; fireEvent.input(input)
    expect(ref.current!.value).toBe('[[港]]')
    expect(changed).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('link'), { ctrlKey: true })
    expect(open).toHaveBeenCalledWith('港')
  })

  it('undoes and redoes line breaks, clears redo after editing, and restores the canonical caret', () => {
    const inputRef = createRef<OutlineInputHandle>()
    render(<ControlledReferenceInput initial="A" inputRef={inputRef} />)
    const input = screen.getByRole('textbox')
    input.focus(); inputRef.current!.setSelectionRange(1, 1)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(inputRef.current!.value).toBe('A\n')
    fireEvent.keyDown(input, { key: 'z', ctrlKey: true })
    expect(inputRef.current!.value).toBe('A')
    expect(inputRef.current!.selectionStart).toBe(1)
    fireEvent.keyDown(input, { key: 'z', ctrlKey: true, shiftKey: true })
    expect(inputRef.current!.value).toBe('A\n')
    expect(inputRef.current!.selectionStart).toBe(2)
    fireEvent.keyDown(input, { key: 'z', metaKey: true })
    fireEvent.paste(input, { clipboardData: { getData: () => 'B' } })
    fireEvent.keyDown(input, { key: 'y', ctrlKey: true })
    expect(inputRef.current!.value).toBe('AB')
  })

  it('restores atomic references through keyboard and browser-menu history actions', () => {
    const inputRef = createRef<OutlineInputHandle>()
    render(<ControlledReferenceInput initial="A[[港]]B" inputRef={inputRef} />)
    const input = screen.getByRole('textbox')
    input.focus(); inputRef.current!.setSelectionRange(6, 6)
    fireEvent.keyDown(input, { key: 'Backspace' })
    expect(inputRef.current!.value).toBe('AB')
    fireEvent(input, new InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType: 'historyUndo' }))
    expect(inputRef.current!.value).toBe('A[[港]]B')
    expect(screen.getByRole('link')).toHaveTextContent('港')
    expect(inputRef.current!.selectionStart).toBe(6)
    fireEvent(input, new InputEvent('beforeinput', { bubbles: true, cancelable: true, inputType: 'historyRedo' }))
    expect(inputRef.current!.value).toBe('AB')
  })

  it('records reference completion in the same history and isolates authoritative external replacements', () => {
    const inputRef = createRef<OutlineInputHandle>()
    const view = render(<ReferenceTextInput ref={inputRef} value="看@港" onChange={() => {}} label="设定正文" />)
    const input = screen.getByRole('textbox')
    input.focus(); inputRef.current!.setSelectionRange(3, 3)
    inputRef.current!.replaceText!('[[港]]', 1, 3)
    fireEvent.keyDown(input, { key: 'z', ctrlKey: true })
    expect(inputRef.current!.value).toBe('看@港')
    fireEvent.keyDown(input, { key: 'y', ctrlKey: true })
    expect(inputRef.current!.value).toBe('看[[港]]')
    view.rerender(<ReferenceTextInput ref={inputRef} value="服务器确认的新正文" onChange={() => {}} label="设定正文" />)
    fireEvent.keyDown(input, { key: 'z', ctrlKey: true })
    expect(inputRef.current!.value).toBe('服务器确认的新正文')
  })

  it('undoes an IME composition as one edit without publishing its intermediate text', () => {
    const inputRef = createRef<OutlineInputHandle>(), changed = vi.fn()
    render(<ControlledReferenceInput initial="A" inputRef={inputRef} onChanged={changed} />)
    const input = screen.getByRole('textbox')
    input.focus(); inputRef.current!.setSelectionRange(1, 1)
    fireEvent.compositionStart(input)
    input.textContent = 'A中'; fireEvent.input(input)
    input.textContent = 'A中文'; fireEvent.input(input)
    expect(changed).not.toHaveBeenCalled()
    fireEvent.compositionEnd(input)
    expect(changed).toHaveBeenLastCalledWith('A中文')
    fireEvent.keyDown(input, { key: 'z', ctrlKey: true })
    expect(inputRef.current!.value).toBe('A')
    fireEvent.keyDown(input, { key: 'y', ctrlKey: true })
    expect(inputRef.current!.value).toBe('A中文')
  })
})

function ControlledReferenceInput({ initial = '', onOpen = vi.fn(), onChanged = vi.fn(), readOnly = false, maxLength, inputRef }: { initial?: string; onOpen?: (title: string) => void; onChanged?: (value: string) => void; readOnly?: boolean; maxLength?: number; inputRef?: React.Ref<OutlineInputHandle> }) {
  const [value, setValue] = useState(initial)
  return <ReferenceTextInput ref={inputRef} value={value} onChange={next => { setValue(next); onChanged(next) }} onOpenReference={onOpen} label="设定正文" readOnly={readOnly} maxLength={maxLength} />
}
