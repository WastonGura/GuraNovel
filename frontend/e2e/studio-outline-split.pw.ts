/// <reference lib="dom" />
import { expect, test, type Page } from '@playwright/test'

async function openOutline(page: Page, mode: 'preview' | 'saved chapter') {
  if (mode === 'preview') { await page.goto('/preview/studio'); return }
  const projectId = '44444444-4444-4444-8444-444444444444', chapterId = '11111111-1111-4111-8111-111111111111'
  const stamp = '2026-10-16T00:00:00Z', unexpected: string[] = []
  const project = { id: projectId, title: '动画验证', slug: 'motion', genre: '悬疑', target_platform: null, workspace_root: 'workspaces/motion', status: 'active', metadata: {}, created_at: stamp, updated_at: stamp }
  const chapter = { id: chapterId, project_id: projectId, chapter_number: 1, title: '港口', status: 'draft', current_outline_document_id: null, current_draft_document_id: null, final_document_id: null, summary_document_id: null, word_count: 0, metadata: {}, created_at: stamp, updated_at: stamp }
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    const reply = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === `/api/v1/projects/${projectId}`) return reply(project)
    if (path.endsWith('/chapters')) return reply([chapter])
    if (path.endsWith('/production-v2')) return reply([])
    if (path.endsWith('/outline/idea')) return reply({ project_id: projectId, chapter_id: chapterId, text: '在港口安排一次重逢', revision: 1, read_only: false })
    if (path.endsWith('/outline/generations')) return reply(request.method() === 'GET' ? [] : {
      id: request.postDataJSON().request_id, project_id: projectId, chapter_id: chapterId, status: 'completed', started_at: stamp,
      idea_revision: 1, idea_text: '在港口安排一次重逢', context_hash: 'motion-fixture', failure_code: null, provenance: { provider_kind: 'fake' },
      candidates: ['偶遇', '追踪', '约定'].map((title, index) => ({ id: `option-${index}`, title: `方案${['一', '二', '三'][index]}：${title}`, summary: '三条不同的故事方向', content: `${title}的完整大纲。\n`.repeat(40) })),
    })
    unexpected.push(`${request.method()} ${path}`)
    return route.fulfill({ status: 404, body: '{}' })
  })
  await page.goto(`/projects/${projectId}/studio/${chapterId}`)
  await expect(page.getByRole('button', { name: '生成大纲方案' })).toBeEnabled()
  expect(unexpected).toEqual([])
}

