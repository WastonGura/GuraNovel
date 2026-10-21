import { expect, test, type Page } from '@playwright/test'

const collectionId = '11111111-1111-4111-8111-111111111111'
const docId = '22222222-2222-4222-8222-222222222222'
const versionId = '33333333-3333-4333-8333-333333333333'
const stamp = '2026-10-20T00:00:00Z'
const conversation = { id: 'conversation', project_id: null, setting_collection_id: collectionId, messages: [] as { id: string; role: string; content: string; tool_calls?: object[] }[], created_at: stamp, updated_at: stamp }

async function workspace(page: Page, documentCount = 1) {
  await page.route(`**/api/v1/setting-collections/${collectionId}`, r => r.fulfill({ json: { id: collectionId, owner_id: null, title: '雾港世界 · 长标题布局验证', slug: 'world', workspace_root: '/tmp/world', description: null, metadata: {}, revision: 1, status: 'active', created_at: stamp, updated_at: stamp } }))
  await page.route(`**/api/v1/setting-collections/${collectionId}/projects`, r => r.fulfill({ json: [] }))
  await page.route(`**/api/v1/setting-collections/${collectionId}/documents`, r => r.fulfill({ json: Array.from({ length: documentCount }, (_, index) => ({ id: index ? `22222222-2222-4222-8222-${String(index).padStart(12, '0')}` : docId, project_id: null, chapter_id: null, path: `world-${index}.md`, current_version: null, created_at: stamp, updated_at: stamp, setting_collection_id: collectionId, title: index ? `其他条目 ${index}` : '潮汐城', type: 'world_overview', metadata: { category: 'world' }, current_version_id: versionId })) }))
  await page.route('**/api/v1/documents/*/content', r => r.fulfill({ json: { document_id: r.request().url().split('/').at(-2), version_id: versionId, content: '潮水在午夜倒流。\n'.repeat(150) } }))
  await page.route('**/api/v1/projects', r => r.fulfill({ json: [] }))
  await page.route('**/api/v1/setting-collections', r => r.fulfill({ json: [] }))
  await page.route(`**/api/v1/setting-collections/${collectionId}/assistant/conversations`, r => r.fulfill({ json: conversation }))
  await page.goto(`/setting-collections/${collectionId}?category=world&note=${docId}&returnTo=/projects/ignored/studio`)
  await expect(page.getByRole('heading', { name: '潮汐城' })).toBeVisible()
}

