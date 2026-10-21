import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { updateProject, type Project } from './api/client'
import { useNovelMetadata } from './useNovelMetadata'

vi.mock('./api/client', () => ({ updateProject: vi.fn() }))
const write = vi.mocked(updateProject)
const project: Project = { id: 'one', title: 'Original', slug: 'one', genre: null, target_platform: null,
  setting_collection_id: null, status: 'draft', workspace_root: '', metadata: { word_count: 42, labels: ['都市'] },
  created_at: '2026-10-19', updated_at: '2026-10-19' }
beforeEach(() => { vi.useFakeTimers(); sessionStorage.clear(); localStorage.clear(); write.mockReset() })
afterEach(() => { cleanup(); vi.useRealTimers() })

it('coalesces rapid edits and preserves unrelated metadata', async () => {
  write.mockImplementation(async (_id, payload) => ({ ...project, ...payload } as Project))
  const hook = renderHook(() => useNovelMetadata(project, vi.fn()))
  act(() => { hook.result.current.change({ title: 'A' }); hook.result.current.change({ title: 'Latest', introduction: 'Intro' }) })
  expect(hook.result.current.values.title).toBe('Latest')
  await act(() => vi.advanceTimersByTimeAsync(399))
  expect(write).not.toHaveBeenCalled()
  await act(() => vi.advanceTimersByTimeAsync(1))
  expect(write).toHaveBeenCalledTimes(1)
  expect(write).toHaveBeenCalledWith('one', expect.objectContaining({ title: 'Latest', metadata: { word_count: 42, labels: ['都市'], introduction: 'Intro' } }))
  expect(sessionStorage.length).toBe(0)
})

it('clears an existing introduction through the server metadata merge contract', async () => {
  const original: Project = { ...project, metadata: { ...project.metadata, introduction: 'Old introduction' } }
  let server = original
  write.mockImplementation(async (_id, payload) => {
    server = { ...server, ...payload, metadata: { ...server.metadata, ...payload.metadata } } as typeof original
    return server
  })
  const hook = renderHook(() => useNovelMetadata(original, vi.fn()))
  act(() => hook.result.current.change({ introduction: '' }))
  await act(async () => { expect(await hook.result.current.flush()).toBe(true) })
  expect(write).toHaveBeenCalledWith('one', expect.objectContaining({ metadata: expect.objectContaining({ introduction: '' }) }))
  hook.unmount()
  const reopened = renderHook(() => useNovelMetadata(server, vi.fn()))
  expect(reopened.result.current.values.introduction).toBe('')
  expect(server.metadata.word_count).toBe(42)
})

it('keeps an absent introduction absent when editing a legacy project title', async () => {
  const original: Project = { ...project, metadata: { ...project.metadata, custom: { language: 'zh' } } }
  let server = original
  write.mockImplementation(async (_id, payload) => {
    server = { ...server, ...payload, metadata: { ...server.metadata, ...payload.metadata } } as Project
    return server
  })
  const hook = renderHook(() => useNovelMetadata(original, vi.fn()))
  act(() => hook.result.current.change({ title: 'Renamed legacy novel' }))
  await act(async () => { expect(await hook.result.current.flush()).toBe(true) })
  expect(write.mock.calls[0][1].metadata).not.toHaveProperty('introduction')
  expect(server.metadata).toEqual(original.metadata)
  hook.unmount()
  const reopened = renderHook(() => useNovelMetadata(server, vi.fn()))
  expect(reopened.result.current.values.title).toBe('Renamed legacy novel')
  expect(reopened.result.current.values.introduction).toBe('')
  expect(write).toHaveBeenCalledTimes(1)
})

it('persists returning to the neutral cover without removing other metadata', async () => {
  const original: Project = { ...project, metadata: { ...project.metadata, cover: 'cover-2.jpg', introduction: 'Existing introduction', custom: { language: 'zh' } } }
  let server = original
  write.mockImplementation(async (_id, payload) => {
    server = { ...server, ...payload, metadata: { ...server.metadata, ...payload.metadata } } as Project
    return server
  })
  const hook = renderHook(() => useNovelMetadata(original, vi.fn()))
  act(() => hook.result.current.change({ cover: 'cover-default' }))
  await act(() => vi.advanceTimersByTimeAsync(400))
  expect(server.metadata).toEqual({ ...original.metadata, cover: 'cover-default' })
  expect(write).toHaveBeenCalledTimes(1)
  hook.unmount()
  const reopened = renderHook(() => useNovelMetadata(server, vi.fn()))
  expect(reopened.result.current.values.cover).toBe('cover-default')
  expect(reopened.result.current.values.introduction).toBe('Existing introduction')
})

