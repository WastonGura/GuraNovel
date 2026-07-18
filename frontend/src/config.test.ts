import { expect, it } from 'vitest'
import { apiBaseUrl } from './config'

it('uses the local API path when no API base URL is supplied', () => {
  expect(apiBaseUrl).toBe('/api/v1')
})
