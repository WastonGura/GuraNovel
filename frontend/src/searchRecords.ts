import type { Project, SettingCollection } from './api/client'
import type { SearchChoice } from './SearchDialog'
import { novelCoverSource } from './novelCover'

export interface SearchRecord extends SearchChoice { project?: Project; collection?: SettingCollection; introduction?: string }
export interface ProjectSearchRecord extends SearchRecord { project: Project; introduction: string }

export function projectSearchRecord(project: Project): ProjectSearchRecord {
  return {
    id: `project:${project.id}`, title: project.title, kind: '作品', project,
    introduction: typeof project.metadata.introduction === 'string' ? project.metadata.introduction : '',
    labels: Array.isArray(project.metadata.labels) ? [...new Set(project.metadata.labels.filter((label): label is string => typeof label === 'string').map(label => label.trim()).filter(Boolean))] : [],
    cover: novelCoverSource(project.metadata.cover),
  }
}

export function collectionSearchRecord(collection: SettingCollection): SearchRecord {
  return { id: `collection:${collection.id}`, title: collection.title, description: collection.description, kind: '设定集', collection, icon: '/ui/icons/collection.svg' }
}

export function projectStatusLabel(status: string): string | undefined {
  return ({ draft: '草稿', active: '创作中', completed: '已完成', archived: '已归档' } as Record<string, string>)[status]
}

export function matchesSearchRecord(item: SearchRecord, query: string): boolean {
  return `${item.title} ${item.description ?? ''} ${item.introduction ?? ''} ${item.labels?.join(' ') ?? ''} ${item.project?.slug ?? item.collection?.slug ?? ''} ${item.project?.genre ?? ''} ${item.project ? projectStatusLabel(item.project.status) ?? '' : ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
}
