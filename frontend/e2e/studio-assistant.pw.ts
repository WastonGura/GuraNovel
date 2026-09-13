/// <reference lib="dom" />
/// <reference types="vite/client" />
import { test, expect } from '@playwright/test'

test('Studio Assistant drawer interaction and visual constraints in real browser', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/preview/studio')

  // Verify page does not have outer scrollbars
  const bodyOverflow = await page.evaluate(() => {
    return {
      windowScrollY: window.scrollY,
      scrollHeight: document.documentElement.scrollHeight,
      clientHeight: document.documentElement.clientHeight,
    }
  })
  expect(bodyOverflow.scrollHeight).toBeLessThanOrEqual(bodyOverflow.clientHeight)

  // Find and click the assistant launcher (shark icon button)
  const launcher = page.getByRole('button', { name: 'Gura', exact: true })
  await expect(launcher).toBeVisible()
  await launcher.click()

  // Assistant dialog appears
  const dialog = page.getByRole('dialog', { name: '与 Gura 对话' })
  await expect(dialog).toBeVisible()

  // Verify the textarea is focused
  const input = page.getByRole('textbox', { name: '给 Gura 的消息' })
  await expect(input).toBeFocused()

  // Type a query
  await input.fill('测试问答')
  const sendButton = page.getByRole('button', { name: '发送给 Gura（仅预览）' })
  await expect(sendButton).toBeEnabled()

  // Press Enter to send
  await input.press('Enter')
  await expect(page.getByText('测试问答')).toBeVisible()

  // Press Escape to close the drawer
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(launcher).toBeFocused()

  // Confirm outer page still has no overflow scroll
  const bodyOverflowAfter = await page.evaluate(() => {
    return {
      scrollHeight: document.documentElement.scrollHeight,
      clientHeight: document.documentElement.clientHeight,
    }
  })
  expect(bodyOverflowAfter.scrollHeight).toBeLessThanOrEqual(bodyOverflowAfter.clientHeight)
})
