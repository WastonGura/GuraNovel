import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError, readStudioFeedback, writeStudioFeedback, submitStudioFeedback, writeDocument, type StudioFeedback } from './api/client'
import { draftRecoveryCopies, useDraftAutosave } from './useDraftAutosave'
import { writeStudioDraft } from './api/client'

vi.mock('./api/client', async original => ({ ...await original<typeof import('./api/client')>(),
  readStudioFeedback: vi.fn(), writeStudioFeedback: vi.fn(), submitStudioFeedback: vi.fn(), writeDocument: vi.fn(), writeStudioDraft: vi.fn(),
}))
const comment = { id: 'comment', start: 0, end: 8, quote: 'original', text: '意见', color: '#458466', submitted: false, orphaned: false }
let server: StudioFeedback
let version: string
let events: string[]
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
const options = { feedback: { projectId: 'project', chapterId: 'chapter', region: 'draft' as const, onChange: vi.fn() } }
async function open(initial = 'original', source = 'v1') {
  const hook = renderHook(() => useDraftAutosave('doc', source, initial, undefined, options))
  await act(async () => {})
  return hook
}
beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers(); sessionStorage.clear(); draftRecoveryCopies.clear(); events = []; version = 'v1'
  vi.mocked(writeStudioDraft).mockImplementation((_project, _chapter, payload) => writeDocument('doc', payload))
  server = { chapter_id: 'chapter', region: 'draft', document_id: 'doc', source_version_id: 'v1', revision: 0, comments: [], requirements: '', read_only: false }
  vi.mocked(readStudioFeedback).mockImplementation(async () => copy({ ...server, source_version_id: version }))
  vi.mocked(writeDocument).mockImplementation(async (_id, payload) => {
    expect(payload.expected_current_version_id).toBe(version)
    events.push('prose:' + payload.content); version = 'v' + (Number(version.slice(1)) + 1)
    return { id: version } as Awaited<ReturnType<typeof writeDocument>>
  })
  vi.mocked(writeStudioFeedback).mockImplementation(async (_project, _chapter, _region, payload) => {
    expect(payload.expected_current_version_id).toBe(version)
    expect(payload.expected_revision).toBe(server.revision)
    events.push('feedback:' + payload.requirements)
    server = { ...server, ...copy({ comments: payload.comments, requirements: payload.requirements }), revision: server.revision + 1, source_version_id: version }
    return copy(server)
  })
  vi.mocked(submitStudioFeedback).mockImplementation(async (_project, _chapter, _region, payload) => {
    expect(payload.expected_revision).toBe(server.revision)
    expect(payload.expected_current_version_id).toBe(version)
    events.push('submit')
    server = { ...server, revision: server.revision + 1, comments: server.comments.map(item => payload.comment_ids.includes(item.id) ? { ...item, submitted: true } : item) }
    return copy({ id: payload.request_id, chapter_id: 'chapter', region: 'draft', document_id: 'doc', source_version_id: version,
      feedback_revision: server.revision, comments: server.comments.filter(item => payload.comment_ids.includes(item.id)), requirements: server.requirements, created_at: '2026-09-12T00:00:00Z' })
  })
})
afterEach(() => { cleanup(); vi.useRealTimers() })

it('adopts server revision prose and feedback only with a clean queue', async () => {
  server.requirements = '已保存要求'; server.comments = [comment]
  const hook = renderHook(({ source, text }) => useDraftAutosave('doc', source, text, undefined, options),
    { initialProps: { source: 'v1', text: 'original' } })
  await act(async () => {})
  version = 'v2'; server.comments = [{ ...comment, orphaned: true, start: 0, end: 0 }]
  await act(async () => { hook.rerender({ source: 'v2', text: 'revised prose' }) })
  expect(hook.result.current.text).toBe('revised prose')
  expect(hook.result.current.feedback).toMatchObject({ source_version_id: 'v2', requirements: '已保存要求', comments: [{ orphaned: true }] })
  expect(writeDocument).not.toHaveBeenCalled()
  act(() => hook.result.current.changeFeedback({ requirements: '尚未保存的要求' }))
  version = 'v3'
  await act(async () => { hook.rerender({ source: 'v3', text: 'external prose' }) })
  expect(hook.result.current.text).toBe('revised prose')
  expect(hook.result.current.feedback?.requirements).toBe('尚未保存的要求')
  expect(hook.result.current.status).toContain('冲突')
  expect(sessionStorage.getItem('guranovel:draft-recovery:doc')).toContain('尚未保存的要求')
})

