import { useCallback, useEffect, useRef, useState, type MouseEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ApiError,
  archiveSettingCollection,
  applySettingProposal,
  createCollectionDocument,
  createSettingDocumentRestorePoint,
  deleteDocument,
  undeleteDocument,
  getDocument,
  getSettingCollection,
  listCollectionDocuments,
  listCollectionProjects,
  listDocumentVersions,
  listSettingDocumentRestorePoints,
  patchDocument,
  readDocumentContent,
  restoreSettingDocumentRestorePoint,
  writeDocument,
  type Document,
  type DocumentVersion,
  type Project,
  type SettingChangeProposal,
  type SettingCollection,
  type SettingDocumentRestorePoint,
  type SettingDocumentRestorePointRequest,
} from './api/client'
import StudioSetting, { type SettingRestorePointStatus, type StudioSettingHandle } from './StudioSetting'
import GlobalAssistant from './GlobalAssistant'
import AssociatedNovelsDialog from './AssociatedNovelsDialog'
import SettingHistoryDialog from './SettingHistoryDialog'
import SettingArchiveDialog from './SettingArchiveDialog'
import SettingCollectionMenu from './SettingCollectionMenu'
import type { SettingTrashEntry } from './SettingTrashDialog'
import {
  categoryNames,
  categoryFromDocument,
  type SettingCategory,
  type SettingNote,
} from './settingNotes'
import { restoreOutlineComments, type OutlineComment } from './studioPreview'
import './studioSetting.css'

interface ConflictState {
  noteId: string
  noteTitle: string
  localDraft: string
  comments?: OutlineComment[]
}

function documentTypeForCategory(category: SettingCategory) {
  return category === 'world' ? ('world_overview' as const) : ('character_profile' as const)
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
    creationRequestId: typeof doc.metadata?.__creation_request_id === 'string' ? doc.metadata.__creation_request_id : undefined,
  }
}

export default function SettingCollectionWorkspace() {
  const { settingCollectionId = '' } = useParams<{ settingCollectionId: string }>()
  return <SettingCollectionSession key={settingCollectionId} settingCollectionId={settingCollectionId} />
}

