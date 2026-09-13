import { act, cleanup, fireEvent, render, renderHook, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Studio from './Studio'
import { draftRecoveryCopies, useDraftAutosave } from './useDraftAutosave'
import { commentColors, commentLimit, previewIssues, reanchorComments, restoreOutlineComments, loadDraftArchives } from './studioPreview'
import { ApiError, getProject, listChapters, readDocumentContent, writeDocument, createRestorePoint, listRestorePoints, readDocumentVersionContent, readRestorePointFeedback, readStudioFeedback, restorePoint } from './api/client'
import { COMMENT_IDLE_MS } from './useFloatingComment'
import { writeStudioDraft } from './api/client'
import { listChapterProductionRuns } from './api/chapterProductionV2Client'

vi.mock('./api/chapterProductionV2Client', async original => ({ ...await original<typeof import('./api/chapterProductionV2Client')>(), listChapterProductionRuns: vi.fn() }))

vi.mock('./api/client', async importOriginal => ({
  ...await importOriginal<typeof import('./api/client')>(), getProject: vi.fn(), listChapters: vi.fn(), readDocumentContent: vi.fn(), writeDocument: vi.fn(), createRestorePoint: vi.fn(), listRestorePoints: vi.fn(), readDocumentVersionContent: vi.fn(), readRestorePointFeedback: vi.fn(), readStudioFeedback: vi.fn(), restorePoint: vi.fn(),
  writeStudioDraft: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listChapterProductionRuns).mockResolvedValue([])
  vi.useFakeTimers()
  localStorage.clear()
  sessionStorage.clear()
  draftRecoveryCopies.clear()
  vi.mocked(writeStudioDraft).mockImplementation((_project, _chapter, payload) => writeDocument('doc', payload))
  vi.mocked(readStudioFeedback).mockImplementation(async (_project, chapter_id, region) => {
    let source_version_id = 'v1'
    try { source_version_id = (await vi.mocked(writeDocument).mock.results.at(-1)?.value)?.id || source_version_id } catch { /* Failed writes leave the version unchanged. */ }
    let archived: Awaited<ReturnType<typeof readRestorePointFeedback>> | undefined
    try {
      const restored = await vi.mocked(restorePoint).mock.results.at(-1)?.value
      if (restored) { source_version_id = restored.id; archived = await readRestorePointFeedback('p', chapter_id, 'point') }
    } catch { /* Failed restoration keeps the current draft. */ }
    return { chapter_id, region, document_id: 'doc', source_version_id, revision: 0, comments: archived?.comments || [], requirements: archived?.requirements || '', read_only: false }
  })
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

function openPreview() { return render(<MemoryRouter><Studio /></MemoryRouter>) }
async function stage(name: string | RegExp) {
  await act(async () => fireEvent.click(screen.getByRole('button', { name })))
}

describe('creation studio', () => {
  it('keeps a blocked Reader attempt on Review and expires feedback after the latest attempt', async () => {
    openPreview(); await stage('Draft'); await stage('提交审阅')
    act(() => vi.advanceTimersByTime(4100))
    await stage('Reader')
    expect(screen.getByRole('button', { name: 'Review' })).toHaveAttribute('aria-current', 'step')
    expect(screen.getByText('审阅完成并处理 Block 问题后，才能进入下一阶段。')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1800))
    await stage('Reader')
    act(() => vi.advanceTimersByTime(700))
    expect(screen.getByText('审阅完成并处理 Block 问题后，才能进入下一阶段。')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1800))
    expect(screen.queryByText('审阅完成并处理 Block 问题后，才能进入下一阶段。')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review' })).toHaveAttribute('aria-current', 'step')
  })

  it('preloads page surfaces, preserves setting editing and counts rapid navigation clicks', async () => {
    openPreview()
    const setting = document.querySelector('[data-studio-page="Setting"]')!
    expect(document.querySelectorAll('[data-studio-page]')).toHaveLength(3)
    await stage('下一个页面')
    await stage('编辑')
    fireEvent.change(screen.getByRole('textbox', { name: '设定正文' }), { target: { value: '页面切换后仍然保留这段文字' } })
    const next = screen.getByRole('button', { name: '下一个页面' })
    await act(async () => { fireEvent.click(next); fireEvent.click(next); fireEvent.click(next) })
    expect(screen.getByRole('region', { name: 'Setting 工作区' })).toBeInTheDocument()
    expect(document.querySelector('[data-studio-page="Setting"]')).toBe(setting)
    expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveValue('页面切换后仍然保留这段文字')
    await stage('下一个页面'); await stage('下一个页面'); await stage('下一个页面')
    expect(screen.getByRole('textbox', { name: '设定正文' })).toHaveValue('页面切换后仍然保留这段文字')
  })
  it('keeps the scrollbar at the window edge when the manuscript moves without resizing', async () => {
    let left = 456, moving = true
    const bounds = HTMLElement.prototype.getBoundingClientRect
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('studio-manuscript-viewport') ? { left, width: 1144 } as DOMRect : bounds.call(this)
    })
    let nextFrame!: FrameRequestCallback
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => { nextFrame = callback; return 1 })
    openPreview(); await stage('Draft')
    const viewport = document.querySelector<HTMLElement>('.studio-manuscript-viewport')!
    const body = viewport.closest<HTMLElement>('.studio-body')!
    body.getAnimations = () => moving ? [{ playState: 'running', transitionProperty: 'left' } as CSSTransition] : []
    expect(viewport.style.getPropertyValue('--chapter-gutter')).toBe('448px')
    const event = new Event('transitionrun', { bubbles: true })
    Object.defineProperty(event, 'propertyName', { value: 'left' })
    left = 520
    fireEvent(body, event)
    expect(viewport.style.getPropertyValue('--chapter-gutter')).toBe('512px')
    left = 663; moving = false
    act(() => nextFrame(360))
    expect(viewport.style.getPropertyValue('--chapter-gutter')).toBe('655px')
  })

  it('keeps submitted comments in Draft without exposing them over the review report', async () => {
    const comment = { id: 'submitted', start: 0, end: 2, quote: '正文', text: '修改意见', color: commentColors[0], submitted: true }
    localStorage.setItem('guranovel:studio-preview:v1', JSON.stringify({ 'preview-10': { outline: '大纲', draft: '正文测试', draftComments: [comment] } }))
    openPreview(); await stage('Draft')
    expect(screen.getByRole('group', { name: '已提交评论圆点' })).toBeInTheDocument()
    await stage('查看已提交评论：正文'); await stage('提交审阅')
    expect(screen.queryByRole('group', { name: '已提交评论圆点' })).toBeNull()
    act(() => vi.advanceTimersByTime(4100))
    expect(screen.getByLabelText('审阅报告')).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: '已提交评论' })).toBeNull()
    await stage('Draft')
    expect(screen.getByRole('button', { name: '查看已提交评论：正文' })).toBeInTheDocument()
  })

  it('opens and collapses the same assistant surface, retains drafts and previews messages without an Agent call', async () => {
    openPreview()
    const launcher = screen.getByRole('button', { name: 'Gura' })
    const surface = launcher.parentElement
    expect(screen.queryByRole('dialog', { name: '与 Gura 对话' })).toBeNull()
    await stage('Gura')
    const input = screen.getByRole('textbox', { name: '给 Gura 的消息' })
    expect(input).toHaveFocus()
    expect(screen.getByRole('button', { name: '发送给 Gura（仅预览）' })).toBeDisabled()
    fireEvent.change(input, { target: { value: '保留我的想法' } })
    await stage('收起助手')
    expect(launcher).toHaveFocus()
    await stage('Gura')
    expect(launcher.parentElement).toBe(surface)
    expect(input).toHaveValue('保留我的想法')
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(screen.queryByText('你 · 未发送')).toBeNull()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(input).toHaveValue('')
    expect(within(screen.getByRole('log', { name: '助手对话记录' })).getByText('保留我的想法')).toBeInTheDocument()
    expect(screen.getByText('你 · 未发送')).toBeInTheDocument()
    expect(writeDocument).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(launcher).toHaveAttribute('aria-expanded', 'false')
    expect(launcher).toHaveFocus()
  })

  it('waits for the actual page transition before showing comments, then idles, wakes and moves by keyboard', async () => {
    const comment = { id: 'pending', start: 0, end: 2, quote: '正文', text: '意见', color: commentColors[0] }
    localStorage.setItem('guranovel:studio-preview:v1', JSON.stringify({ 'preview-10': { outline: '大纲', draft: '正文测试', draftComments: [comment] } }))
    let finish!: () => void
    const finished = new Promise<void>(resolve => { finish = resolve })
    const original = document.startViewTransition
    document.startViewTransition = ((callback: () => void) => { callback(); return { finished, skipTransition: vi.fn() } }) as unknown as typeof document.startViewTransition
    const originalMedia = window.matchMedia
    window.matchMedia = () => ({ matches: false }) as MediaQueryList
    try {
      openPreview(); await stage('Draft')
      const panel = document.querySelector('.studio-floating-comment') as HTMLElement
      expect(panel.dataset.ready).toBe('false'); expect(panel.inert).toBe(true)
      await act(async () => finish())
      expect(panel.dataset.ready).toBe('true'); expect(panel.inert).toBe(false)
      act(() => vi.advanceTimersByTime(COMMENT_IDLE_MS + 10))
      expect(panel.dataset.idle).toBe('true'); expect(panel.inert).toBe(true)
      fireEvent.pointerMove(document, { clientX: 0, clientY: 0 })
      expect(panel.dataset.idle).toBe('false'); expect(panel.inert).toBe(false)
      const previous = panel.style.left
      vi.spyOn(panel, 'getBoundingClientRect').mockReturnValue({ left: parseFloat(previous), top: 115 } as DOMRect)
      fireEvent.keyDown(screen.getByRole('button', { name: '拖动评论框' }), { key: 'ArrowRight' })
      expect(panel.style.left).not.toBe(previous)
      expect(panel.querySelector('textarea')).toHaveValue('意见')
    } finally { document.startViewTransition = original; window.matchMedia = originalMedia }
  })

  it('routes underline clicks to pending comments or the selected submitted circle', async () => {
    const draft = '甲乙丙丁'
    const draftComments = [...draft].map((quote, start) => ({ id: String(start), start, end: start + 1, quote, text: `意见${quote}`, color: commentColors[start], submitted: start > 1 }))
    localStorage.setItem('guranovel:studio-preview:v1', JSON.stringify({ 'preview-10': { outline: '大纲', draft, draftComments } }))
    const createRange = document.createRange.bind(document)
    vi.spyOn(document, 'createRange').mockImplementation(() => {
      const range = createRange()
      range.getClientRects = () => [{ left: 8, top: 10, right: 28, bottom: 30, width: 20, height: 20 }] as unknown as DOMRectList
      return range
    })
    openPreview(); await stage('Draft')
    await stage('打开评论：乙')
    expect(screen.getByRole('textbox', { name: '给 Agent 的修改意见' })).toHaveValue('意见乙')
    await stage('打开评论：丁')
    expect(screen.getByRole('button', { name: '查看已提交评论：丁' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('textbox', { name: '已提交评论' })).toHaveValue('意见丁')
    expect(document.querySelector('[popover]')).toBeNull()
    fireEvent.change(screen.getByRole('textbox', { name: '已提交评论' }), { target: { value: '修改后的意见丁' } })
    await stage('查看已提交评论：丙')
    expect(screen.getByRole('textbox', { name: '已提交评论' })).toHaveValue('意见丙')
    await stage('查看已提交评论：丁')
    expect(screen.getByRole('textbox', { name: '已提交评论' })).toHaveValue('修改后的意见丁')
    await stage('查看已提交评论：丁')
    fireEvent.change(screen.getByRole('textbox', { name: '给写作 Agent 的要求' }), { target: { value: '保留的通用要求' } })
    await stage('查看已提交评论：丙'); await stage('查看已提交评论：丙')
    expect(screen.getByRole('textbox', { name: '给写作 Agent 的要求' })).toHaveValue('保留的通用要求')
    fireEvent.keyDown(screen.getByRole('button', { name: '查看已提交评论：丁' }), { key: 'ArrowLeft', altKey: true })
    expect(within(screen.getByRole('group', { name: '已提交评论圆点' })).getAllByRole('button')[0]).toHaveAccessibleName('查看已提交评论：丁')
    await stage('查看已提交评论：丁')
    fireEvent.keyDown(screen.getByRole('button', { name: '查看已提交评论：丁' }), { key: 'Delete' })
    expect(screen.getByRole('textbox', { name: '给写作 Agent 的要求' })).toHaveValue('保留的通用要求')
  })
  it('confirms selection and cancellation without submitting, and expires feedback after the latest click', async () => {
    openPreview(); await stage('Draft'); await stage('提交审阅')
    act(() => vi.advanceTimersByTime(4100))
    const checkbox = screen.getByRole('checkbox', { name: '交给 Agent 修改：结尾可以更克制' })
    fireEvent.click(checkbox)
    expect(checkbox).toBeChecked()
    expect(screen.getByText('已加入待修改列表：结尾可以更克制')).toBeInTheDocument()
    expect(screen.getByLabelText('审阅报告')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1800))
    fireEvent.click(checkbox)
    expect(checkbox).not.toBeChecked()
    act(() => vi.advanceTimersByTime(700))
    expect(screen.getByText('已移出待修改列表：结尾可以更克制')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1800))
    expect(screen.queryByText('已移出待修改列表：结尾可以更克制')).not.toBeInTheDocument()
    await stage('Suggestion · 结尾可以更克制')
    expect(screen.getByText('对应原文：也许并不只是一个傍晚。')).toBeInTheDocument()
    expect(checkbox).not.toBeChecked()
  })
  it('groups reports by reviewer, shows the highest severity or PASS, and collapses independently', async () => {
    const extra = { ...previewIssues[1], id: 'extra', reviewer: 'Lore Reviewer' as const, title: '另一条连续性问题' }
    previewIssues.push(extra)
    try {
      const view = openPreview()
      await stage('Draft'); await stage('提交审阅')
      act(() => vi.advanceTimersByTime(4100))
      const groups = [...view.container.querySelectorAll<HTMLDetailsElement>('.studio-review-group')]
      expect(groups).toHaveLength(3)
      expect(groups.map(group => group.querySelector('summary')?.textContent)).toEqual(['Editor ReviewerSuggestion', 'Chief ReviewerWarning', 'Lore ReviewerBlock'])
      const lore = groups[2], editor = groups[0]
      const block = within(lore).getByRole('checkbox', { name: '交给 Agent 修改：时间线需要统一' })
      expect(block).toBeChecked(); expect(block).toBeDisabled()
      fireEvent.click(lore.querySelector('summary')!)
      expect(lore.open).toBe(false); expect(editor.open).toBe(true)
      fireEvent.click(within(editor).getByRole('checkbox'))
      expect(lore.open).toBe(false)
      fireEvent.click(lore.querySelector('summary')!)
      expect(block).toBeChecked()
      expect(lore.querySelector('[data-motion-control]')).toBeNull()
      await stage('修改所选 2 项并重新审阅')
      act(() => vi.advanceTimersByTime(4100))
      expect(view.container.querySelector('.studio-review-group summary')).toHaveTextContent('Editor ReviewerPASS')
      expect([...view.container.querySelectorAll('.studio-review-group summary')][2]).toHaveTextContent('Lore ReviewerWarning')
    } finally { previewIssues.pop() }
  })
  it('keeps chapter status independent from selection and removes trailing status text', async () => {
    openPreview()
    await stage('展开章节侧边栏')
    const ongoing = screen.getByRole('button', { name: '第10话 吃吃吃' })
    const closed = screen.getByRole('button', { name: '第9话 重逢' })
    expect(ongoing).toHaveAttribute('data-status', 'ongoing')
    expect(ongoing).toHaveAttribute('aria-current', 'page')
    expect(closed).toHaveAttribute('data-status', 'closed')
    expect(closed).toHaveAccessibleDescription('已结束')
    expect(closed.querySelector('small')).toBeNull()
    await stage('第9话 重逢')
    expect(closed).toHaveAttribute('aria-current', 'page')
    expect(closed).toHaveAttribute('data-status', 'closed')
    expect(ongoing).not.toHaveAttribute('aria-current')
    expect(ongoing).toHaveAttribute('data-status', 'ongoing')
  })

  it('wraps every comment into more rows and promotes the selected comment without an expansion button', async () => {
    const outline = 'ABCDEFGHIJKLMNOPQRST'
    const outlineComments = [...outline].map((quote, start) => ({ id: String(start), start, end: start + 1, quote, color: '#ff858d', text: `意见 ${start + 1}` }))
    localStorage.setItem('guranovel:studio-preview:v1', JSON.stringify({ 'preview-10': { outline, draft: '正文', outlineComments } }))
    const view = openPreview()
    expect(screen.getAllByRole('button', { name: /^评论：/ })).toHaveLength(20)
    expect(screen.getByRole('textbox', { name: '给 Agent 的修改意见' })).toHaveValue('意见 1')
    expect(screen.queryByRole('button', { name: /查看全部/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '评论：T' })).toHaveStyle({ transform: 'translate(72px, 78px)' })
    await stage('评论：T')
    expect(screen.getByRole('button', { name: '评论：T' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('textbox', { name: '给 Agent 的修改意见' })).toHaveValue('意见 20')
    expect(view.container.querySelector('.studio-comment-dots')).toHaveStyle({ height: '130px' })
    expect(screen.getByRole('button', { name: '评论：T' })).toHaveStyle({ transform: 'translate(0px, 104px)' })
  })

  it('adds anchored Draft comments only after confirmation, reanchors on edits and restores them', async () => {
    openPreview(); await stage('Draft')
    const prose = screen.getByRole('textbox', { name: '章节正文' }) as HTMLTextAreaElement
    const original = prose.value
    prose.setSelectionRange(0, 4)
    fireEvent.pointerUp(prose)
    expect(screen.queryByRole('group', { name: '正文评论' })).not.toBeInTheDocument()
    await stage('评论')
    const comment = screen.getByRole('textbox', { name: '给 Agent 的修改意见' })
    expect(comment).toHaveFocus()
    fireEvent.change(comment, { target: { value: '补充雨停的细节' } })
    await stage('发送正文修改意见')
    expect(prose).toHaveValue(original)
    fireEvent.change(prose, { target: { value: `新${original}` } })
    const read = () => JSON.parse(localStorage.getItem('guranovel:studio-preview:v1')!)['preview-10']
    expect(read().draftComments[0]).toMatchObject({ start: 0, end: 5, text: '补充雨停的细节', submitted: true })
    await stage('Outline'); await stage('Draft')
    expect(screen.queryByRole('textbox', { name: '给 Agent 的修改意见' })).not.toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: '已提交评论圆点' })).getAllByRole('button')).toHaveLength(1)
    cleanup(); openPreview(); await stage('Draft')
    expect(screen.queryByRole('textbox', { name: '给 Agent 的修改意见' })).not.toBeInTheDocument()
    await stage(/^查看已提交评论：/)
    expect(screen.getByRole('textbox', { name: '已提交评论' })).toHaveValue('补充雨停的细节')
    const restoredProse = screen.getByRole('textbox', { name: '章节正文' }) as HTMLTextAreaElement
    restoredProse.setSelectionRange(6, 9); fireEvent.pointerUp(restoredProse); await stage('评论')
    expect(screen.getByRole('textbox', { name: '给 Agent 的修改意见' })).toHaveValue('')
    expect(within(screen.getByRole('group', { name: '正文评论' })).getAllByRole('button')).toHaveLength(1)
    expect(new Set(read().draftComments.map((comment: { color: string }) => comment.color)).size).toBe(2)
    await stage('展开章节侧边栏'); await stage('第9话 重逢')
    expect(screen.queryByRole('group', { name: '正文评论' })).not.toBeInTheDocument()
  })

  it('tracks the current workflow path and cycles pages in both directions', async () => {
    openPreview()
    const workflow = screen.getByRole('navigation', { name: '创作阶段' })
    expect(workflow).toHaveStyle({ '--workflow-progress': '0' })
    await stage('Draft')
    expect(workflow).toHaveStyle({ '--workflow-progress': '0.25' })
    await stage('Outline')
    expect(workflow).toHaveStyle({ '--workflow-progress': '0' })
    await stage('上一个页面')
    expect(screen.getByRole('region', { name: 'Detail 工作区' })).toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: '章节侧边栏' })).not.toBeInTheDocument()
    await stage('上一个页面')
    expect(screen.getByRole('region', { name: 'Setting 工作区' })).toBeInTheDocument()
    await stage('下一个页面')
    expect(screen.getByRole('region', { name: 'Detail 工作区' })).toBeInTheDocument()
    await stage('下一个页面')
    expect(screen.getByRole('region', { name: 'Create 工作区' })).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: '章节侧边栏' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上一个页面' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下一个页面' })).toBeInTheDocument()
  })

  it('returns home from the new icon without adding a blue click effect', async () => {
    const view = render(<MemoryRouter initialEntries={['/preview/studio']}><Routes><Route path="/preview/studio" element={<Studio />} /><Route path="/" element={<h1>主页</h1>} /></Routes></MemoryRouter>)
    expect(view.container.querySelector('[data-glow]')).not.toBeInTheDocument()
    const home = screen.getByRole('button', { name: '返回主页' })
    expect(home.querySelector('img')).toHaveAttribute('src', '/ui/studio/home.svg')
    await stage('返回主页')
    expect(screen.getByRole('heading', { name: '主页' })).toBeInTheDocument()
  })

  it('anchors colored comments to selections, switches their drafts and retains them without appending to the outline', async () => {
    const view = openPreview()
    fireEvent.change(screen.getByRole('textbox', { name: '大纲思路' }), { target: { value: '场景一\n场景二\n场景三' } })
    await stage('生成大纲方案'); await stage(/方案 01/)
    const outline = screen.getByRole('textbox', { name: '编辑大纲' }) as HTMLTextAreaElement
    const original = outline.value
    for (const [start, text] of [[0, '拉近人物距离'], [4, '补充动机'], [8, '保留悬念']] as const) {
      outline.setSelectionRange(start, start + 3)
      fireEvent.pointerUp(outline)
      expect(screen.queryAllByRole('button', { name: /^评论：/ })).toHaveLength(start / 4)
      await stage('评论')
      fireEvent.change(screen.getByRole('textbox', { name: '给 Agent 的修改意见' }), { target: { value: text } })
    }
    const read = () => JSON.parse(localStorage.getItem('guranovel:studio-preview:v1')!)['preview-10']
    expect(new Set(read().outlineComments.map((comment: { color: string }) => comment.color)).size).toBe(3)
    expect(view.container.querySelectorAll('.studio-outline-comment-marker')).toHaveLength(3)
    const movements: Keyframe[][] = []
    const animate = vi.fn((frames: Keyframe[]) => { movements.push(frames); return { finished: Promise.resolve(), cancel: vi.fn(), playState: 'finished' } })
    screen.getAllByRole('button', { name: /^评论：/ }).forEach(button => { button.animate = animate as unknown as typeof button.animate })
    await stage('评论：场景一')
    expect(screen.getByRole('textbox', { name: '给 Agent 的修改意见' })).toHaveValue('拉近人物距离')
    expect(movements[2].map(frame => frame.transform)).toEqual(['translate(24px, 0px)', 'translate(24px, 26px)', 'translate(0px, 26px)'])
    expect(read().outlineComments.map((comment: { quote: string }) => comment.quote)).toEqual(['场景一', '场景三', '场景二'])
    expect(view.container.querySelector('.studio-outline-comment')).toHaveStyle({ '--comment-color': read().outlineComments[0].color })
    movements.length = 0
    await stage('评论：场景三')
    expect(movements[1].map(frame => frame.transform)).toEqual(['translate(0px, 0px)', 'translate(0px, 26px)'])
    await stage('发送大纲修改意见')
    expect(outline).toHaveValue(original)
    expect(screen.getByRole('textbox', { name: '给 Agent 的修改意见' })).toHaveValue('保留悬念')
    cleanup(); openPreview()
    expect(screen.getByRole('textbox', { name: '给 Agent 的修改意见' })).toHaveValue('保留悬念')
    expect(screen.getAllByRole('button', { name: /^评论：/ })).toHaveLength(3)
  })

  it('moves annotation anchors on edits, retains deleted comments and rejects invalid stored anchors', () => {
    const comment = { id: 'c', start: 2, end: 4, quote: '丙丁', text: '用户评论', color: '#ff858d' }
    expect(reanchorComments([comment], '甲乙丙丁戊', '新甲乙丙丁戊')[0]).toMatchObject({ start: 3, end: 5, quote: '丙丁' })
    expect(reanchorComments([comment], '甲乙丙丁戊', '甲乙戊')[0]).toMatchObject({ start: 2, end: 2, quote: '丙丁', text: '用户评论' })
    expect(restoreOutlineComments([comment, comment, { ...comment, id: 'bad', end: 200 }], '甲乙丙丁戊')).toEqual([{ ...comment, submitted: false, orphaned: false }])
  })

  it('repairs repeated legacy colors without dropping comments and blocks additions at the shared limit', async () => {
    const draft = 'ABCDEFGHIJKLMNOPQRST'
    const draftComments = [...draft].slice(0, commentLimit).map((quote, start) => ({ id: String(start), start, end: start + 1, quote, color: commentColors[0], text: '意见', submitted: start < 6 }))
    const restored = restoreOutlineComments(draftComments, draft)
    expect(restored).toHaveLength(commentLimit)
    expect(new Set(restored.map(comment => comment.color)).size).toBe(commentLimit)
    expect(restoreOutlineComments([...draftComments, { ...draftComments[0], id: 'legacy-extra' }], draft)).toHaveLength(commentLimit + 1)
    localStorage.setItem('guranovel:studio-preview:v1', JSON.stringify({ 'preview-10': { outline: '大纲', draft, draftComments } }))
    openPreview(); await stage('Draft')
    const prose = screen.getByRole('textbox', { name: '章节正文' }) as HTMLTextAreaElement
    prose.setSelectionRange(15, 16); fireEvent.pointerUp(prose); await stage('评论')
    expect(screen.getByText(`评论上限为 ${commentLimit} 条，请先移除不再需要的评论。`)).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: '已提交评论圆点' })).getAllByRole('button')).toHaveLength(6)
    fireEvent.keyDown(within(screen.getByRole('group', { name: '正文评论' })).getAllByRole('button')[0], { key: 'Delete' })
    prose.setSelectionRange(15, 16); fireEvent.pointerUp(prose); await stage('评论')
    const saved = JSON.parse(localStorage.getItem('guranovel:studio-preview:v1')!)['preview-10'].draftComments
    expect(saved).toHaveLength(commentLimit)
    expect(new Set(saved.map((comment: { color: string }) => comment.color)).size).toBe(commentLimit)
  })

  it('interrupts dot movement and continues from the currently rendered transforms', async () => {
    const outline = '甲乙丙'
    const outlineComments = [...outline].map((quote, start) => ({ id: String(start), start, end: start + 1, quote, color: commentColors[start], text: quote }))
    localStorage.setItem('guranovel:studio-preview:v1', JSON.stringify({ 'preview-10': { outline, draft: '正文', outlineComments } }))
    openPreview()
    const cancel = vi.fn(), frames: Keyframe[][] = []
    screen.getAllByRole('button', { name: /^评论：/ }).forEach(button => { button.animate = ((keys: Keyframe[]) => { frames.push(keys); return { finished: new Promise(() => {}), playState: 'running', cancel } }) as unknown as typeof button.animate })
    await stage('评论：丙')
    const originalStyle = window.getComputedStyle
    vi.spyOn(window, 'getComputedStyle').mockImplementation((element, pseudo) => {
      const style = originalStyle(element, pseudo)
      if (element.matches('.studio-comment-dots button')) Object.defineProperty(style, 'transform', { value: 'matrix(1, 0, 0, 1, 9, 17)', configurable: true })
      return style
    })
    frames.length = 0
    await stage('评论：乙')
    expect(cancel).toHaveBeenCalledTimes(3)
    expect(frames).toHaveLength(3)
    expect(frames.every(keys => keys[0].transform === 'matrix(1, 0, 0, 1, 9, 17)')).toBe(true)
    expect(screen.getByRole('button', { name: '评论：乙' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('dismisses a pending comment without creating one, including keyboard selection', async () => {
    openPreview()
    fireEvent.change(screen.getByRole('textbox', { name: '大纲思路' }), { target: { value: '一个开场' } })
    await stage('生成大纲方案'); await stage(/方案 01/)
    const outline = screen.getByRole('textbox', { name: '编辑大纲' }) as HTMLTextAreaElement
    outline.setSelectionRange(0, 4)
    fireEvent.keyUp(outline, { key: 'Shift' })
    expect(screen.getByRole('button', { name: '评论' })).toBeInTheDocument()
    expect(screen.queryAllByRole('button', { name: /^评论：/ })).toHaveLength(0)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('button', { name: '评论' })).not.toBeInTheDocument()
    fireEvent.pointerUp(outline)
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('button', { name: '评论' })).not.toBeInTheDocument()
    fireEvent.keyUp(outline, { key: 'Shift' })
    await stage('评论')
    expect(screen.getByRole('button', { name: '评论：一个开场' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('keeps the outline visible during focus and shares the chapter scroller with the title and prose', async () => {
    const view = openPreview()
    await stage('Draft')
    const scroller = screen.getByRole('region', { name: '章节滚动区域' })
    const context = screen.getByRole('region', { name: '大纲与审阅面板' })
    expect(scroller).toContainElement(screen.getByRole('heading', { name: '第10话 吃吃吃' }))
    expect(scroller).toContainElement(context)
    expect(scroller).toContainElement(screen.getByRole('textbox', { name: '章节正文' }))
    const glyphs = screen.getByRole('button', { name: '手动免打扰' }).querySelectorAll('img')
    await stage('手动免打扰')
    expect(context.closest('.studio-context-wrap')).not.toHaveClass('is-hidden')
    expect(context.closest('.studio-context-wrap')).not.toHaveAttribute('inert')
    expect(screen.getByRole('button', { name: '退出免打扰' }).querySelectorAll('img')[0]).toBe(glyphs[0])
    expect(glyphs[0]).toHaveClass('is-outgoing')
    expect(glyphs[1]).toHaveClass('is-current')
    await stage('显示大纲')
    expect(screen.getByRole('button', { name: '显示要求或审阅' })).toHaveClass('is-lit')
    await stage('固定大纲面板')
    expect(context.closest('.studio-context-wrap')).toHaveClass('is-pinned')
    fireEvent.pointerMove(view.container.querySelector('.studio')!, { clientX: 20, clientY: 20 })
    expect(screen.getByRole('button', { name: '退出免打扰' }).style.getPropertyValue('--glow-opacity')).toBe('')
  })

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

  it('retains preview prose automatically and restores archives without losing the current draft', async () => {
    openPreview()
    await stage('Draft')
    fireEvent.change(screen.getByRole('textbox', { name: '章节正文' }), { target: { value: '自动保存的正文' } })
    expect(JSON.parse(localStorage.getItem('guranovel:studio-preview:v1')!)['preview-10'].draft).toBe('自动保存的正文')
    await stage('创建还原点')
    fireEvent.change(screen.getByRole('textbox', { name: '章节正文' }), { target: { value: '存档之后继续写的正文' } })
    await stage('展开章节侧边栏')
    await stage('第10话的存档')
    await stage(/查看第10话存档：手动存档/)
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('自动保存的正文')
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveAttribute('readonly')
    expect(JSON.parse(localStorage.getItem('guranovel:studio-preview:v1')!)['preview-10'].draft).toBe('存档之后继续写的正文')
    await stage('返回当前正文')
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('存档之后继续写的正文')
    await stage(/查看第10话存档：手动存档/)
    await stage('从此存档继续写作')
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('自动保存的正文')
    expect(screen.getByRole('textbox', { name: '章节正文' })).not.toHaveAttribute('readonly')
    const archives = JSON.parse(localStorage.getItem('guranovel:studio-archives:preview:v1')!)['preview-10']
    expect(archives[0]).toMatchObject({ summary: '恢复前自动备份', draft: '存档之后继续写的正文' })
    expect(archives).toHaveLength(2)
    cleanup(); openPreview(); await stage('Draft')
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('自动保存的正文')
    await stage('展开章节侧边栏'); await stage('第10话的存档')
    expect(screen.getByRole('button', { name: /查看第10话存档：恢复前自动备份/ })).toBeInTheDocument()
  })

  it('retains legacy archive prose and comments without rendering malformed requirements', () => {
    localStorage.setItem('guranovel:studio-archives:preview:v1', JSON.stringify({ c: [{
      id: 'old', createdAt: '2026-09-12T00:00:00Z', summary: '旧存档', draft: '原文',
      requirements: { invalid: 'not text' }, feedbackAvailable: true,
      comments: [{ id: 'comment', start: 0, end: 0, quote: '原文', color: commentColors[0], text: '保留评论', orphaned: true }],
    }] }))
    const saved = loadDraftArchives().c[0]
    expect(saved).toMatchObject({ draft: '原文', requirements: undefined, feedbackAvailable: false })
    expect(reanchorComments(saved.comments!, '原文', '添加原文')[0]).toMatchObject({ start: 0, end: 0, quote: '原文', text: '保留评论', orphaned: true })
  })

  it('keeps pinned panels visible in focus, and opens the Setting library via page arrows', async () => {
    const view = openPreview()
    fireEvent.click(screen.getByRole('button', { name: '展开章节侧边栏' }))
    fireEvent.click(screen.getByRole('button', { name: '固定章节目录' }))
    fireEvent.click(screen.getByRole('button', { name: '手动免打扰' }))
    expect(view.container.querySelector('.studio-directory')).toHaveClass('is-pinned')
    expect(view.container.querySelector('.studio-directory')).not.toHaveClass('is-hidden')
    expect(view.container.querySelector('.studio-stats')).toHaveClass('is-hidden')
    fireEvent.keyDown(window, { key: 'Escape' })
    await stage('下一个页面')
    expect(screen.getByLabelText('作品设定')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '关系图谱' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下一个页面' })).toBeInTheDocument()
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
    expect(screen.getByRole('button', { name: '通知，1 条未读' }).parentElement).toHaveClass('has-unread')
    fireEvent.click(screen.getByRole('button', { name: '通知，1 条未读' }))
    expect(screen.getByRole('button', { name: '通知' }).parentElement).not.toHaveClass('has-unread')
    await stage(/第10话审阅完成.*查看本章报告/)
    const block = screen.getByRole('checkbox', { name: '交给 Agent 修改：时间线需要统一' })
    expect(block).toBeChecked(); expect(block).toBeDisabled()
    expect(screen.getByRole('button', { name: '进入读者环节' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '进入读者环节' })).toHaveAttribute('data-icon', 'review')
    expect(screen.getByRole('button', { name: '进入读者环节' })).toHaveAttribute('title', '进入读者环节')
    expect(screen.getByRole('button', { name: '修改所选 1 项并重新审阅' })).toHaveAttribute('data-icon', 'send')
    expect(screen.getByRole('button', { name: '修改所选 1 项并重新审阅' }).textContent).toBe('')
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
    await act(async () => render(<MemoryRouter initialEntries={['/projects/p/studio']}><Routes><Route path="/projects/:projectId/studio/:chapterId?" element={<Studio />} /></Routes></MemoryRouter>))
    expect(screen.getByRole('alert')).toHaveTextContent('没有使用示例数据替代真实章节')
    expect(readDocumentContent).not.toHaveBeenCalled()
  })

  it('saves real drafts before page navigation and reuses the returned version after remount', async () => {
    vi.mocked(getProject).mockResolvedValue({ id: 'p', title: 'Real novel' } as Awaited<ReturnType<typeof getProject>>)
    vi.mocked(listChapters).mockResolvedValue([{ id: 'c', chapter_number: 1, title: 'Chapter', metadata: {}, current_draft_document_id: 'doc' }] as Awaited<ReturnType<typeof listChapters>>)
    vi.mocked(readDocumentContent).mockResolvedValue({ document_id: 'doc', version_id: 'v1', content: 'server text' })
    vi.mocked(writeDocument).mockResolvedValueOnce({ id: 'v2' } as Awaited<ReturnType<typeof writeDocument>>).mockResolvedValueOnce({ id: 'v3' } as Awaited<ReturnType<typeof writeDocument>>)
    await act(async () => render(<MemoryRouter initialEntries={['/projects/p/studio']}><Routes><Route path="/projects/:projectId/studio/:chapterId?" element={<Studio />} /></Routes></MemoryRouter>))
    fireEvent.change(screen.getByRole('textbox', { name: '章节正文' }), { target: { value: 'first change' } })
    await stage('下一个页面')
    expect(writeDocument).toHaveBeenCalledWith('doc', { content: 'first change', expected_current_version_id: 'v1' })
    await stage('上一个页面')
    fireEvent.change(screen.getByRole('textbox', { name: '章节正文' }), { target: { value: 'second change' } })
    await act(async () => vi.advanceTimersByTime(700))
    expect(writeDocument).toHaveBeenLastCalledWith('doc', { content: 'second change', expected_current_version_id: 'v2' })
  })

  it('flushes real draft edits before viewing archives and uses the restored server version for further saves', async () => {
    vi.mocked(getProject).mockResolvedValue({ id: 'p', title: 'Real novel' } as Awaited<ReturnType<typeof getProject>>)
    vi.mocked(listChapters).mockResolvedValue([{ id: 'c', chapter_number: 1, title: 'Chapter', metadata: {}, current_draft_document_id: 'doc' }] as Awaited<ReturnType<typeof listChapters>>)
    vi.mocked(readDocumentContent).mockResolvedValue({ document_id: 'doc', version_id: 'v1', content: 'current draft' })
    vi.mocked(listRestorePoints).mockResolvedValue([{ id: 'point', chapter_id: 'c', document_id: 'doc', version_id: 'old', created_at: '2026-09-10T00:00:00Z', summary: '早期存档' }] as Awaited<ReturnType<typeof listRestorePoints>>)
    vi.mocked(readDocumentVersionContent).mockResolvedValue({ document_id: 'doc', version_id: 'old', content: 'archived draft' })
    const archivedComment = { id: 'old-comment', start: 0, end: 8, quote: 'archived', text: '过去的评论', color: commentColors[0], submitted: true, orphaned: false }
    vi.mocked(readRestorePointFeedback).mockResolvedValue({ point_id: 'point', document_id: 'doc', source_version_id: 'old', available: true, comments: [archivedComment], requirements: '过去的写作要求' })
    vi.mocked(writeDocument).mockResolvedValueOnce({ id: 'v2' } as Awaited<ReturnType<typeof writeDocument>>).mockResolvedValueOnce({ id: 'v4' } as Awaited<ReturnType<typeof writeDocument>>)
    vi.mocked(restorePoint).mockRejectedValueOnce(new ApiError(409, 'conflict', 'Version conflict')).mockResolvedValueOnce({ id: 'v3' } as Awaited<ReturnType<typeof restorePoint>>)
    await act(async () => render(<MemoryRouter initialEntries={['/projects/p/studio']}><Routes><Route path="/projects/:projectId/studio/:chapterId?" element={<Studio />} /></Routes></MemoryRouter>))
    fireEvent.change(screen.getByRole('textbox', { name: '章节正文' }), { target: { value: 'latest unsaved text' } })
    await stage('展开章节侧边栏'); await stage('第1话的存档'); await stage(/查看第1话存档：早期存档/)
    expect(writeDocument).toHaveBeenCalledWith('doc', { content: 'latest unsaved text', expected_current_version_id: 'v1' })
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('archived draft')
    expect(readRestorePointFeedback).toHaveBeenCalledWith('p', 'c', 'point')
    expect(document.querySelector('.studio-outline-mark[data-comment-id="old-comment"]')).toHaveTextContent('archived')
    expect(screen.getByRole('heading', { name: '存档写作要求' })).toBeInTheDocument()
    await stage('查看存档评论：archived')
    expect(screen.getByRole('textbox', { name: '存档评论' })).toHaveValue('过去的评论')
    expect(screen.getByRole('textbox', { name: '存档评论' })).toHaveAttribute('readonly')
    fireEvent.keyDown(screen.getByRole('button', { name: '查看存档评论：archived' }), { key: 'Delete' })
    expect(document.querySelector('.studio-outline-mark[data-comment-id="old-comment"]')).toHaveTextContent('archived')
    await stage('查看存档评论：archived')
    expect(restorePoint).not.toHaveBeenCalled()
    await stage('从此存档继续写作')
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveAttribute('readonly')
    expect(screen.getByText('存档操作失败，当前正文仍保留，请重试。')).toBeInTheDocument()
    await stage('返回当前正文')
    expect(screen.getByRole('textbox', { name: '章节正文' })).toHaveValue('latest unsaved text')
    await stage(/查看第1话存档：早期存档/); await stage('从此存档继续写作')
    expect(restorePoint).toHaveBeenLastCalledWith('p', 'c', 'point', { expected_current_version_id: 'v2', request_id: expect.any(String) })
    expect(screen.getByRole('textbox', { name: '给写作 Agent 的要求' })).toHaveValue('过去的写作要求')
    expect(screen.getByRole('button', { name: '查看已提交评论：archived' })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '章节正文' }), { target: { value: 'continued draft' } })
    await act(async () => vi.advanceTimersByTime(700))
    expect(writeDocument).toHaveBeenLastCalledWith('doc', { content: 'continued draft', expected_current_version_id: 'v3' })
  })
})

describe('draft auto-save', () => {
  it('creates explicit server restore points without another draft write and reuses an uncertain request', async () => {
    vi.mocked(getProject).mockResolvedValue({ id: 'p', title: 'Real novel' } as Awaited<ReturnType<typeof getProject>>)
    vi.mocked(listChapters).mockResolvedValue([{ id: 'c', chapter_number: 1, title: 'Chapter', metadata: {}, current_draft_document_id: 'doc' }] as Awaited<ReturnType<typeof listChapters>>)
    vi.mocked(readDocumentContent).mockResolvedValue({ document_id: 'doc', version_id: 'v1', content: 'current draft' })
    vi.mocked(createRestorePoint).mockRejectedValueOnce(new Error('connection lost')).mockResolvedValueOnce({ id: 'point' } as Awaited<ReturnType<typeof createRestorePoint>>)
    await act(async () => render(<MemoryRouter initialEntries={['/projects/p/studio']}><Routes><Route path="/projects/:projectId/studio/:chapterId?" element={<Studio />} /></Routes></MemoryRouter>))
    await stage('创建还原点')
    expect(screen.getByText('存档操作失败，当前正文仍保留，请重试。')).toBeInTheDocument()
    await stage('创建还原点')
    expect(createRestorePoint).toHaveBeenCalledTimes(2)
    expect(vi.mocked(createRestorePoint).mock.calls[1]).toEqual(vi.mocked(createRestorePoint).mock.calls[0])
    expect(createRestorePoint).toHaveBeenLastCalledWith('p', 'c', { expected_current_version_id: 'v1', request_id: expect.any(String) })
    expect(writeDocument).not.toHaveBeenCalled()
    expect(screen.getByText('已创建存档。')).toBeInTheDocument()
  })

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
