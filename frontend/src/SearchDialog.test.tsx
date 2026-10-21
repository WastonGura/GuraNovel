import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SearchDialog from './SearchDialog'

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })))
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

const items = [{ id: 'a', title: '海港' }, { id: 'b', title: '禁用项', disabled: true }, { id: 'c', title: '旧街' }]

it('keeps preview separate from selection and opens exactly once after the dismissal fallback', () => {
  vi.useFakeTimers()
  const onSelect = vi.fn(), onClose = vi.fn()
  render(<SearchDialog title="搜索" query="" onQuery={vi.fn()} inputLabel="查询" placeholder="查询" items={items}
    onSelect={onSelect} onClose={onClose} renderPreview={item => <p>{item?.title}</p>} />)
  const result = screen.getByRole('button', { name: '旧街' })
  fireEvent.pointerEnter(result)
  expect(screen.getByRole('region', { name: '预览' })).toHaveTextContent('旧街')
  expect(onSelect).not.toHaveBeenCalled()
  fireEvent.click(result)
  const dialog = screen.getByRole('dialog')
  expect(dialog).toHaveClass('is-closing')
  expect(onSelect).not.toHaveBeenCalled()
  act(() => { vi.advanceTimersByTime(400) })
  act(() => { vi.advanceTimersByTime(400) })
  expect(onClose).toHaveBeenCalledTimes(1)
  expect(onSelect).toHaveBeenCalledExactlyOnceWith(items[2])
})

it('skips disabled choices with arrow keys, updates preview and can return to the input', () => {
  const props = { title: '搜索', query: '', onQuery: vi.fn(), inputLabel: '查询', placeholder: '查询', onSelect: vi.fn(), onClose: vi.fn(), renderPreview: (item: { title: string } | undefined) => <p>{item?.title}</p> }
  const view = render(<SearchDialog {...props} items={items} />)
  const input = screen.getByRole('searchbox')
  fireEvent.keyDown(input, { key: 'ArrowDown' })
  expect(screen.getByRole('button', { name: '海港' })).toHaveFocus()
  fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' })
  expect(screen.getByRole('button', { name: '旧街' })).toHaveFocus()
  fireEvent.keyDown(document.activeElement!, { key: 'ArrowUp' })
  fireEvent.keyDown(document.activeElement!, { key: 'ArrowUp' })
  expect(input).toHaveFocus()
  view.rerender(<SearchDialog {...props} items={[]} />)
  expect(within(screen.getByRole('group', { name: '搜索结果' })).queryByRole('button')).not.toBeInTheDocument()
  expect(screen.getByRole('region', { name: '预览' })).not.toHaveTextContent('海港')
})

it('does not open a search result when Enter confirms an IME composition', () => {
  const select = vi.fn()
  render(<SearchDialog title="搜索" query="" onQuery={vi.fn()} inputLabel="查询" placeholder="查询" items={items}
    onSelect={select} onClose={vi.fn()} renderPreview={() => null} />)
  expect(fireEvent.keyDown(screen.getByRole('searchbox'), { key: 'Enter', isComposing: true })).toBe(false)
  expect(select).not.toHaveBeenCalled()
  expect(screen.getByRole('dialog')).not.toHaveClass('is-closing')
})

it('keeps a protected document and the picker open when the selection guard fails', async () => {
  let finish!: (allowed: boolean) => void
  const onBeforeSelect = vi.fn(() => new Promise<boolean>(resolve => { finish = resolve }))
  const onSelect = vi.fn()
  render(<SearchDialog title="关联作品" query="" onQuery={vi.fn()} inputLabel="查询" placeholder="查询" items={items}
    onBeforeSelect={onBeforeSelect} onSelect={onSelect} onClose={vi.fn()} renderPreview={() => null} />)
  const result = screen.getByRole('button', { name: '海港' })
  result.focus()
  fireEvent.click(result)
  fireEvent.click(result)
  expect(onBeforeSelect).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('dialog')).not.toHaveClass('is-closing')
  await act(async () => { finish(false) })
  expect(screen.getByRole('dialog')).not.toHaveClass('is-closing')
  expect(result).toHaveFocus()
  expect(onSelect).not.toHaveBeenCalled()
})

it('protects a document before dismissing and opening its selected destination', async () => {
  vi.useFakeTimers()
  let finish!: (allowed: boolean) => void
  const onSelect = vi.fn(), onClose = vi.fn()
  render(<SearchDialog title="关联作品" query="" onQuery={vi.fn()} inputLabel="查询" placeholder="查询" items={items}
    onBeforeSelect={() => new Promise<boolean>(resolve => { finish = resolve })} onSelect={onSelect} onClose={onClose} renderPreview={() => null} />)
  fireEvent.click(screen.getByRole('button', { name: '海港' }))
  expect(screen.getByRole('dialog')).not.toHaveClass('is-closing')
  await act(async () => { finish(true) })
  expect(screen.getByRole('dialog')).toHaveClass('is-closing')
  expect(onSelect).not.toHaveBeenCalled()
  act(() => { vi.advanceTimersByTime(800) })
  expect(onClose).toHaveBeenCalledTimes(1)
  expect(onSelect).toHaveBeenCalledExactlyOnceWith(items[0])
})

it('ignores a late successful guard after the author dismisses the search window', async () => {
  vi.useFakeTimers()
  let finish!: (allowed: boolean) => void
  const onSelect = vi.fn(), onClose = vi.fn()
  render(<SearchDialog title="关联作品" query="" onQuery={vi.fn()} inputLabel="查询" placeholder="查询" items={items}
    onBeforeSelect={() => new Promise<boolean>(resolve => { finish = resolve })} onSelect={onSelect} onClose={onClose} renderPreview={() => null} />)
  fireEvent.click(screen.getByRole('button', { name: '海港' }))
  fireEvent.click(screen.getByRole('button', { name: '关闭关联作品' }))
  await act(async () => { finish(true) })
  act(() => { vi.advanceTimersByTime(800) })
  expect(onClose).toHaveBeenCalledTimes(1)
  expect(onSelect).not.toHaveBeenCalled()
})
