import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import type { DocumentVersion, SettingDocumentRestorePoint } from '../src/api/client'

const collectionId = 'a3121111-1111-4111-8111-111111111111'
const documentId = 'b3121111-1111-4111-8111-111111111111'
const versionId = (number: number) => `c3121111-1111-4111-8111-${String(number).padStart(12, '0')}`
const pointId = (number: number) => `e3121111-1111-4111-8111-${String(number).padStart(12, '0')}`
const stamp = '2026-10-22T02:00:00Z'
const prose = ['旧港只在退潮时开放。', '港口在黄昏关闭。', '灯塔在夜航时为船只引路。', '新港昼夜开放。']
type PointFixture = SettingDocumentRestorePoint & { message: string | null }
type MessageWrite = { pointId: string; message: string | null; expected_message: string | null }

async function workspace(page: Page, archived = false) {
  const version = (number: number): DocumentVersion => ({ id: versionId(number), document_id: documentId, version_number: number,
    parent_version_id: number > 1 ? versionId(number - 1) : null, source: 'user', actor_user_id: null, agent_role: null, workflow_run_id: null,
    content_hash: `hash-${number}`, byte_size: prose[number - 1].length, word_count: prose[number - 1].length,
    file_path: `versions/port-${number}.md`, change_summary: null, created_at: stamp })
  const versions = [1, 2, 3, 4].map(version)
  const points: PointFixture[] = [1, 2, 3].map(number => ({ id: pointId(number), document_id: documentId, version_id: versionId(number),
    point_number: number, summary: `港口的第 ${number} 版正文`, message: number === 3 ? '补全夜航规则' : null, created_at: stamp,
    version: versions[number - 1], matches_current: false }))
  const state = { current: versions[3], content: prose[3], reads: [] as string[], restores: [] as string[], messageWrites: [] as MessageWrite[],
    messageFailure: false, loseMessageResponse: false, activeMessageWrites: 0, maxConcurrentMessageWrites: 0, unexpected: [] as string[] }
  const gates = new Map<string, Promise<void>>()
  const messageGates = new Map<number, Promise<void>>()
  const collection = { id: collectionId, owner_id: null, title: '夜航档案', slug: 'history-notes', status: archived ? 'archived' : 'active',
    description: '历史预览和存档说明的独立验证数据', workspace_root: '/workspace/history-notes', revision: 4, metadata: {}, created_at: stamp, updated_at: stamp }
  const document = () => ({ id: documentId, project_id: null, chapter_id: null, setting_collection_id: collectionId, type: 'world_overview', title: '港口',
    path: 'world/port.md', current_version_id: state.current.id, current_version: state.current, metadata: { category: 'world' }, created_at: stamp, updated_at: stamp })
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), pathname = new URL(request.url()).pathname
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (pathname === `/api/v1/setting-collections/${collectionId}`) return json(collection)
    if (pathname === `/api/v1/setting-collections/${collectionId}/projects`) return json([])
    if (pathname === `/api/v1/setting-collections/${collectionId}/documents`) return json([document()])
    if (pathname === `/api/v1/documents/${documentId}`) return json(document())
    if (pathname === `/api/v1/documents/${documentId}/content`) return json({ document_id: documentId, version_id: state.current.id, content: state.content })
    if (pathname === `/api/v1/documents/${documentId}/restore-points`) { expect(request.method()).toBe('GET'); return json(points) }
    const read = pathname.match(new RegExp(`^/api/v1/documents/${documentId}/versions/([^/]+)/content$`))
    if (read) {
      state.reads.push(read[1])
      const gate = gates.get(read[1]); if (gate) await gate
      const index = versions.findIndex(item => item.id === read[1])
      return index < 0 ? json({}, 404) : json({ document_id: documentId, version_id: read[1], content: prose[index] })
    }
    const message = pathname.match(new RegExp(`^/api/v1/documents/${documentId}/restore-points/([^/]+)/message$`))
    if (message) {
      expect(request.method()).toBe('PATCH')
      const payload = request.postDataJSON() as Omit<MessageWrite, 'pointId'>
      expect(Object.keys(payload).sort()).toEqual(['expected_message', 'message'])
      state.messageWrites.push({ pointId: message[1], ...payload })
      state.activeMessageWrites++
      state.maxConcurrentMessageWrites = Math.max(state.maxConcurrentMessageWrites, state.activeMessageWrites)
      try {
        const gate = messageGates.get(state.messageWrites.length); if (gate) await gate
        if (state.messageFailure) return await route.abort('connectionfailed')
        const point = points.find(item => item.id === message[1])!
        const desired = payload.message?.trim() || null
        if (archived) return await json({ error: { code: 'conflict', message: '设定集已归档。' } }, 409)
        if (point.message === desired) return await json(point)
        if (point.message !== payload.expected_message) return await json({ error: { code: 'conflict', message: '存档说明已更新。' } }, 409)
        point.message = desired
        if (state.loseMessageResponse) { state.loseMessageResponse = false; return await route.abort('connectionfailed') }
        return await json(point)
      } finally { state.activeMessageWrites-- }
    }
    const restore = pathname.match(new RegExp(`^/api/v1/documents/${documentId}/restore-points/([^/]+)/restore$`))
    if (restore) {
      expect(request.method()).toBe('POST')
      expect(archived).toBe(false)
      expect(request.postDataJSON().expected_current_version_id).toBe(state.current.id)
      const point = points.find(item => item.id === restore[1])!
      state.restores.push(point.id)
      const restored = { ...version(4), id: versionId(5), version_number: 5, parent_version_id: state.current.id,
        restored_from_version_id: point.version_id, restored_from_restore_point_id: point.id }
      state.current = restored; state.content = prose[point.point_number - 1]
      return json(restored)
    }
    if (pathname === '/api/v1/projects' || pathname === '/api/v1/setting-collections') return json([])
    state.unexpected.push(`${request.method()} ${pathname}`)
    return json({ error: { code: 'not_found', message: '未定义的历史说明验证接口。' } }, 404)
  })
  await page.goto(`/setting-collections/${collectionId}?note=${documentId}&category=world&mode=note`)
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText(prose[3])
  return { state, points, holdRead(number: number) {
    let release!: () => void
    gates.set(versionId(number), new Promise(resolve => { release = resolve }))
    return () => { gates.delete(versionId(number)); release() }
  }, holdMessage(number: number) {
    let release!: () => void
    messageGates.set(number, new Promise(resolve => { release = resolve }))
    return () => { messageGates.delete(number); release() }
  } }
}

