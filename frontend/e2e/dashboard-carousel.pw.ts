import { expect, test, type Page } from '@playwright/test'
import { openBookshelf } from './dashboardFixture'

async function meanBrightness(page: Page, clip: { x: number; y: number; width: number; height: number }) {
  const png = await page.screenshot({ clip })
  return page.evaluate(async encoded => {
    const image = new Image()
    image.src = `data:image/png;base64,${encoded}`
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = image.width; canvas.height = image.height
    const context = canvas.getContext('2d')!
    context.drawImage(image, 0, 0)
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
    let total = 0
    for (let index = 0; index < pixels.length; index += 4) total += (pixels[index] + pixels[index + 1] + pixels[index + 2]) / 3
    return total / (pixels.length / 4)
  }, png.toString('base64'))
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await openBookshelf(page)
  await page.locator('.recent-art img').evaluateAll(images => Promise.all(images.map(image => (image as HTMLImageElement).decode())))
})

test('hovered trailing recent card keeps its top edge inside the right fade', async ({ page }) => {
  const frame = await page.locator('.dashboard-recent .dashboard-carousel').boundingBox()
  const trailing = page.locator('.recent-card').nth(3)
  const card = await trailing.boundingBox()
  // Hover the visible part without auto-scrolling the partially shown card.
  await page.mouse.move(card!.x + 8, card!.y + 20)
  await expect(trailing).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, -2)')
  const brightness = await meanBrightness(page, { x: Math.floor(frame!.x + frame!.width - 16), y: Math.floor(card!.y) - 1, width: 14, height: 1 })
  expect(brightness).toBeGreaterThan(240)
})

test('the first recent card keeps its soft hover shadow beyond the left shelf edge', async ({ page }) => {
  const first = page.locator('.recent-card').first()
  const card = await first.boundingBox()
  await first.hover()
  await expect(first).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, -2)')
  expect(await meanBrightness(page, { x: Math.floor(card!.x) - 6, y: Math.floor(card!.y + 42), width: 4, height: 24 })).toBeLessThan(254)
})

test('paging recent cards leaves no old cover pixels outside the left shelf boundary', async ({ page }) => {
  const row = page.locator('.recent-row')
  const frame = await page.locator('.dashboard-recent .dashboard-carousel').boundingBox()
  const card = await page.locator('.recent-card').first().boundingBox()
  await page.getByRole('button', { name: '查看更多最近作品', exact: true }).click()
  await expect.poll(() => row.evaluate(element => element.scrollLeft)).toBeGreaterThan(0)
  const target = await row.evaluate(element => Math.min(295, element.scrollWidth - element.clientWidth))
  await expect.poll(() => row.evaluate(element => element.scrollLeft)).toBeCloseTo(target, 0)
  await expect(page.getByRole('button', { name: '查看更多最近作品', exact: true })).toBeDisabled()
  await page.locator('.dashboard-greeting').hover()
  const geometry = await row.evaluate(element => ({ scroll: element.scrollLeft, edge: element.parentElement!.getBoundingClientRect().left,
    cards: [...element.children].map(child => ({ right: child.getBoundingClientRect().right, clip: getComputedStyle(child).clipPath })) }))
  expect(await meanBrightness(page, { x: Math.floor(frame!.x) - 14, y: Math.floor(card!.y + 30), width: 12, height: 24 }), JSON.stringify(geometry)).toBeGreaterThan(254)
  await page.getByRole('button', { name: '查看更早作品', exact: true }).click()
  await expect.poll(() => row.evaluate(element => element.scrollLeft)).toBe(0)
  await page.locator('.recent-card').first().hover()
  expect(await meanBrightness(page, { x: Math.floor(card!.x) - 6, y: Math.floor(card!.y + 42), width: 4, height: 24 })).toBeLessThan(254)
})

test.describe('ordinary motion at fractional device scale', () => {
  test.use({ deviceScaleFactor: 1.25 })
  test('paging and resizing preserve the clean edge and the returned hover shadow', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    const row = page.locator('.recent-row')
    const next = page.getByRole('button', { name: '查看更多最近作品', exact: true })
    const checkCleanEdge = async () => {
      const frame = await page.locator('.dashboard-recent .dashboard-carousel').boundingBox()
      const card = await page.locator('.recent-card').first().boundingBox()
      expect(await meanBrightness(page, { x: Math.floor(frame!.x) - 14, y: Math.floor(card!.y + 30), width: 12, height: 24 })).toBeGreaterThan(254)
    }
    await next.click()
    const target = await row.evaluate(element => Math.min(295, element.scrollWidth - element.clientWidth))
    await expect.poll(() => row.evaluate(element => element.scrollLeft)).toBeCloseTo(target, 0)
    await page.locator('.dashboard-greeting').hover()
    await checkCleanEdge()
    await page.setViewportSize({ width: 1440, height: 900 })
    await checkCleanEdge()
    await page.getByRole('button', { name: '查看更早作品', exact: true }).click()
    await expect.poll(() => row.evaluate(element => element.scrollLeft)).toBe(0)
    const first = page.locator('.recent-card').first()
    const card = await first.boundingBox()
    await first.hover()
    await expect(first).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, -2)')
    expect(await meanBrightness(page, { x: Math.floor(card!.x) - 6, y: Math.floor(card!.y + 42), width: 4, height: 24 })).toBeLessThan(254)
    await next.click()
    await expect.poll(() => row.evaluate(element => element.scrollLeft)).toBeCloseTo(target, 0)
    await page.locator('.dashboard-greeting').hover()
    await checkCleanEdge()
  })
})
