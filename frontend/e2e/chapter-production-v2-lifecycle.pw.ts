import { expect, test, type BrowserContext, type Page, type Route } from '@playwright/test'

const ids = {
  project: '11111111-1111-4111-8111-111111111111',
  chapter: '22222222-2222-4222-8222-222222222222',
  run: '33333333-3333-4333-8333-333333333333',
  action1: '44444444-4444-4444-8444-444444444444',
  action2: '55555555-5555-4555-8555-555555555555',
  outlineDoc: '66666666-6666-4666-8666-666666666666',
  outlineVer: '77777777-7777-4777-8777-777777777777',
  draftDoc: '88888888-8888-4888-8888-888888888888',
  draftVer1: '99999999-9999-4999-8999-999999999999',
  draftVer2: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  finalDoc: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  finalVer: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  editorReport: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  chiefReport: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
  loreReport: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
  panelSession: '12121212-1212-4212-8212-121212121212',
  panelRun: '13131313-1313-4313-8313-131313131313',
}

const sentinels = [
  'UNPUBLISHED NOVEL TEXT must remain snapshot-private',
  'SYSTEM PROMPT TEMPLATE must never render',
  'RAW PROVIDER PAYLOAD must never render',
  'sk-prod-never-render-secret-key-12345',
  'X-Provider-Credential: canary-header-secret',
  'https://private-provider.example/v1/chat/completions',
  'postgresql+asyncpg://postgres:secretpassword@prod-db/guranovel',
  '/mnt/d/private/keys/gemini_service_account.json',
  'Bearer canary-token-never-expose-in-dom',
  'PRIVATE WORKSPACE /srv/guranovel/authors/hidden-draft',
  'CHAIN-OF-THOUGHT hidden reasoning scratchpad',
  'raw provider exception with internal prompt dump and stack traces',
]

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: { 'x-test-diagnostic': sentinels.join(' | ') },
    body: JSON.stringify(body),
  })
}

function initialProject() {
  return {
    id: ids.project,
    slug: 'epic-fantasy-story',
    title: 'Epic Fantasy Story',
    status: 'ACTIVE',
    genre: 'Fantasy',
    target_platform: 'Web',
    workspace_root: '/workspace',
    metadata: { reader_panel_private_context: sentinels },
    created_at: '2026-08-22T00:00:00Z',
    updated_at: '2026-08-22T00:00:00Z',
  }
}

function initialChapter() {
  return {
    id: ids.chapter,
    project_id: ids.project,
    chapter_number: 1,
    title: 'Chapter One: The Awakening',
    status: 'OUTLINE_APPROVED',
    current_outline_document_id: ids.outlineDoc,
    current_draft_document_id: null,
    final_document_id: null,
    summary_document_id: null,
    word_count: 0,
    metadata: {},
    created_at: '2026-08-22T00:00:00Z',
    updated_at: '2026-08-22T00:00:00Z',
  }
}

function outlineVersion() {
  return {
    id: ids.outlineVer,
    document_id: ids.outlineDoc,
    version_number: 1,
    parent_version_id: null,
    source: 'outline_agent',
    actor_user_id: null,
    agent_role: 'outline_agent',
    workflow_run_id: null,
    content_hash: 'a'.repeat(64),
    byte_size: 100,
    word_count: 20,
    file_path: 'chapters/outline.md',
    change_summary: 'Approved outline.',
    created_at: '2026-08-22T00:00:00Z',
  }
}

function outlineDocument() {
  return {
    id: ids.outlineDoc,
    project_id: ids.project,
    chapter_id: ids.chapter,
    type: 'chapter_selected_outline',
    title: 'Selected Outline',
    path: 'chapters/outline.md',
    current_version_id: ids.outlineVer,
    current_version: outlineVersion(),
    created_at: '2026-08-22T00:00:00Z',
    updated_at: '2026-08-22T00:00:00Z',
  }
}

function outlineContent() {
  return {
    document_id: ids.outlineDoc,
    version_id: ids.outlineVer,
    content: '# Chapter One\n\nHero awakens in a dungeon.\n',
  }
}

