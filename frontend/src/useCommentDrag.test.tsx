import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useCommentDrag } from './useCommentDrag'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

function setup(readOnly = false) {
  vi.stubGlobal('PointerEvent', MouseEvent)
  const change = vi.fn(), clicked = vi.fn(), animate = vi.fn(() => ({ cancel: vi.fn(), finished: Promise.resolve() }))
  function Example() {
    const [ids, setIds] = useState(['a', 'b', 'c'])
    const dots = useCommentDrag(!readOnly, next => { change(next); setIds(next) })
    return <section className="studio-context"><div ref={dots}>{ids.map(id => <button data-comment-id={id} key={id} onClick={clicked}>{id}</button>)}</div></section>
  }
  const view = render(<Example />)
  vi.spyOn(view.container.querySelector('section')!, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 100, right: 400, bottom: 300 } as DOMRect)
  screen.getAllByRole('button').forEach((button, index) => {
    vi.spyOn(button, 'getBoundingClientRect').mockReturnValue({ left: 120 + index * 24, top: 130, width: 24, height: 24 } as DOMRect)
    button.animate = animate as unknown as typeof button.animate
  })
  return { change, clicked, animate }
}

it('shrinks outside, restores on reentry, shifts neighbours and only commits the final order on release', () => {
  const { change, animate, clicked } = setup()
  const a = screen.getByRole('button', { name: 'a' })
  fireEvent.pointerDown(a, { button: 0, clientX: 132, clientY: 142 })
  fireEvent.pointerMove(document, { clientX: 80, clientY: 142 })
  expect(document.querySelector('.studio-comment-drag-ghost')).toHaveAttribute('data-outside', 'true')
  expect(document.querySelector('.studio-comment-drag-ghost')).toHaveStyle({ scale: '0' })
  expect(change).not.toHaveBeenCalled()
  fireEvent.pointerMove(document, { clientX: 180, clientY: 142 })
  expect(document.querySelector('.studio-comment-drag-ghost')).toHaveAttribute('data-outside', 'false')
  expect(animate).toHaveBeenCalled()
  fireEvent.pointerUp(document, { clientX: 180, clientY: 142 })
  expect(change).toHaveBeenLastCalledWith(['b', 'c', 'a'])
  expect(screen.getAllByRole('button').map(button => button.textContent)).toEqual(['b', 'c', 'a'])
  expect(document.querySelector('.studio-comment-drag-ghost')).toBeNull()
  fireEvent.click(a)
  expect(clicked).not.toHaveBeenCalled()
})

it('cancels without deleting on Escape or pointer cancellation, then removes on an outside release', () => {
  const { change } = setup()
  const a = screen.getByRole('button', { name: 'a' })
  for (const cancel of ['Escape', 'pointercancel']) {
    fireEvent.pointerDown(a, { button: 0, clientX: 132, clientY: 142 })
    fireEvent.pointerMove(document, { clientX: 80, clientY: 142 })
    if (cancel === 'Escape') fireEvent.keyDown(document, { key: 'Escape' })
    else fireEvent.pointerCancel(document)
    expect(change).not.toHaveBeenCalled()
    expect(document.querySelector('.studio-comment-drag-ghost')).toBeNull()
  }
  fireEvent.pointerDown(a, { button: 0, clientX: 132, clientY: 142 })
  fireEvent.pointerMove(document, { clientX: 80, clientY: 142 })
  fireEvent.pointerUp(document, { clientX: 80, clientY: 142 })
  expect(change).toHaveBeenLastCalledWith(['b', 'c'])
})

it('never reorders or removes read-only comments', () => {
  const { change } = setup(true)
  const a = screen.getByRole('button', { name: 'a' })
  fireEvent.pointerDown(a, { button: 0, clientX: 132, clientY: 142 })
  fireEvent.pointerMove(document, { clientX: 80, clientY: 142 }); fireEvent.pointerUp(document)
  fireEvent.keyDown(a, { key: 'Delete' }); fireEvent.keyDown(a, { key: 'ArrowRight', altKey: true })
  expect(change).not.toHaveBeenCalled()
})
