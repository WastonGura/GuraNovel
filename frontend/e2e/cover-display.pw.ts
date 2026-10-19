import { expect, test } from '@playwright/test'
import { openBookshelf } from './dashboardFixture'

test('small cards request responsive WebP assets without downloading the original JPEGs', async ({ page }) => {
  const originals: string[] = []
  page.on('request', request => { if (/\/ui\/covers\/cover-\d\.jpg/.test(request.url())) originals.push(request.url()) })
  await page.setViewportSize({ width: 1440, height: 900 })
  await openBookshelf(page)
  const image = page.locator('.recent-card').first().locator('img')
  await expect.poll(() => image.evaluate(el => (el as HTMLImageElement).currentSrc)).toContain('-320.webp')
  expect(originals).toHaveLength(0)
})

test('changing covers keeps the decoded old image until the replacement is ready', async ({ page }) => {
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  await page.route(/\/ui\/covers\/(?:responsive\/)?cover-6/, async route => { await pending; await route.fulfill({ path: 'public/ui/covers/cover-6.jpg' }) })
  await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/projects/novel-0', route => route.fulfill({ status: 503, json: {} }))
  await openBookshelf(page)
  await page.locator('.novel-card').first().click()
  const dialog = page.locator('dialog.novel-dialog')
  const image = dialog.locator('.novel-cover-preview img')
  await expect(image).toHaveAttribute('src', '/ui/covers/cover-1.jpg')
  await dialog.evaluate(async el => { await Promise.all(el.getAnimations().map(animation => animation.finished)) })
  const bounds = await dialog.locator('.novel-cover-preview').boundingBox()
  await dialog.getByRole('button', { name: '更换封面', exact: true }).click()
  await page.getByRole('dialog', { name: '更换封面', exact: true }).getByRole('button', { name: '封面 6', exact: true }).click()
  await expect(page.locator('dialog.picker-dialog')).toHaveCount(0)
  expect(await image.getAttribute('src')).toBe('/ui/covers/cover-1.jpg')
  release()
  await expect(image).toHaveAttribute('src', '/ui/covers/cover-6.jpg')
  expect(await dialog.locator('.novel-cover-preview').boundingBox()).toEqual(bounds)
})

test('wide images keep their content in detail view without changing geometry', async ({ page }) => {
  await page.route('https://images.example.test/wide.png', route => route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="200"><rect width="1600" height="200" fill="#777"/></svg>' }))
  await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/projects/novel-0', route => route.fulfill({ status: 503, json: {} }))
  await openBookshelf(page)
  await page.locator('.novel-card').first().click()
  const dialog = page.locator('dialog.novel-dialog')
  await dialog.evaluate(async el => { await Promise.all(el.getAnimations().map(animation => animation.finished)) })
  const before = await dialog.locator('.novel-cover-preview').boundingBox()
  await dialog.getByRole('button', { name: '更换封面', exact: true }).click()
  const picker = page.getByRole('dialog', { name: '更换封面', exact: true })
  await picker.getByLabel('封面图片链接').fill('https://images.example.test/wide.png')
  await picker.getByRole('button', { name: '使用图片', exact: true }).click()
  const image = dialog.locator('.novel-cover-preview img')
  await expect(image).toHaveAttribute('src', 'https://images.example.test/wide.png')
  await expect(image).toHaveCSS('object-fit', 'contain')
  expect(await dialog.locator('.novel-cover-preview').boundingBox()).toEqual(before)
})

test('failed images keep layout and retry after connectivity returns', async ({ page }) => {
  let recover = false
  await page.route(/\/ui\/covers\/(?:responsive\/)?cover-1/, route => recover ? route.fulfill({ path: 'public/ui/covers/cover-1.jpg' }) : route.fulfill({ status: 404, body: '' }))
  await openBookshelf(page)
  const root = page.locator('.recent-card').first().locator('.cover-image')
  await expect(root.getByRole('img', { name: '封面暂不可用' })).toBeVisible()
  const bounds = await root.boundingBox()
  recover = true
  await page.evaluate(() => window.dispatchEvent(new Event('online')))
  await expect(root.locator('img')).toBeVisible()
  await expect(root).toHaveAttribute('aria-busy', 'false')
  expect(await root.boundingBox()).toEqual(bounds)
})

test.describe('high density image selection', () => {
  test.use({ deviceScaleFactor: 2 })
  test('requests a suitable higher resolution rather than an original', async ({ page }) => {
    await openBookshelf(page)
    const image = page.locator('.recent-card').first().locator('img')
    await expect.poll(() => image.evaluate(el => (el as HTMLImageElement).currentSrc)).toContain('-640.webp')
    await expect(image).toHaveAttribute('fetchpriority', 'high')
  })
})

test('offscreen carousel images wait until keyboard navigation reveals them', async ({ page }) => {
  const requested: string[] = []
  page.on('request', request => { if (request.url().includes('/ui/covers/responsive/cover-8-')) requested.push(request.url()) })
  await page.setViewportSize({ width: 1440, height: 900 })
  await openBookshelf(page)
  await expect(page.locator('.cover-image[data-visible="true"][aria-busy="true"]')).toHaveCount(0)
  expect(requested).toHaveLength(0)
  await page.locator('.novel-card').nth(7).focus()
  await expect.poll(() => requested.length).toBeGreaterThan(0)
  await expect(page.locator('.novel-card').nth(7).locator('img')).toBeVisible()
})
