import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MotionFrame, TextMotionSurface, TextSweep, StreamText } from './StudioMotion'
import { animateTextChange, DISSOLVE_MS, snapshotText, textMotionPhase, type TextSnapshot } from './studioTextParticles'

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('continuous studio motion', () => {
  it('reveals setting text over its opaque ancestor without masking glyphs, then removes every cover on interruption', () => {
    vi.stubGlobal('CanvasRenderingContext2D', class {})
    let tick!: FrameRequestCallback
    vi.stubGlobal('requestAnimationFrame', vi.fn(callback => { tick = callback; return 1 }))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ clearRect: vi.fn() } as unknown as CanvasRenderingContext2D)
    const parent = document.createElement('div')
    parent.style.backgroundColor = 'rgb(253, 253, 253)'
    parent.innerHTML = '<div class="setting-document-motion"><div style="background:rgba(0,0,0,.1)" data-text-editor data-sweep-text>查看<span data-reference-title="旧港">旧港</span></div></div>'
    document.body.append(parent)
    const box = parent.firstElementChild as HTMLElement, editor = box.firstElementChild as HTMLElement
    vi.spyOn(box, 'getBoundingClientRect').mockReturnValue({ left: 10, right: 210, top: 20, bottom: 140, width: 200, height: 120 } as DOMRect)
    vi.spyOn(editor, 'getBoundingClientRect').mockReturnValue({ left: 20, right: 200, top: 40, bottom: 100, width: 180, height: 60 } as DOMRect)
    const range = document.createRange()
    range.getClientRects = () => [{ left: 25, right: 145, top: 44, bottom: 64, width: 120, height: 20 }] as unknown as DOMRectList
    vi.spyOn(document, 'createRange').mockReturnValue(range)
    const stop = animateTextChange(box, null)
    tick(0)
    const cover = box.querySelector<HTMLElement>('.studio-text-reveal')!
    expect(cover).toHaveAttribute('aria-hidden', 'true')
    expect(cover.style.pointerEvents).toBe('none')
    expect(cover).toHaveTextContent('')
    expect(cover.firstElementChild).toHaveStyle({ left: '15px', top: '22px', width: '144px', height: '24px' })
    expect((cover.firstElementChild as HTMLElement).style.background).toContain('rgb(253, 253, 253)')
    expect(editor.style.mask).toBe('')
    expect(editor.querySelector('[data-reference-title]')).toHaveTextContent('旧港')
    tick(150); stop()
    expect(box.querySelector('.studio-text-reveal')).toBeNull()
    expect(box.dataset.textPhase).toBeUndefined()
    const restarted = animateTextChange(box, null, 100)
    tick(0)
    editor.dispatchEvent(new Event('scroll'))
    expect(box.dataset.textPhase).toBe('waiting') // Restoring a document bookmark must not abort its incoming reveal.
    tick(100)
    expect(box.querySelectorAll('.studio-text-reveal')).toHaveLength(1)
    editor.dispatchEvent(new Event('scroll'))
    expect(box.querySelector('.studio-text-reveal')).toBeNull()
    expect(box.dataset.textPhase).toBeUndefined()
    restarted(); parent.remove()
  })

  it('reveals visible rows without masking hundreds of lines outside the scrolled viewport', () => {
    vi.stubGlobal('CanvasRenderingContext2D', class {})
    let tick!: FrameRequestCallback
    vi.stubGlobal('requestAnimationFrame', vi.fn(callback => { tick = callback; return 1 }))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ clearRect: vi.fn() } as unknown as CanvasRenderingContext2D)
    const box = document.createElement('div')
    box.innerHTML = '<div style="overflow-y:auto"><div data-text-editor data-sweep-text>长文</div></div>'
    document.body.append(box)
    const rect = (top: number, bottom: number) => ({ left: 0, right: 100, top, bottom, width: 100, height: bottom - top }) as DOMRect
    vi.spyOn(box, 'getBoundingClientRect').mockReturnValue(rect(0, 120))
    vi.spyOn(box.firstElementChild!, 'getBoundingClientRect').mockReturnValue(rect(10, 80))
    const editor = box.querySelector<HTMLElement>('[data-text-editor]')!
    vi.spyOn(editor, 'getBoundingClientRect').mockReturnValue(rect(-300, 8700))
    const masks = vi.spyOn(editor.style, 'mask', 'set')
    const range = document.createRange()
    range.getClientRects = () => Array.from({ length: 300 }, (_, index) => rect(index * 30 - 300, index * 30 - 280)) as unknown as DOMRectList
    vi.spyOn(document, 'createRange').mockReturnValue(range)
    const stop = animateTextChange(box, null)
    tick(0)
    expect.soft(masks.mock.calls.at(-1)?.[0].match(/linear-gradient/g)).toHaveLength(3)
    tick(500)
    expect.soft(box.dataset.textPhase).toBeUndefined()
    stop(); box.remove()
  })

  it('reveals the visible title input and reference editor instead of their measurement mirrors', () => {
    vi.stubGlobal('CanvasRenderingContext2D', class {})
    let tick!: FrameRequestCallback
    vi.stubGlobal('requestAnimationFrame', vi.fn(callback => { tick = callback; return 1 }))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ clearRect: vi.fn() } as unknown as CanvasRenderingContext2D)
    const box = document.createElement('div')
    box.innerHTML = '<span data-text-input><span data-sweep-text>旧港</span><input value="旧港"></span><div data-sweep-text data-text-editor contenteditable="true">港口正文</div>'
    document.body.append(box)
    vi.spyOn(box, 'getBoundingClientRect').mockReturnValue({ left: 0, right: 120, top: 0, bottom: 120, width: 120, height: 120 } as DOMRect)
    const range = document.createRange()
    range.getClientRects = () => [{ left: 0, right: 80, top: 0, bottom: 20, width: 80, height: 20 }] as unknown as DOMRectList
    vi.spyOn(document, 'createRange').mockReturnValue(range)
    const stop = animateTextChange(box, null)
    tick(0)
    expect(box.querySelector('input')?.style.mask).toContain('linear-gradient')
    expect(box.querySelector<HTMLElement>('[data-text-input] [data-sweep-text]')?.style.mask).toBe('')
    expect(box.querySelector<HTMLElement>('[data-text-editor]')?.style.mask).toContain('linear-gradient')
    stop()
    expect(box.querySelector('input')?.style.mask).toBe('')
    box.remove()
  })

  it('interrupts an entry reveal as soon as the author writes without remounting the editor', () => {
    vi.stubGlobal('CanvasRenderingContext2D', class {})
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ clearRect: vi.fn() } as unknown as CanvasRenderingContext2D)
    const panel = (key: string) => <TextMotionSurface className="test-editor-motion" motionKey={key} interruptOnInput><textarea aria-label="文稿" defaultValue={key} /></TextMotionSurface>
    const view = render(panel('first'))
    const input = screen.getByRole('textbox', { name: '文稿' })
    view.rerender(panel('second'))
    expect(view.container.querySelector('[data-text-phase]')).toBeInTheDocument()
    fireEvent.input(input)
    expect(view.container.querySelector('[data-text-phase]')).toBeNull()
    expect(screen.getByRole('textbox', { name: '文稿' })).toBe(input)
  })

  it('dissolves only glyphs visible inside a scrolled text panel', () => {
    const box = document.createElement('div')
    box.innerHTML = '<div style="overflow-y:auto"><span data-sweep-text>可见藏</span></div>'
    document.body.append(box)
    const rect = (top: number, bottom: number) => ({ left: 0, right: 100, top, bottom, width: 100, height: bottom - top }) as DOMRect
    vi.spyOn(box, 'getBoundingClientRect').mockReturnValue(rect(0, 120))
    vi.spyOn(box.firstElementChild!, 'getBoundingClientRect').mockReturnValue(rect(10, 50))
    const range = document.createRange()
    range.getBoundingClientRect = () => rect([20, 46, 70][range.startOffset], [30, 56, 80][range.startOffset])
    vi.spyOn(document, 'createRange').mockReturnValue(range)
    expect(snapshotText(box).glyphs.map(glyph => glyph.text)).toEqual(['可'])
    box.remove()
  })

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
    vi.spyOn(box, 'getBoundingClientRect').mockReturnValue({ left: 0, right: 120, top: 0, bottom: 60, width: 120, height: 60 } as DOMRect)
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
    const delayed = animateTextChange(box, snapshot, 0, DISSOLVE_MS + 200)
    tick(0); tick(DISSOLVE_MS)
    expect(box.dataset.textPhase).toBe('waiting')
    expect(box.querySelector('canvas')).toBeNull()
    expect(text.style.mask).toBe('')
    tick(DISSOLVE_MS + 200)
    expect(box.dataset.textPhase).toBe('reveal')
    tick(DISSOLVE_MS + 250)
    expect(text.style.mask).toContain('linear-gradient')
    delayed()
    expect(box.dataset.textPhase).toBeUndefined()
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
