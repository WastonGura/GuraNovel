import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  const stamp = '2026-10-20T00:00:00Z'
  const base = { genre: null, target_platform: null, status: 'draft', workspace_root: '', created_at: stamp, updated_at: stamp }
  await page.route('**/api/v1/projects', route => route.fulfill({ json: [
    { ...base, id: 'long', slug: 'long', title: '海雾与旧街', setting_collection_id: 'world', metadata: { labels: ['悬疑', '群像', '航海', '成长'], word_count: 12800, cover: 'cover-1.jpg', introduction: '一封没有署名的来信，牵出旧港口被人遗忘的往事。'.repeat(40) } },
    { ...base, id: 'short', slug: 'short', title: '灯塔', metadata: { labels: ['治愈'], introduction: '灯塔下的一次重逢。' } },
  ] }))
  await page.route('**/api/v1/setting-collections', route => route.fulfill({ json: [
    { ...base, id: 'world', slug: 'world', title: '海港档案', description: '人物、地点与世界规则。', revision: 1, metadata: {}, owner_id: null },
  ] }))
  await page.goto('/')
  await page.getByRole('button', { name: '搜索', exact: true }).click()
})

test('novel results show tags and portrait covers; long introductions expand within the preview', async ({ page }) => {
  const dialog = page.getByRole('dialog', { name: '搜索', exact: true })
  const row = dialog.getByRole('button', { name: '海雾与旧街', exact: true })
  await expect(row).toContainText('悬疑')
  await expect(row).not.toContainText('一封没有署名')
  const preview = dialog.getByRole('region', { name: '预览' })
  await expect(preview.locator('.search-preview-art')).toHaveCount(0)
  await expect.poll(() => dialog.evaluate(el => el.getAnimations().some(animation => animation.playState === 'running'))).toBe(false)
  const cover = (await preview.locator('.search-preview-cover').boundingBox())!
  expect(cover.height / cover.width).toBeGreaterThan(1.3)
  await expect(preview).toContainText('成长')
  const text = preview.locator('.search-description-text')
  const collapsed = (await text.boundingBox())!.height
  await preview.getByRole('button', { name: '展开简介', exact: true }).click()
  expect((await text.boundingBox())!.height).toBeGreaterThan(collapsed * 2)
  await preview.getByRole('button', { name: '收起简介', exact: true }).click()
  expect((await text.boundingBox())!.height).toBeLessThanOrEqual(collapsed + 1)
  expect(await page.evaluate(() => window.scrollY)).toBe(0)
  await dialog.getByRole('searchbox').fill('治愈')
  await expect(dialog.locator('.search-choice')).toHaveCount(1)
  await expect(preview).toContainText('灯塔下的一次重逢。')
  await expect(preview.getByRole('button', { name: '展开简介' })).toHaveCount(0)
})

test('collections use a dossier icon and related works instead of an arbitrary cover', async ({ page }) => {
  const dialog = page.getByRole('dialog', { name: '搜索', exact: true })
  await dialog.getByRole('button', { name: '设定集', exact: true }).click()
  const row = dialog.getByRole('button', { name: '海港档案', exact: true })
  await expect(row.locator('img')).toHaveAttribute('src', '/ui/icons/collection.svg')
  const preview = dialog.getByRole('region', { name: '预览' })
  await expect(preview.locator('.cover-image')).toHaveCount(0)
  await expect(preview).toContainText('海港档案')
  await expect(preview).toContainText('海雾与旧街')
})

test('a short desktop window keeps portrait previews and long descriptions inside their column', async ({ page }) => {
  await page.setViewportSize({ width: 820, height: 600 })
  const dialog = page.getByRole('dialog', { name: '搜索', exact: true })
  const preview = dialog.getByRole('region', { name: '预览' })
  await expect(preview.getByRole('button', { name: '展开简介', exact: true })).toBeVisible()
  await preview.getByRole('button', { name: '展开简介', exact: true }).click()
  expect(await preview.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  await preview.getByRole('button', { name: '收起简介', exact: true }).scrollIntoViewIfNeeded()
  await expect(dialog.getByRole('searchbox')).toBeInViewport()
  await preview.getByRole('button', { name: '收起简介', exact: true }).click()
  await dialog.getByRole('searchbox').fill('灯塔')
  await expect(preview).toContainText('灯塔下的一次重逢。')
  expect(await preview.evaluate(element => element.scrollTop)).toBe(0)
  await expect(preview.getByRole('button', { name: '展开简介' })).toHaveCount(0)
  expect(await page.evaluate(() => window.scrollY)).toBe(0)
})
