import { expect, test, type Locator, type Page } from '@playwright/test'
import { openBookshelf } from './dashboardFixture'

async function frame(image: Locator) {
  return image.evaluate(el => {
    const box = el.getBoundingClientRect(), style = getComputedStyle(el)
    return { ratio: box.width / box.height, fit: style.objectFit, position: style.objectPosition }
  })
}

async function comparePresets(page: Page) {
  const outer = page.locator('dialog.novel-dialog')
  for (let index = 1; index <= 8; index++) {
    await outer.getByRole('button', { name: '更换封面', exact: true }).click()
    const picker = page.getByRole('dialog', { name: '更换封面', exact: true })
    const option = picker.getByRole('button', { name: `封面 ${index}`, exact: true })
    const thumb = option.locator('img')
    await expect(thumb).toBeVisible()
    const preview = await frame(thumb)
    const coverBox = await outer.locator('.novel-cover-preview').boundingBox()
    expect(Math.abs(preview.ratio - coverBox!.width / coverBox!.height)).toBeLessThan(.005)
    await option.click()
    await expect(picker).not.toBeAttached()
    const final = outer.locator('.novel-cover-preview img')
    await expect(final).toHaveAttribute('src', `/ui/covers/cover-${index}.jpg`)
    const displayed = await frame(final)
    expect(displayed.fit).toBe(preview.fit)
    expect(displayed.position, `cover ${index}: ${JSON.stringify({ preview, displayed })}`).toBe(preview.position)
    expect(Math.abs(displayed.ratio - preview.ratio)).toBeLessThan(.005)
  }
}

for (const viewport of [{ width: 1440, height: 900 }, { width: 1920, height: 1080 }, { width: 1100, height: 650 }, { width: 390, height: 844 }]) {
  test(`creation thumbnails match the actual cover at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.setViewportSize(viewport)
    await openBookshelf(page, false)
    await page.getByRole('button', { name: '新建作品', exact: true }).click()
    await comparePresets(page)
  })
}

test('detail thumbnails follow live resizing while the picker is open', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/projects/novel-0', route => route.fulfill({ status: 503, json: {} }))
  await openBookshelf(page)
  await page.locator('.novel-card').first().click()
  await page.locator('.novel-cover-preview').click()
  await page.setViewportSize({ width: 1000, height: 650 })
  const thumb = page.locator('.cover-picker-grid button').first().locator('img')
  await expect(thumb).toBeVisible()
  await expect.poll(async () => {
    const box = await page.locator('.novel-cover-preview').boundingBox()
    return Math.abs((await frame(thumb)).ratio - box!.width / box!.height)
  }).toBeLessThan(.005)
  await page.keyboard.press('Escape')
  await comparePresets(page)
})
