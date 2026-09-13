import { afterEach, expect, it, vi } from 'vitest'
import { dissolvePanel } from './studioPanelParticles'

afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('submits only after all panel fragments finish, and preserves the panel on cancellation', async () => {
  let finish!: () => void
  const finished = new Promise<void>(resolve => { finish = resolve })
  const cancel = vi.fn(), complete = vi.fn()
  const original = HTMLElement.prototype.animate
  HTMLElement.prototype.animate = () => ({ finished, cancel }) as unknown as Animation
  const panel = document.createElement('div')
  panel.innerHTML = '<label>评论<textarea>保留内容</textarea></label><button>发送</button>'
  document.body.append(panel)
  try {
    dissolvePanel(panel, complete)
    expect(panel.style.visibility).toBe('hidden')
    expect(document.querySelector('.studio-panel-particles')).toHaveAttribute('aria-hidden', 'true')
    expect(complete).not.toHaveBeenCalled()
    finish(); await finished; await Promise.resolve(); await Promise.resolve()
    expect(complete).toHaveBeenCalledTimes(1)
    expect(document.querySelector('.studio-panel-particles')).toBeNull()
    complete.mockClear()
    const stop = dissolvePanel(panel, complete)
    stop(); await Promise.resolve(); await Promise.resolve()
    expect(complete).not.toHaveBeenCalled()
    expect(panel.style.visibility).toBe('')
    expect(panel.querySelector('textarea')).toHaveValue('保留内容')
    expect(document.querySelector('.studio-panel-particles')).toBeNull()
    expect(cancel).toHaveBeenCalled()
  } finally { HTMLElement.prototype.animate = original }
})
