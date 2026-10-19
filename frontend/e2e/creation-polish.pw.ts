import { expect, test } from '@playwright/test'

test('new works start with a gray cover and hide redundant author controls', async ({ page }) => {
  const covers: string[] = []
  page.on('request', request => { if (request.url().includes('/ui/covers/')) covers.push(request.url()) })
  await page.route('**/api/v1/projects', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/setting-collections', route => route.fulfill({ json: [] }))
  await page.goto('/')
  await page.getByRole('button', { name: '新建作品', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '创建作品', exact: true })
  await expect(dialog.getByRole('button', { name: '取消', exact: true })).toHaveCount(0)
  await expect(dialog.getByLabel('Slug', { exact: true })).toHaveCount(0)
  const cover = dialog.locator('.novel-cover-preview .cover-image')
  await expect(cover).toHaveCSS('background-color', 'rgb(228, 228, 228)')
  await expect(cover.locator('img')).toHaveCount(0)
  expect(covers).toHaveLength(0)
  await expect(dialog.getByRole('button', { name: '创建作品', exact: true }).locator('img')).toHaveAttribute('src', '/ui/icons/book-plus.svg')
  await dialog.getByRole('button', { name: '更换封面', exact: true }).click()
  const picker = page.getByRole('dialog', { name: '更换封面', exact: true })
  await expect(picker.getByRole('button', { name: '默认封面', exact: true })).toHaveAttribute('aria-pressed', 'true')
})

test('search filter hover is black with readable white text', async ({ page }) => {
  await page.route('**/api/v1/projects', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/setting-collections', route => route.fulfill({ json: [] }))
  await page.goto('/')
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '搜索', exact: true })
  for (const title of ['全部', '作品', '设定集']) {
    const button = dialog.getByRole('button', { name: title, exact: true })
    await button.hover()
    await expect(button).toHaveCSS('background-color', 'rgb(23, 23, 23)')
    await expect(button).toHaveCSS('color', 'rgb(255, 255, 255)')
  }
})

test('uncovered works remain gray across bookshelf, details and search without affecting explicit covers', async ({ page }) => {
  const stamp = '2026-10-20T00:00:00Z'
  const base = { slug: 'sample', genre: null, target_platform: null, status: 'draft', workspace_root: '', created_at: stamp, updated_at: stamp }
  await page.route('**/api/v1/projects', route => route.fulfill({ json: [
    { ...base, id: 'gray', title: '未设置封面', metadata: {} },
    { ...base, id: 'art', title: '已有封面', metadata: { cover: 'cover-2.jpg' } },
  ] }))
  await page.route('**/api/v1/projects/gray/chapters', route => route.fulfill({ json: [] }))
  await page.route('**/api/v1/setting-collections', route => route.fulfill({ json: [] }))
  await page.goto('/')
  await expect(page.locator('.novel-card').first().getByRole('img', { name: '默认封面' })).toBeVisible()
  await expect(page.locator('.novel-card').nth(1).locator('img')).toHaveAttribute('src', '/ui/covers/cover-2.jpg')
  await page.locator('.novel-card').first().click()
  const detail = page.locator('dialog.novel-dialog')
  await expect(detail.getByRole('img', { name: '默认封面' })).toBeVisible()
  await detail.getByRole('button', { name: '编辑资料', exact: true }).click()
  await detail.getByText('更多作品信息', { exact: true }).click()
  await expect(detail.locator('.novel-collection-open')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(detail).not.toBeAttached()
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  const search = page.getByRole('dialog', { name: '搜索', exact: true })
  await expect(search.getByRole('region', { name: '预览' }).getByRole('img', { name: '默认封面' })).toBeVisible()
})
