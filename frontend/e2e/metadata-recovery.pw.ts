import { expect, test } from '@playwright/test'
import { openBookshelf } from './dashboardFixture'

test('metadata removes genre and chevron, and aligns the chapter value', async ({ page }) => {
  await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
  await openBookshelf(page)
  await page.locator('.novel-card').first().click()
  const dialog = page.locator('dialog.novel-dialog')
  await dialog.getByRole('button', { name: '编辑资料', exact: true }).click()
  const row = dialog.getByLabel('章节：0', { exact: true })
  const label = (await row.locator('span').boundingBox())!
  const value = (await row.locator('strong').boundingBox())!
  expect(Math.abs((label.y + label.height / 2) - (value.y + value.height / 2))).toBeLessThanOrEqual(1.1)
  await dialog.getByText('更多作品信息', { exact: true }).click()
  await expect(dialog.getByLabel('编辑题材')).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: '选择设定集' })).not.toContainText('⌄')
  await dialog.getByRole('button', { name: '选择设定集' }).click()
  const picker = page.getByRole('dialog', { name: '选择设定集', exact: true })
  await expect(picker.getByRole('button', { name: '不关联设定集' })).toHaveCount(0)
})

test('409 failures allow draft-preserving dismissal and restoration after reload', async ({ page }) => {
  await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/projects/novel-0', route => route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'Project must be bound to a setting collection.' } } }))
  await openBookshelf(page)
  await page.locator('.novel-card').first().click()
  const dialog = page.locator('dialog.novel-dialog')
  await dialog.getByRole('button', { name: '编辑资料', exact: true }).click()
  await dialog.getByLabel('编辑标题').fill('断网也能退出的修改')
  await expect(dialog.getByRole('alert')).toContainText('保存被拒绝')
  await expect(dialog.getByRole('alert')).not.toContainText('网络')
  await dialog.getByRole('button', { name: '保留修改并关闭', exact: true }).click()
  await expect(dialog).not.toBeAttached()
  await page.evaluate(() => sessionStorage.clear())
  await page.reload()
  await page.locator('.novel-card').first().click()
  await dialog.getByRole('button', { name: '编辑资料', exact: true }).click()
  await expect(dialog.getByLabel('编辑标题')).toHaveValue('断网也能退出的修改')
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeAttached()
})

test('a hanging save does not trap dismissal and a later response does not erase recovery', async ({ page }) => {
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  let writes = 0
  await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/projects/novel-0', async route => {
    writes++; await pending
    await route.fulfill({ status: 503, json: {} })
  })
  await openBookshelf(page)
  await page.locator('.novel-card').first().click()
  const dialog = page.locator('dialog.novel-dialog')
  await dialog.getByRole('button', { name: '编辑资料', exact: true }).click()
  await dialog.getByLabel('编辑标题').fill('慢请求中的修改')
  await expect.poll(() => writes).toBe(1)
  await dialog.getByRole('button', { name: '关闭作品详情', exact: true }).click()
  await expect(dialog).not.toBeAttached()
  release()
  await page.locator('.novel-card').first().click()
  await dialog.getByRole('button', { name: '编辑资料', exact: true }).click()
  await expect(dialog.getByLabel('编辑标题')).toHaveValue('慢请求中的修改')
})