function outlineVersions() {
  return [outlineVersion()]
}

type V2Status =
  | 'DRAFTING'
  | 'AUTHOR_REVISION'
  | 'EDITOR_REVIEW'
  | 'REVIEW_REVISION'
  | 'CHIEF_FINAL_REVIEW'
  | 'LORE_FINAL_REVIEW'
  | 'REVISION_READY'
  | 'ARCHIVE_UPDATE'
  | 'COMPLETED'
  | 'FAILED'

interface V2StateOverrides {
  status: V2Status
  currentNode?: string
  awaitingUser?: boolean
  docVerId?: string
  actionId?: string | null
  actionKind?: 'author_revision' | 'review_warning' | 'review_revision' | null
  editorReportId?: string | null
  chiefReportId?: string | null
  loreReportId?: string | null
  failureCode?: string | null
  failedFromStatus?: string | null
}

function buildV2State(overrides: V2StateOverrides) {
  const nodeMap: Record<V2Status, string> = {
    DRAFTING: 'drafting',
    AUTHOR_REVISION: 'author_revision',
    EDITOR_REVIEW: 'editor_review',
    REVIEW_REVISION: 'review_revision',
    CHIEF_FINAL_REVIEW: 'chief_final_review',
    LORE_FINAL_REVIEW: 'lore_final_review',
    REVISION_READY: 'REVISION_READY',
    ARCHIVE_UPDATE: 'archive_update',
    COMPLETED: 'completed',
    FAILED: 'failed',
  }

  return {
    chapter_workflow_run_id: ids.run,
    chapter_id: ids.chapter,
    status: overrides.status,
    current_node: overrides.currentNode ?? nodeMap[overrides.status],
    awaiting_user: overrides.awaitingUser ?? false,
    review_policy_version: 'chapter-quality-v1',
    chief_editor_required: true,
    document_id: ids.draftDoc,
    document_version_id: overrides.docVerId ?? ids.draftVer1,
    content_hash: 'a'.repeat(64),
    editor_report_id: overrides.editorReportId ?? null,
    chief_editor_report_id: overrides.chiefReportId ?? null,
    lore_report_id: overrides.loreReportId ?? null,
    action_request_id: overrides.actionId ?? null,
    action_kind: overrides.actionKind ?? null,
    failed_from_status: overrides.failedFromStatus ?? null,
    failure_code: overrides.failureCode ?? null,
  }
}

type V2StatePayload = ReturnType<typeof buildV2State>
type ActionResult = { body: unknown; status?: number } | null

