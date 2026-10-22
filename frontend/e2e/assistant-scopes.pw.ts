import { expect, test, type Page } from '@playwright/test'

// HTTP route fixtures verify browser behavior and request context, not model quality.
const stamp = '2026-10-22T00:00:00Z'
const projectId = '44444444-4444-4444-8444-444444444444'
const otherProjectId = '55555555-5555-4555-8555-555555555555'
const chapterId = '66666666-6666-4666-8666-666666666666'
const otherChapterId = '88888888-8888-4888-8888-888888888888'
const collectionId = '77777777-7777-4777-8777-777777777777'
const projects = [
  { id: projectId, title: '雾港行记', slug: 'fog-port' },
  { id: otherProjectId, title: '月海来信', slug: 'moon-sea' },
].map(project => ({ ...project, genre: null, target_platform: null, status: 'active', workspace_root: `workspaces/${project.slug}`, metadata: {}, created_at: stamp, updated_at: stamp }))
const collection = { id: collectionId, title: '独立潮汐世界', slug: 'tide-world', owner_id: null, description: null, workspace_root: 'workspaces/tide-world', metadata: {}, revision: 1, status: 'active', created_at: stamp, updated_at: stamp }
const chapter = { id: chapterId, project_id: projectId, chapter_number: 1, title: '抵达雾港', status: 'draft', current_outline_document_id: null, current_draft_document_id: null, final_document_id: null, summary_document_id: null, word_count: 0, metadata: {}, created_at: stamp, updated_at: stamp }
const otherChapter = { ...chapter, id: otherChapterId, chapter_number: 2, title: '潮水来信' }
type Context = { kind: 'home' | 'project' | 'collection'; current_view?: string; project_id?: string; chapter_id?: string | null; collection_id?: string; document_id?: string | null; document_version_id?: string | null }
type Payload = { content: string; client_message_id: string; context: Context }
type Message = { id: string; role: string; content: string; created_at: string; tool_calls?: { client_message_id: string; context: Context; exchange_complete: boolean }[] }
type Conversation = { id: string; title: string; project_id: null; chapter_id: null; setting_collection_id: null; messages: Message[]; created_at: string; updated_at: string }