for (const mode of ['preview', 'saved chapter'] as const) test(`${mode}: one outline sheet opens from its centre into three papers before revealing choices`, async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await openOutline(page, mode)
  await page.getByRole('textbox', { name: '大纲思路' }).fill('在港口安排一次重逢')
  const surface = page.locator('.original-outline-surface')
  const before = await surface.locator('.studio-outline-frame').boundingBox()
  expect(before!.width).toBeGreaterThan(600)
  const samples = await page.evaluate(async () => {
    const surface = document.querySelector('.original-outline-surface')!
    const frame = surface.querySelector('.studio-outline-frame')!
    const papers = [...surface.querySelectorAll('.studio-split-panels > i')]
    const frames: { elapsed: number; gap: number; centre: number; width: number; contentVisible: boolean; dust: boolean; masked: boolean }[] = []
    const start = performance.now()
    ;(surface.querySelector('[aria-label="生成大纲方案"]') as HTMLButtonElement).click()
    await new Promise<void>(resolve => {
      function sample() {
        const elapsed = performance.now() - start
        const a = papers[0].getBoundingClientRect(), b = papers[1].getBoundingClientRect()
        const options = surface.querySelector('.studio-outline-options') as HTMLElement | null
        frames.push({ elapsed, gap: b.left - a.right, centre: b.left + b.width / 2, width: frame.getBoundingClientRect().width,
          contentVisible: !!options && getComputedStyle(options).visibility !== 'hidden' && Number(getComputedStyle(options).opacity) > .05,
          dust: !!surface.querySelector('canvas.studio-text-particles'),
          masked: !!options && [...options.querySelectorAll<HTMLElement>('[data-sweep-text]')].some(text => text.style.mask.includes('linear-gradient')) })
        if (elapsed < 1250) requestAnimationFrame(sample); else resolve()
      }
      requestAnimationFrame(sample)
    })
    return frames
  })
  const unfolding = samples.filter(sample => sample.elapsed > 180 && sample.elapsed < 700)
  expect(unfolding.length).toBeGreaterThan(3)
  expect(unfolding.every(sample => !sample.contentVisible)).toBe(true)
  expect(unfolding.some(sample => sample.gap > 2 && sample.gap < 70)).toBe(true)
  for (const sample of unfolding) expect(sample.centre).toBeCloseTo(960, 0)
  const last = samples.at(-1)!
  expect(last.gap).toBeCloseTo(72, 0)
  expect(last.width).toBeCloseTo(before!.width - 6, 0)
  expect(last.contentVisible).toBe(true)
  await expect(page.getByRole('button', { name: mode === 'preview' ? /方案 01/ : '采用此方案：偶遇' })).toBeVisible()
  if (mode === 'saved chapter') {
    expect(samples.some(sample => sample.dust)).toBe(true)
    expect(samples.some(sample => sample.contentVisible && sample.masked)).toBe(true)
    for (const title of ['偶遇', '追踪', '约定']) await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
    const card = page.getByRole('region', { name: '方案全文：偶遇' })
    expect(await card.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
    expect(await card.evaluate(element => (element as HTMLElement).offsetWidth - element.clientWidth)).toBe(0)
    await card.hover()
    await page.mouse.wheel(0, 200)
    await expect.poll(() => card.evaluate(element => element.scrollTop)).toBeGreaterThan(0)
    const adopt = page.getByRole('button', { name: '采用此方案：偶遇' })
    await expect(adopt).toBeVisible()
    const idle = await adopt.evaluate(element => {
      const style = getComputedStyle(element), rgb = style.color.match(/\d+/g)!.slice(0, 3).map(Number)
      return { background: style.backgroundColor, monochrome: Math.max(...rgb) - Math.min(...rgb) < 2 }
    })
    expect(idle.monochrome).toBe(true)
    for (const action of [adopt, page.getByRole('button', { name: '编辑思路', exact: true })]) {
      const beforeHover = await action.boundingBox()
      const initialColor = await action.evaluate(element => getComputedStyle(element).color)
      await action.hover()
      await expect.poll(() => action.evaluate(element => getComputedStyle(element).color)).not.toBe(initialColor)
      await expect.poll(() => action.evaluate(element => getComputedStyle(element).textDecorationColor)).toBe('rgb(17, 17, 17)')
      const style = await action.evaluate(element => {
        const style = getComputedStyle(element)
        return { background: style.backgroundColor, border: style.borderTopWidth, shadow: style.boxShadow }
      })
      expect(style).toEqual({ background: 'rgba(0, 0, 0, 0)', border: '0px', shadow: 'none' })
      expect(await action.boundingBox()).toEqual(beforeHover)
    }
    await expect(page.getByText('思路已保存', { exact: true })).toHaveCount(0)
    const previous = await page.getByRole('button', { name: '查看上一批大纲' }).boundingBox()
    const next = await page.getByRole('button', { name: '查看下一批大纲' }).boundingBox()
    expect(previous!.y).toBeCloseTo(next!.y, 0)
    expect(previous!.x + previous!.width).toBeLessThan(next!.x)
  }
})

test('reduced motion shows the three choices without waiting for the split', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/preview/studio')
  await page.getByRole('textbox', { name: '大纲思路' }).fill('港口重逢')
  await page.getByRole('button', { name: '生成大纲方案' }).click()
  await expect(page.getByRole('button', { name: /方案 01/ })).toBeVisible({ timeout: 300 })
})

test('returning to the idea merges the papers before revealing text and controls', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await openOutline(page, 'saved chapter')
  await page.getByRole('button', { name: '生成大纲方案' }).click()
  const surface = page.locator('.original-outline-surface')
  await expect(surface.locator('.outline-candidate')).toHaveCount(3)
  await expect(surface).not.toHaveAttribute('data-text-phase', /.+/)
  const samples = await page.evaluate(async () => {
    const surface = document.querySelector('.original-outline-surface')!
    const frame = surface.querySelector('.studio-outline-frame')!
    const papers = [...surface.querySelectorAll('.studio-split-panels > i')]
    const frames: { elapsed: number; gap: number; centre: number; moving: boolean; inputVisible: boolean; controlsVisible: boolean }[] = []
    const start = performance.now()
    ;([...surface.querySelectorAll('button')].find(button => button.textContent === '编辑思路') as HTMLButtonElement).click()
    await new Promise<void>(resolve => {
      function sample() {
        const a = papers[0].getBoundingClientRect(), b = papers[1].getBoundingClientRect()
        const card = surface.querySelector('.studio-idea-card')!, input = card.querySelector('textarea')!
        const footer = surface.querySelector('.studio-outline-refresh-slot')!
        const shown = (element: Element) => getComputedStyle(element).visibility !== 'hidden' && Number(getComputedStyle(element).opacity) > .05
        frames.push({ elapsed: performance.now() - start, gap: b.left - a.right, centre: b.left + b.width / 2,
          moving: frame.getAnimations().some(animation => animation.playState === 'running'),
          inputVisible: shown(card) && shown(input), controlsVisible: shown(card) || shown(footer) })
        if (performance.now() - start < 1650) requestAnimationFrame(sample); else resolve()
      }
      requestAnimationFrame(sample)
    })
    return frames
  })
  const moving = samples.filter(sample => sample.moving)
  expect(moving.length).toBeGreaterThan(5)
  expect(moving.every(sample => !sample.inputVisible && !sample.controlsVisible), JSON.stringify(moving.filter(sample => sample.inputVisible || sample.controlsVisible).slice(0, 3))).toBe(true)
  expect(samples.some(sample => sample.gap > 1 && sample.gap < 70)).toBe(true)
  for (const sample of samples) expect(Math.abs(sample.centre - 960)).toBeLessThanOrEqual(1)
  expect(samples.at(-1)!.gap).toBeCloseTo(0, 0)
  expect(samples.at(-1)!.inputVisible).toBe(true)
  await expect(page.getByRole('textbox', { name: '大纲思路' })).toHaveValue('在港口安排一次重逢')
  await expect(page.getByRole('navigation', { name: '大纲历史批次' })).toHaveCount(0)
})

