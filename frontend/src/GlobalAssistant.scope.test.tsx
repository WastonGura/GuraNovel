import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import GlobalAssistant, { type GlobalAssistantProps } from './GlobalAssistant'
import type { AssistantConversation, AssistantPageContext } from './api/studioAssistantClient'
import { activeAssistantConversationKey } from './assistantRecovery'
import { resetAssistantDeliveries } from './assistantDeliveries'

const projectA = '11111111-1111-4111-8111-111111111111'
const chapterA = '22222222-2222-4222-8222-222222222222'
const date = '2026-10-22T00:00:00Z'
const conversation = (id = 'A', messages: AssistantConversation['messages'] = []): AssistantConversation => ({ id, title: messages[0]?.content || '新对话', project_id: null, setting_collection_id: null, messages, created_at: date, updated_at: date })
const response = (value: unknown = conversation(), status = 200) => new Response(JSON.stringify(value), { status })
const draftKey = (id = 'A') => `guranovel_assistant_draft:conversation:${id}`
const pendingKey = (id = 'A') => `guranovel_assistant_pending:conversation:${id}`
const view = (props: GlobalAssistantProps = {}, path = '/') => <MemoryRouter initialEntries={[path]}><GlobalAssistant {...props} /><button>继续编辑</button></MemoryRouter>
async function open() { await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Gura' })) }) }
async function send(text?: string) {
  if (text !== undefined) fireEvent.change(screen.getByRole('textbox'), { target: { value: text } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '发送给 Gura' })) })
}
function server() {
  const sessions = new Map<string, AssistantConversation>([['A', conversation()]])
  const sent: { id: string; content: string; client_message_id: string; context: AssistantPageContext }[] = []
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    const path = String(url)
    if (path.includes('legacy-conversations')) return response([])
    const id = path.split('/conversations/')[1]?.split('/')[0]
    if (path.endsWith('/messages')) {
      const payload = JSON.parse(String(init?.body))
      sent.push({ id, ...payload })
      const current = sessions.get(id!)!
      current.messages.push({ id: `${sent.length}-user`, role: 'user', content: payload.content, created_at: date, tool_calls: [{ client_message_id: payload.client_message_id, context: payload.context, exchange_complete: true }] },
        { id: `${sent.length}-assistant`, role: 'assistant', content: `${payload.content}的回复`, created_at: date })
      current.title = current.messages[0].content
      return response(current)
    }
    if (id) return response(sessions.get(id))
    if (init?.method === 'POST') {
      if (JSON.parse(String(init.body)).new_conversation) { const next = conversation(JSON.parse(String(init.body)).client_conversation_id); sessions.set(next.id, next); return response(next) }
      return response(sessions.get('A'))
    }
    return response([...sessions.values()].map(item => ({ id: item.id, title: item.title, created_at: date, updated_at: date, message_count: item.messages.length, preview: item.messages.at(-1)?.content || '' })))
  })
  return { fetch, sessions, sent }
}