async function openHistory(page: Page) {
  await page.getByRole('button', { name: '历史存档', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '历史存档', exact: true })
  await expect(dialog.getByRole('navigation', { name: '手动存档列表', exact: true })).toHaveAttribute('aria-busy', 'false')
  return dialog
}

async function saveEvidence(page: Page, filename: string) {
  if (process.env.SETTING_HISTORY_NOTES_EVIDENCE_DIR) await page.screenshot({ path: path.join(process.env.SETTING_HISTORY_NOTES_EVIDENCE_DIR, filename), animations: 'disabled' })
}

test('hover previews ignore late responses, reuse cached versions and freeze the actual restore target during confirmation', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  const { state, holdRead } = await workspace(page)
  const releaseFirst = holdRead(1)
  const dialog = await openHistory(page)
  const detail = dialog.locator('.setting-history-detail')
  const preview = dialog.getByRole('region', { name: '存档正文', exact: true })
  const point = (number: number) => dialog.getByRole('button', { name: `存档 ${number}`, exact: true })
  await expect(detail).toHaveAttribute('data-preview-point', pointId(3))
  await expect(preview).toHaveText(prose[2])
  await point(1).hover()
  await expect.poll(() => state.reads.filter(id => id === versionId(1)).length).toBe(1)
  await expect(detail).toHaveAttribute('data-preview-state', 'loading')
  await expect(detail).toHaveAttribute('data-preview-point', pointId(3))
  await expect(preview).toHaveText(prose[2])
  await dialog.evaluate((element, target) => {
    const observer = new MutationObserver(() => {
      const detail = element.querySelector('.setting-history-detail')
      if (detail?.getAttribute('data-preview-point') !== target) return
      const surface = detail.querySelector('.setting-history-preview-surface')
      if (surface?.getAnimations().length) { element.setAttribute('data-preview-animation-observed', 'true'); observer.disconnect() }
    })
    observer.observe(element, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-preview-point'] })
  }, pointId(2))
  await point(2).hover()
  await expect(detail).toHaveAttribute('data-preview-point', pointId(2))
  await expect(preview).toHaveText(prose[1])
  await expect(dialog).toHaveAttribute('data-preview-animation-observed', 'true')
  const late = page.waitForResponse(response => response.url().includes(`/versions/${versionId(1)}/content`))
  releaseFirst()
  await (await late).finished()
  await page.evaluate(() => new Promise(requestAnimationFrame))
  await expect(detail).toHaveAttribute('data-preview-point', pointId(2))
  await expect(preview).toHaveText(prose[1])
  await point(1).focus()
  await expect(preview).toHaveText(prose[0])
  for (const number of [3, 1, 2, 1, 2]) await point(number).hover()
  await expect(detail).toHaveAttribute('data-preview-point', pointId(2))
  await expect(preview).toHaveText(prose[1])
  for (const number of [1, 2, 3]) expect(state.reads.filter(id => id === versionId(number))).toHaveLength(1)
  await saveEvidence(page, 'hover-cached-preview.png')
  await dialog.getByRole('button', { name: '恢复此存档', exact: true }).click()
  await point(1).hover(); await point(3).focus()
  await expect(detail).toHaveAttribute('data-preview-point', pointId(2))
  await expect(detail).toHaveAccessibleName('确认恢复存档')
  await dialog.getByRole('button', { name: '确认恢复', exact: true }).click()
  await expect(dialog).not.toBeAttached()
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText(prose[1])
  expect(state.restores).toEqual([pointId(2)])
  expect(state.unexpected).toEqual([])
})

