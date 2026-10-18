import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import StudioOutlineHistory from './StudioOutlineHistory'
import type { OutlineGeneration } from './api/client'

afterEach(cleanup)
const batches = [
  { id: 'new', started_at: '2026-10-16T01:00:00Z', status: 'completed', candidates: [{ id: 'new-option' }] },
  { id: 'old', started_at: '2026-10-15T01:00:00Z', status: 'completed', candidates: [{ id: 'old-option' }] },
] as OutlineGeneration[]

it('browses adjacent batches chronologically with a disabled arrow at each boundary', () => {
  const onSelect = vi.fn()
  const view = render(<StudioOutlineHistory batches={batches} activeId="new" onSelect={onSelect} />)
  const history = screen.getByRole('navigation', { name: '大纲历史批次' })
  expect(within(history).getByRole('status')).toHaveTextContent('第 2 / 2 批')
  expect(within(history).getByRole('button', { name: '查看下一批大纲' })).toBeDisabled()
  fireEvent.click(within(history).getByRole('button', { name: '查看上一批大纲' }))
  expect(onSelect).toHaveBeenCalledExactlyOnceWith('old')
  view.rerender(<StudioOutlineHistory batches={batches} activeId="old" onSelect={onSelect} />)
  expect(within(history).getByRole('status')).toHaveTextContent('第 1 / 2 批')
  expect(within(history).getByRole('button', { name: '查看上一批大纲' })).toBeDisabled()
  fireEvent.click(within(history).getByRole('button', { name: '查看下一批大纲' }))
  expect(onSelect).toHaveBeenLastCalledWith('new')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('keeps both arrows disabled when only one batch exists', () => {
  const onSelect = vi.fn()
  render(<StudioOutlineHistory batches={[batches[0]]} activeId="new" onSelect={onSelect} />)
  expect(screen.getByRole('status')).toHaveTextContent('第 1 / 1 批')
  for (const arrow of screen.getAllByRole('button')) { expect(arrow).toBeDisabled(); fireEvent.click(arrow) }
  expect(onSelect).not.toHaveBeenCalled()
})

it('does not choose an arbitrary batch if the active batch is missing', () => {
  const onSelect = vi.fn()
  render(<StudioOutlineHistory batches={batches} activeId="missing" onSelect={onSelect} />)
  for (const arrow of screen.getAllByRole('button')) { expect(arrow).toBeDisabled(); fireEvent.click(arrow) }
  expect(onSelect).not.toHaveBeenCalled()
})

it('counts only completed groups with results, excluding generation attempts', () => {
  const attempts = (['failed', 'running', 'uncertain', 'cancelled', 'completed'] as const).map(status => ({
    ...batches[0], id: status, status, started_at: '2026-10-17T00:00:00Z', candidates: [],
  }))
  render(<StudioOutlineHistory batches={[...batches, ...attempts]} activeId="new" onSelect={vi.fn()} />)
  expect(screen.getByRole('status')).toHaveTextContent('第 2 / 2 批')
  expect(screen.getByRole('button', { name: '查看下一批大纲' })).toBeDisabled()
})
