import { expect, test } from '@playwright/test'
import { openBookshelf } from './dashboardFixture'

test('novel modal animates in and stays mounted until its exit finishes', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.route('**/api/v1/projects/novel-0/chapters', route => route.fulfill({ json: [] }))
  await openBookshelf(page)
  const trigger = page.locator('.novel-card').first()
  await trigger.click()
  const dialog = page.locator('dialog.novel-dialog')
  await expect(dialog).toBeVisible()
  expect(await dialog.evaluate(el => el.getAnimations().some(animation => animation.playState === 'running'))).toBe(true)
  await dialog.getByRole('button', { name: '关闭作品详情' }).click()
  // Check in-flight motion and modal state before detachment, not just a declared animation.
  const exiting = await dialog.evaluate(el => ({ open: (el as HTMLDialogElement).open, animations: el.getAnimations().filter(animation => animation.playState === 'running').length }))
  expect(exiting.open).toBe(true)
  expect(exiting.animations).toBeGreaterThan(0)
  await expect(dialog).toHaveCount(0)
  await expect(trigger).toBeFocused()
  await page.getByRole('button', { name: '新建作品', exact: true }).click()
  const create = page.locator('dialog.novel-dialog')
  await create.getByRole('button', { name: '关闭创建作品', exact: true }).click()
  await expect(create).toHaveClass(/is-closing/)
  await expect(create).toHaveCount(0)
})

test('reduced motion dismisses without waiting for a scale animation', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await openBookshelf(page, false)
  await page.getByRole('button', { name: '新建作品', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '创建作品', exact: true })
  await dialog.getByRole('button', { name: '关闭创建作品', exact: true }).click()
  expect(await page.locator('dialog').count()).toBe(0)
  await expect(page.getByRole('button', { name: '新建作品', exact: true })).toBeFocused()
})
