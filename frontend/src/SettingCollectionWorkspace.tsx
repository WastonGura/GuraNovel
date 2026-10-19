import { useEffect, useRef, useState, type MouseEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ApiError,
  applySettingProposal,
  createCollectionDocument,
  deleteDocument,
  undeleteDocument,
  getDocument,
  getSettingCollection,
  listCollectionDocuments,
  listCollectionProjects,
  patchDocument,
  readDocumentContent,
  writeDocument,
  type Document,
  type Project,
  type SettingChangeProposal,
  type SettingCollection,
} from './api/client'
import StudioSetting from './StudioSetting'
import GlobalAssistant from './GlobalAssistant'
import {
  categoryNames,
  type SettingCategory,
  type SettingNote,
} from './settingNotes'
import { restoreOutlineComments, type OutlineComment } from './studioPreview'
import './studioSetting.css'

interface ConflictState {
  noteId: string
  noteTitle: string
  localDraft: string
}

function documentTypeForCategory(category: SettingCategory) {
  return category === 'world' ? ('world_overview' as const) : ('character_profile' as const)
}

function categoryFromDocument(doc: Document): SettingCategory {
  if (doc.metadata?.category === 'world' || doc.metadata?.category === 'setting') {
    return doc.metadata.category
  }
  const worldTypes = ['world_overview', 'power_system', 'factions', 'geography', 'history']
  return worldTypes.includes(doc.type) ? 'world' : 'setting'
}

function documentToSettingNote(doc: Document, content: string): SettingNote {
  const category = categoryFromDocument(doc)
  const comments = restoreOutlineComments(doc.metadata?.comments, content)
  return {
    id: doc.id,
    documentId: doc.id,
    versionId: doc.current_version_id || undefined,
    category,
    title: doc.title || `未命名${categoryNames[category]}`,
    body: content,
    comments,
  }
}

function slugify(text: string): string {
  const clean = text.toLowerCase().replace(/[^a-z0-9_-]/g, '-').replace(/-+/g, '-').replace(/^-+|-+$/g, '')
  return clean || 'note'
}

