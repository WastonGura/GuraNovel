/// <reference lib="dom" />
/// <reference types="vite/client" />
import { test, expect, type Route } from '@playwright/test'

const collectionId = 'c1111111-1111-4111-8111-111111111111'
const projectId = 'p1111111-1111-4111-8111-111111111111'
const docId = 'd1111111-1111-4111-8111-111111111111'
const versionId = 'v1111111-1111-4111-8111-111111111111'

const mockCollection = {
  id: collectionId,
  owner_id: null,
  title: '测试设定集',
  slug: 'test-settings',
  description: '用于端到端测试的设定集',
  status: 'active',
  workspace_root: '/workspace/settings',
  revision: 1,
  metadata: {},
  created_at: '2026-09-18T00:00:00Z',
  updated_at: '2026-09-18T00:00:00Z',
}

const mockProjects = [
  {
    id: projectId,
    slug: 'novel-1',
    title: '关联测试小说',
    genre: 'fantasy',
    target_platform: 'web',
    status: 'active',
    workspace_root: '/workspace/novel-1',
    setting_collection_id: collectionId,
    metadata: {},
    created_at: '2026-09-18T00:00:00Z',
    updated_at: '2026-09-18T00:00:00Z',
  },
]

const mockDocs = [
  {
    id: docId,
    project_id: null,
    setting_collection_id: collectionId,
    chapter_id: null,
    type: 'character_profile',
    title: '海滨城',
    path: 'setting/haibin.md',
    current_version_id: versionId,
    current_version: null,
    metadata: { category: 'setting' },
    created_at: '2026-09-18T00:00:00Z',
    updated_at: '2026-09-18T00:00:00Z',
  },
]

