import { expect, test, type BrowserContext, type Page, type Route } from '@playwright/test'

const ids = {
  run: '11111111-1111-4111-8111-111111111111',
  action: '22222222-2222-4222-8222-222222222222',
  change: '33333333-3333-4333-8333-333333333333',
  affected: '44444444-4444-4444-8444-444444444444',
  document: '55555555-5555-4555-8555-555555555555',
  expectedVersion: '66666666-6666-4666-8666-666666666666',
  plan: '77777777-7777-4777-8777-777777777777',
  planDocument: '88888888-8888-4888-8888-888888888888',
  planVersion: '99999999-9999-4999-8999-999999999999',
  operation: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  review: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  appliedVersion: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  olderRun: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  olderChange: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
}

const sentinels = [
  'private-change-request-browser-never-render',
  'sk-browser-never-render-12345678',
  'C:\\private\\browser\\artifact.json',
  'Authorization: Bearer browser-never-render',
  'provider=private-browser-model region=us-secret-1',
  'https://private-provider.invalid/v1',
  'postgresql+asyncpg://private:secret@db/private_test',
  'raw provider exception browser-never-render',
]

type Status = 'USER_CONFIRMATION' | 'APPLY_CHANGE' | 'CONSISTENCY_REVIEW' | 'PROJECT_UPDATED' | 'CANCELLED'

function run(status: Status) {
  const terminal = status === 'PROJECT_UPDATED' || status === 'CANCELLED'
  const applied = status === 'CONSISTENCY_REVIEW' || status === 'PROJECT_UPDATED'
  return {
    id: ids.run,
    maintenance_change_id: ids.change,
    type: 'project_maintenance',
    status,
    current_node: {
      USER_CONFIRMATION: 'user_confirm_revision',
      APPLY_CHANGE: 'apply_revision',
      CONSISTENCY_REVIEW: 'consistency_review',
      PROJECT_UPDATED: 'project_updated',
      CANCELLED: 'cancel_maintenance',
    }[status],
    next_node: null,
    awaiting_user: status === 'USER_CONFIRMATION',
    title: 'Move the reveal earlier',
    created_at: '2026-08-03T00:00:00Z',
    updated_at: terminal ? '2026-08-03T00:04:00Z' : '2026-08-03T00:01:00Z',
    completed_at: terminal ? '2026-08-03T00:04:00Z' : null,
    affected_items: [{
      id: ids.affected,
      position: 0,
      type: 'chapter',
      stable_reference: 'chapter/three',
      impact_level: 'high',
      reason: 'The reveal scene must move.',
      document_id: ids.document,
      chapter_id: null,
    }],
    revision_plan: {
      id: ids.plan,
      document_id: ids.planDocument,
      version_id: ids.planVersion,
      review_outcome: 'passed',
      summary: 'Move the reveal and retain the established timeline.',
      operations: [{
        id: ids.operation,
        sequence: 1,
        operation: 'revise',
        document_id: ids.document,
        expected_version_id: ids.expectedVersion,
        affected_item_ids: [ids.affected],
        instruction: 'Move the reveal without changing event order.',
      }],
    },
    consistency_review: status === 'PROJECT_UPDATED'
      ? { id: ids.review, outcome: 'clean', findings: [] }
      : null,
    applied_document_version_ids: applied ? [ids.appliedVersion] : [],
    pending_action: status === 'USER_CONFIRMATION' ? {
      id: ids.action,
      type: 'project_maintenance_revision_confirmation',
      status: 'pending',
      confirmation_kind: 'revision_confirmation',
      review_outcome: 'passed',
      allowed_decisions: ['approve', 'revise', 'cancel'],
    } : null,
  }
}

function olderRun() {
  return {
    ...run('PROJECT_UPDATED'),
    id: ids.olderRun,
    maintenance_change_id: ids.olderChange,
    title: 'Older server run',
    created_at: '2026-08-02T00:00:00Z',
    updated_at: '2026-08-02T00:01:00Z',
    completed_at: '2026-08-02T00:01:00Z',
  }
}

function json(route: Route, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    headers: { 'x-test-diagnostic': sentinels.join(' | ') },
    body: JSON.stringify(body),
  })
}

