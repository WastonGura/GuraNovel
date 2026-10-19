import { expect, test } from '@playwright/test'
import { openBookshelf } from './dashboardFixture'

test('metadata follows the supplied icon rows, label chips and introduction hierarchy', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
  let savedLabels: unknown
  let writes = 0
  await page.route('**/api/v1/projects/novel-0', async route => {
    writes++
    savedLabels = route.request().postDataJSON().metadata.labels
    await route.fulfill({ status: 503, json: { detail: 'offline' } })
  })
  await openBookshelf(page)
  await page.locator('.novel-card').first().click()
  const dialog = page.locator('dialog.novel-dialog')
  await dialog.getByRole('button', { name: '编辑资料', exact: true }).click()
  const size = dialog.locator('img[src="/ui/studio/size.svg"]')
  const chapter = dialog.locator('img[src="/ui/studio/chapter.svg"]')
  const label = dialog.locator('img[src="/ui/studio/label.svg"]')
  await expect(size).toBeVisible()
  const sizeBox = await size.boundingBox(), chapterBox = await chapter.boundingBox(), labelBox = await label.boundingBox()
  expect(chapterBox!.y).toBeGreaterThan(sizeBox!.y + sizeBox!.height)
  expect(labelBox!.y).toBeGreaterThan(chapterBox!.y + chapterBox!.height)
  expect(chapterBox!.x).toBeCloseTo(sizeBox!.x, 0)
  const titleSize = await dialog.getByLabel('编辑标题').evaluate(el => parseFloat(getComputedStyle(el).fontSize))
  expect(titleSize).toBeGreaterThanOrEqual(28)
  const tags = dialog.getByRole('group', { name: '作品标签' })
  await tags.getByRole('button', { name: '添加标签', exact: true }).click()
  const picker = page.getByRole('dialog', { name: '添加标签', exact: true })
  const input = picker.getByRole('textbox', { name: '搜索或输入标签' })
  await expect(input).toBeFocused()
  await input.fill('都')
  await picker.getByRole('button', { name: '都市', exact: true }).click()
  await expect.poll(() => savedLabels).toEqual(['都市'])
  await page.keyboard.press('Escape')
  await expect(picker).not.toBeVisible()
  await expect(dialog).toBeVisible()
  await expect(tags.getByRole('button', { name: '添加标签', exact: true })).toBeFocused()
  await expect(tags.getByRole('button', { name: '删除标签：都市', exact: true })).toBeVisible()
  const intro = dialog.getByLabel('编辑简介')
  const introBox = await intro.boundingBox(), tagsBox = await tags.boundingBox()
  expect(introBox!.y).toBeGreaterThan(tagsBox!.y + tagsBox!.height)
  expect(introBox!.height).toBeGreaterThanOrEqual(180)
  await expect(dialog.getByRole('alert')).toBeVisible()
  const before = writes
  await dialog.getByRole('button', { name: '重试同步', exact: true }).click()
  await expect.poll(() => writes).toBe(before + 1)
})