test('reduced motion returns to the idea immediately', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await openOutline(page, 'saved chapter')
  await page.getByRole('button', { name: '生成大纲方案' }).click()
  await page.getByRole('button', { name: '编辑思路' }).click()
  await expect(page.getByRole('textbox', { name: '大纲思路' })).toBeVisible({ timeout: 300 })
  await expect(page.getByRole('button', { name: '返回方案' })).toBeVisible({ timeout: 300 })
  await expect(page.locator('.studio-text-particles')).toHaveCount(0)
})

test('outgoing dust keeps the current text positions after resize and card scrolling', async ({ page }) => {
  await page.addInitScript(() => {
    const ink: { text: string; x: number; y: number }[] = []
    Object.assign(window, { outlineInk: ink })
    const fillText = CanvasRenderingContext2D.prototype.fillText
    CanvasRenderingContext2D.prototype.fillText = function (text, x, y, maxWidth) {
      ink.push({ text, x, y })
      if (maxWidth === undefined) fillText.call(this, text, x, y)
      else fillText.call(this, text, x, y, maxWidth)
    }
  })
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await openOutline(page, 'saved chapter')
  await page.getByRole('button', { name: '生成大纲方案' }).click()
  const surface = page.locator('.original-outline-surface')
  await expect(surface.locator('.outline-candidate')).toHaveCount(3)
  await expect(surface).not.toHaveAttribute('data-text-phase', /.+/)
  await page.setViewportSize({ width: 1280, height: 720 })
  await expect.poll(() => surface.locator('.studio-outline-frame').evaluate(element => element.getAnimations().filter(animation => animation.playState === 'running').length)).toBe(0)
  const firstCard = page.getByRole('region', { name: '方案全文：偶遇' })
  await firstCard.hover()
  await page.mouse.wheel(0, 220)
  await expect.poll(() => firstCard.evaluate(element => element.scrollTop)).toBeGreaterThan(200)
  const expected = await surface.evaluate(element => {
    const origin = element.getBoundingClientRect(), first = element.querySelector('.outline-candidate-scroll')!.getBoundingClientRect()
    const heading = element.querySelectorAll('h2')[1], range = document.createRange()
    range.setStart(heading.firstChild!, 0); range.setEnd(heading.firstChild!, 1)
    const glyph = range.getBoundingClientRect()
    ;(window as unknown as { outlineInk: unknown[] }).outlineInk.length = 0
    return { x: glyph.left - origin.left, top: glyph.top - origin.top, bottom: glyph.bottom - origin.top,
      firstLeft: first.left - origin.left, firstRight: first.right - origin.left }
  })
  await page.getByRole('button', { name: '编辑思路' }).click()
  const ink = await page.evaluate(() => (window as unknown as { outlineInk: { text: string; x: number; y: number }[] }).outlineInk)
  const title = ink.find(glyph => glyph.text === '追')!
  expect(title).toBeDefined()
  expect(title.x).toBeCloseTo(expected.x, 0)
  expect(title.y).toBeGreaterThanOrEqual(expected.top)
  expect(title.y).toBeLessThanOrEqual(expected.bottom)
  expect(ink.some(glyph => glyph.text === '方' && glyph.x >= expected.firstLeft && glyph.x < expected.firstRight)).toBe(false)
})
