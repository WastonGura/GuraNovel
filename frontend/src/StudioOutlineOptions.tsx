import { useEffect, useRef, useState } from 'react'
import { ApiError, cancelOutlineGeneration, createOutlineGeneration, getOutlineGeneration, listOutlineGenerations,
  readDocumentContent, selectOutlineCandidate, type OutlineGeneration, type OutlineSelection, type SelectedOutline } from './api/client'
import type { useChapterIdea } from './useChapterIdea'
import StudioOutlineSurface, { OutlineChoiceLabel, OutlineRefreshButton, OutlineSendButton } from './StudioOutlineSurface'
import StudioOutlineHistory from './StudioOutlineHistory'
import { TextSweep } from './StudioMotion'
import './studioOutlineOptions.css'

type PendingGeneration = { request_id: string; expected_idea_revision: number }
type PendingSelection = { runId: string; payload: OutlineSelection }
function hasOutlineResults(batch: OutlineGeneration) {
  return batch.status === 'completed' && batch.candidates.length > 0
}
function recover<T>(key: string): T | null {
  try { return JSON.parse(sessionStorage.getItem(key) || 'null') as T | null } catch { return null }
}

export default function StudioOutlineOptions({ projectId, chapterId, idea, beforeSelect, onSelected }: {
  projectId: string; chapterId: string; idea: ReturnType<typeof useChapterIdea>
  beforeSelect: () => Promise<string | null | undefined>
  onSelected: (selection: SelectedOutline, content: string) => void
}) {
  const generationKey = `guranovel:outline-request:${projectId}:${chapterId}`
  const selectionKey = `guranovel:outline-selection:${projectId}:${chapterId}`
  const [pending, setPending] = useState(() => recover<PendingGeneration>(generationKey))
  const [selection, setSelection] = useState(() => recover<PendingSelection>(selectionKey))
  const [batches, setBatches] = useState<OutlineGeneration[]>([])
  const [activeId, setActiveId] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [editingIdea, setEditingIdea] = useState(false)
  const guard = useRef(false)
  const mounted = useRef(false)
  const active = batches.find(batch => batch.id === activeId)
  const latestAttempt = batches[0]
  const ongoing = batches.find(batch => batch.status === 'running' || batch.status === 'uncertain')
  function upsert(batch: OutlineGeneration) {
    if (!mounted.current) return
    setBatches(previous => [batch, ...previous.filter(item => item.id !== batch.id)].sort((a, b) => b.started_at.localeCompare(a.started_at)))
    if (hasOutlineResults(batch)) setActiveId(batch.id)
    if (batch.status !== 'running') {
      const stored = recover<PendingGeneration>(generationKey)
      if (stored?.request_id === batch.id) { sessionStorage.removeItem(generationKey); setPending(null) }
    }
  }
  useEffect(() => {
    let live = true; mounted.current = true
    listOutlineGenerations(projectId, chapterId).then(values => {
      if (!live) return
      const newestFirst = [...values].sort((a, b) => b.started_at.localeCompare(a.started_at))
      setBatches(newestFirst); setActiveId(newestFirst.find(hasOutlineResults)?.id || ''); setLoaded(true)
      const completed = values.find(item => item.id === pending?.request_id && item.status !== 'running')
      if (completed) { sessionStorage.removeItem(generationKey); setPending(null) }
    }).catch(() => { if (live) setError('大纲方案加载失败，已有内容未改动。请刷新重试。') })
    return () => { live = false; mounted.current = false }
    // Pending request is a mount snapshot; task state is subsequently refreshed by ID.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, chapterId, generationKey])
  useEffect(() => {
    if (ongoing?.status !== 'running') return
    let live = true
    const timer = window.setTimeout(() => {
      void getOutlineGeneration(projectId, chapterId, ongoing.id).then(batch => { if (live) upsert(batch) })
        .catch(() => { if (live) setError('任务状态查询失败，可核对原请求，未重新调用模型。') })
    }, 2000)
    return () => { live = false; window.clearTimeout(timer) }
    // A new response schedules the next bounded provider-state observation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, chapterId, batches, ongoing?.id, ongoing?.status])

  async function generate() {
    if (guard.current || !loaded || ongoing) return
    guard.current = true; setBusy(true); setError('')
    try {
      if (!await idea.flush()) { setError('思路尚未保存，请先处理保存失败或冲突。'); return }
      const revision = idea.getRevision()
      if (revision === undefined) return
      const payload = pending || { request_id: crypto.randomUUID(), expected_idea_revision: revision }
      sessionStorage.setItem(generationKey, JSON.stringify(payload)); setPending(payload)
      let batch: OutlineGeneration
      try { batch = pending ? await getOutlineGeneration(projectId, chapterId, payload.request_id) : await createOutlineGeneration(projectId, chapterId, payload) }
      catch (cause) {
        if (!(cause instanceof ApiError) || cause.status !== 404) throw cause
        batch = await createOutlineGeneration(projectId, chapterId, payload)
      }
      upsert(batch); if (mounted.current) setEditingIdea(false)
    } catch { if (mounted.current) setError('生成请求尚未核对完成。请核对原请求，已有方案和思路仍保留。') }
    finally { guard.current = false; if (mounted.current) setBusy(false) }
  }
  async function choose(runId: string, candidateId: string) {
    if (guard.current || idea.readOnly) return
    guard.current = true; setBusy(true); setError('')
    try {
      const version = await beforeSelect()
      if (version === undefined) { setError('当前思路或大纲尚未保存，已暂停切换方案。'); return }
      if (selection && (selection.runId !== runId || selection.payload.candidate_id !== candidateId)) {
        setError('上次选择尚待核对，请先核对原选择或明确保留当前大纲。'); return
      }
      const next = selection || { runId, payload: { request_id: crypto.randomUUID(), candidate_id: candidateId, expected_current_version_id: version } }
      sessionStorage.setItem(selectionKey, JSON.stringify(next)); setSelection(next)
      const selected = await selectOutlineCandidate(projectId, chapterId, runId, next.payload)
      const saved = await readDocumentContent(selected.document_id)
      if (saved.document_id !== selected.document_id || saved.version_id !== selected.version_id) throw new ApiError(409, 'outline_changed', 'Outline changed')
      sessionStorage.removeItem(selectionKey); setSelection(null)
      if (mounted.current) onSelected(selected, saved.content)
    } catch (cause) {
      if (mounted.current) setError(cause instanceof ApiError && cause.status === 409
        ? '大纲版本已变化，当前编辑仍保留，请核对服务器内容后再选择。' : '方案选择尚待核对，可重试原选择，未自动确认或生成正文。')
    } finally { guard.current = false; if (mounted.current) setBusy(false) }
  }
  async function refresh() {
    const runId = ongoing?.id || pending?.request_id || activeId
    if (!runId) return
    try { upsert(await getOutlineGeneration(projectId, chapterId, runId)); setError('') }
    catch { setError('任务状态仍未读取成功，请稍后核对原请求。') }
  }
  async function abandon() {
    if (!ongoing || guard.current || !window.confirm('模型调用结果尚不确定，放弃后重新生成可能产生额外调用。确认放弃这个请求？')) return
    guard.current = true; setBusy(true)
    try { upsert(await cancelOutlineGeneration(projectId, chapterId, ongoing.id)); setError('') }
    catch { setError('放弃请求未完成，请重新核对任务状态。') }
    finally { guard.current = false; setBusy(false) }
  }
  const choosing = Boolean(active?.status === 'completed' && active.candidates.length && !editingIdea)
  const ideaStatus = idea.status === '思路已保存' ? '' : idea.status
  const inputStatus = busy || ongoing?.status === 'running' ? '正在生成…' : ideaStatus
  const cannotGenerate = busy || !loaded || !idea.ready || idea.readOnly || !idea.text.trim() || Boolean(ongoing)
  const history = choosing && <StudioOutlineHistory batches={batches} activeId={activeId}
    onSelect={id => { setActiveId(id); setEditingIdea(false) }} />
  return <section className="studio-outline-alternatives" aria-label="大纲方案比较">
    <StudioOutlineSurface choosing={choosing} motionKey={choosing ? active!.id : 'idea'} footer={<>
      {choosing ? <>
        <button className="outline-secondary" onClick={() => setEditingIdea(true)}>编辑思路</button>
        <OutlineRefreshButton disabled={cannotGenerate} onClick={() => void generate()} />
      </> : active?.status === 'completed' ? <button className="outline-secondary" onClick={() => setEditingIdea(false)}>返回方案</button> : null}
      {history}
    </>}>
      {choosing && <div className="studio-outline-options persisted-outline-options">
        {active!.candidates.map((candidate, index) => {
          const title = candidate.title.replace(/^\s*方案\s*[一二三四五六七八九十\d０-９]+\s*[：:、.．—-]\s*/u, '').trim() || candidate.title
          return <article key={candidate.id} className="outline-candidate">
          <div className="outline-candidate-scroll" tabIndex={0} role="region" aria-label={'方案全文：' + title}>
            <small data-sweep-text>方案 {String(index + 1).padStart(2, '0')}</small>
            <h2 data-sweep-text>{title}</h2><p data-sweep-text className="outline-candidate-summary">{candidate.summary}</p>
            <div data-sweep-text className="outline-candidate-content">{candidate.content}</div>
          </div>
          <button className="studio-option-action" disabled={busy || idea.readOnly} aria-label={'采用此方案：' + title} onClick={() => void choose(active!.id, candidate.id)}><OutlineChoiceLabel /></button>
        </article>})}
      </div>}
      <div className="studio-idea-card" data-text-inactive={choosing} aria-hidden={choosing} inert={choosing}>
        <div className="studio-idea-copy" aria-hidden="true"><TextSweep text={idea.text || '你的大纲思路…'} changeKey="idea" /></div>
        <textarea aria-label="大纲思路" maxLength={8000} readOnly={!idea.ready || idea.readOnly || busy || choosing}
          value={idea.text} onChange={event => idea.change(event.target.value)} placeholder="你的大纲思路…" />
        <div className="outline-input-actions">{inputStatus && <span role="status">{inputStatus}</span>}
          {pending ? <button disabled={cannotGenerate} onClick={() => void generate()}>核对上次生成请求</button> : <OutlineSendButton disabled={cannotGenerate} onClick={() => void generate()} />}
        </div>
      </div>
    </StudioOutlineSurface>
    <div className="outline-recovery" aria-live="polite">
      {choosing && (busy || ideaStatus) && <span className="outline-save-state" role="status">{busy ? '正在处理请求…' : ideaStatus}</span>}
      {idea.status.includes('保存失败') && <button onClick={() => void idea.flush()}>重试保存思路</button>}
      {(idea.status.includes('冲突') || idea.status.includes('加载失败')) && <button onClick={() => void idea.reloadServer()}>使用服务器思路</button>}
      {(ongoing || pending) && <button disabled={busy} onClick={() => void refresh()}>刷新生成状态</button>}
      {ongoing?.status === 'uncertain' && <><p role="status">模型结果尚不确定，请先核对原请求。</p><button disabled={busy} onClick={() => void abandon()}>放弃未确定的请求</button></>}
      {selection && <><button disabled={busy} onClick={() => void choose(selection.runId, selection.payload.candidate_id)}>核对上次选择</button>
        <button disabled={busy} onClick={() => { if (window.confirm('保留当前大纲并清除本机待核对选择？服务端仍按版本校验处理原请求。')) { sessionStorage.removeItem(selectionKey); setSelection(null) } }}>保留当前大纲</button></>}
      {error && <p role="alert">{error}</p>}
      {latestAttempt?.status === 'failed' && <p role="alert">本次生成失败，已有方案未改动。可以重新生成。</p>}
    </div>
  </section>
}