async function assistantFixture(page: Page, legacy = false) {
  const conversations: Conversation[] = []
  const sends: { conversationId: string; payload: Payload }[] = []
  const creates: { new_conversation?: boolean; client_conversation_id?: string }[] = []
  const unexpected: string[] = []
  const legacyReads: string[] = []
  let failure: 'none' | 'reject' | 'lost-ack' = 'none'
  let loadFailure = false
  let heldSend: Promise<void> | null = null
  let heldLegacyRead: Promise<void> | null = null
  const createConversation = (clientId?: string) => {
    const existing = conversations.find(conversation => conversation.id === clientId)
    if (existing) return existing
    const id = clientId || `aaaaaaaa-aaaa-4aaa-8aaa-${String(conversations.length + 1).padStart(12, '0')}`
    const conversation: Conversation = { id, title: '新对话', project_id: null, chapter_id: null, setting_collection_id: null, messages: [], created_at: stamp, updated_at: stamp }
    conversations.unshift(conversation)
    return conversation
  }
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method()
    const reply = (json: unknown) => route.fulfill({ json })
    const endpoint = path.match(/^\/api\/v1\/assistant\/conversations(?:\/([^/]+)(\/messages)?)?$/)
    if (endpoint) {
      if (!endpoint[2]) {
        if (loadFailure) return route.fulfill({ status: 503, json: { detail: 'fixture service unavailable' } })
        if (!endpoint[1] && method === 'GET') return reply(conversations.map(conversation => ({ id: conversation.id, title: conversation.title, created_at: conversation.created_at, updated_at: conversation.updated_at, message_count: conversation.messages.length, preview: conversation.messages.at(-1)?.content || '' })))
        if (!endpoint[1]) {
          expect(method).toBe('POST')
          const payload = request.postData() ? request.postDataJSON() : {}
          expect(Object.keys(payload).filter(key => !['new_conversation', 'client_conversation_id'].includes(key))).toEqual([])
          if ('new_conversation' in payload) expect(typeof payload.new_conversation).toBe('boolean')
          if (payload.client_conversation_id) {
            expect(payload.new_conversation).toBe(true)
            expect(payload.client_conversation_id).toMatch(/^[0-9a-f-]{36}$/i)
          }
          creates.push(payload)
          return reply(payload.new_conversation || !conversations.length ? createConversation(payload.client_conversation_id) : conversations[0])
        }
        expect(method).toBe('GET')
        const conversation = conversations.find(item => item.id === endpoint[1])
        expect(conversation).toBeDefined()
        return reply(conversation)
      }
      expect(method).toBe('POST')
      const conversation = conversations.find(item => item.id === endpoint[1])!
      expect(conversation).toBeDefined()
      const payload = request.postDataJSON() as Payload
      expect(Object.keys(payload).sort()).toEqual(['client_message_id', 'content', 'context'])
      expect(payload.client_message_id).toMatch(/^[0-9a-f-]{36}$/i)
      expect(['home', 'project', 'collection']).toContain(payload.context.kind)
      sends.push({ conversationId: conversation.id, payload })
      const held = heldSend
      heldSend = null
      if (held) await held
      if (failure !== 'reject' && !conversation.messages.some(message => message.tool_calls?.some(call => call.client_message_id === payload.client_message_id))) {
        if (!conversation.messages.length) conversation.title = payload.content.slice(0, 80)
        conversation.messages.push(
          { id: `user-${sends.length}`, role: 'user', content: payload.content, created_at: stamp, tool_calls: [{ client_message_id: payload.client_message_id, context: payload.context, exchange_complete: true }] },
          { id: `reply-${sends.length}`, role: 'assistant', content: `HTTP 测试回复（${payload.context.kind}）：${payload.content}`, created_at: stamp },
        )
      }
      if (failure !== 'none') return route.fulfill({ status: 503, json: { detail: 'fixture model unavailable' } })
      return reply(conversation)
    }
    if (path.startsWith('/api/v1/assistant/legacy-conversations')) {
      expect(method).toBe('GET')
      if (!legacy) return reply([])
      expect(new URL(request.url()).searchParams.get('project_id')).toBe(projectId)
      legacyReads.push(path)
      const old = { id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', title: '之前的作品讨论', project_id: projectId, chapter_id: null, setting_collection_id: null, created_at: stamp, updated_at: stamp, messages: [
        { id: 'legacy-user', role: 'user', content: '之前保存的作品问题', created_at: stamp },
        { id: 'legacy-reply', role: 'assistant', content: '之前保存的回答', created_at: stamp },
      ] }
      const other = { ...old, id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', title: '另一次作品讨论', messages: [
        { id: 'other-legacy-user', role: 'user', content: '另一次保存的作品问题', created_at: stamp },
        { id: 'other-legacy-reply', role: 'assistant', content: '另一次保存的回答', created_at: stamp },
      ] }
      if (path.endsWith(other.id)) {
        const held = heldLegacyRead
        heldLegacyRead = null
        if (held) await held
        return reply(other)
      }
      return reply(path.endsWith(old.id) ? old : [old, other].map(conversation => ({ id: conversation.id, title: conversation.title, created_at: stamp, updated_at: stamp, message_count: 2, preview: conversation.messages.at(-1)!.content })))
    }
    if (path === '/api/v1/projects') return reply(projects)
    if (path === '/api/v1/setting-collections') return reply([collection])
    if (path === `/api/v1/setting-collections/${collectionId}`) return reply(collection)
    if (path === `/api/v1/setting-collections/${collectionId}/projects` || path === `/api/v1/setting-collections/${collectionId}/documents`) return reply([])
    if (path === `/api/v1/projects/${projectId}`) return reply(projects[0])
    if (path === `/api/v1/projects/${otherProjectId}`) return reply(projects[1])
    if (path === `/api/v1/projects/${projectId}/chapters`) return reply([chapter, otherChapter])
    if (path === `/api/v1/projects/${otherProjectId}/chapters`) return reply([])
    const idea = path.match(/\/chapters\/([^/]+)\/outline\/idea$/)
    if (idea && [chapterId, otherChapterId].includes(idea[1])) return reply({ project_id: projectId, chapter_id: idea[1], text: '', revision: 0, read_only: false })
    if (path.endsWith('/outline/generations') || path.endsWith('/production-v2')) return reply([])
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: {} })
  })
  return {
    conversations, sends, creates, unexpected, legacyReads,
    setFailure: (next: typeof failure) => { failure = next },
    setLoadFailure: (next: boolean) => { loadFailure = next },
    holdNextSend: () => {
      let release!: () => void
      heldSend = new Promise<void>(resolve => { release = resolve })
      return release
    },
    holdNextLegacyRead: () => {
      let release!: () => void
      heldLegacyRead = new Promise<void>(resolve => { release = resolve })
      return release
    },
  }
}

