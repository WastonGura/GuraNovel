import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ApiError, listSettingDocumentRestorePoints, updateSettingDocumentRestorePointMessage, type SettingDocumentRestorePoint } from './api/client'

type Attempt = { message: string | null; expected_message: string | null }
type Lane = {
  point: SettingDocumentRestorePoint; draft: string; expected: string | null; token: string
  attempt: Attempt | null; failed: boolean; conflict: boolean; paused: boolean; composing: boolean
  error: string; backupError: string; latest: string | null | undefined; reading: boolean
  timer: ReturnType<typeof setTimeout> | null; running: Promise<boolean> | null
}
type MessageView = { draft: string; pending: boolean; error: string; conflict: boolean; paused: boolean; latest: string | null | undefined }
const normalize = (value: string) => value.trim() || null
const key = (documentId: string, pointId: string) => `guranovel:setting-archive-message:${documentId}:${pointId}`
const nullableMessage = (value: unknown) => value === null || typeof value === 'string' && value.length <= 160
const samePoint = (left: SettingDocumentRestorePoint, right: SettingDocumentRestorePoint) => left.id === right.id && left.document_id === right.document_id
  && left.version_id === right.version_id && right.version.id === left.version_id && right.version.document_id === left.document_id
  && left.point_number === right.point_number && left.created_at === right.created_at

