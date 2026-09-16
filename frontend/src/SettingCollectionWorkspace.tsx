import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  createCollectionDocument,
  deleteDocument,
  getDocument,
  getSettingCollection,
  listCollectionDocuments,
  listCollectionProjects,
  patchDocument,
  readDocumentContent,
  writeDocument,
  type Document,
  type Project,
  type SettingCollection,
} from './api/client'
import StudioSetting from './StudioSetting'
import {
  categoryNames,
  type SettingCategory,
  type SettingNote,
} from './settingNotes'
import { type OutlineComment } from './studioPreview'
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
  const comments = Array.isArray(doc.metadata?.comments) ? (doc.metadata.comments as unknown as OutlineComment[]) : []
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
  const [collection, setCollection] = useState<SettingCollection | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [notes, setNotes] = useState<SettingNote[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveStatus, setSaveStatus] = useState<'saved' | 'saving' | 'conflict' | 'error'>('saved')
  const [conflict, setConflict] = useState<ConflictState | null>(null)

  useEffect(() => {
    let cancelled = false
    async function loadWorkspace() {
      setLoading(true)
      setLoadError(null)
      try {
        const [loadedCollection, loadedProjects, loadedDocs] = await Promise.all([
          getSettingCollection(settingCollectionId),
          listCollectionProjects(settingCollectionId),
          listCollectionDocuments(settingCollectionId),
        ])
        if (cancelled) return

        // Fetch contents for all documents in parallel
        const contentEntries = await Promise.all(
          loadedDocs.map(async (doc) => {
            try {
              const res = await readDocumentContent(doc.id)
              return [doc.id, res.content] as const
            } catch {
              return [doc.id, ''] as const
            }
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
  }, [settingCollectionId])

  const isArchived = collection?.status === 'archived'

  async function handleSaveNoteContent(
    noteId: string,
    content: string,
    expectedVersionId?: string
  ): Promise<{ versionId: string } | 'conflict' | false> {
    if (isArchived) return false
    const currentNote = notes.find((n) => n.id === noteId)
    if (!currentNote) return false
    const versionToExpect = expectedVersionId || currentNote.versionId
    if (!versionToExpect) return false

    setSaveStatus('saving')
    try {
      const newVersion = await writeDocument(noteId, {
        content,
        expected_current_version_id: versionToExpect,
      })
      if (!newVersion) return false
      setCollection((prev) => (prev ? { ...prev, revision: prev.revision + 1 } : prev))
      setNotes((prev) =>
        prev.map((n) => (n.id === noteId ? { ...n, body: content, versionId: newVersion.id } : n))
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

  async function handleDeleteNotes(noteIds: string[]): Promise<boolean> {
    if (isArchived) return false
    try {
      await Promise.all(noteIds.map((id) => deleteDocument(id)))
      setCollection((prev) => (prev ? { ...prev, revision: prev.revision + 1 } : prev))
      setNotes((prev) => prev.filter((n) => !noteIds.includes(n.id)))
      return true
    } catch {
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
          <Link to="/" className="setting-workspace-back">← 返回项目列表</Link>
          <span className="setting-workspace-loading">正在加载设定集…</span>
        </header>
        <div className="setting-workspace-loading-body">
          <p>正在读取设定集文件与关联数据…</p>
        </div>
      </div>
    )
  }

  if (loadError || !collection) {
    return (
      <div className="setting-workspace-page studio" role="alert">
        <header className="setting-workspace-header">
          <Link to="/" className="setting-workspace-back">← 返回项目列表</Link>
          <h1 className="setting-workspace-title">设定集加载失败</h1>
        </header>
        <div className="setting-workspace-error-body">
          <p>{loadError || '未找到该设定集'}</p>
          <Link to="/">返回主页</Link>
        </div>
      </div>
    )
  }

  const saveStatusMessage =
    isArchived
      ? '已归档 · 只读'
      : saveStatus === 'saving'
        ? '正在保存…'
        : saveStatus === 'conflict'
          ? '保存冲突（草稿已保留）'
          : saveStatus === 'error'
            ? '保存失败'
            : '已保存'

  return (
    <div className="setting-workspace-page studio">
      <header className="setting-workspace-header" aria-label="设定集工作区导航">
        <Link to="/" className="setting-workspace-back" aria-label="返回项目列表">
          ← 项目
        </Link>
        <div className="setting-workspace-header-title">
          <h1 className="setting-workspace-title">{collection.title}</h1>
          <span className="setting-badge-revision" aria-label={`版本 r${collection.revision}`} title={`修订版本 r${collection.revision}`}>
            r{collection.revision}
          </span>
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
          <span className="setting-impact-warning" title="修改将影响关联小说后续启动的新任务">
            ⚠️ 修改将影响关联小说后续启动的新任务
          </span>
          <span className={`setting-save-pill is-${saveStatus}`} role="status">
            {saveStatusMessage}
          </span>
        </div>
      </header>

      {conflict && (
        <div className="setting-conflict-banner" role="alert">
          <div className="setting-conflict-info">
            <strong>版本冲突（409 Conflict）</strong>
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

      <main className="setting-workspace-body">
        <StudioSetting
          preview={false}
          hidden={false}
          collection={collection}
          backendNotes={notes}
          readOnly={isArchived}
          onSaveNoteContent={handleSaveNoteContent}
          onRenameNote={handleRenameNote}
          onCreateNote={handleCreateNote}
          onDeleteNotes={handleDeleteNotes}
          syncUrlParams={true}
          defaultPinned={true}
          saveStatusText={saveStatusMessage}
          onTyping={() => {}}
        />
      </main>
    </div>
  )
}