async function installFakeApi(context: BrowserContext, page: Page) {
  const violations: string[] = []
  const observedStatuses: Status[] = []
  let startCount = 0
  let resolveCount = 0
  let statusIndex = 0
  let resolved = false
  const expectedStartBody = JSON.stringify({
    title: 'Move the reveal earlier',
    change_request: sentinels[0],
    scope_hints: ['chapter'],
  })

  page.on('console', (message) => {
    if (sentinels.some((sentinel) => message.text().includes(sentinel))) violations.push('console leak')
    if (message.type() === 'error' || message.type() === 'warning') violations.push(`console:${message.type()}`)
  })
  page.on('pageerror', (error) => {
    if (sentinels.some((sentinel) => error.message.includes(sentinel))) violations.push('page error leak')
    violations.push('pageerror')
  })

  await context.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== 'http://127.0.0.1:5173') {
      violations.push('external request')
      await route.abort('blockedbyclient')
      return
    }
    if (!url.pathname.startsWith('/api/v1/')) {
      await route.continue()
      return
    }

    const method = request.method()
    const base = `/api/v1/projects/project-1/maintenance`
    if (method === 'POST' && url.pathname === `${base}/start`) {
      startCount += 1
      if (request.postData() !== expectedStartBody) violations.push('start body mismatch')
      await json(route, run('USER_CONFIRMATION'))
      return
    }
    if (method === 'POST' && url.pathname === `${base}/${ids.run}/actions/${ids.action}/resolve`) {
      resolveCount += 1
      if (request.postData() !== JSON.stringify({ decision: 'approve' })) violations.push('resolve body mismatch')
      resolved = true
      await json(route, run('APPLY_CHANGE'))
      return
    }
    if (method === 'GET' && url.pathname === `${base}/${ids.run}`) {
      if (!resolved) {
        await json(route, run('USER_CONFIRMATION'))
        return
      }
      // React StrictMode aborts the first development-only load before remounting.
      // Keep the fake server state stable for that replay, then advance on live polls.
      const statuses: Status[] = ['APPLY_CHANGE', 'APPLY_CHANGE', 'CONSISTENCY_REVIEW', 'PROJECT_UPDATED']
      const status = statuses[Math.min(statusIndex++, statuses.length - 1)]
      observedStatuses.push(status)
      await json(route, run(status))
      return
    }
    if (method === 'GET' && url.pathname === base && url.search === '?offset=0&limit=20') {
      await json(route, [{ ...run('PROJECT_UPDATED'), title: 'Newest server run' }, olderRun()])
      return
    }
    violations.push('unexpected API request')
    await route.abort('blockedbyclient')
  })

  return {
    violations,
    observedStatuses,
    counts: () => ({ startCount, resolveCount }),
  }
}

test('project maintenance lifecycle survives history, deep link, and reload without leaking diagnostics', async ({ context, page }) => {
  const fake = await installFakeApi(context, page)
  await page.goto('/projects/project-1/maintenance/start')
  await page.getByLabel('Change title').fill(' Move the reveal earlier ')
  await page.getByLabel('Change request').fill(` ${sentinels[0]} `)
  await page.getByLabel('Chapters').check()
  await page.getByRole('button', { name: 'Analyze change' }).dblclick()

  await expect(page.getByRole('button', { name: 'Approve plan' })).toBeVisible()
  expect(fake.counts().startCount).toBe(1)
  await page.getByRole('button', { name: 'Approve plan' }).dblclick()
  await expect(page).toHaveURL(`/projects/project-1/maintenance/${ids.run}/status`)
  await expect(page.getByRole('heading', { name: 'Applying approved changes' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Project updated' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Reviewing project consistency' })).toBeVisible({ timeout: 5_000 })
  await expect(page.getByRole('heading', { name: 'Project updated' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Project updated' })).toBeVisible({ timeout: 5_000 })

  await page.getByRole('link', { name: 'Back to maintenance history' }).click()
  const historyLinks = page.locator('.maintenance-history-list a')
  await expect(historyLinks).toHaveText(['Newest server run', 'Older server run'])
  await historyLinks.first().click()
  await expect(page).toHaveURL(`/projects/project-1/maintenance/${ids.run}/status`)
  await expect(page.getByRole('heading', { name: 'Project updated' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Project updated' })).toBeVisible()

  expect(fake.counts()).toEqual({ startCount: 1, resolveCount: 1 })
  const transitions = fake.observedStatuses.filter((status, index, all) => status !== all[index - 1])
  expect(transitions.slice(0, 3)).toEqual(['APPLY_CHANGE', 'CONSISTENCY_REVIEW', 'PROJECT_UPDATED'])
  for (const sentinel of sentinels) await expect(page.locator('body')).not.toContainText(sentinel)
  expect(fake.violations).toEqual([])
})

test('unsafe maintenance error details stay out of the UI and browser diagnostics', async ({ context, page }) => {
  const violations: string[] = []
  page.on('console', (message) => {
    if (sentinels.some((sentinel) => message.text().includes(sentinel))) violations.push('console leak')
  })
  page.on('pageerror', (error) => {
    if (sentinels.some((sentinel) => error.message.includes(sentinel))) violations.push('page error leak')
  })
  await context.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== 'http://127.0.0.1:5173') {
      violations.push('external request')
      await route.abort('blockedbyclient')
      return
    }
    if (!url.pathname.startsWith('/api/v1/')) {
      await route.continue()
      return
    }
    if (
      request.method() === 'POST'
      && url.pathname === '/api/v1/projects/project-1/maintenance/start'
    ) {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'workflow_state_error',
            message: sentinels.join(' | '),
            details: { raw_provider_output: sentinels[7] },
          },
        }),
      })
      return
    }
    violations.push('unexpected API request')
    await route.abort('blockedbyclient')
  })

  await page.goto('/projects/project-1/maintenance/start')
  await page.getByLabel('Change title').fill('Safe maintenance title')
  await page.getByLabel('Change request').fill('Safe maintenance request')
  await page.getByRole('button', { name: 'Analyze change' }).click()

  await expect(page.getByRole('alert')).toContainText(
    'This maintenance decision is stale. Reload the current gate and try again.',
  )
  for (const sentinel of sentinels) await expect(page.locator('body')).not.toContainText(sentinel)
  expect(violations).toEqual([])
})