async function setupRouteMocks(
  context: BrowserContext,
  page: Page,
  stateSupplier: () => V2StatePayload | null,
  onAction?: (url: URL, method: string, postData: string | null) => Promise<ActionResult> | ActionResult,
) {
  const violations: string[] = []
  const requests: string[] = []

  page.on('console', (message) => {
    if (sentinels.some((s) => message.text().includes(s))) {
      violations.push(`console_leak: ${message.text()}`)
    }
  })

  page.on('pageerror', (error) => {
    if (sentinels.some((s) => error.message.includes(s))) {
      violations.push(`pageerror_leak: ${error.message}`)
    }
  })

  await context.route('**/*', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()

    if (url.origin !== 'http://127.0.0.1:5173') {
      violations.push(`external_request: ${req.url()}`)
      await route.abort('blockedbyclient')
      return
    }

    if (!url.pathname.startsWith('/api/v1/')) {
      await route.continue()
      return
    }

    requests.push(`${method} ${url.pathname}`)
    const requestSurface = [req.url(), req.postData() ?? '', JSON.stringify(await req.allHeaders())]
      .join('\n')
    if (sentinels.some((sentinel) => requestSurface.includes(sentinel))) {
      violations.push(`request_leak: ${method} ${url.pathname}`)
    }

    // Projects & Chapters basic routes
    if (method === 'GET' && url.pathname === `/api/v1/projects/${ids.project}`) {
      await json(route, initialProject())
      return
    }

    if (
      method === 'GET' &&
      url.pathname === `/api/v1/projects/${ids.project}/chapters/${ids.chapter}`
    ) {
      await json(route, initialChapter())
      return
    }

    // Document routes
    if (method === 'GET' && url.pathname === `/api/v1/documents/${ids.outlineDoc}`) {
      await json(route, outlineDocument())
      return
    }

    if (method === 'GET' && url.pathname === `/api/v1/documents/${ids.outlineDoc}/content`) {
      await json(route, outlineContent())
      return
    }

    if (method === 'GET' && url.pathname === `/api/v1/documents/${ids.outlineDoc}/versions`) {
      await json(route, outlineVersions())
      return
    }

    // V2 List Runs
    if (
      method === 'GET' &&
      url.pathname === `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2`
    ) {
      const currentState = stateSupplier()
      if (!currentState) {
        await json(route, [])
      } else {
        await json(route, [
          {
            workflow_run_id: ids.run,
            project_id: ids.project,
            chapter_id: ids.chapter,
            status: currentState.status,
            current_node: currentState.current_node,
            started_at: '2026-08-22T00:00:00Z',
            updated_at: '2026-08-22T00:01:00Z',
          },
        ])
      }
      return
    }

    // V2 Get State
    if (
      method === 'GET' &&
      url.pathname === `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2/${ids.run}`
    ) {
      const currentState = stateSupplier()
      if (!currentState) {
        await json(route, { error: { code: 'not_found', message: 'Not found' } }, 404)
      } else {
        await json(route, currentState)
      }
      return
    }

    // Dynamic Action handler
    if (onAction) {
      const handled = await onAction(url, method, req.postData())
      if (handled) {
        await json(route, handled.body, handled.status || 200)
        return
      }
    }

    violations.push(`unhandled_api_request: ${method} ${url.pathname}`)
    await route.abort('blockedbyclient')
  })

  return { requests, violations }
}

// ==============================================================================
// Flow 1: Standard Happy Path (3-stage review, warning proceed, finalization)
// ==============================================================================