const input = (page: Page) => page.getByRole('textbox', { name: '给 Gura 的消息', exact: true })
const history = (page: Page) => page.getByRole('log', { name: '助手对话记录', exact: true })
const activeId = (page: Page) => page.evaluate(() => sessionStorage.getItem('guranovel_assistant_active_conversation'))

async function openAssistant(page: Page) {
  await expect(page.getByRole('complementary', { name: 'Gura 助手', exact: true })).toHaveCount(1)
  const dialog = page.getByRole('dialog', { name: '与 Gura 对话', exact: true })
  const launcher = page.getByRole('button', { name: 'Gura', exact: true })
  if (await launcher.getAttribute('aria-expanded') === 'false') await launcher.click()
  await expect(dialog).toBeVisible()
  await expect(page.getByRole('button', { name: '发送给 Gura（仅预览）', exact: true })).toHaveCount(0)
  return dialog
}

async function send(page: Page, content: string) {
  await input(page).fill(content)
  await page.getByRole('button', { name: '发送给 Gura', exact: true }).click()
  await expect(history(page)).toContainText(`：${content}`)
  await expect(input(page)).toHaveValue('')
}

async function newConversation(page: Page) {
  await page.getByRole('button', { name: '新建对话', exact: true }).click()
  await expect(history(page).locator('.studio-assistant-message')).toHaveCount(0)
  await expect(input(page)).toHaveValue('')
}

async function selectConversation(page: Page, title: string) {
  await page.getByRole('button', { name: '历史对话', exact: true }).click()
  await page.getByRole('button', { name: `继续对话：${title}`, exact: true }).click()
  await expect(history(page)).toContainText(title)
}

