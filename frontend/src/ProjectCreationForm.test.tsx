import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ProjectCreationStarted } from './api/client'
import ProjectCreationForm from './ProjectCreationForm'

vi.mock('./api/client', () => ({
  startProjectCreation: vi.fn(),
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

function creationStarted(overrides: Partial<ProjectCreationStarted> = {}): ProjectCreationStarted {
  return {
    id: 'run-1',
    status: 'pending',
    pending_action: {
      id: 'action-1',
      type: 'review_concept',
      status: 'pending',
      allowed_decisions: ['approved', 'rejected'],
      review_severity: 'standard',
      concept_options: [],
    },
    ...overrides,
  }
}

function Location() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function getSeedTextarea() {
  return screen.getByRole('textbox', { name: /创作灵感/ })
}

function getTargetPlatformInput() {
  return screen.getByRole('textbox', { name: /目标平台/ })
}

function getStylePreferenceInput() {
  return screen.getByRole('textbox', { name: /风格偏好/ })
}

function getPreferredGenresInput() {
  return screen.getByRole('textbox', { name: /偏好类型/ })
}

function getDislikedElementsInput() {
  return screen.getByRole('textbox', { name: /不喜欢的元素/ })
}

function renderForm(projectId = 'project-1') {
  return render(
    <MemoryRouter initialEntries={[`/projects/${projectId}/creation/start`]}>
      <ProjectCreationForm projectId={projectId} />
      <Location />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe('ProjectCreationForm', () => {
  it('renders all form fields with Chinese labels', () => {
    renderForm()
    expect(getSeedTextarea()).toBeInTheDocument()
    expect(getTargetPlatformInput()).toBeInTheDocument()
    expect(getPreferredGenresInput()).toBeInTheDocument()
    expect(getDislikedElementsInput()).toBeInTheDocument()
    expect(getStylePreferenceInput()).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始创作' })).toBeInTheDocument()
  })

  it('shows character count on user_seed textarea', () => {
    renderForm()
    expect(screen.getByText('0 / 4000')).toBeInTheDocument()
    fireEvent.change(getSeedTextarea(), { target: { value: 'Hello' } })
    expect(screen.getByText('5 / 4000')).toBeInTheDocument()
  })

  it('truncates user_seed at 4000 characters', () => {
    renderForm()
    const textarea = getSeedTextarea()
    fireEvent.change(textarea, { target: { value: 'a'.repeat(5000) } })
    expect(textarea).toHaveValue('a'.repeat(4000))
    expect(screen.getByText('4000 / 4000')).toBeInTheDocument()
  })

  it('calls startProjectCreation with correctly parsed data', async () => {
    mockedApi.startProjectCreation.mockResolvedValue(creationStarted())
    renderForm()
    fireEvent.change(getSeedTextarea(), { target: { value: '一个关于龙的故事' } })
    fireEvent.change(getTargetPlatformInput(), { target: { value: '起点中文网' } })
    fireEvent.change(getPreferredGenresInput(), { target: { value: '奇幻, 冒险' } })
    fireEvent.change(getDislikedElementsInput(), { target: { value: '后宫, 系统流' } })
    fireEvent.change(getStylePreferenceInput(), { target: { value: '轻松幽默' } })
    fireEvent.click(screen.getByRole('button', { name: '开始创作' }))

    await waitFor(() => expect(mockedApi.startProjectCreation).toHaveBeenCalledWith('project-1', {
      user_seed: '一个关于龙的故事',
      target_platform: '起点中文网',
      preferred_genres: ['奇幻', '冒险'],
      disliked_elements: ['后宫', '系统流'],
      style_preference: '轻松幽默',
    }))
  })

  it('navigates to gate page on success', async () => {
    mockedApi.startProjectCreation.mockResolvedValue(creationStarted({ id: 'run-42' }))
    renderForm()
    fireEvent.change(getSeedTextarea(), { target: { value: '一个故事' } })
    fireEvent.click(screen.getByRole('button', { name: '开始创作' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/projects/project-1/creation/run-42/gate'))
  })

  it('shows 409 conflict error message', async () => {
    mockedApi.startProjectCreation.mockRejectedValue(new api.ApiError(409, 'conflict', ''))
    renderForm()
    fireEvent.change(getSeedTextarea(), { target: { value: '一个故事' } })
    fireEvent.click(screen.getByRole('button', { name: '开始创作' }))
    expect(await screen.findByText('项目已有活跃的创建工作流。')).toBeInTheDocument()
  })

  it('shows 422 validation error message', async () => {
    mockedApi.startProjectCreation.mockRejectedValue(new api.ApiError(422, 'validation_error', 'user_seed 不能为空'))
    renderForm()
    fireEvent.change(getSeedTextarea(), { target: { value: '一个故事' } })
    fireEvent.click(screen.getByRole('button', { name: '开始创作' }))
    expect(await screen.findByText('user_seed 不能为空')).toBeInTheDocument()
  })

  it('disables button and shows loading text while pending', async () => {
    mockedApi.startProjectCreation.mockReturnValue(new Promise(() => undefined))
    renderForm()
    fireEvent.change(getSeedTextarea(), { target: { value: '一个故事' } })
    const button = screen.getByRole('button', { name: '开始创作' })
    fireEvent.click(button)
    expect(button).toBeDisabled()
    expect(screen.getByText('正在启动创作…')).toBeInTheDocument()
  })

  it('prevents duplicate submission while pending', async () => {
    mockedApi.startProjectCreation.mockReturnValue(new Promise(() => undefined))
    renderForm()
    fireEvent.change(getSeedTextarea(), { target: { value: '一个故事' } })
    const button = screen.getByRole('button', { name: '开始创作' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(mockedApi.startProjectCreation).toHaveBeenCalledTimes(1)
  })

  it('shows error when user_seed is empty', async () => {
    renderForm()
    fireEvent.click(screen.getByRole('button', { name: '开始创作' }))
    expect(await screen.findByText('请填写创作灵感。')).toBeInTheDocument()
    expect(mockedApi.startProjectCreation).not.toHaveBeenCalled()
  })

  it('sends null for empty optional fields', async () => {
    mockedApi.startProjectCreation.mockResolvedValue(creationStarted())
    renderForm()
    fireEvent.change(getSeedTextarea(), { target: { value: '一个故事' } })
    fireEvent.click(screen.getByRole('button', { name: '开始创作' }))
    await waitFor(() => expect(mockedApi.startProjectCreation).toHaveBeenCalledWith('project-1', {
      user_seed: '一个故事',
      target_platform: null,
      preferred_genres: [],
      disliked_elements: [],
      style_preference: null,
    }))
  })
})