test('typing updates the list immediately and serializes autosaves without allowing an older acknowledgement to replace a newer draft', async ({ page }) => {
  const { state, points, holdMessage } = await workspace(page)
  let dialog = await openHistory(page)
  const first = dialog.getByRole('button', { name: '存档 1', exact: true })
  await first.click()
  let input = dialog.getByRole('textbox', { name: '存档说明', exact: true })
  await expect(input).toHaveValue('')
  await expect(dialog.getByRole('button', { name: /^(添加说明|编辑说明|保存说明|取消)$/ })).toHaveCount(0)
  await page.clock.install({ time: new Date('2026-10-22T02:00:00Z') })
  await page.clock.pauseAt(new Date('2026-10-22T02:00:01Z'))
  const releaseFirst = holdMessage(1), releaseRetry = holdMessage(2)
  const initial = '明确旧港的潮汐规则', latest = '继续补全最新的夜航规则'
  state.loseMessageResponse = true
  await input.fill(initial)
  await expect(first).toContainText(initial)
  expect(state.messageWrites).toHaveLength(0)
  await page.clock.runFor(399)
  expect(state.messageWrites).toHaveLength(0)
  await page.clock.runFor(1)
  await expect.poll(() => state.messageWrites.length).toBe(1)
  await expect(input).toBeEnabled()
  await expect(input).toHaveAttribute('aria-busy', 'true')
  await input.fill(latest)
  await expect(first).toContainText(latest)
  await page.clock.runFor(500)
  expect(state.messageWrites).toHaveLength(1)
  await expect(input).toHaveValue(latest)
  await dialog.getByRole('button', { name: '存档 2', exact: true }).click()
  await expect(input).toHaveValue('')
  await dialog.getByRole('button', { name: '存档 1', exact: true }).click()
  await expect(input).toHaveValue(latest)
  expect(state.messageWrites).toHaveLength(1)
  releaseFirst()
  await expect(dialog.getByRole('button', { name: '重试同步', exact: true })).toBeVisible()
  expect(points[0].message).toBe(initial)
  await expect(input).toHaveValue(latest)
  await expect(first).toContainText(latest)
  await dialog.getByRole('button', { name: '重试同步', exact: true }).click()
  await expect.poll(() => state.messageWrites.length).toBe(2)
  expect(state.messageWrites[1]).toEqual(state.messageWrites[0])
  await expect(input).toHaveValue(latest)
  await dialog.evaluate((element, expected) => {
    const root = element as HTMLElement
    root.dataset.draftSampleCount = '0'
    const sample = () => {
      if (!root.isConnected || root.dataset.stopSampling) return
      const input = root.querySelector<HTMLInputElement>('input[aria-label="存档说明"]')
      if (input) {
        root.dataset.draftSampleCount = String(Number(root.dataset.draftSampleCount) + 1)
        if (input.value !== expected) root.dataset.sawDraftRollback = 'true'
      }
      requestAnimationFrame(sample)
    }
    requestAnimationFrame(sample)
  }, latest)
  releaseRetry()
  await page.clock.resume()
  await expect.poll(() => points[0].message).toBe(latest)
  await expect.poll(() => state.messageWrites.length).toBe(3)
  await expect(input).toHaveValue(latest)
  await expect(first).toContainText(latest)
  await expect.poll(async () => Number(await dialog.getAttribute('data-draft-sample-count'))).toBeGreaterThan(0)
  await dialog.evaluate(element => { (element as HTMLElement).dataset.stopSampling = 'true' })
  await expect(dialog).not.toHaveAttribute('data-saw-draft-rollback', 'true')
  expect(state.messageWrites[2]).toEqual({ pointId: pointId(1), message: latest, expected_message: initial })
  expect(state.maxConcurrentMessageWrites).toBe(1)
  await saveEvidence(page, 'autosync-newer-draft.png')

  const third = dialog.getByRole('button', { name: '存档 3', exact: true })
  await third.click()
  input = dialog.getByRole('textbox', { name: '存档说明', exact: true })
  await expect(input).toHaveValue('补全夜航规则')
  const thirdMessage = '补充灯塔与远海的联系'
  await input.fill(thirdMessage)
  await expect(third).toContainText(thirdMessage)
  await input.press('Tab')
  await expect.poll(() => points[2].message).toBe(thirdMessage)
  expect(state.messageWrites[3]).toEqual({ pointId: pointId(3), message: thirdMessage, expected_message: '补全夜航规则' })
  await page.reload()
  dialog = await openHistory(page)
  await dialog.getByRole('button', { name: '存档 1', exact: true }).click()
  input = dialog.getByRole('textbox', { name: '存档说明', exact: true })
  await expect(input).toHaveValue(latest)
  await dialog.getByRole('button', { name: '存档 3', exact: true }).click()
  await expect(input).toHaveValue(thirdMessage)
  const count = state.messageWrites.length
  await input.fill(thirdMessage); await input.press('Tab')
  expect(state.messageWrites).toHaveLength(count)
  await input.fill(''); await input.press('Tab')
  await expect.poll(() => points[2].message).toBeNull()
  expect(state.messageWrites.at(-1)).toEqual({ pointId: pointId(3), message: null, expected_message: thirdMessage })
  expect(state.current.id).toBe(versionId(4))
  expect(state.content).toBe(prose[3])
  expect(state.unexpected).toEqual([])
})

