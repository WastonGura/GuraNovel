import { cleanup, fireEvent, render, screen, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ProjectCreationRun } from './api/client'
import ConceptGate from './ConceptGate'

vi.mock('./api/client', () => ({
  getProjectCreationRun: vi.fn(),
  readDocumentContent: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    code: string
    constructor(status: number, code: string, message: string) {
      super(message)
      this.status = status
      this.code = code
    }
  },
}))

import * as api from './api/client'

const mockedApi = vi.mocked(api)

// ── Helpers ─────────────────────────────────────────────────

const CONCEPT_MARKDOWN = `# Concept Options

## Option \`cyber-ronin\`: 赛博浪人

一名被遗弃在科技废土中的改造人武士，寻找最后的净土。

在近未来日本，赛博改造技术已经普及，但社会崩溃后只剩下废墟和割据的军阀。主人公是一位被遗弃的军用级改造人，体内植入了禁忌的AI核心。

Genres: 赛博朋克, 武侠, 冒险

## Option \`silk-empire\`: 丝绸帝国

一位商队少女在丝绸之路上周旋于帝国之间，用智慧和勇气开辟新的贸易路线。

公元7世纪，丝绸之路最繁荣的时期，一位来自西域的少女继承父亲的商队，在各大帝国之间寻找生存之道。

Genres: 历史, 冒险, 政治

## Option \`void-song\`: 虚空之歌

宇宙深处的音乐信号中隐藏着一个失落文明的最后讯息，一支探险队为此踏上未知旅程。

23世纪，人类在太阳系外发现了第一个外星文明的遗迹——一段无法解读的音乐信号。一支由科学家和探险家组成的队伍被派往信号源头。

Genres: 科幻, 探险, 悬疑
`

function conceptDoc() {
  return { document_id: 'doc-concept', version_id: 'v1', content: CONCEPT_MARKDOWN }
}

function gate(overrides: Partial<ProjectCreationRun> = {}): ProjectCreationRun {
  return {
    id: 'run-1',
    type: 'project_creation',
    status: 'concept_options',
    current_node: 'concept_review',
    next_node: null,
    awaiting_user: true,
    pending_action: {
      id: 'action-1',
      type: 'project_creation_concept_selection',
      status: 'pending',
      allowed_decisions: ['select', 'fuse'],
      review_severity: 'clean',
      concept_document_id: 'doc-concept',
      concept_version_id: 'v1',
      options: ['cyber-ronin', 'silk-empire', 'void-song'],
    },
    ...overrides,
  }
}

function renderGate(props: { projectId?: string; workflowRunId?: string } = {}) {
  return render(
    <MemoryRouter>
      <ConceptGate
        projectId={props.projectId ?? 'project-1'}
        workflowRunId={props.workflowRunId ?? 'run-1'}
      />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
  vi.useRealTimers()
})

// ── Tests ───────────────────────────────────────────────────