function SettingCollectionSession({ settingCollectionId }: { settingCollectionId: string }) {
  const navigate = useNavigate()
  const editor = useRef<StudioSettingHandle>(null)
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
  const [projects, setProjects] = useState<Project[] | null>(null)
  const [projectsStatus, setProjectsStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [associationsOpen, setAssociationsOpen] = useState(false)
  const associationsLoaded = useCallback((items: Project[]) => { setProjects(items); setProjectsStatus('ready') }, [])
  const [notes, setNotes] = useState<SettingNote[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveStatus, setSaveStatus] = useState<'saved' | 'saving' | 'conflict' | 'error'>('saved')
  const [conflict, setConflict] = useState<ConflictState | null>(null)
  const [reloadTick, setReloadTick] = useState(0)
  const [history, setHistory] = useState<{ note: SettingNote; blocked?: string } | null>(null)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const [operationBusy, setOperationBusy] = useState(false)
  const [operationError, setOperationError] = useState('')
  const [pointNotice, setPointNotice] = useState<SettingRestorePointStatus | null>(null)
  const [pointBusy, setPointBusy] = useState(false)
  const operation = useRef(false)
  const pendingPoints = useRef(new Map<string, SettingDocumentRestorePointRequest>())

  useEffect(() => {
    if (!pointNotice) return
    const timer = window.setTimeout(() => setPointNotice(null), 2500)
    return () => window.clearTimeout(timer)
  }, [pointNotice])

  function pendingPoint(documentId: string) {
    const cached = pendingPoints.current.get(documentId)
    if (cached) return cached
    try {
      const stored = JSON.parse(sessionStorage.getItem(`guranovel:setting-restore-point-pending:${documentId}`) || 'null')
      if (stored && typeof stored.request_id === 'string' && typeof stored.expected_current_version_id === 'string') {
        const request = { request_id: stored.request_id, expected_current_version_id: stored.expected_current_version_id }
        pendingPoints.current.set(documentId, request)
        return request
      }
    } catch { /* The in-memory request still protects retries when storage is unavailable. */ }
  }

  function rememberPoint(documentId: string, request?: SettingDocumentRestorePointRequest) {
    if (request) pendingPoints.current.set(documentId, request)
    else pendingPoints.current.delete(documentId)
    try {
      const key = `guranovel:setting-restore-point-pending:${documentId}`
      if (request) sessionStorage.setItem(key, JSON.stringify(request))
      else sessionStorage.removeItem(key)
    } catch { /* Keep the request identity in memory for this session. */ }
  }

  async function refreshAssociations(): Promise<boolean> {
    if (isPreview) return true
    setProjectsStatus('loading')
    try {
      setProjects(await listCollectionProjects(settingCollectionId))
      setProjectsStatus('ready')
      return true
    } catch { setProjectsStatus('error'); return false }
  }

  async function openHistory(note: SettingNote) {
    if (!note.documentId || operation.current) return
    operation.current = true; setOperationBusy(true); setOperationError('')
    try {
      const saved = isArchived || !editor.current || await editor.current.flush()
      const snapshot = editor.current?.getNoteSnapshot?.(note.documentId) ?? note
      setHistory({ note: { ...snapshot }, blocked: saved ? undefined : '当前草稿未能保存。你仍可查看历史；请关闭窗口并同步草稿后再恢复。' })
      void refreshAssociations()
    } catch { setOperationError('历史存档暂时无法打开，当前草稿已保留。请重试。') }
    finally { operation.current = false; setOperationBusy(false) }
  }

  async function createRestorePoint(note: SettingNote): Promise<boolean> {
    if (!note.documentId || isArchived || operation.current) return false
    const documentId = note.documentId
    operation.current = true; setOperationBusy(true); setOperationError(''); setPointNotice(null); setPointBusy(true)
    try {
      if (!editor.current || !await editor.current.flush()) throw new Error('当前草稿未能保存，已暂停存档。草稿仍保留，请重试同步后再存档。')
      const snapshot = editor.current.getNoteSnapshot?.(documentId)
      if (!snapshot?.versionId) throw new Error('当前内容尚未保存，已暂停存档。请重试同步后再存档。')
      const notice = { documentId, versionId: snapshot.versionId, title: snapshot.title, body: snapshot.body, commentsKey: JSON.stringify(snapshot.comments || []) }
      let previousRequest = pendingPoint(documentId)
      try {
        const points = await listSettingDocumentRestorePoints(documentId)
        if (previousRequest) {
          const unresolved = previousRequest
          const confirmed = points.some(point => point.id === unresolved.request_id
            && point.document_id === documentId && point.version_id === unresolved.expected_current_version_id
            && point.version.id === point.version_id && point.version.document_id === documentId)
          if (confirmed) { rememberPoint(documentId); previousRequest = undefined }
        }
        if (!previousRequest && points.some(point => point.matches_current && point.version_id === snapshot.versionId)) { setPointNotice(notice); return true }
      } catch (failure) {
        // Retrying an unresolved request is safe even while the list cannot load.
        if (!previousRequest) throw new Error('存档列表未能读取，当前草稿已保留。请重试存档。', { cause: failure })
      }
      const request = previousRequest ?? { request_id: crypto.randomUUID(), expected_current_version_id: snapshot.versionId }
      rememberPoint(documentId, request)
      const matchesRequest = (point: SettingDocumentRestorePoint) => point.id === request.request_id && point.document_id === documentId
        && point.version_id === request.expected_current_version_id && point.version.id === point.version_id && point.version.document_id === documentId
      let point: SettingDocumentRestorePoint
      try {
        point = await createSettingDocumentRestorePoint(documentId, request)
      } catch (failure) {
        let existing: SettingDocumentRestorePoint | undefined
        try {
          existing = (await listSettingDocumentRestorePoints(documentId)).find(matchesRequest)
          if (!existing && failure instanceof ApiError && [400, 403, 404, 409, 422].includes(failure.status)) rememberPoint(documentId)
        } catch { /* Keep the exact request identity until its result can be confirmed. */ }
        if (!existing) throw new Error(failure instanceof ApiError && failure.status === 409
          ? '当前内容已更新，存档结果未能确认。请重试核对。' : '存档结果未能确认，当前草稿已保留。重试会核对同一次存档。', { cause: failure })
        point = existing
      }
      if (!matchesRequest(point)) throw new Error('存档结果未能确认，当前草稿已保留。请重试核对。')
      rememberPoint(documentId)
      if (point.version_id !== snapshot.versionId) throw new Error('上次存档已核实，当前内容尚未存档。请再次点击存档。')
      setPointNotice(notice)
      return true
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : '存档结果未能确认，当前草稿已保留。请重试核对。'
      setOperationError(message)
      return false
    } finally { operation.current = false; setOperationBusy(false); setPointBusy(false) }
  }

  async function restoreVersion(point: SettingDocumentRestorePoint, content: string): Promise<boolean> {
    if (!history || isArchived || operation.current) return false
    const documentId = history.note.documentId!
    const expectedVersionId = history.note.versionId
    const version = point.version
    if (!expectedVersionId || point.document_id !== documentId || point.version_id !== version.id || version.document_id !== documentId || history.blocked) return false
    operation.current = true; setOperationBusy(true); setOperationError('')
    try {
      if (projectsStatus !== 'ready' && !await refreshAssociations()) throw new Error('关联小说未能读取，请重新加载后再恢复。')
      if (editor.current && !await editor.current.flush()) throw new Error('当前草稿未能保存，已暂停恢复。请关闭窗口重试同步。')
      const snapshot = editor.current?.getNoteSnapshot?.(documentId)
      if (!snapshot || snapshot.versionId !== expectedVersionId) throw new Error('当前版本已更新，草稿已保留。请重新打开历史核对。')
      let restored: DocumentVersion
      let fresh: Document
      try {
        restored = await restoreSettingDocumentRestorePoint(documentId, point.id, { expected_current_version_id: expectedVersionId, change_summary: `恢复到存档 ${point.point_number}` })
      } catch (failure) {
        try {
          fresh = await getDocument(documentId)
          const current = fresh.current_version ?? (await listDocumentVersions(documentId)).find(item => item.id === fresh.current_version_id)
          if (!current || current.document_id !== documentId || current.parent_version_id !== expectedVersionId || current.restored_from_version_id !== version.id || current.restored_from_restore_point_id !== point.id) throw failure
          restored = current
        } catch {
          throw new Error(failure instanceof ApiError && failure.status === 409 ? '版本已更新，当前草稿已保留。请重新打开历史核对。' : '恢复结果未能确认，当前文稿已保留。可重试核对。')
        }
      }
      if (restored.document_id !== documentId || restored.parent_version_id !== expectedVersionId || restored.restored_from_version_id !== version.id || restored.restored_from_restore_point_id !== point.id) throw new Error('恢复结果未能确认，当前文稿已保留。可重试核对。')
      try { fresh = await getDocument(documentId) } catch { throw new Error('恢复结果未能确认，当前文稿已保留。可重试核对。') }
      if (fresh.current_version_id !== restored.id || fresh.setting_collection_id !== settingCollectionId) throw new Error('恢复后版本又有更新，当前草稿已保留。请重新打开历史核对。')
      const comments = restoreOutlineComments(fresh.metadata?.comments, content)
      editor.current?.replaceSavedNote?.(documentId, content, restored.id, comments, { body: snapshot.body, comments: snapshot.comments || [] })
      setNotes(previous => previous.map(note => note.id === documentId || note.documentId === documentId ? { ...note, body: content, comments, versionId: restored.id } : note))
      setSaveStatus('saved'); setConflict(null)
      setCollection(previous => previous ? { ...previous, revision: previous.revision + 1 } : previous)
      return true
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : '恢复结果未能确认，当前文稿已保留。可重试核对。'
      setOperationError(message)
      throw new Error(message, { cause: failure })
    } finally { operation.current = false; setOperationBusy(false) }
  }

  async function archiveCollection(): Promise<boolean> {
    if (isPreview || isArchived || operation.current) return false
    operation.current = true; setOperationBusy(true); setOperationError('')
    try {
      if (editor.current && !await editor.current.flush()) throw new Error('当前条目未能保存，已暂停归档。草稿仍保留，请关闭窗口重试同步。')
      let archived: SettingCollection
      try { archived = await archiveSettingCollection(settingCollectionId) }
      catch {
        try {
          archived = await getSettingCollection(settingCollectionId)
          if (archived.status !== 'archived') throw new Error('not archived')
        } catch { throw new Error('归档结果未能确认，当前文稿已保留。可重试核对。') }
      }
      if (archived.id !== settingCollectionId || archived.status !== 'archived') throw new Error('归档结果未能确认，当前文稿已保留。可重试核对。')
      setCollection(archived)
      return true
    } catch (failure) {
      const message = failure instanceof Error ? failure.message : '归档结果未能确认，当前文稿已保留。可重试核对。'
      setOperationError(message)
      throw new Error(message, { cause: failure })
    } finally { operation.current = false; setOperationBusy(false) }
  }

  const impact = <div className="setting-lifecycle-impact" aria-label="受影响的关联小说">
    {projectsStatus === 'loading' ? <span role="status">正在读取关联小说…</span> : projectsStatus === 'error' ? <div role="alert">关联小说未能读取。<button type="button" onClick={() => { void refreshAssociations() }}>重新加载关联小说</button></div>
      : <><span>关联小说 · {projects?.length ?? 0} 部</span>{projects?.length ? <ul>{projects.map(project => <li key={project.id}>{project.title}</li>)}</ul> : <p>暂无关联小说</p>}</>}
  </div>

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
        const [loadedCollection, loadedDocs] = await Promise.all([
          getSettingCollection(settingCollectionId),
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

  useEffect(() => {
    let cancelled = false
    async function loadProjects() {
      if (isPreview) { setProjectsStatus('ready'); return }
      setProjectsStatus('loading')
      try {
        const items = await listCollectionProjects(settingCollectionId)
        if (!cancelled) { setProjects(items); setProjectsStatus('ready') }
      } catch { if (!cancelled) setProjectsStatus('error') }
    }
    void loadProjects()
    return () => { cancelled = true }
  }, [settingCollectionId, isPreview])

  async function loadTrash(): Promise<SettingTrashEntry[]> {
    const documents = await listCollectionDocuments(settingCollectionId, { deleted: true })
    return documents.map(document => ({ id: document.id, title: document.title || '未命名条目', category: categoryFromDocument(document), deletedAt: document.deleted_at }))
  }

  const isArchived = collection?.status === 'archived'

  async function handleSaveNoteContent(
    noteId: string,
    content: string,
    expectedVersionId?: string,
    comments?: OutlineComment[],
  ): Promise<{ versionId: string } | 'conflict' | false> {
    if (isArchived) return false
    if (isPreview) return { versionId: 'preview-ver' }
    const currentNote = notes.find((n) => n.id === noteId || n.documentId === noteId)
    if (!currentNote) return false
    const versionToExpect = expectedVersionId || currentNote.versionId
    if (!versionToExpect) return false
    const superseded = () => {
      const latest = editor.current?.getNoteSnapshot?.(noteId)
      return latest && latest.versionId !== versionToExpect
    }

    setSaveStatus('saving')
    try {
      const newVersion = await writeDocument(noteId, {
        content,
        expected_current_version_id: versionToExpect,
        setting_comments: comments ?? currentNote.comments ?? [],
      })
      if (!newVersion) return false
      if (superseded()) return { versionId: newVersion.id }
      setCollection((prev) => (prev ? { ...prev, revision: prev.revision + 1 } : prev))
      setNotes((prev) =>
        prev.map((n) => ((n.id === noteId || n.documentId === noteId) && n.versionId === versionToExpect ? { ...n, body: content, comments: comments ?? n.comments, versionId: newVersion.id } : n))
      )
      setSaveStatus('saved')
      setConflict(null)
      return { versionId: newVersion.id }
    } catch (err: unknown) {
      if (superseded()) return err instanceof ApiError && err.status === 409 ? 'conflict' : false
      if (err instanceof ApiError && err.status === 409) {
        // Optimistic concurrency conflict: retain local draft!
        setSaveStatus('conflict')
        setConflict({
          noteId,
          noteTitle: currentNote.title,
          localDraft: content,
          comments: comments ?? currentNote.comments ?? [],
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
        prev.map((n) => (n.id === noteId || n.documentId === noteId ? { ...n, title: newTitle } : n))
      )
      return true
    } catch {
      return false
    }
  }

  async function handleCreateNote(
    category: SettingCategory,
    title: string,
    tempId: string,
    content = '',
    comments: OutlineComment[] = [],
  ): Promise<SettingNote | null> {
    if (isArchived) return null
    const type = documentTypeForCategory(category)
    const path = `${category}/${tempId}.md`
    try {
      const doc = await createCollectionDocument(settingCollectionId, {
        type,
        client_request_id: tempId,
        title,
        path,
        content,
        metadata: { category, comments },
      })
      setCollection((prev) => (prev ? { ...prev, revision: prev.revision + 1 } : prev))
      const createdNote: SettingNote = {
        id: tempId,
        creationRequestId: tempId,
        documentId: doc.id,
        versionId: doc.current_version_id || undefined,
        category,
        title,
        body: content,
        comments,
      }
      setNotes((prev) => [...prev.filter(n => n.id !== tempId && n.id !== doc.id), createdNote])
      return createdNote
    } catch {
      return null
    }
  }

  async function handleDeleteNotes(noteIds: string[]): Promise<string[]> {
    if (isArchived) return []
    const results = await Promise.allSettled(noteIds.map(id => deleteDocument(id)))
    const confirmed = new Set(noteIds.filter((_, index) => results[index].status === 'fulfilled'))
    if (results.some(result => result.status === 'rejected')) {
      // A lost response does not tell us whether DELETE committed. Reconcile the
      // durable trash before leaving a deleted document editable in the list.
      try {
        const trash = await listCollectionDocuments(settingCollectionId, { deleted: true })
        trash.forEach(document => { if (noteIds.includes(document.id)) confirmed.add(document.id) })
      } catch { /* Keep unconfirmed entries visible for an explicit retry. */ }
    }
    const succeeded = [...confirmed]
    setNotes(prev => prev.filter(note => !succeeded.includes(note.documentId || note.id)))
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
    for (const [index, result] of results.entries()) {
      if (result.status !== 'rejected') continue
      try {
        const document = await getDocument(noteIds[index])
        if (document.deleted_at !== null) continue
        const body = await readDocumentContent(document.id)
        restored.push(documentToSettingNote(document, body.content))
      } catch { /* Retry remains available until both state and body are readable. */ }
    }
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
      const comments = restoreOutlineComments(freshDoc.metadata?.comments, freshContent.content)
      editor.current?.replaceSavedNote?.(conflict.noteId, freshContent.content, freshDoc.current_version_id || '', comments)
      setNotes((prev) =>
        prev.map((n) =>
          n.id === conflict.noteId || n.documentId === conflict.noteId
            ? {
                ...n,
                body: freshContent.content,
                versionId: freshDoc.current_version_id || undefined,
                comments,
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
      const draft = editor.current?.getNoteSnapshot?.(conflict.noteId)
      const content = draft?.body ?? conflict.localDraft
      const expected = { body: content, comments: draft?.comments ?? conflict.comments ?? [] }
      const comments = restoreOutlineComments(expected.comments, content)
      const freshDoc = await getDocument(conflict.noteId)
      if (!freshDoc.current_version_id) return
      const ver = await writeDocument(conflict.noteId, {
        content,
        expected_current_version_id: freshDoc.current_version_id,
        setting_comments: comments,
      })
      editor.current?.replaceSavedNote?.(conflict.noteId, content, ver.id, comments, expected)
      setCollection((prev) => (prev ? { ...prev, revision: prev.revision + 1 } : prev))
      setNotes((prev) =>
        prev.map((n) =>
          n.id === conflict.noteId || n.documentId === conflict.noteId
            ? { ...n, body: content, versionId: ver.id, comments }
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
          <button type="button"
            className="setting-pill-novels"
            title={projectsStatus === 'error' ? '关联小说未能更新，点击重新查看' : undefined}
            aria-disabled={operationBusy}
            onClick={() => { if (!operation.current) setAssociationsOpen(true) }}
          >
            {projects !== null ? `关联小说 (${projects.length})` : projectsStatus === 'error' ? '关联小说 · 重试' : '关联小说…'}
          </button>
          {!isPreview && <SettingCollectionMenu disabled={operationBusy || Boolean(history) || associationsOpen || archiveOpen} onCollaborate={() => editor.current?.openCollaboration?.()} onArchive={isArchived ? undefined : () => { setArchiveOpen(true); void refreshAssociations() }} />}
        </div>
      </header>

      {navigationError && <p className="setting-navigation-error" role="alert">{navigationError}</p>}
      {!history && !archiveOpen && operationError && <p className="setting-navigation-error" role="alert">{operationError}</p>}

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

      <section className="setting-workspace-body" aria-label="设定集正文" inert={operationBusy} aria-busy={operationBusy}>
        <StudioSetting
          key={collection.id}
          ref={editor}
          onAssistantContext={setAssistantContext}
          onOpenHistory={isPreview ? undefined : note => { void openHistory(note) }}
          onCreateRestorePoint={isPreview ? undefined : createRestorePoint}
          restorePointStatus={pointNotice}
          restorePointBusy={pointBusy}
          collaborationInMenu={!isPreview}
          libraryFooter={homeLink}
          preview={isPreview}
          hidden={false}
          collection={collection}
          backendNotes={isPreview ? undefined : notes}
          readOnly={isArchived}
          referencingProjects={projects ?? []}
          onSaveNoteContent={isPreview ? undefined : handleSaveNoteContent}
          onApplyProposal={isPreview ? undefined : handleApplyProposal}
          onRenameNote={isPreview ? undefined : handleRenameNote}
          onCreateNote={isPreview ? undefined : handleCreateNote}
          onDeleteNotes={isPreview ? undefined : handleDeleteNotes}
          onRestoreNotes={isPreview ? undefined : handleRestoreNotes}
          onLoadTrash={isPreview ? undefined : loadTrash}
          onCollaborationApplied={(result, stableId) => {
            const note = { ...documentToSettingNote(result.document, result.content), id: stableId }
            setNotes(previous => [...previous.filter(item => item.id !== stableId && item.documentId !== result.document.id), note])
            setSaveStatus('saved')
          }}
          syncUrlParams={true}
          defaultPinned={true}
          saveStatusText={saveStatusMessage}
          onTyping={() => {}}
        />
      </section>
      {history?.note.documentId && <SettingHistoryDialog documentId={history.note.documentId} title={history.note.title} readOnly={isArchived} restoreBlockedReason={history.blocked} impact={impact} onRestore={restoreVersion} onClose={() => setHistory(null)} />}
      {archiveOpen && <SettingArchiveDialog title={collection.title} readOnly={isArchived} impact={impact} impactReady={projectsStatus === 'ready'} onArchive={archiveCollection} onClose={() => setArchiveOpen(false)} />}
      {associationsOpen && <AssociatedNovelsDialog collectionId={settingCollectionId}
        onProjectsLoaded={associationsLoaded}
        onBeforeOpen={async () => !editor.current || await editor.current.flush()}
        onOpenProject={project => navigate(`/?project=${encodeURIComponent(project.id)}`)}
        onClose={() => setAssociationsOpen(false)} />}
      <GlobalAssistant key={settingCollectionId} collection={{ id: settingCollectionId, title: collection.title, ...assistantContext }} preview={isPreview} currentView="SettingCollection" />
    </div>
  )
}