test('home uses the HTTP service, keeps distinct bubbles, and reloads its active conversation', async ({ page }) => {
  const fixture = await assistantFixture(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await openAssistant(page)
  await send(page, '如何从书架开始创作？')
  const conversationId = await activeId(page)
  expect(fixture.sends).toHaveLength(1)
  expect(fixture.sends[0]).toEqual({ conversationId, payload: { content: '如何从书架开始创作？', client_message_id: expect.any(String), context: { kind: 'home', current_view: 'home' } } })
  const user = history(page).locator('.studio-assistant-message.is-user')
  const reply = history(page).locator('.studio-assistant-message').filter({ hasText: 'HTTP 测试回复' })
  await expect(user).toHaveCSS('background-color', 'rgb(36, 36, 36)')
  await expect(user).toHaveCSS('color', 'rgb(255, 255, 255)')
  await expect(reply).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  await expect(reply).toHaveCSS('color', 'rgb(34, 34, 34)')
  await expect.poll(async () => (await page.getByRole('complementary', { name: 'Gura 助手', exact: true }).boundingBox())!.width).toBeCloseTo(320, 0)
  await page.reload()
  await expect(history(page)).toContainText('HTTP 测试回复（home）：如何从书架开始创作？')
  await expect(user).toHaveCount(1)
  expect(await activeId(page)).toBe(conversationId)
  expect(fixture.sends).toHaveLength(1)
  expect(fixture.conversations).toHaveLength(1)
  expect(fixture.unexpected).toEqual([])
})

test('one conversation, history and draft continue through bookshelf, two details, Studio and independent settings', async ({ page }) => {
  const fixture = await assistantFixture(page)
  await page.goto('/')
  await openAssistant(page)
  await send(page, '首页开始的同一段对话')
  const conversationId = await activeId(page)
  await input(page).fill('跨页面继续填写的草稿')
  await page.getByRole('button', { name: `Open ${projects[0].title}`, exact: true }).first().click()
  await expect(page.getByRole('dialog', { name: projects[0].title, exact: true })).toBeVisible()
  await expect(page.locator('.studio-assistant')).toHaveCount(0)
  expect(await activeId(page)).toBe(conversationId)
  await page.getByRole('button', { name: '关闭作品详情', exact: true }).click()
  await openAssistant(page)
  await expect(input(page)).toHaveValue('跨页面继续填写的草稿')
  await expect(history(page)).toContainText('首页开始的同一段对话')
  await page.getByRole('button', { name: `Open ${projects[1].title}`, exact: true }).first().click()
  await expect(page.getByRole('dialog', { name: projects[1].title, exact: true })).toBeVisible()
  await expect(page.locator('.studio-assistant')).toHaveCount(0)
  expect(fixture.sends).toHaveLength(1)
  await page.getByRole('button', { name: '关闭作品详情', exact: true }).click()
  await openAssistant(page)
  await expect(input(page)).toHaveValue('跨页面继续填写的草稿')
  await page.getByRole('button', { name: `Open ${projects[0].title}`, exact: true }).first().click()
  await expect(page.locator('.studio-assistant')).toHaveCount(0)
  await page.getByRole('button', { name: /第1话.*抵达雾港/ }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/studio/${chapterId}`))
  await openAssistant(page)
  await expect(input(page)).toHaveValue('跨页面继续填写的草稿')
  await expect(history(page)).toContainText('首页开始的同一段对话')
  await send(page, '本章大纲怎么写？')
  expect(fixture.sends.at(-1)?.payload.context).toEqual({ kind: 'project', project_id: projectId, chapter_id: chapterId, current_view: 'Outline' })
  await input(page).fill('从 Studio 带到设定集的草稿')
  await page.getByRole('button', { name: '返回主页', exact: true }).click()
  await page.getByRole('button', { name: `Open ${collection.title} setting collection`, exact: true }).click()
  await expect(page.getByRole('heading', { name: collection.title, exact: true })).toBeVisible()
  await openAssistant(page)
  await expect(input(page)).toHaveValue('从 Studio 带到设定集的草稿')
  await expect(history(page)).toContainText('本章大纲怎么写？')
  await send(page, '独立设定中的新问题')
  expect(fixture.sends.at(-1)?.payload.context).toEqual({ kind: 'collection', collection_id: collectionId })
  await page.getByRole('link', { name: '回到书架', exact: true }).click()
  await openAssistant(page)
  for (const text of ['首页开始的同一段对话', '本章大纲怎么写？', '独立设定中的新问题']) await expect(history(page)).toContainText(text)
  expect(await activeId(page)).toBe(conversationId)
  expect(new Set(fixture.sends.map(send => send.conversationId))).toEqual(new Set([conversationId]))
  expect(fixture.conversations).toHaveLength(1)
  expect(fixture.unexpected).toEqual([])
})

test('explicit new conversation is empty, history restores separate drafts, and refresh keeps the active choice', async ({ page }) => {
  const fixture = await assistantFixture(page)
  await page.goto('/')
  await openAssistant(page)
  await send(page, '第一段全局对话')
  const first = await activeId(page)
  await input(page).fill('第一段对话的草稿')
  await newConversation(page)
  const second = await activeId(page)
  expect(second).not.toBe(first)
  await send(page, '第二段全局对话')
  await input(page).fill('第二段对话的草稿')
  await page.reload()
  await expect(input(page)).toHaveValue('第二段对话的草稿')
  await expect(history(page)).toContainText('第二段全局对话')
  await expect(history(page)).not.toContainText('第一段全局对话')
  expect(await activeId(page)).toBe(second)
  const historyToggle = page.getByRole('button', { name: '历史对话', exact: true })
  await expect(historyToggle).toHaveAttribute('aria-pressed', 'false')
  await historyToggle.click()
  await expect(historyToggle).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('region', { name: '历史对话', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '返回当前对话', exact: true })).toHaveCount(0)
  await historyToggle.click()
  await expect(historyToggle).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByRole('region', { name: '历史对话', exact: true })).toHaveCount(0)
  await expect(input(page)).toHaveValue('第二段对话的草稿')
  expect(await activeId(page)).toBe(second)
  await selectConversation(page, '第一段全局对话')
  await expect(input(page)).toHaveValue('第一段对话的草稿')
  await expect(history(page)).not.toContainText('第二段全局对话')
  expect(await activeId(page)).toBe(first)
  await page.reload()
  await expect(input(page)).toHaveValue('第一段对话的草稿')
  await expect(history(page)).toContainText('第一段全局对话')
  await expect(history(page)).not.toContainText('第二段全局对话')
  expect(await activeId(page)).toBe(first)
  await selectConversation(page, '第二段全局对话')
  await expect(input(page)).toHaveValue('第二段对话的草稿')
  expect(await activeId(page)).toBe(second)
  expect(fixture.creates.filter(payload => payload.new_conversation)).toEqual([{ new_conversation: true, client_conversation_id: second }])
  expect(fixture.conversations).toHaveLength(2)
  expect(fixture.unexpected).toEqual([])
})

test('a late response for conversation A cannot replace conversation B history, draft, focus or active identity', async ({ page }) => {
  const fixture = await assistantFixture(page)
  await page.goto('/')
  await openAssistant(page)
  await send(page, '等待中的对话 A')
  const first = await activeId(page)
  const release = fixture.holdNextSend()
  await input(page).fill('对话 A 的迟到回答')
  await page.getByRole('button', { name: '发送给 Gura', exact: true }).click()
  await expect.poll(() => fixture.sends.length).toBe(2)
  await newConversation(page)
  const second = await activeId(page)
  await send(page, '当前对话 B')
  await input(page).fill('对话 B 尚未发送的草稿')
  const externalFocus = page.getByRole('button', { name: '搜索', exact: true })
  await externalFocus.focus()
  const lateResponse = page.waitForResponse(response => response.url().endsWith(`/${first}/messages`))
  release()
  await (await lateResponse).finished()
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  await expect(input(page)).toHaveValue('对话 B 尚未发送的草稿')
  await expect(externalFocus).toBeFocused()
  await expect(history(page)).toContainText('当前对话 B')
  await expect(history(page)).not.toContainText('对话 A 的迟到回答')
  expect(await activeId(page)).toBe(second)
  await selectConversation(page, '等待中的对话 A')
  await expect(history(page)).toContainText('HTTP 测试回复（home）：对话 A 的迟到回答')
  await expect(history(page).locator('.studio-assistant-message.is-user')).toHaveCount(2)
  await expect(input(page)).toHaveValue('')
  expect(await activeId(page)).toBe(first)
  await expect(page.getByRole('button', { name: '重试未确认的消息', exact: true })).toHaveCount(0)
  expect(fixture.sends).toHaveLength(3)
  expect(fixture.unexpected).toEqual([])
})

test('a reply arriving after navigation continues the same conversation without resending or replacing newer typing and focus', async ({ page }) => {
  const fixture = await assistantFixture(page)
  await page.goto(`/projects/${projectId}/studio/${chapterId}`)
  await openAssistant(page)
  await send(page, '导航前已经开始的全局对话')
  const conversationId = await activeId(page)
  const release = fixture.holdNextSend()
  await input(page).fill('从 Studio 发送而尚未返回的问题')
  await page.getByRole('button', { name: '发送给 Gura', exact: true }).click()
  await expect.poll(() => fixture.sends.length).toBe(2)
  expect(fixture.sends[1].payload.context).toEqual({ kind: 'project', project_id: projectId, chapter_id: chapterId, current_view: 'Outline' })
  await input(page).fill('准备在设定集继续发送的新草稿')
  await page.getByRole('button', { name: '返回主页', exact: true }).click()
  await page.getByRole('button', { name: `Open ${collection.title} setting collection`, exact: true }).click()
  await expect(page.getByRole('heading', { name: collection.title, exact: true })).toBeVisible()
  await openAssistant(page)
  await expect(input(page)).toHaveValue('准备在设定集继续发送的新草稿')
  const externalFocus = page.getByRole('link', { name: '回到书架', exact: true })
  await externalFocus.focus()
  const lateResponse = page.waitForResponse(response => response.url().endsWith(`/${conversationId}/messages`))
  release()
  await (await lateResponse).finished()
  await expect(history(page)).toContainText('HTTP 测试回复（project）：从 Studio 发送而尚未返回的问题')
  await expect(page.getByRole('button', { name: '重试未确认的消息', exact: true })).toHaveCount(0)
  await expect(input(page)).toHaveValue('准备在设定集继续发送的新草稿')
  await expect(externalFocus).toBeFocused()
  expect(await activeId(page)).toBe(conversationId)
  expect(fixture.sends).toHaveLength(2)
  await page.getByRole('button', { name: '发送给 Gura', exact: true }).click()
  await expect(history(page)).toContainText('HTTP 测试回复（collection）：准备在设定集继续发送的新草稿')
  expect(fixture.sends[2]).toMatchObject({ conversationId, payload: { context: { kind: 'collection', collection_id: collectionId } } })
  expect(fixture.unexpected).toEqual([])
})

test('a reply completing while project details are open restores the same chat and draft after closing', async ({ page }) => {
  const fixture = await assistantFixture(page)
  await page.goto('/')
  await openAssistant(page)
  const release = fixture.holdNextSend()
  await input(page).fill('在书架发送的延迟问题')
  await page.getByRole('button', { name: '发送给 Gura', exact: true }).click()
  await expect.poll(() => fixture.sends.length).toBe(1)
  const conversationId = await activeId(page)
  await input(page).fill('关闭详情后继续填写的草稿')
  await page.getByRole('button', { name: `Open ${projects[0].title}`, exact: true }).first().click()
  const detail = page.getByRole('dialog', { name: projects[0].title, exact: true })
  await expect(detail).toBeVisible()
  await expect(page.locator('.studio-assistant')).toHaveCount(0)
  const editing = page.getByRole('button', { name: '编辑资料', exact: true })
  await editing.focus()
  const lateResponse = page.waitForResponse(response => response.url().endsWith(`/${conversationId}/messages`))
  release()
  await (await lateResponse).finished()
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  await expect(detail).toBeVisible()
  await expect(editing).toBeFocused()
  await expect(page.locator('.studio-assistant')).toHaveCount(0)
  await expect(page).toHaveURL(`/?project=${projectId}`)
  await page.getByRole('button', { name: '关闭作品详情', exact: true }).click()
  await openAssistant(page)
  await expect(history(page)).toContainText('HTTP 测试回复（home）：在书架发送的延迟问题')
  await expect(input(page)).toHaveValue('关闭详情后继续填写的草稿')
  await expect(page.getByRole('button', { name: '重试未确认的消息', exact: true })).toHaveCount(0)
  expect(await activeId(page)).toBe(conversationId)
  expect(fixture.sends).toHaveLength(1)
  expect(fixture.unexpected).toEqual([])
})

test('earlier page conversations are readable without stale content or changing the active global conversation and draft', async ({ page }, testInfo) => {
  const fixture = await assistantFixture(page, true)
  await page.goto(`/projects/${projectId}/studio/${chapterId}`)
  await expect(page.getByRole('region', { name: '本章大纲', exact: true })).toBeVisible()
  await openAssistant(page)
  await send(page, '现在继续的全局对话')
  const conversationId = await activeId(page)
  await input(page).fill('读取旧对话时保留的草稿')
  const historyToggle = page.getByRole('button', { name: '历史对话', exact: true })
  await expect(historyToggle).toHaveAttribute('aria-pressed', 'false')
  await historyToggle.click()
  await expect(historyToggle).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('heading', { name: '早期页面对话', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '返回当前对话', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '查看早期对话：之前的作品讨论', exact: true }).click()
  await expect(historyToggle).toHaveAttribute('aria-pressed', 'true')
  await expect(history(page)).toContainText('之前保存的回答')
  await expect(page.getByText('早期页面对话 · 只读', { exact: true })).toBeVisible()
  await expect(input(page)).toHaveCount(0)
  await expect(page.getByRole('button', { name: '发送给 Gura', exact: true })).toHaveCount(0)
  expect(await activeId(page)).toBe(conversationId)
  await expect(page.getByRole('button', { name: '返回当前对话', exact: true })).toHaveCount(0)
  await historyToggle.click()
  await expect(historyToggle).toHaveAttribute('aria-pressed', 'false')
  await expect(input(page)).toHaveValue('读取旧对话时保留的草稿')
  await historyToggle.click()
  const release = fixture.holdNextLegacyRead()
  await page.getByRole('button', { name: '查看早期对话：另一次作品讨论', exact: true }).click()
  try {
    await expect.poll(() => fixture.legacyReads.at(-1)).toBe('/api/v1/assistant/legacy-conversations/cccccccc-cccc-4ccc-8ccc-cccccccccccc')
    await expect(history(page)).toContainText('正在载入助手会话')
    await expect(history(page)).not.toContainText('之前保存的回答')
    await expect(history(page)).not.toContainText('另一次保存的回答')
    await expect(input(page)).toHaveCount(0)
    expect(await activeId(page)).toBe(conversationId)
    await page.screenshot({ path: testInfo.outputPath('legacy-switch-loading.png') })
  } finally { release() }
  await expect(history(page)).toContainText('另一次保存的回答')
  await expect(history(page)).not.toContainText('之前保存的回答')
  await historyToggle.click()
  await expect(historyToggle).toHaveAttribute('aria-pressed', 'false')
  await expect(input(page)).toHaveValue('读取旧对话时保留的草稿')
  await expect(history(page)).toContainText('现在继续的全局对话')
  await expect(history(page)).not.toContainText('之前保存的回答')
  await expect(history(page)).not.toContainText('另一次保存的回答')
  expect(fixture.legacyReads).toEqual(['/api/v1/assistant/legacy-conversations', '/api/v1/assistant/legacy-conversations/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', '/api/v1/assistant/legacy-conversations', '/api/v1/assistant/legacy-conversations/cccccccc-cccc-4ccc-8ccc-cccccccccccc'])
  expect(fixture.sends).toHaveLength(1)
  expect(fixture.conversations).toHaveLength(1)
  expect(fixture.unexpected).toEqual([])
})

test('retry after a page change uses the original conversation, identity and exact message context', async ({ page }) => {
  const fixture = await assistantFixture(page)
  await page.goto(`/projects/${projectId}/studio/${chapterId}`)
  await openAssistant(page)
  fixture.setFailure('reject')
  await input(page).fill('只能按第一章上下文重试')
  await page.getByRole('button', { name: '发送给 Gura', exact: true }).click()
  await expect(input(page)).toHaveValue('只能按第一章上下文重试')
  const original = fixture.sends[0]
  expect(original.payload.context).toEqual({ kind: 'project', project_id: projectId, chapter_id: chapterId, current_view: 'Outline' })
  await page.getByRole('button', { name: '收起助手', exact: true }).click()
  await page.getByRole('button', { name: '展开章节侧边栏', exact: true }).click()
  await page.getByRole('button', { name: '第2话 潮水来信', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/studio/${otherChapterId}`))
  await openAssistant(page)
  await input(page).fill('第二章新草稿')
  fixture.setFailure('none')
  await page.getByRole('button', { name: '重试未确认的消息', exact: true }).click()
  await expect(page.getByRole('button', { name: '重试未确认的消息', exact: true })).toHaveCount(0)
  expect(fixture.sends[1]).toEqual(original)
  await expect(input(page)).toHaveValue('第二章新草稿')
  await page.getByRole('button', { name: '发送给 Gura', exact: true }).click()
  await expect(history(page)).toContainText('HTTP 测试回复（project）：第二章新草稿')
  expect(fixture.sends[2].conversationId).toBe(original.conversationId)
  expect(fixture.sends[2].payload.context).toEqual({ kind: 'project', project_id: projectId, chapter_id: otherChapterId, current_view: 'Outline' })
  expect(fixture.unexpected).toEqual([])
})

