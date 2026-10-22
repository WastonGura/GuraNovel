import { expect, test, type Locator, type Page } from '@playwright/test'
import path from 'node:path'
import type { DocumentVersion, SettingDocumentRestorePoint } from '../src/api/client'

const collectionId = 'a3071111-1111-4111-8111-111111111111'
const documentId = 'b3071111-1111-4111-8111-111111111111'
const versionId = (number: number) => `c3071111-1111-4111-8111-${String(number).padStart(12, '0')}`
const stamp = '2026-10-22T01:00:00Z'
const body = '潮汐每六小时转换一次，灯塔在退潮时为夜航船只指路。'

async function workspace(page: Page) {
  const state = { content: body, pointPosts: 0, unexpected: [] as string[] }
  const version = (number: number): DocumentVersion => ({
    id: versionId(number), document_id: documentId, version_number: number, parent_version_id: number > 1 ? versionId(number - 1) : null,
    source: 'user', actor_user_id: null, agent_role: null, workflow_run_id: null, content_hash: `hash-${number}`,
    byte_size: body.length, word_count: body.length, file_path: `versions/port-${number}.md`, change_summary: null, created_at: stamp,
  })
  const current = version(2)
  const points: SettingDocumentRestorePoint[] = [{
    id: 'e3071111-1111-4111-8111-111111111111', document_id: documentId, version_id: versionId(1), point_number: 1,
    summary: '最初的潮汐法则', created_at: stamp, version: version(1), matches_current: false,
  }]
  const collection = { id: collectionId, owner_id: null, title: '潮汐档案', slug: 'icon-workspace', status: 'active', description: '港口、灯塔与夜航者',
    workspace_root: '/workspace/icon-workspace', revision: 2, metadata: {}, created_at: stamp, updated_at: stamp }
  const document = { id: documentId, project_id: null, chapter_id: null, setting_collection_id: collectionId, type: 'world_overview', title: '潮汐法则',
    path: 'world/tides.md', current_version_id: current.id, current_version: current, metadata: { category: 'world' }, created_at: stamp, updated_at: stamp }
  let pointHold: Promise<void> | undefined
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()), pathname = url.pathname
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (pathname === `/api/v1/setting-collections/${collectionId}`) return json(collection)
    if (pathname === `/api/v1/setting-collections/${collectionId}/projects`) return json([])
    if (pathname === `/api/v1/setting-collections/${collectionId}/documents`) return json(url.searchParams.get('deleted') === 'true' ? [] : [document])
    if (pathname === `/api/v1/documents/${documentId}`) return json(document)
    if (pathname === `/api/v1/documents/${documentId}/content`) return json({ document_id: documentId, version_id: current.id, content: state.content })
    if (pathname === `/api/v1/documents/${documentId}/restore-points`) {
      if (request.method() === 'GET') return json(points)
      state.pointPosts++
      const payload = request.postDataJSON()
      expect(payload.expected_current_version_id).toBe(current.id)
      if (pointHold) await pointHold
      const point: SettingDocumentRestorePoint = { id: payload.request_id, document_id: documentId, version_id: current.id, point_number: points.length + 1,
        summary: null, created_at: stamp, version: current, matches_current: true }
      points.push(point)
      return json(point, 201)
    }
    const read = pathname.match(new RegExp(`^/api/v1/documents/${documentId}/versions/([^/]+)/content$`))
    if (read) return json({ document_id: documentId, version_id: read[1], content: read[1] === current.id ? body : '灯塔只在退潮时亮起。' })
    if (pathname === `/api/v1/setting-collections/${collectionId}/collaboration`) return json({
      id: 'f3071111-1111-4111-8111-111111111111', setting_collection_id: collectionId, document_id: null,
      agent_role: 'lore_agent', messages: [], created_at: stamp, updated_at: stamp,
    })
    if (pathname === '/api/v1/projects' || pathname === '/api/v1/setting-collections') return json([])
    state.unexpected.push(`${request.method()} ${pathname}`)
    return json({ error: { code: 'not_found', message: '未定义的图标验证接口' } }, 404)
  })
  await page.goto(`/setting-collections/${collectionId}?note=${documentId}&category=world&mode=note`)
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText(body)
  return { state, points, holdPoint() {
    let release!: () => void
    pointHold = new Promise(resolve => { release = resolve })
    return () => { pointHold = undefined; release() }
  } }
}

async function saveEvidence(page: Page, filename: string) {
  const directory = process.env.SETTING_UI_EVIDENCE_DIR
  if (directory) await page.screenshot({ path: path.join(directory, filename), fullPage: false })
}

