import { test, expect, type Page } from '@playwright/test'

const collection = { id: 'settings', owner_id: null, slug: 'settings', title: '星港设定集', status: 'active', description: '', workspace_root: '', revision: 1, metadata: {}, created_at: '', updated_at: '' }
const document = { id: 'doc', project_id: null, setting_collection_id: 'settings', chapter_id: null, type: 'world_overview', title: '星港', path: 'world/port.md', current_version_id: 'v1', current_version: null, metadata: { category: 'world' }, created_at: '', updated_at: '' }
const project = { id: 'novel', title: '在同一个星港发生的故事——归航之夜', slug: 'novel', genre: null, target_platform: null, status: 'active', workspace_root: '', setting_collection_id: 'settings', metadata: { labels: ['科幻', '群像'], introduction: '归航者的故事。'.repeat(150) }, created_at: '', updated_at: '' }
async function workspace(page: Page) {
  await page.route('**/api/v1/setting-collections/settings', route => route.fulfill({ json: collection }))
  await page.route('**/api/v1/setting-collections/settings/documents', route => route.fulfill({ json: [document] }))
  await page.route('**/api/v1/documents/doc/content', route => route.fulfill({ json: { document_id: 'doc', version_id: 'v1', content: '星港的旧稿。' } }))
}

test('failed association counts remain retryable and opening a novel protects a rejected title draft', async ({ page }) => {
  await workspace(page)
  let failProjects = true, rejectTitle = true
  await page.route('**/api/v1/setting-collections/settings/projects', route => failProjects ? route.fulfill({ status: 503, json: {} }) : route.fulfill({ json: [project] }))
  await page.route('**/api/v1/documents/doc', route => rejectTitle ? route.fulfill({ status: 503, json: {} }) : route.fulfill({ json: { ...document, title: '星港新名' } }))
  await page.goto('/setting-collections/settings?note=doc&category=world')
  await expect(page.getByRole('button', { name: '关联小说 · 重试' })).toBeVisible()
  failProjects = false
  await page.getByLabel('设定标题').fill('星港新名')
  await page.getByRole('button', { name: '关联小说 · 重试' }).click()
  const dialog = page.getByRole('dialog', { name: '关联作品', exact: true })
  await expect(dialog.getByRole('region', { name: '预览' })).toContainText(project.title)
  await expect(dialog.getByRole('region', { name: '预览' })).toContainText('群像')
  await dialog.getByLabel('搜索关联作品').fill('归航')
  await dialog.getByRole('button', { name: project.title, exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('未能保存')
  await expect(page).toHaveURL(/setting-collections/)
  rejectTitle = false
  await dialog.getByRole('button', { name: project.title, exact: true }).click()
  await expect(page).toHaveURL(/\?project=novel$/)
})

test('zero associations still opens an animated keyboard-dismissable empty dialog', async ({ page }) => {
  await workspace(page)
  await page.route('**/api/v1/setting-collections/settings/projects', route => route.fulfill({ json: [] }))
  await page.goto('/setting-collections/settings')
  const launcher = page.getByRole('button', { name: '关联小说 (0)', exact: true })
  await launcher.click()
  const dialog = page.getByRole('dialog', { name: '关联作品', exact: true })
  await expect(dialog).toContainText('暂无关联作品')
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeAttached()
  await expect(launcher).toBeFocused()
})
