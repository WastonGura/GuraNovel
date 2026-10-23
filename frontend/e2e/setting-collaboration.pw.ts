import { expect, test, type Page } from '@playwright/test'

const collectionId = '11111111-1111-4111-8111-111111111111'
const docId = '22222222-2222-4222-8222-222222222222'
const worldId = '44444444-4444-4444-8444-444444444444'
const stamp = '2026-10-20T00:00:00Z'

async function openCollectionCollaboration(page: Page) {
  await page.getByRole('button', { name: '设定集操作', exact: true }).click()
  await page.getByRole('menu', { name: '设定集操作', exact: true }).getByRole('menuitem', { name: '与设定 Agent 协作', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '设定 Agent 协作', exact: true })).toBeVisible()
}
type DocumentFixture = { id: string; project_id: null; chapter_id: null; setting_collection_id: string; title: string; type: string; path: string; current_version_id: string; current_version: null; metadata: object; created_at: string; updated_at: string }
type MessageFixture = { id: string; role: 'user' | 'assistant'; content: string; client_message_id: string | null; proposal: null | { id: string; setting_collection_id: string; target_document_id: string | null; base_version_id: string | null; title: string; category: string; proposed_content: string; reason: string; status: 'pending' | 'accepted'; created_at: string; source_task: { agent_role: string; workflow_run_id: null; chapter_id: null; novel_title: null } }; proposal_status: 'pending' | 'accepted' | null; base_content: string | null; base_title: string | null; accepted_version_id: string | null; created_at: string }

