import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAssistantResize } from './useAssistantResize'

const key = 'guranovel_assistant_size'

class TestPointerEvent extends MouseEvent {
  readonly pointerId: number
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init)
    this.pointerId = init.pointerId ?? 1
  }
}

function Example({ enabled = true, right, bottom }: { enabled?: boolean; right?: number; bottom?: number }) {
  const resize = useAssistantResize({ enabled, right, bottom })
  return <section data-testid="panel" style={resize.style} data-resizing={resize.isResizing}>
    {(['left', 'top', 'corner'] as const).map(edge => <button key={edge} {...resize.getHandleProps(edge)} />)}
  </section>
}

function dimensions() {
  const style = screen.getByTestId('panel').style
  return { width: parseFloat(style.getPropertyValue('--assistant-width')), height: parseFloat(style.getPropertyValue('--assistant-height')) }
}

function viewport(width: number, height: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: height })
  act(() => { window.dispatchEvent(new Event('resize')) })
}

describe('Gura independent panel sizing', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubGlobal('PointerEvent', TestPointerEvent)
    viewport(1440, 1000)
  })
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

  it('grows the left edge without changing height and commits only after release', () => {
    render(<Example />)
    const original = dimensions()
    const handle = screen.getByRole('separator', { name: '调整 Gura 宽度' })
    const capture = vi.fn(), release = vi.fn()
    handle.setPointerCapture = capture
    handle.releasePointerCapture = release
    handle.hasPointerCapture = () => true
    fireEvent.pointerDown(handle, { button: 0, clientX: 1000, clientY: 400, pointerId: 7 })
    fireEvent.pointerMove(window, { clientX: 880, clientY: 300, pointerId: 7 })
    expect(dimensions().width).toBe(original.width + 120)
    expect(dimensions().height).toBe(original.height)
    expect(localStorage.getItem(key)).toBeNull()
    expect(capture).toHaveBeenCalledWith(7)
    fireEvent.pointerUp(window, { pointerId: 7 })
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual(dimensions())
    expect(release).toHaveBeenCalledWith(7)
    expect(screen.getByTestId('panel')).toHaveAttribute('data-resizing', 'false')
  })

  it('resizes height independently, then both dimensions from the corner', () => {
    render(<Example />)
    const original = dimensions()
    fireEvent.pointerDown(screen.getByRole('separator', { name: '调整 Gura 高度' }), { button: 0, clientX: 500, clientY: 500 })
    fireEvent.pointerMove(window, { clientX: 300, clientY: 410 })
    fireEvent.pointerUp(window)
    expect(dimensions()).toEqual({ width: original.width, height: original.height + 90 })
    fireEvent.pointerDown(screen.getByRole('button', { name: '调整 Gura 大小' }), { button: 0, clientX: 500, clientY: 500 })
    fireEvent.pointerMove(window, { clientX: 450, clientY: 470 })
    fireEvent.pointerUp(window)
    expect(dimensions()).toEqual({ width: original.width + 50, height: original.height + 120 })
  })

  it('stops dragging at the desktop panel limits instead of filling the screen', () => {
    render(<Example />)
    fireEvent.pointerDown(screen.getByRole('button', { name: '调整 Gura 大小' }), { button: 0, clientX: 500, clientY: 500 })
    fireEvent.pointerMove(window, { clientX: -2000, clientY: -2000 })
    fireEvent.pointerUp(window)
    expect(dimensions()).toEqual({ width: 560, height: 720 })
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual({ width: 560, height: 720 })
  })

  it('uses the existing responsive baseline as the minimum and keeps the panel inside the viewport', () => {
    viewport(800, 700)
    render(<Example right={30} bottom={40} />)
    const baseline = dimensions()
    const corner = screen.getByRole('button', { name: '调整 Gura 大小' })
    fireEvent.pointerDown(corner, { button: 0, clientX: 400, clientY: 400 })
    fireEvent.pointerMove(window, { clientX: -2000, clientY: -2000 })
    expect(dimensions()).toEqual({ width: 360, height: 560 })
    fireEvent.pointerMove(window, { clientX: 3000, clientY: 3000 })
    expect(dimensions()).toEqual(baseline)
    fireEvent.pointerUp(window)
    viewport(390, 600)
    expect(dimensions()).toEqual({ width: 285, height: 320 * 4 / 3 })
    viewport(390, 500)
    expect(dimensions()).toEqual({ width: 210, height: 400 })
    cleanup()
    localStorage.clear()
    render(<Example />)
    expect(dimensions()).toEqual({ width: 210, height: 280 })
  })

  it('restores preferred independent dimensions after a smaller viewport grows, including after remount', () => {
    localStorage.setItem(key, JSON.stringify({ width: 500, height: 650 }))
    const view = render(<Example />)
    expect(dimensions()).toEqual({ width: 500, height: 650 })
    viewport(600, 650)
    expect(dimensions()).toEqual({ width: 320, height: 520 })
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual({ width: 500, height: 650 })
    view.unmount()
    render(<Example />)
    viewport(1440, 1000)
    expect(dimensions()).toEqual({ width: 500, height: 650 })
  })

  it('supports keyboard arrows, accelerated steps and reset while preserving the untouched axis preference', () => {
    localStorage.setItem(key, JSON.stringify({ width: 500, height: 650 }))
    viewport(800, 650)
    render(<Example />)
    const width = screen.getByRole('separator', { name: '调整 Gura 宽度' })
    const height = screen.getByRole('separator', { name: '调整 Gura 高度' })
    const before = dimensions()
    fireEvent.keyDown(width, { key: 'ArrowRight', shiftKey: true })
    expect(dimensions().width).toBe(before.width - 40)
    expect(JSON.parse(localStorage.getItem(key)!).height).toBe(650)
    viewport(1440, 1000)
    expect(dimensions().height).toBe(650)
    fireEvent.keyDown(width, { key: 'ArrowDown' })
    expect(dimensions().height).toBe(650)
    fireEvent.keyDown(height, { key: 'ArrowUp' })
    expect(dimensions().height).toBe(660)
    expect(height).toHaveAttribute('aria-valuenow', '660')
    fireEvent.doubleClick(width)
    expect(dimensions()).toEqual({ width: 320, height: 320 * 4 / 3 })
    expect(localStorage.getItem(key)).toBeNull()
    for (let count = 0; count < 10; count++) fireEvent.keyDown(width, { key: 'ArrowRight', shiftKey: true })
    expect(dimensions().width).toBe(320)
  })

  it('applies the same limits to keyboard resizing and to oversized saved preferences', () => {
    localStorage.setItem(key, JSON.stringify({ width: 1200, height: 1000 }))
    render(<Example />)
    expect(dimensions()).toEqual({ width: 560, height: 720 })
    viewport(800, 700)
    expect(dimensions()).toEqual({ width: 360, height: 560 })
    viewport(1440, 1000)
    const width = screen.getByRole('separator', { name: '调整 Gura 宽度' })
    const height = screen.getByRole('separator', { name: '调整 Gura 高度' })
    fireEvent.doubleClick(width)
    for (let count = 0; count < 20; count++) {
      fireEvent.keyDown(width, { key: 'ArrowLeft', shiftKey: true })
      fireEvent.keyDown(height, { key: 'ArrowUp', shiftKey: true })
    }
    expect(dimensions()).toEqual({ width: 560, height: 720 })
    expect(width).toHaveAttribute('aria-valuemax', '560')
    expect(height).toHaveAttribute('aria-valuemax', '720')
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual({ width: 560, height: 720 })
  })

  it('prioritizes the responsive minimum over percentage limits when the window is small', () => {
    viewport(600, 650)
    render(<Example />)
    const initial = dimensions()
    expect(initial).toEqual({ width: 320, height: 320 * 4 / 3 })
    const corner = screen.getByRole('button', { name: '调整 Gura 大小' })
    fireEvent.pointerDown(corner, { button: 0, clientX: 400, clientY: 400 })
    fireEvent.pointerMove(window, { clientX: -2000, clientY: -2000 })
    fireEvent.pointerUp(window)
    expect(dimensions()).toEqual({ width: 320, height: 520 })
    viewport(320, 360)
    expect(dimensions()).toEqual({ width: 144, height: 288 })
  })

  it('cancels on pointer cancellation or Escape and ignores a second pointer', () => {
    render(<Example />)
    const handle = screen.getByRole('button', { name: '调整 Gura 大小' })
    const original = dimensions()
    for (const cancel of ['pointercancel', 'Escape', 'lostpointercapture', 'blur']) {
      fireEvent.pointerDown(handle, { button: 0, clientX: 500, clientY: 500, pointerId: 2 })
      fireEvent.pointerMove(window, { clientX: 400, clientY: 400, pointerId: 3 })
      fireEvent.pointerUp(window, { pointerId: 3 })
      expect(dimensions()).toEqual(original)
      expect(screen.getByTestId('panel')).toHaveAttribute('data-resizing', 'true')
      fireEvent.pointerMove(window, { clientX: 400, clientY: 400, pointerId: 2 })
      if (cancel === 'Escape') fireEvent.keyDown(window, { key: 'Escape' })
      else if (cancel === 'lostpointercapture') fireEvent(handle, new TestPointerEvent('lostpointercapture', { pointerId: 2 }))
      else if (cancel === 'blur') fireEvent.blur(window)
      else fireEvent.pointerCancel(window, { pointerId: 2 })
      expect(dimensions()).toEqual(original)
      expect(screen.getByTestId('panel')).toHaveAttribute('data-resizing', 'false')
      expect(localStorage.getItem(key)).toBeNull()
    }
    fireEvent.pointerMove(window, { clientX: 0, clientY: 0, pointerId: 2 })
    expect(dimensions()).toEqual(original)
  })

  it('releases capture and listeners when disabled or unmounted without persisting an interrupted drag', () => {
    const view = render(<Example />)
    const handle = screen.getByRole('button', { name: '调整 Gura 大小' })
    const release = vi.fn()
    handle.setPointerCapture = vi.fn()
    handle.releasePointerCapture = release
    handle.hasPointerCapture = () => true
    fireEvent.pointerDown(handle, { button: 0, clientX: 500, clientY: 500 })
    fireEvent.pointerMove(window, { clientX: 400, clientY: 400 })
    view.rerender(<Example enabled={false} />)
    expect(release).toHaveBeenCalledOnce()
    expect(screen.getByTestId('panel')).toHaveAttribute('data-resizing', 'false')
    expect(handle).toHaveAttribute('tabindex', '-1')
    fireEvent.keyDown(handle, { key: 'ArrowLeft' })
    fireEvent.pointerMove(window, { clientX: 0, clientY: 0 })
    expect(dimensions().width).toBe(320)
    view.rerender(<Example />)
    fireEvent.pointerDown(handle, { button: 0, clientX: 500, clientY: 500 })
    view.unmount()
    expect(release).toHaveBeenCalledTimes(2)
    fireEvent.pointerUp(window)
    expect(localStorage.getItem(key)).toBeNull()
  })

  it('ignores malformed preferences and remains usable when storage is blocked', () => {
    localStorage.setItem(key, JSON.stringify({ width: '600', height: 700 }))
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new Error('Storage blocked') })
    render(<Example />)
    expect(dimensions()).toEqual({ width: 320, height: 320 * 4 / 3 })
    fireEvent.keyDown(screen.getByRole('separator', { name: '调整 Gura 宽度' }), { key: 'ArrowLeft' })
    expect(dimensions().width).toBe(330)
  })
})
