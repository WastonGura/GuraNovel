import { expect, type Page } from '@playwright/test'

export async function openBookshelf(page: Page, populated = true) {
  const stamp = '2026-10-18T00:00:00Z'
  const projects = populated ? Array.from({ length: 8 }, (_, index) => ({
    id: `novel-${index}`, title: `作品 ${index + 1}：一个用于布局验证的长标题`, slug: `novel-${index}`,
    genre: null, target_platform: null, workspace_root: `workspaces/novel-${index}`, metadata: {}, status: 'active', created_at: stamp, updated_at: stamp,
  })) : []
  const collections = populated ? [{ id: 'world', title: '设定集布局样例', slug: 'world', owner_id: null, description: null, workspace_root: 'workspaces/world', metadata: {}, revision: 1, status: 'active', created_at: stamp, updated_at: stamp }] : []
  await page.route('**/api/v1/projects', route => route.fulfill({ json: projects }))
  await page.route('**/api/v1/setting-collections', route => route.fulfill({ json: collections }))
  await page.goto('/')
  await expect(page.locator('.dashboard-row .dashboard-skeleton')).toHaveCount(0)
  if (populated) await expect(page.locator('.novel-card').first()).toBeVisible()
}
