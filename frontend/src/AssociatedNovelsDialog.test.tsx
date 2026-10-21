import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AssociatedNovelsDialog from './AssociatedNovelsDialog'
import { listCollectionProjects, type Project } from './api/client'

vi.mock('./api/client', async importOriginal => ({ ...await importOriginal<typeof import('./api/client')>(), listCollectionProjects: vi.fn() }))
const list = vi.mocked(listCollectionProjects)
const project = (id: string, extra: Partial<Project> = {}): Project => ({ id, title: `故事 ${id}`, slug: `story-${id}`, genre: null, target_platform: null,
  status: 'draft', workspace_root: `/workspace/${id}`, metadata: {}, created_at: '2026-10-20T00:00:00Z', updated_at: '2026-10-20T00:00:00Z', ...extra })

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('opens a searchable empty state for a collection with no associated novels', async () => {
  list.mockResolvedValue([])
  render(<AssociatedNovelsDialog collectionId="world-a" onOpenProject={vi.fn()} onClose={vi.fn()} />)
  expect(screen.getByRole('dialog', { name: '关联作品' })).toBeInTheDocument()
  expect(await screen.findByText('暂无关联作品')).toBeInTheDocument()
  expect(list).toHaveBeenCalledExactlyOnceWith('world-a')
  expect(screen.getByRole('searchbox', { name: '搜索关联作品' })).toHaveFocus()
})

it('keeps load failure distinct from zero and retries the actual association list', async () => {
  list.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([project('one')])
  const loaded = vi.fn()
  render(<AssociatedNovelsDialog collectionId="world-a" onOpenProject={vi.fn()} onProjectsLoaded={loaded} onClose={vi.fn()} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('关联作品加载失败')
  expect(screen.queryByText('暂无关联作品')).not.toBeInTheDocument()
  expect(screen.queryByText('0 部作品')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '重新加载关联作品' }))
  expect(await screen.findByRole('button', { name: '故事 one' })).toBeInTheDocument()
  expect(list).toHaveBeenCalledTimes(2)
  expect(loaded).toHaveBeenCalledExactlyOnceWith([project('one')])
})

it('reuses full-title, cover, labels, status and description previews while searching labels and archived works', async () => {
  const title = '跨越群星与海洋的很长很长的故事标题'
  list.mockResolvedValue([project('one', { title, status: 'archived', metadata: { labels: ['奇幻', '奇幻', ' 海港 '], introduction: '灯塔重新亮起。', cover: 'cover-1.jpg' } }), project('two')])
  render(<AssociatedNovelsDialog collectionId="world-a" onOpenProject={vi.fn()} onClose={vi.fn()} />)
  await screen.findByRole('button', { name: title })
  const preview = screen.getByRole('region', { name: '预览' })
  expect(within(preview).getByRole('heading', { name: title })).toBeInTheDocument()
  expect(within(preview).getByText('已归档')).toBeInTheDocument()
  expect(within(preview).getByText('灯塔重新亮起。')).toBeInTheDocument()
  expect(within(preview).getAllByText('奇幻')).toHaveLength(1)
  expect(within(preview).getByText('海港')).toBeInTheDocument()
  expect(preview.querySelector('.search-preview-cover .cover-image')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '海港' } })
  expect(screen.getByRole('button', { name: title })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '故事 two' })).not.toBeInTheDocument()
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '已归档' } })
  expect(screen.getByRole('button', { name: title })).toBeInTheDocument()
})

it('retains the picker and focused result after an unsuccessful draft flush, then opens once on retry', async () => {
  const work = project('one')
  list.mockResolvedValue([work])
  const before = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true)
  const open = vi.fn(), close = vi.fn()
  render(<AssociatedNovelsDialog collectionId="world-a" onBeforeOpen={before} onOpenProject={open} onClose={close} />)
  const button = await screen.findByRole('button', { name: '故事 one' })
  button.focus()
  await act(async () => { fireEvent.click(button) })
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(await screen.findByRole('alert')).toHaveTextContent('当前文稿未能保存')
  expect(button).toHaveFocus()
  expect(open).not.toHaveBeenCalled()
  await act(async () => { fireEvent.click(button) })
  expect(before).toHaveBeenCalledTimes(2)
  expect(close).toHaveBeenCalledTimes(1)
  expect(open).toHaveBeenCalledExactlyOnceWith(work)
})

it('expands an overflowing introduction through the same preview control used by global search', async () => {
  const introduction = '大海把过去的记忆送回了岸边。'.repeat(30)
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(function (this: HTMLElement) { return this.classList.contains('search-description-text') ? 480 : 0 })
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) { return this.classList.contains('search-description-text') ? 100 : 0 })
  list.mockResolvedValue([project('long', { metadata: { introduction } })])
  render(<AssociatedNovelsDialog collectionId="world-a" onOpenProject={vi.fn()} onClose={vi.fn()} />)
  const expand = await screen.findByRole('button', { name: '展开简介' })
  expect(expand).toHaveAttribute('aria-expanded', 'false')
  fireEvent.click(expand)
  expect(screen.getByRole('button', { name: '收起简介' })).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByText(introduction)).toHaveClass('is-expanded')
})

it('ignores a previous collection response after switching collections', async () => {
  let oldResponse!: (works: Project[]) => void
  list.mockImplementationOnce(() => new Promise(resolve => { oldResponse = resolve })).mockResolvedValueOnce([project('new')])
  const loaded = vi.fn()
  const props = { onOpenProject: vi.fn(), onProjectsLoaded: loaded, onClose: vi.fn() }
  const view = render(<AssociatedNovelsDialog collectionId="world-a" {...props} />)
  view.rerender(<AssociatedNovelsDialog collectionId="world-b" {...props} />)
  await screen.findByRole('button', { name: '故事 new' })
  await act(async () => { oldResponse([project('old')]) })
  expect(screen.queryByRole('button', { name: '故事 old' })).not.toBeInTheDocument()
  expect(loaded).toHaveBeenCalledExactlyOnceWith([project('new')])
})