async function expectIconButton(button: Locator) {
  await expect(button).toBeVisible()
  const icon = button.locator('svg')
  await expect(icon).toHaveCount(1)
  await expect(icon).toHaveAttribute('stroke-width', '1.65')
  await expect(icon).toHaveAttribute('stroke-linecap', 'round')
  await expect(icon).toHaveAttribute('stroke-linejoin', 'round')
  await expect(icon).toHaveAttribute('aria-hidden', 'true')
  const control = (await button.boundingBox())!, glyph = (await icon.boundingBox())!
  expect(control.width).toBeCloseTo(32, 0)
  expect(control.height).toBeCloseTo(32, 0)
  expect(glyph.width).toBeCloseTo(20, 0)
  expect(glyph.height).toBeCloseTo(20, 0)
  expect(Math.abs(glyph.x + glyph.width / 2 - control.x - control.width / 2)).toBeLessThanOrEqual(1)
  expect(Math.abs(glyph.y + glyph.height / 2 - control.y - control.height / 2)).toBeLessThanOrEqual(1)
}

async function settleIcon(button: Locator) {
  await button.evaluate(element => Promise.allSettled(element.getAnimations({ subtree: true })
    .filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity)
    .map(animation => animation.finished)))
}

async function expectStrokeMotion(page: Page, button: Locator, partClass: string) {
  const part = button.locator(`.${partClass}`)
  const originalBounds = (await button.boundingBox())!
  await page.mouse.move(0, 0)
  await settleIcon(button)
  const idle = await part.evaluate(element => getComputedStyle(element).transform)
  await button.hover(); await settleIcon(button)
  expect(await part.evaluate(element => getComputedStyle(element).transform)).not.toBe(idle)
  expect(await button.boundingBox()).toEqual(originalBounds)
  await page.mouse.move(0, 0); await settleIcon(button)
  await expect(part).toHaveCSS('transform', idle)
  expect(await button.boundingBox()).toEqual(originalBounds)
  await button.focus(); await button.press('Tab'); await page.keyboard.press('Shift+Tab')
  await expect(button).toBeFocused()
  await settleIcon(button)
  expect(await part.evaluate(element => getComputedStyle(element).transform)).not.toBe(idle)
  expect(await button.boundingBox()).toEqual(originalBounds)
  await page.getByRole('heading', { name: '潮汐档案', exact: true }).click()
  await settleIcon(button)
  await expect(part).toHaveCSS('transform', idle)
}

