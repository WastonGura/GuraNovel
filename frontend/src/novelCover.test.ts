import { expect, it } from 'vitest'
import { novelCoverSource, novelCoverSourceSet } from './novelCover'

it('uses the neutral cover for missing/invalid metadata and preserves an explicit default choice', () => {
  expect(novelCoverSource(undefined)).toBe('cover-default')
  expect(novelCoverSource('')).toBe('cover-default')
  expect(novelCoverSource('cover-default')).toBe('cover-default')
  expect(novelCoverSourceSet('cover-default')).toBeUndefined()
})

it('preserves explicit presets, uploads and external links', () => {
  expect(novelCoverSource('cover-6.jpg')).toBe('/ui/covers/cover-6.jpg')
  expect(novelCoverSource('cover-upload:' + 'a'.repeat(64))).toContain('/covers/' + 'a'.repeat(64))
  expect(novelCoverSource('https://example.test/cover.png')).toBe('https://example.test/cover.png')
  expect(novelCoverSource('invalid', '')).toBe('')
})