test('a failed autosave keeps the input and list draft across closing and reopening history until retry succeeds', async ({ page }) => {
  const { state, points } = await workspace(page)
  let dialog = await openHistory(page)
  await dialog.getByRole('button', { name: '存档 1', exact: true }).click()
  let input = dialog.getByRole('textbox', { name: '存档说明', exact: true })
  const message = '关闭窗口也不能丢失的说明草稿'
  state.messageFailure = true
  await input.fill(message); await input.press('Tab')
  await expect(dialog.getByRole('button', { name: '重试同步', exact: true })).toBeVisible()
  await expect(input).toHaveValue(message)
  await expect(dialog.getByRole('button', { name: '存档 1', exact: true })).toContainText(message)
  await input.focus(); await input.press('Escape')
  await expect(dialog).toBeVisible()
  await expect(input).toHaveValue(message)
  await dialog.getByRole('button', { name: '关闭历史存档', exact: true }).click()
  await expect(dialog).not.toBeAttached()
  expect(points[0].message).toBeNull()
  dialog = await openHistory(page)
  await dialog.getByRole('button', { name: '存档 1', exact: true }).click()
  input = dialog.getByRole('textbox', { name: '存档说明', exact: true })
  await expect(input).toHaveValue(message)
  await expect(dialog.getByRole('button', { name: '存档 1', exact: true })).toContainText(message)
  await expect(dialog.getByRole('button', { name: '重试同步', exact: true })).toBeVisible()
  state.messageFailure = false
  await dialog.getByRole('button', { name: '重试同步', exact: true }).click()
  await expect.poll(() => points[0].message).toBe(message)
  await expect(input).toHaveValue(message)
  expect(state.messageWrites.every(write => write.pointId === pointId(1) && write.message === message && write.expected_message === null)).toBe(true)
  expect(state.maxConcurrentMessageWrites).toBe(1)
  expect(state.unexpected).toEqual([])
})