for (const viewport of [{ width: 1440, height: 900 }, { width: 926, height: 716 }]) test(`setting actions share compact icon geometry and keyboard navigation at ${viewport.width}x${viewport.height}`, async ({ page }) => {
  await page.setViewportSize(viewport)
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  const { state } = await workspace(page)
  const history = page.getByRole('button', { name: '历史存档', exact: true })
  const save = page.getByRole('button', { name: '存档当前内容', exact: true })
  const more = page.getByRole('button', { name: '设定集操作', exact: true })
  const trash = page.getByRole('button', { name: '回收站', exact: true })
  for (const button of [history, save, more, trash]) await expectIconButton(button)
  const title = (await page.getByRole('textbox', { name: '设定标题', exact: true }).boundingBox())!
  const historyBounds = (await history.boundingBox())!, saveBounds = (await save.boundingBox())!
  expect(historyBounds.x).toBeGreaterThanOrEqual(title.x + title.width)
  expect(saveBounds.x).toBeGreaterThanOrEqual(historyBounds.x + historyBounds.width)
  expect(saveBounds.x + saveBounds.width).toBeLessThanOrEqual(viewport.width - 8)
  await expect(page.getByRole('link', { name: '回到书架', exact: true }).locator('img, svg')).toHaveCount(0)
  await expectStrokeMotion(page, history, 'setting-action-icon__history-hand')
  await expectStrokeMotion(page, trash, 'setting-action-icon__trash-lid')
  await saveEvidence(page, `workspace-${viewport.width}.png`)

  await more.focus(); await page.keyboard.press('Enter')
  const menu = page.getByRole('menu', { name: '设定集操作', exact: true })
  const collaborate = menu.getByRole('menuitem', { name: '与设定 Agent 协作', exact: true })
  const archive = menu.getByRole('menuitem', { name: '归档设定集', exact: true })
  await expect(collaborate).toBeFocused()
  await settleIcon(menu)
  for (const item of [collaborate, archive]) {
    const glyph = (await item.locator('svg').boundingBox())!
    expect(glyph.width).toBeCloseTo(18, 0); expect(glyph.height).toBeCloseTo(18, 0)
  }
  await page.keyboard.press('ArrowDown'); await expect(archive).toBeFocused()
  await page.keyboard.press('ArrowUp'); await expect(collaborate).toBeFocused()
  await saveEvidence(page, `actions-menu-${viewport.width}.png`)
  await page.keyboard.press('Enter')
  await expect(menu).not.toBeAttached()
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作', exact: true })
  await expect(panel.getByRole('heading', { name: '一起构建这个世界', exact: true })).toBeVisible()
  const back = panel.getByRole('button', { name: '关闭设定协作', exact: true })
  await expect(back).toHaveText('返回文稿')
  await expect(back.locator('img, svg')).toHaveCount(0)
  await back.click(); await expect(panel).not.toBeAttached()
  await history.click()
  const dialog = page.getByRole('dialog', { name: '历史存档', exact: true })
  await expect(dialog.getByRole('region', { name: '存档正文', exact: true })).toHaveText('灯塔只在退潮时亮起。')
  await settleIcon(dialog)
  await expectIconButton(dialog.getByRole('button', { name: '关闭历史存档', exact: true }))
  await saveEvidence(page, `history-${viewport.width}.png`)
  await page.keyboard.press('Escape'); await expect(dialog).not.toBeAttached()
  await trash.click()
  const bin = page.getByRole('dialog', { name: '回收站', exact: true })
  await expect(bin.getByRole('status')).toHaveText('回收站是空的')
  await settleIcon(bin)
  await expectIconButton(bin.getByRole('button', { name: '关闭回收站', exact: true }))
  await saveEvidence(page, `trash-${viewport.width}.png`)
  await page.keyboard.press('Escape'); await expect(bin).not.toBeAttached()
  await page.getByRole('button', { name: '搜索内容', exact: true }).click()
  const search = page.getByRole('dialog', { name: '搜索内容', exact: true })
  await search.getByRole('textbox', { name: '搜索设定内容', exact: true }).fill('潮汐')
  await settleIcon(search)
  await expectIconButton(search.getByRole('button', { name: '关闭搜索内容', exact: true }))
  await expectIconButton(search.getByRole('button', { name: '清空搜索', exact: true }))
  await expect(search.getByRole('heading', { name: '潮汐法则', exact: true })).toBeVisible()
  await saveEvidence(page, `search-${viewport.width}.png`)
  await page.keyboard.press('Escape'); await expect(search).not.toBeAttached()
  await page.getByRole('button', { name: '关系图谱', exact: true }).click()
  for (const name of ['缩小图谱', '放大图谱', '图谱归位']) await expectIconButton(page.getByRole('button', { name, exact: true }))
  await page.getByRole('button', { name: '放大图谱', exact: true }).click()
  await expect(page.locator('.setting-graph-scale')).toHaveText('120%')
  await page.getByRole('button', { name: '图谱归位', exact: true }).click()
  await expect(page.locator('.setting-graph-scale')).toHaveText('100%')
  await saveEvidence(page, `graph-${viewport.width}.png`)
  expect(await page.evaluate(() => document.documentElement.scrollHeight - innerHeight)).toBe(0)
  expect(state.unexpected).toEqual([])
})

test('the save glyph follows a real pending archive request and confirms success without moving its control', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const { state, points, holdPoint } = await workspace(page)
  const save = page.getByRole('button', { name: '存档当前内容', exact: true })
  const bounds = (await save.boundingBox())!
  const release = holdPoint()
  await save.click()
  await expect.poll(() => state.pointPosts).toBe(1)
  const pending = page.getByRole('button', { name: '正在存档…', exact: true })
  await expect(pending).toBeDisabled()
  await expect(pending).toHaveAttribute('aria-busy', 'true')
  await expect(pending).toHaveAttribute('data-busy', 'true')
  await expect(pending.locator('.setting-action-icon__save-tab')).toHaveCSS('opacity', '0.65')
  await expect(page.getByRole('button', { name: '历史存档', exact: true })).toBeDisabled()
  expect(await pending.boundingBox()).toEqual(bounds)
  await saveEvidence(page, 'save-pending.png')
  release()
  await expect(save).toHaveAttribute('data-state', 'success')
  await expect(save).toBeEnabled()
  await expect(save).toHaveAccessibleDescription(/已存档/)
  await expect(page.getByText('已存档', { exact: true })).toHaveCount(0)
  await expect(save.locator('.setting-action-icon__save-check')).toHaveCSS('opacity', '1')
  await expect(save.locator('.setting-action-icon__save-check')).toHaveCSS('stroke-dashoffset', '0px')
  await expect(save.locator('.setting-action-icon__save-panel')).toHaveCSS('opacity', '0')
  expect(await save.boundingBox()).toEqual(bounds)
  expect(points).toHaveLength(2)
  expect(state.pointPosts).toBe(1)
  await saveEvidence(page, 'save-success.png')
  await save.click()
  await expect(save).toHaveAttribute('data-state', 'success')
  expect(state.pointPosts).toBe(1)
  expect(state.unexpected).toEqual([])
})