test('chapter production v2 flow 1: standard happy path with review warning proceed and completion', async ({
  context,
  page,
}) => {
  let currentState: V2StatePayload | null = null

  const { violations } = await setupRouteMocks(
    context,
    page,
    () => currentState,
    async (url, method, postData) => {
      const base = `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2`

      // 1. Start V2
      if (method === 'POST' && url.pathname === `${base}/start`) {
        currentState = buildV2State({
          status: 'AUTHOR_REVISION',
          awaitingUser: true,
          actionId: ids.action1,
          actionKind: 'author_revision',
        })
        return {
          body: {
            workflow_run_id: ids.run,
            action_request_id: ids.action1,
            outline_document_id: ids.outlineDoc,
            outline_version_id: ids.outlineVer,
            draft_document_id: ids.draftDoc,
            draft_version_id: ids.draftVer1,
          },
        }
      }

      // 2. Resolve Author Action (accept draft)
      if (
        method === 'POST' &&
        url.pathname === `${base}/${ids.run}/actions/${ids.action1}/resolve`
      ) {
        const payload = JSON.parse(postData || '{}')
        expect(payload.decision).toBe('accept')
        currentState = buildV2State({
          status: 'EDITOR_REVIEW',
          awaitingUser: false,
        })
        return {
          body: {
            workflow_run_id: ids.run,
            draft_document_id: ids.draftDoc,
            draft_version_id: ids.draftVer1,
            action_request_id: null,
          },
        }
      }

      // 3. Trigger Editor Review -> yields warning
      if (method === 'POST' && url.pathname === `${base}/${ids.run}/review`) {
        if (!currentState) return null
        if (currentState.status === 'EDITOR_REVIEW') {
          currentState = buildV2State({
            status: 'EDITOR_REVIEW',
            awaitingUser: true,
            editorReportId: ids.editorReport,
            actionId: ids.action2,
            actionKind: 'review_warning',
          })
        } else if (currentState.status === 'CHIEF_FINAL_REVIEW') {
          currentState = buildV2State({
            status: 'LORE_FINAL_REVIEW',
            awaitingUser: false,
            editorReportId: ids.editorReport,
            chiefReportId: ids.chiefReport,
          })
        } else if (currentState.status === 'LORE_FINAL_REVIEW') {
          currentState = buildV2State({
            status: 'REVISION_READY',
            awaitingUser: false,
            editorReportId: ids.editorReport,
            chiefReportId: ids.chiefReport,
            loreReportId: ids.loreReport,
          })
        }
        return {
          body: {
            workflow_run_id: ids.run,
            draft_document_id: ids.draftDoc,
            draft_version_id: ids.draftVer1,
            action_request_id: currentState.action_request_id,
          },
        }
      }

      // 4. Resolve Review Warning (proceed_with_warnings)
      if (
        method === 'POST' &&
        url.pathname === `${base}/${ids.run}/actions/${ids.action2}/resolve`
      ) {
        const payload = JSON.parse(postData || '{}')
        expect(payload.decision).toBe('proceed_with_warnings')
        currentState = buildV2State({
          status: 'CHIEF_FINAL_REVIEW',
          awaitingUser: false,
          editorReportId: ids.editorReport,
        })
        return {
          body: {
            workflow_run_id: ids.run,
            draft_document_id: ids.draftDoc,
            draft_version_id: ids.draftVer1,
            action_request_id: null,
          },
        }
      }

      // 5. Finalize Chapter
      if (method === 'POST' && url.pathname === `${base}/${ids.run}/finalize`) {
        currentState = buildV2State({
          status: 'COMPLETED',
          awaitingUser: false,
          docVerId: ids.finalVer,
        })
        return {
          body: {
            workflow_run_id: ids.run,
            final_document_id: ids.finalDoc,
            final_version_id: ids.finalVer,
          },
        }
      }

      return null
    },
  )

  await page.goto(`/projects/${ids.project}/chapters/${ids.chapter}`)

  // Step 1: Initial Workbench View
  await expect(page.getByRole('heading', { name: 'Chapter production (V2)' })).toBeVisible()
  await expect(page.getByText('Chapter production (V2) has not started yet.')).toBeVisible()

  // Start production
  await page.getByRole('button', { name: 'Start chapter production (V2)' }).click()

  // Step 2: Author Revision Gate
  await expect(page.getByRole('heading', { name: 'Author revision required' })).toBeVisible()
  await expect(page.getByLabel('Chapter production summary').getByText(ids.draftVer1)).toBeVisible()
  await page.getByRole('button', { name: 'Confirm accept draft' }).click()

  // Step 3: Editor Review Stage & Warnings
  await expect(page.getByRole('heading', { name: 'Editor review stage' })).toBeVisible()
  await page.getByRole('button', { name: 'Trigger chapter review' }).click()

  await expect(page.getByText('Review warnings found')).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Proceed with warnings' })).toBeVisible()
  await page.getByRole('button', { name: 'Confirm proceed with warnings' }).click()

  // Step 4: Chief Editor Final Review Stage
  await expect(page.getByRole('heading', { name: 'Chief editor final review' })).toBeVisible()
  await page.getByRole('button', { name: 'Trigger chapter review' }).click()

  // Step 5: Lore & Continuity Review Stage
  await expect(page.getByRole('heading', { name: 'Lore & continuity final review' })).toBeVisible()
  await page.getByRole('button', { name: 'Trigger chapter review' }).click()

  // Step 6: Revision Ready for Finalization
  await expect(page.getByRole('heading', { name: 'Revision ready for finalization' })).toBeVisible()
  await page.getByRole('button', { name: 'Finalize chapter' }).click()

  // Step 7: Completed State
  await expect(page.getByRole('heading', { name: 'Chapter production completed' })).toBeVisible()
  await expect(page.getByLabel('Chapter production summary').getByText(ids.finalVer)).toBeVisible()

  // Privacy Sentinel checks
  const bodyText = await page.locator('body').innerText()
  for (const sentinel of sentinels) {
    expect(bodyText).not.toContain(sentinel)
  }
  expect(violations).toEqual([])
})