test('concurrent message edits remain paused after reading the latest value until the author explicitly continues synchronization', async ({ page }) => {
  const { state, points } = await workspace(page)
  const dialog = await openHistory(page)
  const input = dialog.getByRole('textbox', { name: '存档说明', exact: true })
  await expect(input).toHaveValue('补全夜航规则')
  const draft = '作者希望保留的新版说明', remote = '其他作者刚补充的说明'
  await input.fill(draft)
  points[2].message = remote
  await input.press('Tab')
  await expect(dialog.getByRole('button', { name: '读取最新说明', exact: true })).toBeVisible()
  await expect(input).toHaveValue(draft)
  await expect(dialog.getByRole('button', { name: '存档 3', exact: true })).toContainText(draft)
  expect(state.messageWrites).toEqual([{ pointId: pointId(3), message: draft, expected_message: '补全夜航规则' }])
  await dialog.getByRole('button', { name: '读取最新说明', exact: true }).click()
  await expect(dialog.getByText('最新说明：' + remote, { exact: true })).toBeVisible()
  await expect(input).toHaveValue(draft)
  await expect(dialog.getByRole('button', { name: '继续同步', exact: true })).toBeVisible()
  expect(state.messageWrites).toHaveLength(1)
  expect(points[2].message).toBe(remote)
  await dialog.getByRole('button', { name: '继续同步', exact: true }).click()
  await expect.poll(() => points[2].message).toBe(draft)
  expect(state.messageWrites[1]).toEqual({ pointId: pointId(3), message: draft, expected_message: remote })
  await expect(input).toHaveValue(draft)
  expect(state.unexpected).toEqual([])
})

test('an archived collection can preview archive messages but exposes no writable note input or restore action', async ({ page }) => {
  const { state } = await workspace(page, true)
  const dialog = await openHistory(page)
  await expect(dialog.getByLabel('存档说明', { exact: true })).toHaveText('补全夜航规则')
  await expect(dialog.getByRole('textbox', { name: '存档说明', exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: '存档 1', exact: true }).focus()
  await expect(dialog.getByLabel('存档说明', { exact: true })).toHaveText('未添加说明')
  await expect(dialog.getByRole('button', { name: '恢复此存档', exact: true })).toBeDisabled()
  expect(state.messageWrites).toEqual([])
  expect(state.restores).toEqual([])
  expect(state.unexpected).toEqual([])
})

for (const viewport of [{ width: 926, height: 716 }, { width: 390, height: 844 }]) test('inline autosync and preview changes respect reduced motion within ' + viewport.width + 'x' + viewport.height, async ({ page }) => {
  await page.setViewportSize(viewport)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const { state, points } = await workspace(page)
  const dialog = await openHistory(page)
  await dialog.getByRole('button', { name: '存档 1', exact: true }).click()
  await expect(dialog.getByRole('region', { name: '存档正文', exact: true })).toHaveText(prose[0])
  await expect(dialog.locator('.setting-history-preview-surface')).toHaveCSS('animation-name', 'none')
  const input = dialog.getByRole('textbox', { name: '存档说明', exact: true })
  await input.fill('潮'.repeat(170))
  await expect(input).toHaveValue('潮'.repeat(160))
  await expect(dialog.getByRole('button', { name: '存档 1', exact: true })).toContainText('潮'.repeat(160))
  const bounds = (await dialog.boundingBox())!
  for (const control of [input, dialog.getByRole('button', { name: '关闭历史存档', exact: true }), dialog.getByRole('button', { name: '恢复此存档', exact: true })]) {
    const box = (await control.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(bounds.x)
    expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width)
    expect(box.y + box.height).toBeLessThanOrEqual(bounds.y + bounds.height)
  }
  await saveEvidence(page, 'inline-autosync-reduced-' + viewport.width + '.png')
  await input.press('Escape')
  await expect(dialog).toBeVisible()
  await expect(input).toHaveValue('潮'.repeat(160))
  await expect.poll(() => points[0].message).toBe('潮'.repeat(160))
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeAttached()
  expect(state.messageWrites).toHaveLength(1)
  expect(state.unexpected).toEqual([])
})