test('reduced motion keeps hover strokes static while retaining keyboard focus and save feedback', async ({ page }) => {
  await page.setViewportSize({ width: 926, height: 716 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const { state } = await workspace(page)
  const history = page.getByRole('button', { name: '历史存档', exact: true })
  const hand = history.locator('.setting-action-icon__history-hand')
  await history.hover()
  await expect(hand).toHaveCSS('transform', 'none')
  expect(await history.evaluate(element => Math.max(...getComputedStyle(element).transitionDuration.split(',').map(Number.parseFloat)))).toBeLessThanOrEqual(.001)
  expect(await hand.evaluate(element => Math.max(...getComputedStyle(element).transitionDuration.split(',').map(Number.parseFloat)))).toBeLessThanOrEqual(.001)
  await history.focus(); await history.press('Tab'); await page.keyboard.press('Shift+Tab')
  await expect(history).toBeFocused()
  await expect(hand).toHaveCSS('transform', 'none')
  await page.getByRole('button', { name: '存档当前内容', exact: true }).click()
  const save = page.getByRole('button', { name: '存档当前内容', exact: true })
  await expect(save).toHaveAttribute('data-state', 'success')
  await expect(save.locator('.setting-action-icon__save-check')).toHaveCSS('opacity', '1')
  expect(await save.locator('.setting-action-icon__save-check').evaluate(element => Math.max(...getComputedStyle(element).transitionDuration.split(',').map(Number.parseFloat)))).toBeLessThanOrEqual(.001)
  expect(state.pointPosts).toBe(1)
  expect(state.unexpected).toEqual([])
})

for (const motion of ['no-preference', 'reduce'] as const) test(`pin states remain distinct and control real sidebar persistence with ${motion} motion`, async ({ page }) => {
  await page.setViewportSize(motion === 'reduce' ? { width: 926, height: 716 } : { width: 1440, height: 900 })
  await page.emulateMedia({ reducedMotion: motion })
  const { state } = await workspace(page)
  const sidebar = page.locator('#setting-library')
  const pin = sidebar.getByRole('button', { name: /固定设定侧边栏$/ })
  const shape = pin.locator('.setting-action-icon__pin')
  const trigger = page.getByRole('button', { name: '展开设定侧边栏', exact: true })
  const angle = () => shape.evaluate(element => {
    const transform = getComputedStyle(element).transform
    const matrix = transform === 'none' ? new DOMMatrixReadOnly() : new DOMMatrixReadOnly(transform)
    return Math.atan2(matrix.b, matrix.a) * 180 / Math.PI
  })
  await page.mouse.move(0, 0)
  await expect(pin).toHaveAttribute('aria-pressed', 'true')
  await expectIconButton(pin)
  await settleIcon(sidebar)
  const bounds = (await pin.boundingBox())!
  expect(await angle()).toBeCloseTo(0, 1)
  await saveEvidence(page, `pin-${motion}-fixed.png`)

  await pin.click()
  await expect(pin).toHaveAttribute('aria-pressed', 'false')
  await page.getByRole('heading', { name: '潮汐档案', exact: true }).click()
  await expect(trigger).toHaveAttribute('aria-expanded', 'false')
  await expect(sidebar).toHaveAttribute('inert', '')
  await expect(sidebar).toBeHidden()
  await saveEvidence(page, `pin-${motion}-collapsed.png`)
  await trigger.hover()
  await expect(trigger).toHaveAttribute('aria-expanded', 'true')
  await expect(sidebar).toBeVisible()
  await settleIcon(sidebar)
  const sideBounds = (await sidebar.boundingBox())!
  await page.mouse.move(sideBounds.x + sideBounds.width / 2, sideBounds.y + 250)
  await settleIcon(pin)
  expect(await angle()).toBeCloseTo(28, 1)
  await expectIconButton(pin)
  expect(await pin.boundingBox()).toEqual(bounds)
  if (motion === 'reduce') expect(await shape.evaluate(element => Math.max(...getComputedStyle(element).transitionDuration.split(',').map(Number.parseFloat)))).toBeLessThanOrEqual(.001)
  await saveEvidence(page, `pin-${motion}-unfixed.png`)

  await pin.click()
  await expect(pin).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('heading', { name: '潮汐档案', exact: true }).click()
  await settleIcon(sidebar)
  await expect(trigger).toHaveAttribute('aria-expanded', 'true')
  await expect(sidebar).toBeVisible()
  expect(await angle()).toBeCloseTo(0, 1)
  expect(await pin.boundingBox()).toEqual(bounds)
  if (motion === 'reduce') expect(await shape.evaluate(element => Math.max(...getComputedStyle(element).transitionDuration.split(',').map(Number.parseFloat)))).toBeLessThanOrEqual(.001)
  expect(state.unexpected).toEqual([])
})
