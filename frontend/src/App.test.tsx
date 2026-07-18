import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('application shell', () => {
  it('provides landmarks and a root workbench placeholder', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getByRole('banner', { name: 'GuraNovel workbench' })).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Workbench navigation' })).toBeInTheDocument()
    expect(screen.getByRole('main')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Creative workbench' })).toBeInTheDocument()
  })

  it('renders a chapter route placeholder', () => {
    render(
      <MemoryRouter initialEntries={['/projects/story-1/chapters/chapter-2']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: 'Chapter workbench' })).toBeInTheDocument()
    expect(screen.getByText('Chapter chapter-2 in project story-1 will appear here.')).toBeInTheDocument()
  })
})
