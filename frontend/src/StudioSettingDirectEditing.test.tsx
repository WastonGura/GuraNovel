import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { createRef } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import StudioSetting from './StudioSetting'

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); vi.useFakeTimers() })
afterEach(() => { cleanup(); vi.useRealTimers() })

it('edits a saved entry directly and autosaves without an edit-mode switch', async () => {
  const save = vi.fn().mockResolvedValue({ versionId: 'v2' })
  render(<MemoryRouter><StudioSetting preview={false} backendNotes={[{ id: 'a', documentId: 'a', versionId: 'v1', title: '旧城', body: '旧城正文', category: 'world' }]} onSaveNoteContent={save} /></MemoryRouter>)
  expect(screen.queryByRole('button', { name: /^编辑$/ })).not.toBeInTheDocument()
  fireEvent.input(screen.getByRole('textbox', { name: '设定正文' }), { target: { textContent: '旧城的新正文' } })
  await act(async () => { vi.advanceTimersByTime(450) })
  expect(save).toHaveBeenCalledWith('a', '旧城的新正文', 'v1', [])
})

it('keeps untouched new entries local, focuses title and persists only actual writing', async () => {
  const create = vi.fn().mockImplementation(async (category, title, id, body) => ({ id, documentId: id, category, title, body, versionId: 'v1', comments: [] }))
  const save = vi.fn().mockResolvedValue({ versionId: 'v2' })
  const ref = createRef<{ flush: () => Promise<boolean> }>()
  render(<MemoryRouter><StudioSetting ref={ref} preview={false} backendNotes={[]} onCreateNote={create} onSaveNoteContent={save} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: '新建内容' }))
  const title = screen.getByRole('textbox', { name: '设定标题' })
  expect(title).toHaveFocus()
  await act(async () => { vi.advanceTimersByTime(1000); await ref.current!.flush() })
  expect(create).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: '手动新建条目' })).not.toBeInTheDocument()
  fireEvent.change(title, { target: { value: '雨城' } })
  fireEvent.input(screen.getByRole('textbox', { name: '设定正文' }), { target: { textContent: '雨一直下。' } })
  await act(async () => { vi.advanceTimersByTime(450); await ref.current!.flush() })
  expect(create).toHaveBeenCalledTimes(1)
  expect(create).toHaveBeenCalledWith('setting', '雨城', expect.any(String), '雨一直下。', [])
  expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveTextContent('雨一直下。')
})

it('recovers the exact first creation request and newer writing after a lost response', async () => {
  const create = vi.fn().mockResolvedValue(null)
  const ref = createRef<{ flush: () => Promise<boolean> }>()
  const view = render(<MemoryRouter><StudioSetting ref={ref} preview={false} collection={{ id: 'collection' } as never} backendNotes={[]} onCreateNote={create} onSaveNoteContent={vi.fn()} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: '新建内容' }))
  fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: '雨城' } })
  fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '第一版' } })
  await act(async () => { vi.advanceTimersByTime(450) })
  const first = create.mock.calls[0]
  fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '响应丢失后继续写' } })
  view.unmount()
  render(<MemoryRouter><StudioSetting ref={ref} preview={false} collection={{ id: 'collection' } as never} backendNotes={[]} onCreateNote={create} onSaveNoteContent={vi.fn()} /></MemoryRouter>)
  expect(screen.getByLabelText('设定正文')).toHaveTextContent('响应丢失后继续写')
  await act(async () => { await ref.current!.flush() })
  expect(create.mock.calls.at(-1)).toEqual(first)
  expect(screen.getByRole('alert')).toHaveTextContent('保存')
})

it('restores each document cursor and inner scroll when returning from another entry', () => {
  render(<MemoryRouter><StudioSetting preview={false} defaultPinned backendNotes={[
    { id: 'a', title: '雨城', body: '第一篇正文的内容', category: 'setting', versionId: 'v1' },
    { id: 'b', title: '钟楼', body: '第二篇正文的内容', category: 'setting', versionId: 'v2' },
  ]} onSaveNoteContent={vi.fn()} /></MemoryRouter>)
  const first = screen.getByLabelText('设定正文')
  first.focus()
  const text = document.createTreeWalker(first, NodeFilter.SHOW_TEXT).nextNode()!
  const range = document.createRange()
  range.setStart(text, 3); range.setEnd(text, 6)
  const selection = window.getSelection()!
  selection.removeAllRanges(); selection.addRange(range)
  const scroll = document.querySelector('.setting-document-scroll') as HTMLElement
  scroll.scrollTop = 210
  const list = within(screen.getByLabelText('设定条目'))
  fireEvent.click(list.getByRole('button', { name: '钟楼' }))
  scroll.scrollTop = 70
  fireEvent.click(list.getByRole('button', { name: '雨城' }))
  const returned = screen.getByLabelText('设定正文')
  expect(returned).toContainElement(selection.anchorNode?.parentElement || null)
  expect(selection.getRangeAt(0).startOffset).toBe(3)
  expect(selection.getRangeAt(0).endOffset).toBe(6)
  expect(selection.toString()).toBe('正文的')
  expect(scroll.scrollTop).toBe(210)
})
