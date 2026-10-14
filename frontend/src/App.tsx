import { Link, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import Dashboard from './Dashboard'
import Studio from './Studio'
import SettingCollectionWorkspace from './SettingCollectionWorkspace'
import GlobalAssistant from './GlobalAssistant'

type RetiredWorkspace = 'project' | 'chapter' | 'reader' | 'creation' | 'maintenance'

function RetiredWorkspaceRedirect({ workspace }: { workspace: RetiredWorkspace }) {
  const { projectId = '', chapterId = '', documentId, documentVersionId, sessionId } = useParams()
  const location = useLocation()
  const project = encodeURIComponent(projectId)
  if (workspace === 'project' || workspace === 'creation' || workspace === 'maintenance') {
    return <Navigate replace to={`/?project=${project}`}
      state={workspace === 'project' ? null : { retiredWorkspace: workspace }} />
  }
  const search = new URLSearchParams()
  if (workspace === 'reader') {
    search.set('stage', 'Reader')
    if (documentId) search.set('readerDocument', documentId)
    if (documentVersionId) search.set('readerVersion', documentVersionId)
    if (sessionId) search.set('readerSession', sessionId)
  } else {
    const stage = new URLSearchParams(location.search).get('stage')
    if (stage) search.set('stage', stage)
  }
  return <Navigate replace to={{
    pathname: `/projects/${project}/studio/${encodeURIComponent(chapterId)}`,
    search: search.size ? `?${search}` : '',
  }} />
}

function NotFound() {
  return <section className="page" aria-labelledby="route-title">
    <h1 id="route-title">未找到页面</h1>
    <p className="muted">此页面不存在，请返回书架打开作品。</p>
    <Link to="/">返回书架</Link>
  </section>
}

export default function App() {
  const { pathname } = useLocation()
  const studio = /^\/preview\/studio(?:\/[^/]+)?$/.test(pathname)
    || /^\/projects\/[^/]+\/studio(?:\/[^/]+)?$/.test(pathname)
  const setting = /^\/setting-collections\/[^/]+$/.test(pathname)
  const shell = studio ? 'studio-app-shell' : setting
    ? 'studio-app-shell setting-workspace-shell' : 'dashboard-app-shell'

  return (
    <div className={`app-shell ${shell}`}>
      <div className={`workspace${!studio && !setting ? ' dashboard-workspace' : ''}`}>
        <main className={setting ? 'setting-app-main' : undefined}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/preview/studio" element={<Studio />} />
            <Route path="/preview/studio/:chapterId" element={<Studio />} />
            <Route path="/projects/:projectId/studio" element={<Studio />} />
            <Route path="/projects/:projectId/studio/:chapterId" element={<Studio />} />
            <Route path="/setting-collections/:settingCollectionId" element={<SettingCollectionWorkspace />} />

            <Route path="/projects/:projectId" element={<RetiredWorkspaceRedirect workspace="project" />} />
            <Route path="/projects/:projectId/chapters/:chapterId" element={<RetiredWorkspaceRedirect workspace="chapter" />} />
            <Route path="/projects/:projectId/chapters/:chapterId/documents/:documentId/versions/:documentVersionId/reader-panel" element={<RetiredWorkspaceRedirect workspace="reader" />} />
            <Route path="/projects/:projectId/chapters/:chapterId/documents/:documentId/versions/:documentVersionId/reader-panel/:sessionId" element={<RetiredWorkspaceRedirect workspace="reader" />} />
            <Route path="/projects/:projectId/creation/start" element={<RetiredWorkspaceRedirect workspace="creation" />} />
            <Route path="/projects/:projectId/creation/:workflowRunId/gate" element={<RetiredWorkspaceRedirect workspace="creation" />} />
            <Route path="/projects/:projectId/maintenance" element={<RetiredWorkspaceRedirect workspace="maintenance" />} />
            <Route path="/projects/:projectId/maintenance/start" element={<RetiredWorkspaceRedirect workspace="maintenance" />} />
            <Route path="/projects/:projectId/maintenance/:workflowRunId/status" element={<RetiredWorkspaceRedirect workspace="maintenance" />} />
            <Route path="/projects/:projectId/maintenance/:workflowRunId" element={<RetiredWorkspaceRedirect workspace="maintenance" />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
      {!studio && !setting && <GlobalAssistant />}
    </div>
  )
}