/** Each manual archive has its own CAS lane; prose versions are never written here. */
export function useSettingArchiveMessages(documentId: string, points: SettingDocumentRestorePoint[], readOnly: boolean, onUpdated: (point: SettingDocumentRestorePoint) => void) {
  const lanes = useRef(new Map<string, Lane>()), live = useRef(true), readonlyRef = useRef(readOnly), updated = useRef(onUpdated)
  const [views, setViews] = useState<Record<string, MessageView>>({})
  useLayoutEffect(() => { readonlyRef.current = readOnly; updated.current = onUpdated }, [readOnly, onUpdated])
  const publish = useCallback(() => {
    if (live.current) setViews(Object.fromEntries([...lanes.current].map(([id, lane]) => [id, {
      draft: lane.draft, pending: Boolean(lane.running) || lane.reading, error: [lane.error, lane.backupError].filter(Boolean).join(' '),
      conflict: lane.conflict, paused: lane.paused, latest: lane.latest,
    }])))
  }, [])
  function clearTimer(lane: Lane) { if (lane.timer) clearTimeout(lane.timer); lane.timer = null }
  function owns(lane: Lane) {
    try { return JSON.parse(sessionStorage.getItem(key(documentId, lane.point.id)) || 'null')?.token === lane.token } catch { return false }
  }
  function backup(lane: Lane, onlyOwned = false) {
    if (onlyOwned && !owns(lane)) return
    try {
      sessionStorage.setItem(key(documentId, lane.point.id), JSON.stringify({ pointId: lane.point.id, draft: lane.draft, expected: lane.expected,
        token: lane.token, attempt: lane.attempt, failed: lane.failed, conflict: lane.conflict, paused: lane.paused }))
      lane.backupError = ''
    } catch { lane.backupError = '浏览器无法暂存说明，关闭前请复制备份。' }
  }
  function clearBackup(lane: Lane) {
    if (!owns(lane)) { if (live.current) lane.backupError = ''; return }
    try { sessionStorage.removeItem(key(documentId, lane.point.id)); lane.backupError = '' }
    catch { lane.backupError = '浏览器无法清除本机暂存，重新打开时请核对说明。' }
  }
  function ensure(point: SettingDocumentRestorePoint): Lane {
    const existing = lanes.current.get(point.id)
    if (existing) { existing.point = point; return existing }
    const lane: Lane = { point, draft: point.message || '', expected: point.message ?? null, token: crypto.randomUUID(), attempt: null,
      failed: false, conflict: false, paused: false, composing: false, error: '', backupError: '', latest: undefined, reading: false, timer: null, running: null }
    try {
      const saved = JSON.parse(sessionStorage.getItem(key(documentId, point.id)) || 'null')
      const savedAttempt = saved?.attempt && nullableMessage(saved.attempt.message) && nullableMessage(saved.attempt.expected_message) ? saved.attempt as Attempt : null
      if (!readonlyRef.current && saved?.pointId === point.id && typeof saved.draft === 'string' && saved.draft.length <= 160 && nullableMessage(saved.expected)
        && (normalize(saved.draft) !== (point.message ?? null) || savedAttempt)) {
        lane.draft = saved.draft; lane.expected = saved.expected
        lane.attempt = savedAttempt
        lane.failed = saved.failed === true; lane.conflict = saved.conflict === true; lane.paused = saved.paused === true
        if (lane.failed) lane.error = lane.conflict ? '说明已被其他操作更新，请读取最新说明后继续。' : '说明同步失败，当前输入仍保留，请重试同步。'
        backup(lane)
      }
    } catch { lane.backupError = '浏览器无法读取暂存说明，请保持页面打开并复制未同步内容。' }
    lanes.current.set(point.id, lane)
    return lane
  }
  async function flush(pointId: string): Promise<boolean> {
    const lane = lanes.current.get(pointId)
    if (!lane) return true
    clearTimer(lane)
    if (lane.running) return lane.running
    if (readonlyRef.current) return normalize(lane.draft) === lane.expected && !lane.attempt
    if (lane.composing || lane.failed || lane.conflict || lane.paused || lane.reading) return false
    if (lane.draft.length > 160) { lane.failed = true; lane.error = '说明最多 160 字。'; publish(); return false }
    const work = async (): Promise<boolean> => {
      while (lane.attempt || normalize(lane.draft) !== lane.expected) {
        if (!live.current && !owns(lane)) return false
        if (lane.composing || readonlyRef.current) return false
        const attempt = lane.attempt || { message: normalize(lane.draft), expected_message: lane.expected }
        lane.attempt = attempt; backup(lane, !live.current)
        try {
          const result = await updateSettingDocumentRestorePointMessage(documentId, pointId, attempt)
          if (!samePoint(lane.point, result) || (result.message ?? null) !== attempt.message) throw new Error('Invalid archive message acknowledgement')
          lane.expected = attempt.message; lane.attempt = null; lane.point = result; lane.error = ''; lane.failed = false
          if (live.current) updated.current(result)
          // A reopened dialog owns a different token; the old instance must not
          // erase its draft or launch another writer after this acknowledgement.
          if (!live.current && !owns(lane)) return false
          if (normalize(lane.draft) === lane.expected) clearBackup(lane)
          else backup(lane, true)
          publish()
        } catch (error) {
          lane.failed = true; lane.conflict = error instanceof ApiError && error.status === 409
          lane.error = lane.conflict ? '说明已被其他操作更新，请读取最新说明后继续。' : '说明同步失败，当前输入仍保留，请重试同步。'
          backup(lane, true); publish(); return false
        }
      }
      clearBackup(lane); return true
    }
    const promise = work().finally(() => { lane.running = null; publish() })
    lane.running = promise; publish()
    return promise
  }
  function schedule(lane: Lane) {
    clearTimer(lane)
    if (lane.composing || lane.failed || lane.conflict || lane.paused || readonlyRef.current) return
    lane.timer = setTimeout(() => { lane.timer = null; void flush(lane.point.id) }, 400)
  }
  async function flushAll() { return (await Promise.all([...lanes.current.keys()].map(flush))).every(Boolean) }
  useEffect(() => {
    const sessionLanes = lanes.current
    live.current = true
    return () => { live.current = false; sessionLanes.forEach(clearTimer); void flushAll() }
  // Lanes are scoped to the keyed dialog session.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId])
  useEffect(() => {
    for (const point of points) {
      const isNew = !lanes.current.has(point.id), lane = ensure(point)
      if (isNew && (lane.attempt || normalize(lane.draft) !== lane.expected)) schedule(lane)
    }
    publish()
  // Only incoming archive records initialize lanes; typing updates them directly.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, publish])
  function change(point: SettingDocumentRestorePoint, draft: string) {
    if (readonlyRef.current) return
    const lane = ensure(point)
    lane.draft = draft; lane.token = crypto.randomUUID(); backup(lane); schedule(lane); publish()
  }
  function composition(point: SettingDocumentRestorePoint, active: boolean) {
    const lane = ensure(point); lane.composing = active
    if (active) clearTimer(lane); else schedule(lane)
  }
  function endInput(point: SettingDocumentRestorePoint) {
    const lane = ensure(point)
    lane.draft = normalize(lane.draft) || ''
    if (lane.attempt || normalize(lane.draft) !== lane.expected) backup(lane)
    publish()
    return flush(point.id)
  }
  function retry(pointId: string) {
    const lane = lanes.current.get(pointId)
    if (!lane || lane.conflict || lane.reading) return
    lane.failed = false; lane.paused = false; lane.error = ''; backup(lane); void flush(pointId)
  }
  async function readLatest(pointId: string) {
    const lane = lanes.current.get(pointId)
    if (!lane || lane.running || lane.reading) return
    lane.reading = true; clearTimer(lane); publish()
    try {
      const latest = (await listSettingDocumentRestorePoints(documentId)).find(point => point.id === pointId)
      if (!latest || !samePoint(lane.point, latest)) throw new Error('Invalid latest archive message')
      if (!live.current) return
      lane.expected = latest.message ?? null; lane.latest = lane.expected; lane.attempt = null
      lane.failed = false; lane.conflict = false; lane.paused = true; lane.error = ''; lane.point = latest
      updated.current(latest); backup(lane)
    } catch { lane.error = '最新说明读取失败，请重试。当前输入仍保留。' }
    finally { lane.reading = false; publish() }
  }
  function view(point: SettingDocumentRestorePoint): MessageView {
    return views[point.id] || { draft: point.message || '', pending: false, error: '', conflict: false, paused: false, latest: undefined }
  }
  return { view, change, composition, endInput, flush, flushAll, retry, readLatest }
}
