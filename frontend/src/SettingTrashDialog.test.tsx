import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SettingTrashDialog, { type SettingTrashEntry } from './SettingTrashDialog'

const entries: SettingTrashEntry[] = [
  { id: 'harbor', title: '海港的旧称', category: 'world', deletedAt: '2026-10-19T06:30:00Z' },
  { id: 'lin', title: '林远', category: 'setting', deletedAt: '2026-10-18T06:30:00Z' },
]
beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })))
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

it('loads durable entries and keeps failed batch restores selected for an explicit retry', async () => {
  const onLoad = vi.fn().mockResolvedValue(entries)
  const onRestore = vi.fn().mockResolvedValueOnce(['harbor']).mockResolvedValueOnce(['lin'])
  render(<SettingTrashDialog collectionId="one" onLoad={onLoad} onRestore={onRestore} onClose={vi.fn()} />)
  await screen.findByText('海港的旧称')
  fireEvent.click(screen.getByRole('checkbox', { name: '选择海港的旧称' }))
  fireEvent.click(screen.getByRole('checkbox', { name: '选择林远' }))
  fireEvent.click(screen.getByRole('button', { name: '恢复所选 2 项' }))
  await waitFor(() => expect(screen.queryByText('海港的旧称')).not.toBeInTheDocument())
  expect(screen.getByRole('checkbox', { name: '选择林远' })).toBeChecked()
  expect(screen.getByRole('alert')).toHaveTextContent('1 项恢复结果未能确认')
  fireEvent.click(screen.getByRole('button', { name: '恢复所选 1 项' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('回收站是空的'))
  expect(onRestore.mock.calls.map(call => call[0].map((entry: SettingTrashEntry) => entry.id))).toEqual([['harbor', 'lin'], ['lin']])
})

it('shows load failure and retries instead of claiming the trash is empty', async () => {
  const onLoad = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(entries)
  render(<SettingTrashDialog collectionId="one" onLoad={onLoad} onRestore={vi.fn()} onClose={vi.fn()} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('无法读取回收站')
  expect(screen.queryByText('回收站是空的')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '重新加载回收站' }))
  await screen.findByText('海港的旧称')
  expect(onLoad).toHaveBeenCalledTimes(2)
})

it('keeps hidden selections explicit while searching and keeps archived collections readonly', async () => {
  const onLoad = vi.fn().mockResolvedValue(entries), onRestore = vi.fn()
  const view = render(<SettingTrashDialog collectionId="one" onLoad={onLoad} onRestore={onRestore} onClose={vi.fn()} />)
  await screen.findByText('林远')
  fireEvent.click(screen.getByRole('checkbox', { name: '选择林远' }))
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索回收站' }), { target: { value: '世界观' } })
  expect(screen.queryByText('林远')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '恢复所选 1 项' })).toHaveAccessibleDescription('已选 1 项（1 项不在当前搜索中）')
  expect(screen.getByText('已选 1 项（1 项不在当前搜索中）')).toBeInTheDocument()
  view.rerender(<SettingTrashDialog collectionId="one" readOnly onLoad={onLoad} onRestore={onRestore} onClose={vi.fn()} />)
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /恢复/ })).not.toBeInTheDocument()
  expect(screen.getByText('已归档，只读')).toBeInTheDocument()
  expect(onRestore).not.toHaveBeenCalled()
})

it('blocks duplicate restore calls, preserves entries after rejection and closes with the shared animation', async () => {
  let rejectRestore!: (reason: Error) => void
  const onLoad = vi.fn().mockResolvedValue(entries)
  const onRestore = vi.fn(() => new Promise<string[]>((_, reject) => { rejectRestore = reject }))
  const onClose = vi.fn()
  render(<SettingTrashDialog collectionId="one" onLoad={onLoad} onRestore={onRestore} onClose={onClose} />)
  await screen.findByText('海港的旧称')
  const item = screen.getByRole('listitem', { name: '海港的旧称' })
  fireEvent.click(within(item).getByRole('button', { name: '恢复海港的旧称' }))
  fireEvent.click(within(item).getByRole('button', { name: '恢复海港的旧称' }))
  expect(onRestore).toHaveBeenCalledTimes(1)
  await act(async () => { rejectRestore(new Error('offline')) })
  expect(screen.getByRole('alert')).toHaveTextContent('恢复结果未能确认')
  expect(screen.getByText('海港的旧称')).toBeInTheDocument()
  vi.useFakeTimers()
  fireEvent.click(screen.getByRole('button', { name: '关闭回收站' }))
  expect(screen.getByRole('dialog')).toHaveClass('is-closing')
  expect(onClose).not.toHaveBeenCalled()
  act(() => { vi.advanceTimersByTime(400) })
  expect(onClose).toHaveBeenCalledTimes(1)
})

it('ignores a late response from a different collection instead of mixing deleted documents', async () => {
  let resolveFirst!: (items: SettingTrashEntry[]) => void
  const onLoadFirst = vi.fn(() => new Promise<SettingTrashEntry[]>(resolve => { resolveFirst = resolve }))
  const onLoadSecond = vi.fn().mockResolvedValue([entries[1]])
  const view = render(<SettingTrashDialog collectionId="one" onLoad={onLoadFirst} onRestore={vi.fn()} onClose={vi.fn()} />)
  expect(screen.getByRole('status')).toHaveTextContent('正在读取回收站')
  view.rerender(<SettingTrashDialog collectionId="two" onLoad={onLoadSecond} onRestore={vi.fn()} onClose={vi.fn()} />)
  await screen.findByText('林远')
  await act(async () => { resolveFirst([entries[0]]) })
  expect(screen.queryByText('海港的旧称')).not.toBeInTheDocument()
  expect(screen.getByText('林远')).toBeInTheDocument()
})