for (const viewport of [{ width: 1440, height: 900 }, { width: 1062, height: 660 }, { width: 820, height: 600 }, { width: 720, height: 600 }]) {
  test(`collection Gura reserves reading space and restores focus at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await workspace(page)
    await expect(page.getByText('修改会影响关联作品的后续创作')).toHaveCount(0)
    await expect(page.getByRole('link', { name: '回到书架' })).toHaveAttribute('href', '/')
    await expect(page.getByRole('button', { name: 'Gura', exact: true })).toHaveCount(1)
    const main = page.locator('.setting-main')
    const before = await main.boundingBox()
    const toolsBefore = await page.locator('.setting-editor-tools').boundingBox()
    await page.getByRole('button', { name: 'Gura', exact: true }).click()
    const drawer = page.getByRole('dialog', { name: '与 Gura 对话' })
    await expect(drawer).toBeVisible()
    await expect(page.getByRole('textbox', { name: '给 Gura 的消息' })).toBeFocused()
    await expect.poll(async () => {
      const content = await main.boundingBox(), panel = await drawer.boundingBox()
      return viewport.width >= 800 ? content!.x + content!.width <= panel!.x - 16 : content!.y + content!.height <= panel!.y
    }).toBe(true)
    if (viewport.width >= 800) await expect.poll(async () => (await page.getByLabel('设定标题').boundingBox())!.width).toBeGreaterThan(160)
    const toolsAfter = await page.locator('.setting-editor-tools').boundingBox()
    expect(toolsAfter!.x).toBeCloseTo(toolsBefore!.x, 0)
    expect(toolsAfter!.y).toBeCloseTo(toolsBefore!.y, 0)
    expect(await page.evaluate(() => document.documentElement.scrollHeight - innerHeight)).toBe(0)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('button', { name: 'Gura', exact: true })).toBeFocused()
    await expect.poll(async () => (await main.boundingBox())!.width).toBeCloseTo(before!.width, 0)
  })
}

test('home always opens the bookshelf rather than a returnTo project', async ({ page }) => {
  await workspace(page)
  await page.getByRole('link', { name: '回到书架' }).click()
  await expect(page).toHaveURL('/')
  await expect(page.getByRole('button', { name: '新建作品', exact: true })).toBeVisible()
})

test('large collection title has quiet sidebar navigation', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 901 })
  await workspace(page)
  const header = page.getByRole('banner').or(page.locator('.setting-workspace-header'))
  await expect(header.getByRole('link')).toHaveCount(0)
  await expect(page.locator('.setting-library').getByRole('link', { name: '回到书架' })).toHaveAttribute('href', '/')
  await expect(page.getByRole('link', { name: '回到书架' })).toHaveText('回到书架')
  expect(await page.locator('.setting-workspace-title').evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThan(30)
})

test('note context menu animates both ways, keeps focus and respects reduced motion', async ({ page }) => {
  await workspace(page)
  const note = page.getByRole('button', { name: '潮汐城', exact: true })
  await note.click({ button: 'right' })
  const menu = page.locator('.setting-note-menu')
  await expect(menu).toBeVisible()
  expect(await menu.evaluate(el => getComputedStyle(el).animationName)).toContain('setting-menu-in')
  await page.keyboard.press('Escape')
  await expect(menu).toHaveClass(/is-closing/)
  await expect(menu).toHaveAttribute('inert', '')
  await expect(menu).toHaveCount(0)
  await expect(note).toBeFocused()
  await note.click({ button: 'right' })
  await page.getByRole('heading', { name: '雾港世界 · 长标题布局验证' }).click()
  await expect(menu).toHaveClass(/is-closing/)
  await expect(menu).toHaveCount(0)
  await note.click({ button: 'right' })
  await page.getByRole('menuitem', { name: '删除条目' }).click()
  await expect(menu).toHaveClass(/is-closing/)
  await expect(page.getByRole('dialog', { name: '删除这个条目？' })).toBeVisible()
  await expect(menu).toHaveCount(0)
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await note.click({ button: 'right' })
  expect(await menu.evaluate(el => getComputedStyle(el).animationName)).toBe('none')
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
})

test('category motion causes no phantom scroll', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 901 })
  await workspace(page, 35)
  for (const category of ['设定', '世界观', '设定']) {
    await page.getByRole('group', { name: '设定分类' }).getByRole('button', { name: category, exact: true }).click()
    const overflow = await page.locator('.setting-note-list').evaluate(async el => {
      let max = 0
      const until = performance.now() + 1300
      do { max = Math.max(max, el.scrollHeight - el.clientHeight); await new Promise(requestAnimationFrame) } while (performance.now() < until)
      return max
    })
    if (category === '设定') expect(overflow).toBe(0)
    else {
      expect(overflow).toBeGreaterThan(0)
      const list = page.locator('.setting-note-list')
      await list.focus()
      await page.keyboard.press('End')
      await expect.poll(() => list.evaluate(el => el.scrollTop)).toBeGreaterThan(0)
    }
  }
})

test('a lost message response is reconciled after refresh without re-sending', async ({ page }) => {
  let saved = { ...conversation, messages: [...conversation.messages] }, sends = 0
  await workspace(page)
  await page.route(`**/api/v1/setting-collections/${collectionId}/assistant/conversations`, r => r.fulfill({ json: saved }))
  await page.route(`**/assistant/conversations/conversation/messages`, async r => {
    const payload = r.request().postDataJSON()
    sends++
    expect(payload).toMatchObject({ document_id: docId, document_version_id: versionId })
    saved = { ...conversation, messages: [
      { id: 'user', role: 'user', content: payload.content, tool_calls: [{ client_message_id: payload.client_message_id }] },
      { id: 'reply', role: 'assistant', content: '根据已保存的设定，潮水会在午夜倒流。' },
    ] }
    await r.fulfill({ status: 503, json: {} })
  })
  await page.getByRole('button', { name: 'Gura', exact: true }).click()
  const input = page.getByRole('textbox', { name: '给 Gura 的消息' })
  await input.fill('解释潮汐')
  await page.getByRole('button', { name: '发送给 Gura', exact: true }).click()
  await expect(input).toHaveValue('解释潮汐')
  await page.reload()
  await expect(page.getByRole('log', { name: '助手对话记录' })).toContainText('潮水会在午夜倒流')
  await expect(input).toHaveValue('')
  expect(sends).toBe(1)
})
