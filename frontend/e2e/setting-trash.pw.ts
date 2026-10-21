/// <reference lib="dom" />
import { expect, test, type Page, type Route } from '@playwright/test'

const collectionId = 'c3661111-1111-4111-8111-111111111111'
const firstId = 'd3661111-1111-4111-8111-111111111111'
const secondId = 'd3662222-2222-4222-8222-222222222222'
const versionId = 'b3661111-1111-4111-8111-111111111111'
type Entry = ReturnType<typeof mockDocument>
function mockDocument(id: string, title: string, deleted = false) {
  return { id, project_id: null, setting_collection_id: collectionId, chapter_id: null,
    type: 'character_profile', title, path: `setting/${id}.md`, current_version_id: versionId,
    current_version: null, metadata: { category: 'setting' }, deleted_at: deleted ? '2026-10-19T06:30:00Z' : null,
    created_at: '2026-10-18T00:00:00Z', updated_at: '2026-10-19T00:00:00Z' }
}
function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}
async function fixture(page: Page, initial: Entry[], options: { archived?: boolean; failLoad?: boolean; failRestore?: string; loseDeleteResponse?: boolean; loseRestoreResponse?: boolean; blockRestoredContent?: boolean } = {}) {
  const entries = initial.map(entry => ({ ...entry }))
  const deletes: string[] = [], restores: string[] = []
  let failLoad = options.failLoad, failRestore = options.failRestore
  let loseDeleteResponse = options.loseDeleteResponse, loseRestoreResponse = options.loseRestoreResponse
  let blockRestoredContent = options.blockRestoredContent
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname, method = route.request().method()
    if (path === `/api/v1/setting-collections/${collectionId}`) return json(route, {
      id: collectionId, owner_id: null, slug: 'trash-workspace', title: '回收站验证设定集', description: null,
      status: options.archived ? 'archived' : 'active', workspace_root: '/workspace/trash', revision: 3,
      metadata: {}, created_at: '2026-10-18T00:00:00Z', updated_at: '2026-10-19T00:00:00Z',
    })
    if (path === `/api/v1/setting-collections/${collectionId}/projects`) return json(route, [])
    if (path === `/api/v1/setting-collections/${collectionId}/documents`) {
      const trash = url.searchParams.get('deleted') === 'true'
      if (trash && failLoad) return json(route, { error: { code: 'unavailable', message: 'temporary unavailable' } }, 503)
      return json(route, entries.filter(entry => Boolean(entry.deleted_at) === trash))
    }
    const match = path.match(/^\/api\/v1\/documents\/([^/]+)(?:\/(content|undelete))?$/)
    const entry = entries.find(item => item.id === match?.[1])
    if (entry && match) {
      if (method === 'DELETE') {
        deletes.push(entry.id); entry.deleted_at = '2026-10-19T06:30:00Z'
        if (loseDeleteResponse) { loseDeleteResponse = false; return route.abort('connectionreset') }
        return route.fulfill({ status: 204 })
      }
      if (match[2] === 'undelete') {
        restores.push(entry.id)
        if (entry.id === failRestore) { failRestore = undefined; return json(route, { error: { code: 'unavailable', message: 'restore failed' } }, 503) }
        entry.deleted_at = null
        if (loseRestoreResponse) { loseRestoreResponse = false; return route.abort('connectionreset') }
        return json(route, entry)
      }
      if (entry.deleted_at) return json(route, { error: { code: 'not_found', message: 'deleted document' } }, 404)
      if (match[2] === 'content' && blockRestoredContent) return json(route, { error: { code: 'unavailable', message: 'content temporarily unavailable' } }, 503)
      if (match[2] === 'content') return json(route, { document_id: entry.id, version_id: versionId, content: `${entry.title}的原文，保留 [[港口]] 引用。` })
      return json(route, entry)
    }
    return json(route, { error: { code: 'not_found', message: 'not found' } }, 404)
  })
  return { entries, deletes, restores, allowTrashLoad: () => { failLoad = false }, allowContentRead: () => { blockRestoredContent = false } }
}

