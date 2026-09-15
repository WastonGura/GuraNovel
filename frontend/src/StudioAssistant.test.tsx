import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Studio from './Studio'
import GlobalAssistant from './GlobalAssistant'
import { getProject, listChapters, readDocumentContent } from './api/client'
import { listChapterProductionRuns } from './api/chapterProductionV2Client'

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
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renders preview assistant with backwards compatible labels and no network call', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/studio']}>
          <Routes>
            <Route path="/studio" element={<Studio />} />
          </Routes>
        </MemoryRouter>
      )
    })

    const launcher = screen.getByRole('button', { name: 'Gura' })
    fireEvent.click(launcher)

    expect(screen.getByRole('dialog', { name: '与 Gura 对话' })).toBeInTheDocument()
    expect(screen.getByText('交互预览')).toBeInTheDocument()
    expect(screen.getByText('仅本次预览 · Agent 尚未接入')).toBeInTheDocument()

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
        return new Response(JSON.stringify(updatedConv), { status: 200, headers: { 'Content-Type': 'application/json' } })
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

    expect(screen.getByText('只读助手')).toBeInTheDocument()
    expect(screen.getByText('受限只读业务助手 · 无修改定稿权限')).toBeInTheDocument()

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
      '/api/v1/projects/proj-123/assistant/conversations/conv-001/messages',
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

    expect(screen.getByText('发送失败，请稍后重试。')).toBeInTheDocument()
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
    const sendPromise = new Promise<Response>(resolve => {
      resolveSend = resolve
    })

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.includes('/assistant/conversations') && (!init || init.method === 'POST') && !url.includes('/messages')) {
        return new Response(JSON.stringify(mockConv), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.includes('/messages')) {
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
              { id: 'm-1', role: 'user', content: '思考状态测试', created_at: '2026-09-13T10:02:00Z' },
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
    expect(launcher).toHaveAttribute('aria-expanded', 'false')
    expect(launcher).toHaveFocus()
  })

  it('persists open state and draft across remounts via sessionStorage', async () => {
    sessionStorage.setItem('guranovel_assistant_open', 'true')
    sessionStorage.setItem('guranovel_assistant_draft', '草稿暂存文字')

    await act(async () => {
      render(
        <MemoryRouter initialEntries={['/studio']}>
          <Routes>
            <Route path="/studio" element={<Studio />} />
          </Routes>
        </MemoryRouter>
      )
    })

    const input = screen.getByRole('textbox', { name: '给 Gura 的消息' })
    expect(screen.getByRole('dialog', { name: '与 Gura 对话' })).toBeInTheDocument()
    expect(input).toHaveValue('草稿暂存文字')

    fireEvent.change(input, { target: { value: '更新后的暂存文字' } })
    expect(sessionStorage.getItem('guranovel_assistant_draft')).toBe('更新后的暂存文字')

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

    // It should have initialized conversation with inferred project ID 'proj-999'
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/v1/projects/proj-999/assistant/conversations',
      expect.objectContaining({
        method: 'POST',
      })
    )
    expect(screen.getByText('只读助手')).toBeInTheDocument()
  })

  it('renders and toggles correctly on dashboard without studio wrapper', async () => {
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
    expect(launcher.querySelector('svg')).toBeInTheDocument()

    // Open assistant on Dashboard
    await act(async () => {
      fireEvent.click(launcher)
    })

    const assistant = document.querySelector('.studio-assistant')
    expect(assistant).toHaveClass('is-open')
    expect(screen.getByText('交互预览')).toBeInTheDocument()
  })
})
