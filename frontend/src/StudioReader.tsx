import { useEffect, useRef, useState } from 'react'
import { ApiError, readDocumentContent } from './api/client'
import { getChapterProductionRun } from './api/chapterProductionV2Client'
import { cancelReaderPanel, getReaderPanel, listReaderPanels, resumeReaderPanel, startReaderPanel,
  type ReaderPanelDetail, type ReaderPanelSessionDetail, type ReaderPanelStartPayload } from './api/readerPanelClient'
import { readerPersonas, readerStageKey, type StudioChapter } from './studioPreview'

const names: Record<string, string> = Object.fromEntries(readerPersonas.map(([id, name]) => [`studio_${id}`, name]))
const statusNames: Record<string, string> = { created: '准备阅读', preparing: '准备阅读', independent_reading: '独立阅读中',
  initial_reports_locked: '独立意见已保存', issue_extraction: '整理问题中', initial_balloting: '独立投票中',
  initial_ballots_locked: '独立投票已保存', discussing: '讨论中', final_balloting: '最终投票中',
  final_ballots_locked: '最终投票已保存', report_generating: '整理读者报告中', completed: '阅读已完成',
  degraded_completed: '部分阅读完成', failed: '阅读失败', cancelled: '阅读已取消' }

export default function StudioReader({ projectId, chapter, onChange, onFinal }: {
  projectId: string; chapter: StudioChapter; onChange: (patch: Partial<StudioChapter>) => void; onFinal: () => void
}) {
  const [panel, setPanel] = useState<ReaderPanelSessionDetail | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const operation = useRef<AbortController | null>(null)
  const [pending, setPending] = useState<ReaderPanelStartPayload | null>(null)
  const requestKey = `${readerStageKey(chapter.id, chapter.versionId!)}:request`
  const check = (value: ReaderPanelDetail): ReaderPanelSessionDetail => {
    if (value.is_noop || value.project_id !== projectId || value.chapter_id !== chapter.id
      || value.document_id !== chapter.documentId || value.document_version_id !== chapter.versionId) {
      throw new ApiError(409, 'reader_changed', '读者会话或正文版本已变化。')
    }
    return value
  }
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    void (async () => {
      const saved = sessionStorage.getItem(requestKey)
      const recovered = saved ? JSON.parse(saved) : null
      if (recovered && (recovered.document_id !== chapter.documentId || recovered.document_version_id !== chapter.versionId
        || !Array.isArray(recovered.reader_profile_ids) || recovered.reader_profile_ids.some((id: string) => !names[id]))) throw new Error('Invalid saved invitation')
      const panels = await listReaderPanels(projectId, chapter.id, { limit: 100 }, controller.signal)
      const found = panels.find(item => !item.is_noop && item.document_id === chapter.documentId && item.document_version_id === chapter.versionId)
      const value = found && !found.is_noop ? check(await getReaderPanel(projectId, chapter.id, found.session_id,
        { include_initial_reports: true, include_transcript: true, data_limit: 200 }, controller.signal)) : null
      if (!active) return
      setPanel(value); setPending(recovered); setLoaded(true)
      const invited = recovered?.reader_profile_ids || value?.reader_profile_ids
      if (invited) onChange({ readers: invited.map((id: string) => id.replace(/^studio_/, '')) })
      if (recovered) setError('上次邀请请求尚待核对，重试会恢复原请求。')
    })().catch(() => { if (active) setError('读者会话加载失败，请重新进入本环节核对。') })
    return () => { active = false; controller.abort(); operation.current?.abort() }
    // A mounted panel belongs to one immutable chapter version.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, chapter.id, chapter.documentId, chapter.versionId])

  async function run(cancel = false) {
    if ((!cancel && operation.current) || !loaded) return
    if (cancel) operation.current?.abort()
    const controller = new AbortController()
    operation.current = controller; setBusy(true); setError('')
    let current = panel
    try {
      if (cancel && panel) {
        const cancelled = check(await cancelReaderPanel(projectId, chapter.id, panel.session_id, controller.signal))
        controller.signal.throwIfAborted(); setPanel(cancelled); return
      }
      const state = await getChapterProductionRun(projectId, chapter.id, chapter.productionState!.chapter_workflow_run_id, controller.signal)
      const saved = await readDocumentContent(chapter.documentId!)
      controller.signal.throwIfAborted()
      if (state.status !== 'REVISION_READY' || state.awaiting_user || state.document_id !== chapter.documentId
        || state.document_version_id !== chapter.versionId || saved.version_id !== chapter.versionId || saved.content !== chapter.draft) {
        throw new ApiError(409, 'reader_changed', '正文版本或审阅状态已变化，请重新加载核对。')
      }
      if (!current || pending) {
        const payload: ReaderPanelStartPayload = pending || { document_id: chapter.documentId!, document_version_id: chapter.versionId!,
          reader_profile_ids: chapter.readers.map(id => `studio_${id}`), mode: chapter.readers.length <= 2 ? 'quick' : chapter.readers.length <= 4 ? 'standard' : 'panel',
          idempotency_key: crypto.randomUUID() }
        sessionStorage.setItem(requestKey, JSON.stringify(payload)); setPending(payload)
        current = check(await startReaderPanel(projectId, chapter.id, payload, controller.signal))
        controller.signal.throwIfAborted()
        if (JSON.stringify(current.reader_profile_ids) !== JSON.stringify(payload.reader_profile_ids)) throw new Error('Reader selection changed')
        setPanel(current); sessionStorage.removeItem(requestKey); setPending(null)
      }
      current = check(await getReaderPanel(projectId, chapter.id, current.session_id,
        { include_initial_reports: true, include_transcript: true, data_limit: 200 }, controller.signal))
      for (let step = 0; step < 6; step++) {
        controller.signal.throwIfAborted(); setPanel(current)
        if (current.stale) throw new Error('Stale reader panel')
        if (cancel) {
          if (current.permitted_operations.includes('cancel')) current = check(await cancelReaderPanel(projectId, chapter.id, current.session_id, controller.signal))
          break
        }
        if (!current.permitted_operations.includes('resume')) break
        await resumeReaderPanel(projectId, chapter.id, current.session_id, controller.signal)
        current = check(await getReaderPanel(projectId, chapter.id, current.session_id,
          { include_initial_reports: true, include_transcript: true, data_limit: 200 }, controller.signal))
      }
      controller.signal.throwIfAborted(); setPanel(current)
    } catch (cause) {
      if (current && !controller.signal.aborted) {
        try {
          const recovered = check(await getReaderPanel(projectId, chapter.id, current.session_id,
            { include_initial_reports: true, include_transcript: true, data_limit: 200 }, controller.signal))
          if (!controller.signal.aborted) setPanel(recovered)
        } catch { /* Keep the last verified state and the retry control. */ }
      }
      if (!controller.signal.aborted) setError(cause instanceof ApiError && cause.status === 409
        ? '正文版本或读者会话已变化，请重新加载核对。' : '阅读请求未完成，重试会核对原会话并继续未完成阶段。')
    } finally {
      if (operation.current === controller) { operation.current = null; if (!controller.signal.aborted) setBusy(false) }
    }
  }
  const changeInvitation = (id: string) => onChange({ readers: chapter.readers.includes(id)
    ? chapter.readers.filter(item => item !== id) : [...chapter.readers, id] })
  return <section className={`studio-reader studio-enter${panel ? ' is-discussion' : ''}`} aria-label="读者环节">
    {!panel ? <><h2>邀请读者</h2><div className="studio-reader-list">{readerPersonas.map(([id, name, description]) =>
      <div className="studio-reader-person" key={id}><span className="studio-avatar" aria-hidden="true" /><div><strong>{name}</strong><small>{description}</small></div>
        <button disabled={!loaded || busy || Boolean(pending)} className={chapter.readers.includes(id) ? 'is-invited' : ''}
          aria-pressed={chapter.readers.includes(id)} aria-label={`${chapter.readers.includes(id) ? '取消邀请' : '邀请'}${name}`}
          onClick={() => changeInvitation(id)}>{chapter.readers.includes(id) ? '取消' : '邀请'}</button></div>)}</div></>
      : <div className="studio-reader-transcript">
        <div className="studio-message"><span className="studio-avatar" /><div><p>主持人 <time>{statusNames[panel.status] || panel.status}</time></p>
          <p>第{chapter.number}话 {chapter.title}</p><p>{(panel.reader_profile_ids || []).map(id => names[id] || id).join('、')}</p>
          <p>{panel.completed_readers} / {panel.planned_readers} 位读者完成独立阅读</p>
          {panel.simulated !== false && <p className="studio-muted">{panel.simulated ? '当前为模拟阅读结果' : '模型来源尚未核验'}</p>}
          {(panel.stale || panel.failure_reason || panel.degradation_reason) && <p role="alert">{panel.stale ? '正文版本已变化，此报告仅供历史参考。' : `阅读未完整完成：${panel.failure_reason || panel.degradation_reason}`}</p>}
        </div></div>
        {panel.initial_reports?.map((report, index) => <div className="studio-message" key={`initial-${index}`}><span className="studio-avatar" /><div>
          <p>{names[report.reader_profile_id || ''] || '读者'} <time>独立阅读</time></p><p>{report.overall_reaction}</p>
          {report.concerns.map((concern, i) => <p key={i}>{concern.symptom}</p>)}</div></div>)}
        {panel.transcript?.map((message, index) => <div className="studio-message" key={`message-${index}`}><span className="studio-avatar" /><div>
          <p>{message.speaker_type === 'moderator' ? '主持人' : names[message.reader_profile_id || ''] || '读者'} <time>第 {message.round_number} 轮</time></p>
          <p>{message.claim}</p>{message.concession && <p>{message.concession}</p>}{message.proposed_action && <p>{message.proposed_action}</p>}</div></div>)}
        {panel.review_report && <div className="studio-message"><span className="studio-avatar" /><div><p>主持人 <time>读者报告</time></p><p>{panel.review_report.summary}</p>
          {panel.review_report.blocking_issues.map(issue => <p key={issue.issue_number}>需关注：{issue.title}</p>)}
          {panel.review_report.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
          <p className="studio-muted">读者意见供作者参考，尚未修改正文。</p></div></div>}
      </div>}
    {error && <p role="alert">{error}</p>}
    <div className="studio-reader-footer"><button disabled={busy || !loaded} onClick={onFinal}>{panel ? '结束旁观，进入终稿' : '跳过读者环节'}</button>
      {panel && ['failed', 'cancelled'].includes(panel.status) && <button disabled={busy} onClick={() => { setPanel(null); setError('') }}>重新邀请</button>}
      {panel?.permitted_operations.includes('cancel') && <button onClick={() => void run(true)}>取消阅读</button>}
      {(!panel || error || panel.permitted_operations.includes('resume')) && <button className="studio-primary" disabled={!loaded || busy || (!panel && !pending && !chapter.readers.length)}
        aria-busy={busy} onClick={() => void run()}>{busy ? '正在阅读…' : error ? '重试阅读请求' : panel ? '继续阅读' : '开始阅读'}</button>}
    </div>
  </section>
}
