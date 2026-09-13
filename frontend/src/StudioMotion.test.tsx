import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MotionFrame, TextSweep, StreamText } from './StudioMotion'
import { animateTextChange, DISSOLVE_MS, textMotionPhase, type TextSnapshot } from './studioTextParticles'

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('continuous studio motion', () => {
  it('keeps chrome mounted, interpolates real height and reveals new controls only after resizing', async () => {
    const calls: { element: HTMLElement; frames: Keyframe[]; options: KeyframeAnimationOptions }[] = []
    let finish!: () => void
    const finished = new Promise<void>(resolve => { finish = resolve })
    let naturalHeight = 200
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return { height: this.classList.contains('studio-motion-content') ? naturalHeight : parseFloat(this.style.height) || 0 } as DOMRect
    })
    const original = HTMLElement.prototype.animate
    HTMLElement.prototype.animate = function (frames, options) {
      calls.push({ element: this, frames: frames as Keyframe[], options: options as KeyframeAnimationOptions })
      return { finished, cancel: vi.fn() } as unknown as Animation
    }
    try {
      const panel = (show: boolean) => <MotionFrame chrome={<footer><button data-motion-control>send</button><button data-motion-control data-visible={show}>next</button></footer>}><p>content</p></MotionFrame>
      const view = render(panel(false))
      const send = screen.getByRole('button', { name: 'send' })
      const frame = view.container.querySelector('.studio-motion-frame') as HTMLElement
      frame.dataset.textPhase = 'dissolve'
      naturalHeight = 600
      view.rerender(panel(true))
      expect(calls).toHaveLength(0)
      expect(screen.getByRole('button', { name: 'next' }).style.opacity).toBe('0')
      frame.dataset.textPhase = 'reveal'
      frame.dispatchEvent(new Event('studio:text-ready'))
      expect(screen.getByRole('button', { name: 'send' })).toBe(send)
      expect(send.parentElement?.parentElement).toHaveClass('studio-motion-frame')
      expect(calls.find(call => call.element.classList.contains('studio-motion-frame'))?.frames).toEqual([{ height: '200px' }, { height: '600px' }])
      expect(calls[0].options.easing).toBe('cubic-bezier(0, 0, .58, 1)')
      const next = screen.getByRole('button', { name: 'next' })
      expect(next.style.opacity).toBe('0')
      expect(next.inert).toBe(true)
      expect(calls.some(call => call.element === send)).toBe(false)
      await act(async () => finish())
      expect(next.style.opacity).toBe('')
      expect(next.inert).toBe(false)
      expect(calls.some(call => call.element === next)).toBe(true)
    } finally { HTMLElement.prototype.animate = original }
  })

  it('completes all dust before revealing wrapped rows and cleans up on interruption', () => {
    vi.stubGlobal('CanvasRenderingContext2D', class {})
    let tick!: FrameRequestCallback
    vi.stubGlobal('requestAnimationFrame', vi.fn(callback => { tick = callback; return 1 }))
    const cancel = vi.fn()
    vi.stubGlobal('cancelAnimationFrame', cancel)
    const ctx = {
      measureText: () => ({ fontBoundingBoxDescent: 3 }), fillText: vi.fn(), clearRect: vi.fn(), drawImage: vi.fn(),
      getImageData: () => ({ data: new Uint8ClampedArray([0, 0, 0, 255]) }),
      save: vi.fn(), restore: vi.fn(), beginPath: vi.fn(), rect: vi.fn(), clip: vi.fn(), fillRect: vi.fn(),
      createLinearGradient: () => ({ addColorStop: vi.fn() }),
    }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D)
    const box = document.createElement('div')
    box.innerHTML = '<span data-sweep-text>new<sup>1</sup></span><button>pin</button>'
    document.body.append(box)
    const text = box.querySelector<HTMLElement>('[data-sweep-text]')!
    const range = document.createRange()
    range.getClientRects = () => (range.startContainer.parentElement?.tagName === 'SUP'
      ? [{ left: 40, right: 48, top: 4, bottom: 14, width: 8, height: 10 }]
      : [{ left: 12, right: 72, top: 0, bottom: 20, width: 60, height: 20 }, { left: 24, right: 84, top: 30, bottom: 50, width: 60, height: 20 }]) as unknown as DOMRectList
    vi.spyOn(document, 'createRange').mockReturnValue(range)
    const snapshot: TextSnapshot = { width: 120, height: 60, glyphs: [
      { text: '第一行', x: 0, y: 0, width: 60, height: 20, font: '16px sans-serif', color: '#111' },
      { text: '第二行', x: 0, y: 30, width: 60, height: 20, font: '16px sans-serif', color: '#111' },
    ] }
    const stop = animateTextChange(box, snapshot)
    const pin = box.querySelector('button')
    expect(box.querySelector('canvas')).toHaveAttribute('aria-hidden', 'true')
    expect(box.querySelector('canvas')?.width).toBe(snapshot.width)
    expect(box.querySelector('canvas')?.height).toBe(snapshot.height)
    tick(0); tick(DISSOLVE_MS - 1)
    expect(box.dataset.textPhase).toBe('dissolve')
    expect(text.style.mask).toBe('')
    tick(DISSOLVE_MS)
    expect(box.dataset.textPhase).toBe('reveal')
    expect(box.querySelector('canvas')).toBeNull()
    expect(box.querySelector('[data-sweep-text]')).toBe(text)
    tick(DISSOLVE_MS + 50)
    const firstMask = text.style.mask
    expect(firstMask).toContain('linear-gradient')
    expect(firstMask).toContain('transparent 0px')
    expect(firstMask.match(/linear-gradient/g)).toHaveLength(2) // The superscript belongs to its surrounding line.
    expect(firstMask).toContain('24px 28px / 84px 24px') // A pending indented row cannot expose text to its left.
    tick(DISSOLVE_MS + 150)
    expect(text.style.mask).not.toBe(firstMask)
    expect(text.style.transform).toBe('')
    expect(ctx.fillText).toHaveBeenCalledTimes(2) // Only the two outgoing glyphs were rasterized.
    tick(DISSOLVE_MS + 500)
    expect(box.dataset.textPhase).toBeUndefined()
    expect(box.querySelector('canvas')).toBeNull()
    expect(box.querySelector('button')).toBe(pin)
    expect(box.querySelector('[data-sweep-text]')).toBe(text)
    expect(text.style.mask).toBe('')
    stop(); expect(cancel).toHaveBeenCalledTimes(1)
    const interrupt = animateTextChange(box, snapshot)
    tick(0); tick(DISSOLVE_MS + 50); interrupt()
    expect(box.dataset.textPhase).toBeUndefined()
    expect(box.querySelector('canvas')).toBeNull()
    expect(text.style.mask).toBe('')
    box.remove()
  })

  it('does not reveal any new text until the dust lifetime has ended', () => {
    expect(textMotionPhase(0, true)).toBe('dissolve')
    expect(textMotionPhase(DISSOLVE_MS - 1, true)).toBe('dissolve')
    expect(textMotionPhase(DISSOLVE_MS, true)).toBe('reveal')
    expect(textMotionPhase(0, false)).toBe('reveal')
  })

  it('honors reduced motion without hiding the final text or adding old interactive copies', () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
    const view = render(<MotionFrame><StreamText text="完整文字" /></MotionFrame>)
    expect(view.container.querySelector('.studio-stream')).toHaveAttribute('aria-busy', 'false')
    view.rerender(<MotionFrame><TextSweep text="新内容" /><button>新内容</button></MotionFrame>)
    expect(screen.getByRole('button', { name: '新内容' })).toBeInTheDocument()
    expect(view.container.querySelector('.studio-text-old')).toBeNull()
  })

  it('does not replay already-generated reports when returning to the panel', () => {
    const view = render(<StreamText text="已生成的报告" startedAt={Date.now() - 10000} />)
    expect(view.container.querySelector('.studio-stream')).toHaveAttribute('aria-busy', 'false')
    expect(view.container.querySelector('.studio-stream > [aria-hidden]')).toHaveTextContent('已生成的报告')
  })
})