test('home errors preserve its message and refresh retries with the same identity and context', async ({ page }) => {
  const fixture = await assistantFixture(page)
  fixture.setFailure('reject')
  await page.goto('/')
  await openAssistant(page)
  await input(page).fill('服务恢复后再问这个问题')
  await page.getByRole('button', { name: '发送给 Gura', exact: true }).click()
  await expect(input(page)).toHaveValue('服务恢复后再问这个问题')
  await expect(history(page)).toContainText(/暂不可用|发送失败|无法连接/)
  const original = fixture.sends[0]
  await page.reload()
  await expect(input(page)).toHaveValue('服务恢复后再问这个问题')
  fixture.setFailure('none')
  await page.getByRole('button', { name: '重试未确认的消息', exact: true }).click()
  await expect(history(page)).toContainText('HTTP 测试回复（home）：服务恢复后再问这个问题')
  await expect(input(page)).toHaveValue('')
  expect(fixture.sends).toEqual([original, original])
  await expect(history(page).locator('.studio-assistant-message.is-user')).toHaveCount(1)
  expect(fixture.unexpected).toEqual([])
})

test('an unavailable conversation service preserves the draft until recovery', async ({ page }) => {
  const fixture = await assistantFixture(page)
  await page.goto('/')
  await openAssistant(page)
  await send(page, '载入错误前已保存的问题')
  await input(page).fill('服务尚未恢复时写下的问题')
  fixture.setLoadFailure(true)
  await page.reload()
  await expect(history(page)).toContainText(/暂不可用|无法连接/)
  await expect(input(page)).toHaveValue('服务尚未恢复时写下的问题')
  fixture.setLoadFailure(false)
  await page.getByRole('button', { name: '重新载入会话', exact: true }).click()
  await expect(history(page)).toContainText('载入错误前已保存的问题')
  await send(page, '服务尚未恢复时写下的问题')
  expect(fixture.conversations).toHaveLength(1)
  expect(fixture.unexpected).toEqual([])
})
for (const viewport of [{ width: 1440, height: 900 }, { width: 926, height: 716 }]) {
  test(`project detail keeps its original layout without Gura and restores the bookshelf chat at ${viewport.width}x${viewport.height}`, async ({ page }, testInfo) => {
    const fixture = await assistantFixture(page)
    await page.setViewportSize(viewport)
    await page.goto('/')
    await openAssistant(page)
    await send(page, '详情打开前的书架对话')
    const conversationId = await activeId(page)
    const panel = page.getByRole('complementary', { name: 'Gura 助手', exact: true })
    const width = page.getByRole('separator', { name: '调整 Gura 宽度', exact: true })
    const height = page.getByRole('separator', { name: '调整 Gura 高度', exact: true })
    for (let count = 0; count < 9; count++) await width.press('Shift+ArrowLeft')
    for (let count = 0; count < 10; count++) await height.press('Shift+ArrowUp')
    await expect.poll(async () => (await panel.boundingBox())!.width).toBeCloseTo(Math.min(560, viewport.width * .45), 0)
    await expect.poll(async () => (await panel.boundingBox())!.height).toBeCloseTo(Math.min(720, viewport.height * .8), 0)
    const savedPreference = await page.evaluate(() => JSON.parse(localStorage.getItem('guranovel_assistant_size')!))
    await input(page).fill('详情关闭后仍保留的书架草稿')
    await page.getByRole('button', { name: `Open ${projects[0].title}`, exact: true }).first().click()
    const detail = page.getByRole('dialog', { name: projects[0].title, exact: true })
    await expect(detail).toBeVisible()
    await expect(page.locator('.studio-assistant')).toHaveCount(0)
    await expect.poll(async () => (await detail.boundingBox())!.width).toBeCloseTo(Math.min(1120, viewport.width - 48), 0)
    await expect.poll(async () => (await detail.boundingBox())!.height).toBeCloseTo(Math.min(760, viewport.height - 48), 0)
    const padding = await page.locator('.novel-dialog-layout').evaluate(element => {
      const style = getComputedStyle(element)
      return { left: parseFloat(style.paddingLeft), right: parseFloat(style.paddingRight) }
    })
    expect(padding.right).toBeCloseTo(padding.left, 0)
    await page.getByRole('button', { name: '编辑资料', exact: true }).click()
    await expect(page.getByRole('textbox', { name: '编辑标题', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '章节目录', exact: true }).click()
    await expect(page.getByRole('button', { name: /第1话.*抵达雾港/ })).toBeVisible()
    await page.reload()
    await expect(detail).toBeVisible()
    await expect(page.locator('.studio-assistant')).toHaveCount(0)
    await expect.poll(async () => (await detail.boundingBox())!.width).toBeCloseTo(Math.min(1120, viewport.width - 48), 0)
    await page.screenshot({ path: testInfo.outputPath('detail-without-assistant.png') })
    expect(await page.evaluate(() => document.documentElement.scrollHeight - innerHeight)).toBe(0)
    expect(await activeId(page)).toBe(conversationId)
    await page.getByRole('button', { name: '关闭作品详情', exact: true }).click()
    await expect(page).toHaveURL('/')
    await openAssistant(page)
    await expect(history(page)).toContainText('详情打开前的书架对话')
    await expect(input(page)).toHaveValue('详情关闭后仍保留的书架草稿')
    await expect.poll(async () => (await panel.boundingBox())!.width).toBeCloseTo(savedPreference.width, 0)
    await expect.poll(async () => (await panel.boundingBox())!.height).toBeCloseTo(savedPreference.height, 0)
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('guranovel_assistant_size')!))).toEqual(savedPreference)
    await input(page).focus()
    await expect(input(page)).toHaveCSS('outline-style', 'none')
    await page.getByRole('button', { name: `Open ${projects[0].title}`, exact: true }).first().click()
    await expect(detail).toBeVisible()
    await expect(page.locator('.studio-assistant')).toHaveCount(0)
    await page.getByRole('button', { name: '关闭作品详情', exact: true }).press('Escape')
    await expect(page).toHaveURL('/')
    await expect(detail).toHaveCount(0)
    await openAssistant(page)
    await expect(input(page)).toHaveValue('详情关闭后仍保留的书架草稿')
    expect(await activeId(page)).toBe(conversationId)
    expect(fixture.sends).toHaveLength(1)
    expect(fixture.conversations).toHaveLength(1)
    expect(fixture.unexpected).toEqual([])
  })
}
for (const location of ['home', 'project'] as const) {
  test(`${location} reload reconciles a saved message after a lost acknowledgment without resending`, async ({ page }) => {
    const fixture = await assistantFixture(page)
    fixture.setFailure('lost-ack')
    await page.goto(location === 'home' ? '/' : `/projects/${projectId}/studio/${chapterId}`)
    if (location === 'project') await expect(page.getByRole('region', { name: '本章大纲', exact: true })).toBeVisible()
    await openAssistant(page)
    await input(page).fill('刷新后核对已保存的消息')
    await page.getByRole('button', { name: '发送给 Gura', exact: true }).click()
    await expect(input(page)).toHaveValue('刷新后核对已保存的消息')
    expect(fixture.sends).toHaveLength(1)
    const conversationId = await activeId(page)
    fixture.setFailure('none')
    await page.reload()
    await expect(history(page)).toContainText(`HTTP 测试回复（${location}）：刷新后核对已保存的消息`)
    await expect(input(page)).toHaveValue('')
    await expect(history(page).locator('.studio-assistant-message.is-user')).toHaveCount(1)
    expect(await activeId(page)).toBe(conversationId)
    expect(fixture.sends).toHaveLength(1)
    expect(fixture.unexpected).toEqual([])
  })
}
