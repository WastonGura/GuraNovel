import { act, cleanup, fireEvent, render as renderView, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRef, type ReactElement } from 'react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import StudioSetting from './StudioSetting'
import { applySettingChanges, buildNoteGraph, emptySettingConversation, noteLinks, readSettingNotes, readSettingWorkspace, settingExampleReply, settingStorageKey, type SettingNote } from './settingNotes'

beforeEach(() => { localStorage.clear(); vi.useFakeTimers(); HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') } })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })
const seed: SettingNote[] = [
  { id: 'a', title: '甲', body: '住在 [[海滨城]]，路过 [[旧街]]。再次 [[海滨城]]。', category: 'setting' },
  { id: 'b', title: '海滨城', body: '[[甲]] 的故乡。', category: 'world' },
]
const render = (ui: ReactElement) => renderView(ui, { wrapper: MemoryRouter })
function marquee(list: HTMLElement, first: number, last = first) {
  const rows = [...list.querySelectorAll<HTMLElement>('[data-note-id]')]
  vi.spyOn(list, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 0, 200, 300))
  rows.forEach((row, index) => vi.spyOn(row, 'getBoundingClientRect').mockReturnValue(new DOMRect(8, 20 + index * 40, 175, 32)))
  fireEvent(list, new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 195, clientY: 21 + first * 40 }))
  fireEvent(document, new MouseEvent('pointermove', { bubbles: true, clientX: 10, clientY: 50 + last * 40 }))
  fireEvent(document, new MouseEvent('pointerup', { bubbles: true }))
  fireEvent.click(list)
}

