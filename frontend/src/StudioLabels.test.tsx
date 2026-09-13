import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import StudioLabels from './StudioLabels'

beforeEach(() => {
  localStorage.clear()
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new Event('close')) }
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('filters common labels, adds custom labels, prevents duplicates, deletes and restores per novel', () => {
  const view = render(<StudioLabels projectKey="preview" initial={['都市']} />)
  fireEvent.click(screen.getByRole('button', { name: '添加标签' }))
  const input = screen.getByRole('textbox', { name: '搜索或输入标签' })
  fireEvent.change(input, { target: { value: '科' } })
  const common = screen.getByRole('group', { name: '常用标签' })
  expect(within(common).getAllByRole('button')).toHaveLength(1)
  fireEvent.click(within(common).getByRole('button', { name: '科幻' }))
  fireEvent.change(input, { target: { value: '  Space   Opera  ' } })
  fireEvent.submit(input.closest('form')!)
  fireEvent.change(input, { target: { value: 'space opera' } })
  fireEvent.submit(input.closest('form')!)
  expect(screen.getByRole('group', { name: '小说标签' }).querySelectorAll('.studio-label-name')).toHaveLength(3)
  fireEvent.click(screen.getByRole('button', { name: '关闭标签选择' }))
  fireEvent.click(screen.getByRole('button', { name: '删除标签：都市' }))
  view.unmount()
  render(<StudioLabels projectKey="preview" initial={['都市']} />)
  expect(screen.queryByRole('button', { name: '删除标签：都市' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '删除标签：Space Opera' })).toBeInTheDocument()
  cleanup()
  render(<StudioLabels projectKey="another-novel" initial={['历史']} />)
  expect(screen.queryByRole('button', { name: '删除标签：Space Opera' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '删除标签：历史' })).toBeInTheDocument()
})

it('enforces count and name limits, ignores IME Enter, and retains labels when persistence fails', () => {
  render(<StudioLabels projectKey="preview" initial={['都市', '科幻', '奇幻', '玄幻', '仙侠', '悬疑', '推理']} />)
  fireEvent.click(screen.getByRole('button', { name: '添加标签' }))
  const input = screen.getByRole('textbox', { name: '搜索或输入标签' })
  fireEvent.change(input, { target: { value: '字'.repeat(17) } })
  fireEvent.submit(input.closest('form')!)
  expect(screen.queryByRole('button', { name: `删除标签：${'字'.repeat(17)}` })).not.toBeInTheDocument()
  fireEvent.change(input, { target: { value: '群像' } })
  expect(fireEvent.keyDown(input, { key: 'Enter', isComposing: true })).toBe(false)
  fireEvent.submit(input.closest('form')!)
  fireEvent.change(input, { target: { value: '第九个' } })
  fireEvent.submit(input.closest('form')!)
  expect(screen.queryByRole('button', { name: '删除标签：第九个' })).not.toBeInTheDocument()
  expect(within(screen.getByRole('group', { name: '常用标签' })).queryAllByRole('button').every(button => (button as HTMLButtonElement).disabled)).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '关闭标签选择' }))
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  fireEvent.click(screen.getByRole('button', { name: '删除标签：都市' }))
  expect(screen.getByRole('button', { name: '删除标签：都市' })).toBeInTheDocument()
})