// ==============================================================================
// Flow 2: Author Revision & Review Revision Loop
// ==============================================================================

test('chapter production v2 flow 2: manual edit and blocking review revision loop', async ({
  context,
  page,
}) => {
  let currentState: V2StatePayload | null = null

  const { violations } = await setupRouteMocks(
    context,
    page,
    () => currentState,
    async (url, method, postData) => {
      const base = `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2`

      // 1. Start V2
      if (method === 'POST' && url.pathname === `${base}/start`) {
        currentState = buildV2State({
          status: 'AUTHOR_REVISION',
          awaitingUser: true,
          actionId: ids.action1,
          actionKind: 'author_revision',
        })
        return {
          body: {
            workflow_run_id: ids.run,
            action_request_id: ids.action1,
            outline_document_id: ids.outlineDoc,
            outline_version_id: ids.outlineVer,
            draft_document_id: ids.draftDoc,
            draft_version_id: ids.draftVer1,
          },
        }
      }

      // 2. Submit Manual Edit
      if (
        method === 'POST' &&
        url.pathname === `${base}/${ids.run}/actions/${ids.action1}/resolve`
      ) {
        const payload = JSON.parse(postData || '{}')
        expect(payload.decision).toBe('submit_manual_edit')
        expect(payload.content).toContain('Hero finds an ancient sword')
        currentState = buildV2State({
          status: 'EDITOR_REVIEW',
          awaitingUser: false,
          docVerId: ids.draftVer2,
        })
        return {
          body: {
            workflow_run_id: ids.run,
            draft_document_id: ids.draftDoc,
            draft_version_id: ids.draftVer2,
            action_request_id: null,
          },
        }
      }

      // 3. Trigger Editor Review -> yields Blocking review revision
      if (method === 'POST' && url.pathname === `${base}/${ids.run}/review`) {
        currentState = buildV2State({
          status: 'EDITOR_REVIEW',
          awaitingUser: true,
          docVerId: ids.draftVer2,
          editorReportId: ids.editorReport,
          actionId: ids.action2,
          actionKind: 'review_revision',
        })
        return {
          body: {
            workflow_run_id: ids.run,
            draft_document_id: ids.draftDoc,
            draft_version_id: ids.draftVer2,
            action_request_id: ids.action2,
          },
        }
      }

      // 4. Resolve Review Revision
      if (
        method === 'POST' &&
        url.pathname === `${base}/${ids.run}/actions/${ids.action2}/resolve`
      ) {
        const payload = JSON.parse(postData || '{}')
        expect(payload.decision).toBe('request_review_revision')
        currentState = buildV2State({
          status: 'REVISION_READY',
          awaitingUser: false,
          docVerId: ids.draftVer2,
        })
        return {
          body: {
            workflow_run_id: ids.run,
            draft_document_id: ids.draftDoc,
            draft_version_id: ids.draftVer2,
            action_request_id: null,
          },
        }
      }

      // 5. Finalize Chapter
      if (method === 'POST' && url.pathname === `${base}/${ids.run}/finalize`) {
        currentState = buildV2State({
          status: 'COMPLETED',
          awaitingUser: false,
          docVerId: ids.finalVer,
        })
        return {
          body: {
            workflow_run_id: ids.run,
            final_document_id: ids.finalDoc,
            final_version_id: ids.finalVer,
          },
        }
      }

      return null
    },
  )

  await page.goto(`/projects/${ids.project}/chapters/${ids.chapter}`)
  await page.getByRole('button', { name: 'Start chapter production (V2)' }).click()

  // In Author Revision -> Switch to Manual Edit Tab
  await page.getByRole('tab', { name: 'Submit manual edit' }).click()
  await page
    .getByLabel('Manual draft content')
    .fill('# Chapter One\n\nHero finds an ancient sword in the dungeon.\n')
  await page.getByRole('button', { name: 'Save manual edit' }).click()

  // In Editor Review on Version 2
  await expect(page.getByRole('heading', { name: 'Editor review stage' })).toBeVisible()
  await expect(page.getByLabel('Chapter production summary').getByText(ids.draftVer2)).toBeVisible()
  await page.getByRole('button', { name: 'Trigger chapter review' }).click()

  // Blocking Findings Detected
  await expect(page.getByText('Blocking review findings detected')).toBeVisible()
  await page.getByRole('button', { name: 'Request review revision' }).click()

  // Revision ready & Finalize
  await expect(page.getByRole('heading', { name: 'Revision ready for finalization' })).toBeVisible()
  await page.getByRole('button', { name: 'Finalize chapter' }).click()

  await expect(page.getByRole('heading', { name: 'Chapter production completed' })).toBeVisible()

  // Privacy Sentinel checks
  const bodyText = await page.locator('body').innerText()
  for (const sentinel of sentinels) {
    expect(bodyText).not.toContain(sentinel)
  }
  expect(violations).toEqual([])
})

