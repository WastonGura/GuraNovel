import { expect, test } from '@playwright/test'
import { openBookshelf } from './dashboardFixture'

for (const kind of ['添加标签', '选择设定集', '更换封面']) {
  test(`${kind} animates in and out while retaining native modality`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
    await openBookshelf(page)
    await page.locator('.novel-card').first().click()
    const outer = page.locator('dialog.novel-dialog')
    await outer.getByRole('button', { name: '编辑资料', exact: true }).click()
    if (kind === '选择设定集') await outer.getByText('更多作品信息', { exact: true }).click()
    const trigger = outer.getByRole('button', { name: kind, exact: true })
    await trigger.click()
    const picker = page.locator('dialog.picker-dialog')
    expect(await picker.evaluate(el => el.getAnimations().some(animation => animation.playState === 'running'))).toBe(true)
    await page.keyboard.press('Escape')
    expect(await picker.evaluate(el => ({ open: (el as HTMLDialogElement).open, moving: el.getAnimations().some(animation => animation.playState === 'running') }))).toEqual({ open: true, moving: true })
    await expect(picker).toHaveCount(0)
    await expect(outer).toBeVisible()
    await expect(trigger).toBeFocused()
  })
}

test('local image upload previews a server reference and automatically saves it', async ({ page }) => {
  const identity = 'a'.repeat(64)
  let saved: unknown, uploads = 0
  await page.route('**/api/v1/covers', async route => { uploads++; expect(route.request().headers()['content-type']).toBe('image/jpeg'); await route.fulfill({ status: 201, json: { cover: 'cover-upload:' + identity } }) })
  await page.route('**/api/v1/covers/' + identity + '*', route => route.fulfill({ path: 'public/ui/covers/cover-8.jpg' }))
  await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/projects/novel-0', async route => { saved = route.request().postDataJSON().metadata.cover; await route.fulfill({ status: 503, json: { detail: 'offline' } }) })
  await openBookshelf(page)
  await page.locator('.novel-card').first().click()
  const outer = page.locator('dialog.novel-dialog')
  await outer.getByRole('button', { name: '更换封面', exact: true }).click()
  const picker = page.locator('dialog.picker-dialog')
  await picker.getByLabel('上传本地封面', { exact: true }).setInputFiles('public/ui/covers/cover-8.jpg')
  await expect(picker).toHaveCount(0)
  expect(uploads).toBe(1)
  await expect(outer.locator('.novel-cover-preview img')).toHaveAttribute('src', '/api/v1/covers/' + identity)
  await expect.poll(() => saved).toBe('cover-upload:' + identity)
})

test('failed uploads and oversized images preserve the cover; reduced motion closes immediately', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  let uploads = 0
  await page.route('**/api/v1/covers', async route => { uploads++; await route.fulfill({ status: 503, json: { detail: 'offline' } }) })
  await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
  await openBookshelf(page)
  await page.locator('.novel-card').first().click()
  const outer = page.locator('dialog.novel-dialog')
  await outer.getByRole('button', { name: '更换封面', exact: true }).click()
  const picker = page.locator('dialog.picker-dialog')
  await picker.getByLabel('上传本地封面', { exact: true }).setInputFiles({ name: 'big.png', mimeType: 'image/png', buffer: Buffer.alloc(5 * 1024 * 1024 + 1) })
  await expect(picker.getByRole('alert')).toContainText('不能超过')
  expect(uploads).toBe(0)
  await picker.getByLabel('上传本地封面', { exact: true }).setInputFiles('public/ui/covers/cover-8.jpg')
  await expect(picker.getByRole('alert')).toContainText('上传失败')
  await expect(outer.locator('.novel-cover-preview img')).toHaveAttribute('src', '/ui/covers/cover-1.jpg')
  expect(await picker.evaluate(el => el.getAnimations().length)).toBe(0)
  await page.keyboard.press('Escape')
  expect(await picker.count()).toBe(0)
})

test('closing during an upload aborts it and ignores a late response', async ({ page }) => {
  let release!: () => void
  const blocked = new Promise<void>(resolve => { release = resolve })
  let started = false, finished = false, writes = 0
  await page.route('**/api/v1/covers', async route => {
    started = true
    await blocked
    await route.fulfill({ status: 201, json: { cover: 'cover-upload:' + 'b'.repeat(64) } }).catch(() => {})
    finished = true
  })
  await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/projects/novel-0', async route => { writes++; await route.fulfill({ status: 503, json: { detail: 'offline' } }) })
  await openBookshelf(page)
  await page.locator('.novel-card').first().click()
  const outer = page.locator('dialog.novel-dialog')
  const trigger = outer.getByRole('button', { name: '更换封面', exact: true })
  await trigger.click()
  const picker = page.locator('dialog.picker-dialog')
  await picker.getByLabel('上传本地封面', { exact: true }).setInputFiles('public/ui/covers/cover-8.jpg')
  await expect.poll(() => started).toBe(true)
  await page.keyboard.press('Escape')
  release()
  await expect.poll(() => finished).toBe(true)
  await expect(picker).toHaveCount(0)
  await expect(trigger).toBeFocused()
  await expect(outer.locator('.novel-cover-preview img')).toHaveAttribute('src', '/ui/covers/cover-1.jpg')
  expect(writes).toBe(0)
})