it('does not report a completed save before server feedback has loaded', async () => {
  let finish!: (value: StudioFeedback) => void
  vi.mocked(readStudioFeedback).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const hook = await open()
  expect(hook.result.current.status).toBe('正在加载评论…')
  expect(hook.result.current.feedback).toBeNull()
  await act(async () => finish({ ...server, requirements: '服务器保存的要求' }))
  expect(hook.result.current.status).toBe('已自动保存')
  expect(hook.result.current.feedback?.requirements).toBe('服务器保存的要求')
})

it('serializes source and feedback while preserving text and requirements edited in flight', async () => {
  const hook = await open()
  let finish!: () => void
  vi.mocked(writeDocument).mockImplementationOnce(async (_id, payload) => {
    events.push('prose:' + payload.content)
    await new Promise<void>(resolve => { finish = resolve })
    version = 'v2'
    return { id: version } as Awaited<ReturnType<typeof writeDocument>>
  })
  act(() => { hook.result.current.changeFeedback({ comments: [comment], requirements: '旧要求' }); hook.result.current.change('prefix original') })
  let saving!: Promise<boolean>
  await act(async () => { saving = hook.result.current.flush() })
  act(() => hook.result.current.change('new prefix original'))
  act(() => hook.result.current.changeFeedback({ requirements: '新要求', comments: hook.result.current.feedback!.comments.map(item => ({ ...item, text: '补充意见' })) }))
  await act(async () => { finish(); expect(await saving).toBe(true) })
  expect(events).toEqual(['prose:prefix original', 'feedback:旧要求', 'prose:new prefix original', 'feedback:新要求'])
  expect(server.comments[0]).toMatchObject({ start: 11, end: 19, quote: 'original', text: '补充意见' })
  expect(hook.result.current.feedback?.requirements).toBe('新要求')
  expect(sessionStorage.getItem('guranovel:draft-recovery:doc')).toBeNull()
})

it('retries the exact uncertain feedback request before saving later prose', async () => {
  const hook = await open()
  vi.mocked(writeStudioFeedback).mockRejectedValueOnce(new Error('network'))
  act(() => hook.result.current.changeFeedback({ comments: [comment], requirements: '保留要求' }))
  await act(async () => { expect(await hook.result.current.flush()).toBe(false) })
  const original = copy(vi.mocked(writeStudioFeedback).mock.calls[0][3])
  expect(JSON.parse(sessionStorage.getItem('guranovel:draft-recovery:doc')!).feedback.write.payload).toEqual(original)
  act(() => hook.result.current.change('prefix original'))
  await act(async () => { expect(await hook.result.current.flush()).toBe(true) })
  expect(vi.mocked(writeStudioFeedback).mock.calls[1][3]).toEqual(original)
  expect(events).toEqual(['feedback:保留要求', 'prose:prefix original', 'feedback:保留要求'])
})

it('reconciles an accepted feedback write after reload without writing the unchanged prose again', async () => {
  const first = await open()
  act(() => first.result.current.changeFeedback({ comments: [comment], requirements: '可恢复' }))
  const accept = vi.mocked(writeStudioFeedback).getMockImplementation()!
  vi.mocked(writeStudioFeedback).mockImplementationOnce(async (...args) => { await accept(...args); throw new Error('response lost') })
  await act(async () => { expect(await first.result.current.flush()).toBe(false) })
  const pending = copy(vi.mocked(writeStudioFeedback).mock.calls[0][3])
  first.unmount()
  vi.mocked(writeStudioFeedback).mockImplementationOnce(async (_p, _c, _r, payload) => { expect(payload).toEqual(pending); return copy(server) })
  const second = await open()
  await act(async () => { expect(await second.result.current.flush()).toBe(true) })
  expect(second.result.current.feedback?.comments[0].text).toBe('意见')
  expect(writeDocument).not.toHaveBeenCalled()
  expect(sessionStorage.getItem('guranovel:draft-recovery:doc')).toBeNull()
})

it('waits for acknowledgement before panel motion and preserves edits made during that motion', async () => {
  const hook = await open()
  act(() => hook.result.current.changeFeedback({ comments: [comment], requirements: '发送时的要求' }))
  let finish!: () => void
  const animate = vi.fn(() => new Promise<void>(resolve => { finish = resolve }))
  let sending!: Promise<boolean>
  await act(async () => { sending = hook.result.current.submit([comment.id], animate) })
  expect(events).toEqual(['feedback:发送时的要求', 'submit'])
  expect(animate).toHaveBeenCalledOnce()
  expect(hook.result.current.feedback?.comments[0].submitted).toBe(false)
  act(() => hook.result.current.changeFeedback({ requirements: '发送后补充', comments: [{ ...comment, text: '新的意见' }] }))
  await act(async () => vi.advanceTimersByTime(700))
  expect(events).toHaveLength(2)
  await act(async () => { finish(); expect(await sending).toBe(true) })
  expect(hook.result.current.feedback?.comments[0]).toMatchObject({ text: '新的意见', submitted: true })
  expect(server.requirements).toBe('发送后补充')
})

