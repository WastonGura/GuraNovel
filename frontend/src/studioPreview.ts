import type { ChapterProductionReviewReport, ChapterProductionState, ChapterProductionStatus, ReviewRevisionSelection } from './api/chapterProductionV2Client'

export type OutlineComment = { id: string; start: number; end: number; quote: string; color: string; text: string; submitted?: boolean; orphaned?: boolean }
export const commentColors = ['#8d9bff', '#ff858d', '#ffbe69', '#8fcba9', '#be94df', '#70bdcf', '#447ac2', '#b96972', '#af8c31', '#458466', '#8256a9', '#377b86']
export const commentLimit = commentColors.length

export function restoreOutlineComments(value: unknown, outline: string): OutlineComment[] {
  if (!Array.isArray(value)) return []
  const ids = new Set<string>()
  const used = new Set<string>()
  return value.filter((item): item is OutlineComment => {
    if (!item || typeof item.id !== 'string' || ids.has(item.id) || !Number.isInteger(item.start) || !Number.isInteger(item.end)
      || item.start < 0 || item.end < item.start || item.end > outline.length || typeof item.quote !== 'string'
      || typeof item.text !== 'string' || !commentColors.includes(item.color)) return false
    ids.add(item.id)
    return true
  }).map(item => {
    const color = used.has(item.color) ? commentColors.find(color => !used.has(color)) || item.color : item.color
    used.add(color)
    return { ...item, color, submitted: item.submitted === true, orphaned: item.orphaned === true }
  })
}

// Re-anchor comments around the changed span; retain comments whose source was deleted.
export function reanchorComments(comments: OutlineComment[], before: string, after: string): OutlineComment[] {
  let start = 0, end = before.length, nextEnd = after.length
  while (start < end && start < nextEnd && before[start] === after[start]) start++
  while (end > start && nextEnd > start && before[end - 1] === after[nextEnd - 1]) { end--; nextEnd-- }
  const delta = after.length - before.length
  return comments.map(comment => {
    if (comment.orphaned) return comment
    const from = comment.start <= start ? comment.start : comment.start >= end ? comment.start + delta : start
    const to = comment.end <= start ? comment.end : comment.end >= end ? comment.end + delta : nextEnd
    return { ...comment, start: from, end: to, quote: after.slice(from, to) || comment.quote }
  })
}

// Formal feedback retains the original quote when its position becomes invalid.
export function reanchorFeedbackComments(comments: OutlineComment[], before: string, after: string): OutlineComment[] {
  let start = 0, end = before.length, nextEnd = after.length
  while (start < end && start < nextEnd && before[start] === after[start]) start++
  while (end > start && nextEnd > start && before[end - 1] === after[nextEnd - 1]) { end--; nextEnd-- }
  return comments.map(comment => {
    if (comment.orphaned) return comment
    const next = comment.end <= start ? comment : comment.start >= end
      ? { ...comment, start: comment.start + nextEnd - end, end: comment.end + nextEnd - end } : null
    return next && after.slice(next.start, next.end) === comment.quote ? next : { ...comment, start: 0, end: 0, orphaned: true }
  })
}

export const stages = ['Outline', 'Draft', 'Review', 'Reader', 'Final'] as const
export const readerStageKey = (chapterId: string, versionId: string) => `guranovel:studio-reader:${chapterId}:${versionId}`
export type Stage = typeof stages[number]
export type DraftArchive = { id: string; createdAt: string; summary: string; draft?: string; comments?: OutlineComment[]; requirements?: string; feedbackAvailable?: boolean; documentId?: string; versionId?: string }
export const archiveStorageKey = 'guranovel:studio-archives:preview:v1'
export function loadDraftArchives(): Record<string, DraftArchive[]> {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(archiveStorageKey) || 'null')
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return {}
    return Object.fromEntries(Object.entries(saved).map(([chapterId, entries]) => [chapterId, Array.isArray(entries) ? entries.filter((entry): entry is DraftArchive => entry && typeof entry.id === 'string' && typeof entry.draft === 'string' && typeof entry.summary === 'string' && typeof entry.createdAt === 'string' && Number.isFinite(Date.parse(entry.createdAt))).map(entry => ({ ...entry, comments: restoreOutlineComments(entry.comments, entry.draft!), requirements: typeof entry.requirements === 'string' ? entry.requirements : undefined, feedbackAvailable: entry.feedbackAvailable === true && typeof entry.requirements === 'string' })) : []]))
  } catch { return {} }
}
export const archiveTime = (value: string) => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
export const reviewers = ['Editor Reviewer', 'Chief Reviewer', 'Lore Reviewer'] as const
export type StudioIssue = { id: string; reviewer: typeof reviewers[number]; level: 'Block' | 'Warning' | 'Suggestion'; title: string; detail: string; quote: string }
export type StudioChapter = {
  id: string; number: number; title: string; volume: string; stage: Stage
  outlineStep: 'new' | 'choose' | 'edit'; idea: string; outline: string; draft: string; requirements: string
  review: 'idle' | 'running' | 'done'; completed: number; issues: StudioIssue[]; selected: string[]
  readers: string[]; discussion: boolean; published: boolean
  documentId?: string; versionId?: string
  outlineDocumentId?: string; outlineVersionId?: string; feedbackReadOnly?: boolean
  outlineComments?: OutlineComment[]
  draftComments?: OutlineComment[]
  reviewFinishedAt?: number
  productionStatus?: ChapterProductionStatus
  revisionRequest?: ReviewRevisionSelection
  feedbackRequest?: import('./studioProduction').StudioFeedbackRequest
  productionState?: ChapterProductionState
  productionError?: string
  reviewReports?: ChapterProductionReviewReport[]
  chiefEditorRequired?: boolean
}

