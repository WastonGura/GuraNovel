import { expect, test, type Locator, type Page } from '@playwright/test'

const collectionId = 'a3111111-1111-4111-8111-111111111111'
const docId = 'b3111111-1111-4111-8111-111111111111'
const projectId = 'd3111111-1111-4111-8111-111111111111'
const stamp = '2026-10-21T01:00:00Z'
const versionId = (number: number) => `c3111111-1111-4111-8111-${String(number).padStart(12, '0')}`
const pointId = (number: number) => `e3111111-1111-4111-8111-${String(number).padStart(12, '0')}`
type Failure = 'network' | 'conflict' | null
type CommentFixture = { id: string; start: number; end: number; quote: string; text: string; color: string; submitted: boolean }
type VersionFixture = {
  id: string; document_id: string; version_number: number; parent_version_id: string | null; restored_from_version_id: string | null; restored_from_restore_point_id: string | null
  source: 'user'; actor_user_id: null; agent_role: null; workflow_run_id: null; content_hash: string; byte_size: number; word_count: number
  file_path: string; change_summary: string | null; created_at: string; metadata: { setting_comments: CommentFixture[] }
}
type SnapshotFixture = { version: VersionFixture; content: string; comments: CommentFixture[] }
type PointFixture = { id: string; document_id: string; version_id: string; point_number: number; summary: string | null; message: string | null; created_at: string }
type CreatePointFixture = { request_id: string; expected_current_version_id: string }

