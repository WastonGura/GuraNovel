import { expect, test } from '@playwright/test'
import { openBookshelf } from './dashboardFixture'

test('bookshelf controls have no pointer-following halo and retain keyboard focus and actions', async ({ page }) => {
  await openBookshelf(page)
  const card = page.locator('.novel-card').first()
  const add = page.getByRole('button', { name: '新建作品', exact: true })
  for (const control of [card, add]) {
    await control.hover()
    await expect.poll(() => control.evaluate(element => {
      const style = getComputedStyle(element, '::after')
      return style.content === 'none' || style.display === 'none' || Number(style.opacity) === 0
    })).toBe(true)
  }
  await add.focus()
  await expect(add).toBeFocused()
  expect(await add.evaluate(element => parseFloat(getComputedStyle(element).outlineWidth))).toBeGreaterThan(0)
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: '创建作品' })
  await expect(dialog).toBeVisible()
  const cancel = dialog.getByRole('button', { name: '取消', exact: true })
  await cancel.hover()
  expect(await cancel.evaluate(element => getComputedStyle(element, '::after').backgroundImage)).toBe('none')
  await cancel.click()
  await expect(dialog).not.toBeVisible()
  await expect(add).toBeFocused()
})