describe('setting notes and graph', () => {
  it('copies the context-menu target or selected names and reports clipboard failures without changing notes', async () => {
    const original = Object.getOwnPropertyDescriptor(navigator, 'clipboard'), writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    try {
      localStorage.setItem(settingStorageKey, JSON.stringify([seed[0], { ...seed[1], category: 'setting' }]))
      render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
      const list = screen.getByLabelText('设定条目', { exact: true })
      fireEvent.contextMenu(within(list).getByRole('button', { name: '海滨城' }))
      await act(async () => { fireEvent.click(screen.getByRole('menuitem', { name: '复制条目名称' })) })
      expect(writeText).toHaveBeenLastCalledWith('海滨城')
      expect(screen.getByRole('menuitem', { name: '已复制名称' })).toBeInTheDocument()
      fireEvent.keyDown(screen.getByRole('menuitem', { name: '已复制名称' }), { key: 'Escape' })
      marquee(list, 0, 1)
      fireEvent.contextMenu(within(list).getByRole('button', { name: '甲' }))
      writeText.mockRejectedValueOnce(new Error('Clipboard denied'))
      await act(async () => { fireEvent.click(screen.getByRole('menuitem', { name: '复制所选条目名称' })) })
      expect(writeText).toHaveBeenLastCalledWith('甲\n海滨城')
      expect(screen.getByRole('menuitem', { name: '复制失败，请重试' })).toBeInTheDocument()
      expect(readSettingNotes()).toHaveLength(2)
    } finally {
      if (original) Object.defineProperty(navigator, 'clipboard', original)
      else Reflect.deleteProperty(navigator, 'clipboard')
    }
  })

  it('moves from the first completion to search, clamps the last item, and clears selection on blank clicks', () => {
    localStorage.setItem(settingStorageKey, JSON.stringify([...seed, { id: 'c', title: '乙', body: '', category: 'setting' }]))
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    const list = screen.getByLabelText('设定条目', { exact: true })
    fireEvent.click(within(list).getByRole('button', { name: '甲' }))
    expect(within(list).getByRole('button', { name: '甲' })).toHaveAttribute('aria-pressed', 'false')
    marquee(list, 0)
    expect(within(list).getByRole('button', { name: '甲' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(list)
    expect(within(list).getByRole('button', { name: '甲' })).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    const editor = screen.getByRole('textbox', { name: '设定正文' })
    fireEvent.change(editor, { target: { value: '@' } })
    fireEvent.keyDown(editor, { key: 'ArrowUp' })
    const search = screen.getByRole('textbox', { name: '检索引用条目' })
    expect(search).toHaveFocus()
    fireEvent.keyDown(search, { key: 'ArrowDown' })
    expect(screen.getByRole('option', { name: '海滨城' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(search, { key: 'ArrowDown' }); fireEvent.keyDown(search, { key: 'ArrowDown' })
    expect(screen.getByRole('option', { name: '乙' })).toHaveAttribute('aria-selected', 'true')
    expect(within(screen.getByRole('listbox')).queryByRole('button', { name: '关闭' })).not.toBeInTheDocument()
    fireEvent.keyDown(search, { key: 'Enter' })
    expect(readSettingNotes()[0].body).toBe('[[乙]]')
    fireEvent.click(screen.getByRole('button', { name: /引用条目 @/ }))
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('restores note navigation through browser history and saves edits before returning', () => {
    function HistoryControls() {
      const navigate = useNavigate()
      return <><button onClick={() => navigate(-1)}>历史后退</button><button onClick={() => navigate(1)}>历史前进</button></>
    }
    localStorage.setItem(settingStorageKey, JSON.stringify(seed))
    render(<><StudioSetting preview hidden={false} onTyping={() => {}} /><HistoryControls /></>)
    fireEvent.click(within(document.querySelector('.setting-note-body') as HTMLElement).getAllByRole('button', { name: '海滨城' })[0])
    expect(screen.getByRole('heading', { name: '海滨城' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByRole('textbox', { name: '设定正文' }), { target: { value: '保留编辑内容。' } })
    fireEvent.click(screen.getByRole('button', { name: '历史后退' }))
    expect(screen.getByRole('heading', { name: '甲' })).toBeInTheDocument()
    expect(readSettingNotes()[1].body).toBe('保留编辑内容。')
    fireEvent.click(screen.getByRole('button', { name: '历史前进' }))
    expect(screen.getByRole('heading', { name: '海滨城' })).toBeInTheDocument()
  })

  it('annotates a reading selection across wiki links and inserts a reference from keyboard completion', () => {
    localStorage.setItem(settingStorageKey, JSON.stringify(seed))
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    const reading = document.querySelector('.studio-reading-outline')!
    const first = reading.querySelector('[data-source-start]')!.firstChild!
    const last = reading.querySelector('.setting-inline-link')!.nextSibling!.firstChild!
    const range = document.createRange(); range.setStart(first, 1); range.setEnd(last, 2)
    const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range)
    fireEvent.pointerUp(reading)
    fireEvent.click(screen.getByRole('button', { name: '评论' }))
    fireEvent.change(screen.getByRole('textbox', { name: '给 Agent 的修改意见' }), { target: { value: '说明这个地点' } })
    expect(readSettingNotes()[0].comments?.[0]).toMatchObject({ quote: '在 [[海滨城]]，路', start: 1, text: '说明这个地点' })
    selection.removeAllRanges()
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    const editor = screen.getByRole('textbox', { name: '设定正文' }) as HTMLTextAreaElement
    fireEvent.change(editor, { target: { value: `${seed[0].body}@海` } })
    expect(screen.getByRole('option', { name: '海滨城' })).toBeInTheDocument()
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(readSettingNotes()[0].body).toBe(`${seed[0].body}[[海滨城]]`)
    expect(readSettingNotes()[0].comments?.[0].text).toBe('说明这个地点')
  })

  it('supports multi-selection, keyboard ordering and reversible deletion without removing references', () => {
    const notes = [...seed, { id: 'c', title: '乙', body: '认识 [[甲]]。', category: 'setting' }]
    localStorage.setItem(settingStorageKey, JSON.stringify(notes))
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    const listElement = screen.getByLabelText('设定条目', { exact: true }), list = within(listElement)
    fireEvent.click(list.getByRole('button', { name: '乙' }))
    expect(list.getByRole('button', { name: '乙' })).toHaveAttribute('aria-pressed', 'false')
    marquee(listElement, 1)
    fireEvent.keyDown(list.getByRole('button', { name: '乙' }), { altKey: true, key: 'ArrowUp' })
    expect(readSettingNotes().filter(note => note.category === 'setting').map(note => note.id)).toEqual(['c', 'a'])
    marquee(listElement, 0, 1)
    expect(screen.queryByRole('button', { name: '删除所选' })).not.toBeInTheDocument()
    fireEvent.contextMenu(list.getByRole('button', { name: '甲' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '删除 2 个条目' }))
    expect(readSettingNotes()).toHaveLength(3)
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(readSettingNotes()).toHaveLength(3)
    fireEvent.keyDown(list.getByRole('button', { name: '甲' }), { key: 'Delete' })
    expect(readSettingNotes()).toHaveLength(3)
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(readSettingNotes().map(note => note.id)).toEqual(['b'])
    expect(readSettingNotes()[0].body).toBe('[[甲]] 的故乡。')
    fireEvent.click(screen.getByRole('button', { name: '撤销删除' }))
    expect(readSettingNotes().map(note => note.id)).toEqual(['c', 'b', 'a'])
  })

  it('right-clicks an unselected item without deleting the earlier selection, and Esc clears selection without leaving the list', () => {
    localStorage.setItem(settingStorageKey, JSON.stringify([seed[0], { ...seed[1], category: 'setting' }]))
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    const list = within(screen.getByLabelText('设定条目', { exact: true }))
    fireEvent.click(list.getByRole('button', { name: '甲' }))
    fireEvent.contextMenu(list.getByRole('button', { name: '海滨城' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '删除条目' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('海滨城')
    fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }))
    expect(readSettingNotes()).toHaveLength(2)
    fireEvent.keyDown(list.getByRole('button', { name: '海滨城' }), { key: 'Escape' })
    expect(list.getByRole('button', { name: '海滨城' })).toHaveAttribute('aria-pressed', 'false')
  })
  it('lists outgoing references once, includes unresolved links and excludes incoming-only notes', () => {
    localStorage.setItem(settingStorageKey, JSON.stringify([...seed, { id: 'c', title: '乙', category: 'setting', body: '认识 [[甲]]。' }]))
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    const references = within(screen.getByRole('region', { name: '引用条目' }))
    expect(references.getAllByRole('button')).toHaveLength(2)
    expect(references.getByRole('button', { name: /海滨城/ })).toBeInTheDocument()
    expect(references.queryByRole('button', { name: /乙/ })).not.toBeInTheDocument()
    fireEvent.click(references.getByRole('button', { name: /旧街/ }))
    expect(screen.getByRole('textbox', { name: '设定标题' })).toHaveValue('旧街')
  })

  it('stages anchored comments across remounts, allows additions and sends them together with the existing draft', () => {
    localStorage.setItem(settingStorageKey, JSON.stringify({ notes: seed, conversation: { ...emptySettingConversation(), draft: '另一段还没发送的想法' } }))
    const view = render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    const editor = screen.getByRole('textbox', { name: '设定正文' }) as HTMLTextAreaElement
    editor.focus(); editor.setSelectionRange(3, 6); fireEvent.pointerUp(editor)
    expect(readSettingNotes()[0].comments).toBeUndefined()
    fireEvent.click(screen.getByRole('button', { name: '评论' }))
    fireEvent.change(screen.getByRole('textbox', { name: '给 Agent 的修改意见' }), { target: { value: '解释这个地方与角色的联系' } })
    const original = editor.value
    fireEvent.change(editor, { target: { value: `新增。${original}` } })
    expect(readSettingNotes()[0].comments?.[0]).toMatchObject({ start: 6, end: 9, quote: original.slice(3, 6), text: '解释这个地方与角色的联系' })
    view.unmount()
    const stagedView = render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    expect(screen.getByRole('textbox', { name: '给 Agent 的修改意见' })).toHaveValue('解释这个地方与角色的联系')
    fireEvent.click(screen.getByRole('button', { name: '暂存评论并继续讨论' }))
    const workspace = readSettingWorkspace()
    expect(workspace.notes[0].comments?.[0].submitted).not.toBe(true)
    expect(workspace.conversation.contextId).toBe('a')
    expect(workspace.conversation.messages).toHaveLength(0)
    expect(workspace.conversation.stagedComments).toHaveLength(1)
    expect(screen.getByRole('textbox', { name: '给设定 Agent 的消息' })).toHaveValue('另一段还没发送的想法')
    stagedView.unmount()
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '返回设定对话' }))
    fireEvent.click(screen.getByText('1 条评论'))
    fireEvent.change(screen.getByRole('textbox', { name: '暂存评论 1 的修改意见' }), { target: { value: '解释这个地方与角色的联系，并补充他的童年' } })
    fireEvent.click(screen.getByRole('button', { name: '发送设定消息（仅预览）' }))
    const sent = readSettingWorkspace()
    expect(sent.conversation.stagedComments).toEqual([])
    expect(sent.notes[0].comments?.[0].submitted).toBe(true)
    expect(sent.conversation.messages).toHaveLength(1)
    expect(sent.conversation.messages[0].text).toContain(`引用：${original.slice(3, 6)}`)
    expect(sent.conversation.messages[0].text).toContain('并补充他的童年')
    expect(sent.conversation.messages[0].text).toContain('另一段还没发送的想法')
    fireEvent.click(screen.getByRole('button', { name: '正在讨论 · 甲' }))
    const dot = within(screen.getByRole('group', { name: '条目已发送评论' })).getByRole('button')
    fireEvent.keyDown(dot, { key: 'Delete' })
    expect(readSettingNotes()[0].comments).toEqual([])
    expect(readSettingWorkspace().conversation.messages).toHaveLength(1)
  })

  it('keeps staged comments independent across notes and returns one to its editor without deleting it', () => {
    const notes = seed.map(note => ({ ...note, comments: [{ id: note.id, start: 0, end: 1, quote: note.body[0], text: '补充细节', color: '#8d9bff' }] }))
    localStorage.setItem(settingStorageKey, JSON.stringify({ notes, conversation: { ...emptySettingConversation(), stagedComments: notes.map(note => ({ noteId: note.id, commentId: note.id })) } }))
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '返回设定对话' }))
    fireEvent.click(screen.getByText('2 条评论'))
    fireEvent.click(screen.getByRole('button', { name: `移回条目：${notes[0].body[0]}` }))
    expect(readSettingWorkspace().conversation.stagedComments).toEqual([{ noteId: 'b', commentId: 'b' }])
    expect(readSettingNotes()[0].comments).toHaveLength(1)
    fireEvent.change(screen.getByRole('textbox', { name: '暂存评论 1 的修改意见' }), { target: { value: '' } })
    expect(screen.getByRole('button', { name: '发送设定消息（仅预览）' })).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox', { name: '暂存评论 1 的修改意见' }), { target: { value: '城市有多大？' } })
    fireEvent.click(screen.getByRole('button', { name: '发送设定消息（仅预览）' }))
    expect(readSettingWorkspace().conversation.messages[0].text).toContain('城市有多大？')
    expect(readSettingNotes()[0].comments?.[0].submitted).not.toBe(true)
  })

  it('preserves staged comments and the draft on send storage failure, and rejects corrupt stage references', () => {
    const notes = [{ ...seed[0], comments: [{ id: 'c', start: 0, end: 1, quote: '住', text: '补充', color: '#8d9bff' }] }]
    const conversation = { ...emptySettingConversation(), draft: '补充说明', stagedComments: [{ noteId: 'a', commentId: 'c' }] }
    localStorage.setItem(settingStorageKey, JSON.stringify({ notes, conversation }))
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '返回设定对话' }))
    const fail = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    fireEvent.click(screen.getByRole('button', { name: '发送设定消息（仅预览）' }))
    expect(screen.getByRole('textbox', { name: '给设定 Agent 的消息' })).toHaveValue('补充说明')
    expect(screen.getByText('1 条评论')).toBeInTheDocument()
    expect(readSettingWorkspace().conversation.messages).toHaveLength(0)
    expect(screen.getByRole('alert')).toHaveTextContent('本机保存失败')
    fail.mockRestore()
    localStorage.setItem(settingStorageKey, JSON.stringify({ notes, conversation: { ...conversation, stagedComments: [{ noteId: 'a', commentId: 'missing' }] } }))
    expect(() => readSettingWorkspace()).toThrow('Invalid staged comments')
  })

  it('retains comments when applying proposals and rejects corrupt comment data without rewriting storage', () => {
    const notes: SettingNote[] = [{ id: 'a', title: '甲', category: 'setting', body: '甲住在这里', comments: [{ id: 'comment', start: 1, end: 3, quote: '住在', color: '#8d9bff', text: '具体哪里？' }] }]
    const result = applySettingChanges(notes, [{ id: 'proposal', noteId: 'a', title: '甲', category: 'setting', body: '新增。甲住在这里', before: notes[0], status: 'pending' }])
    expect(result[0].comments?.[0]).toMatchObject({ start: 4, end: 6, text: '具体哪里？' })
    expect(notes[0].comments?.[0].start).toBe(1)
    const raw = JSON.stringify([{ ...notes[0], comments: [{ ...notes[0].comments![0], end: 999 }] }])
    localStorage.setItem(settingStorageKey, raw)
    expect(() => readSettingWorkspace()).toThrow('Invalid setting notes')
    expect(localStorage.getItem(settingStorageKey)).toBe(raw)
  })

  it('keeps submitted comment controls attached when switching directly between annotated notes', () => {
    localStorage.setItem(settingStorageKey, JSON.stringify(seed.map(note => ({ ...note, category: 'setting', comments: [{ id: note.id, start: 0, end: 1, quote: note.body[0], text: '补充细节', color: '#8d9bff', submitted: true }] }))))
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(within(screen.getByLabelText('设定条目')).getByRole('button', { name: '海滨城' }))
    fireEvent.keyDown(within(screen.getByRole('group', { name: '条目已发送评论' })).getByRole('button'), { key: 'Delete' })
    expect(readSettingNotes()[0].comments).toHaveLength(1)
    expect(readSettingNotes()[1].comments).toEqual([])
  })

  it('deduplicates links and reciprocal edges, keeps unresolved targets and rejects ambiguous titles', () => {
    expect(noteLinks('[[甲]] [[ 甲 ]] [[乙]] [[跨\n行]] [[]]')).toEqual(['甲', '乙'])
    const graph = buildNoteGraph(seed)
    expect(graph.edges).toEqual([{ source: 'a', target: 'b' }, { source: 'a', target: 'missing:旧街' }])
    expect(graph.nodes.find(node => node.id === 'missing:旧街')?.missing).toBe(true)
    expect(buildNoteGraph([...seed, { ...seed[1], id: 'duplicate' }]).edges).toContainEqual({ source: 'a', target: 'missing:海滨城' })
  })

  it('edits, renames references and preserves notes across remounts without a server claim', () => {
    localStorage.setItem(settingStorageKey, JSON.stringify(seed))
    const view = render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(within(screen.getByLabelText('设定分类')).getByRole('button', { name: '世界观' }))
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: '海风城' } })
    fireEvent.blur(screen.getByLabelText('设定标题'))
    fireEvent.change(screen.getByLabelText('设定正文'), { target: { value: '新的内容，提到 [[甲]]。' } })
    fireEvent.click(screen.getByRole('button', { name: '阅读' }))
    const saved = readSettingNotes()
    expect(saved[0].body).toBe('住在 [[海风城]]，路过 [[旧街]]。再次 [[海风城]]。')
    expect(saved[1].body).toContain('新的内容')
    expect(screen.getByText('已保存到本机 · 交互预览')).toBeInTheDocument()
    view.unmount()
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getAllByRole('button', { name: '海风城' })[0])
    expect(screen.getByRole('heading', { name: '海风城' })).toBeInTheDocument()
    expect(within(screen.getByLabelText('引用条目')).getByRole('button', { name: /甲/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: '海雾城' } })
    fireEvent.click(screen.getByRole('button', { name: '手动新建条目' }))
    const afterCreate = readSettingNotes()
    expect(afterCreate).toHaveLength(3)
    expect(afterCreate.find(note => note.id === 'b')?.title).toBe('海雾城')
    expect(afterCreate[0].body).toContain('[[海雾城]]')
  })

  it('filters by body and opens graph notes and missing references by keyboard', () => {
    localStorage.setItem(settingStorageKey, JSON.stringify(seed))
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '搜索内容' }))
    fireEvent.change(screen.getByLabelText('搜索设定内容'), { target: { value: '路过' } })
    expect(within(screen.getByLabelText('搜索结果')).getByRole('button', { name: /甲/ })).toBeInTheDocument()
    expect(screen.getByLabelText('搜索设定内容').closest('.setting-main')).not.toBeNull()
    fireEvent.change(screen.getByLabelText('搜索设定内容'), { target: { value: '故乡' } })
    expect(within(screen.getByLabelText('搜索结果')).getByRole('button', { name: /海滨城/ })).toBeInTheDocument()
    expect(within(screen.getByLabelText('设定条目')).getByRole('button', { name: '甲' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('搜索设定内容'), { target: { value: '不存在的条目' } })
    expect(screen.getByRole('status')).toHaveTextContent('没有找到相关条目')
    expect(within(screen.getByLabelText('搜索结果')).queryByRole('button')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '关系图谱' }))
    fireEvent.click(screen.getByRole('button', { name: '放大图谱' }))
    expect(screen.getByRole('button', { name: '图谱归位' })).toHaveTextContent('120%')
    fireEvent.keyDown(screen.getByRole('group', { name: '关系图谱画布' }), { key: 'Home' })
    expect(screen.getByRole('button', { name: '图谱归位' })).toHaveTextContent('100%')
    fireEvent.keyDown(screen.getByRole('button', { name: '创建引用条目：旧街' }), { key: 'Enter' })
    expect(screen.getByLabelText('设定标题')).toHaveValue('旧街')
    expect(readSettingNotes().some(note => note.title === '旧街')).toBe(true)
  })

  it('reveals the sidebar on demand, preserves pinned visibility and returns keyboard focus on Escape', () => {
    const view = render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    const sidebar = screen.getByLabelText('设定侧边栏'), trigger = screen.getByRole('button', { name: '展开设定侧边栏' })
    expect(sidebar).toHaveAttribute('inert')
    fireEvent.pointerEnter(trigger)
    expect(sidebar).not.toHaveAttribute('inert')
    fireEvent.pointerLeave(trigger.parentElement!)
    expect(sidebar).toHaveAttribute('inert')
    fireEvent.focus(trigger)
    fireEvent.click(screen.getByRole('button', { name: '固定设定侧边栏' }))
    fireEvent.pointerLeave(trigger.parentElement!)
    view.rerender(<StudioSetting preview hidden onTyping={() => {}} />)
    expect(sidebar).not.toHaveAttribute('inert')
    fireEvent.click(screen.getByRole('button', { name: '取消固定设定侧边栏' }))
    expect(sidebar).toHaveAttribute('inert')
    view.rerender(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getByRole('button', { name: '固定设定侧边栏' }), { key: 'Escape' })
    expect(sidebar).toHaveAttribute('inert')
    expect(trigger).toHaveFocus()
  })

  it('does not allow conflicting titles or leaving unsaved local data', async () => {
    localStorage.setItem(settingStorageKey, JSON.stringify(seed))
    const ref = createRef<{ flush: () => Promise<boolean> }>()
    render(<StudioSetting ref={ref} preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: '海滨城' } })
    fireEvent.click(screen.getByRole('button', { name: '阅读' }))
    expect(screen.getByRole('alert')).toHaveTextContent('已有同名条目')
    expect(screen.getByLabelText('设定标题')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('设定标题'), { target: { value: '甲' } })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    fireEvent.change(screen.getByLabelText('设定正文'), { target: { value: '不能丢掉的本地文字' } })
    let result = true
    await act(async () => { result = await ref.current!.flush() })
    expect(result).toBe(false)
    expect(screen.getByLabelText('设定正文')).toHaveValue('不能丢掉的本地文字')
  })

  it('keeps invalid stored data and real project data untouched', () => {
    localStorage.setItem(settingStorageKey, '{broken')
    const view = render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    expect(screen.getByRole('alert')).toHaveTextContent('原始数据已保留')
    expect(screen.getAllByRole('button', { name: '新建内容' }).every(button => button.hasAttribute('disabled'))).toBe(true)
    expect(localStorage.getItem(settingStorageKey)).toBe('{broken')
    view.unmount()
    const invalidConversation = JSON.stringify({ notes: seed, conversation: { ...emptySettingConversation(), messages: [{ id: 'bad', role: 'assistant', text: '坏数据', changes: null }] } })
    localStorage.setItem(settingStorageKey, invalidConversation)
    expect(() => readSettingWorkspace()).toThrow('Invalid setting conversation')
    expect(localStorage.getItem(settingStorageKey)).toBe(invalidConversation)
    render(<StudioSetting preview={false} hidden={false} onTyping={() => {}} />)
    expect(screen.getByText('设定接口尚未接入')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '甲' })).toBeNull()
  })

  it('keeps one ongoing conversation across individual and batch acceptance, viewing notes and remounting', () => {
    localStorage.setItem(settingStorageKey, JSON.stringify(seed))
    const view = render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '新建内容' }))
    expect(readSettingNotes()).toHaveLength(2)
    fireEvent.change(screen.getByLabelText('给设定 Agent 的消息'), { target: { value: '聊聊地下组织' } })
    fireEvent.keyDown(screen.getByLabelText('给设定 Agent 的消息'), { key: 'Enter', keyCode: 229, isComposing: true })
    expect(readSettingWorkspace().conversation.messages).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: '发送设定消息（仅预览）' }))
    expect(readSettingWorkspace().conversation.messages[0].text).toBe('聊聊地下组织')
    fireEvent.click(screen.getByRole('button', { name: '示例回复' }))
    expect(readSettingNotes()).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: '查看提案：灰潮会' }))
    fireEvent.change(screen.getByLabelText('提案正文：灰潮会'), { target: { value: '调整后的组织，活动于 [[旧港仓库]]。' } })
    fireEvent.click(screen.getAllByRole('button', { name: '接受此条' })[0])
    expect(readSettingNotes().find(note => note.title === '灰潮会')?.body).toContain('调整后的组织')
    fireEvent.change(screen.getByLabelText('给设定 Agent 的消息'), { target: { value: '下一轮还没发送的想法' } })
    fireEvent.click(screen.getByRole('button', { name: '查看条目' }))
    expect(screen.getByRole('heading', { name: '灰潮会' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回设定对话' }))
    expect(screen.getByLabelText('给设定 Agent 的消息')).toHaveValue('下一轮还没发送的想法')
    fireEvent.click(screen.getByRole('button', { name: '接受本批' }))
    expect(readSettingNotes()).toHaveLength(4)
    expect(screen.getByRole('region', { name: '设定 Agent 会话' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '示例回复' }))
    expect(readSettingWorkspace().conversation.messages).toHaveLength(3)
    fireEvent.click(screen.getByRole('button', { name: '接受本批' }))
    expect(readSettingNotes()).toHaveLength(6)
    view.unmount()
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '返回设定对话' }))
    expect(screen.getByLabelText('给设定 Agent 的消息')).toHaveValue('下一轮还没发送的想法')
    expect(screen.getAllByText('已接受')).toHaveLength(5)
    expect(readSettingNotes()).toHaveLength(6)
  })

  it('preserves manual notes when discussing them and applies no partial batch on a conflict', () => {
    localStorage.setItem(settingStorageKey, JSON.stringify(seed))
    render(<StudioSetting preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText('设定正文'), { target: { value: '用户亲自写的设定' } })
    fireEvent.click(screen.getByRole('button', { name: '交给 Agent 讨论' }))
    expect(readSettingWorkspace().conversation.contextId).toBe('a')
    fireEvent.click(screen.getByRole('button', { name: '示例回复' }))
    expect(readSettingNotes()[0].body).toBe('用户亲自写的设定')
    const proposal = readSettingWorkspace().conversation.messages[0].changes[0]
    expect(proposal.before?.body).toBe('用户亲自写的设定')
    const batch = settingExampleReply(seed, emptySettingConversation()).changes
    const edited = seed.map(note => note.id === 'b' ? { ...note, body: '后来改过的城市' } : note)
    expect(() => applySettingChanges(edited, batch)).toThrow('已发生变化')
    expect(edited).toHaveLength(2)
    expect(edited[1].body).toBe('后来改过的城市')
  })

  it('saves accepted changes and conversation status together, preserving the previous snapshot on quota failure', async () => {
    localStorage.setItem(settingStorageKey, JSON.stringify(seed))
    const ref = createRef<{ flush: () => Promise<boolean> }>()
    render(<StudioSetting ref={ref} preview hidden={false} onTyping={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '新建内容' }))
    fireEvent.click(screen.getByRole('button', { name: '示例回复' }))
    const before = localStorage.getItem(settingStorageKey)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    fireEvent.click(screen.getByRole('button', { name: '接受本批' }))
    expect(localStorage.getItem(settingStorageKey)).toBe(before)
    expect(screen.getByRole('alert')).toHaveTextContent('本机保存失败')
    let result = true
    await act(async () => { result = await ref.current!.flush() })
    expect(result).toBe(false)
  })

  it('renders proposal metadata, reason, sourceTask, and cross-novel impact warning', () => {
    const chatWithProposal = {
      ...emptySettingConversation(),
      messages: [
        {
          id: 'msg-1',
          role: 'assistant' as const,
          text: '设定 Agent 发现需要补充设定：',
          createdAt: Date.now(),
          changes: [
            {
              id: 'prop-1',
              noteId: 'a',
              title: '甲',
              body: '甲获得了新的法宝。',
              category: 'setting' as const,
              before: seed[0],
              status: 'pending' as const,
              baseVersionId: 'ver-base-001',
              reason: '第2章剧情需要法宝线索',
              sourceTask: {
                novelTitle: '天穹之剑',
                agentRole: 'lore_agent',
                chapterId: 'chap-101',
              },
            },
          ],
        },
      ],
    }
    localStorage.setItem(settingStorageKey, JSON.stringify({ notes: seed, conversation: chatWithProposal }))

    render(
      <StudioSetting
        preview
        hidden={false}
        onTyping={() => {}}
        referencingProjects={[
          { id: 'p1', title: '天穹之剑' },
          { id: 'p2', title: '星穹旅人' },
        ]}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: '返回设定对话' }))

    // Expand proposal
    fireEvent.click(screen.getByRole('button', { name: '查看提案：甲' }))

    // Verify metadata displayed
    expect(screen.getByText('第2章剧情需要法宝线索')).toBeInTheDocument()
    expect(screen.getByText(/天穹之剑 · lore_agent/)).toBeInTheDocument()
    expect(screen.getByText('ver-base')).toBeInTheDocument()
    expect(screen.getByText('共享设定变更将影响关联小说后续任务：天穹之剑、星穹旅人')).toBeInTheDocument()
  })

  it('handles OCC 409 conflict when accepting proposal with stale baseVersionId', async () => {
    const onSaveNoteContent = vi.fn().mockResolvedValue('conflict')
    const chatWithProposal = {
      ...emptySettingConversation(),
      messages: [
        {
          id: 'msg-1',
          role: 'assistant' as const,
          text: '提案',
          createdAt: Date.now(),
          changes: [
            {
              id: 'prop-1',
              noteId: 'a',
              title: '甲',
              body: '新正文',
              category: 'setting' as const,
              before: seed[0],
              status: 'pending' as const,
              baseVersionId: 'ver-stale-001',
            },
          ],
        },
      ],
    }
    localStorage.setItem(settingStorageKey, JSON.stringify({ notes: seed, conversation: chatWithProposal }))

    render(
      <StudioSetting
        preview={false}
        hidden={false}
        backendNotes={seed}
        initialConversation={chatWithProposal}
        initialView={{ mode: 'chat' }}
        onTyping={() => {}}
        onSaveNoteContent={onSaveNoteContent}
      />
    )

    // Accept proposal
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '接受此条' }))
    })

    // Expect onSaveNoteContent called with baseVersionId
    expect(onSaveNoteContent).toHaveBeenCalledWith('a', '新正文', 'ver-stale-001')

    // Conflict error message displayed
    expect(screen.getByRole('alert')).toHaveTextContent('「甲」已发生变化，请重新生成或人工合并。')
    // Proposal remains pending (not accepted)
    expect(screen.getByRole('button', { name: '接受此条' })).toBeInTheDocument()
  })

  it('successfully updates note and accepts proposal with matching baseVersionId', async () => {
    const onSaveNoteContent = vi.fn().mockResolvedValue({ versionId: 'ver-new-002' })
    const chatWithProposal = {
      ...emptySettingConversation(),
      messages: [
        {
          id: 'msg-1',
          role: 'assistant' as const,
          text: '提案',
          createdAt: Date.now(),
          changes: [
            {
              id: 'prop-1',
              noteId: 'a',
              title: '甲',
              body: '更新后的甲设定',
              category: 'setting' as const,
              before: seed[0],
              status: 'pending' as const,
              baseVersionId: 'ver-base-001',
            },
          ],
        },
      ],
    }
    localStorage.setItem(settingStorageKey, JSON.stringify({ notes: seed, conversation: chatWithProposal }))

    render(
      <StudioSetting
        preview={false}
        hidden={false}
        backendNotes={seed}
        initialConversation={chatWithProposal}
        initialView={{ mode: 'chat' }}
        onTyping={() => {}}
        onSaveNoteContent={onSaveNoteContent}
      />
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '接受此条' }))
    })

    expect(onSaveNoteContent).toHaveBeenCalledWith('a', '更新后的甲设定', 'ver-base-001')
    expect(screen.getByRole('button', { name: '查看条目' })).toBeInTheDocument()
    expect(screen.getByText('已接受')).toBeInTheDocument()
  })

  it('dismisses a proposal without mutating notes or calling onSaveNoteContent', async () => {
    const onSaveNoteContent = vi.fn()
    const chatWithProposal = {
      ...emptySettingConversation(),
      messages: [
        {
          id: 'msg-1',
          role: 'assistant' as const,
          text: '提案',
          createdAt: Date.now(),
          changes: [
            {
              id: 'prop-1',
              noteId: 'a',
              title: '甲',
              body: '不合适的新正文',
              category: 'setting' as const,
              before: seed[0],
              status: 'pending' as const,
              baseVersionId: 'ver-base-001',
            },
          ],
        },
      ],
    }
    localStorage.setItem(settingStorageKey, JSON.stringify({ notes: seed, conversation: chatWithProposal }))

    render(
      <StudioSetting
        preview={false}
        hidden={false}
        backendNotes={seed}
        initialConversation={chatWithProposal}
        initialView={{ mode: 'chat' }}
        onTyping={() => {}}
        onSaveNoteContent={onSaveNoteContent}
      />
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '暂不采用' }))
    })

    expect(onSaveNoteContent).not.toHaveBeenCalled()
    expect(screen.getByText('未采用')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '接受此条' })).not.toBeInTheDocument()
  })
})

