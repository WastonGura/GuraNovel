import { reanchorComments, restoreOutlineComments, type OutlineComment } from './studioPreview'

export type SettingCategory = 'setting' | 'world'
export type SettingNote = { id: string; category: SettingCategory; title: string; body: string; comments?: OutlineComment[] }
export type SettingChange = { id: string; noteId: string; title: string; body: string; category: SettingCategory; before: SettingNote | null; status: 'pending' | 'accepted' | 'dismissed' }
export type SettingMessage = { id: string; role: 'user' | 'assistant'; text: string; changes: SettingChange[]; createdAt?: number }
export type SettingConversation = { messages: SettingMessage[]; draft: string; contextId: string | null; scrollTop: number; stagedComments?: { noteId: string; commentId: string }[] }
export type SettingWorkspace = { notes: SettingNote[]; conversation: SettingConversation }
export const emptySettingConversation = (): SettingConversation => ({ messages: [], draft: '', contextId: null, scrollTop: 0 })
export const settingStorageKey = 'guranovel:setting-preview:v1'
export const categoryNames = { setting: '设定', world: '世界观' }

export function stagedSettingComments(notes: SettingNote[], conversation: SettingConversation) {
  return (conversation.stagedComments || []).flatMap(({ noteId, commentId }) => {
    const note = notes.find(note => note.id === noteId), comment = note?.comments?.find(comment => comment.id === commentId && !comment.submitted)
    return note && comment ? [{ note, comment }] : []
  })
}

export const sampleSettingNotes: SettingNote[] = [
  { id: 'intro', category: 'setting', title: '设定', body: '正文部分' },
  { id: 'lin', category: 'setting', title: '林远', body: '旧街上的普通青年。他在雨停之后走进 [[街角食堂]]，口袋里带着一枚 [[金属碎片]]。\n\n他并不知道，自己的到来已经被 [[窗边的女孩]] 注意到了。' },
  { id: 'girl', category: 'setting', title: '窗边的女孩', body: '她总坐在 [[街角食堂]] 靠窗的位置，能辨认 [[金属碎片]] 上的刻痕。\n\n她认识 [[林远]]，却暂时没有告诉他原因。' },
  { id: 'owner', category: 'setting', title: '食堂老板', body: '在 [[旧街]] 经营了很多年，熟悉这里每一位常客。\n\n他注意到了 [[窗边的女孩]] 停下筷子的动作，但没有追问。' },
  { id: 'fragment', category: 'setting', title: '金属碎片', body: '边缘泛着银色，表面刻着不完整的线条。它与 [[动量干涉]] 有关，被 [[林远]] 带进了 [[街角食堂]]。' },
  { id: 'letter', category: 'setting', title: '未寄出的信', body: '没有署名的信封，收件地址是 [[第七码头]]。\n\n信中提到了 [[潮汐档案]]。' },
  { id: 'city', category: 'world', title: '海滨城', body: '一座沿海而建的城市。[[旧街]] 位于老城区，沿海一侧连着 [[第七码头]]。' },
  { id: 'street', category: 'world', title: '旧街', body: '雨后总是比其他地方安静。[[街角食堂]] 开在这里，门上的风铃已经用了很多年。\n\n从这里可以步行前往 [[第七码头]]。' },
  { id: 'diner', category: 'world', title: '街角食堂', body: '由 [[食堂老板]] 经营的一间小食堂，位于 [[旧街]] 转角。\n\n[[林远]] 与 [[窗边的女孩]] 在这里相遇。' },
  { id: 'pier', category: 'world', title: '第七码头', body: '[[海滨城]] 最早建成的码头之一。关于 [[金属碎片]] 的旧记录，可能仍保存在附近。' },
  { id: 'momentum', category: 'world', title: '动量干涉', body: '对运动状态施加影响的现象，需要借助特定介质。\n\n[[金属碎片]] 是目前已知的线索之一。任何使用都需要付出代价。' },
]

export function noteLinks(body: string): string[] {
  return [...new Set([...body.matchAll(/\[\[([^\]\n[]+)\]\]/g)].map(match => match[1].trim()).filter(Boolean))]
}

