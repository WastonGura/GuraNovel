import { expect, it } from 'vitest'
import { getApiBaseUrl } from './config'

it('uses the local API path when no API base URL is supplied', () => {
  expect(getApiBaseUrl()).toBe('/api/v1')
})