async function authoringBackend(page: Page, existing = false, multiple = false, legacyWorld = false) {
  let revision = 1, waiting: Promise<void> | null = null
  let release: () => void = () => {}
  const state = { creates: 0, sends: 0, accepts: 0, sessions: 0, body: existing ? '港口在黄昏关闭。' : '', docs: [] as DocumentFixture[], messages: [] as MessageFixture[] }
  const versionId = () => `33333333-3333-4333-8333-${String(revision).padStart(12, '0')}`
  const document = (title: string, metadata: object = { category: 'setting' }): DocumentFixture => ({ id: docId, project_id: null, chapter_id: null, setting_collection_id: collectionId,
    title, type: 'character_profile', path: 'setting/harbor.md', current_version_id: versionId(), current_version: null, metadata, created_at: stamp, updated_at: stamp })
  if (existing) state.docs.push(legacyWorld ? { ...document('旧港', {}), type: 'factions' } : document('旧港'))
  if (multiple) state.docs.push({ ...document('潮汐法则', { category: 'world' }), id: worldId, type: 'world_overview', path: 'world/tides.md' })
  const conversation = () => ({ id: 'setting-conversation', setting_collection_id: collectionId, document_id: null, agent_role: 'lore_agent', messages: state.messages, created_at: stamp, updated_at: stamp })
  let globalMessages: object[] = []
  const globalConversation = () => ({ id: 'global-conversation', title: '全局 Gura 对话', project_id: null, setting_collection_id: null, messages: globalMessages, created_at: stamp, updated_at: stamp })
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (path === `/api/v1/setting-collections/${collectionId}`) return json({ id: collectionId, owner_id: null, title: '雾港档案', slug: 'harbor', status: 'active', description: null, workspace_root: '/workspace/harbor', revision, metadata: {}, created_at: stamp, updated_at: stamp })
    if (path === `/api/v1/setting-collections/${collectionId}/projects`) return json([])
    if (path === `/api/v1/setting-collections/${collectionId}/documents`) {
      if (method === 'POST') {
        const payload = request.postDataJSON()
        state.creates++; state.body = payload.content; state.docs = [document(payload.title, payload.metadata)]
        return json(state.docs[0], 201)
      }
      return json(state.docs)
    }
    if (path === `/api/v1/documents/${docId}`) {
      if (method === 'PATCH') { const payload = request.postDataJSON(); state.docs[0] = { ...state.docs[0], ...payload } }
      return json(state.docs[0])
    }
    if (path === `/api/v1/documents/${docId}/content`) {
      if (method === 'PUT') {
        const payload = request.postDataJSON()
        if (payload.expected_current_version_id !== state.docs[0].current_version_id) return json({ error: { code: 'conflict', message: '正文版本已变化' } }, 409)
        const parent = state.docs[0].current_version_id
        revision++; state.body = payload.content; state.docs[0] = { ...state.docs[0], current_version_id: versionId() }
        return json({ id: versionId(), document_id: docId, version_number: revision, parent_version_id: parent, source: 'user', actor_user_id: null, agent_role: null, workflow_run_id: null, content_hash: 'hash', byte_size: state.body.length, word_count: state.body.length, file_path: 'setting/harbor.md', change_summary: null, created_at: stamp })
      }
      return json({ document_id: docId, version_id: state.docs[0].current_version_id, content: state.body })
    }
    if (path === `/api/v1/documents/${worldId}`) return json(state.docs.find(doc => doc.id === worldId))
    if (path === `/api/v1/documents/${worldId}/content`) return json({ document_id: worldId, version_id: state.docs.find(doc => doc.id === worldId)!.current_version_id, content: '潮汐每六小时转换一次。' })
    if (path === `/api/v1/setting-collections/${collectionId}/collaboration`) { state.sessions++; return json(conversation()) }
    if (path === `/api/v1/setting-collections/${collectionId}/collaboration/messages`) {
      const payload = request.postDataJSON(), base = state.docs.find(doc => doc.id === docId)
      state.sends++
      // The server chooses the target from its collection snapshot; the browser cannot invent model context.
      expect(Object.keys(payload).sort()).toEqual(['client_message_id', 'content'])
      const baseBody = base ? state.body : '', baseTitle = base?.title || '', baseVersion = base?.current_version_id || null
      const metadataCategory = (base?.metadata as { category?: string } | undefined)?.category
      const baseCategory = metadataCategory || (base && ['world_overview', 'power_system', 'factions', 'geography', 'history'].includes(base.type) ? 'world' : 'setting')
      const proposed = base ? `${baseBody}\n灯塔只在退潮时亮起。` : '灯塔只在退潮时亮起，为夜航船只指引回港的方向。'
      const number = state.sends
      if (waiting) { const gate = waiting; waiting = null; await gate }
      state.messages.push({ id: `author-${number}`, role: 'user', content: payload.content, client_message_id: payload.client_message_id, proposal: null, proposal_status: null, base_content: null, base_title: null, accepted_version_id: null, created_at: stamp },
        { id: `agent-${number}`, role: 'assistant', content: '设定 Agent 提出了灯塔的退潮规则。', client_message_id: null, proposal: { id: `proposal-${number}`, setting_collection_id: collectionId, target_document_id: base?.id || null, base_version_id: baseVersion, title: baseTitle || '夜航灯塔', category: baseCategory, proposed_content: proposed, reason: '让灯塔与港口的潮汐产生联系。', status: 'pending', created_at: stamp, source_task: { agent_role: 'lore_agent', workflow_run_id: null, chapter_id: null, novel_title: null } }, proposal_status: 'pending', base_content: baseBody, base_title: baseTitle, accepted_version_id: null, created_at: stamp })
      return json(conversation())
    }
    if (path.match(/\/collaboration\/proposals\/proposal-\d+\/accept$/)) {
      const payload = request.postDataJSON(), proposalId = path.split('/').at(-2), message = state.messages.find(item => item.proposal?.id === proposalId)!
      const target = state.docs.find(doc => doc.id === message.proposal!.target_document_id)
      expect(payload).toEqual({ expected_current_version_id: target?.current_version_id || null, expected_draft_content: target ? state.body : '', expected_title: target?.title || '' })
      state.accepts++; revision++; state.body = message.proposal!.proposed_content
      const accepted = { ...(target || document(message.proposal!.title)), current_version_id: versionId() }
      state.docs = target ? state.docs.map(doc => doc.id === target.id ? accepted : doc) : [...state.docs, accepted]
      message.proposal_status = 'accepted'; message.accepted_version_id = versionId(); message.proposal!.status = 'accepted'
      return json({ conversation: conversation(), document: accepted, content: state.body })
    }
    if (path === '/api/v1/assistant/conversations') return json(method === 'GET' ? [] : globalConversation())
    if (path === '/api/v1/assistant/conversations/global-conversation') return json(globalConversation())
    if (path === '/api/v1/assistant/conversations/global-conversation/messages') {
      const payload = request.postDataJSON()
      expect(payload.context).toMatchObject({ kind: 'collection', collection_id: collectionId })
      globalMessages = [{ id: 'global-user', role: 'user', content: payload.content, tool_calls: [{ client_message_id: payload.client_message_id, context: payload.context, exchange_complete: true }] }, { id: 'global-agent', role: 'assistant', content: 'Gura 可以通过侧栏底部的回到书架帮助你导航。' }]
      return json(globalConversation())
    }
    if (path === '/api/v1/projects' || path === '/api/v1/setting-collections') return json([])
    return json({ error: { code: 'not_found', message: '测试中未定义的接口' } }, 404)
  })
  return { state, holdReply() { waiting = new Promise(resolve => { release = resolve }); return () => release() } }
}