function mockVersion(verId: string) {
  return {
    id: verId,
    document_id: docId,
    version_number: 2,
    parent_version_id: versionId,
    source: 'user',
    actor_user_id: null,
    agent_role: null,
    workflow_run_id: null,
    content_hash: 'hash123',
    byte_size: 100,
    word_count: 50,
    file_path: 'setting/haibin.md',
    change_summary: null,
    created_at: '2026-09-18T00:00:00Z',
  }
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

test.describe('Setting Collection Standalone Workspace Chromium E2E', () => {
  test('P1 Layout: verifies body width > 300px, no text squishing, and zero whole-page scroll', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })

    await page.route(`**/api/v1/setting-collections/${collectionId}`, route => json(route, mockCollection))
    await page.route(`**/api/v1/setting-collections/${collectionId}/projects`, route => json(route, mockProjects))
    await page.route(`**/api/v1/setting-collections/${collectionId}/documents`, route => json(route, mockDocs))
    await page.route(`**/api/v1/documents/${docId}/content`, route => json(route, {
      document_id: docId,
      version_id: versionId,
      content: '海滨城是一座面朝大海的古老港口城市。城中分为上城区与下城区。',
    }))

    await page.goto(`/setting-collections/${collectionId}`)

    // Wait for document to render
    const heading = page.locator('h1', { hasText: '海滨城' })
    await expect(heading).toBeVisible()

    // 1. Verify widths are not collapsed to 0px
    const bodyBox = await page.locator('.setting-workspace-body').boundingBox()
    const mainBox = await page.locator('.setting-main').boundingBox()
    const scrollBox = await page.locator('.setting-document-scroll').boundingBox()

    expect(bodyBox).not.toBeNull()
    expect(bodyBox!.width).toBeGreaterThan(500)
    expect(mainBox).not.toBeNull()
    expect(mainBox!.width).toBeGreaterThan(400)
    expect(scrollBox).not.toBeNull()
    expect(scrollBox!.width).toBeGreaterThan(400)

    // Heading and text width must not be squished vertically into 0px
    const headingBox = await heading.boundingBox()
    expect(headingBox).not.toBeNull()
    expect(headingBox!.width).toBeGreaterThan(200)

    // 2. Verify zero whole-page scroll
    const overflow = await page.evaluate(() => ({
      scrollHeight: document.documentElement.scrollHeight,
      clientHeight: document.documentElement.clientHeight,
      windowScrollY: window.scrollY,
    }))
    expect(overflow.scrollHeight).toBeLessThanOrEqual(overflow.clientHeight + 1)
    expect(overflow.windowScrollY).toBe(0)
  })

  test('P1 Read Defense: handles document read failure with error banner and retry, never writes empty doc', async ({ page }) => {
    let failContent = true
    let putCalled = false

    await page.route(`**/api/v1/setting-collections/${collectionId}`, route => json(route, mockCollection))
    await page.route(`**/api/v1/setting-collections/${collectionId}/projects`, route => json(route, mockProjects))
    await page.route(`**/api/v1/setting-collections/${collectionId}/documents`, route => json(route, mockDocs))
    await page.route(`**/api/v1/documents/${docId}/content`, route => {
      if (route.request().method() === 'PUT') {
        putCalled = true
        return json(route, mockVersion('v2222222-2222-4222-8222-222222222222'))
      }
      if (failContent) {
        return route.fulfill({ status: 500, body: 'Internal Server Error' })
      }
      return json(route, {
        document_id: docId,
        version_id: versionId,
        content: '重试成功恢复的正文内容。',
      })
    })

    await page.goto(`/setting-collections/${collectionId}`)

    // Verify error banner is displayed
    const errorAlert = page.locator('.setting-workspace-page[role="alert"]')
    await expect(errorAlert).toBeVisible()
    await expect(errorAlert).toContainText('设定集加载失败')

    const retryBtn = page.getByRole('button', { name: '重试加载' })
    await expect(retryBtn).toBeVisible()

    // Verify empty doc was not written to server
    expect(putCalled).toBe(false)

    // Now allow content to succeed and retry
    failContent = false
    await retryBtn.click()

    // Error banner disappears and content renders
    await expect(errorAlert).not.toBeVisible()
    await expect(page.getByText('重试成功恢复的正文内容。')).toBeVisible()
  })

  test('P1 Draft Recovery & 409 Conflict: preserves draft in sessionStorage and restores on reload', async ({ page }) => {
    let returnConflict = false

    await page.route(`**/api/v1/setting-collections/${collectionId}`, route => json(route, mockCollection))
    await page.route(`**/api/v1/setting-collections/${collectionId}/projects`, route => json(route, mockProjects))
    await page.route(`**/api/v1/setting-collections/${collectionId}/documents`, route => json(route, mockDocs))
    await page.route(`**/api/v1/documents/${docId}/content`, route => {
      if (route.request().method() === 'PUT') {
        if (returnConflict) {
          return route.fulfill({
            status: 409,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'OCC conflict: expected version mismatch' }),
          })
        }
        return json(route, mockVersion('v2222222-2222-4222-8222-222222222222'))
      }
      return json(route, {
        document_id: docId,
        version_id: versionId,
        content: '初始设定正文。',
      })
    })

    await page.goto(`/setting-collections/${collectionId}`)
    await expect(page.getByText('初始设定正文。')).toBeVisible()

    // Enter edit mode
    await page.getByRole('button', { name: '编辑' }).click()
    const textarea = page.getByLabel('设定正文')
    await expect(textarea).toBeVisible()

    // Type new text
    returnConflict = true
    await textarea.fill('用户正在编辑的重要未保存设定。')

    // Verify draft saved into sessionStorage immediately
    const draftInStorage = await page.evaluate((id) => sessionStorage.getItem(`guranovel:setting-draft-recovery:${id}`), docId)
    expect(draftInStorage).toBe('用户正在编辑的重要未保存设定。')

    // Wait for debounce save queue to attempt saving and encounter 409 conflict
    const conflictBanner = page.locator('.setting-conflict-banner')
    await expect(conflictBanner).toBeVisible({ timeout: 5000 })
    await expect(conflictBanner).toContainText('版本冲突')

    // Reload the page: draft must be recovered from sessionStorage
    await page.reload()
    await expect(page.locator('h1', { hasText: '海滨城' })).toBeVisible()

    // Verify the recovered text is displayed on screen
    await expect(page.getByText('用户正在编辑的重要未保存设定。')).toBeVisible()
  })

  test('P1 Proposal Loop: accepting proposal applies changes and blocks on conflict', async ({ page }) => {
    let proposalApplied = false
    let returnProposalConflict = false

    await page.route(`**/api/v1/setting-collections/${collectionId}`, route => json(route, mockCollection))
    await page.route(`**/api/v1/setting-collections/${collectionId}/projects`, route => json(route, mockProjects))
    await page.route(`**/api/v1/setting-collections/${collectionId}/documents`, route => json(route, mockDocs))
    await page.route(`**/api/v1/documents/${docId}/content`, route => json(route, {
      document_id: docId,
      version_id: versionId,
      content: '海滨城港口。',
    }))

    await page.route(`**/api/v1/setting-collections/${collectionId}/proposals/apply`, route => {
      if (returnProposalConflict) {
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Proposal conflict: base version outdated' }),
        })
      }
      proposalApplied = true
      return json(route, {
        id: docId,
        project_id: null,
        setting_collection_id: collectionId,
        chapter_id: null,
        type: 'character_profile',
        title: '海滨城',
        path: 'setting/haibin.md',
        current_version_id: 'v-applied-3333',
        current_version: null,
        metadata: { category: 'setting' },
        created_at: '2026-09-18T00:00:00Z',
        updated_at: '2026-09-18T00:00:00Z',
      })
    })

    // Open workspace in chat mode
    await page.goto(`/setting-collections/${collectionId}?mode=chat`)

    // Trigger example reply to generate a change proposal
    const exampleBtn = page.locator('button.setting-example')
    await expect(exampleBtn).toBeVisible()
    await exampleBtn.click()

    // Verify pending change batch heading appears
    await expect(page.locator('.setting-batch-heading')).toBeVisible()

    // Expand change card to see details
    const changeCard = page.locator('.setting-change').first()
    await expect(changeCard).toBeVisible()

    // Test conflict first
    returnProposalConflict = true
    const acceptBtn = changeCard.getByRole('button', { name: '接受此条' })
    await acceptBtn.click()

    // Verify conflict banner appears
    await expect(page.locator('.setting-conflict-banner')).toBeVisible()
    expect(proposalApplied).toBe(false)

    // Now allow proposal to succeed
    returnProposalConflict = false
    await acceptBtn.click()

    // Verify apply endpoint was called and card changes to accepted
    await expect(page.locator('.setting-change.is-accepted')).toBeVisible()
    expect(proposalApplied).toBe(true)
  })
})