// ==============================================================================
// Flow 3: Error State Recovery & Safe Error Redaction
// ==============================================================================

test('chapter production v2 flow 3: failure state reconciliation and safe resumption', async ({
  context,
  page,
}) => {
  let currentState: V2StatePayload | null = buildV2State({
    status: 'FAILED',
    currentNode: 'failed',
    awaitingUser: false,
    failureCode: 'provider_timeout',
    failedFromStatus: 'DRAFTING',
  })

  const { violations } = await setupRouteMocks(
    context,
    page,
    () => currentState,
    async (url, method) => {
      const base = `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/production-v2`

      // Resume Drafting
      if (method === 'POST' && url.pathname === `${base}/${ids.run}/resume`) {
        currentState = buildV2State({
          status: 'AUTHOR_REVISION',
          awaitingUser: true,
          actionId: ids.action1,
          actionKind: 'author_revision',
        })
        return {
          body: {
            workflow_run_id: ids.run,
            action_request_id: ids.action1,
            outline_document_id: ids.outlineDoc,
            outline_version_id: ids.outlineVer,
            draft_document_id: ids.draftDoc,
            draft_version_id: ids.draftVer1,
          },
        }
      }

      return null
    },
  )

  await page.goto(`/projects/${ids.project}/chapters/${ids.chapter}`)

  // Verify Error message rendered safely without leaking raw stack trace or secrets
  await expect(
    page.getByRole('heading', { name: 'Chapter production encountered an issue' }),
  ).toBeVisible()
  await expect(page.getByText('AI drafting provider timed out. You may resume drafting.')).toBeVisible()

  // Click resume button
  await page.getByRole('button', { name: 'Resume drafting' }).click()

  // Verify successful recovery to Author Revision
  await expect(page.getByRole('heading', { name: 'Author revision required' })).toBeVisible()

  // Privacy Sentinel checks
  const bodyText = await page.locator('body').innerText()
  for (const sentinel of sentinels) {
    expect(bodyText).not.toContain(sentinel)
  }
  expect(violations).toEqual([])
})

