import { expect, test, type Page } from '@playwright/test'

const collectionId = '11111111-1111-4111-8111-111111111111'
const docId = '22222222-2222-4222-8222-222222222222'
const worldId = '44444444-4444-4444-8444-444444444444'
const defaultConversationId = '77777777-7777-4777-8777-000000000001'
const stamp = '2026-10-20T00:00:00Z'

test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.status === testInfo.expectedStatus || page.isClosed()) return
  const focus = await page.evaluate(() => {
    const element = document.activeElement
    return { tag: element?.tagName, role: element?.getAttribute('role'), label: element?.getAttribute('aria-label'),
      className: element?.getAttribute('class'), text: element?.textContent?.slice(0, 100) }
  })
  console.log('Active element after failure:', JSON.stringify(focus))
  await testInfo.attach('active-element', { body: JSON.stringify(focus, null, 2), contentType: 'application/json' })
  await page.screenshot({ path: testInfo.outputPath('failure.png'), fullPage: true })
})

async function openCollectionCollaboration(page: Page) {
  await page.getByRole('button', { name: '设定集操作', exact: true }).click()
  await page.getByRole('menu', { name: '设定集操作', exact: true }).getByRole('menuitem', { name: '与设定 Agent 协作', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '设定 Agent 协作', exact: true })).toBeVisible()
}

async function selectAgentConversation(page: Page, title: string) {
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作', exact: true })
  const toggle = panel.getByRole('button', { name: '历史对话', exact: true })
  if (await toggle.getAttribute('aria-pressed') !== 'true') await toggle.click()
  await panel.getByRole('region', { name: '设定 Agent 历史对话', exact: true }).getByRole('button', { name: `继续对话：${title}`, exact: true }).click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
}
type DocumentFixture = { id: string; project_id: null; chapter_id: null; setting_collection_id: string; title: string; type: string; path: string; current_version_id: string; current_version: null; metadata: object; created_at: string; updated_at: string }
type MessageFixture = { id: string; role: 'user' | 'assistant'; content: string; client_message_id: string | null; attempt_id: string | null; proposal: null | { id: string; setting_collection_id: string; target_document_id: string | null; base_version_id: string | null; title: string; category: string; proposed_content: string; reason: string; status: 'pending' | 'accepted'; created_at: string; source_task: { agent_role: string; workflow_run_id: null; chapter_id: null; novel_title: null } }; proposal_status: 'pending' | 'accepted' | null; base_content: string | null; base_title: string | null; accepted_version_id: string | null; created_at: string }
type SendFixture = { client_message_id: string; content: string }
type AttemptFixture = { id: string; client_message_id: string; content: string; request: SendFixture; status: 'prepared' | 'dispatched' | 'result_saved' | 'completed' | 'finished'; error_code: string | null; provenance: null; has_result: boolean; user_message_id: string | null; assistant_message_id: string | null; created_at: string; updated_at: string }
type ReplyFixture = 'complete' | 'lost_complete' | 'prepared' | 'dispatched_user' | 'result_saved'
type ConversationFixture = { id: string; title: string; is_default: boolean; client_request_id: string | null; setting_collection_id: string; document_id: null; agent_role: 'lore_agent'; messages: MessageFixture[]; attempts: AttemptFixture[]; created_at: string; updated_at: string }

