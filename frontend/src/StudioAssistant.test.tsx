import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Studio from './Studio'
import GlobalAssistant from './GlobalAssistant'
import { getProject, listChapters, readDocumentContent } from './api/client'
import { listChapterProductionRuns } from './api/chapterProductionV2Client'
import { resetAssistantDeliveries } from './assistantDeliveries'

vi.mock('./api/chapterProductionV2Client', async original => ({
  ...await original<typeof import('./api/chapterProductionV2Client')>(),
  listChapterProductionRuns: vi.fn(),
}))

vi.mock('./api/client', async importOriginal => ({
  ...await importOriginal<typeof import('./api/client')>(),
  getProject: vi.fn(),
  listChapters: vi.fn(),
  readDocumentContent: vi.fn(),
}))

describe('Studio Assistant', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listChapterProductionRuns).mockResolvedValue([])
    localStorage.clear()
    sessionStorage.clear()
    resetAssistantDeliveries()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('preserves focus moved to another menu item immediately after closing Gura', () => {
    vi.useFakeTimers()
    render(<MemoryRouter initialEntries={['/preview/studio']}><GlobalAssistant preview /><div role="menu"><button type="button" role="menuitem">与设定 Agent 协作</button></div></MemoryRouter>)
    const launcher = screen.getByRole('button', { name: 'Gura' })
    fireEvent.click(launcher)
    const input = screen.getByRole('textbox', { name: '给 Gura 的消息' })
    expect(input).toHaveFocus()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(launcher).toHaveAttribute('aria-expanded', 'false')
    expect(launcher).toHaveFocus()
    const action = screen.getByRole('menuitem', { name: '与设定 Agent 协作' })
    action.focus()
    expect(action).toHaveFocus()
    act(() => { vi.advanceTimersByTime(50) })
    expect(action).toHaveFocus()
  })

  it('continues the same conversation draft when changing collections', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ id: 'conv', project_id: null, messages: [] }), { status: 200 }))
    const view = (id: string) => <MemoryRouter><GlobalAssistant collection={{ id, title: id }} /></MemoryRouter>
    const result = render(view('world-A'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Gura' })) })
    fireEvent.change(screen.getByRole('textbox', { name: '给 Gura 的消息' }), { target: { value: 'A 的想法' } })
    await act(async () => { result.rerender(view('world-B')) })
    expect(screen.getByRole('textbox', { name: '给 Gura 的消息' })).toHaveValue('A 的想法')
    fireEvent.change(screen.getByRole('textbox', { name: '给 Gura 的消息' }), { target: { value: 'B 的想法' } })
    await act(async () => { result.rerender(view('world-A')) })
    expect(screen.getByRole('textbox', { name: '给 Gura 的消息' })).toHaveValue('B 的想法')
  })

  it('recovers unsent text after refresh during conversation initialization retry', async () => {
    let calls = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      calls++
      if (calls === 1) return new Response('{}', { status: 503 })
      if (calls === 2) return new Promise<Response>(() => {})
      return new Response(JSON.stringify({ id: 'world-conv', project_id: null, messages: [] }), { status: 200 })
    })
    const view = <MemoryRouter><GlobalAssistant collection={{ id: 'world', title: '世界' }} /></MemoryRouter>
    const first = render(view)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Gura' })) })
    fireEvent.change(screen.getByRole('textbox', { name: '给 Gura 的消息' }), { target: { value: '刷新也不能丢失' } })
    expect(screen.getByRole('button', { name: '发送给 Gura' })).toBeDisabled()
    expect(screen.queryByText('软件哪里不会用？')).not.toBeInTheDocument()
    act(() => { fireEvent.click(screen.getByRole('button', { name: '重新载入会话' })) })
    expect(calls).toBe(2)
    first.unmount()
    await act(async () => { render(view) })
    expect(screen.getByRole('textbox', { name: '给 Gura 的消息' })).toHaveValue('刷新也不能丢失')
  })

  it('retries a rejected setting version with the current context and a fresh identity', async () => {
    const requests: { context: { document_version_id: string }; client_message_id: string }[] = []
    const conversation = { id: 'world-conv', project_id: null, messages: [] }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      if (String(url).endsWith('/messages')) {
        requests.push(JSON.parse(String(init?.body)))
        return new Response('{}', { status: 409 })
      }
      return new Response(JSON.stringify(conversation), { status: 200 })
    })
    const view = (versionId: string) => <MemoryRouter><GlobalAssistant collection={{ id: 'world', title: '世界', documentId: 'doc', versionId }} /></MemoryRouter>
    const rendered = render(view('old'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Gura' })) })
    const input = screen.getByRole('textbox', { name: '给 Gura 的消息' })
    fireEvent.change(input, { target: { value: '解释当前设定' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '发送给 Gura' })) })
    expect(input).toHaveValue('解释当前设定')
    rendered.rerender(view('current'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '发送给 Gura' })) })
    expect(requests).toHaveLength(2)
    expect(requests[1].context.document_version_id).toBe('current')
    expect(requests[1].client_message_id).not.toBe(requests[0].client_message_id)
  })

  it('renders preview assistant with backwards compatible labels and no network call', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/preview/studio']}>
          <Routes>
            <Route path="/preview/studio" element={<Studio />} />
          </Routes>
        </MemoryRouter>
      )
    })

    const launcher = screen.getByRole('button', { name: 'Gura' })
    fireEvent.click(launcher)

    expect(screen.getByRole('dialog', { name: '与 Gura 对话' })).toBeInTheDocument()
    expect(screen.getByText('交互预览')).toBeInTheDocument()
    expect(screen.getByText('预览消息不会发送')).toBeInTheDocument()

    const input = screen.getByRole('textbox', { name: '给 Gura 的消息' })
    const sendButton = screen.getByRole('button', { name: '发送给 Gura（仅预览）' })
    expect(sendButton).toBeDisabled()

    fireEvent.change(input, { target: { value: '预览测试消息' } })
    expect(sendButton).not.toBeDisabled()

    fireEvent.click(sendButton)
    expect(input).toHaveValue('')
    expect(within(screen.getByRole('log', { name: '助手对话记录' })).getByText('预览测试消息')).toBeInTheDocument()
    expect(screen.getByText('你 · 未发送')).toBeInTheDocument()

    // No network request in preview mode
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('initializes real conversation and sends bounded messages with tool badges', async () => {
    vi.mocked(getProject).mockResolvedValue({ id: 'proj-123', title: '测试工程' } as Awaited<ReturnType<typeof getProject>>)
    vi.mocked(listChapters).mockResolvedValue([
      { id: 'chap-456', chapter_number: 1, title: '第一章', metadata: {}, current_draft_document_id: 'doc-1' }
    ] as Awaited<ReturnType<typeof listChapters>>)
    vi.mocked(readDocumentContent).mockResolvedValue({ document_id: 'doc-1', version_id: 'v1', content: '第一章草稿' })

    const mockConv = {
      id: 'conv-001',
      project_id: 'proj-123',
      chapter_id: 'chap-456',
      title: 'Gura 创作对话',
      messages: [
        {
          id: 'msg-1',
          role: 'user',
          content: '之前的消息',
          created_at: '2026-09-13T10:00:00Z',
        },
        {
          id: 'msg-2',
          role: 'assistant',
          content: '这是之前的回复',
          tool_calls: [{ id: 'tc-1', name: 'get_chapter_outline', arguments: {} }],
          created_at: '2026-09-13T10:00:01Z',
        }
      ],
      created_at: '2026-09-13T10:00:00Z',
      updated_at: '2026-09-13T10:00:01Z',
    }

    const updatedConv = {
      ...mockConv,
      messages: [
        ...mockConv.messages,
        {
          id: 'msg-3',
          role: 'user',
          content: '本章的大纲是什么？',
          created_at: '2026-09-13T10:01:00Z',
        },
        {
          id: 'msg-4',
          role: 'assistant',
          content: '本章大纲已查阅：主角与对手初次相遇。',
          tool_calls: [{ id: 'tc-2', name: 'get_chapter_outline', arguments: {} }],
          created_at: '2026-09-13T10:01:02Z',
        }
      ]
    }

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.includes('/assistant/conversations') && (!init || init.method === 'POST') && !url.includes('/messages')) {
        return new Response(JSON.stringify(mockConv), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/messages')) {
        const payload = JSON.parse(String(init?.body))
        return new Response(JSON.stringify({ ...updatedConv, messages: updatedConv.messages.map(message => message.id === 'msg-3'
          ? { ...message, tool_calls: [{ client_message_id: payload.client_message_id, context: payload.context, exchange_complete: true }] } : message) }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify({}), { status: 404 })
    })

    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/projects/proj-123/studio/chap-456']}>
          <Routes>
            <Route path="/projects/:projectId/studio/:chapterId?" element={<Studio />} />
          </Routes>
        </MemoryRouter>
      )
    })

    const launcher = screen.getByRole('button', { name: 'Gura' })
    await act(async () => {
      fireEvent.click(launcher)
    })

    expect(screen.queryByText('只读助手')).not.toBeInTheDocument()
    expect(screen.queryByText('受限只读业务助手 · 无修改定稿权限')).not.toBeInTheDocument()

    // History loaded
    expect(screen.getByText('之前的消息')).toBeInTheDocument()
    expect(screen.getByText('这是之前的回复')).toBeInTheDocument()
    expect(screen.getByText('已查阅 章节大纲')).toBeInTheDocument()

    const input = screen.getByRole('textbox', { name: '给 Gura 的消息' })
    const sendButton = screen.getByRole('button', { name: '发送给 Gura' })

    fireEvent.change(input, { target: { value: '本章的大纲是什么？' } })
    await act(async () => {
      fireEvent.click(sendButton)
    })

    // Check updated messages rendered
    expect(screen.getByText('本章大纲已查阅：主角与对手初次相遇。')).toBeInTheDocument()

    // Verify fetch call payload
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/v1/assistant/conversations/conv-001/messages',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: expect.stringMatching(/"content":"本章的大纲是什么？".*"client_message_id":/),
      })
    )
  })

  it('displays error and recovers draft if assistant request fails', async () => {
    vi.mocked(getProject).mockResolvedValue({ id: 'proj-123', title: '测试工程' } as Awaited<ReturnType<typeof getProject>>)
    vi.mocked(listChapters).mockResolvedValue([
      { id: 'chap-456', chapter_number: 1, title: '第一章', metadata: {}, current_draft_document_id: 'doc-1' }
    ] as Awaited<ReturnType<typeof listChapters>>)
    vi.mocked(readDocumentContent).mockResolvedValue({ document_id: 'doc-1', version_id: 'v1', content: '第一章草稿' })

    const mockConv = {
      id: 'conv-001',
      project_id: 'proj-123',
      chapter_id: 'chap-456',
      title: 'Gura 创作对话',
      messages: [],
      created_at: '2026-09-13T10:00:00Z',
      updated_at: '2026-09-13T10:00:00Z',
    }

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.includes('/assistant/conversations') && (!init || init.method === 'POST') && !url.includes('/messages')) {
        return new Response(JSON.stringify(mockConv), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/messages')) {
        return new Response(JSON.stringify({ detail: 'Service unavailable' }), { status: 500, statusText: 'Internal Server Error' })
      }
      return new Response(JSON.stringify({}), { status: 404 })
    })

    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/projects/proj-123/studio/chap-456']}>
          <Routes>
            <Route path="/projects/:projectId/studio/:chapterId?" element={<Studio />} />
          </Routes>
        </MemoryRouter>
      )
    })

    const launcher = screen.getByRole('button', { name: 'Gura' })
    await act(async () => {
      fireEvent.click(launcher)
    })

    const input = screen.getByRole('textbox', { name: '给 Gura 的消息' })
    const sendButton = screen.getByRole('button', { name: '发送给 Gura' })

    fireEvent.change(input, { target: { value: '未能送达的消息' } })
    await act(async () => {
      fireEvent.click(sendButton)
    })

    expect(screen.getByText('无法连接 Gura 助手服务，请稍后重试；消息已保留。')).toBeInTheDocument()
    // Draft is restored in input
    expect(input).toHaveValue('未能送达的消息')
  })

  it('shows thinking indicator while request is in flight and supports closing by Escape', async () => {
    vi.mocked(getProject).mockResolvedValue({ id: 'proj-123', title: '测试工程' } as Awaited<ReturnType<typeof getProject>>)
    vi.mocked(listChapters).mockResolvedValue([
      { id: 'chap-456', chapter_number: 1, title: '第一章', metadata: {}, current_draft_document_id: 'doc-1' }
    ] as Awaited<ReturnType<typeof listChapters>>)
    vi.mocked(readDocumentContent).mockResolvedValue({ document_id: 'doc-1', version_id: 'v1', content: '第一章草稿' })

    const mockConv = {
      id: 'conv-001',
      project_id: 'proj-123',
      chapter_id: 'chap-456',
      title: 'Gura 创作对话',
      messages: [],
      created_at: '2026-09-13T10:00:00Z',
      updated_at: '2026-09-13T10:00:00Z',
    }

    let resolveSend!: (value: Response) => void
    let sentRequest: { client_message_id: string; context: Record<string, unknown> }
    const sendPromise = new Promise<Response>(resolve => {
      resolveSend = resolve
    })

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.includes('/assistant/conversations') && (!init || init.method === 'POST') && !url.includes('/messages')) {
        return new Response(JSON.stringify(mockConv), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/messages')) {
        sentRequest = JSON.parse(String(init?.body))
        return sendPromise
      }
      return new Response(JSON.stringify({}), { status: 404 })
    })

    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/projects/proj-123/studio/chap-456']}>
          <Routes>
            <Route path="/projects/:projectId/studio/:chapterId?" element={<Studio />} />
          </Routes>
        </MemoryRouter>
      )
    })

    const launcher = screen.getByRole('button', { name: 'Gura' })
    await act(async () => {
      fireEvent.click(launcher)
    })

    const input = screen.getByRole('textbox', { name: '给 Gura 的消息' })
    const sendButton = screen.getByRole('button', { name: '发送给 Gura' })

    fireEvent.change(input, { target: { value: '思考状态测试' } })
    await act(async () => {
      fireEvent.click(sendButton)
    })

    // In flight: thinking indicator visible
    expect(screen.getByText('正在查阅工程状态并思考…')).toBeInTheDocument()
    expect(screen.getByText('思考状态测试')).toBeInTheDocument()

    // Resolve request
    await act(async () => {
      resolveSend(
        new Response(
          JSON.stringify({
            ...mockConv,
            messages: [
              { id: 'm-1', role: 'user', content: '思考状态测试', created_at: '2026-09-13T10:02:00Z', tool_calls: [{ client_message_id: sentRequest.client_message_id, context: sentRequest.context, exchange_complete: true }] },
              { id: 'm-2', role: 'assistant', content: '已完成思考并回复', created_at: '2026-09-13T10:02:01Z' },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      )
    })

    expect(screen.queryByText('正在查阅工程状态并思考…')).toBeNull()
    expect(screen.getByText('已完成思考并回复')).toBeInTheDocument()

    // Press Escape to close assistant
    fireEvent.keyDown(input, { key: 'Escape' })
    const currentLauncher = screen.getByRole('button', { name: 'Gura' })
    expect(currentLauncher).toHaveAttribute('aria-expanded', 'false')
    expect(currentLauncher).toHaveFocus()
  })

  it('persists open state and draft across remounts via sessionStorage', async () => {
    sessionStorage.setItem('guranovel_assistant_open', 'true')
    sessionStorage.setItem('guranovel_assistant_draft:preview', '草稿暂存文字')

    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/preview/studio']}>
          <Routes>
            <Route path="/preview/studio" element={<Studio />} />
          </Routes>
        </MemoryRouter>
      )
    })

    const input = screen.getByRole('textbox', { name: '给 Gura 的消息' })
    expect(screen.getByRole('dialog', { name: '与 Gura 对话' })).toBeInTheDocument()
    expect(input).toHaveValue('草稿暂存文字')

    fireEvent.change(input, { target: { value: '更新后的暂存文字' } })
    expect(sessionStorage.getItem('guranovel_assistant_draft:preview')).toBe('更新后的暂存文字')

    const closeBtn = screen.getByRole('button', { name: '收起助手' })
    fireEvent.click(closeBtn)
    expect(sessionStorage.getItem('guranovel_assistant_open')).toBe('false')
  })

  it('renders interactive action buttons for tool results and triggers navigation on click', async () => {
    vi.mocked(getProject).mockResolvedValue({ id: 'proj-123', title: '测试工程' } as Awaited<ReturnType<typeof getProject>>)
    vi.mocked(listChapters).mockResolvedValue([
      { id: 'chap-456', chapter_number: 1, title: '第一章', metadata: {}, current_draft_document_id: 'doc-1' }
    ] as Awaited<ReturnType<typeof listChapters>>)
    vi.mocked(readDocumentContent).mockResolvedValue({ document_id: 'doc-1', version_id: 'v1', content: '第一章草稿' })

    const mockConv = {
      id: 'conv-action-01',
      project_id: 'proj-123',
      chapter_id: 'chap-456',
      title: 'Gura 创作对话',
      messages: [
        {
          id: 'msg-action-1',
          role: 'assistant',
          content: '好的！已为你创建新章节，你可以直接点击前往：',
          tool_calls: [{ id: 'call_1', name: 'create_chapter', arguments: { title: '第二章' } }],
          tool_results: [
            {
              tool_call_id: 'call_1',
              name: 'create_chapter',
              result: {
                action: 'create_chapter',
                chapter_id: 'chap-789',
                chapter_number: 2,
                title: '第二章',
                target_url: '/projects/proj-123/chapters/chap-789',
              }
            }
          ],
          created_at: '2026-09-13T10:05:00Z',
        }
      ],
      created_at: '2026-09-13T10:00:00Z',
      updated_at: '2026-09-13T10:05:00Z',
    }

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/assistant/conversations')) {
        return new Response(JSON.stringify(mockConv), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify({}), { status: 404 })
    })

    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/projects/proj-123/studio/chap-456']}>
          <Routes>
            <Route path="/projects/:projectId/studio/:chapterId?" element={<Studio />} />
            <Route path="/projects/:projectId/chapters/:chapterId" element={<div data-testid="target-chapter-view">目标章节已进入</div>} />
          </Routes>
        </MemoryRouter>
      )
    })

    const launcher = screen.getByRole('button', { name: 'Gura' })
    await act(async () => {
      fireEvent.click(launcher)
    })

    // Action button is rendered
    const actionBtn = screen.getByRole('button', { name: '👉 前往第 2 章' })
    expect(actionBtn).toBeInTheDocument()

    // Click action button to navigate
    await act(async () => {
      fireEvent.click(actionBtn)
    })

    expect(screen.getByTestId('target-chapter-view')).toBeInTheDocument()
  })

  it('infers route context when mounted globally on project workspace', async () => {
    const mockConv = {
      id: 'conv-global-01',
      project_id: 'proj-999',
      messages: [],
      created_at: '2026-09-13T10:00:00Z',
      updated_at: '2026-09-13T10:00:00Z',
    }

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/assistant/conversations')) {
        return new Response(JSON.stringify(mockConv), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify({}), { status: 404 })
    })

    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/projects/proj-999']}>
          <GlobalAssistant />
        </MemoryRouter>
      )
    })

    const launcher = screen.getByRole('button', { name: 'Gura' })
    await act(async () => {
      fireEvent.click(launcher)
    })

    // The global conversation stays global; each send carries the inferred project context.
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/v1/assistant/conversations',
      expect.objectContaining({
        method: 'POST',
      })
    )
    expect(screen.queryByText('只读助手')).not.toBeInTheDocument()
  })

  it('renders and toggles correctly on dashboard without studio wrapper', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ id: 'home-conv', project_id: null, messages: [] }), { status: 200 }))
    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/']}>
          <div className="app-shell dashboard-app-shell">
            <GlobalAssistant />
          </div>
        </MemoryRouter>
      )
    })

    const launcher = screen.getByRole('button', { name: 'Gura' })
    expect(launcher).toBeInTheDocument()
    expect(launcher).toHaveAttribute('aria-expanded', 'false')

    // Open assistant on Dashboard
    await act(async () => {
      fireEvent.click(launcher)
    })

    const assistant = document.querySelector('.studio-assistant')
    expect(assistant).toHaveClass('is-open')
    expect(screen.queryByText('交互预览')).not.toBeInTheDocument()
    expect(screen.getByText('软件哪里不会用？')).toBeInTheDocument()
  })
})
