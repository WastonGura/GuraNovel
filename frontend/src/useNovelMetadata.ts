import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { updateProject, type Project, type Metadata } from './api/client'
import { emptyNovelForm, type NovelFormValues } from './novelForm'

function fields(project: Project): NovelFormValues {
  return { ...emptyNovelForm, title: project.title, platform: project.target_platform ?? '',
    collectionId: project.setting_collection_id ?? '', cover: typeof project.metadata.cover === 'string' ? project.metadata.cover : '',
    introduction: typeof project.metadata.introduction === 'string' ? project.metadata.introduction : '',
    labels: Array.isArray(project.metadata.labels) ? project.metadata.labels.filter(label => typeof label === 'string').join(', ') : '' }
}
function content(values: NovelFormValues) {
  return { title: values.title.trim(), target_platform: values.platform.trim() || null,
    setting_collection_id: values.collectionId.trim() || null, cover: values.cover.trim(), introduction: values.introduction.trim(),
    labels: values.labels.split(/[,，]/).map(label => label.trim()).filter(Boolean) }
}
const signature = (values: NovelFormValues) => JSON.stringify(content(values))

export function useNovelMetadata(project: Project, onSaved: (project: Project) => void) {
  const storageKey = `guranovel:novel-metadata:${project.id}`
  const [recovery] = useState(() => {
    const initial = fields(project)
    let durable: string | null = null
    try { durable = localStorage.getItem(storageKey) } catch { /* Session recovery can still work. */ }
    for (const location of ['session', 'local']) {
      try {
        const copy: unknown = JSON.parse((location === 'session' ? sessionStorage : localStorage).getItem(storageKey) || 'null')
        if (!copy || typeof copy !== 'object' || !Object.keys(initial).every(key => typeof (copy as Record<string, unknown>)[key] === 'string')) continue
        const restored = Object.fromEntries(Object.keys(initial).map(key => [key, (copy as Record<string, string>)[key]])) as unknown as NovelFormValues
        const repair = !restored.collectionId && Boolean(initial.collectionId)
        if (repair) restored.collectionId = initial.collectionId
        return { values: restored, durable, notice: repair ? '作品需要关联设定集，已恢复原设定集；其他修改仍保留。' : null }
      } catch { /* Try the other recovery location before using server values. */ }
    }
    return { values: initial, durable, notice: null }
  })
  const [values, setValues] = useState(recovery.values)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(recovery.notice)
  const current = useRef({ values, saved: signature(fields(project)), project, timer: 0, durableCopy: recovery.durable,
    pending: null as Promise<boolean> | null, mounted: true })
  const callback = useRef(onSaved)
  useLayoutEffect(() => { callback.current = onSaved })

  function retain() {
    const state = current.current
    if (!state.mounted) return true
    try {
      if (signature(state.values) === state.saved) {
        sessionStorage.removeItem(storageKey)
        const durable = localStorage.getItem(storageKey)
        if (durable && (durable === state.durableCopy || durable === JSON.stringify(state.values))) localStorage.removeItem(storageKey)
      }
      else {
        const copy = JSON.stringify(state.values)
        sessionStorage.setItem(storageKey, copy)
        if (sessionStorage.getItem(storageKey) !== copy) throw new Error('Recovery copy was not retained')
      }
      return true
    } catch {
      if (state.mounted) setError('本地恢复副本不可用，请保持页面打开并检查自动保存。')
      return false
    }
  }
  function keepDraft() {
    const state = current.current
    window.clearTimeout(state.timer)
    if (signature(state.values) === state.saved) return true
    const copy = JSON.stringify(state.values)
    // A verified durable copy also survives closing the browser tab.
    try { localStorage.setItem(storageKey, copy); if (localStorage.getItem(storageKey) === copy) { state.durableCopy = copy; return true } } catch { /* Fall back to this tab's recovery copy. */ }
    try { sessionStorage.setItem(storageKey, copy); if (sessionStorage.getItem(storageKey) === copy) return true } catch { /* Keep the editor open rather than lose the only copy. */ }
    setError('浏览器无法保留修改副本，请复制内容后重试保存。')
    return false
  }
  function flush(): Promise<boolean> {
    const state = current.current
    window.clearTimeout(state.timer)
    if (state.pending) return state.pending
    const work = async () => {
      while (signature(state.values) !== state.saved) {
        const snapshot = content(state.values)
        if (!snapshot.title) { if (state.mounted) setError('请填写标题，修改仍保留在当前页面。'); return false }
        if (state.mounted) { setSaving(true); setError(null) }
        const { introduction, labels, cover, setting_collection_id, ...payload } = snapshot
        if (setting_collection_id !== (state.project.setting_collection_id ?? null) && !setting_collection_id) {
          if (state.mounted) setError('作品需要关联设定集，请选择一个设定集；修改仍保留。')
          return false
        }
        const metadata: Metadata = { ...state.project.metadata, labels }
        if (cover) metadata.cover = cover
        else delete metadata.cover
        if (introduction || Object.hasOwn(state.project.metadata, 'introduction')) metadata.introduction = introduction
        else delete metadata.introduction
        try {
          const updated = await updateProject(project.id, { ...payload, metadata,
            ...(setting_collection_id !== (state.project.setting_collection_id ?? null) ? { setting_collection_id } : {}) })
          state.project = updated; state.saved = JSON.stringify(snapshot)
          // The parent bookshelf must receive a completed save even after this editor closes.
          callback.current(updated)
          retain()
        } catch (cause) {
          const retained = retain()
          const status = typeof cause === 'object' && cause !== null && 'status' in cause ? cause.status : 0
          const reason = status === 409 ? '保存被拒绝，请检查设定集关联后重试。' : status === 422 ? '资料格式未通过校验，请检查输入。'
            : status === 404 ? '作品或设定集已不存在，请核对后重试。' : typeof status === 'number' && status >= 500 ? '服务暂时无法保存，请稍后重试。' : '网络连接失败，请恢复连接后重试。'
          if (state.mounted) setError(`${reason}${retained ? '已保留修改，也可以保留修改后关闭。' : '修改只在当前窗口中，请复制内容后重试。'}`)
          return false
        }
      }
      return true
    }
    state.pending = work().finally(() => {
      state.pending = null
      if (state.mounted) setSaving(false)
    })
    return state.pending
  }
  function change(patch: Partial<NovelFormValues>) {
    const state = current.current
    state.values = { ...state.values, ...patch }
    setValues(state.values); setError(null); retain()
    window.clearTimeout(state.timer)
    state.timer = window.setTimeout(() => { void flush() }, 400)
  }
  useEffect(() => {
    const state = current.current
    state.mounted = true
    if (signature(state.values) !== state.saved) state.timer = window.setTimeout(() => { void flush() }, 400)
    const warn = (event: BeforeUnloadEvent) => {
      if (signature(state.values) !== state.saved || state.pending) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', warn)
    const reconnect = () => { void flush() }
    window.addEventListener('online', reconnect)
    return () => { state.mounted = false; window.clearTimeout(state.timer); window.removeEventListener('beforeunload', warn); window.removeEventListener('online', reconnect) }
    // Each mounted editor is keyed by project identity; its queue survives view switches.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return { values, change, flush, keepDraft, saving, error }
}