async function authoringBackend(page: Page, existing = false, multiple = false, legacyWorld = false) {
  let revision = 1, waiting: Promise<void> | null = null
  let release: () => void = () => {}
  let nextReply: ReplyFixture = 'complete', beforeDispatch: Promise<void> | null = null
  let releaseDispatch: () => void = () => {}
  let loseCreateAck = false, failHistory = false, attemptNumber = 0
  let heldCreate: Promise<void> | null = null
  const heldReads = new Map<string, Promise<void>>()
  const state = { creates: 0, sends: 0, providerCalls: 0, checks: 0, resumes: 0, finishes: 0, accepts: 0, sessions: 0, latePosts: 0,
    body: existing ? '港口在黄昏关闭。' : '', docs: [] as DocumentFixture[], messages: [] as MessageFixture[], attempts: [] as AttemptFixture[],
    sentPayloads: [] as SendFixture[], finishedPayloads: [] as SendFixture[], sendConversations: [] as string[], acceptConversations: [] as string[],
    conversationCreates: [] as string[], conversationReads: [] as string[], historyReads: [] as string[], conversations: new Map<string, ConversationFixture>() }
  const versionId = () => `33333333-3333-4333-8333-${String(revision).padStart(12, '0')}`
  const document = (title: string, metadata: object = { category: 'setting' }): DocumentFixture => ({ id: docId, project_id: null, chapter_id: null, setting_collection_id: collectionId,
    title, type: 'character_profile', path: 'setting/harbor.md', current_version_id: versionId(), current_version: null, metadata, created_at: stamp, updated_at: stamp })
  if (existing) state.docs.push(legacyWorld ? { ...document('旧港', {}), type: 'factions' } : document('旧港'))
  if (multiple) state.docs.push({ ...document('潮汐法则', { category: 'world' }), id: worldId, type: 'world_overview', path: 'world/tides.md' })
  const defaultConversation: ConversationFixture = { id: defaultConversationId, title: '新对话', is_default: true, client_request_id: null,
    setting_collection_id: collectionId, document_id: null, agent_role: 'lore_agent', messages: state.messages, attempts: state.attempts, created_at: stamp, updated_at: stamp }
  state.conversations.set(defaultConversation.id, defaultConversation)
  function createConversation(clientId: string): ConversationFixture {
    const found = [...state.conversations.values()].find(item => item.client_request_id === clientId)
    if (found) return found
    const conversation: ConversationFixture = { ...defaultConversation, id: `77777777-7777-4777-8777-${String(state.conversations.size + 1).padStart(12, '0')}`,
      title: '新对话', is_default: false, client_request_id: clientId, messages: [], attempts: [] }
    state.conversations.set(conversation.id, conversation)
    return conversation
  }
  // This deterministic HTTP fixture exercises browser recovery, not model quality or backend idempotency.
  const results = new Map<string, MessageFixture[]>()
  const attemptOwners = new Map<string, ConversationFixture>()
  function prepare(payload: SendFixture, owner = defaultConversation): AttemptFixture {
    const attempt: AttemptFixture = { id: `55555555-5555-4555-8555-${String(++attemptNumber).padStart(12, '0')}`,
      client_message_id: payload.client_message_id, content: payload.content, request: { ...payload }, status: 'prepared', error_code: null,
      provenance: null, has_result: false, user_message_id: null, assistant_message_id: null, created_at: stamp, updated_at: stamp }
    if (!owner.attempts.length) owner.title = payload.content.slice(0, 48)
    owner.attempts.push(attempt); attemptOwners.set(attempt.id, owner)
    return attempt
  }
  function dispatch(attempt: AttemptFixture) {
    state.providerCalls++
    attempt.status = 'dispatched'
    const base = state.docs.find(doc => doc.id === docId), number = Number(attempt.id.slice(-12))
    const baseBody = base ? state.body : '', baseTitle = base?.title || '', baseVersion = base?.current_version_id || null
    const metadataCategory = (base?.metadata as { category?: string } | undefined)?.category
    const baseCategory = metadataCategory || (base && ['world_overview', 'power_system', 'factions', 'geography', 'history'].includes(base.type) ? 'world' : 'setting')
    results.set(attempt.id, [
      { id: `author-${number}`, role: 'user', content: attempt.content, client_message_id: attempt.client_message_id, attempt_id: attempt.id,
        proposal: null, proposal_status: null, base_content: null, base_title: null, accepted_version_id: null, created_at: stamp },
      { id: `agent-${number}`, role: 'assistant', content: '设定 Agent 提出了灯塔的退潮规则。', client_message_id: null, attempt_id: attempt.id,
        proposal: { id: `proposal-${number}`, setting_collection_id: collectionId, target_document_id: base?.id || null, base_version_id: baseVersion, title: baseTitle || '夜航灯塔', category: baseCategory,
          proposed_content: base ? `${baseBody}\n灯塔只在退潮时亮起。` : '灯塔只在退潮时亮起，为夜航船只指引回港的方向。', reason: '让灯塔与港口的潮汐产生联系。', status: 'pending', created_at: stamp,
          source_task: { agent_role: 'lore_agent', workflow_run_id: null, chapter_id: null, novel_title: null } },
        proposal_status: 'pending', base_content: baseBody, base_title: baseTitle, accepted_version_id: null, created_at: stamp },
    ])
  }
  function complete(attempt: AttemptFixture) {
    if (attempt.status === 'finished') return
    const pair = results.get(attempt.id)!, owner = attemptOwners.get(attempt.id)!
    for (const message of pair) if (!owner.messages.some(item => item.id === message.id)) owner.messages.push(message)
    attempt.status = 'completed'; attempt.has_result = true; attempt.error_code = null
    attempt.user_message_id = pair[0].id; attempt.assistant_message_id = pair[1].id
  }
  let globalMessages: object[] = []
  const globalConversation = () => ({ id: 'global-conversation', title: '全局 Gura 对话', project_id: null, setting_collection_id: null, messages: globalMessages, created_at: stamp, updated_at: stamp })
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), method = request.method()
    let path = new URL(request.url()).pathname
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
    const root = `/api/v1/setting-collections/${collectionId}/collaboration`
    if (path === `${root}/conversations`) {
      if (method === 'POST') {
        const payload = request.postDataJSON()
        expect(Object.keys(payload)).toEqual(['client_request_id'])
        expect(payload.client_request_id).toMatch(/^[0-9a-f-]{36}$/i)
        state.conversationCreates.push(payload.client_request_id)
        const created = createConversation(payload.client_request_id)
        if (heldCreate) await heldCreate
        if (loseCreateAck) { loseCreateAck = false; return route.abort('connectionfailed') }
        return json(created)
      }
      expect(method).toBe('GET')
      state.historyReads.push(new URL(request.url()).search)
      if (failHistory) return json({ error: { code: 'unavailable', message: '历史列表暂不可用。' } }, 503)
      const params = new URL(request.url()).searchParams, limit = Number(params.get('limit') ?? 30), offset = Number(params.get('offset') ?? 0)
      expect(limit).toBe(30)
      const summaries = [...state.conversations.values()].reverse().filter(item => item.messages.length > 0 || item.attempts.length > 0).map(item => ({ id: item.id, title: item.title, created_at: item.created_at,
        updated_at: item.updated_at, message_count: item.messages.length, preview: item.messages.at(-1)?.content || item.attempts.at(-1)?.content || '', is_default: item.is_default }))
      return json({ items: summaries.slice(offset, offset + limit), next_offset: offset + limit < summaries.length ? offset + limit : null })
    }
    let owner = defaultConversation
    const explicit = path.match(new RegExp(`^${root}/conversations/([^/]+)(.*)$`))
    if (explicit) {
      const found = state.conversations.get(explicit[1])
      if (!found) return json({ error: { code: 'not_found', message: '指定会话不存在。' } }, 404)
      owner = found; path = `${root}${explicit[2]}`
    }
    const conversation = () => owner
    if (path === root) {
      state.sessions++; state.conversationReads.push(owner.id)
      const snapshot = structuredClone(owner), gate = heldReads.get(owner.id)
      if (gate) await gate
      return json(snapshot)
    }
    if (path === `/api/v1/setting-collections/${collectionId}/collaboration/messages`) {
      const payload: SendFixture = request.postDataJSON(), reply = nextReply
      nextReply = 'complete'; state.sends++; state.sentPayloads.push(payload); state.sendConversations.push(owner.id)
      // The server chooses the target from its collection snapshot; the browser cannot invent model context.
      expect(Object.keys(payload).sort()).toEqual(['client_message_id', 'content'])
      if (beforeDispatch) { const gate = beforeDispatch; beforeDispatch = null; await gate; state.latePosts++ }
      const found = owner.attempts.find(item => item.client_message_id === payload.client_message_id)
      if (found) { expect(found.request).toEqual(payload); return json(conversation()) }
      const attempt = prepare(payload, owner)
      if (reply === 'prepared') return json({ error: { code: 'setting_collaboration_failed', message: '尚未调用设定 Agent。' } }, 503)
      dispatch(attempt)
      if (waiting) { const gate = waiting; waiting = null; await gate }
      if (reply === 'dispatched_user') {
        owner.messages.push({ id: 'earlier-agent', role: 'assistant', content: '旧讨论的回复不属于本次请求。', client_message_id: null, attempt_id: null,
          proposal: null, proposal_status: null, base_content: null, base_title: null, accepted_version_id: null, created_at: stamp }, results.get(attempt.id)![0])
        attempt.error_code = 'provider_response_unknown'
        return json({ error: { code: 'provider_response_unknown', message: '生成结果尚未确认。' } }, 503)
      }
      if (reply === 'result_saved') {
        attempt.status = 'result_saved'; attempt.has_result = true
        return route.abort('connectionfailed')
      }
      complete(attempt)
      if (reply === 'lost_complete') return route.abort('connectionfailed')
      return json(conversation())
    }
    if (path === `/api/v1/setting-collections/${collectionId}/collaboration/attempts/finish`) {
      const payload: SendFixture = request.postDataJSON()
      state.finishes++; state.finishedPayloads.push(payload)
      const attempt = owner.attempts.find(item => item.client_message_id === payload.client_message_id) || prepare(payload, owner)
      expect(attempt.request).toEqual(payload)
      if (attempt.status !== 'completed') attempt.status = 'finished'
      return json(conversation())
    }
    const attemptPath = path.match(/\/collaboration\/attempts\/([^/]+)(\/resume)?$/)
    if (attemptPath) {
      const attempt = owner.attempts.find(item => item.id === attemptPath[1])
      expect(attempt).toBeDefined()
      if (!attempt) return json({ error: { code: 'not_found', message: '请求不存在' } }, 404)
      if (attemptPath[2]) {
        expect(method).toBe('POST'); state.resumes++
        if (attempt.status === 'prepared') { dispatch(attempt); complete(attempt) }
        else if (attempt.status === 'result_saved') complete(attempt)
        return json(conversation())
      }
      expect(method).toBe('GET'); state.checks++
      return json(attempt)
    }
    if (path.match(/\/collaboration\/proposals\/proposal-\d+\/accept$/)) {
      const payload = request.postDataJSON(), proposalId = path.split('/').at(-2), message = owner.messages.find(item => item.proposal?.id === proposalId)!
      const target = state.docs.find(doc => doc.id === message.proposal!.target_document_id)
      expect(payload).toEqual({ expected_current_version_id: target?.current_version_id || null, expected_draft_content: target ? state.body : '', expected_title: target?.title || '' })
      state.accepts++; state.acceptConversations.push(owner.id); revision++; state.body = message.proposal!.proposed_content
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
  return { state,
    respondNextAs(reply: ReplyFixture) { nextReply = reply },
    loseNextCreateAck() { loseCreateAck = true },
    holdCreateAck() {
      let releaseCreate!: () => void
      heldCreate = new Promise(resolve => { releaseCreate = resolve })
      return () => { heldCreate = null; releaseCreate() }
    },
    failHistory(fail: boolean) { failHistory = fail },
    seedConversation(clientId: string) { return createConversation(clientId) },
    seedCompleted(payload: SendFixture, conversationId = defaultConversationId) {
      const attempt = prepare(payload, state.conversations.get(conversationId)!)
      dispatch(attempt); complete(attempt)
    },
    holdConversationRead(conversationId: string) {
      let releaseRead!: () => void
      heldReads.set(conversationId, new Promise(resolve => { releaseRead = resolve }))
      return () => { heldReads.delete(conversationId); releaseRead() }
    },
    seedUnfinished(payload: SendFixture, status: 'prepared' | 'result_saved') {
      const attempt = prepare(payload)
      if (status === 'result_saved') { dispatch(attempt); attempt.status = 'result_saved'; attempt.has_result = true }
    },
    completeLatest() { complete(state.attempts.at(-1)!) },
    holdReply() { waiting = new Promise(resolve => { release = resolve }); return () => release() },
    holdBeforeDispatch() { beforeDispatch = new Promise(resolve => { releaseDispatch = resolve }); return () => releaseDispatch() },
  }
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

test('a prepared request survives refresh and calls the fixture provider only after explicit continuation', async ({ page }) => {
  const backend = await authoringBackend(page, true)
  backend.respondNextAs('prepared')
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' })
  await draft.fill('请讨论港口的潮汐约定。')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect(panel.getByRole('alert')).toBeVisible()
  expect(backend.state.sends).toBe(1)
  expect(backend.state.providerCalls).toBe(0)
  const original = { ...backend.state.sentPayloads[0] }
  // The server must recover the unfinished request even without this tab's local journal.
  await page.evaluate(({ collection, conversation }) => {
    sessionStorage.removeItem(`guranovel:setting-agent:collection:${collection}`)
    sessionStorage.removeItem(`guranovel:setting-agent:collection:${collection}:conversation:${conversation}`)
  }, { collection: collectionId, conversation: defaultConversationId })
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(draft).toHaveValue(original.content)
  await expect(panel.getByRole('button', { name: '继续发送', exact: true })).toBeVisible()
  expect(backend.state.providerCalls).toBe(0)
  expect(backend.state.resumes).toBe(0)
  await panel.getByRole('button', { name: '继续发送', exact: true }).click()
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await expect(draft).toHaveValue('')
  expect(backend.state.attempts).toHaveLength(1)
  expect(backend.state.attempts[0]).toMatchObject({ request: original, status: 'completed' })
  expect(backend.state.sends).toBe(1)
  expect(backend.state.resumes).toBe(1)
  expect(backend.state.providerCalls).toBe(1)
  expect(backend.state.accepts).toBe(0)
  expect(backend.state.body).toBe('港口在黄昏关闭。')
})

for (const scenario of [
  { status: 'prepared', draft: '我后来写的新构想还没有发送。' },
  { status: 'result_saved', draft: '旧请求：请讨论港口潮汐。' },
] as const) test(`a server-only ${scenario.status} request preserves a local newer draft through completion and refresh`, async ({ page }) => {
  const backend = await authoringBackend(page, true)
  const original = { client_message_id: '66666666-6666-4666-8666-666666666666', content: '旧请求：请讨论港口潮汐。' }
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' })
  await expect(panel.getByRole('heading', { name: '一起构建这个世界' })).toBeVisible()
  // This draft has never been sent; the unfinished request was created by another tab.
  await draft.fill(scenario.draft)
  await panel.getByRole('button', { name: '关闭设定协作', exact: true }).click()
  await expect(panel).not.toBeAttached()
  expect(backend.state.sends).toBe(0)
  backend.seedUnfinished(original, scenario.status)
  await openCollectionCollaboration(page)
  const resume = panel.getByRole('button', { name: scenario.status === 'prepared' ? '继续发送' : '恢复结果', exact: true })
  await expect(resume).toBeVisible()
  await expect(draft).toHaveValue(scenario.draft)
  await expect(panel.getByRole('button', { name: '发送给设定 Agent', exact: true })).toBeDisabled()
  await resume.click()
  await expect(panel.getByRole('log').getByText(original.content, { exact: true })).toHaveCount(1)
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await expect(draft).toHaveValue(scenario.draft)
  await expect(panel.getByRole('button', { name: '发送给设定 Agent', exact: true })).toBeEnabled()
  expect(backend.state.resumes).toBe(1)
  expect(backend.state.providerCalls).toBe(1)
  expect(backend.state.sends).toBe(0)
  expect(backend.state.messages).toHaveLength(2)
  expect(backend.state.attempts[0]).toMatchObject({ status: 'completed', request: original })
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await expect(draft).toHaveValue(scenario.draft)
  await expect(panel.getByRole('button', { name: '发送给设定 Agent', exact: true })).toBeEnabled()
  expect(backend.state.resumes).toBe(1)
  expect(backend.state.providerCalls).toBe(1)
  expect(backend.state.sends).toBe(0)
  expect(backend.state.accepts).toBe(0)
  expect(backend.state.body).toBe('港口在黄昏关闭。')
})

test('an unpaired user record stays unconfirmed after refresh and checking the same attempt never sends again', async ({ page }, testInfo) => {
  const backend = await authoringBackend(page, true)
  backend.respondNextAs('dispatched_user')
  const release = backend.holdReply()
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' })
  await draft.fill('请保留这一条尚未确认的请求。')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect.poll(() => backend.state.providerCalls).toBe(1)
  await expect(panel.getByRole('status').filter({ hasText: '设定 Agent 正在生成，离开后可检查结果。' })).toBeVisible()
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(panel.getByRole('button', { name: '检查结果', exact: true })).toBeVisible()
  await expect(panel.getByRole('button', { name: '发送给设定 Agent', exact: true })).toBeDisabled()
  await expect(draft).toHaveValue('请保留这一条尚未确认的请求。')
  await expect(panel.getByRole('status').filter({ hasText: '请求已发出，结果尚未确认；请检查结果或结束等待。' })).toBeVisible()
  expect(backend.state.attempts[0]).toMatchObject({ status: 'dispatched', error_code: null })
  expect(backend.state.messages).toHaveLength(0)
  expect(backend.state.sends).toBe(1)
  expect(backend.state.providerCalls).toBe(1)
  await page.screenshot({ path: testInfo.outputPath('in-flight-after-refresh-uncertain.png'), fullPage: true })
  release()
  await expect.poll(() => backend.state.messages.length).toBe(2)
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(panel.getByRole('log')).toContainText('旧讨论的回复不属于本次请求。')
  await expect(panel.getByRole('log')).toContainText('请保留这一条尚未确认的请求。')
  await expect(draft).toHaveValue('请保留这一条尚未确认的请求。')
  await expect(panel.getByRole('button', { name: '发送给设定 Agent', exact: true })).toBeDisabled()
  await expect(panel.getByRole('button', { name: '结束等待', exact: true })).toBeVisible()
  const checked = backend.state.checks
  await panel.getByRole('button', { name: '检查结果', exact: true }).click()
  await expect.poll(() => backend.state.checks).toBe(checked + 1)
  await expect(panel.getByRole('button', { name: '检查结果', exact: true })).toBeEnabled()
  await expect(panel.getByRole('button', { name: '发送给设定 Agent', exact: true })).toBeDisabled()
  expect(backend.state.providerCalls).toBe(1)
  expect(backend.state.sends).toBe(1)
  expect(backend.state.resumes).toBe(0)
  expect(backend.state.messages).toHaveLength(2)
  await page.screenshot({ path: testInfo.outputPath('unconfirmed-user-only.png'), fullPage: true })
  await draft.fill('我后来写的另一条想法仍要保留。')
  backend.completeLatest()
  await panel.getByRole('button', { name: '检查结果', exact: true }).click()
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await expect(draft).toHaveValue('我后来写的另一条想法仍要保留。')
  await expect(panel.getByRole('button', { name: '发送给设定 Agent', exact: true })).toBeEnabled()
  expect(backend.state.messages).toHaveLength(3)
  expect(backend.state.providerCalls).toBe(1)
  expect(backend.state.sends).toBe(1)
  expect(backend.state.accepts).toBe(0)
})

test('a saved result resumes without another provider call and cannot overwrite a newer manuscript or draft', async ({ page }, testInfo) => {
  const backend = await authoringBackend(page, true)
  backend.respondNextAs('result_saved')
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  const body = page.getByLabel('设定正文', { exact: true })
  await expect(body).toHaveText('港口在黄昏关闭。')
  const originalVersion = backend.state.docs[0].current_version_id
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' })
  await draft.fill('讨论港口的灯塔。')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect(panel.getByRole('alert')).toBeVisible()
  await panel.getByRole('button', { name: '关闭设定协作', exact: true }).click()
  await expect(panel).not.toBeAttached()
  await body.fill('港口彻夜开放，这是作者刚保存的新正文。')
  await expect.poll(() => backend.state.body).toBe('港口彻夜开放，这是作者刚保存的新正文。')
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(panel.getByRole('button', { name: '恢复结果', exact: true })).toBeVisible()
  expect(backend.state.messages).toHaveLength(0)
  expect(backend.state.providerCalls).toBe(1)
  expect(backend.state.resumes).toBe(0)
  await draft.fill('下一条输入与旧结果分开保留。')
  await panel.getByRole('button', { name: '恢复结果', exact: true }).click()
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await expect(draft).toHaveValue('下一条输入与旧结果分开保留。')
  await panel.getByRole('button', { name: '比较建议', exact: true }).click()
  await expect(panel).toContainText('目标条目已更新，请基于设定集的最新内容重新讨论。')
  await expect(panel.getByRole('button', { name: '采纳建议', exact: true })).toHaveCount(0)
  await expect(panel).toContainText('港口彻夜开放，这是作者刚保存的新正文。')
  expect(backend.state.messages.at(-1)?.proposal?.base_version_id).toBe(originalVersion)
  expect(backend.state.docs[0].current_version_id).not.toBe(originalVersion)
  expect(backend.state.providerCalls).toBe(1)
  expect(backend.state.sends).toBe(1)
  expect(backend.state.resumes).toBe(1)
  expect(backend.state.accepts).toBe(0)
  await page.screenshot({ path: testInfo.outputPath('recovered-result-stale-proposal.png'), fullPage: true })
  await panel.getByRole('button', { name: '关闭设定协作', exact: true }).click()
  await expect(body).toHaveText('港口彻夜开放，这是作者刚保存的新正文。')
})

test('a completed reply lost in transit reloads once and retains later typing even when its text is identical', async ({ page }, testInfo) => {
  const backend = await authoringBackend(page, true)
  backend.respondNextAs('lost_complete')
  const release = backend.holdReply()
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' })
  const text = '我也想把同一句话作为下一条草稿。'
  await draft.fill(text)
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect.poll(() => backend.state.providerCalls).toBe(1)
  await draft.fill('先改成另一句，再输入原句。')
  await draft.fill(text)
  release()
  await expect(panel.getByRole('alert')).toBeVisible()
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(panel.getByRole('log').getByText(text, { exact: true })).toHaveCount(1)
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await expect(draft).toHaveValue(text)
  await expect(panel.getByRole('button', { name: '发送给设定 Agent', exact: true })).toBeEnabled()
  expect(backend.state.messages).toHaveLength(2)
  expect(backend.state.attempts).toHaveLength(1)
  expect(backend.state.sends).toBe(1)
  expect(backend.state.providerCalls).toBe(1)
  expect(backend.state.resumes).toBe(0)
  expect(backend.state.accepts).toBe(0)
  expect(backend.state.body).toBe('港口在黄昏关闭。')
  await page.screenshot({ path: testInfo.outputPath('lost-reply-recovered-same-text-draft.png'), fullPage: true })
})

test('ending an unknown request fences a late POST and only a later explicit send uses a new identity', async ({ page }, testInfo) => {
  const backend = await authoringBackend(page, true)
  const release = backend.holdBeforeDispatch()
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' })
  await draft.fill('原始请求尚未确认是否到达。')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect.poll(() => backend.state.sends).toBe(1)
  const original = { ...backend.state.sentPayloads[0] }
  expect(backend.state.providerCalls).toBe(0)
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(draft).toHaveValue(original.content)
  await expect(panel.getByRole('button', { name: '发送给设定 Agent', exact: true })).toBeDisabled()
  await panel.getByRole('button', { name: '结束等待', exact: true }).click()
  await expect(panel.getByRole('button', { name: '发送给设定 Agent', exact: true })).toBeEnabled()
  await expect(draft).toHaveValue(original.content)
  expect(backend.state.finishedPayloads).toEqual([original])
  expect(backend.state.attempts[0]).toMatchObject({ status: 'finished', request: original })
  await draft.fill('结束旧请求后保留我新写的构想。')
  release()
  await expect.poll(() => backend.state.latePosts).toBe(1)
  expect(backend.state.providerCalls).toBe(0)
  expect(backend.state.messages).toHaveLength(0)
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(draft).toHaveValue('结束旧请求后保留我新写的构想。')
  await expect(panel.getByRole('button', { name: '发送给设定 Agent', exact: true })).toBeEnabled()
  expect(backend.state.sends).toBe(1)
  expect(backend.state.providerCalls).toBe(0)
  await page.screenshot({ path: testInfo.outputPath('finished-request-retains-new-input.png'), fullPage: true })
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  expect(backend.state.sentPayloads).toHaveLength(2)
  expect(backend.state.sentPayloads[1].client_message_id).not.toBe(original.client_message_id)
  expect(backend.state.sentPayloads[1].content).toBe('结束旧请求后保留我新写的构想。')
  expect(backend.state.providerCalls).toBe(1)
  expect(backend.state.resumes).toBe(0)
  expect(backend.state.finishes).toBe(1)
  expect(backend.state.accepts).toBe(0)
  expect(backend.state.body).toBe('港口在黄昏关闭。')
})

test('unused local new discussions survive history round trips without creating sessions or blank history rows', async ({ page }) => {
  const backend = await authoringBackend(page, true)
  backend.seedCompleted({ client_message_id: 'used-history', content: '已使用的设定讨论' })
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' })
  await expect(panel.getByRole('log')).toContainText('已使用的设定讨论')
  for (let index = 0; index < 3; index++) {
    await panel.getByRole('button', { name: '新建对话', exact: true }).click()
    await expect(panel.getByRole('heading', { name: '一起构建这个世界' })).toBeVisible()
    await expect(draft).toHaveValue('')
    expect(backend.state.conversationCreates).toHaveLength(0)
    await panel.getByRole('button', { name: '历史对话', exact: true }).click()
    const history = panel.getByRole('region', { name: '设定 Agent 历史对话', exact: true })
    await expect(history.getByRole('button', { name: '继续对话：已使用的设定讨论', exact: true })).toBeVisible()
    await expect(history.getByRole('button', { name: '继续对话：新对话', exact: true })).toHaveCount(0)
    await panel.getByRole('button', { name: '新建对话', exact: true }).click()
    await expect(draft).toHaveValue('')
    expect(backend.state.conversationCreates).toHaveLength(0)
    await selectAgentConversation(page, '已使用的设定讨论')
    await expect(panel.getByRole('log')).toContainText('已使用的设定讨论')
  }
  await panel.getByRole('button', { name: '新建对话', exact: true }).click()
  await draft.fill('本地新讨论尚未发送的构想。')
  await selectAgentConversation(page, '已使用的设定讨论')
  await panel.getByRole('button', { name: '新建对话', exact: true }).click()
  await expect(draft).toHaveValue('本地新讨论尚未发送的构想。')
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(draft).toHaveValue('本地新讨论尚未发送的构想。')
  await panel.getByRole('button', { name: '历史对话', exact: true }).click()
  const history = panel.getByRole('region', { name: '设定 Agent 历史对话', exact: true })
  await expect(history.getByRole('button', { name: /^继续对话：/ })).toHaveCount(1)
  await expect(history.getByRole('button', { name: '继续对话：新对话', exact: true })).toHaveCount(0)
  expect(backend.state.conversations.size).toBe(1)
  expect(backend.state.conversationCreates).toHaveLength(0)
  expect(backend.state.sends).toBe(0)
  expect(backend.state.providerCalls).toBe(1)
})

test('a previously selected empty server conversation remains writable without creating a replacement or showing a blank history row', async ({ page }) => {
  const backend = await authoringBackend(page, true)
  backend.seedCompleted({ client_message_id: 'used-before-legacy-empty', content: '原有的已使用讨论' })
  const legacyEmpty = backend.seedConversation('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'), text = '旧版空会话中保留的未发送输入。'
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await page.evaluate(({ collection, id, draft }) => {
    const key = `guranovel:setting-agent:collection:${collection}`
    sessionStorage.setItem(`${key}:active-conversation`, id)
    sessionStorage.setItem(`${key}:conversation:${id}`, JSON.stringify({ draft, revision: 1, pending: null, acceptance: null, scrollTop: 0 }))
  }, { collection: collectionId, id: legacyEmpty.id, draft: text })
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' }), toggle = panel.getByRole('button', { name: '历史对话', exact: true })
  await expect(draft).toHaveValue(text)
  expect(backend.state.conversationReads.at(-1)).toBe(legacyEmpty.id)
  await toggle.click()
  const history = panel.getByRole('region', { name: '设定 Agent 历史对话', exact: true })
  await expect(history.getByRole('button', { name: '继续对话：原有的已使用讨论', exact: true })).toBeVisible()
  await expect(history.getByRole('button', { name: '继续对话：新对话', exact: true })).toHaveCount(0)
  await toggle.click()
  await expect(draft).toHaveValue(text)
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect(panel.getByRole('log').getByText(text, { exact: true })).toHaveCount(1)
  await expect(draft).toHaveValue('')
  expect(backend.state.conversationCreates).toHaveLength(0)
  expect(backend.state.sendConversations).toEqual([legacyEmpty.id])
  await toggle.click()
  await expect(history.getByRole('button', { name: `继续对话：${text}`, exact: true })).toBeVisible()
  await toggle.click()
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(panel.getByRole('log')).toContainText(text)
  await expect(draft).toHaveValue('')
  expect(backend.state.conversationReads.at(-1)).toBe(legacyEmpty.id)
  expect(backend.state.sends).toBe(1)
  expect(backend.state.providerCalls).toBe(2)
  expect(backend.state.body).toBe('港口在黄昏关闭。')
})

test('setting Agent history toggles with the same icon and restores separate conversations, drafts and the selected session after refresh', async ({ page }) => {
  const backend = await authoringBackend(page, true)
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' }), log = panel.getByRole('log')
  await draft.fill('第一段设定讨论')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect(log).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await draft.fill('第一段讨论尚未发送的草稿。')
  await panel.getByRole('button', { name: '新建对话', exact: true }).click()
  await expect(panel.getByRole('heading', { name: '一起构建这个世界' })).toBeVisible()
  await expect(draft).toHaveValue('')
  await expect(panel.getByRole('button', { name: '比较建议', exact: true })).toHaveCount(0)
  await expect(log).not.toContainText('第一段设定讨论')
  await draft.fill('第二段设定讨论')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect(log).toContainText('第二段设定讨论')
  await expect(panel.getByRole('button', { name: '比较建议', exact: true })).toHaveCount(1)
  const second = [...backend.state.conversations.values()].find(item => !item.is_default)!
  await draft.fill('第二段讨论自己的草稿。')
  const reads = backend.state.conversationReads.length, toggle = panel.getByRole('button', { name: '历史对话', exact: true })
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')
  const history = panel.getByRole('region', { name: '设定 Agent 历史对话', exact: true })
  await expect(history.getByRole('button', { name: '继续对话：第一段设定讨论', exact: true })).toBeVisible()
  await expect(history.getByRole('button', { name: '继续对话：第二段设定讨论', exact: true })).toBeVisible()
  await expect(panel.getByRole('button', { name: '返回当前对话', exact: true })).toHaveCount(0)
  expect(backend.state.conversationReads).toHaveLength(reads)
  expect(backend.state.historyReads).toHaveLength(1)
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await expect(history).not.toBeAttached()
  await expect(draft).toHaveValue('第二段讨论自己的草稿。')
  await selectAgentConversation(page, '第一段设定讨论')
  await expect(log).toContainText('第一段设定讨论')
  await expect(log).not.toContainText('第二段设定讨论')
  await expect(draft).toHaveValue('第一段讨论尚未发送的草稿。')
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(log).toContainText('第一段设定讨论')
  await expect(draft).toHaveValue('第一段讨论尚未发送的草稿。')
  expect(backend.state.conversationReads.at(-1)).toBe(defaultConversationId)
  await selectAgentConversation(page, '第二段设定讨论')
  await expect(log).toContainText('第二段设定讨论')
  await expect(log).not.toContainText('第一段设定讨论')
  await expect(draft).toHaveValue('第二段讨论自己的草稿。')
  expect(backend.state.conversationReads.at(-1)).toBe(second.id)
  expect(backend.state.sendConversations).toEqual([defaultConversationId, second.id])
  expect(backend.state.conversationCreates).toHaveLength(1)
  expect(backend.state.providerCalls).toBe(2)
  expect(backend.state.accepts).toBe(0)
  expect(backend.state.body).toBe('港口在黄昏关闭。')
})

test('a lost first-send creation acknowledgment reuses its identity after refresh and sends the preserved message only once', async ({ page }) => {
  const backend = await authoringBackend(page, true)
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' })
  await draft.fill('新建之前的原始讨论')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await draft.fill('新建失败也要保留原会话的草稿。')
  await panel.getByRole('button', { name: '历史对话', exact: true }).click()
  await panel.getByRole('button', { name: '新建对话', exact: true }).click()
  await expect(draft).toHaveValue('')
  expect(backend.state.conversationCreates).toHaveLength(0)
  const firstMessage = '待创建的新讨论首条消息。'
  await draft.fill(firstMessage)
  backend.loseNextCreateAck()
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect(panel.getByRole('alert')).toBeVisible()
  expect(backend.state.conversations.size).toBe(2)
  expect(backend.state.conversationCreates).toHaveLength(1)
  const identity = backend.state.conversationCreates[0], created = [...backend.state.conversations.values()].find(item => !item.is_default)!
  expect(created.id).not.toBe(identity)
  expect(created.client_request_id).toBe(identity)
  expect(backend.state.sends).toBe(1)
  expect(backend.state.providerCalls).toBe(1)
  await draft.fill('旧创建请求尚未确认时后来写的新稿。')
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(draft).toHaveValue('旧创建请求尚未确认时后来写的新稿。')
  await expect(panel.getByRole('log')).not.toContainText('新建之前的原始讨论')
  expect(backend.state.conversationCreates).toEqual([identity])
  expect(backend.state.sends).toBe(1)
  await panel.getByRole('button', { name: '继续发送', exact: true }).click()
  await expect(panel.getByRole('log').getByText(firstMessage, { exact: true })).toHaveCount(1)
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await expect(draft).toHaveValue('旧创建请求尚未确认时后来写的新稿。')
  expect(backend.state.conversationCreates).toEqual([identity, identity])
  expect(backend.state.conversations.size).toBe(2)
  expect(backend.state.sends).toBe(2)
  expect(backend.state.providerCalls).toBe(2)
  expect(backend.state.sentPayloads[1].content).toBe(firstMessage)
  expect(backend.state.sendConversations[1]).toBe(created.id)
  expect(created.messages).toHaveLength(2)
  await page.reload()
  await openCollectionCollaboration(page)
  await expect(draft).toHaveValue('旧创建请求尚未确认时后来写的新稿。')
  await expect(panel.getByRole('log')).not.toContainText('新建之前的原始讨论')
  await expect(panel.getByRole('log')).toContainText(firstMessage)
  expect(backend.state.conversationReads.at(-1)).toBe(created.id)
  await selectAgentConversation(page, '新建之前的原始讨论')
  await expect(draft).toHaveValue('新建失败也要保留原会话的草稿。')
  expect(backend.state.attempts).toHaveLength(1)
  expect(backend.state.accepts).toBe(0)
})

for (const destination of ['body', 'external'] as const) test(`a deferred first-send creation ACK respects ${destination} focus after starting locally from history`, async ({ page }) => {
  const backend = await authoringBackend(page, true)
  backend.seedCompleted({ client_message_id: 'focus-before-create', content: '用于验证异步新建的旧讨论' })
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' })
  await expect(panel.getByRole('log')).toContainText('用于验证异步新建的旧讨论')
  await draft.fill('异步创建期间原会话草稿不丢失。')
  await panel.getByRole('button', { name: '历史对话', exact: true }).click()
  await expect(panel.getByRole('region', { name: '设定 Agent 历史对话', exact: true })).toBeVisible()
  const create = panel.getByRole('button', { name: '新建对话', exact: true })
  await create.click()
  await expect(draft).toHaveValue('')
  expect(backend.state.conversationCreates).toHaveLength(0)
  const firstMessage = '首次发送才创建服务端会话。'
  await draft.fill(firstMessage)
  const release = backend.holdCreateAck(), send = panel.getByRole('button', { name: '发送给设定 Agent', exact: true })
  await send.click()
  await expect.poll(() => backend.state.conversationCreates.length).toBe(1)
  await expect(send).toBeDisabled()
  const outside = page.getByRole('button', { name: '设定集操作', exact: true })
  if (destination === 'external') {
    await outside.focus()
    await expect(outside).toBeFocused()
  } else {
    // Disabling the clicked button leaves focus on BODY while the HTTP ACK is held.
    await expect.poll(() => page.evaluate(() => document.activeElement?.tagName)).toBe('BODY')
  }
  release()
  await expect(panel.getByRole('log').getByText(firstMessage, { exact: true })).toHaveCount(1)
  await expect(panel.getByRole('log')).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  await expect(draft).toHaveValue('')
  await expect(panel.getByRole('log')).not.toContainText('用于验证异步新建的旧讨论')
  if (destination === 'external') await expect(outside).toBeFocused()
  else await expect(draft).toBeFocused()
  await expect(create).toBeEnabled()
  expect(backend.state.conversationCreates).toHaveLength(1)
  expect(backend.state.conversations.size).toBe(2)
  expect(backend.state.sends).toBe(1)
  expect(backend.state.providerCalls).toBe(2)
  expect(backend.state.accepts).toBe(0)
  await selectAgentConversation(page, '用于验证异步新建的旧讨论')
  await expect(draft).toHaveValue('异步创建期间原会话草稿不丢失。')
})

test('a failed summary list retries without loading every conversation and selecting a delayed session never shows the previous chat', async ({ page }) => {
  const backend = await authoringBackend(page, true)
  backend.seedCompleted({ client_message_id: 'history-original', content: '原来的设定讨论' })
  const other = backend.seedConversation('99999999-9999-4999-8999-999999999999')
  backend.seedCompleted({ client_message_id: 'history-other', content: '另一段设定讨论' }, other.id)
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' }), log = panel.getByRole('log')
  await expect(log).toContainText('原来的设定讨论')
  await draft.fill('列表失败和切换时也保留原来的新输入。')
  const reads = backend.state.conversationReads.length
  backend.failHistory(true)
  await panel.getByRole('button', { name: '历史对话', exact: true }).click()
  const history = panel.getByRole('region', { name: '设定 Agent 历史对话', exact: true })
  await expect(history.getByRole('alert')).toBeVisible()
  backend.failHistory(false)
  await history.getByRole('button', { name: '重试载入历史对话', exact: true }).click()
  await expect(history.getByRole('button', { name: '继续对话：另一段设定讨论', exact: true })).toBeVisible()
  expect(backend.state.conversationReads).toHaveLength(reads)
  const release = backend.holdConversationRead(other.id)
  await history.getByRole('button', { name: '继续对话：另一段设定讨论', exact: true }).click()
  await expect.poll(() => backend.state.conversationReads.at(-1)).toBe(other.id)
  await expect(log).not.toContainText('原来的设定讨论')
  await expect(log).not.toContainText('另一段设定讨论')
  await expect(draft).toHaveValue('')
  release()
  await expect(log).toContainText('另一段设定讨论')
  await expect(log).not.toContainText('原来的设定讨论')
  await selectAgentConversation(page, '原来的设定讨论')
  await expect(log).toContainText('原来的设定讨论')
  await expect(draft).toHaveValue('列表失败和切换时也保留原来的新输入。')
  expect(backend.state.sends).toBe(0)
  expect(backend.state.providerCalls).toBe(2)
  expect(backend.state.conversationCreates).toHaveLength(0)
  expect(backend.state.accepts).toBe(0)
})

test('a late reply and pending proposal from conversation A cannot replace conversation B or its newer input and focus', async ({ page }) => {
  const backend = await authoringBackend(page, true)
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' }), log = panel.getByRole('log')
  await draft.fill('等待中的设定讨论 A')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect(log).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  const release = backend.holdReply()
  await draft.fill('A 中仍在生成的旧请求。')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect.poll(() => backend.state.sends).toBe(2)
  const oldRequest = backend.state.sentPayloads[1]
  await draft.fill('A 中后来输入的新草稿。')
  await panel.getByRole('button', { name: '新建对话', exact: true }).click()
  await expect(panel.getByRole('heading', { name: '一起构建这个世界' })).toBeVisible()
  await expect(panel.getByRole('button', { name: '检查结果', exact: true })).toHaveCount(0)
  await expect(panel.getByRole('button', { name: '比较建议', exact: true })).toHaveCount(0)
  await draft.fill('当前的设定讨论 B')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect(log).toContainText('当前的设定讨论 B')
  await draft.fill('B 中的未发送新输入。')
  await draft.focus()
  const response = page.waitForResponse(item => item.request().method() === 'POST' && item.url().endsWith('/messages') && item.request().postDataJSON().client_message_id === oldRequest.client_message_id)
  release()
  await (await response).finished()
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  await expect(draft).toHaveValue('B 中的未发送新输入。')
  await expect(draft).toBeFocused()
  await expect(log).toContainText('当前的设定讨论 B')
  await expect(log).not.toContainText('A 中仍在生成的旧请求。')
  await expect(panel.getByRole('button', { name: '比较建议', exact: true })).toHaveCount(1)
  await selectAgentConversation(page, '等待中的设定讨论 A')
  await expect(log.getByText(oldRequest.content, { exact: true })).toHaveCount(1)
  await expect(draft).toHaveValue('A 中后来输入的新草稿。')
  await expect(panel.getByRole('button', { name: '检查结果', exact: true })).toHaveCount(0)
  await expect(panel.getByRole('button', { name: '比较建议', exact: true })).toHaveCount(2)
  expect(backend.state.sends).toBe(3)
  expect(backend.state.providerCalls).toBe(3)
  expect(backend.state.finishes).toBe(0)
  expect(backend.state.accepts).toBe(0)
  expect(backend.state.body).toBe('港口在黄昏关闭。')
})

test('a first-send conversation with a pending reply cannot become the legacy empty pointer when creating another local discussion', async ({ page }) => {
  const backend = await authoringBackend(page, true)
  backend.seedCompleted({ client_message_id: 'pointer-used-b', content: '历史中的已使用讨论 B' })
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' }), log = panel.getByRole('log')
  await expect(log).toContainText('历史中的已使用讨论 B')
  await panel.getByRole('button', { name: '新建对话', exact: true }).click()
  const release = backend.holdReply()
  await draft.fill('A 的首次发送已经创建，但回复仍未返回。')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect.poll(() => backend.state.sends).toBe(1)
  expect(backend.state.conversationCreates).toHaveLength(1)
  const first = [...backend.state.conversations.values()].find(item => !item.is_default)!, original = backend.state.sentPayloads[0]
  await draft.fill('A 中另外保留的新输入。')
  await selectAgentConversation(page, '历史中的已使用讨论 B')
  await expect(log).toContainText('历史中的已使用讨论 B')
  await panel.getByRole('button', { name: '新建对话', exact: true }).click()
  await expect(panel.getByRole('heading', { name: '一起构建这个世界' })).toBeVisible()
  await expect(draft).toHaveValue('')
  await expect(panel.getByRole('button', { name: '检查结果', exact: true })).toHaveCount(0)
  expect(backend.state.conversationCreates).toHaveLength(1)
  await draft.fill('独立讨论 C 的首次发送')
  await panel.getByRole('button', { name: '发送给设定 Agent', exact: true }).click()
  await expect(log).toContainText('独立讨论 C 的首次发送')
  await expect(log).toContainText('设定 Agent 提出了灯塔的退潮规则。')
  const third = [...backend.state.conversations.values()].find(item => !item.is_default && item.id !== first.id)!
  expect(third).toBeDefined()
  expect(backend.state.conversationCreates).toHaveLength(2)
  expect(backend.state.conversationCreates[1]).not.toBe(backend.state.conversationCreates[0])
  expect(backend.state.sendConversations).toEqual([first.id, third.id])
  await draft.fill('C 的新输入不能被 A 的晚回复替换。')
  await draft.focus()
  const response = page.waitForResponse(item => item.request().method() === 'POST' && item.url().endsWith('/messages') && item.request().postDataJSON().client_message_id === original.client_message_id)
  release()
  await (await response).finished()
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  await expect(draft).toHaveValue('C 的新输入不能被 A 的晚回复替换。')
  await expect(draft).toBeFocused()
  await expect(log).not.toContainText(original.content)
  await selectAgentConversation(page, original.content)
  await expect(log.getByText(original.content, { exact: true })).toHaveCount(1)
  await expect(draft).toHaveValue('A 中另外保留的新输入。')
  await selectAgentConversation(page, '独立讨论 C 的首次发送')
  await expect(draft).toHaveValue('C 的新输入不能被 A 的晚回复替换。')
  expect(backend.state.sends).toBe(2)
  expect(backend.state.providerCalls).toBe(3)
  expect(backend.state.finishes).toBe(0)
  expect(backend.state.accepts).toBe(0)
  expect(backend.state.body).toBe('港口在黄昏关闭。')
})

for (const scenario of [
  { width: 926, height: 716, motion: 'no-preference' },
  { width: 390, height: 844, motion: 'reduce' },
] as const) test(`used setting conversations have visible hover and keyboard focus at ${scenario.width}x${scenario.height} with ${scenario.motion} motion`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width: scenario.width, height: scenario.height })
  await page.emulateMedia({ reducedMotion: scenario.motion })
  const backend = await authoringBackend(page, true)
  backend.seedCompleted({ client_message_id: 'hover-a', content: '已经使用的历史讨论 A' })
  const other = backend.seedConversation('cccccccc-cccc-4ccc-8ccc-cccccccccccc')
  backend.seedCompleted({ client_message_id: 'hover-b', content: '已经使用的历史讨论 B' }, other.id)
  backend.seedConversation('dddddddd-dddd-4ddd-8ddd-dddddddddddd')
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' })
  await expect(panel.getByRole('log')).toContainText('已经使用的历史讨论 A')
  await panel.getByRole('button', { name: '历史对话', exact: true }).click()
  const history = panel.getByRole('region', { name: '设定 Agent 历史对话', exact: true })
  const row = history.getByRole('button', { name: '继续对话：已经使用的历史讨论 A', exact: true })
  await expect(row).toBeVisible()
  await expect(history.getByRole('button', { name: /^继续对话：/ })).toHaveCount(2)
  await expect(history.getByRole('button', { name: '继续对话：新对话', exact: true })).toHaveCount(0)
  const appearance = () => row.evaluate(element => {
    const style = getComputedStyle(element), title = getComputedStyle(element.querySelector('.setting-collaboration-session-title')!)
    return JSON.stringify({ background: style.backgroundColor, color: style.color, shadow: style.boxShadow, border: style.borderColor,
      transform: style.transform, opacity: style.opacity, titleColor: title.color, before: getComputedStyle(element, '::before').opacity })
  })
  await page.mouse.move(0, 0)
  const idle = await appearance(), idleBounds = await row.boundingBox()
  expect(JSON.parse(idle).background).toBe('rgba(0, 0, 0, 0)')
  expect(JSON.parse(idle).titleColor).toBe('rgb(68, 68, 68)')
  await row.hover()
  await row.evaluate(element => Promise.all(element.getAnimations({ subtree: true }).map(animation => animation.finished.catch(() => undefined))))
  const hovered = await appearance()
  const styles = { idle: JSON.parse(idle), hovered: JSON.parse(hovered),
    studioSettingAncestor: await row.evaluate(element => Boolean(element.closest('.studio .studio-setting'))) }
  console.log(`History row settled styles: ${JSON.stringify(styles)}`)
  await testInfo.attach('history-row-settled-styles', { body: JSON.stringify(styles, null, 2), contentType: 'application/json' })
  expect(styles.studioSettingAncestor).toBe(true)
  await expect(row).toHaveCSS('background-color', 'rgb(238, 238, 238)')
  await expect(row.locator('.setting-collaboration-session-title')).toHaveCSS('color', 'rgb(23, 23, 23)')
  if (scenario.motion === 'reduce') expect(await row.boundingBox()).toEqual(idleBounds)
  await page.screenshot({ path: testInfo.outputPath(`history-hover-${scenario.width}-${scenario.motion}.png`), fullPage: true })
  await page.mouse.move(0, 0)
  await expect.poll(appearance).toBe(idle)
  await row.focus()
  await row.press('Tab')
  await page.keyboard.press('Shift+Tab')
  await expect(row).toBeFocused()
  expect(await row.evaluate(element => element.matches(':focus-visible'))).toBe(true)
  expect(await row.evaluate(element => getComputedStyle(element).outlineStyle)).not.toBe('none')
  await expect(row).toHaveCSS('background-color', 'rgb(238, 238, 238)')
  await expect(row.locator('.setting-collaboration-session-title')).toHaveCSS('color', 'rgb(23, 23, 23)')
  if (scenario.motion === 'reduce') await expect(panel).toHaveCSS('animation-name', 'none')
  await page.screenshot({ path: testInfo.outputPath(`history-focus-${scenario.width}-${scenario.motion}.png`), fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
  expect(await page.evaluate(() => document.documentElement.scrollHeight - innerHeight)).toBe(0)
  expect(backend.state.conversationCreates).toHaveLength(0)
  expect(backend.state.sends).toBe(0)
  expect(backend.state.providerCalls).toBe(2)
})

