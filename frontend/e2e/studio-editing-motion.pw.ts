/// <reference lib="dom" />
/// <reference types="vite/client" />
import { test, expect } from '@playwright/test'

const viewports = [
  { width: 2471, height: 1200, name: 'ultra-wide' },
  { width: 1440, height: 900, name: 'desktop-standard' },
  { width: 1100, height: 800, name: 'desktop-compact' },
  { width: 390, height: 844, name: 'mobile-narrow' },
]

test.describe('Studio Editing Comments and Continuous Motion E2E', () => {
  for (const vp of viewports) {
    test(`verifies zero whole-page scroll and layout stability at ${vp.name} (${vp.width}x${vp.height})`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height })
      await page.goto('/preview/studio')

      // 1. Initial load (Outline stage) zero whole-page scroll check
      const checkZeroScroll = async () => {
        const overflow = await page.evaluate(() => ({
          scrollHeight: document.documentElement.scrollHeight,
          clientHeight: document.documentElement.clientHeight,
          windowScrollY: window.scrollY,
        }))
        expect(overflow.scrollHeight).toBeLessThanOrEqual(overflow.clientHeight + 1)
        expect(overflow.windowScrollY).toBe(0)
      }

      await checkZeroScroll()

      // 2. Navigate to Draft stage
      const draftTab = page.getByRole('button', { name: 'Draft', exact: true })
      await expect(draftTab).toBeVisible()
      await draftTab.click()

      // Wait for manuscript viewport to mount
      const manuscript = page.locator('.studio-manuscript-viewport')
      await expect(manuscript).toBeVisible()
      await checkZeroScroll()

      // 3. Verify internal scrolling container exists
      const chapterScroll = page.locator('.studio-chapter-scroll')
      await expect(chapterScroll).toBeVisible()

      // 4. Pin the outline/context panel and check sticky positioning above edge masks
      const pinButton = page.getByRole('button', { name: '大纲面板', exact: true })
      if (await pinButton.isVisible()) {
        await pinButton.click()
        const contextWrap = page.locator('.studio-context-wrap')
        await expect(contextWrap).toHaveClass(/is-pinned/)
      }
      await checkZeroScroll()

      // 5. Open Gura assistant
      const launcher = page.getByRole('button', { name: 'Gura', exact: true })
      await expect(launcher).toBeVisible()
      await launcher.click()

      const assistantDialog = page.getByRole('dialog', { name: '与 Gura 对话' })
      await expect(assistantDialog).toBeVisible()
      await checkZeroScroll()

      // 6. Collapse assistant, then rapid reverse toggle
      const collapseBtn = page.getByRole('button', { name: '收起助手', exact: true })
      await collapseBtn.click()
      await expect(assistantDialog).toBeHidden()

      // Rapid reverse
      await launcher.click()
      await collapseBtn.click()
      await expect(assistantDialog).toBeHidden()
      await checkZeroScroll()

      // 7. Check Review stage comment isolation
      const reviewTab = page.getByRole('button', { name: 'Review', exact: true })
      if (await reviewTab.isVisible()) {
        await reviewTab.click()
        await checkZeroScroll()
        // Submitted comment dots should not be present in Review stage
        const submittedDots = page.locator('.studio-submitted-comments')
        await expect(submittedDots).toHaveCount(0)
      }
    })
  }

  test('Draft text selection, comment creation, and submission flow', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/preview/studio')

    // Go to Draft
    const draftTab = page.getByRole('button', { name: 'Draft', exact: true })
    await draftTab.click()

    const proseTextarea = page.getByRole('textbox', { name: '章节正文' })
    await expect(proseTextarea).toBeVisible()

    // Select text in prose
    await proseTextarea.focus()
    await proseTextarea.evaluate((el: HTMLTextAreaElement) => {
      el.setSelectionRange(0, 10)
      el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientX: 300, clientY: 300 }))
    })

    // "评论" action button appears
    const commentAction = page.getByRole('button', { name: '评论', exact: true })
    if (await commentAction.isVisible()) {
      await commentAction.click()

      // Floating comment box is shown
      const commentBox = page.locator('.studio-floating-comment')
      await expect(commentBox).toBeVisible()

      // Comment grip is present
      const grip = page.locator('.studio-comment-grip')
      await expect(grip).toBeVisible()

      // Fill in comment text
      const commentInput = page.getByPlaceholder('哪里需要再调整？')
      await commentInput.fill('需要补充环境描写的细节')

      // Send comment
      const sendButton = page.getByRole('button', { name: '发送正文修改意见' })
      await expect(sendButton).toBeEnabled()
      await sendButton.click()

      // A submitted comment dot appears in the context panel
      const submittedGroup = page.locator('.studio-submitted-comments')
      await expect(submittedGroup).toBeVisible()
    }
  })
})
