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
