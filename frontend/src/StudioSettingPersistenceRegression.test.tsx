import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createRef, useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import StudioSetting, { type StudioSettingHandle } from './StudioSetting'
import type { SettingNote } from './settingNotes'

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear(); vi.useFakeTimers()
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

it('does not warn about unsaved changes after the first creation has saved the final prose', async () => {
  const ref = createRef<{ flush: () => Promise<boolean> }>()
  const create = vi.fn().mockImplementation(async (category, title, id, body, comments) => ({ id, documentId: 'saved-document', category, title, body, comments, versionId: 'v1' }))
  render(<MemoryRouter><StudioSetting ref={ref} preview={false} backendNotes={[]} onCreateNote={create} onSaveNoteContent={vi.fn()} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: '新建内容' }))
  fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: '雨城' } })
  fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '雨一直下。' } })
  await act(async () => { expect(await ref.current!.flush()).toBe(true) })
  expect(create).toHaveBeenCalledTimes(1)
  const leaving = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(leaving)
  expect(leaving.defaultPrevented).toBe(false)
})

it('keeps one local creation and the latest prose while parent props acknowledge a different server document ID', async () => {
  let finishCreate!: () => void
  let finishSave!: () => void
  const save = vi.fn(() => new Promise<{ versionId: string }>(resolve => { finishSave = () => resolve({ versionId: 'v2' }) }))
  function Parent() {
    const [notes, setNotes] = useState<SettingNote[]>([])
    return <StudioSetting preview={false} backendNotes={notes} defaultPinned onSaveNoteContent={save}
      onCreateNote={async (category, title, id, body) => {
        await new Promise<void>(resolve => { finishCreate = resolve })
        const created = { category, title, id, documentId: 'server-id', versionId: 'v1', body: body || '' }
        setNotes(previous => [...previous, created])
        return created
      }} />
  }
  render(<MemoryRouter><Parent /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: '新建内容' }))
  fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: '雨城' } })
  fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '请求中的第一句' } })
  await act(async () => { vi.advanceTimersByTime(450) })
  fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '请求未返回时继续写第二句' } })
  await act(async () => { finishCreate() })
  expect(screen.getAllByRole('button', { name: /^雨城$/ })).toHaveLength(1)
  expect(screen.getByLabelText('设定正文')).toHaveTextContent('请求未返回时继续写第二句')
  expect(save).toHaveBeenCalledWith('server-id', '请求未返回时继续写第二句', 'v1', [])
  await act(async () => { finishSave() })
})

it('explicitly replaces a failed first-creation journal when the author resolves its conflict', async () => {
  let finishCreate!: () => void
  let setParentNotes!: (notes: SettingNote[]) => void
  const ref = createRef<StudioSettingHandle>()
  const save = vi.fn().mockResolvedValueOnce('conflict').mockResolvedValue({ versionId: 'v3' })
  let stableId = ''
  function Parent() {
    const [notes, setNotes] = useState<SettingNote[]>([])
    setParentNotes = setNotes
    return <StudioSetting ref={ref} preview={false} backendNotes={notes} onSaveNoteContent={save}
      onCreateNote={async (category, title, id, body) => {
        stableId = id
        await new Promise<void>(resolve => { finishCreate = resolve })
        const created = { id, documentId: 'server-id', category, title, body: body || '', versionId: 'v1', comments: [] }
        setNotes([created])
        return created
      }} />
  }
  render(<MemoryRouter><Parent /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: '新建内容' }))
  fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: '雨城' } })
  fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '创建请求中的原文' } })
  await act(async () => { vi.advanceTimersByTime(450) })
  fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '创建期间继续写的草稿' } })
  await act(async () => { finishCreate() })
  expect(save).toHaveBeenCalledWith('server-id', '创建期间继续写的草稿', 'v1', [])
  await act(async () => {
    ref.current!.replaceSavedNote!('server-id', '明确选择的服务器正文', 'remote-v2', [])
    setParentNotes([{ id: stableId, documentId: 'server-id', category: 'setting', title: '雨城', body: '明确选择的服务器正文', versionId: 'remote-v2', comments: [] }])
  })
  expect(screen.getByLabelText('设定正文')).toHaveTextContent('明确选择的服务器正文')
  expect(sessionStorage.getItem('guranovel:setting-creation:studio')).toBe('[]')
  fireEvent.input(screen.getByLabelText('设定正文'), { target: { textContent: '解决之后继续写' } })
  await act(async () => { expect(await ref.current!.flush()).toBe(true) })
  expect(save).toHaveBeenLastCalledWith('server-id', '解决之后继续写', 'remote-v2', [])
})