async function workspace(page: Page, options: { versions?: number; points?: number; longPreview?: boolean } = {}) {
  const oldBody = options.longPreview ? `旧港保留着潮汐时代的灯塔。\n${'旧街的地理与历史都被记录在册。\n'.repeat(100)}` : '旧港只在退潮时开放。'
  const oldComments: CommentFixture[] = [{ id: 'historic-comment', start: 0, end: 2, quote: '旧港', text: '确认旧港的地理关系', color: '#70bdcf', submitted: true }]
  const currentComments: CommentFixture[] = [{ id: 'current-comment', start: 0, end: 2, quote: '新港', text: '当前版本的作者意见', color: '#70bdcf', submitted: true }]
  const row = (number: number, content: string, comments: CommentFixture[], restoredFrom: string | null = null): SnapshotFixture => ({
    content, comments,
    version: { id: versionId(number), document_id: docId, version_number: number, parent_version_id: number > 1 ? versionId(number - 1) : null,
      restored_from_version_id: restoredFrom, restored_from_restore_point_id: null, source: 'user', actor_user_id: null, agent_role: null, workflow_run_id: null,
      content_hash: `hash-${number}`, byte_size: content.length, word_count: content.length, file_path: `versions/port-${number}.md`,
      change_summary: restoredFrom ? '恢复历史正文' : `第 ${number} 次保存`, created_at: stamp, metadata: { setting_comments: comments } },
  })
  const count = options.versions ?? 3
  const state = {
    status: 'active', revision: 1, title: '潮汐档案', currentId: versionId(count), saveFailure: null as Failure, restoreFailure: null as Failure,
    archiveFailure: null as Failure, loseRestoreResponse: false, saveAttempts: 0, restoreAttempts: 0, archiveAttempts: 0,
    createFailure: null as Failure, loseCreateResponse: false, createAttempts: 0, pointReadFailures: 0, createPayloads: [] as CreatePointFixture[],
    associationCount: 1, associationReads: 0, associationFailure: false,
    events: [] as string[], versionReads: [] as string[], unexpected: [] as string[],
    points: Array.from({ length: options.points ?? 1 }, (_, index): PointFixture => ({ id: pointId(index + 1), document_id: docId, version_id: versionId(index + 1),
      point_number: index + 1, summary: null, message: index === 0 ? '旧港的初始设定' : null, created_at: stamp })),
    snapshots: Array.from({ length: count }, (_, index) => row(index + 1, index === 0 ? oldBody : index + 1 === count ? '新港昼夜开放。' : `港口的第 ${index + 1} 版记录。`, index === 0 ? oldComments : currentComments)),
  }
  const current = () => state.snapshots.find(snapshot => snapshot.version.id === state.currentId)!
  const pointResponse = (point: PointFixture) => ({ ...point, version: state.snapshots.find(snapshot => snapshot.version.id === point.version_id)!.version,
    matches_current: point.version_id === state.currentId })
  const collection = () => ({ id: collectionId, owner_id: null, title: state.title, slug: 'history-archive', status: state.status,
    description: '用于历史与归档交互验证', workspace_root: '/workspace/history-archive', revision: state.revision, metadata: {}, created_at: stamp, updated_at: stamp })
  const document = () => ({ id: docId, project_id: null, chapter_id: null, setting_collection_id: collectionId, type: 'world_overview', title: '港口',
    path: 'world/port.md', current_version_id: state.currentId, current_version: current().version,
    metadata: { category: 'world', comments: current().comments, comments_source_version_id: state.currentId }, created_at: stamp, updated_at: stamp })
  const project = { id: projectId, title: '归航之夜', slug: 'returning-at-night', genre: null, target_platform: null, status: 'active',
    workspace_root: '/workspace/novel', setting_collection_id: collectionId, metadata: { labels: ['群像'], introduction: '同一个港口中的另一段故事。' }, created_at: stamp, updated_at: stamp }
  const projects = [project, { ...project, id: 'd3111111-1111-4111-8111-111111111112', title: '夜渡', slug: 'night-ferry' }]
  let associationHold: Promise<void> | null = null
  let saveHold: Promise<void> | null = null
  function append(content: string, comments: CommentFixture[], restoredFrom: string | null = null) {
    const snapshot = row(state.snapshots.length + 1, content, comments, restoredFrom)
    snapshot.version.parent_version_id = state.currentId
    state.snapshots.push(snapshot); state.currentId = snapshot.version.id; state.revision++
    return snapshot
  }
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    const conflict = () => json({ error: { code: 'conflict', message: '版本已被其他操作更新。' } }, 409)
    if (path === `/api/v1/setting-collections/${collectionId}`) return json(collection())
    if (path === `/api/v1/setting-collections/${collectionId}/projects`) {
      state.associationReads++
      if (associationHold) await associationHold
      return state.associationFailure ? route.abort('connectionfailed') : json(projects.slice(0, state.associationCount))
    }
    if (path === `/api/v1/setting-collections/${collectionId}/documents`) return json([document()])
    if (path === `/api/v1/setting-collections/${collectionId}/collaboration` || path === `/api/v1/setting-collections/${collectionId}/collaboration/conversations/f3111111-1111-4111-8111-111111111111`) return json({
      id: 'f3111111-1111-4111-8111-111111111111', title: '新对话', is_default: true, client_request_id: null, setting_collection_id: collectionId,
      document_id: null, agent_role: 'lore_agent', messages: [], attempts: [], created_at: stamp, updated_at: stamp,
    })
    if (path === `/api/v1/documents/${docId}`) return json(document())
    if (path === `/api/v1/documents/${docId}/versions`) return json(state.snapshots.map(snapshot => snapshot.version))
    if (path === `/api/v1/documents/${docId}/restore-points`) {
      if (method === 'GET') {
        if (state.pointReadFailures) { state.pointReadFailures--; return route.abort('connectionfailed') }
        return json(state.points.map(pointResponse))
      }
      expect(method).toBe('POST')
      state.createAttempts++; state.events.push('create-point-request')
      const payload = request.postDataJSON() as CreatePointFixture
      expect(Object.keys(payload).sort()).toEqual(['expected_current_version_id', 'request_id'])
      state.createPayloads.push(payload)
      const previous = state.points.find(point => point.id === payload.request_id)
      if (previous) return previous.version_id === payload.expected_current_version_id ? json(pointResponse(previous)) : conflict()
      expect(state.status).toBe('active')
      if (state.createFailure === 'network') return route.abort('connectionfailed')
      if (state.createFailure === 'conflict' || payload.expected_current_version_id !== state.currentId) return conflict()
      const point: PointFixture = { id: payload.request_id, document_id: docId, version_id: state.currentId, point_number: state.points.length + 1, summary: null, message: null, created_at: stamp }
      state.points.push(point); state.events.push('create-point-complete')
      if (state.loseCreateResponse) { state.loseCreateResponse = false; return route.abort('connectionfailed') }
      return json(pointResponse(point), 201)
    }
    if (path === `/api/v1/documents/${docId}/content`) {
      if (method === 'PUT') {
        state.saveAttempts++; state.events.push('save-request')
        if (saveHold) await saveHold
        if (state.saveFailure === 'network') return route.abort('connectionfailed')
        if (state.saveFailure === 'conflict' || request.postDataJSON().expected_current_version_id !== state.currentId) return conflict()
        expect(state.status).toBe('active')
        const payload = request.postDataJSON(), saved = append(payload.content, payload.setting_comments ?? current().comments)
        state.events.push('save-complete'); return json(saved.version)
      }
      return json({ document_id: docId, version_id: state.currentId, content: current().content })
    }
    const readVersion = path.match(new RegExp(`^/api/v1/documents/${docId}/versions/([^/]+)/content$`))
    if (readVersion) {
      state.versionReads.push(readVersion[1])
      const snapshot = state.snapshots.find(snapshot => snapshot.version.id === readVersion[1])
      return snapshot ? json({ document_id: docId, version_id: snapshot.version.id, content: snapshot.content }) : json({}, 404)
    }
    const restore = path.match(new RegExp(`^/api/v1/documents/${docId}/restore-points/([^/]+)/restore$`))
    if (restore) {
      state.restoreAttempts++; state.events.push('restore-request')
      expect(state.status).toBe('active')
      const payload = request.postDataJSON()
      expect(payload.expected_current_version_id).toBe(state.currentId)
      if (state.restoreFailure === 'network') return route.abort('connectionfailed')
      if (state.restoreFailure === 'conflict') {
        append('其他作者更新后的远端正文。', []); return conflict()
      }
      const point = state.points.find(point => point.id === restore[1])!
      const target = state.snapshots.find(snapshot => snapshot.version.id === point.version_id)!
      const restored = append(target.content, target.comments, target.version.id)
      restored.version.restored_from_restore_point_id = point.id
      if (state.loseRestoreResponse) { state.loseRestoreResponse = false; return route.abort('connectionfailed') }
      return json(restored.version)
    }
    if (path === `/api/v1/setting-collections/${collectionId}/archive`) {
      state.archiveAttempts++; state.events.push('archive-request')
      if (state.archiveFailure === 'network') return route.abort('connectionfailed')
      if (state.archiveFailure === 'conflict') return conflict()
      state.status = 'archived'; state.revision++; return json(collection())
    }
    if (path === '/api/v1/projects' || path === '/api/v1/setting-collections') return json([])
    state.unexpected.push(`${method} ${path}`)
    return json({ error: { code: 'not_found', message: '未定义的验证接口。' } }, 404)
  })
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&category=world&mode=note`)
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText('新港昼夜开放。')
  return { state, current, oldBody, oldComments, holdAssociationRefresh() {
    let release!: () => void
    associationHold = new Promise(resolve => { release = resolve })
    return () => { associationHold = null; release() }
  }, holdSave() {
    let release!: () => void
    saveHold = new Promise(resolve => { release = resolve })
    return () => { saveHold = null; release() }
  } }
}

async function openHistory(page: Page) {
  await page.getByRole('button', { name: '历史存档', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '历史存档', exact: true })
  await expect(dialog.getByRole('navigation', { name: '手动存档列表', exact: true })).toHaveAttribute('aria-busy', 'false')
  return dialog
}

async function history(page: Page) {
  const dialog = await openHistory(page)
  await expect(dialog.getByRole('button', { name: '存档 1', exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '存档 1', exact: true }).click()
  return dialog
}

async function saveCurrent(page: Page) {
  await page.getByRole('button', { name: '存档当前内容', exact: true }).click()
  await expect(page.getByRole('button', { name: '存档当前内容', exact: true })).toHaveAttribute('data-state', 'success')
  await expect(page.getByText('已存档', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('dialog', { name: '历史存档', exact: true })).not.toBeAttached()
}

async function expectPageArchive(page: Page) {
  const tools = page.locator('.setting-editor-tools')
  const create = tools.getByRole('button', { name: '存档当前内容', exact: true })
  const history = tools.getByRole('button', { name: '历史存档', exact: true })
  await expect(create.locator('svg')).toHaveCount(1)
  await expect(create).toHaveAttribute('title', '存档当前内容')
  expect(await create.innerText()).toBe('')
  const button = (await create.boundingBox())!, icon = (await create.locator('svg').boundingBox())!
  const historyBounds = (await history.boundingBox())!, editor = (await page.getByLabel('设定正文', { exact: true }).boundingBox())!
  expect(button.width).toBeCloseTo(32, 0)
  expect(button.height).toBeCloseTo(32, 0)
  expect(icon.width).toBeCloseTo(20, 0)
  expect(icon.height).toBeCloseTo(20, 0)
  expect(Math.abs(icon.x + icon.width / 2 - button.x - button.width / 2)).toBeLessThanOrEqual(1)
  expect(Math.abs(icon.y + icon.height / 2 - button.y - button.height / 2)).toBeLessThanOrEqual(1)
  expect(Math.abs(historyBounds.y + historyBounds.height / 2 - button.y - button.height / 2)).toBeLessThanOrEqual(2)
  expect(button.x).toBeGreaterThanOrEqual(page.viewportSize()!.width / 2)
  expect(button.x).toBeGreaterThanOrEqual(historyBounds.x + historyBounds.width)
  expect(button.x + button.width).toBeLessThanOrEqual(page.viewportSize()!.width - 8)
  expect(button.y + button.height).toBeLessThanOrEqual(editor.y)
  await expect(tools.getByRole('button', { name: '与设定 Agent 协作', exact: true })).toHaveCount(0)
}

async function expectHistoryChrome(dialog: Locator, pointCount: number) {
  const header = dialog.locator(':scope > div > header')
  const title = header.getByRole('heading', { name: '历史存档', exact: true })
  const count = header.locator(':scope > span').filter({ hasText: `${pointCount} 个存档` })
  const close = header.getByRole('button', { name: '关闭历史存档', exact: true })
  await expect(dialog.getByRole('button', { name: '存档当前内容', exact: true })).toHaveCount(0)
  const headerBounds = (await header.boundingBox())!, closeBounds = (await close.boundingBox())!
  for (const control of [title, count, close]) {
    await expect(control).toBeVisible()
    const bounds = (await control.boundingBox())!
    expect(Math.abs(bounds.y + bounds.height / 2 - closeBounds.y - closeBounds.height / 2)).toBeLessThanOrEqual(2)
    expect(bounds.x).toBeGreaterThanOrEqual(headerBounds.x)
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(headerBounds.x + headerBounds.width)
  }
  const restore = dialog.getByRole('button', { name: '恢复此存档', exact: true })
  const restoreBounds = (await restore.boundingBox())!
  await expect(restore).toHaveCSS('background-color', 'rgb(34, 34, 34)')
  await expect(restore).toHaveCSS('color', 'rgb(255, 255, 255)')
  expect(restoreBounds.height).toBeGreaterThanOrEqual(38)
  expect(restoreBounds.width).toBeGreaterThan(restoreBounds.height)
  expect(await restore.evaluate(element => Number.parseFloat(getComputedStyle(element).borderRadius))).toBeGreaterThanOrEqual(restoreBounds.height / 2)
}

async function archiveConfirmation(page: Page) {
  await page.getByRole('button', { name: '设定集操作', exact: true }).click()
  const menu = page.getByRole('menu', { name: '设定集操作', exact: true })
  await expect(menu).toBeVisible()
  await expect(page.getByRole('dialog', { name: '归档设定集', exact: true })).not.toBeAttached()
  await menu.getByRole('menuitem', { name: '归档设定集', exact: true }).click()
  await expect(page.locator('[role="menu"]')).not.toBeAttached()
  const dialog = page.getByRole('dialog', { name: '归档设定集', exact: true })
  await expect(dialog.getByRole('button', { name: '确认归档', exact: true })).toBeVisible()
  await expect(dialog).toContainText('归档后')
  await expect(dialog).toContainText('已写章节')
  await expect(dialog).toContainText('归航之夜')
  return dialog
}

test('autosaves protect current prose without creating user history, while one manual point survives later writing and reload', async ({ page }) => {
  const { state, current } = await workspace(page, { points: 0 })
  const editor = page.getByLabel('设定正文', { exact: true })
  const saved = '自动保存的港口正文。'
  await editor.fill(saved)
  await expect.poll(() => current().content).toBe(saved)
  expect(state.points).toHaveLength(0)
  expect(state.createAttempts).toBe(0)
  const savedVersionId = state.currentId, technicalCount = state.snapshots.length
  let dialog = await openHistory(page)
  await expect(dialog.getByRole('button', { name: /^存档 \d+$/ })).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: '存档当前内容', exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeAttached()
  await saveCurrent(page)
  expect(state.points).toHaveLength(1)
  expect(state.createAttempts).toBe(1)
  expect(state.createPayloads[0].expected_current_version_id).toBe(savedVersionId)
  expect(state.snapshots).toHaveLength(technicalCount)
  await saveCurrent(page)
  expect(state.createAttempts).toBe(1) // The page action also deduplicates an already-current archive.
  await editor.fill('存档后继续自动同步的新正文。')
  await expect.poll(() => current().content).toBe('存档后继续自动同步的新正文。')
  expect(state.points).toHaveLength(1)
  await page.reload()
  await expect(editor).toHaveText('存档后继续自动同步的新正文。')
  dialog = await history(page)
  await expect(dialog.getByRole('button', { name: /^存档 \d+$/ })).toHaveCount(1)
  await expect(dialog.getByRole('region', { name: '存档正文' })).toHaveText(saved)
  expect(state.points[0].version_id).toBe(savedVersionId)
  expect(state.createAttempts).toBe(1)
  expect(state.unexpected).toEqual([])
})

test('uncertain manual creation retries the exact journaled identity after reload and never creates two points', async ({ page }) => {
  const { state } = await workspace(page, { points: 0 })
  state.createFailure = 'network'
  await page.getByRole('button', { name: '存档当前内容', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('存档结果未能确认')
  expect(state.points).toHaveLength(0)
  const request = state.createPayloads[0]
  expect(await page.evaluate(id => JSON.parse(sessionStorage.getItem(`guranovel:setting-restore-point-pending:${id}`)!), docId)).toEqual(request)
  state.createFailure = null
  await page.reload()
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText('新港昼夜开放。')
  await saveCurrent(page)
  const dialog = await openHistory(page)
  await expect(dialog.getByRole('button', { name: '存档 1', exact: true })).toBeVisible()
  expect(state.createPayloads).toEqual([request, request])
  expect(state.points).toHaveLength(1)
  expect(state.points[0].id).toBe(request.request_id)
  expect(await page.evaluate(id => sessionStorage.getItem(`guranovel:setting-restore-point-pending:${id}`), docId)).toBeNull()
  expect(state.unexpected).toEqual([])
})

test('a committed manual point with a lost response is recognized by request identity without a duplicate POST', async ({ page }) => {
  const { state } = await workspace(page, { points: 0 })
  state.loseCreateResponse = true
  await saveCurrent(page)
  const dialog = await openHistory(page)
  await expect(dialog.getByRole('button', { name: '存档 1', exact: true })).toBeVisible()
  expect(state.createAttempts).toBe(1)
  expect(state.points).toHaveLength(1)
  expect(state.points[0].id).toBe(state.createPayloads[0].request_id)
  expect(state.snapshots).toHaveLength(3)
  expect(await page.evaluate(id => sessionStorage.getItem(`guranovel:setting-restore-point-pending:${id}`), docId)).toBeNull()
  expect(state.unexpected).toEqual([])
})

test('restoring a preview appends a new version and reloads its original comments', async ({ page }) => {
  const { state, current, oldBody } = await workspace(page)
  const previousId = state.currentId
  const dialog = await history(page)
  await expect(dialog.getByRole('region', { name: '存档正文' })).toHaveText(oldBody)
  expect(state.restoreAttempts).toBe(0)
  await dialog.getByRole('button', { name: '恢复此存档', exact: true }).click()
  await expect(dialog.getByRole('button', { name: '返回预览', exact: true })).toBeVisible()
  expect(state.restoreAttempts).toBe(0)
  await dialog.getByRole('button', { name: '确认恢复', exact: true }).click()
  await expect.poll(() => state.currentId).toBe(versionId(4))
  expect(current().version).toMatchObject({ parent_version_id: previousId, restored_from_version_id: versionId(1), restored_from_restore_point_id: pointId(1), version_number: 4 })
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText(oldBody)
  await page.reload()
  const editor = page.getByLabel('设定正文', { exact: true })
  await expect(editor).toHaveText(oldBody)
  await expect(editor.locator('[data-comment-id="historic-comment"]')).toHaveCount(1)
  const reloadedHistory = await history(page)
  await expect(reloadedHistory.getByRole('button', { name: /^存档 \d+$/ })).toHaveCount(1)
  expect(state.points).toHaveLength(1) // Restoring appends technical history, never a new manual point.
  expect(state.unexpected).toEqual([])
})

test('a lost restore response is reconciled by its exact source version without restoring twice', async ({ page }) => {
  const { state, current, oldBody } = await workspace(page)
  state.loseRestoreResponse = true
  const dialog = await history(page)
  await dialog.getByRole('button', { name: '恢复此存档', exact: true }).click()
  await dialog.getByRole('button', { name: '确认恢复', exact: true }).click()
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText(oldBody)
  expect(state.restoreAttempts).toBe(1)
  expect(current().version.restored_from_version_id).toBe(versionId(1))
  expect(current().version.restored_from_restore_point_id).toBe(pointId(1))
  expect(state.snapshots).toHaveLength(4)
  await page.reload()
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText(oldBody)
})

for (const failure of ['conflict', 'network'] as const) test(`a ${failure} during restore preserves the author's draft and does not apply the historical text`, async ({ page }) => {
  const { state, oldBody } = await workspace(page)
  const editor = page.getByLabel('设定正文', { exact: true })
  const draft = '作者刚写下的正文必须保留。'
  await editor.fill(draft)
  const dialog = await history(page)
  await expect(dialog.getByRole('region', { name: '存档正文' })).toHaveText(oldBody)
  state.restoreFailure = failure
  await dialog.getByRole('button', { name: '恢复此存档', exact: true }).click()
  await dialog.getByRole('button', { name: '确认恢复', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText(failure === 'conflict' ? '版本已更新，当前草稿已保留' : '恢复结果未能确认，当前文稿已保留')
  await expect(editor).toHaveText(draft)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeAttached()
  await expect(editor).toHaveText(draft)
  expect(state.restoreAttempts).toBe(1)
})