for (const scenario of [
  { width: 926, height: 716, motion: 'no-preference' },
  { width: 390, height: 844, motion: 'reduce' },
] as const) test(`the setting Agent top fade keeps history, proposal clicks and the composer usable at ${scenario.width}x${scenario.height} with ${scenario.motion} motion`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width: scenario.width, height: scenario.height })
  await page.emulateMedia({ reducedMotion: scenario.motion })
  const backend = await authoringBackend(page, true)
  for (let index = 0; index < 8; index++) backend.seedCompleted({ client_message_id: `fade-${index}`, content: `历史讨论 ${index}：为海港、灯塔和潮汐补充关联规则。` })
  await page.goto(`/setting-collections/${collectionId}?note=${docId}&mode=note`)
  await openCollectionCollaboration(page)
  const panel = page.getByRole('complementary', { name: '设定 Agent 协作' }), log = panel.getByRole('log')
  const draft = panel.getByRole('textbox', { name: '给设定 Agent 的消息' })
  await expect(log).toContainText('历史讨论 7')
  await draft.fill('淡出区域不能干扰这段未发送输入。')
  await expect.poll(() => log.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
  await log.evaluate(element => { element.scrollTop = 0 })
  await expect.poll(() => log.evaluate(element => getComputedStyle(element).maskImage)).toBe('none')
  const compare = panel.getByRole('button', { name: /^(比较建议|收起比较)$/ }).nth(2)
  const bounds = (await compare.boundingBox())!
  await log.evaluate((element, center) => { element.scrollTop += center - element.getBoundingClientRect().top - 16 }, bounds.y + bounds.height / 2)
  await expect.poll(() => log.evaluate(element => getComputedStyle(element).maskImage)).not.toBe('none')
  const fadedTarget = (await compare.boundingBox())!, logBounds = (await log.boundingBox())!
  expect(fadedTarget.y + fadedTarget.height / 2 - logBounds.y).toBeGreaterThan(0)
  expect(fadedTarget.y + fadedTarget.height / 2 - logBounds.y).toBeLessThan(20)
  await compare.click()
  await expect(compare).toHaveAttribute('aria-expanded', 'true')
  const toggle = panel.getByRole('button', { name: '历史对话', exact: true }), create = panel.getByRole('button', { name: '新建对话', exact: true })
  for (const button of [toggle, create]) {
    const box = (await button.boundingBox())!
    expect(box.width).toBe(32); expect(box.height).toBe(32)
  }
  await page.screenshot({ path: testInfo.outputPath(`top-fade-${scenario.width}-${scenario.motion}.png`), fullPage: true })
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')
  await expect(panel.getByRole('region', { name: '设定 Agent 历史对话', exact: true })).toBeVisible()
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await expect(draft).toHaveValue('淡出区域不能干扰这段未发送输入。')
  await expect(draft).toBeVisible()
  if (scenario.motion === 'reduce') await expect(panel).toHaveCSS('animation-name', 'none')
  expect(await page.evaluate(() => document.documentElement.scrollHeight - innerHeight)).toBe(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0)
  expect(backend.state.sends).toBe(0)
  expect(backend.state.conversationCreates).toHaveLength(0)
  expect(backend.state.accepts).toBe(0)
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