it('waits for the renamed title before deleting and blocks deletion on title failure', async () => {
  let finishRename!: (success: boolean) => void
  const rename = vi.fn(() => new Promise<boolean>(resolve => { finishRename = resolve }))
  const remove = vi.fn().mockResolvedValue(['a'])
  render(<MemoryRouter><StudioSetting preview={false} defaultPinned backendNotes={[{ id: 'a', documentId: 'a', versionId: 'v1', title: '旧街', body: '旧街原文', category: 'setting' }]}
    onSaveNoteContent={vi.fn().mockResolvedValue({ versionId: 'v2' })} onRenameNote={rename} onDeleteNotes={remove} /></MemoryRouter>)
  fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: '新街' } })
  fireEvent.contextMenu(screen.getByRole('button', { name: /^新街$/ }), { clientX: 30, clientY: 40 })
  fireEvent.click(screen.getByRole('menuitem', { name: '删除条目' }))
  fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
  await act(async () => { await Promise.resolve() })
  expect(rename).toHaveBeenCalledWith('a', '新街')
  expect(remove).not.toHaveBeenCalled()
  await act(async () => { finishRename(false) })
  expect(remove).not.toHaveBeenCalled()
  expect(screen.getByLabelText('设定标题')).toHaveValue('新街')
})

it('flushes a recovered unsaved body even when no new typing occurred after reload', async () => {
  sessionStorage.setItem('guranovel:setting-draft-recovery:a', '刷新前未保存的正文')
  const save = vi.fn().mockResolvedValue({ versionId: 'v2' })
  const ref = createRef<{ flush: () => Promise<boolean> }>()
  render(<MemoryRouter><StudioSetting ref={ref} preview={false} backendNotes={[{ id: 'a', documentId: 'a', versionId: 'v1', title: '旧街', body: '服务器旧正文', category: 'setting' }]} onSaveNoteContent={save} /></MemoryRouter>)
  expect(screen.getByLabelText('设定正文')).toHaveTextContent('刷新前未保存的正文')
  await act(async () => { expect(await ref.current!.flush()).toBe(true) })
  expect(save).toHaveBeenCalledWith('a', '刷新前未保存的正文', 'v1', [])
})

it('keeps a newer title recovery copy when an older title request finishes before the next debounce', async () => {
  let finishRename!: (success: boolean) => void
  const rename = vi.fn(() => new Promise<boolean>(resolve => { finishRename = resolve }))
  const note: SettingNote = { id: 'a', documentId: 'a', versionId: 'v1', title: '旧街', body: '旧街原文', category: 'setting' }
  const props = { preview: false, onSaveNoteContent: vi.fn().mockResolvedValue({ versionId: 'v2' }), onRenameNote: rename }
  const view = render(<MemoryRouter><StudioSetting {...props} backendNotes={[note]} /></MemoryRouter>)
  fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: '请求中的标题' } })
  await act(async () => { vi.advanceTimersByTime(450) })
  expect(rename).toHaveBeenCalledWith('a', '请求中的标题')
  fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: '请求后继续改的新标题' } })
  await act(async () => { finishRename(true) })
  expect(sessionStorage.getItem('guranovel:setting-title-recovery:a')).toBe('请求后继续改的新标题')
  view.unmount()
  render(<MemoryRouter><StudioSetting {...props} backendNotes={[{ ...note, title: '请求中的标题' }]} /></MemoryRouter>)
  expect(screen.getByLabelText('设定标题')).toHaveValue('请求后继续改的新标题')
})

it('keeps the original conflict base when refreshed backend props contain a newer remote version', async () => {
  sessionStorage.setItem('guranovel:setting-draft-recovery:a', '基于 v1 的未保存正文')
  sessionStorage.setItem('guranovel:setting-feedback-recovery:a', JSON.stringify({ body: '基于 v1 的未保存正文', versionId: 'v1', comments: [] }))
  const save = vi.fn().mockResolvedValue('conflict')
  const ref = createRef<{ flush: () => Promise<boolean> }>()
  const note: SettingNote = { id: 'a', documentId: 'a', versionId: 'v1', title: '旧街', body: '旧街原文', category: 'setting' }
  const view = render(<MemoryRouter><StudioSetting ref={ref} preview={false} backendNotes={[note]} onSaveNoteContent={save} /></MemoryRouter>)
  view.rerender(<MemoryRouter><StudioSetting ref={ref} preview={false} backendNotes={[{ ...note, body: '另一位作者更新的正文', versionId: 'remote-v2' }]} onSaveNoteContent={save} /></MemoryRouter>)
  await act(async () => { expect(await ref.current!.flush()).toBe(false) })
  expect(save).toHaveBeenCalledWith('a', '基于 v1 的未保存正文', 'v1', [])
  expect(screen.getByLabelText('设定正文')).toHaveTextContent('基于 v1 的未保存正文')
})
