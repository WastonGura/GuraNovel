import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import SettingUndoToast from './SettingUndoToast'

beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false }))) })
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

it('never steals focus and pauses auto-hide while the pointer or keyboard is inside', () => {
  const onDismiss = vi.fn()
  render(<><input aria-label="正文" autoFocus /><SettingUndoToast operation={{ id: 'first', ids: ['a'] }} onUndo={vi.fn()} onDismiss={onDismiss} durationMs={5000} /></>)
  const notice = screen.getByRole('status')
  expect(screen.getByRole('textbox')).toHaveFocus()
  act(() => { vi.advanceTimersByTime(2000) })
  fireEvent.pointerEnter(notice)
  act(() => { vi.advanceTimersByTime(10000) })
  expect(notice).not.toHaveClass('is-closing')
  fireEvent.focus(screen.getByRole('button', { name: '撤销' }))
  fireEvent.pointerLeave(notice)
  act(() => { vi.advanceTimersByTime(10000) })
  expect(notice).not.toHaveClass('is-closing')
  fireEvent.blur(screen.getByRole('button', { name: '撤销' }), { relatedTarget: screen.getByRole('textbox') })
  act(() => { vi.advanceTimersByTime(2999) })
  expect(notice).not.toHaveClass('is-closing')
  act(() => { vi.advanceTimersByTime(1) })
  expect(notice).toHaveClass('is-closing')
  act(() => { vi.advanceTimersByTime(400) })
  expect(onDismiss).toHaveBeenCalledTimes(1)
})

it('retries only failed undo IDs and keeps failure visible until it can be handled', async () => {
  const onUndo = vi.fn().mockResolvedValueOnce(['a']).mockResolvedValueOnce(['b']), onDismiss = vi.fn()
  render(<SettingUndoToast operation={{ id: 'first', ids: ['a', 'b'] }} onUndo={onUndo} onDismiss={onDismiss} />)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '撤销' })) })
  expect(screen.getByRole('alert')).toHaveTextContent('1 项撤销结果未能确认')
  act(() => { vi.advanceTimersByTime(20000) })
  expect(onDismiss).not.toHaveBeenCalled()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重试撤销' })) })
  act(() => { vi.advanceTimersByTime(400) })
  expect(onUndo.mock.calls.map(call => call[0])).toEqual([['a', 'b'], ['b']])
  expect(onDismiss).toHaveBeenCalledTimes(1)
})

it('shows only the latest operation and ignores a previous pending undo when it completes', async () => {
  let finish!: (ids: string[]) => void
  const onUndo = vi.fn<(ids: string[]) => Promise<string[]>>(() => new Promise<string[]>(resolve => { finish = resolve })), onDismiss = vi.fn()
  const view = render(<SettingUndoToast operation={{ id: 'first', ids: ['a'] }} onUndo={onUndo} onDismiss={onDismiss} />)
  fireEvent.click(screen.getByRole('button', { name: '撤销' }))
  view.rerender(<SettingUndoToast operation={{ id: 'second', ids: ['b'] }} onUndo={onUndo} onDismiss={onDismiss} />)
  await act(async () => { finish(['a']) })
  expect(onDismiss).not.toHaveBeenCalled()
  expect(screen.getAllByRole('status')).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: '撤销' }))
  expect(onUndo.mock.calls.map(call => call[0])).toEqual([['a'], ['b']])
})

it('prevents repeated undo while pending and dismisses immediately with reduced motion', async () => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
  let reject!: (error: Error) => void
  const onUndo = vi.fn<(ids: string[]) => Promise<string[]>>(() => new Promise<string[]>((_, fail) => { reject = fail })), onDismiss = vi.fn()
  render(<SettingUndoToast operation={{ id: 'one', ids: ['a'] }} onUndo={onUndo} onDismiss={onDismiss} />)
  const undo = screen.getByRole('button', { name: '撤销' })
  fireEvent.click(undo)
  fireEvent.click(undo)
  expect(onUndo).toHaveBeenCalledTimes(1)
  await act(async () => { reject(new Error('offline')) })
  expect(screen.getByRole('alert')).toHaveTextContent('撤销结果未能确认')
  fireEvent.click(screen.getByRole('button', { name: '关闭删除提示' }))
  expect(onDismiss).toHaveBeenCalledTimes(1)
})
