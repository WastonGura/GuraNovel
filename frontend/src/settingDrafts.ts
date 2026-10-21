import type { SettingNote } from './settingNotes'

export type CreationDraft = { note: SettingNote; request?: SettingNote }
export function settingDraftKey(scope: string) { return `guranovel:setting-creation:${scope}` }
export function readCreationDrafts(scope: string): CreationDraft[] {
  try {
    const data: unknown = JSON.parse(sessionStorage.getItem(settingDraftKey(scope)) || '[]')
    if (!Array.isArray(data)) return []
    return data.filter((entry): entry is CreationDraft => Boolean(entry && typeof entry.note?.id === 'string' && typeof entry.note?.title === 'string' && typeof entry.note?.body === 'string' && ['setting', 'world'].includes(entry.note?.category)))
  } catch { return [] }
}
export function writeCreationDrafts(scope: string, drafts: CreationDraft[]) {
  sessionStorage.setItem(settingDraftKey(scope), JSON.stringify(drafts))
}