export type NoteGraph = {
  nodes: { id: string; title: string; category?: SettingCategory; missing: boolean }[]
  edges: { source: string; target: string }[]
}

// Ambiguous titles stay unresolved; never silently link to a different note.
export function resolveNote(notes: SettingNote[], title: string): SettingNote | undefined {
  const matches = notes.filter(note => note.title.trim() === title.trim())
  return matches.length === 1 ? matches[0] : undefined
}

export function buildNoteGraph(notes: SettingNote[]): NoteGraph {
  const nodes: NoteGraph['nodes'] = notes.map(note => ({ ...note, missing: false }))
  const edges: NoteGraph['edges'] = []
  const seen = new Set<string>()
  for (const note of notes) for (const title of noteLinks(note.body)) {
    const target = resolveNote(notes, title)?.id || `missing:${title}`
    if (target === note.id) continue
    if (!nodes.some(node => node.id === target)) nodes.push({ id: target, title, missing: true })
    const key = JSON.stringify([note.id, target].sort())
    if (!seen.has(key)) { seen.add(key); edges.push({ source: note.id, target }) }
  }
  return { nodes, edges }
}

function isNote(note: unknown): note is SettingNote {
  if (!note || typeof note !== 'object') return false
  const value = note as SettingNote
  return typeof value.id === 'string' && typeof value.title === 'string' && typeof value.body === 'string' && ['setting', 'world'].includes(value.category)
    && (value.comments === undefined || (Array.isArray(value.comments) && restoreOutlineComments(value.comments, value.body).length === value.comments.length
      && value.comments.every(comment => comment.submitted === undefined || typeof comment.submitted === 'boolean')))
}

export function readSettingWorkspace(): SettingWorkspace {
  const raw = localStorage.getItem(settingStorageKey)
  if (!raw) return { notes: sampleSettingNotes, conversation: emptySettingConversation() }
  const stored = JSON.parse(raw)
  // Read the earlier array format without clearing or migrating it until the next save.
  const { notes, conversation } = Array.isArray(stored) ? { notes: stored, conversation: emptySettingConversation() } : stored || {}
  if (!Array.isArray(notes) || !notes.every(isNote)
    || new Set(notes.map(note => note.id)).size !== notes.length) throw new Error('Invalid setting notes')
  if (!conversation || typeof conversation.draft !== 'string' || !(conversation.contextId === null || typeof conversation.contextId === 'string')
    || !Number.isFinite(conversation.scrollTop) || conversation.scrollTop < 0 || !Array.isArray(conversation.messages)
    || !conversation.messages.every((message: SettingMessage) => message && typeof message.id === 'string' && ['user', 'assistant'].includes(message.role)
      && typeof message.text === 'string' && (message.role === 'user' || Number.isFinite(message.createdAt)) && Array.isArray(message.changes) && message.changes.every(change => change && typeof change.id === 'string'
        && typeof change.noteId === 'string' && isNote({ ...change, id: change.noteId }) && ['pending', 'accepted', 'dismissed'].includes(change.status)
        && (change.before === null || isNote(change.before))))) throw new Error('Invalid setting conversation')
  const messages = conversation.messages as SettingMessage[], changes = messages.flatMap(message => message.changes)
  if (conversation.stagedComments !== undefined && (!Array.isArray(conversation.stagedComments)
    || !conversation.stagedComments.every((item: { noteId: string; commentId: string }) => item && typeof item.noteId === 'string' && typeof item.commentId === 'string')
    || stagedSettingComments(notes, conversation).length !== conversation.stagedComments.length
    || new Set(conversation.stagedComments.map((item: { noteId: string; commentId: string }) => JSON.stringify([item.noteId, item.commentId]))).size !== conversation.stagedComments.length)) throw new Error('Invalid staged comments')
  if (new Set(messages.map(message => message.id)).size !== messages.length || new Set(changes.map(change => change.id)).size !== changes.length) throw new Error('Duplicate conversation ids')
  return { notes: notes.map(note => note.comments ? { ...note, comments: restoreOutlineComments(note.comments, note.body) } : note), conversation }
}

