/// <reference types="vite/client" />
import { test } from '@playwright/test'
import type { Chapter } from '../src/api/client'

// HTTP fixtures exercise the real router and browser; this is not backend acceptance.
test('Studio deep links, history, pending recovery and server chapter identity', async ({ page }) => {
  await page.unroute('**/api/v1/**');
  const stamp = '2026-09-12T00:00:00Z';
  const first = '11111111-1111-4111-8111-111111111111', second = '22222222-2222-4222-8222-222222222222', createdId = '33333333-3333-4333-8333-333333333333';
  const project = { id: '44444444-4444-4444-8444-444444444444', slug: 'navigation', title: '导航验证作品', genre: '科幻', target_platform: null, status: 'active', workspace_root: 'workspaces/navigation', metadata: {}, created_at: stamp, updated_at: stamp };
  const chapter = (id: string, number: number): Chapter => ({ id, project_id: project.id, chapter_number: number, title: `章节 ${number}`, status: 'draft', current_outline_document_id: null, current_draft_document_id: `doc-${id}`, final_document_id: null, summary_document_id: null, word_count: 20, metadata: { volume: '第一卷' }, created_at: stamp, updated_at: stamp });
  const chapters: Chapter[] = [chapter(first, 1), chapter(second, 2)];
  const drafts = new Map(chapters.map(ch => [ch.current_draft_document_id!, { document_id: ch.current_draft_document_id, version_id: 'version-1', content: `${ch.title}。这是来自 HTTP 测试替身的正文，用于验证正式导航。` }]));
  let writes = 0, creates = 0;
  const unexpected: string[] = [];
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = request.url().replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    const reply = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/v1/projects') return reply([project]);
    if (path === '/api/v1/setting-collections') return reply([]);
    if (path === `/api/v1/projects/${project.id}`) return reply(project);
    if (path === `/api/v1/projects/${project.id}/chapters`) {
      if (request.method() === 'POST') { creates++; const created = { ...chapter(createdId, 17), current_draft_document_id: null, title: '新章' }; chapters.push(created); return reply(created); }
      return reply(chapters);
    }
    if (path.endsWith('/production-v2') && request.method() === 'GET') return reply([]);
    const feedback = path.match(/\/chapters\/([^/]+)\/feedback\/draft$/);
    if (feedback) {
      const draft = drafts.get(`doc-${feedback[1]}`)!;
      return reply({ chapter_id: feedback[1], region: 'draft', document_id: draft.document_id, source_version_id: draft.version_id, revision: 0, comments: [], requirements: '', read_only: false });
    }
    const match = path.match(/^\/api\/v1\/documents\/([^/]+)\/content$/);
    const scoped = path.match(/^\/api\/v1\/projects\/[^/]+\/chapters\/([^/]+)\/draft\/content$/);
    const documentId = scoped ? `doc-${scoped[1]}` : match?.[1];
    if (documentId && drafts.has(documentId)) {
      const draft = drafts.get(documentId)!;
      if (request.method() === 'PUT') {
        const body = request.postDataJSON();
        if (body.expected_current_version_id !== draft.version_id) throw new Error('Stale document write');
        writes++; draft.content = body.content; draft.version_id = `saved-${writes}`;
        return reply({ id: draft.version_id, document_id: draft.document_id, version_number: writes + 1, parent_version_id: body.expected_current_version_id, source: 'user', actor_user_id: null, agent_role: null, workflow_run_id: null, content_hash: 'hash', byte_size: draft.content.length, word_count: draft.content.length, file_path: 'chapter.md', change_summary: null, created_at: stamp });
      }
      return reply(draft);
    }
    unexpected.push(`${request.method()} ${path}`); return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open 导航验证作品', exact: true }).first().click();
  await page.getByRole('dialog', { name: '导航验证作品', exact: true }).waitFor();
  if (!page.url().includes(`project=${project.id}`)) throw new Error('Details URL missing');
  await page.reload();
  await page.getByRole('dialog', { name: '导航验证作品', exact: true }).waitFor();
  await page.getByRole('button', { name: /第1话.*章节 1/ }).click();
  const prose = page.getByRole('textbox', { name: '章节正文', exact: true });
  await prose.waitFor();
  if (!page.url().includes(`/studio/${first}`)) throw new Error('Chapter deep link incorrect');
  await prose.fill('第一章修改后的正文，切换章节时必须保存。');
  await page.mouse.move(700, 90);
  await page.getByRole('button', { name: '展开章节侧边栏', exact: true }).click();
  await page.getByRole('button', { name: '第2话 章节 2', exact: true }).click();
  await page.waitForURL(`**/studio/${second}*`);
  if (writes !== 1) throw new Error(`Expected one flushed write, got ${writes}`);
  await page.goBack();
  await page.waitForURL(`**/studio/${first}*`);
  if (await prose.inputValue() !== '第一章修改后的正文，切换章节时必须保存。') throw new Error('Back lost draft');
  await page.goForward();
  await page.waitForURL(`**/studio/${second}*`);
  await prose.fill('第二章在后退前输入的正文。');
  await page.goBack();
  await page.waitForURL(`**/studio/${first}*`);
  await page.goForward();
  await page.waitForURL(`**/studio/${second}*`);
  if (await prose.inputValue() !== '第二章在后退前输入的正文。') throw new Error('Pending edit lost on history navigation');
  await page.waitForFunction(id => !sessionStorage.getItem(`guranovel:draft-recovery:doc-${id}`), second);
  if (drafts.get(`doc-${second}`)!.content !== '第二章在后退前输入的正文。') throw new Error('Recovered edit was not saved');
  await page.goBack();
  await page.waitForURL(`**/studio/${first}*`);
  await page.reload();
  await prose.waitFor();
  if (await prose.inputValue() !== '第一章修改后的正文，切换章节时必须保存。') throw new Error('Refresh lost draft');
  await page.goto(`/projects/${project.id}/studio/${first}?view=Detail`);
  await page.waitForURL(`**/?project=${project.id}`);
  await page.getByRole('dialog', { name: '导航验证作品', exact: true }).waitFor();
  await page.getByRole('button', { name: /第1话.*章节 1/ }).click();
  await prose.waitFor();
  await page.getByRole('button', { name: '展开章节侧边栏', exact: true }).click();
  await page.locator('.studio-directory summary').first().hover();
  await page.getByRole('button', { name: '在第一卷新建章节', exact: true }).click();
  await page.waitForURL(`**/studio/${createdId}*`);
  if (creates !== 1) throw new Error('Duplicate chapter creation');
  await page.reload();
  await page.getByRole('region', { name: '本章大纲', exact: true }).waitFor();
  await page.setViewportSize({ width: 820, height: 700 });
  await page.getByRole('button', { name: '展开章节侧边栏', exact: true }).click();
  await page.getByRole('button', { name: '固定章节目录', exact: true }).click();
  await page.waitForTimeout(500);

  if (unexpected.length) throw new Error(JSON.stringify(unexpected));
  await page.unroute('**/api/v1/**');
  await page.goto('/preview/studio');
  await page.setViewportSize({ width: 1440, height: 900 });
  return { detailsRefresh: true, exactChapter: true, savedBeforeNavigation: writes, historyAndRefresh: true, detailRefresh: true, serverCreated: creates, unexpected };
})