test('new entry autosaves, collaborates with the setting Agent, adopts explicitly and reloads independently of Gura', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const { state } = await authoringBackend(page)
  await page.goto(`/setting-collections/${collectionId}`)
  await page.getByRole('button', { name: '新建内容', exact: true }).click()
  const title = page.getByRole('textbox', { name: '设定标题' }), body = page.getByLabel('设定正文', { exact: true })
  await expect(title).toBeFocused()
  expect(state.creates).toBe(0)
  await title.fill('夜航灯塔')
  await body.fill('灯塔守护着雾中的港口。')
  await expect.poll(() => state.body).toBe('灯塔守护着雾中的港口。')
  expect(state.creates).toBe(1)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  await expect(panel).toBeVisible()
  await panel.getByRole('textbox', { name: '给设定 Agent 的消息' }).fill('为灯塔补充一条与潮汐相关的规则。')
  await panel.getByRole('button', { name: '发送给设定 Agent' }).click()
  await panel.getByRole('button', { name: '比较建议' }).click()
  await expect(panel.locator(':scope > header h2')).toHaveCount(0)
  await expect(body).toHaveText('灯塔守护着雾中的港口。')
  expect(state.accepts).toBe(0)
  await panel.getByRole('button', { name: '采纳建议' }).click()
  await expect(body).toHaveText('灯塔守护着雾中的港口。\n灯塔只在退潮时亮起。')
  expect(state.accepts).toBe(1)
  await page.reload()
  await expect(body).toHaveText(state.body)
  await expect(title).toHaveValue('夜航灯塔')
  await page.getByRole('button', { name: 'Gura', exact: true }).click()
  const gura = page.getByRole('dialog', { name: '与 Gura 对话' })
  await gura.getByRole('textbox', { name: '给 Gura 的消息' }).fill('如何回到主页？')
  await gura.getByRole('button', { name: '发送给 Gura', exact: true }).click()
  await expect(gura.getByRole('log', { name: '助手对话记录' })).toContainText('Gura 可以通过侧栏底部')
  await expect(gura.getByRole('log', { name: '助手对话记录' })).not.toContainText('设定 Agent 提出了')
  await gura.getByRole('textbox', { name: '给 Gura 的消息' }).press('Escape')
  await openCollectionCollaboration(page)
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await expect(panel.getByRole('log')).not.toContainText('Gura 可以通过侧栏底部')
  expect(await page.evaluate(() => document.documentElement.scrollHeight - innerHeight)).toBe(0)
})

test('typing during a setting Agent request retains fresh prose and requires a new comparison instead of applying stale advice', async ({ page }) => {
  const backend = await authoringBackend(page, true)
  const release = backend.holdReply()
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  const body = page.getByLabel('设定正文', { exact: true })
  await expect(body).toHaveText('港口在黄昏关闭。')
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  await panel.getByRole('textbox', { name: '给设定 Agent 的消息' }).fill('补充港口的细节。')
  await panel.getByRole('button', { name: '发送给设定 Agent' }).click()
  await expect.poll(() => backend.state.sends).toBe(1)
  await panel.getByRole('button', { name: '关闭设定协作' }).click()
  await expect(panel).not.toBeAttached()
  await expect(body).toBeFocused()
  await body.fill('港口彻夜开放，船只会在黎明前归来。')
  await expect.poll(() => backend.state.body).toBe('港口彻夜开放，船只会在黎明前归来。')
  release()
  await expect.poll(() => backend.state.messages.length).toBe(2)
  await openCollectionCollaboration(page)
  await panel.getByRole('button', { name: '比较建议' }).click()
  await expect(panel).toContainText('目标条目已更新，请基于设定集的最新内容重新讨论。')
  await expect(panel.getByRole('button', { name: '采纳建议' })).toHaveCount(0)
  await expect(panel.getByRole('button', { name: '基于最新设定集重新讨论' })).toBeVisible()
  await expect(body).toHaveText('港口彻夜开放，船只会在黎明前归来。')
  expect(backend.state.accepts).toBe(0)
  await page.reload()
  await expect(body).toHaveText('港口彻夜开放，船只会在黎明前归来。')
})