it('serializes writes and never replaces newer input with an older response', async () => {
  let resolve!: (value: Project) => void
  write.mockImplementationOnce(() => new Promise(done => { resolve = done }))
  write.mockImplementation(async (_id, payload) => ({ ...project, ...payload } as Project))
  const hook = renderHook(() => useNovelMetadata(project, vi.fn()))
  act(() => hook.result.current.change({ title: 'First' }))
  let pending!: Promise<boolean>
  act(() => { pending = hook.result.current.flush() })
  act(() => hook.result.current.change({ title: 'Newest', introduction: 'While saving' }))
  await act(() => vi.advanceTimersByTimeAsync(500))
  expect(write).toHaveBeenCalledTimes(1)
  expect(hook.result.current.values.title).toBe('Newest')
  await act(async () => { resolve({ ...project, title: 'First' }); await pending })
  expect(write).toHaveBeenCalledTimes(2)
  expect(write.mock.calls[1][1]).toEqual(expect.objectContaining({ title: 'Newest', metadata: expect.objectContaining({ introduction: 'While saving' }) }))
  expect(hook.result.current.values.title).toBe('Newest')
})

it('restores failed edits across remounts, isolates projects and retries on reconnect', async () => {
  write.mockRejectedValueOnce(new Error('offline'))
  const hook = renderHook(() => useNovelMetadata(project, vi.fn()))
  act(() => hook.result.current.change({ title: 'Recovered' }))
  await act(async () => { expect(await hook.result.current.flush()).toBe(false) })
  expect(hook.result.current.error).toContain('已保留修改')
  hook.unmount()
  const other = renderHook(() => useNovelMetadata({ ...project, id: 'two', title: 'Other' }, vi.fn()))
  expect(other.result.current.values.title).toBe('Other')
  const restored = renderHook(() => useNovelMetadata(project, vi.fn()))
  expect(restored.result.current.values.title).toBe('Recovered')
  write.mockImplementation(async (_id, payload) => ({ ...project, ...payload } as Project))
  await act(async () => { window.dispatchEvent(new Event('online')); await Promise.resolve() })
  expect(write).toHaveBeenCalledTimes(2)
  expect(restored.result.current.error).toBeNull()
  expect(sessionStorage.length).toBe(0)
})

it('keeps an empty title locally and blocks leaving until it is valid', async () => {
  write.mockImplementation(async (_id, payload) => ({ ...project, ...payload } as Project))
  const hook = renderHook(() => useNovelMetadata(project, vi.fn()))
  act(() => hook.result.current.change({ title: '' }))
  await act(async () => { expect(await hook.result.current.flush()).toBe(false) })
  expect(write).not.toHaveBeenCalled()
  expect(hook.result.current.values.title).toBe('')
  act(() => hook.result.current.change({ title: 'Valid' }))
  await act(async () => { expect(await hook.result.current.flush()).toBe(true) })
  expect(write).toHaveBeenCalledTimes(1)
})

it('automatically persists a cover change and restores the saved selection', async () => {
  const original = { ...project, metadata: { ...project.metadata, cover: 'cover-2.jpg' } }
  write.mockImplementation(async (_id, payload) => ({ ...original, ...payload } as Project))
  const hook = renderHook(() => useNovelMetadata(original, vi.fn()))
  expect(hook.result.current.values.cover).toBe('cover-2.jpg')
  act(() => hook.result.current.change({ cover: 'cover-6.jpg' }))
  await act(() => vi.advanceTimersByTimeAsync(400))
  expect(write).toHaveBeenCalledWith('one', expect.objectContaining({ metadata: { word_count: 42, labels: ['都市'], cover: 'cover-6.jpg' } }))
})

