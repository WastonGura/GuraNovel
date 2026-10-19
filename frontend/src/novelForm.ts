import { novelDefaultCover } from './novelCover'

export interface NovelFormValues {
  title: string; platform: string; collectionId: string
  introduction: string; labels: string; cover: string
}

export const emptyNovelForm: NovelFormValues = { title: '', platform: '', collectionId: 'new', introduction: '', labels: '', cover: novelDefaultCover }