test('the collection Agent keeps the same history and draft while switching entries and categories', async ({ page }) => {
  const { state } = await authoringBackend(page, true, true)
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&category=setting&mode=note`)
  await expect(page.getByRole('textbox', { name: '设定标题' })).toHaveValue('旧港')
  const open = () => openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' })
  await open()
  await draft.fill('统一考虑港口和潮汐，不限于当前打开的条目。')
  await panel.getByRole('button', { name: '发送给设定 Agent' }).click()
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await draft.fill('这段未发送的整体世界构想要保留。')
  await panel.getByRole('button', { name: '关闭设定协作' }).click()
  await expect(panel).not.toBeAttached()
  const showLibrary = async () => {
    const trigger = page.getByRole('button', { name: '展开设定侧边栏' })
    if (await trigger.getAttribute('aria-expanded') === 'false') await trigger.click()
  }
  await showLibrary()
  await page.getByRole('button', { name: '世界观', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '设定标题' })).toHaveValue('潮汐法则')
  await open()
  await expect(panel.locator(':scope > header h2')).toHaveCount(0)
  await expect(draft).toHaveValue('这段未发送的整体世界构想要保留。')
  await expect(panel.getByRole('log')).toContainText('统一考虑港口和潮汐，不限于当前打开的条目。')
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await panel.getByRole('button', { name: '关闭设定协作' }).click()
  await expect(panel).not.toBeAttached()
  await showLibrary()
  await page.getByRole('button', { name: '设定', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '设定标题' })).toHaveValue('旧港')
  await open()
  await expect(draft).toHaveValue('这段未发送的整体世界构想要保留。')
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  expect(state.sends).toBe(1)
  expect(state.creates).toBe(0)
  await page.reload()
  await open()
  await expect(draft).toHaveValue('这段未发送的整体世界构想要保留。')
  await expect(panel.getByRole('log')).toContainText('统一考虑港口和潮汐，不限于当前打开的条目。')
})

test('an empty collection can discuss and propose a new entry without creating a placeholder document', async ({ page }) => {
  const { state } = await authoringBackend(page)
  await page.goto(`/setting-collections/${collectionId}`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  await expect(panel.getByRole('heading', { name: '一起构建这个世界' })).toBeVisible()
  expect(state.docs).toHaveLength(0)
  expect(state.creates).toBe(0)
  await panel.getByRole('textbox', { name: '给设定 Agent 的消息' }).fill('先讨论一座由潮汐决定航路的海港，并建议第一个条目。')
  await panel.getByRole('button', { name: '发送给设定 Agent' }).click()
  await panel.getByRole('button', { name: '比较建议' }).click()
  await expect(panel).toContainText('夜航灯塔')
  expect(state.docs).toHaveLength(0)
  expect(state.creates).toBe(0)
  expect(state.accepts).toBe(0)
  expect(state.messages.at(-1)?.proposal).toMatchObject({ target_document_id: null, base_version_id: null })
  await panel.getByRole('button', { name: '采纳建议' }).click()
  await expect.poll(() => state.accepts).toBe(1)
  expect(state.docs).toHaveLength(1)
  expect(state.creates).toBe(0)
  await panel.getByRole('button', { name: '关闭设定协作' }).click()
  await expect(page.getByRole('textbox', { name: '设定标题' })).toHaveValue('夜航灯塔')
  await expect(page.getByLabel('设定正文', { exact: true })).toHaveText(state.body)
})

test('adopting a legacy world document preserves its world category and URL without switching entries', async ({ page }) => {
  const { state } = await authoringBackend(page, true, false, true)
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&category=world&mode=note`)
  const title = page.getByRole('textbox', { name: '设定标题' }), body = page.getByLabel('设定正文', { exact: true })
  await expect(title).toHaveValue('旧港')
  await expect(body).toHaveText('港口在黄昏关闭。')
  await expect(page.getByRole('button', { name: '世界观', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByLabel('世界观条目', { exact: true })).toContainText('旧港')
  expect(state.docs[0]).toMatchObject({ type: 'factions', metadata: {} })
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  await panel.getByRole('textbox', { name: '给设定 Agent 的消息' }).fill('补充这个港口组织的潮汐约定。')
  await panel.getByRole('button', { name: '发送给设定 Agent' }).click()
  await panel.getByRole('button', { name: '比较建议' }).click()
  expect(state.messages.at(-1)?.proposal).toMatchObject({ target_document_id: docId, category: 'world' })
  await panel.getByRole('button', { name: '采纳建议' }).click()
  await expect.poll(() => state.accepts).toBe(1)
  await panel.getByRole('button', { name: '关闭设定协作' }).click()
  await expect(title).toHaveValue('旧港')
  await expect(body).toHaveText('港口在黄昏关闭。\n灯塔只在退潮时亮起。')
  await expect(page.getByRole('button', { name: '世界观', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByLabel('世界观条目', { exact: true })).toContainText('旧港')
  expect(new URL(page.url()).searchParams.get('category')).toBe('world')
  expect(new URL(page.url()).searchParams.get('note')).toBe(docId)
})

for (const viewport of [{ width: 1440, height: 900 }, { width: 926, height: 716 }]) {
test(`the main Agent stays separate from Gura and retains the manuscript at ${viewport.width}x${viewport.height}`, async ({ page }) => {
  await page.setViewportSize(viewport)
  await authoringBackend(page, true)
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  const body = page.getByLabel('设定正文', { exact: true })
  await expect(body).toHaveText('港口在黄昏关闭。')
  const writingStart = (await page.getByRole('textbox', { name: '设定标题' }).boundingBox())!
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('heading', { name: '一起构建这个世界' })).toBeVisible()
  await expect(body).toBeHidden()
  expect((await panel.boundingBox())!.width).toBeGreaterThan(viewport.width > 1100 ? 700 : 280)
  const assertStart = async (title: { x: number; y: number }) => {
    await expect.poll(async () => Math.abs((await panel.boundingBox())!.x - title.x)).toBeLessThanOrEqual(12)
    const introduction = panel.getByRole('heading', { name: '一起构建这个世界' })
    await expect.poll(async () => Math.abs((await introduction.boundingBox())!.x - title.x)).toBeLessThanOrEqual(12)
    await expect.poll(async () => Math.abs((await introduction.boundingBox())!.y - title.y)).toBeLessThanOrEqual(2)
    const column = await panel.evaluate(element => {
      const bounds = element.getBoundingClientRect(), style = getComputedStyle(element)
      const left = bounds.left + Number.parseFloat(style.paddingLeft), right = bounds.right - Number.parseFloat(style.paddingRight)
      return { left, right, width: right - left }
    })
    const composer = panel.locator('.setting-collaboration-composer')
    await expect.poll(async () => Math.abs((await composer.boundingBox())!.width - Math.min(column.width, 760))).toBeLessThanOrEqual(1)
    const inputBounds = (await composer.boundingBox())!
    expect(Math.abs(inputBounds.x + inputBounds.width / 2 - (column.left + column.right) / 2)).toBeLessThanOrEqual(1)
    expect(inputBounds.x).toBeGreaterThanOrEqual(column.left - 1)
    expect(inputBounds.x + inputBounds.width).toBeLessThanOrEqual(column.right + 1)
  }
  await assertStart(writingStart)
  await page.getByRole('button', { name: 'Gura', exact: true }).click()
  const gura = page.getByRole('dialog', { name: '与 Gura 对话' })
  await expect(gura).toBeVisible()
  await expect.poll(async () => {
    const main = (await panel.boundingBox())!, floating = (await gura.boundingBox())!
    return main.x + main.width < floating.x
  }).toBe(true)
  await panel.getByRole('button', { name: '关闭设定协作' }).click()
  await expect(body).toBeVisible()
  const writingWithGura = (await page.getByRole('textbox', { name: '设定标题' }).boundingBox())!
  await openCollectionCollaboration(page)
  await expect(panel.getByRole('heading', { name: '一起构建这个世界' })).toBeVisible()
  await assertStart(writingWithGura)
  const height = page.getByRole('separator', { name: '调整 Gura 高度' })
  await height.press('Shift+ArrowUp')
  await expect.poll(async () => {
    const main = (await panel.boundingBox())!, floating = (await gura.boundingBox())!
    return main.x + main.width < floating.x
  }).toBe(true)
  await gura.getByRole('textbox', { name: '给 Gura 的消息' }).press('Escape')
  await panel.getByRole('textbox', { name: '给设定 Agent 的消息' }).fill('这段生成要求不能丢失。')
  await panel.getByRole('button', { name: '关闭设定协作' }).click()
  await expect(body).toBeVisible()
  await expect(body).toBeFocused()
  await openCollectionCollaboration(page)
  await expect(panel.getByRole('textbox', { name: '给设定 Agent 的消息' })).toHaveValue('这段生成要求不能丢失。')
  expect(await page.evaluate(() => document.documentElement.scrollHeight - innerHeight)).toBe(0)
})
}