describe('one global assistant conversation', () => {
  beforeEach(() => { sessionStorage.clear(); resetAssistantDeliveries() })
  afterEach(() => { cleanup(); vi.restoreAllMocks() })

  it('continues one conversation through home, detail, chapter and collection with current context per send', async () => {
    const { fetch, sent } = server()
    const rendered = render(view())
    await open()
    await send('首页问题')
    rendered.rerender(view({ project: { id: projectA }, currentView: 'Detail' }))
    expect(screen.getByText('首页问题的回复')).toBeInTheDocument()
    await send('详情问题')
    rendered.rerender(view({ project: { id: projectA }, chapter: { id: chapterA }, currentView: 'Outline' }))
    await send('章节问题')
    rendered.rerender(view({ collection: { id: 'world', title: '世界', documentId: 'doc', versionId: 'v1' } }))
    await send('设定问题')
    expect(sent.map(item => item.id)).toEqual(['A', 'A', 'A', 'A'])
    expect(sent.map(item => item.context)).toEqual([{ kind: 'home', current_view: 'home' }, { kind: 'project', project_id: projectA, current_view: 'Detail' },
      { kind: 'project', project_id: projectA, chapter_id: chapterA, current_view: 'Outline' }, { kind: 'collection', collection_id: 'world', document_id: 'doc', document_version_id: 'v1' }])
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST' && !String(init.body).includes('client_message_id'))).toHaveLength(1)
    expect(sessionStorage.getItem(activeAssistantConversationKey)).toBe('A')
    expect(screen.queryByText('交互预览')).not.toBeInTheDocument()
  })

  it('canonicalizes uppercase route UUIDs before journaling and accepts the canonical completed ACK', async () => {
    const project = 'ABCDEFAB-ABCD-4ABC-8ABC-ABCDEFABCDEF'
    const chapter = 'FEDCBAFE-DCBA-4FED-8FED-FEDCBAFEDCBA'
    const { sent } = server()
    render(view({ currentView: 'Outline' }, `/projects/${project}/studio/${chapter}`))
    await open(); await send('大写链接中的章节问题')
    expect(sent[0].context).toEqual({ kind: 'project', project_id: project.toLowerCase(), chapter_id: chapter.toLowerCase(), current_view: 'Outline' })
    expect(sessionStorage.getItem(pendingKey())).toBeNull()
    expect(screen.getByText('大写链接中的章节问题的回复')).toBeInTheDocument()
    expect(screen.queryByText(/操作结果尚未确认/)).not.toBeInTheDocument()
  })

  it('restores the active conversation and its draft after a page remount without creating another conversation', async () => {
    const { fetch } = server()
    const first = render(view())
    await open(); await send('原有历史')
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '还没发送的草稿' } })
    first.unmount()
    await act(async () => { render(view({ project: { id: projectA }, currentView: 'Detail' })) })
    expect(screen.getByRole('textbox')).toHaveValue('还没发送的草稿')
    expect(screen.getByText('原有历史的回复')).toBeInTheDocument()
    expect(fetch.mock.calls.filter(([url, init]) => url === '/api/v1/assistant/conversations' && init?.method === 'POST')).toHaveLength(1)
    expect(fetch).toHaveBeenCalledWith('/api/v1/assistant/conversations/A', expect.any(Object))
  })

  it('creates an empty conversation and resumes each conversation with its own draft through history', async () => {
    const { fetch } = server()
    render(view()); await open(); await send('第一段对话')
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'A 的草稿' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '新建对话' })) })
    const newId = sessionStorage.getItem(activeAssistantConversationKey)!
    expect(screen.getByRole('textbox')).toHaveValue('')
    expect(screen.queryByText('第一段对话的回复')).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'B 的草稿' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '历史对话' })) })
    expect(screen.getByRole('region', { name: '历史对话' })).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '历史对话' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('button', { name: '返回当前对话' })).not.toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '历史对话' })) })
    expect(screen.getByRole('textbox')).toHaveValue('B 的草稿')
    expect(screen.getByRole('textbox')).toHaveFocus()
    expect(screen.getByRole('button', { name: '历史对话' })).toHaveAttribute('aria-pressed', 'false')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '历史对话' })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '继续对话：第一段对话' })) })
    expect(screen.getByRole('textbox')).toHaveValue('A 的草稿')
    expect(screen.getByText('第一段对话的回复')).toBeInTheDocument()
    expect(sessionStorage.getItem(draftKey(newId))).toBe('B 的草稿')
    expect(fetch.mock.calls.some(([, init]) => init?.body && JSON.parse(String(init.body)).new_conversation === true)).toBe(true)
  })

  it('reuses a known empty conversation when new conversation is clicked repeatedly', async () => {
    const { fetch } = server(); render(view()); await open()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '新建对话' })); fireEvent.click(screen.getByRole('button', { name: '新建对话' })) })
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  it('keeps the original pending context and identity when navigation and newer typing precede retry', async () => {
    const sent: unknown[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      if (String(url).endsWith('/messages')) { sent.push(JSON.parse(String(init?.body))); if (sent.length === 1) throw new TypeError('lost ACK') }
      return response()
    })
    const rendered = render(view({ collection: { id: 'world', title: '世界', documentId: 'doc', versionId: 'old' } }))
    await open(); await send('原请求')
    rendered.rerender(view({ project: { id: projectA }, chapter: { id: chapterA }, currentView: 'Draft' }))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '更新的草稿' } })
    expect(screen.getByRole('button', { name: '发送给 Gura' })).toBeDisabled()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重试未确认的消息' })) })
    expect(sent).toHaveLength(2)
    expect(sent[1]).toEqual(sent[0])
    expect(screen.getByRole('textbox')).toHaveValue('更新的草稿')
  })

  it('retains in-flight identity across refresh and retries the exact request from a different page', async () => {
    const sent: unknown[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      if (String(url).endsWith('/messages')) { sent.push(JSON.parse(String(init?.body))); if (sent.length === 1) return new Promise<Response>(() => {}) }
      return response()
    })
    const first = render(view({ project: { id: projectA }, chapter: { id: chapterA }, currentView: 'Outline' }))
    await open(); await send('只能执行一次')
    expect(screen.getByRole('textbox')).toHaveValue('')
    expect(JSON.parse(sessionStorage.getItem(pendingKey())!)).toMatchObject({ conversationId: 'A', text: '只能执行一次', context: { kind: 'project', project_id: projectA, chapter_id: chapterA, current_view: 'Outline' } })
    first.unmount()
    resetAssistantDeliveries() // A real browser reload also discards in-memory transport state.
    await act(async () => { render(view()) })
    await send()
    expect(sent).toHaveLength(2); expect(sent[1]).toEqual(sent[0])
  })

  it('delivers automatically across a page remount without another send, preserving newer typing, focus and the final history over a stale load', async () => {
    let resolveSend!: (value: Response) => void
    let resolveLoad!: (value: Response) => void
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    let payload: { content: string; client_message_id: string; context: AssistantPageContext }
    fetch.mockImplementation(async (url, init) => {
      if (String(url).endsWith('/messages')) { payload = JSON.parse(String(init?.body)); return new Promise<Response>(accept => { resolveSend = accept }) }
      if (String(url).endsWith('/conversations/A')) return new Promise<Response>(accept => { resolveLoad = accept })
      return original(url, init)
    })
    const first = render(view()); await open(); await send('页面切换前的消息')
    first.unmount()
    await act(async () => { render(view({ project: { id: projectA }, currentView: 'Detail' })) })
    expect(screen.getByText('页面切换前的消息')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '详情页继续写的草稿' } })
    const outside = screen.getByRole('button', { name: '继续编辑' }); outside.focus()
    const acknowledged = conversation('A', [{ id: 'u', role: 'user', content: payload!.content, created_at: date, tool_calls: [{ client_message_id: payload!.client_message_id, context: payload!.context, exchange_complete: true }] },
      { id: 'a', role: 'assistant', content: '跨页面自动到达的回复', created_at: date }])
    await act(async () => { resolveSend(response(acknowledged)) })
    expect(screen.getByText('跨页面自动到达的回复')).toBeInTheDocument()
    expect(screen.getByRole('textbox')).toHaveValue('详情页继续写的草稿')
    expect(outside).toHaveFocus()
    await act(async () => { resolveLoad(response()) })
    expect(screen.getByText('跨页面自动到达的回复')).toBeInTheDocument()
    expect(sessionStorage.getItem(pendingKey())).toBeNull()
    expect(fetch.mock.calls.filter(([url]) => String(url).endsWith('/messages'))).toHaveLength(1)
    expect(screen.queryByRole('button', { name: '重试未确认的消息' })).not.toBeInTheDocument()
  })

  it('ignores a stale GET error after a newer completed delivery supplied the current conversation', async () => {
    let resolveSend!: (value: Response) => void
    let rejectLoad!: (cause: Error) => void
    let payload: { content: string; client_message_id: string; context: AssistantPageContext }
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    fetch.mockImplementation(async (url, init) => {
      if (String(url).endsWith('/messages')) { payload = JSON.parse(String(init?.body)); return new Promise<Response>(accept => { resolveSend = accept }) }
      if (String(url).endsWith('/conversations/A')) return new Promise<Response>((_accept, reject) => { rejectLoad = reject })
      return original(url, init)
    })
    const first = render(view()); await open(); await send('跨页发送')
    first.unmount()
    await act(async () => { render(view({ project: { id: projectA } })) })
    await act(async () => { resolveSend(response(conversation('A', [
      { id: 'u', role: 'user', content: payload!.content, created_at: date, tool_calls: [{ client_message_id: payload!.client_message_id, context: payload!.context, exchange_complete: true }] },
      { id: 'a', role: 'assistant', content: '较新且已完成的回复', created_at: date },
    ]))) })
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '下一条消息' } })
    expect(screen.getByRole('button', { name: '发送给 Gura' })).toBeEnabled()
    await act(async () => { rejectLoad(new TypeError('stale GET failed')) })
    expect(screen.getByText('较新且已完成的回复')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新载入会话' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发送给 Gura' })).toBeEnabled()
    expect(screen.getByRole('textbox')).toHaveValue('下一条消息')
  })

  it('keeps a failed delivery and a failed load recoverable without getting stuck loading', async () => {
    let rejectSend!: (cause: Error) => void
    let rejectLoad!: (cause: Error) => void
    let loads = 0
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    fetch.mockImplementation(async (url, init) => {
      if (String(url).endsWith('/messages')) return new Promise<Response>((_accept, reject) => { rejectSend = reject })
      if (String(url).endsWith('/conversations/A') && ++loads === 1) return new Promise<Response>((_accept, reject) => { rejectLoad = reject })
      return original(url, init)
    })
    const first = render(view()); await open(); await send('失败后仍可恢复')
    first.unmount()
    await act(async () => { render(view({ collection: { id: 'world', title: '世界' } })) })
    await act(async () => { rejectSend(new TypeError('send failed')) })
    await act(async () => { rejectLoad(new TypeError('GET failed')) })
    expect(screen.getByText('无法连接 Gura 助手服务')).toBeInTheDocument()
    expect(screen.getByRole('textbox')).toHaveValue('失败后仍可恢复')
    expect(screen.getByRole('button', { name: '发送给 Gura' })).toBeDisabled()
    expect(screen.queryByText('正在载入助手会话…')).not.toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重新载入会话' })) })
    expect(screen.queryByRole('button', { name: '重新载入会话' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试未确认的消息' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发送给 Gura' })).toBeEnabled()
    expect(fetch.mock.calls.filter(([url]) => String(url).endsWith('/messages'))).toHaveLength(1)
  })

  it('restores an in-flight failure in the same conversation after a page remount without sending again', async () => {
    let reject!: (cause: Error) => void
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    fetch.mockImplementation(async (url, init) => String(url).endsWith('/messages') ? new Promise<Response>((_accept, refuse) => { reject = refuse }) : original(url, init))
    const first = render(view()); await open(); await send('失败后保留')
    first.unmount()
    await act(async () => { render(view({ collection: { id: 'world', title: '世界' } })) })
    await act(async () => { reject(new TypeError('connection lost')) })
    expect(screen.getByText('发送失败，请稍后重试。')).toBeInTheDocument()
    expect(screen.getByRole('textbox')).toHaveValue('失败后保留')
    expect(screen.getByRole('button', { name: '重试未确认的消息' })).toBeInTheDocument()
    expect(fetch.mock.calls.filter(([url]) => String(url).endsWith('/messages'))).toHaveLength(1)
  })

  it('preserves newer identical text across a page remount and a late ACK', async () => {
    let resolve!: (value: Response) => void
    let sent: { content: string; client_message_id: string; context: AssistantPageContext }
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    fetch.mockImplementation(async (url, init) => {
      if (String(url).endsWith('/messages')) { sent = JSON.parse(String(init?.body)); return new Promise<Response>(accept => { resolve = accept }) }
      return original(url, init)
    })
    const first = render(view()); await open(); await send('相同的文字')
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '相同的文字' } })
    first.unmount()
    await act(async () => { render(view({ project: { id: projectA }, currentView: 'Detail' })) })
    expect(screen.getByRole('textbox')).toHaveValue('相同的文字')
    await act(async () => { resolve(response(conversation('A', [{ id: 'u', role: 'user', content: sent!.content, created_at: date,
      tool_calls: [{ client_message_id: sent!.client_message_id, context: sent!.context, exchange_complete: true }] }]))) })
    expect(screen.getByRole('textbox')).toHaveValue('相同的文字')
    expect(sessionStorage.getItem(draftKey())).toBe('相同的文字')
  })

  it('preserves an explicitly cleared draft on a full reload while retaining the original retry separately', async () => {
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    fetch.mockImplementation(async (url, init) => { if (String(url).endsWith('/messages')) throw new TypeError('lost'); return original(url, init) })
    const first = render(view()); await open(); await send('保留原请求身份')
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '' } })
    first.unmount(); resetAssistantDeliveries()
    await act(async () => { render(view()) })
    expect(screen.getByRole('textbox')).toHaveValue('')
    expect(screen.getByRole('button', { name: '重试未确认的消息' })).toBeInTheDocument()
    expect(sessionStorage.getItem(pendingKey())).not.toBeNull()
  })

  it('clears automatically restored failure text on reload when the server confirms the exact exchange', async () => {
    let saved: AssistantConversation
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    fetch.mockImplementation(async (url, init) => {
      if (String(url).endsWith('/messages')) {
        const payload = JSON.parse(String(init?.body))
        saved = conversation('A', [{ id: 'u', role: 'user', content: payload.content, created_at: date, tool_calls: [{ client_message_id: payload.client_message_id, context: payload.context, exchange_complete: true }] }])
        throw new TypeError('ACK lost')
      }
      if (String(url).endsWith('/conversations/A') && saved) return response(saved)
      return original(url, init)
    })
    const first = render(view()); await open(); await send('自动恢复的原文')
    expect(screen.getByRole('textbox')).toHaveValue('自动恢复的原文')
    first.unmount(); resetAssistantDeliveries()
    await act(async () => { render(view()) })
    expect(screen.getByRole('textbox')).toHaveValue('')
    expect(sessionStorage.getItem(pendingKey())).toBeNull()
  })

  it('ignores a late ACK after selecting a new conversation and reconciles the original when revisited', async () => {
    const { fetch, sessions } = server()
    const original = fetch.getMockImplementation()!
    let resolve!: (value: Response) => void
    let payload: { content: string; client_message_id: string; context: AssistantPageContext }
    fetch.mockImplementation(async (url, init) => {
      if (String(url).endsWith('/A/messages')) { payload = JSON.parse(String(init?.body)); return new Promise<Response>(accept => { resolve = accept }) }
      return original(url, init)
    })
    render(view()); await open(); await send('A 的请求')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '新建对话' })) })
    const newId = sessionStorage.getItem(activeAssistantConversationKey)!
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'B 的新草稿' } })
    const outside = screen.getByRole('button', { name: '继续编辑' }); outside.focus()
    const acknowledged = conversation('A', [{ id: 'm', role: 'user', content: 'A 的请求', created_at: date, tool_calls: [{ client_message_id: payload!.client_message_id, context: payload!.context, exchange_complete: true }] }])
    sessions.set('A', acknowledged)
    await act(async () => { resolve(response(acknowledged)) })
    expect(outside).toHaveFocus()
    expect(screen.getByRole('textbox')).toHaveValue('B 的新草稿')
    expect(sessionStorage.getItem(pendingKey())).not.toBeNull()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '历史对话' })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '继续对话：A 的请求' })) })
    expect(sessionStorage.getItem(pendingKey())).toBeNull()
    expect(sessionStorage.getItem(draftKey(newId))).toBe('B 的新草稿')
  })

  it.each([{ content: '别的文字', context: { kind: 'home', current_view: 'home' } }, { content: '原请求', context: { kind: 'home' } }])('does not clear recovery for an inexact stored ACK ($content)', async acknowledged => {
    sessionStorage.setItem(activeAssistantConversationKey, 'A'); sessionStorage.setItem('guranovel_assistant_open', 'true')
    sessionStorage.setItem(pendingKey(), JSON.stringify({ id: 'retry', text: '原请求', conversationId: 'A', context: { kind: 'home', current_view: 'home' } }))
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => response(conversation('A', [{ id: 'm', role: 'user', ...acknowledged, tool_calls: [{ client_message_id: 'retry', context: acknowledged.context, exchange_complete: true }], created_at: date }])))
    await act(async () => { render(view()) })
    expect(screen.getByRole('button', { name: '重试未确认的消息' })).toBeInTheDocument()
  })

  it.each([false, undefined])('keeps an incomplete exchange on reload and preserves its identity on unconfirmed-operation errors (%s)', async complete => {
    const context = { kind: 'collection' as const, collection_id: 'world', document_id: 'doc', document_version_id: 'old' }
    sessionStorage.setItem(activeAssistantConversationKey, 'A'); sessionStorage.setItem('guranovel_assistant_open', 'true')
    sessionStorage.setItem(pendingKey(), JSON.stringify({ id: 'retry', text: '原请求', conversationId: 'A', context }))
    const requests: { client_message_id: string }[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      if (String(url).endsWith('/messages')) { requests.push(JSON.parse(String(init?.body))); return response({ error: { code: 'assistant_exchange_unconfirmed' } }, 409) }
      return response(conversation('A', [{ id: 'u', role: 'user', content: '原请求', created_at: date, tool_calls: [{ client_message_id: 'retry', context, exchange_complete: complete }] }]))
    })
    await act(async () => { render(view({ collection: { id: 'world', title: '世界', documentId: 'doc', versionId: 'new' } })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重试未确认的消息' })) })
    expect(screen.getByText('操作结果尚未确认，请先查看作品状态；原消息已保留，请勿重复执行。')).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重试未确认的消息' })) })
    expect(requests.map(request => request.client_message_id)).toEqual(['retry', 'retry'])
    expect(JSON.parse(sessionStorage.getItem(pendingKey())!).context).toEqual(context)
  })

  it('does not accept a 200 response for the same conversation without an exact completed user ACK', async () => {
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    fetch.mockImplementation(async (url, init) => String(url).endsWith('/messages') ? response() : original(url, init))
    render(view()); await open(); await send('服务仍未确认')
    expect(screen.getByText('操作结果尚未确认，请先查看作品状态；原消息已保留，请勿重复执行。')).toBeInTheDocument()
    expect(screen.getByRole('textbox')).toHaveValue('服务仍未确认')
    expect(sessionStorage.getItem(pendingKey())).not.toBeNull()
  })

  it('keeps a known active conversation on load failure and retries loading without silently creating a fresh chat', async () => {
    sessionStorage.setItem(activeAssistantConversationKey, 'A'); sessionStorage.setItem(draftKey(), '仍然保留')
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({}, 503))
    render(view()); await open()
    expect(screen.getByRole('textbox')).toHaveValue('仍然保留')
    expect(screen.getByRole('button', { name: '发送给 Gura' })).toBeDisabled()
    expect(screen.queryByText('软件哪里不会用？')).not.toBeInTheDocument()
    fetch.mockResolvedValue(response())
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重新载入会话' })) })
    expect(fetch.mock.calls.every(([url, init]) => url === '/api/v1/assistant/conversations/A' && init?.method !== 'POST')).toBe(true)
  })

  it('preserves typing while the initial conversation loads, merging an existing target draft visibly', async () => {
    let resolve!: (value: Response) => void
    sessionStorage.setItem(draftKey(), '此前的草稿')
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Promise<Response>(accept => { resolve = accept }))
    render(view()); act(() => { fireEvent.click(screen.getByRole('button', { name: 'Gura' })) })
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '等待时写的文字' } })
    await act(async () => { resolve(response()) })
    expect(screen.getByRole('textbox')).toHaveValue('此前的草稿\n\n等待时写的文字')
    expect(screen.getByText('此前暂存的草稿已合并，请核对后发送。')).toBeInTheDocument()
  })

  it('blocks transport if a durable retry identity cannot be stored', async () => {
    const { fetch } = server(); render(view()); await open()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '不能丢' } })
    const setItem = Storage.prototype.setItem
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key, value) { if (key.startsWith('guranovel_assistant_pending:')) throw new Error('quota'); setItem.call(this, key, value) })
    await send()
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith('/messages'))).toBe(false)
    expect(screen.getByRole('textbox')).toHaveValue('不能丢')
  })

  it('retains one new-conversation identity through a wrong ACK and retries from history without replacing the original draft', async () => {
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    const createIds: string[] = []
    fetch.mockImplementation(async (url, init) => {
      const body = init?.body && JSON.parse(String(init.body))
      if (body?.new_conversation) {
        createIds.push(body.client_conversation_id)
        return response(conversation(createIds.length === 1 ? 'wrong-ack' : body.client_conversation_id))
      }
      return original(url, init)
    })
    render(view()); await open()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '原对话的草稿' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '历史对话' })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '新建对话' })) })
    expect(screen.getByRole('alert')).toHaveTextContent('新建对话的响应不匹配')
    expect(sessionStorage.getItem(activeAssistantConversationKey)).toBe('A')
    expect(sessionStorage.getItem(draftKey())).toBe('原对话的草稿')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '新建对话' })) })
    expect(createIds).toHaveLength(2); expect(createIds[1]).toBe(createIds[0])
    expect(sessionStorage.getItem(activeAssistantConversationKey)).toBe(createIds[0])
    expect(screen.getByRole('textbox')).toHaveValue('')
  })

  it('does not send to the old conversation while a new conversation is being created', async () => {
    let resolve!: (value: Response) => void
    let newId = ''
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    fetch.mockImplementation(async (url, init) => {
      const body = init?.body && JSON.parse(String(init.body))
      if (body?.new_conversation) { newId = body.client_conversation_id; return new Promise<Response>(accept => { resolve = accept }) }
      return original(url, init)
    })
    render(view()); await open()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '原有草稿' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '新建对话' })) })
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '等待新对话时继续写' } })
    expect(screen.getByRole('button', { name: '发送给 Gura' })).toBeDisabled()
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith('/messages'))).toBe(false)
    await act(async () => { resolve(response(conversation(newId))) })
    expect(screen.getByRole('textbox')).toHaveValue('')
    expect(sessionStorage.getItem(draftKey())).toBe('等待新对话时继续写')
  })

  it('opens legacy page history read-only without replacing the active conversation or its draft', async () => {
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    fetch.mockImplementation(async (url, init) => {
      if (String(url).includes('legacy-conversations/old')) return response(conversation('old', [{ id: 'old-message', role: 'assistant', content: '早期记录', created_at: date }]))
      if (String(url).includes('legacy-conversations')) return response([{ id: 'old', title: '早期章节', created_at: date, updated_at: date, message_count: 1, preview: '早期记录' }])
      return original(url, init)
    })
    render(view({ project: { id: projectA }, currentView: 'Detail' })); await open()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '当前草稿' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '历史对话' })) })
    expect(screen.getByText('早期页面对话')).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查看早期对话：早期章节' })) })
    expect(screen.getByText('早期记录')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发送给 Gura' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '历史对话' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: '历史对话' }))
    expect(screen.getByRole('textbox')).toHaveValue('当前草稿')
    expect(sessionStorage.getItem(activeAssistantConversationKey)).toBe('A')
    expect(fetch).toHaveBeenCalledWith(`/api/v1/assistant/legacy-conversations?project_id=${projectA}`, expect.any(Object))
  })

  it('hides the prior legacy records and shows a loader while the newly selected legacy conversation is loading', async () => {
    let resolveLegacy!: (value: Response) => void
    const { fetch } = server(); const original = fetch.getMockImplementation()!
    fetch.mockImplementation(async (url, init) => {
      const path = String(url)
      if (path.includes('legacy-conversations/old-1')) return response(conversation('old-1', [{ id: 'old-1-message', role: 'assistant', content: '第一段早期记录', created_at: date }]))
      if (path.includes('legacy-conversations/old-2')) return new Promise<Response>(accept => { resolveLegacy = accept })
      if (path.includes('legacy-conversations')) return response(['old-1', 'old-2'].map(id => ({ id, title: id, created_at: date, updated_at: date, message_count: 1, preview: id })))
      return original(url, init)
    })
    render(view({ project: { id: projectA } })); await open()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '历史对话' })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查看早期对话：old-1' })) })
    expect(screen.getByText('第一段早期记录')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '历史对话' }))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '历史对话' })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查看早期对话：old-2' })) })
    expect(screen.queryByText('第一段早期记录')).not.toBeInTheDocument()
    expect(screen.getByText('正在载入助手会话…')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    await act(async () => { resolveLegacy(response(conversation('old-2', [{ id: 'old-2-message', role: 'assistant', content: '第二段早期记录', created_at: date }]))) })
    expect(screen.getByText('第二段早期记录')).toBeInTheDocument()
    expect(screen.queryByText('第一段早期记录')).not.toBeInTheDocument()
    expect(screen.queryByText('正在载入助手会话…')).not.toBeInTheDocument()
    expect(sessionStorage.getItem(activeAssistantConversationKey)).toBe('A')
  })

  it('recovers the former home draft once, retains project drafts and keeps preview visibly unsent', async () => {
    sessionStorage.setItem('guranovel_assistant_draft:home', '早期首页草稿')
    sessionStorage.setItem(`guranovel_assistant_draft:project:${projectA}`, '早期作品草稿')
    const { fetch } = server(); const real = render(view()); await open()
    expect(screen.getByRole('textbox')).toHaveValue('早期首页草稿')
    expect(sessionStorage.getItem('guranovel_assistant_draft:home')).toBeNull()
    expect(sessionStorage.getItem(`guranovel_assistant_draft:project:${projectA}`)).toBe('早期作品草稿')
    real.unmount(); fetch.mockClear()
    await act(async () => { render(view({}, '/preview/studio')) })
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '预览测试' } })
    fireEvent.click(screen.getByRole('button', { name: '发送给 Gura（仅预览）' }))
    expect(screen.getByText('你 · 未发送')).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
    expect(sessionStorage.getItem(activeAssistantConversationKey)).toBe('A')
  })
})