test('history remains readable when saving fails, but restoring cannot bypass the unsaved draft', async ({ page }) => {
  const { state, oldBody } = await workspace(page)
  state.saveFailure = 'network'
  const draft = '网络失败时未提交的本地文字。'
  await page.getByLabel('设定正文', { exact: true }).fill(draft)
  await page.getByRole('button', { name: '存档当前内容', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: '当前草稿未能保存，已暂停存档' })).toBeVisible()
  const dialog = await history(page)
  await expect(dialog.getByRole('region', { name: '存档正文' })).toHaveText(oldBody)
  await expect(dialog.getByRole('button', { name: '恢复此存档', exact: true })).toBeDisabled()
  await expect(dialog.getByRole('button', { name: '存档当前内容', exact: true })).toHaveCount(0)
  await expect(dialog.getByRole('alert')).toContainText('当前草稿未能保存')
  await expect(dialog.getByRole('button', { name: '恢复此存档', exact: true })).toHaveAccessibleDescription(/当前草稿未能保存/)
  expect(state.restoreAttempts).toBe(0)
  expect(state.createAttempts).toBe(0)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeAttached()
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText(draft)
  await page.reload()
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText(draft)
  expect(state.currentId).toBe(versionId(3))
})

test('archiving waits for all editing to save and leaves history and related novels readable', async ({ page }) => {
  const { state, current } = await workspace(page)
  const cancellation = await archiveConfirmation(page)
  expect(state.archiveAttempts).toBe(0)
  await cancellation.getByRole('button', { name: '取消', exact: true }).click()
  await expect(cancellation).not.toBeAttached()
  expect(state.archiveAttempts).toBe(0)
  state.saveFailure = 'network'
  const draft = '归档前这段最新正文必须先保存。'
  await page.getByLabel('设定正文', { exact: true }).fill(draft)
  let dialog = await archiveConfirmation(page)
  await dialog.getByRole('button', { name: '确认归档', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('未能保存')
  expect(state.archiveAttempts).toBe(0)
  expect(state.status).toBe('active')
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeAttached()
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText(draft)
  state.saveFailure = null
  dialog = await archiveConfirmation(page)
  await dialog.getByRole('button', { name: '确认归档', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: '已归档 · 只读' })).toBeVisible()
  expect(current().content).toBe(draft)
  expect(state.events.lastIndexOf('save-complete')).toBeLessThan(state.events.indexOf('archive-request'))
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveAttribute('contenteditable', 'false')
  await expect(page.getByRole('button', { name: '新建内容', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '存档当前内容', exact: true })).toHaveCount(0)
  const versions = await history(page)
  await expect(versions.getByRole('button', { name: '恢复此存档', exact: true })).toBeDisabled()
  await expect(versions.getByRole('button', { name: '存档当前内容', exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(versions).not.toBeAttached()
  await page.getByRole('button', { name: '设定集操作', exact: true }).click()
  const menu = page.getByRole('menu', { name: '设定集操作', exact: true })
  await expect(menu.getByRole('menuitem', { name: '归档设定集', exact: true })).toHaveCount(0)
  await menu.getByRole('menuitem', { name: '与设定 Agent 协作', exact: true }).click()
  await expect(menu).not.toBeAttached()
  const collaboration = page.getByRole('complementary', { name: '设定 Agent 协作', exact: true })
  await expect(collaboration).toBeVisible()
  await expect(collaboration.getByRole('textbox', { name: '给设定 Agent 的消息', exact: true })).toBeDisabled()
  await collaboration.getByRole('button', { name: '关闭设定协作', exact: true }).click()
  await expect(collaboration).not.toBeAttached()
  await page.getByRole('button', { name: '关联小说 (1)', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '关联作品', exact: true })).toContainText('归航之夜')
  expect(state.unexpected).toEqual([])
})

for (const failure of ['conflict', 'network'] as const) test(`a ${failure} during archive remains an explicit failure with the collection editable`, async ({ page }) => {
  const { state } = await workspace(page)
  state.archiveFailure = failure
  const dialog = await archiveConfirmation(page)
  await dialog.getByRole('button', { name: '确认归档', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('归档结果未能确认')
  expect(state.status).toBe('active')
  expect(state.archiveAttempts).toBe(1)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeAttached()
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveAttribute('contenteditable', 'true')
  expect(state.unexpected).toEqual([])
})

for (const viewport of [{ width: 1440, height: 900 }, { width: 926, height: 716 }]) test(`opening history keeps the known association button stable while impact refreshes, fails and retries at ${viewport.width}x${viewport.height}`, async ({ page }) => {
  await page.setViewportSize(viewport)
  const { state, holdAssociationRefresh, holdSave } = await workspace(page)
  const associations = page.locator('.setting-pill-novels')
  await expect(associations).toHaveAccessibleName('关联小说 (1)')
  const initial = (await associations.boundingBox())!
  const expectStable = async (phase: string) => {
    const next = (await associations.boundingBox())!
    await expect.soft(associations, phase).toHaveAccessibleName('关联小说 (1)')
    for (const key of ['x', 'y', 'width'] as const) expect.soft(Math.abs(next[key] - initial[key]), `${phase} ${key}: ${initial[key]} → ${next[key]}`).toBeLessThanOrEqual(0.5)
  }
  const previousReads = state.associationReads
  const release = holdAssociationRefresh()
  const releaseSave = holdSave()
  const draft = '打开历史前必须同步的最新港口正文。'
  await page.getByLabel('设定正文', { exact: true }).fill(draft)
  await page.getByRole('button', { name: '历史存档', exact: true }).click()
  await expect.poll(() => state.saveAttempts).toBe(1)
  await expect(page.getByRole('region', { name: '设定集正文', exact: true })).toHaveAttribute('aria-busy', 'true')
  await associations.evaluate(element => Promise.all(element.getAnimations().map(animation => animation.finished)))
  await expect.soft(associations, 'pending flush must not flash the navigation button').toHaveCSS('opacity', '1')
  await expectStable('waiting for the draft to flush')
  await associations.evaluate(element => (element as HTMLButtonElement).click())
  await expect(page.getByRole('dialog', { name: '关联作品', exact: true })).not.toBeAttached()
  releaseSave()
  let dialog = page.getByRole('dialog', { name: '历史存档', exact: true })
  await expect(dialog.getByRole('button', { name: '存档 1', exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '存档 1', exact: true }).click()
  await expect.poll(() => state.associationReads).toBe(previousReads + 1)
  await expectStable('background refresh pending')
  await expect(dialog.getByRole('navigation', { name: '手动存档列表', exact: true }).getByText('手动存档', { exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: '恢复此存档', exact: true }).click()
  await expect(dialog.getByRole('status').filter({ hasText: '正在读取关联小说…' })).toBeVisible()
  expect(state.restoreAttempts).toBe(0)
  state.associationFailure = true; release()
  await expect(dialog.getByRole('button', { name: '重新加载关联小说', exact: true })).toBeVisible()
  await expectStable('background refresh failed')
  await dialog.getByRole('button', { name: '确认恢复', exact: true }).click()
  await expect(dialog.getByRole('alert').filter({ hasText: /^关联小说未能读取，请重新加载后再恢复。$/ })).toBeVisible()
  expect(state.restoreAttempts).toBe(0)
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText(draft)
  state.associationFailure = false
  await dialog.getByRole('button', { name: '重新加载关联小说', exact: true }).click()
  await expect(dialog.getByText('归航之夜', { exact: true })).toBeVisible()
  await expectStable('same count successfully refreshed')
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeAttached()
  state.associationCount = 2
  dialog = await history(page)
  await expect(associations).toHaveAccessibleName('关联小说 (2)')
  await dialog.getByRole('button', { name: '恢复此存档', exact: true }).click()
  await expect(dialog.getByText('关联小说 · 2 部', { exact: true })).toBeVisible()
  await expect(dialog.getByText('夜渡', { exact: true })).toBeVisible()
  expect(state.restoreAttempts).toBe(0)
  expect(state.unexpected).toEqual([])
})

for (const viewport of [{ width: 1440, height: 900 }, { width: 926, height: 716 }]) test(`history is animated, keyboard accessible and scrolls within its columns at ${viewport.width}x${viewport.height}`, async ({ page }) => {
  await page.setViewportSize(viewport)
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  const { oldBody } = await workspace(page, { versions: 24, points: 24, longPreview: true })
  await expectPageArchive(page)
  const trigger = page.getByRole('button', { name: '历史存档', exact: true })
  await trigger.click()
  const dialog = page.getByRole('dialog', { name: '历史存档', exact: true })
  await expect(dialog).toHaveCSS('animation-name', 'creation-dialog-in')
  await dialog.evaluate(element => Promise.all(element.getAnimations().map(animation => animation.finished)))
  const first = dialog.getByRole('button', { name: '存档 1', exact: true })
  await first.focus(); await first.press('Enter')
  await first.hover()
  await expect.soft(first.locator('strong')).toHaveCSS('color', 'rgb(255, 255, 255)')
  await expect.soft(first.locator('.setting-history-version-meta')).toHaveCSS('color', 'rgb(255, 255, 255)')
  await expect.soft(first.locator('.setting-history-version-meta > span')).toHaveCSS('color', 'rgb(255, 255, 255)')
  await expect.soft(first.locator('.setting-history-version-heading')).toHaveCSS('padding', '0px')
  await expect.soft(first.locator('.setting-history-version-meta')).toHaveCSS('padding', '0px')
  await expect.soft(first.locator('.setting-history-version-meta > span')).toHaveCSS('padding', '0px')
  const preview = dialog.getByRole('region', { name: '存档正文' })
  await expect(preview).toHaveText(oldBody)
  await expectHistoryChrome(dialog, 24)
  const listBounds = (await dialog.getByRole('navigation', { name: '手动存档列表' }).boundingBox())!
  const detailBounds = (await dialog.locator('.setting-history-detail').boundingBox())!
  expect(Math.abs(listBounds.y - detailBounds.y)).toBeLessThanOrEqual(1)
  expect(Math.abs(listBounds.x + listBounds.width - detailBounds.x)).toBeLessThanOrEqual(1)
  expect(listBounds.width).toBeGreaterThanOrEqual(200)
  expect(detailBounds.width).toBeGreaterThan(listBounds.width)
  expect(await preview.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
  const box = (await dialog.boundingBox())!
  expect(box.width).toBeCloseTo(Math.min(960, viewport.width - 40), 0)
  expect(box.height).toBeLessThanOrEqual(viewport.height - 46)
  expect(box.x).toBeGreaterThanOrEqual(15)
  expect(box.y).toBeGreaterThanOrEqual(23)
  expect(await page.evaluate(() => document.documentElement.scrollHeight - innerHeight)).toBe(0)
  const restore = dialog.getByRole('button', { name: '恢复此存档', exact: true })
  const footer = dialog.locator('.setting-history-footer')
  await expect(footer).toHaveText('恢复此存档')
  const footerBounds = (await footer.boundingBox())!, restoreBounds = (await restore.boundingBox())!
  const rightPadding = await footer.evaluate(element => Number.parseFloat(getComputedStyle(element).paddingRight))
  expect(Math.abs(restoreBounds.x + restoreBounds.width - footerBounds.x - footerBounds.width + rightPadding)).toBeLessThanOrEqual(2)
  await restore.focus(); await expect(restore).toBeFocused()
  const handle = (await dialog.elementHandle())!
  await dialog.evaluate(element => {
    const observer = new MutationObserver(() => {
      if (!element.classList.contains('is-closing')) return
      element.setAttribute('data-was-open-on-exit', String((element as HTMLDialogElement).open))
      element.setAttribute('data-was-inert-on-exit', String((element as HTMLElement).inert))
      observer.disconnect()
    })
    observer.observe(element, { attributes: true, attributeFilter: ['class'] })
  })
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeAttached()
  expect(await handle.evaluate(element => ({ open: element.getAttribute('data-was-open-on-exit'), inert: element.getAttribute('data-was-inert-on-exit') }))).toEqual({ open: 'true', inert: 'true' })
  await handle.dispose()
  await expect(trigger).toBeFocused()
})

for (const viewport of [{ width: 390, height: 844 }, { width: 320, height: 568 }]) test(`history keeps its title, count and close on one header row and a pill footer within the narrow viewport at ${viewport.width}x${viewport.height}`, async ({ page }) => {
  await page.setViewportSize(viewport.width === 320 ? { width: 1280, height: 900 } : viewport)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const { oldBody, state } = await workspace(page, { versions: 24, points: 24, longPreview: true })
  const dialog = await history(page)
  // Exercise an already-open modal reflow at the minimum viewport independently of the pinned sidebar.
  if (viewport.width === 320) await page.setViewportSize(viewport)
  await expect(dialog.getByRole('region', { name: '存档正文' })).toHaveText(oldBody)
  await expectHistoryChrome(dialog, 24)
  const bounds = (await dialog.boundingBox())!
  expect(bounds.x).toBeGreaterThanOrEqual(8)
  expect(bounds.y).toBeGreaterThanOrEqual(8)
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width - 8)
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height - 8)
  const list = dialog.getByRole('navigation', { name: '手动存档列表' })
  const listBounds = (await list.boundingBox())!, detailBounds = (await dialog.locator('.setting-history-detail').boundingBox())!
  expect(listBounds.y + listBounds.height).toBeLessThanOrEqual(detailBounds.y + 1)
  expect(Math.abs(listBounds.width - detailBounds.width)).toBeLessThanOrEqual(1)
  expect(await list.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true)
  const footer = (await dialog.locator('.setting-history-footer').boundingBox())!
  expect(footer.y + footer.height).toBeLessThanOrEqual(bounds.y + bounds.height)
  await dialog.getByRole('button', { name: '恢复此存档', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(dialog.getByRole('button', { name: '返回预览', exact: true })).toBeFocused()
  await expect(dialog.getByRole('button', { name: '确认恢复', exact: true })).toBeVisible()
  expect(state.restoreAttempts).toBe(0)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeAttached()
  expect(state.unexpected).toEqual([])
})

for (const viewport of [{ width: 1440, height: 900 }, { width: 926, height: 716 }]) test(`entry and empty-category headings share an origin and the collection actions menu is anchored and keyboard accessible at ${viewport.width}x${viewport.height}`, async ({ page }) => {
  await page.setViewportSize(viewport)
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  const { state } = await workspace(page)
  const surface = page.locator('.setting-document-motion')
  const settleHeading = async () => {
    await expect(surface).not.toHaveAttribute('data-text-phase', /.+/)
    await surface.evaluate(element => Promise.all(element.getAnimations({ subtree: true })
      .filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity)
      .map(animation => animation.finished.catch(() => {}))))
  }
  await settleHeading()
  const entry = (await page.locator('.setting-document').getByRole('heading', { name: '港口', exact: true }).boundingBox())!
  await page.getByRole('group', { name: '设定分类' }).getByRole('button', { name: '设定', exact: true }).click()
  const empty = page.locator('.setting-empty').getByRole('heading', { name: '设定', exact: true })
  await expect(empty).toBeVisible()
  await settleHeading()
  const emptyBox = (await empty.boundingBox())!
  expect(Math.abs(emptyBox.x - entry.x), `heading x: entry=${entry.x}, empty=${emptyBox.x}`).toBeLessThanOrEqual(2)
  expect(Math.abs(emptyBox.y - entry.y), `heading y: entry=${entry.y}, empty=${emptyBox.y}`).toBeLessThanOrEqual(2)

  const trigger = page.getByRole('button', { name: '设定集操作', exact: true })
  const button = (await trigger.boundingBox())!
  expect(button.width).toBeGreaterThanOrEqual(32)
  expect(button.height).toBeGreaterThanOrEqual(32)
  expect(button.x).toBeGreaterThanOrEqual(0)
  expect(button.x + button.width).toBeLessThanOrEqual(viewport.width)
  expect(button.y).toBeGreaterThanOrEqual(0)
  expect(button.y + button.height).toBeLessThanOrEqual(viewport.height)
  await trigger.focus()
  await page.keyboard.press('Enter')
  const menu = page.getByRole('menu', { name: '设定集操作', exact: true })
  await expect(menu).toBeVisible()
  await expect(menu.getByRole('menuitem', { name: '与设定 Agent 协作', exact: true })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(menu.getByRole('menuitem', { name: '归档设定集', exact: true })).toBeFocused()
  await page.keyboard.press('ArrowUp')
  await expect(menu.getByRole('menuitem', { name: '与设定 Agent 协作', exact: true })).toBeFocused()
  await expect(page.getByRole('dialog', { name: '归档设定集', exact: true })).not.toBeAttached()
  expect(state.archiveAttempts).toBe(0)
  await menu.evaluate(element => Promise.all(element.getAnimations().map(animation => animation.finished)))
  const bounds = (await menu.boundingBox())!
  expect(bounds.x).toBeGreaterThanOrEqual(8)
  expect(bounds.y).toBeGreaterThanOrEqual(button.y + button.height - 2)
  expect(bounds.y - button.y - button.height).toBeLessThanOrEqual(16)
  expect(Math.abs(bounds.x + bounds.width - button.x - button.width)).toBeLessThanOrEqual(2)
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width - 8)
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height - 8)
  await page.keyboard.press('Escape')
  await expect(page.locator('[role="menu"]')).not.toBeAttached()
  await expect(trigger).toBeFocused()
  await trigger.click()
  await expect(menu).toBeVisible()
  const outside = page.getByRole('group', { name: '设定分类' }).getByRole('button', { name: '设定', exact: true })
  await outside.click()
  await expect(page.locator('[role="menu"]')).not.toBeAttached()
  await expect(outside).toBeFocused()
  await trigger.click()
  await expect(menu).toBeVisible()
  await page.evaluate(() => {
    const observer = new MutationObserver(() => {
      if (!document.querySelector('.setting-collaboration-panel')) return
      document.body.dataset.collaborationOpenedWithMenu = String(!!document.querySelector('[role="menu"]'))
      observer.disconnect()
    })
    observer.observe(document.body, { childList: true, subtree: true })
  })
  await menu.getByRole('menuitem', { name: '与设定 Agent 协作', exact: true }).click()
  await expect(menu).not.toBeAttached()
  const collaboration = page.getByRole('complementary', { name: '设定 Agent 协作', exact: true })
  await expect(collaboration).toBeVisible()
  await expect(collaboration.getByRole('heading', { name: '一起构建这个世界', exact: true })).toBeVisible()
  await expect(page.locator('body')).toHaveAttribute('data-collaboration-opened-with-menu', 'false')
  await collaboration.getByRole('button', { name: '关闭设定协作', exact: true }).click()
  await expect(collaboration).not.toBeAttached()
  await expect(empty).toBeVisible()
  expect(state.archiveAttempts).toBe(0)
  expect(state.unexpected).toEqual([])
})