test('chapter production v2 flow 4: revision-ready reader panel reaches an editor-only report without leaks or edits', async ({
  context,
  page,
}) => {
  const currentState = buildV2State({
    status: 'REVISION_READY',
    awaitingUser: false,
    editorReportId: ids.editorReport,
    chiefReportId: ids.chiefReport,
    loreReportId: ids.loreReport,
  })

  const panelDetail = {
    is_noop: false,
    session_id: ids.panelSession,
    workflow_run_id: ids.panelRun,
    project_id: ids.project,
    chapter_id: ids.chapter,
    document_id: ids.draftDoc,
    document_version_id: ids.draftVer1,
    source_hash: 'a'.repeat(64),
    mode: 'standard',
    status: 'completed',
    stale: false,
    degradation_reason: null,
    failure_reason: null,
    planned_readers: 4,
    completed_readers: 4,
    failed_readers: 0,
    issue_count: 1,
    initial_ballot_count: 4,
    final_ballot_count: 4,
    discussion_message_count: 5,
    created_at: '2026-08-28T01:00:00Z',
    updated_at: '2026-08-28T01:02:00Z',
    completed_at: '2026-08-28T01:02:00Z',
    review_report: {
      summary: 'Editor review should consider one local clarification.',
      blocking_issues: [],
      warnings: ['A high-confidence minority risk remains visible.'],
      notes: ['Reader Panel did not modify the manuscript.'],
      suggested_actions: [{
        priority: 'manual_review',
        target_segment_ids: ['S002'],
        suggested_action: 'clarify',
        instruction: 'Consider one causal beat at the transition.',
      }],
    },
    issues: [{
      issue_number: 1,
      title: 'Abrupt transition',
      category: 'pacing',
      symptom: 'The scene changes before the cause is clear.',
      root_cause_hypotheses: ['The causal beat is compressed.'],
      evidence: [{ segment_ids: ['S002'], note: 'The transition begins here.' }],
      target_audience_relevance: 'high',
      minority_risk: true,
      discussion_status: 'closed',
      consensus_class: 'polarized',
      recommended_priority: 'manual_review',
    }],
    initial_reports: [],
    transcript: null,
    permitted_operations: [],
  }

  const { requests, violations } = await setupRouteMocks(
    context,
    page,
    () => currentState,
    async (url, method, postData) => {
      const panelBase = `/api/v1/projects/${ids.project}/chapters/${ids.chapter}/reader-panels`
      if (method === 'POST' && url.pathname === panelBase) {
        expect(JSON.parse(postData || '{}')).toEqual({
          document_id: ids.draftDoc,
          document_version_id: ids.draftVer1,
          mode: 'standard',
          config_overrides: {
            max_ballot_issues: 6,
            max_discussion_issues: 4,
            max_rounds_per_issue: 2,
            min_valid_readers: 3,
          },
          test_goals: ['Check the transition.'],
          target_audience: ['Fantasy readers.'],
        })
        return {
          body: {
            ...panelDetail,
            status: 'independent_reading',
            completed_readers: 0,
            issue_count: 0,
            initial_ballot_count: 0,
            final_ballot_count: 0,
            discussion_message_count: 0,
            completed_at: null,
            review_report: null,
            issues: [],
            permitted_operations: ['cancel'],
          },
        }
      }
      if (method === 'GET' && url.pathname === `${panelBase}/${ids.panelSession}`) {
        return { body: panelDetail }
      }
      return null
    },
  )

  await page.goto(`/projects/${ids.project}/chapters/${ids.chapter}`)
  await expect(page.getByRole('heading', { name: 'Revision ready for finalization' })).toBeVisible()
  await page.getByRole('link', { name: 'Open Reader Panel' }).click()

  await expect(page).toHaveURL(new RegExp(
    `/projects/${ids.project}/chapters/${ids.chapter}/documents/${ids.draftDoc}/versions/${ids.draftVer1}/reader-panel$`,
  ))
  await page.getByLabel('Test goals').fill('Check the transition.')
  await page.getByLabel('Target audience').fill('Fantasy readers.')
  await page.getByRole('button', { name: 'Start Reader Panel' }).click()

  await expect(page).toHaveURL(new RegExp(`/reader-panel/${ids.panelSession}$`))
  await expect(page.getByRole('heading', { name: 'Reader Panel report' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Editor handoff' })).toContainText(
    'Editor review should consider one local clarification.',
  )
  await expect(page.getByText(ids.draftVer1)).toBeVisible()
  await expect(page.getByText(/results never modify the chapter automatically/i)).toBeVisible()

  const bodyText = await page.locator('body').innerText()
  for (const sentinel of sentinels) expect(bodyText).not.toContain(sentinel)
  expect(requests.filter((request) => /^(POST|PUT|PATCH|DELETE) .*\/documents\//.test(request)))
    .toEqual([])
  expect(requests.some((request) => request.includes('/finalize'))).toBe(false)
  expect(violations).toEqual([])
})
