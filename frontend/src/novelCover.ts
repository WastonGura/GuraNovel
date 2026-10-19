import { coverAssetVariants } from './coverAssets'
import { getApiBaseUrl } from './config'

export const novelCoverPresets = Array.from({ length: 8 }, (_, index) => `cover-${index + 1}.jpg`)
export const novelDefaultCover = 'cover-default'
export function novelCoverSource(value: unknown, fallback = novelDefaultCover): string {
  if (typeof value !== 'string') return fallback
  if (value === novelDefaultCover) return novelDefaultCover
  if (/^cover-upload:[a-f0-9]{64}$/.test(value)) return `${getApiBaseUrl().replace(/\/+$/, '')}/covers/${value.slice(13)}`
  if (novelCoverPresets.includes(value)) return `/ui/covers/${value}`
  try { const url = new URL(value); if (url.protocol === 'http:' || url.protocol === 'https:') return url.href } catch { /* Unknown values use the existing artwork. */ }
  return fallback
}


export function novelCoverSourceSet(source: string): string | undefined {
  const preset = source.match(/^\/ui\/covers\/(cover-[1-8])\.jpg$/)
  if (preset) return Object.entries(coverAssetVariants[preset[1] as keyof typeof coverAssetVariants]).map(([width, path]) => `${path} ${width}w`).join(', ')
  if (/\/covers\/[a-f0-9]{64}$/.test(source)) return [160, 320, 640, 1200].map(width => `${source}?w=${width} ${width}w`).join(', ')
  return undefined
}
