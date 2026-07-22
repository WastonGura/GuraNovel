import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ConceptSelection from './ConceptSelection'

vi.mock('./api/client', () => ({
  resolveProjectCreationAction: vi.fn(),
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

const defaultProps = {
  projectId: 'project-1',
  workflowRunId: 'run-1',
  actionId: 'action-1',
  allowedDecisions: ['select', 'fuse'],
  options: ['glass-archive', 'iron-kingdom', 'crystal-realm'],
  onResolved: vi.fn(),
}

function renderConceptSelection(props: Partial<typeof defaultProps> = {}) {
  const merged = { ...defaultProps, ...props }
  return render(<ConceptSelection {...merged} />)
}

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('ConceptSelection', () => {
  it('renders the concept selection heading and description', () => {
    renderConceptSelection()
    expect(screen.getByRole('heading', { name: '概念选择' })).toBeInTheDocument()
    expect(screen.getByText('请选择一个概念方案，或融合多个概念创作自定义方案。')).toBeInTheDocument()
  })

  it('renders mode toggle when both select and fuse are allowed', () => {
    renderConceptSelection()
    expect(screen.getByRole('tab', { name: '选择概念' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '自定义融合' })).toBeInTheDocument()
  })

  it('does not render mode toggle when only select is allowed', () => {
    renderConceptSelection({ allowedDecisions: ['select'] })
    expect(screen.queryByRole('tab', { name: '自定义融合' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '选择这个概念' })).toBeInTheDocument()
  })

  it('defaults to select mode and renders concept option cards', () => {
    renderConceptSelection()
    const cards = screen.getAllByRole('button', { name: /glass-archive|iron-kingdom|crystal-realm/ })
    expect(cards).toHaveLength(3)
    expect(screen.getByRole('button', { name: '选择这个概念' })).toBeDisabled()
  })

  it('highlights a selected concept card with blue border and enables the select button', () => {
    renderConceptSelection()
    const card = screen.getByRole('button', { name: 'glass-archive' })
    fireEvent.click(card)

    expect(card).toHaveClass('selected')
    expect(card).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '选择这个概念' })).toBeEnabled()
  })

  it('allows changing the selected concept card', () => {
    renderConceptSelection()
    fireEvent.click(screen.getByRole('button', { name: 'glass-archive' }))
    fireEvent.click(screen.getByRole('button', { name: 'crystal-realm' }))

    expect(screen.getByRole('button', { name: 'glass-archive' })).not.toHaveClass('selected')
    expect(screen.getByRole('button', { name: 'crystal-realm' })).toHaveClass('selected')
  })

  it('switches to fuse mode and shows textarea with character count', () => {
    renderConceptSelection()
    fireEvent.click(screen.getByRole('tab', { name: '自定义融合' }))

    expect(screen.getByRole('tabpanel', { name: '自定义融合概念' })).toBeInTheDocument()
    const textarea = screen.getByPlaceholderText('请输入您的融合概念方案（1–4000 字）')
    expect(textarea).toBeInTheDocument()
    expect(screen.getByText('0/4000')).toBeInTheDocument()
  })

  it('updates character count when typing in fuse textarea', () => {
    renderConceptSelection()
    fireEvent.click(screen.getByRole('tab', { name: '自定义融合' }))

    const textarea = screen.getByPlaceholderText('请输入您的融合概念方案（1–4000 字）')
    fireEvent.change(textarea, { target: { value: '一个融合概念方案' } })
    expect(screen.getByText('8/4000')).toBeInTheDocument()
  })

  it('disables fuse submit button when textarea is empty', () => {
    renderConceptSelection()
    fireEvent.click(screen.getByRole('tab', { name: '自定义融合' }))
    expect(screen.getByRole('button', { name: '提交融合概念' })).toBeDisabled()
  })

  it('enables fuse submit button when textarea has content', () => {
    renderConceptSelection()
    fireEvent.click(screen.getByRole('tab', { name: '自定义融合' }))

    const textarea = screen.getByPlaceholderText('请输入您的融合概念方案（1–4000 字）')
    fireEvent.change(textarea, { target: { value: '我的自定义概念' } })
    expect(screen.getByRole('button', { name: '提交融合概念' })).toBeEnabled()
  })

  it('calls resolveProjectCreationAction with select decision on submit', async () => {
    mockedApi.resolveProjectCreationAction.mockResolvedValue({ status: 'concept_selected' })
    renderConceptSelection()

    fireEvent.click(screen.getByRole('button', { name: 'glass-archive' }))
    fireEvent.click(screen.getByRole('button', { name: '选择这个概念' }))

    await waitFor(() => {
      expect(mockedApi.resolveProjectCreationAction).toHaveBeenCalledWith(
        'project-1',
        'run-1',
        'action-1',
        { decision: 'select', option_id: 'glass-archive' },
      )
    })
  })

  it('calls resolveProjectCreationAction with fuse decision on submit', async () => {
    mockedApi.resolveProjectCreationAction.mockResolvedValue({ status: 'concept_selected' })
    renderConceptSelection()

    fireEvent.click(screen.getByRole('tab', { name: '自定义融合' }))
    const textarea = screen.getByPlaceholderText('请输入您的融合概念方案（1–4000 字）')
    fireEvent.change(textarea, { target: { value: '自定义融合概念文本' } })
    fireEvent.click(screen.getByRole('button', { name: '提交融合概念' }))

    await waitFor(() => {
      expect(mockedApi.resolveProjectCreationAction).toHaveBeenCalledWith(
        'project-1',
        'run-1',
        'action-1',
        { decision: 'fuse', fused_concept: '自定义融合概念文本' },
      )
    })
  })

  it('calls onResolved when API returns concept_selected status', async () => {
    const onResolved = vi.fn()
    mockedApi.resolveProjectCreationAction.mockResolvedValue({ status: 'concept_selected' })
    renderConceptSelection({ onResolved })

    fireEvent.click(screen.getByRole('button', { name: 'glass-archive' }))
    fireEvent.click(screen.getByRole('button', { name: '选择这个概念' }))

    await waitFor(() => {
      expect(onResolved).toHaveBeenCalledTimes(1)
    })
  })

  it('shows loading state while API call is in progress', async () => {
    let resolveApi!: (value: { status: string }) => void
    mockedApi.resolveProjectCreationAction.mockReturnValue(
      new Promise((resolve) => { resolveApi = resolve }),
    )
    renderConceptSelection()

    fireEvent.click(screen.getByRole('button', { name: 'glass-archive' }))
    fireEvent.click(screen.getByRole('button', { name: '选择这个概念' }))

    expect(screen.getByRole('button', { name: '提交中…' })).toBeDisabled()

    resolveApi({ status: 'concept_selected' })
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '提交中…' })).not.toBeInTheDocument()
    })
  })

  it('displays error for 409 conflict', async () => {
    mockedApi.resolveProjectCreationAction.mockRejectedValue(
      new api.ApiError(409, 'action_already_resolved', 'Action already resolved'),
    )
    renderConceptSelection()

    fireEvent.click(screen.getByRole('button', { name: 'glass-archive' }))
    fireEvent.click(screen.getByRole('button', { name: '选择这个概念' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('该操作已被处理，无法重复提交。')
  })

  it('displays generic error for non-409 failures', async () => {
    mockedApi.resolveProjectCreationAction.mockRejectedValue(new Error('Network error'))
    renderConceptSelection()

    fireEvent.click(screen.getByRole('button', { name: 'glass-archive' }))
    fireEvent.click(screen.getByRole('button', { name: '选择这个概念' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('操作失败，请重试。')
  })

  it('prevents duplicate submission while pending in select mode', async () => {
    mockedApi.resolveProjectCreationAction.mockReturnValue(new Promise(() => undefined))
    renderConceptSelection()

    fireEvent.click(screen.getByRole('button', { name: 'glass-archive' }))
    const submitButton = screen.getByRole('button', { name: '选择这个概念' })
    fireEvent.click(submitButton)
    fireEvent.click(submitButton)

    expect(mockedApi.resolveProjectCreationAction).toHaveBeenCalledTimes(1)
  })

  it('prevents duplicate submission while pending in fuse mode', async () => {
    mockedApi.resolveProjectCreationAction.mockReturnValue(new Promise(() => undefined))
    renderConceptSelection()

    fireEvent.click(screen.getByRole('tab', { name: '自定义融合' }))
    const textarea = screen.getByPlaceholderText('请输入您的融合概念方案（1–4000 字）')
    fireEvent.change(textarea, { target: { value: '融合概念' } })
    const submitButton = screen.getByRole('button', { name: '提交融合概念' })
    fireEvent.click(submitButton)
    fireEvent.click(submitButton)

    expect(mockedApi.resolveProjectCreationAction).toHaveBeenCalledTimes(1)
  })

  it('trims whitespace from fused concept before submitting', async () => {
    mockedApi.resolveProjectCreationAction.mockResolvedValue({ status: 'concept_selected' })
    renderConceptSelection()

    fireEvent.click(screen.getByRole('tab', { name: '自定义融合' }))
    const textarea = screen.getByPlaceholderText('请输入您的融合概念方案（1–4000 字）')
    fireEvent.change(textarea, { target: { value: '  带空白的融合概念  ' } })
    fireEvent.click(screen.getByRole('button', { name: '提交融合概念' }))

    await waitFor(() => {
      expect(mockedApi.resolveProjectCreationAction).toHaveBeenCalledWith(
        'project-1',
        'run-1',
        'action-1',
        { decision: 'fuse', fused_concept: '带空白的融合概念' },
      )
    })
  })

  it('defaults to fuse mode when select is not allowed', () => {
    renderConceptSelection({ allowedDecisions: ['fuse'] })
    expect(screen.getByRole('tabpanel', { name: '自定义融合概念' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '选择这个概念' })).not.toBeInTheDocument()
  })
})