export function readSettingNotes(): SettingNote[] { return readSettingWorkspace().notes }

export function applySettingChanges(notes: SettingNote[], changes: SettingChange[]): SettingNote[] {
  const next = [...notes]
  for (const change of changes.filter(change => change.status === 'pending')) {
    const title = change.title.trim(), current = next.find(note => note.id === change.noteId)
    if (!title || title.length > 100 || /[\]\n[]/.test(title) || !change.body.trim() || change.body.length > 30000) throw new Error('请补全条目内容，标题最多 100 字，正文最多 30000 字。')
    if (next.some(note => note.id !== change.noteId && note.title.trim() === title)) throw new Error(`「${title}」已存在，请先处理同名条目。`)
    if (change.before ? !current || current.title !== change.before.title || current.body !== change.before.body || current.category !== change.before.category : current) throw new Error(`「${title}」已发生变化，请查看最新条目后重新讨论。`)
    const note = { id: change.noteId, title, body: change.body, category: change.category, ...(current?.comments ? { comments: reanchorComments(current.comments, current.body, change.body) } : {}) }
    if (current) next.splice(next.indexOf(current), 1, note)
    else next.push(note)
  }
  return next
}

// Explicit UI fixtures, never presented as model output for an arbitrary user message.
export function settingExampleReply(notes: SettingNote[], conversation: SettingConversation): SettingMessage {
  const changes: SettingChange[] = [], context = notes.find(note => note.id === conversation.contextId)
  const pendingTitles = new Set(conversation.messages.flatMap(message => message.changes).filter(change => change.status === 'pending').map(change => change.title))
  const propose = (title: string, body: string, category: SettingCategory, before: SettingNote | null = null) => {
    if (pendingTitles.has(title)) return
    changes.push({ id: crypto.randomUUID(), noteId: before?.id || crypto.randomUUID(), title, body, category, before, status: 'pending' })
  }
  if (context) propose(context.title, `${context.body}\n\n可以进一步补充它与 [[旧港仓库]] 的联系，以及这段联系对故事人物的影响。`, context.category, { ...context })
  else {
    const examples: [string, string, SettingCategory][] = [
      ['灰潮会', '活跃在 [[海滨城]] 旧港的地下组织，以替人寻找失物为表面生意。成员在 [[旧港仓库]] 交换消息。\n\n它真正掌握的，是港口货物去向与城市人情往来的记录。', 'setting'],
      ['旧港仓库', '位于 [[海滨城]] 旧码头的一座废弃仓库。白天有人在这里修补渔网，入夜后成为 [[灰潮会]] 的会面地点。', 'world'],
      ['潮汐暗号', '[[灰潮会]] 用潮位与风向组合成约定暗号，决定当天是否可以进入 [[旧港仓库]]。暗号过期即失效。', 'setting'],
      ['守仓人', '负责看守 [[旧港仓库]]，从不公开承认与 [[灰潮会]] 的关系。他能辨认 [[潮汐暗号]]，但只会替自己信任的人开门。', 'setting'],
    ]
    for (const [title, body, category] of examples) if (!notes.some(note => note.title === title) && !pendingTitles.has(title) && changes.length < 2) propose(title, body, category)
    const city = notes.find(note => note.title === '海滨城')
    if (city && !city.body.includes('[[灰潮会]]')) propose(city.title, `${city.body}\n\n旧港的 [[灰潮会]] 以 [[旧港仓库]] 为据点，其影响沿码头的人情网络延伸。`, city.category, { ...city })
  }
  return { id: crypto.randomUUID(), role: 'assistant', createdAt: Date.now(), text: changes.length ? '这是一组示例提案。可以展开调整内容，再逐条接受或接受这一批；确认后，我们继续讨论其他条目。' : '目前的示例提案已展示。可以先处理待确认内容，或打开一个条目，通过「交给 Agent 讨论」体验补充已有设定。', changes }
}