export const previewProse = `雨是在黄昏停下来的。

林远推开街角那家食堂的门，门上的风铃迟了一拍，才发出清脆的响声。热汤的白汽越过柜台，把玻璃上的城市映成了一片模糊的金色。

“老样子？”老板没有抬头。

他点了点头，把湿透的外套搭在椅背上。口袋里的金属碎片轻轻碰了一下桌沿，声音很小，却让窗边的女孩停住了筷子。

她看向他，又像什么都没有发生过一样，低下头去。

林远突然觉得，这个再平常不过的傍晚，也许并不只是一个傍晚。`

export const outlineOptions = [
  { title: '日常中的异响', text: '用一顿晚饭拉近人物距离。林远在食堂遇见陌生女孩，金属碎片的轻响让她暴露了对旧城事件的了解。以她留下的一张车票收尾。' },
  { title: '错过的来信', text: '雨后，林远回到旧居，发现一封寄往三年前的信。他循着信中的地址来到食堂，却发现收信人早已等在那里。' },
  { title: '两个人的秘密', text: '从女孩的视角切入，写她观察林远的过程。晚饭中的试探逐渐接近真相，但两个人都选择保留最后一句话。' },
]
export const readerPersonas = [
  ['plot', '剧情党', '关注节奏、悬念与伏笔回收'], ['character', '角色党', '关注人物动机与关系变化'],
  ['world', '设定党', '关注世界规则与逻辑一致性'], ['emotion', '情感党', '关注共鸣与情绪的递进'],
  ['language', '文字党', '关注表达、意象与阅读体验'], ['casual', '休闲读者', '以轻松阅读的第一感受为主'],
] as const

export const previewIssues: StudioIssue[] = [
  { id: 'timeline', reviewer: 'Lore Reviewer', level: 'Block', title: '时间线需要统一', detail: '前文仍在下雨，这里已进入雨后的傍晚。补充时间过渡，避免场景衔接断裂。', quote: '雨是在黄昏停下来的。' },
  { id: 'motivation', reviewer: 'Chief Reviewer', level: 'Warning', title: '女孩的反应缺少铺垫', detail: '可以补充她对金属碎片的辨认细节，让停下筷子的动作有更清晰的依据。', quote: '却让窗边的女孩停住了筷子。' },
  { id: 'ending', reviewer: 'Editor Reviewer', level: 'Suggestion', title: '结尾可以更克制', detail: '尝试用一个具体动作承接悬念，减少对转折的直接说明。', quote: '也许并不只是一个傍晚。' },
]

export function newStudioChapter(id: string, number: number, title = '未命名章节', volume = '第一卷'): StudioChapter {
  return { id, number, title, volume, stage: 'Outline', outlineStep: 'new', idea: '', outline: '', draft: '', requirements: '', review: 'idle', completed: 0, issues: [], selected: [], readers: [], discussion: false, published: false }
}

export function initialPreview(): StudioChapter[] {
  return Array.from({ length: 10 }, (_, i) => ({
    ...newStudioChapter(`preview-${i + 1}`, i + 1, ['雨停之前', '旧街来信', '玻璃回声', '未寄出的信', '夜行列车', '第七码头', '海面以下', '慢半拍', '重逢', '吃吃吃'][i], i < 4 ? '第一卷' : i < 8 ? '第二卷' : '第三卷'),
    draft: previewProse, outline: outlineOptions[0].text,
    stage: i === 9 ? 'Outline' as const : 'Final' as const, published: i !== 9,
  }))
}
