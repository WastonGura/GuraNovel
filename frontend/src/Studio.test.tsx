import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Studio from './Studio'
import { useDraftAutosave } from './useDraftAutosave'
import { ApiError, getProject, listChapters, readDocumentContent, writeDocument } from './api/client'

vi.mock('./api/client', async importOriginal => ({
  ...await importOriginal<typeof import('./api/client')>(), getProject: vi.fn(), listChapters: vi.fn(), readDocumentContent: vi.fn(), writeDocument: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
  localStorage.clear()
  sessionStorage.clear()
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

function openPreview() { return render(<MemoryRouter><Studio /></MemoryRouter>) }
async function stage(name: string | RegExp) {
  await act(async () => fireEvent.click(screen.getByRole('button', { name })))
}

describe('creation studio', () => {
  it('enters automatic focus on typing only; pointer movement exits auto but not manual', async () => {
    const view = openPreview()
    act(() => vi.advanceTimersByTime(20_000))
    expect(view.container.querySelector('.studio')).toHaveAttribute('data-focus-mode', 'off')
    await stage('Draft')
    const prose = screen.getByRole('textbox', { name: '章节正文' })
    fireEvent.keyDown(prose, { key: 'a' })
    expect(view.container.querySelector('.studio')).toHaveAttribute('data-focus-mode', 'auto')
    fireEvent.mouseMove(window)
    expect(view.container.querySelector('.studio')).toHaveAttribute('data-focus-mode', 'off')
    fireEvent.click(screen.getByRole('button', { name: '手动免打扰' }))
    fireEvent.mouseMove(window)
    expect(view.container.querySelector('.studio')).toHaveAttribute('data-focus-mode', 'manual')
    expect(screen.getByRole('button', { name: '展开章节侧边栏' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '退出免打扰' }))
    expect(view.container.querySelector('.studio')).toHaveAttribute('data-focus-mode', 'off')
  })

  it('retains preview prose automatically, and renders restore points as unavailable', async () => {
    openPreview()
    await stage('Draft')
    fireEvent.change(screen.getByRole('textbox', { name: '章节正文' }), { target: { value: '自动保存的正文' } })
    expect(JSON.parse(localStorage.getItem('guranovel:studio-preview:v1')!)['preview-10'].draft).toBe('自动保存的正文')
    expect(screen.getByRole('button', { name: '创建还原点（后端暂未接入）' })).toBeDisabled()
    cleanup(); openPreview(); await stage('Draft')
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('自动保存的正文')
  })

  it('keeps pinned panels visible in focus, and opens an empty Setting via page arrows', async () => {
    const view = openPreview()
    fireEvent.click(screen.getByRole('button', { name: '展开章节侧边栏' }))
    fireEvent.click(screen.getByRole('button', { name: '固定章节目录' }))
    fireEvent.click(screen.getByRole('button', { name: '手动免打扰' }))
    expect(view.container.querySelector('.studio-directory')).toHaveClass('is-pinned')
    expect(view.container.querySelector('.studio-directory')).not.toHaveClass('is-hidden')
    expect(view.container.querySelector('.studio-stats')).toHaveClass('is-hidden')
    fireEvent.keyDown(window, { key: 'Escape' })
    await stage('下一个页面')
    expect(screen.getByLabelText('Setting 留白')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下一个页面' })).toBeDisabled()
  })

  it('recovers new preview chapters but never restores cached publication flags', () => {
    localStorage.setItem('guranovel:studio-preview:v1', JSON.stringify({ added: { number: 11, title: '新的章节', volume: '第四卷', outline: '新大纲', draft: '不能丢失的新章正文', published: true, stage: 'Final' } }))
    openPreview()
    expect(screen.getByRole('heading', { name: '第11话 新的章节' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('不能丢失的新章正文')
    expect(screen.getByRole('textbox', { name: '章节正文' })).not.toHaveAttribute('readonly')
  })

  it('collects three review completions, notifies while viewing an old chapter, and locks Block selections', async () => {
    openPreview()
    await stage('Draft'); await stage('提交审阅')
    act(() => vi.advanceTimersByTime(1450))
    expect(screen.queryByLabelText('审阅报告')).not.toBeInTheDocument()
    expect(screen.getByText('Editor Reviewer 完成审阅')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开章节侧边栏' }))
    await stage(/第9话.*重逢/)
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveAttribute('readonly')
    act(() => vi.advanceTimersByTime(3000))
    expect(screen.getByRole('heading', { name: '第9话 重逢' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '通知，1 条未读' }))
    await stage(/第10话审阅完成.*查看本章报告/)
    const block = screen.getByRole('checkbox', { name: '交给 Agent 修改：时间线需要统一' })
    expect(block).toBeChecked(); expect(block).toBeDisabled()
    expect(screen.getByRole('button', { name: '进入读者环节' })).toBeDisabled()
    await stage('修改所选 1 项并重新审阅')
    act(() => vi.advanceTimersByTime(4100))
    expect(screen.queryByRole('checkbox', { name: '交给 Agent 修改：时间线需要统一' })).not.toBeInTheDocument()
    expect((screen.getByRole('textbox', { name: '章节正文' }) as HTMLTextAreaElement).value).toContain('又过了几个小时')
    await stage('进入读者环节')
    expect(screen.getByRole('button', { name: '跳过读者环节' })).toBeInTheDocument()
    await stage('跳过读者环节')
    await stage('发布章节')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await stage('确认发布')
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveAttribute('readonly')
    expect(screen.getByRole('button', { name: 'Draft' })).toBeDisabled()
  })

  it('supports outline choices and author-observer reader invitations without chat input', async () => {
    openPreview()
    fireEvent.change(screen.getByRole('textbox', { name: '大纲思路' }), { target: { value: '写一场重逢' } })
    await stage('生成大纲方案')
    fireEvent.click(screen.getByRole('button', { name: /方案 01/ }))
    expect((screen.getByRole('textbox', { name: '编辑大纲' }) as HTMLTextAreaElement).value).toContain('写一场重逢')
    await stage('确认大纲并进入正文'); await stage('提交审阅')
    act(() => vi.advanceTimersByTime(4100)); await stage('修改所选 1 项并重新审阅')
    act(() => vi.advanceTimersByTime(4100)); await stage('进入读者环节')
    expect(screen.getByRole('button', { name: '开始阅读' })).toBeDisabled()
    await stage('邀请剧情党')
    expect(screen.getByRole('button', { name: '取消邀请剧情党' })).toHaveAttribute('aria-pressed', 'true')
    await stage('开始阅读')
    expect(screen.getByText('作者旁观 · 无需参与讨论')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('does not substitute fixtures when a real project fails to load', async () => {
    vi.mocked(getProject).mockRejectedValue(new Error('offline'))
    vi.mocked(listChapters).mockResolvedValue([])
    await act(async () => render(<MemoryRouter initialEntries={['/projects/p/studio']}><Routes><Route path="/projects/:projectId/studio" element={<Studio />} /></Routes></MemoryRouter>))
    expect(screen.getByRole('alert')).toHaveTextContent('没有使用示例数据替代真实章节')
    expect(readDocumentContent).not.toHaveBeenCalled()
  })

  it('saves real drafts before page navigation and reuses the returned version after remount', async () => {
    vi.mocked(getProject).mockResolvedValue({ id: 'p', title: 'Real novel' } as Awaited<ReturnType<typeof getProject>>)
    vi.mocked(listChapters).mockResolvedValue([{ id: 'c', chapter_number: 1, title: 'Chapter', metadata: {}, current_draft_document_id: 'doc' }] as Awaited<ReturnType<typeof listChapters>>)
    vi.mocked(readDocumentContent).mockResolvedValue({ document_id: 'doc', version_id: 'v1', content: 'server text' })
    vi.mocked(writeDocument).mockResolvedValueOnce({ id: 'v2' } as Awaited<ReturnType<typeof writeDocument>>).mockResolvedValueOnce({ id: 'v3' } as Awaited<ReturnType<typeof writeDocument>>)
    await act(async () => render(<MemoryRouter initialEntries={['/projects/p/studio']}><Routes><Route path="/projects/:projectId/studio" element={<Studio />} /></Routes></MemoryRouter>))
    fireEvent.change(screen.getByRole('textbox', { name: '章节正文' }), { target: { value: 'first change' } })
    await stage('下一个页面')
    expect(writeDocument).toHaveBeenCalledWith('doc', { content: 'first change', expected_current_version_id: 'v1' })
    await stage('上一个页面')
    fireEvent.change(screen.getByRole('textbox', { name: '章节正文' }), { target: { value: 'second change' } })
    await act(async () => vi.advanceTimersByTime(700))
    expect(writeDocument).toHaveBeenLastCalledWith('doc', { content: 'second change', expected_current_version_id: 'v2' })
  })
})

describe('draft auto-save', () => {
  it('serializes changes typed during an in-flight save against the new server version', async () => {
    let complete!: (value: Awaited<ReturnType<typeof writeDocument>>) => void
    vi.mocked(writeDocument).mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
    vi.mocked(writeDocument).mockResolvedValueOnce({ id: 'v3' } as Awaited<ReturnType<typeof writeDocument>>)
    const hook = renderHook(() => useDraftAutosave('doc', 'v1', 'original'))
    act(() => hook.result.current.change('first'))
    await act(async () => vi.advanceTimersByTime(700))
    act(() => hook.result.current.change('second'))
    await act(async () => complete({ id: 'v2' } as Awaited<ReturnType<typeof writeDocument>>))
    expect(writeDocument).toHaveBeenNthCalledWith(2, 'doc', { content: 'second', expected_current_version_id: 'v2' })
    expect(hook.result.current.status).toBe('已自动保存')
    expect(hook.result.current.text).toBe('second')
  })

  it('retains local prose on conflicts and reports an unsuccessful flush', async () => {
    vi.mocked(writeDocument).mockRejectedValue(new ApiError(409, 'document_version_conflict', 'conflict'))
    const hook = renderHook(() => useDraftAutosave('doc', 'v1', 'original'))
    act(() => hook.result.current.change('never lose this'))
    await act(async () => { expect(await hook.result.current.flush()).toBe(false) })
    expect(hook.result.current.text).toBe('never lose this')
    expect(hook.result.current.status).toContain('版本冲突')
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
  })

  it('recovers unsaved prose after leaving the route without overwriting a newer server version', async () => {
    const first = renderHook(() => useDraftAutosave('doc', 'v1', 'original'))
    act(() => first.result.current.change('recover me'))
    first.unmount()
    const second = renderHook(() => useDraftAutosave('doc', 'v2', 'changed remotely'))
    expect(second.result.current.text).toBe('recover me')
    await act(async () => { expect(await second.result.current.flush()).toBe(false) })
    expect(writeDocument).not.toHaveBeenCalled()
    expect(second.result.current.status).toContain('冲突')
  })
})