it('omits unchanged collection bindings rather than sending a forbidden null', async () => {
  write.mockImplementation(async (_id, payload) => ({ ...project, ...payload } as Project))
  const hook = renderHook(() => useNovelMetadata(project, vi.fn()))
  act(() => hook.result.current.change({ title: 'Changed' }))
  await act(async () => { await hook.result.current.flush() })
  expect(write.mock.calls[0][1]).not.toHaveProperty('setting_collection_id')
})

it('keeps a durable draft for leaving after a failed save and restores it without the session copy', async () => {
  write.mockRejectedValue(new Error('offline'))
  const hook = renderHook(() => useNovelMetadata(project, vi.fn()))
  act(() => hook.result.current.change({ title: 'Keep this title', introduction: 'Keep this text' }))
  await act(async () => { await hook.result.current.flush() })
  act(() => { expect(hook.result.current.keepDraft()).toBe(true) })
  hook.unmount(); sessionStorage.clear()
  const recovered = renderHook(() => useNovelMetadata(project, vi.fn()))
  expect(recovered.result.current.values.title).toBe('Keep this title')
  expect(recovered.result.current.values.introduction).toBe('Keep this text')
})

it('repairs the unsupported unbound choice in a recovered draft without losing its text', () => {
  sessionStorage.setItem('guranovel:novel-metadata:one', JSON.stringify({ title: 'Recovered title', genre: '', platform: '', collectionId: '', cover: '', introduction: 'Recovered intro', labels: '都市', slug: '' }))
  const hook = renderHook(() => useNovelMetadata({ ...project, setting_collection_id: 'world' }, vi.fn()))
  expect(hook.result.current.values.collectionId).toBe('world')
  expect(hook.result.current.values.title).toBe('Recovered title')
  expect(hook.result.current.error).toContain('已恢复原设定集')
})

it('reports a rejected save as a conflict instead of incorrectly blaming the network', async () => {
  write.mockRejectedValue(Object.assign(new Error('conflict'), { status: 409 }))
  const hook = renderHook(() => useNovelMetadata(project, vi.fn()))
  act(() => hook.result.current.change({ title: 'Keep me' }))
  await act(async () => { await hook.result.current.flush() })
  expect(hook.result.current.error).toContain('保存被拒绝')
  expect(hook.result.current.error).not.toContain('网络')
})

it('retains the exit backup if an in-flight save finishes after the editor has closed', async () => {
  let resolve!: (value: Project) => void
  write.mockImplementation(() => new Promise(done => { resolve = done }))
  const hook = renderHook(() => useNovelMetadata(project, vi.fn()))
  act(() => hook.result.current.change({ title: 'Slow save' }))
  let pending!: Promise<boolean>
  act(() => { pending = hook.result.current.flush() })
  act(() => { expect(hook.result.current.keepDraft()).toBe(true) })
  hook.unmount()
  await act(async () => { resolve({ ...project, title: 'Slow save' }); await pending })
  expect(JSON.parse(localStorage.getItem('guranovel:novel-metadata:one')!).title).toBe('Slow save')
})

it('removes an older exit backup after recovering and successfully saving newer edits', async () => {
  write.mockRejectedValue(new Error('offline'))
  const first = renderHook(() => useNovelMetadata(project, vi.fn()))
  act(() => first.result.current.change({ title: 'Draft A' }))
  act(() => { first.result.current.keepDraft() })
  first.unmount()
  const recovered = renderHook(() => useNovelMetadata(project, vi.fn()))
  act(() => recovered.result.current.change({ title: 'Newer B' }))
  write.mockImplementation(async (_id, payload) => ({ ...project, ...payload } as Project))
  await act(async () => { await recovered.result.current.flush() })
  expect(localStorage.getItem('guranovel:novel-metadata:one')).toBeNull()
  expect(sessionStorage.getItem('guranovel:novel-metadata:one')).toBeNull()
})

it('does not claim a recovery copy exists when both browser storage locations fail', async () => {
  const blocked = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage blocked') })
  write.mockRejectedValue(new Error('offline'))
  const hook = renderHook(() => useNovelMetadata(project, vi.fn()))
  act(() => hook.result.current.change({ title: 'Only in memory' }))
  await act(async () => { await hook.result.current.flush() })
  expect(hook.result.current.error).not.toContain('已保留修改')
  act(() => { expect(hook.result.current.keepDraft()).toBe(false) })
  expect(hook.result.current.values.title).toBe('Only in memory')
  blocked.mockRestore()
})
