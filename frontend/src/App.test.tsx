import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate, useParams } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

vi.mock('./Dashboard', () => ({ default: () => <section aria-label="书架">作品列表</section> }))
vi.mock('./Studio', () => ({ default: function MockStudio() {
  const { projectId, chapterId } = useParams()
  return <section aria-label="创作区">{projectId || 'preview'}|{chapterId || 'latest'}</section>
} }))
vi.mock('./SettingCollectionWorkspace', () => ({ default: function MockSettingWorkspace() {
  const { settingCollectionId } = useParams()
  return <section aria-label="设定集">{settingCollectionId}</section>
} }))
vi.mock('./GlobalAssistant', () => ({ default: () => <aside aria-label="Gura">助手</aside> }))

function Location() {
  const location = useLocation(), navigate = useNavigate()
  return <><output data-testid="location">{location.pathname}{location.search}</output>
    <output data-testid="state">{JSON.stringify(location.state)}</output>
    <button onClick={() => navigate(-1)}>后退</button></>
}
function open(path: string, previous?: string) {
  return render(<MemoryRouter initialEntries={previous ? [previous, path] : [path]}>
    <App /><Location />
  </MemoryRouter>)
}
afterEach(cleanup)

describe('single frontend shell', () => {
  it.each([
    ['/', '书架', '作品列表'],
    ['/projects/p/studio', '创作区', 'p|latest'],
    ['/projects/p/studio/c', '创作区', 'p|c'],
    ['/preview/studio', '创作区', 'preview|latest'],
    ['/setting-collections/s', '设定集', 's'],
  ])('opens %s without legacy navigation', (path, name, content) => {
    open(path)
    expect(screen.getByRole('region', { name })).toHaveTextContent(content)
    expect(screen.getByRole('main')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Workbench navigation' })).not.toBeInTheDocument()
    expect(screen.queryByRole('banner')).not.toBeInTheDocument()
  })

  it('retains the standalone setting main layout', () => {
    open('/setting-collections/s')
    expect(screen.getByRole('main')).toHaveClass('setting-app-main')
    expect(screen.getByRole('main').closest('.app-shell')).toHaveClass('setting-workspace-shell')
  })

  it('renders a useful not-found page without reviving old chrome', () => {
    open('/projects/p/chapters/c/unknown')
    expect(screen.getByRole('heading', { name: '未找到页面' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回书架' })).toHaveAttribute('href', '/')
  })
})

describe('retired links', () => {
  it('opens the designed project detail rather than the old project workspace', async () => {
    open('/projects/p')
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/?project=p'))
    expect(screen.getByRole('region', { name: '书架' })).toBeInTheDocument()
  })

  it('opens the exact Studio chapter and preserves its requested stage', async () => {
    open('/projects/p/chapters/c?stage=Review')
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/projects/p/studio/c?stage=Review'))
    expect(screen.getByRole('region', { name: '创作区' })).toHaveTextContent('p|c')
  })

  it.each([
    ['/projects/p/creation/start', 'creation'],
    ['/projects/p/creation/run/gate', 'creation'],
    ['/projects/p/maintenance', 'maintenance'],
    ['/projects/p/maintenance/start', 'maintenance'],
    ['/projects/p/maintenance/run', 'maintenance'],
    ['/projects/p/maintenance/run/status', 'maintenance'],
  ])('opens the project detail with an honest capability notice for %s', async (path, feature) => {
    open(path)
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/?project=p'))
    expect(JSON.parse(screen.getByTestId('state').textContent!)).toEqual({ retiredWorkspace: feature })
  })

  it.each(['', '/session-id'])('preserves immutable reader references%s', async suffix => {
    open('/projects/p/chapters/c/documents/doc/versions/v1/reader-panel' + suffix)
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/projects/p/studio/c?'))
    const target = new URL(screen.getByTestId('location').textContent!, 'http://localhost')
    expect(target.searchParams.get('stage')).toBe('Reader')
    expect(target.searchParams.get('readerDocument')).toBe('doc')
    expect(target.searchParams.get('readerVersion')).toBe('v1')
    expect(target.searchParams.get('readerSession')).toBe(suffix ? 'session-id' : null)
  })

  it('replaces the retired history entry so back does not loop through it', async () => {
    open('/projects/p/chapters/c', '/')
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/projects/p/studio/c'))
    fireEvent.click(screen.getByRole('button', { name: '后退' }))
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/'))
    expect(screen.getByRole('region', { name: '书架' })).toBeInTheDocument()
  })
})