test('delete uses a compact undo notice and a reload still exposes durable trash with the same document identity', async ({ page }) => {
  const state = await fixture(page, [mockDocument(firstId, '旧街')])
  await page.goto(`/setting-collections/${collectionId}`)
  const note = page.locator(`.setting-note-list [data-note-id="${firstId}"]`)
  await expect(note).toBeVisible()
  await note.click({ button: 'right' })
  await page.getByRole('menuitem', { name: '删除条目', exact: true }).click()
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  const toast = page.locator('.setting-undo-toast')
  await expect(toast).toHaveText(/已移至回收站.*撤销/)
  await expect(page.locator('.setting-delete-undo')).toHaveCount(0)
  await expect(note).toHaveCount(0)
  expect(state.deletes).toEqual([firstId])
  await page.reload()
  const trigger = page.getByRole('button', { name: '回收站', exact: true })
  await trigger.click()
  const trash = page.getByRole('dialog', { name: '回收站', exact: true })
  await expect(trash).toBeVisible()
  await expect(trash).toHaveClass(/setting-trash-dialog/)
  await expect(trash.getByRole('searchbox', { name: '搜索回收站' })).toBeFocused()
  await expect(trash.getByRole('listitem', { name: '旧街', exact: true })).toContainText('删除')
  await trash.getByRole('button', { name: '恢复旧街', exact: true }).click()
  await expect(trash.getByRole('status')).toHaveText('回收站是空的')
  await trash.getByRole('button', { name: '关闭回收站' }).click()
  await expect(trash).not.toBeAttached()
  await expect(trigger).toBeFocused()
  await expect(note).toBeVisible()
  expect(state.restores).toEqual([firstId])
  expect(state.entries[0].current_version_id).toBe(versionId)
})

test('batch restore keeps failed entries in the trash and retries only their IDs', async ({ page }) => {
  const state = await fixture(page, [mockDocument(firstId, '旧街', true), mockDocument(secondId, '港口', true)], { failRestore: secondId })
  await page.goto(`/setting-collections/${collectionId}`)
  await page.getByRole('button', { name: '回收站', exact: true }).click()
  const trash = page.getByRole('dialog', { name: '回收站', exact: true })
  await trash.getByRole('checkbox', { name: '选择全部', exact: true }).check()
  await trash.getByRole('button', { name: '恢复所选 2 项' }).click()
  await expect(trash.getByRole('alert')).toContainText('1 项恢复结果未能确认')
  await expect(trash.getByRole('checkbox', { name: '选择港口', exact: true })).toBeChecked()
  await expect(trash.getByRole('listitem', { name: '旧街', exact: true })).toHaveCount(0)
  await trash.getByRole('button', { name: '恢复所选 1 项' }).click()
  await expect(trash.getByRole('status')).toHaveText('回收站是空的')
  expect(state.restores).toEqual([firstId, secondId, secondId])
  await trash.getByRole('button', { name: '关闭回收站' }).click()
  await expect(page.locator(`.setting-note-list [data-note-id="${firstId}"]`)).toBeVisible()
  await expect(page.locator(`.setting-note-list [data-note-id="${secondId}"]`)).toBeVisible()
})

test('load failure can retry and archived collections can browse but cannot restore', async ({ page }) => {
  const state = await fixture(page, [mockDocument(firstId, '旧街', true)], { archived: true, failLoad: true })
  await page.goto(`/setting-collections/${collectionId}`)
  await page.getByRole('button', { name: '回收站', exact: true }).click()
  const trash = page.getByRole('dialog', { name: '回收站', exact: true })
  await expect(trash.getByRole('alert')).toContainText('无法读取回收站')
  await expect(trash.getByText('回收站是空的', { exact: true })).toHaveCount(0)
  state.allowTrashLoad()
  await trash.getByRole('button', { name: '重新加载回收站' }).click()
  await expect(trash.getByRole('listitem', { name: '旧街', exact: true })).toBeVisible()
  await expect(trash.getByText('已归档，只读', { exact: true })).toBeVisible()
  await expect(trash.getByRole('checkbox')).toHaveCount(0)
  await expect(trash.getByRole('button', { name: /恢复/ })).toHaveCount(0)
  expect(state.restores).toEqual([])
})

test('a narrow viewport scrolls only inside the trash and respects reduced motion and keyboard dismissal', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 700 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const entries = Array.from({ length: 28 }, (_, index) => mockDocument(`d366${index.toString().padStart(4, '0')}-1111-4111-8111-111111111111`, `旧街档案 ${index + 1}`, true))
  await fixture(page, entries)
  await page.goto(`/setting-collections/${collectionId}`)
  const trigger = page.getByRole('button', { name: '回收站', exact: true })
  await trigger.click()
  const trash = page.getByRole('dialog', { name: '回收站', exact: true })
  const content = trash.locator('.setting-trash-content')
  await expect(trash.getByRole('listitem')).toHaveCount(28)
  await content.hover()
  await page.mouse.wheel(0, 600)
  await expect.poll(() => content.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
  expect(await page.evaluate(() => ({ y: window.scrollY, overflow: document.documentElement.scrollHeight - document.documentElement.clientHeight }))).toEqual({ y: 0, overflow: 0 })
  expect(await trash.evaluate(element => getComputedStyle(element).animationName)).toBe('none')
  await page.keyboard.press('Escape')
  await expect(trash).not.toBeAttached()
  await expect(trigger).toBeFocused()
})

