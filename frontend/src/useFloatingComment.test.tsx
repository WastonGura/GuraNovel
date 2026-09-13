import { useRef } from 'react'
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useFloatingComment } from './useFloatingComment'

function Fixture() {
  const sheet = useRef<HTMLDivElement>(null)
  const { panel } = useFloatingComment(sheet, true, true, 'one')
  return <><div className="studio-chapter-content"><div className="studio-context-wrap" /><div><div className="studio-prose"><div ref={sheet} data-sheet /></div></div></div>
    <aside className="studio-sidebar"><div className="studio-directory" /></aside>
    <div ref={panel}><button className="studio-comment-grip">移动</button><textarea defaultValue="保留评论" /></div></>
}
beforeEach(() => {
  vi.stubGlobal('innerWidth', 1440); vi.stubGlobal('innerHeight', 900)
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) { return Math.min(440, parseFloat(this.style.maxHeight) || 440) })
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockImplementation(function (this: HTMLElement) { return parseFloat(this.style.width) || 230 })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const narrow = window.innerWidth < 760
    if (this.classList.contains('studio-sidebar')) {
      const pinned = this.querySelector('.is-pinned')
      return { left: narrow ? pinned ? 20 : 130 : 1200, top: narrow && pinned ? 540 : 110, width: narrow ? pinned ? 350 : 260 : 240 } as DOMRect
    }
    const left = narrow ? 20 : 300, right = narrow ? 370 : 1150
    if (this.classList.contains('studio-prose')) return { left, right, width: right - left } as DOMRect
    if (this.hasAttribute('data-sheet')) return { right: right - parseFloat(this.closest<HTMLElement>('.studio-prose')!.style.paddingRight || '0') } as DOMRect
    return { left: parseFloat(this.style.left) || 0, top: parseFloat(this.style.top) || 0 } as DOMRect
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('reserves space for an open or pinned chapter directory and restores the gutter when it closes', async () => {
  const view = render(<Fixture />)
  const sidebar = view.container.querySelector('.studio-sidebar')!
  const directory = view.container.querySelector('.studio-directory')!
  const prose = view.container.querySelector<HTMLElement>('.studio-prose')!
  const context = view.container.querySelector<HTMLElement>('.studio-context-wrap')!
  const box = screen.getByRole('button', { name: '移动' }).parentElement!
  expect(prose.style.paddingRight).toBe('0px')
  await act(async () => { sidebar.classList.add('is-open') })
  expect(parseFloat(box.style.left) + 230).toBeLessThan(1200)
  expect(context.style.paddingRight).toBe(prose.style.paddingRight)
  await act(async () => { directory.classList.add('is-pinned'); sidebar.classList.remove('is-open') })
  expect(parseFloat(box.style.left) + 230).toBeLessThan(1200)
  await act(async () => { directory.classList.remove('is-pinned') })
  expect(prose.style.paddingRight).toBe('0px')
  expect(screen.getByRole('textbox')).toHaveValue('保留评论')
})

it('keeps a narrow overlay directory usable and places the comment above a bottom-pinned directory', async () => {
  vi.stubGlobal('innerWidth', 390)
  const view = render(<Fixture />)
  const sidebar = view.container.querySelector('.studio-sidebar')!
  const box = screen.getByRole('button', { name: '移动' }).parentElement!
  await act(async () => { sidebar.classList.add('is-open') })
  expect(box.dataset.occluded).toBe('true'); expect(box.inert).toBe(true)
  await act(async () => { view.container.querySelector('.studio-directory')!.classList.add('is-pinned'); sidebar.classList.remove('is-open') })
  expect(box.dataset.occluded).toBe('false')
  expect(parseFloat(box.style.top) + box.offsetHeight).toBeLessThan(540)
  expect(screen.getByRole('textbox')).toHaveValue('保留评论')
})