describe('ConceptGate', () => {
  it('shows loading skeleton while fetching', () => {
    mockedApi.getProjectCreationRun.mockReturnValue(new Promise(() => undefined))
    renderGate()
    expect(screen.getByText('加载审核关卡…')).toBeInTheDocument()
    const busy = document.querySelector('[aria-busy="true"]')
    expect(busy).not.toBeNull()
    const cards = document.querySelectorAll('.concept-card.skeleton')
    expect(cards.length).toBe(3)
  })

  it('displays 404 error with no retry', async () => {
    mockedApi.getProjectCreationRun.mockRejectedValue(
      new api.ApiError(404, 'not_found', 'Project creation workflow not found.'),
    )
    renderGate()
    expect(await screen.findByRole('alert')).toHaveTextContent('工作流未找到')
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument()
  })

  it('displays network error with retry button', async () => {
    mockedApi.getProjectCreationRun.mockRejectedValue(new Error('Connection refused'))
    renderGate()
    expect(await screen.findByRole('alert')).toHaveTextContent('无法加载审核关卡')
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('retries on network error', async () => {
    mockedApi.getProjectCreationRun
      .mockRejectedValueOnce(new Error('Connection refused'))
      .mockResolvedValueOnce(gate())
    mockedApi.readDocumentContent.mockResolvedValue(conceptDoc())

    renderGate()
    expect(await screen.findByRole('alert')).toHaveTextContent('无法加载审核关卡')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByText('概念方案')).toBeInTheDocument()
    expect(mockedApi.getProjectCreationRun).toHaveBeenCalledTimes(2)
  })

  it('renders clean gate with green badge and concept cards', async () => {
    mockedApi.getProjectCreationRun.mockResolvedValue(gate())
    mockedApi.readDocumentContent.mockResolvedValue(conceptDoc())

    renderGate()

    expect(await screen.findByText('审查通过')).toBeInTheDocument()
    expect(screen.getByText('概念方案')).toBeInTheDocument()
    expect(screen.getByText('赛博浪人')).toBeInTheDocument()
    expect(screen.getByText('赛博朋克')).toBeInTheDocument()
    expect(screen.getByText('丝绸帝国')).toBeInTheDocument()
    expect(screen.getByText('历史')).toBeInTheDocument()
    expect(screen.getByText('虚空之歌')).toBeInTheDocument()
    expect(screen.getByText('科幻')).toBeInTheDocument()
  })

  it('renders warning gate with yellow badge and warnings section', async () => {
    mockedApi.getProjectCreationRun.mockResolvedValue(gate({
      pending_action: {
        id: 'action-1',
        type: 'project_creation_concept_selection',
        status: 'pending',
        allowed_decisions: ['select', 'fuse'],
        review_severity: 'warning',
        concept_document_id: 'doc-concept',
        concept_version_id: 'v1',
        options: ['cyber-ronin'],
      },
    }))
    mockedApi.readDocumentContent.mockResolvedValue(conceptDoc())

    renderGate()

    expect(await screen.findByRole('status')).toHaveTextContent('有建议')
    expect(screen.getByText('审核通过，但首席编辑提供了改进建议。')).toBeInTheDocument()
    expect(screen.getByText('赛博浪人')).toBeInTheDocument()
  })

  it('renders blocking gate with red badge and blocking section', async () => {
    mockedApi.getProjectCreationRun.mockResolvedValue(gate({
      status: 'revision_required',
      current_node: 'concept_regeneration',
      pending_action: {
        id: 'action-1',
        type: 'project_creation_concept_regeneration',
        status: 'pending',
        allowed_decisions: ['regenerate', 'feedback'],
        review_severity: 'blocking',
        concept_document_id: 'doc-concept',
        concept_version_id: 'v1',
        options: undefined,
      },
    }))
    mockedApi.readDocumentContent.mockResolvedValue(conceptDoc())

    renderGate()

    expect(await screen.findByRole('status')).toHaveTextContent('需要修改')
    expect(screen.getByText('首席编辑认为当前概念方案存在问题，需要重新生成。')).toBeInTheDocument()
    expect(screen.getByText('赛博浪人')).toBeInTheDocument()
  })

  it('renders gate without errors when document fetch fails', async () => {
    mockedApi.getProjectCreationRun.mockResolvedValue(gate())
    mockedApi.readDocumentContent.mockRejectedValue(new Error('Document not found'))

    renderGate()

    expect(await screen.findByText('审查通过')).toBeInTheDocument()
    const grid = document.querySelector('.concept-grid')
    expect(grid).toBeInTheDocument()
  })

  it('polls every 5 seconds when awaiting_user and regeneration type', async () => {
    vi.useFakeTimers()

    mockedApi.getProjectCreationRun.mockResolvedValue(gate({
      status: 'revision_required',
      pending_action: {
        id: 'action-1',
        type: 'project_creation_concept_regeneration',
        status: 'pending',
        allowed_decisions: ['regenerate', 'feedback'],
        review_severity: 'blocking',
        concept_document_id: 'doc-concept',
        concept_version_id: 'v1',
      },
    }))
    mockedApi.readDocumentContent.mockResolvedValue(conceptDoc())

    renderGate()

    // Flush initial effects (microtasks) - advance time by 0
    await act(() => vi.advanceTimersByTimeAsync(0))

    expect(mockedApi.getProjectCreationRun).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('status')).toHaveTextContent('需要修改')

    // Advance 5 seconds → setInterval fires
    await act(() => vi.advanceTimersByTimeAsync(5000))
    expect(mockedApi.getProjectCreationRun).toHaveBeenCalledTimes(2)

    // Advance another 5 seconds
    await act(() => vi.advanceTimersByTimeAsync(5000))
    expect(mockedApi.getProjectCreationRun).toHaveBeenCalledTimes(3)
  })

  it('does not poll when awaiting_user but selection type', async () => {
    vi.useFakeTimers()

    mockedApi.getProjectCreationRun.mockResolvedValue(gate())
    mockedApi.readDocumentContent.mockResolvedValue(conceptDoc())

    renderGate()

    await act(() => vi.advanceTimersByTimeAsync(0))

    expect(screen.getByText('审查通过')).toBeInTheDocument()
    expect(mockedApi.getProjectCreationRun).toHaveBeenCalledTimes(1)

    await act(() => vi.advanceTimersByTimeAsync(5000))
    expect(mockedApi.getProjectCreationRun).toHaveBeenCalledTimes(1)
  })

  it('stops polling when gate transitions away from regeneration', async () => {
    vi.useFakeTimers()

    mockedApi.getProjectCreationRun
      .mockResolvedValueOnce(gate({
        status: 'revision_required',
        pending_action: {
          id: 'action-1',
          type: 'project_creation_concept_regeneration',
          status: 'pending',
          allowed_decisions: ['regenerate', 'feedback'],
          review_severity: 'blocking',
          concept_document_id: 'doc-concept',
          concept_version_id: 'v1',
        },
      }))
      .mockResolvedValueOnce(gate())

    mockedApi.readDocumentContent.mockResolvedValue(conceptDoc())

    renderGate()

    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(screen.getByRole('status')).toHaveTextContent('需要修改')
    expect(mockedApi.getProjectCreationRun).toHaveBeenCalledTimes(1)

    // Advance 5s → poll fires, gets selection type → stops polling
    await act(() => vi.advanceTimersByTimeAsync(5000))
    expect(mockedApi.getProjectCreationRun).toHaveBeenCalledTimes(2)

    // No more polling after this
    await act(() => vi.advanceTimersByTimeAsync(5000))
    expect(mockedApi.getProjectCreationRun).toHaveBeenCalledTimes(2)
  })

  it('parses all genre tags across cards', async () => {
    mockedApi.getProjectCreationRun.mockResolvedValue(gate())
    mockedApi.readDocumentContent.mockResolvedValue(conceptDoc())

    renderGate()

    await screen.findByText('审查通过')
    // Some genres appear in multiple cards — use getAllByText
    expect(screen.getAllByText('冒险').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('赛博朋克')).toBeInTheDocument()
    expect(screen.getByText('武侠')).toBeInTheDocument()
    expect(screen.getByText('历史')).toBeInTheDocument()
    expect(screen.getByText('政治')).toBeInTheDocument()
    expect(screen.getByText('科幻')).toBeInTheDocument()
    expect(screen.getByText('探险')).toBeInTheDocument()
    expect(screen.getByText('悬疑')).toBeInTheDocument()
  })

  it('refetches when projectId or workflowRunId changes', async () => {
    mockedApi.getProjectCreationRun.mockResolvedValue(gate())
    mockedApi.readDocumentContent.mockResolvedValue(conceptDoc())

    const { rerender } = renderGate()
    await screen.findByText('审查通过')

    mockedApi.getProjectCreationRun.mockResolvedValue(gate({
      pending_action: {
        id: 'action-2',
        type: 'project_creation_concept_selection',
        status: 'pending',
        allowed_decisions: ['select', 'fuse'],
        review_severity: 'warning',
        concept_document_id: 'doc-concept-2',
        concept_version_id: 'v2',
        options: ['void-song'],
      },
    }))

    rerender(
      <MemoryRouter>
        <ConceptGate projectId="project-2" workflowRunId="run-2" />
      </MemoryRouter>,
    )

    // The badge shows "有建议" with role="status"
    expect(await screen.findByRole('status')).toHaveTextContent('有建议')
    expect(mockedApi.getProjectCreationRun).toHaveBeenCalledTimes(2)
  })
})