it('retains comments with a changed quote as explicitly orphaned feedback', async () => {
  const hook = await open()
  act(() => hook.result.current.changeFeedback({ comments: [comment] }))
  act(() => hook.result.current.change('changed text with original elsewhere'))
  await act(async () => { expect(await hook.result.current.flush()).toBe(true) })
  expect(server.comments[0]).toMatchObject({ quote: 'original', start: 0, end: 0, orphaned: true })
})

it('does not overwrite newer feedback after recovery or edit a server readonly chapter', async () => {
  const first = await open()
  act(() => first.result.current.changeFeedback({ comments: [comment], requirements: '未保存要求' }))
  first.unmount(); server.revision = 2; server.requirements = '另一处保存的要求'
  const next = await open()
  await act(async () => { expect(await next.result.current.flush()).toBe(false) })
  expect(next.result.current.status).toContain('冲突')
  expect(next.result.current.feedback?.requirements).toBe('未保存要求')
  expect(next.result.current.feedback?.comments[0].text).toBe(comment.text)
  expect(next.result.current.feedback?.read_only).toBe(true)
  expect(writeStudioFeedback).not.toHaveBeenCalled()
  expect(JSON.parse(sessionStorage.getItem('guranovel:draft-recovery:doc')!).feedback.values.requirements).toBe('未保存要求')
  next.unmount(); sessionStorage.clear(); draftRecoveryCopies.clear(); server.read_only = true
  const readonly = await open()
  act(() => { readonly.result.current.change('forbidden'); readonly.result.current.changeFeedback({ requirements: 'forbidden' }) })
  expect(readonly.result.current.text).toBe('original')
  expect(readonly.result.current.feedback?.requirements).toBe('另一处保存的要求')
})

it('does not dissolve a panel when submission fails and retains the same submission identity for retry', async () => {
  const hook = await open()
  act(() => hook.result.current.changeFeedback({ comments: [comment] }))
  vi.mocked(submitStudioFeedback).mockRejectedValueOnce(new ApiError(503, 'unavailable', 'network'))
  const animate = vi.fn(async () => {})
  await act(async () => { expect(await hook.result.current.submit([comment.id], animate)).toBe(false) })
  expect(animate).not.toHaveBeenCalled()
  const request = copy(vi.mocked(submitStudioFeedback).mock.calls[0][3])
  await act(async () => { expect(await hook.result.current.submit([comment.id], animate)).toBe(true) })
  expect(vi.mocked(submitStudioFeedback).mock.calls[1][3]).toEqual(request)
  expect(hook.result.current.feedback?.comments[0].submitted).toBe(true)
})

it('does not let a departed editor clear a newer recovery copy when its request finishes', async () => {
  const first = await open()
  let finish!: () => void
  vi.mocked(writeDocument).mockImplementationOnce(async () => {
    await new Promise<void>(resolve => { finish = resolve })
    version = 'v2'
    return { id: version } as Awaited<ReturnType<typeof writeDocument>>
  })
  act(() => first.result.current.change('first edit'))
  let pending!: Promise<boolean>
  await act(async () => { pending = first.result.current.flush() })
  first.unmount()
  const next = await open()
  act(() => next.result.current.change('newer unsaved edit'))
  await act(async () => { finish(); await pending })
  expect(next.result.current.text).toBe('newer unsaved edit')
  expect(JSON.parse(sessionStorage.getItem('guranovel:draft-recovery:doc')!).text).toBe('newer unsaved edit')
  expect(JSON.parse(draftRecoveryCopies.get('guranovel:draft-recovery:doc')!.value!).text).toBe('newer unsaved edit')
})

it('retains feedback after storage failure and warns before leaving even with no editor mounted', async () => {
  const first = await open()
  const failStorage = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('full') })
  act(() => first.result.current.changeFeedback({ comments: [comment], requirements: '不能丢失的要求' }))
  first.unmount()
  const unload = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(unload)
  expect(unload.defaultPrevented).toBe(true)
  const next = await open()
  expect(next.result.current.feedback?.requirements).toBe('不能丢失的要求')
  expect(next.result.current.feedback?.comments[0].text).toBe(comment.text)
  failStorage.mockRestore()
  await act(async () => { expect(await next.result.current.flush()).toBe(true) })
  expect(server.requirements).toBe('不能丢失的要求')
  const clean = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(clean)
  expect(clean.defaultPrevented).toBe(false)
})