export default function SettingCollectionWorkspace() {
  const { settingCollectionId = '' } = useParams<{ settingCollectionId: string }>()
  const navigate = useNavigate()
  const editor = useRef<{ flush: () => Promise<boolean> }>(null)
  const [assistantContext, setAssistantContext] = useState<{ documentId?: string; versionId?: string }>({})
  const [leaving, setLeaving] = useState(false)
  const [navigationError, setNavigationError] = useState('')
  async function goHome(event: MouseEvent<HTMLAnchorElement>) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    if (leaving) return
    setLeaving(true)
    try {
      if (!editor.current || await editor.current.flush()) navigate('/')
      else setNavigationError('内容尚未保存，草稿仍保留在当前页面。请重试返回主页。')
    } finally { setLeaving(false) }
  }
  const homeLink = <Link to="/" className="setting-workspace-home" aria-disabled={leaving} onClick={goHome}>回到书架</Link>
  const isPreview = settingCollectionId === 'preview'
  const [collection, setCollection] = useState<SettingCollection | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [notes, setNotes] = useState<SettingNote[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveStatus, setSaveStatus] = useState<'saved' | 'saving' | 'conflict' | 'error'>('saved')
  const [conflict, setConflict] = useState<ConflictState | null>(null)
  const [reloadTick, setReloadTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    async function loadWorkspace() {
      if (isPreview) {
        setCollection({
          id: 'preview',
          title: '设定集（预览）',
          slug: 'preview-collection',
          description: '独立设定集预览',
          status: 'active',
          owner_id: 'preview',
          workspace_root: '',
          revision: 1,
          metadata: {},
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        })
        setProjects([])
        setNotes([])
        setLoading(false)
        return
      }

      setLoading(true)
      setLoadError(null)
      try {
        const [loadedCollection, loadedProjects, loadedDocs] = await Promise.all([
          getSettingCollection(settingCollectionId),
          listCollectionProjects(settingCollectionId),
          listCollectionDocuments(settingCollectionId),
        ])
        if (cancelled) return

        // Fetch contents for all documents in parallel - failure must not be masked by empty strings!
        const contentEntries = await Promise.all(
          loadedDocs.map(async (doc) => {
            const res = await readDocumentContent(doc.id)
            return [doc.id, res.content] as const
          })
        )
        if (cancelled) return

        const contentMap = new Map(contentEntries)
        const loadedNotes = loadedDocs.map((doc) =>
          documentToSettingNote(doc, contentMap.get(doc.id) ?? '')
        )

        setCollection(loadedCollection)
        setProjects(loadedProjects)
        setNotes(loadedNotes)
        setLoading(false)
      } catch (err: unknown) {
        console.error('loadWorkspace error:', err)
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) {
          setLoadError('未找到该设定集，或无权访问。')
        } else {
          setLoadError('设定集加载失败，请刷新后重试。')
        }
        setLoading(false)
      }
    }

    if (settingCollectionId) {
      void loadWorkspace()
    }
    return () => {
      cancelled = true
    }
  }, [settingCollectionId, isPreview, reloadTick])

  const isArchived = collection?.status === 'archived'

  async function handleSaveNoteContent(
    noteId: string,
    content: string,
    expectedVersionId?: string,
    comments?: OutlineComment[],
  ): Promise<{ versionId: string } | 'conflict' | false> {
    if (isArchived) return false
    if (isPreview) return { versionId: 'preview-ver' }
    const currentNote = notes.find((n) => n.id === noteId)
    if (!currentNote) return false
    const versionToExpect = expectedVersionId || currentNote.versionId
    if (!versionToExpect) return false

    setSaveStatus('saving')
    try {
      const newVersion = await writeDocument(noteId, {
        content,
        expected_current_version_id: versionToExpect,
        setting_comments: comments ?? currentNote.comments ?? [],
      })
      if (!newVersion) return false
      setCollection((prev) => (prev ? { ...prev, revision: prev.revision + 1 } : prev))
      setNotes((prev) =>
        prev.map((n) => (n.id === noteId ? { ...n, body: content, comments: comments ?? n.comments, versionId: newVersion.id } : n))
      )
      setSaveStatus('saved')
      setConflict(null)
      return { versionId: newVersion.id }
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        // Optimistic concurrency conflict: retain local draft!
        setSaveStatus('conflict')
        setConflict({
          noteId,
          noteTitle: currentNote.title,
          localDraft: content,
        })
        return 'conflict'
      }
      setSaveStatus('error')
      return false
    }
  }

  async function handleRenameNote(noteId: string, newTitle: string): Promise<boolean> {
    if (isArchived) return false
    try {
      await patchDocument(noteId, { title: newTitle })
      setCollection((prev) => (prev ? { ...prev, revision: prev.revision + 1 } : prev))
      setNotes((prev) =>
        prev.map((n) => (n.id === noteId ? { ...n, title: newTitle } : n))
      )
      return true
    } catch {
      return false
    }
  }

  async function handleCreateNote(
    category: SettingCategory,
    title: string,
    tempId: string
  ): Promise<SettingNote | null> {
    if (isArchived) return null
    const type = documentTypeForCategory(category)
    const slug = slugify(title)
    const path = `${category}/${slug}-${Date.now().toString(36)}.md`
    try {
      const doc = await createCollectionDocument(settingCollectionId, {
        type,
        title,
        path,
        content: '',
        metadata: { category },
      })
      setCollection((prev) => (prev ? { ...prev, revision: prev.revision + 1 } : prev))
      const createdNote: SettingNote = {
        id: doc.id,
        documentId: doc.id,
        versionId: doc.current_version_id || undefined,
        category,
        title,
        body: '',
        comments: [],
      }
      setNotes((prev) =>
        prev.map((n) => (n.id === tempId ? createdNote : n))
      )
      return createdNote
    } catch {
      return null
    }
  }

  async function handleDeleteNotes(noteIds: string[]): Promise<string[]> {
    if (isArchived) return []
    const results = await Promise.allSettled(noteIds.map(id => deleteDocument(id)))
    const succeeded = noteIds.filter((_, index) => results[index].status === 'fulfilled')
    setNotes(prev => prev.filter(note => !succeeded.includes(note.id)))
    return succeeded
  }

  async function handleRestoreNotes(noteIds: string[]): Promise<string[]> {
    if (isArchived) return []
    const results = await Promise.allSettled(noteIds.map(async id => {
      const document = await undeleteDocument(id)
      const body = await readDocumentContent(id)
      return documentToSettingNote(document, body.content)
    }))
    const restored = results.flatMap(result => result.status === 'fulfilled' ? [result.value] : [])
    setNotes(prev => [...prev.filter(note => !restored.some(item => item.id === note.id)), ...restored])
    return restored.map(note => note.id)
  }

  async function handleApplyProposal(
    proposal: SettingChangeProposal
  ): Promise<{ versionId: string; documentId: string } | 'conflict' | false> {
    if (isArchived || isPreview || !settingCollectionId) return false
    try {
      const doc = await applySettingProposal(settingCollectionId, proposal)
      setCollection((prev) => (prev ? { ...prev, revision: prev.revision + 1 } : prev))
      setNotes((prev) => {
        const existingIndex = prev.findIndex((n) => n.id === doc.id)
        const updatedNote = documentToSettingNote(doc, proposal.proposed_content)
        if (existingIndex >= 0) {
          const next = [...prev]
          next[existingIndex] = updatedNote
          return next
        }
        return [...prev, updatedNote]
      })
      setSaveStatus('saved')
      setConflict(null)
      return {
        versionId: doc.current_version_id || '',
        documentId: doc.id,
      }
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        setSaveStatus('conflict')
        setConflict({
          noteId: proposal.target_document_id || proposal.id,
          noteTitle: proposal.title,
          localDraft: proposal.proposed_content,
        })
        return 'conflict'
      }
      setSaveStatus('error')
      return false
    }
  }

  // Conflict banner actions:
  async function handleReloadConflict() {
    if (!conflict) return
    try {
      const [freshDoc, freshContent] = await Promise.all([
        getDocument(conflict.noteId),
        readDocumentContent(conflict.noteId),
      ])
      // The author explicitly chose to replace this draft. Otherwise the child
      // editor restores its stale recovery copy over the freshly loaded text.
      sessionStorage.removeItem(`guranovel:setting-draft-recovery:${conflict.noteId}`)
      sessionStorage.removeItem(`guranovel:setting-feedback-recovery:${conflict.noteId}`)
      setNotes((prev) =>
        prev.map((n) =>
          n.id === conflict.noteId
            ? {
                ...n,
                body: freshContent.content,
                versionId: freshDoc.current_version_id || undefined,
              }
            : n
        )
      )
      setConflict(null)
      setSaveStatus('saved')
    } catch {
      setSaveStatus('error')
    }
  }

  async function handleOverwriteConflict() {
    if (!conflict) return
    try {
      const freshDoc = await getDocument(conflict.noteId)
      if (!freshDoc.current_version_id) return
      const ver = await writeDocument(conflict.noteId, {
        content: conflict.localDraft,
        expected_current_version_id: freshDoc.current_version_id,
      })
      setCollection((prev) => (prev ? { ...prev, revision: prev.revision + 1 } : prev))
      setNotes((prev) =>
        prev.map((n) =>
          n.id === conflict.noteId
            ? { ...n, body: conflict.localDraft, versionId: ver.id }
            : n
        )
      )
      setConflict(null)
      setSaveStatus('saved')
    } catch {
      setSaveStatus('error')
    }
  }

  function handleDismissConflict() {
    setConflict(null)
  }

  if (loading) {
    return (
      <div className="setting-workspace-page studio" aria-busy="true">
        <header className="setting-workspace-header">
          <span className="setting-workspace-loading">正在加载设定集…</span>
        </header>
        <div className="setting-workspace-fallback-navigation">{homeLink}</div>
      </div>
    )
  }

  if (loadError || !collection) {
    return (
      <div className="setting-workspace-page studio" role="alert">
        <header className="setting-workspace-header">
          <h1 className="setting-workspace-title">设定集加载失败</h1>
        </header>
        <div className="setting-workspace-error-body">
          {homeLink}
          <p>{loadError || '未找到该设定集'}</p>
          <div className="setting-workspace-error-actions" style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <button
              type="button"
              className="btn-conflict-reload"
              onClick={() => setReloadTick((t) => t + 1)}
            >
              重试加载
            </button>
            <Link to="/">返回主页</Link>
          </div>
        </div>
      </div>
    )
  }

  const saveStatusMessage =
    isArchived
      ? ''
      : saveStatus === 'saving'
        ? '正在保存…'
        : saveStatus === 'conflict'
          ? '保存冲突（草稿已保留）'
          : saveStatus === 'error'
            ? '保存失败'
            : ''

  return (
    <div className="setting-workspace-page studio">
      <header className="setting-workspace-header" aria-label="设定集工作区导航">
        <div className="setting-workspace-header-title">
          <h1 className="setting-workspace-title" title={collection.title}>{collection.title}</h1>
          {isArchived && (
            <span className="setting-badge-archived" role="status">
              已归档 · 只读
            </span>
          )}
        </div>

        <div className="setting-workspace-header-meta">
          <span
            className="setting-pill-novels"
            title={projects.length ? `关联小说：${projects.map((p) => p.title).join('、')}` : '暂无关联小说'}
          >
            关联小说 ({projects.length})
          </span>
        </div>
      </header>

      {navigationError && <p className="setting-navigation-error" role="alert">{navigationError}</p>}

      {conflict && (
        <div className="setting-conflict-banner" role="alert">
          <div className="setting-conflict-info">
            <strong>版本冲突</strong>
            <span>
              条目「{conflict.noteTitle}」已被其他操作或新任务更新。您的本地草稿已保留，未被覆盖。
            </span>
          </div>
          <div className="setting-conflict-actions">
            <button
              type="button"
              className="btn-conflict-reload"
              onClick={handleReloadConflict}
            >
              拉取最新版本覆盖草稿
            </button>
            <button
              type="button"
              className="btn-conflict-overwrite"
              onClick={handleOverwriteConflict}
            >
              以本地草稿覆盖服务端
            </button>
            <button
              type="button"
              className="btn-conflict-dismiss"
              onClick={handleDismissConflict}
            >
              保留本地草稿继续编辑
            </button>
          </div>
        </div>
      )}

      <section className="setting-workspace-body" aria-label="设定集正文">
        <StudioSetting
          ref={editor}
          onAssistantContext={setAssistantContext}
          libraryFooter={homeLink}
          preview={isPreview}
          hidden={false}
          collection={collection}
          backendNotes={isPreview ? undefined : notes}
          readOnly={isArchived}
          referencingProjects={projects}
          onSaveNoteContent={isPreview ? undefined : handleSaveNoteContent}
          onApplyProposal={isPreview ? undefined : handleApplyProposal}
          onRenameNote={isPreview ? undefined : handleRenameNote}
          onCreateNote={isPreview ? undefined : handleCreateNote}
          onDeleteNotes={isPreview ? undefined : handleDeleteNotes}
          onRestoreNotes={isPreview ? undefined : handleRestoreNotes}
          syncUrlParams={true}
          defaultPinned={true}
          saveStatusText={saveStatusMessage}
          onTyping={() => {}}
        />
      </section>
      <GlobalAssistant key={settingCollectionId} collection={{ id: settingCollectionId, title: collection.title, ...assistantContext }} preview={isPreview} currentView="SettingCollection" />
    </div>
  )
}