test('reconciles a lost deletion response against durable trash instead of leaving a ghost active document', async ({ page }) => {
  const state = await fixture(page, [mockDocument(firstId, '旧街')], { loseDeleteResponse: true })
  await page.goto(`/setting-collections/${collectionId}`)
  const note = page.locator(`.setting-note-list [data-note-id="${firstId}"]`)
  await note.click({ button: 'right' })
  await page.getByRole('menuitem', { name: '删除条目', exact: true }).click()
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(page.locator('.setting-undo-toast')).toContainText('已移至回收站')
  await expect(note).toHaveCount(0)
  expect(state.deletes).toEqual([firstId])
  await page.getByRole('button', { name: '回收站', exact: true }).click()
  const trash = page.getByRole('dialog', { name: '回收站', exact: true })
  await expect(trash.getByRole('listitem', { name: '旧街', exact: true })).toBeVisible()
})

test('reconciles a lost restore response with the active document and removes its stale trash row once', async ({ page }) => {
  const state = await fixture(page, [mockDocument(firstId, '旧街', true)], { loseRestoreResponse: true })
  await page.goto(`/setting-collections/${collectionId}`)
  await page.getByRole('button', { name: '回收站', exact: true }).click()
  const trash = page.getByRole('dialog', { name: '回收站', exact: true })
  await trash.getByRole('button', { name: '恢复旧街', exact: true }).click()
  await expect(trash.getByRole('status')).toHaveText('回收站是空的')
  expect(state.restores).toEqual([firstId])
  await trash.getByRole('button', { name: '关闭回收站' }).click()
  await expect(page.locator(`.setting-note-list [data-note-id="${firstId}"]`)).toHaveCount(1)
  await expect(page.locator(`.setting-note-list [data-note-id="${firstId}"]`)).toBeVisible()
  expect(state.entries[0].current_version_id).toBe(versionId)
})

test('retains an uncertain deletion when the trash cannot be checked and lets the author retry', async ({ page }) => {
  const state = await fixture(page, [mockDocument(firstId, '旧街')], { loseDeleteResponse: true, failLoad: true })
  await page.goto(`/setting-collections/${collectionId}`)
  const note = page.locator(`.setting-note-list [data-note-id="${firstId}"]`)
  async function remove() {
    await note.click({ button: 'right' })
    await page.getByRole('menuitem', { name: '删除条目', exact: true }).click()
    await page.getByRole('button', { name: '确认删除', exact: true }).click()
  }
  await remove()
  await expect(page.getByRole('alert')).toContainText('删除')
  await expect(note).toBeVisible()
  await expect(page.locator('.setting-undo-toast')).toHaveCount(0)
  state.allowTrashLoad()
  await remove()
  await expect(page.locator('.setting-undo-toast')).toContainText('已移至回收站')
  await expect(note).toHaveCount(0)
  expect(state.entries[0].current_version_id).toBe(versionId)
})

test('does not remove a trash row until its restored content can be read and retried safely', async ({ page }) => {
  const state = await fixture(page, [mockDocument(firstId, '旧街', true)], { loseRestoreResponse: true, blockRestoredContent: true })
  await page.goto(`/setting-collections/${collectionId}`)
  await page.getByRole('button', { name: '回收站', exact: true }).click()
  const trash = page.getByRole('dialog', { name: '回收站', exact: true })
  await trash.getByRole('button', { name: '恢复旧街', exact: true }).click()
  await expect(trash.getByRole('alert')).toContainText('恢复结果未能确认')
  await expect(trash.getByRole('listitem', { name: '旧街', exact: true })).toBeVisible()
  await expect(trash.getByText('回收站是空的', { exact: true })).toHaveCount(0)
  state.allowContentRead()
  await trash.getByRole('button', { name: '恢复所选 1 项' }).click()
  await expect(trash.getByRole('status')).toHaveText('回收站是空的')
  expect(state.restores).toEqual([firstId, firstId])
  expect(state.entries[0].current_version_id).toBe(versionId)
})
