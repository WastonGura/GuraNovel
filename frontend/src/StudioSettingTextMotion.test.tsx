import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import StudioSetting from './StudioSetting'
import { animateTextChange } from './studioTextParticles'

vi.mock('./studioTextParticles', () => ({
  snapshotText: vi.fn((box: HTMLElement) => ({ width: 400, height: 300, glyphs: [...box.querySelectorAll('[data-sweep-text]')].flatMap(node => [...(node.textContent || '')].map(text => ({ text, x: 0, y: 0, width: 10, height: 20, font: '16px sans-serif', color: '#171717' }))) })),
  animateTextChange: vi.fn(() => vi.fn()),
}))

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); vi.clearAllMocks(); vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false }))) })
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

const notes = [
  { id: 'rain', title: '雨城', body: '雨城每夜下雨。', category: 'setting' as const, versionId: 'rain-v1' },
  { id: 'tower', title: '钟楼', body: '钟楼建在山顶。', category: 'setting' as const, versionId: 'tower-v1' },
]
const documentAnimations = () => vi.mocked(animateTextChange).mock.calls.filter(([box]) => box.classList.contains('setting-document') || box.classList.contains('setting-document-motion'))
function writeBody(value: string) {
  const input = screen.getByRole('textbox', { name: '设定正文' })
  if (input instanceof HTMLTextAreaElement) fireEvent.change(input, { target: { value } })
  else { input.textContent = value; fireEvent.input(input) }
  return input
}
const bodyText = () => {
  const input = screen.getByRole('textbox', { name: '设定正文' })
  return input instanceof HTMLTextAreaElement ? input.value : input.textContent
}

it('dissolves the latest directly edited text when switching entries, without replaying while typing', () => {
  render(<MemoryRouter><StudioSetting preview={false} defaultPinned backendNotes={notes} onSaveNoteContent={vi.fn()} /></MemoryRouter>)
  const input = screen.getByRole('textbox', { name: '设定正文' })
  input.focus()
  writeBody('雨城后来又下起雪。')
  expect(screen.getByRole('textbox', { name: '设定正文' })).toBe(input)
  expect(input).toHaveFocus()
  expect(documentAnimations()).toHaveLength(0)
  fireEvent.click(within(screen.getByLabelText('设定条目')).getByRole('button', { name: '钟楼' }))
  const calls = documentAnimations()
  expect(calls).toHaveLength(1)
  expect(calls[0][1]?.glyphs.map(glyph => glyph.text).join('')).toContain('雨城后来又下起雪。')
  expect(calls[0][1]?.glyphs.map(glyph => glyph.text).join('')).toContain('雨城')
  expect(bodyText()).toBe('钟楼建在山顶。')
})

it('preserves the outgoing document transition when switching to an empty category', () => {
  render(<MemoryRouter><StudioSetting preview={false} defaultPinned backendNotes={[notes[0]]} onSaveNoteContent={vi.fn()} /></MemoryRouter>)
  fireEvent.click(screen.getByRole('button', { name: /^世界观$/ }))
  const calls = documentAnimations()
  expect(calls).toHaveLength(1)
  expect(calls[0][1]?.glyphs.map(glyph => glyph.text).join('')).toContain('雨城每夜下雨。')
  expect(screen.getByRole('heading', { name: '世界观' })).toBeInTheDocument()
})

it('keeps final text immediately available with reduced motion enabled', () => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
  render(<MemoryRouter><StudioSetting preview={false} defaultPinned backendNotes={notes} onSaveNoteContent={vi.fn()} /></MemoryRouter>)
  fireEvent.click(within(screen.getByLabelText('设定条目')).getByRole('button', { name: '钟楼' }))
  expect(documentAnimations()).toHaveLength(0)
  expect(bodyText()).toBe('钟楼建在山顶。')
})
