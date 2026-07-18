import { Link, Route, Routes, useParams } from 'react-router-dom'

function Placeholder({ title, detail }: { title: string; detail: string }) {
  return (
    <section className="placeholder" aria-labelledby="route-title">
      <p className="eyebrow">Local workspace</p>
      <h1 id="route-title">{title}</h1>
      <p>{detail}</p>
    </section>
  )
}

function Home() {
  return (
    <section className="welcome" aria-labelledby="route-title">
      <p className="eyebrow">Drafting desk</p>
      <h1 id="route-title">Creative workbench</h1>
      <p className="lede">
        A quiet local place to keep projects, shape chapters, review approvals, and gather documents.
      </p>
      <dl className="work-areas">
        <div>
          <dt>Projects</dt>
          <dd>Story worlds and their working context.</dd>
        </div>
        <div>
          <dt>Chapters</dt>
          <dd>Drafts held close to their narrative thread.</dd>
        </div>
        <div>
          <dt>Approvals</dt>
          <dd>Clear review points before work moves forward.</dd>
        </div>
        <div>
          <dt>Documents</dt>
          <dd>Reference material kept within reach.</dd>
        </div>
      </dl>
    </section>
  )
}

function ProjectPlaceholder() {
  const { projectId } = useParams()
  return <Placeholder title="Project workbench" detail={`Project ${projectId} will appear here.`} />
}

function ChapterPlaceholder() {
  const { chapterId, projectId } = useParams()
  return (
    <Placeholder
      title="Chapter workbench"
      detail={`Chapter ${chapterId} in project ${projectId} will appear here.`}
    />
  )
}

export default function App() {
  return (
    <div className="app-shell">
      <header className="topbar" aria-label="GuraNovel workbench">
        <Link className="wordmark" to="/">GuraNovel</Link>
        <span className="workspace-name">Creative workbench</span>
      </header>
      <div className="workspace">
        <nav aria-label="Workbench navigation">
          <Link to="/">Overview</Link>
          <span>Projects</span>
          <span>Approvals</span>
          <span>Documents</span>
        </nav>
        <main>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/projects/:projectId" element={<ProjectPlaceholder />} />
            <Route
              path="/projects/:projectId/chapters/:chapterId"
              element={<ChapterPlaceholder />}
            />
          </Routes>
        </main>
      </div>
    </div>
  )
}
