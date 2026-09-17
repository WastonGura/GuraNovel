import { useLayoutEffect, useRef, useState } from 'react'
import { MotionFrame, StreamText } from './StudioMotion'
import { categoryNames, stagedSettingComments, type SettingChange, type SettingConversation as Conversation, type SettingNote } from './settingNotes'

function ChangeCard({ change, disabled, referencingProjects, onEdit, onAccept, onDismiss, onOpen }: {
  change: SettingChange
  disabled: boolean
  referencingProjects?: { id: string; title: string }[]
  onEdit: (body: string) => void
  onAccept: () => void
  onDismiss: () => void
  onOpen: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const pending = change.status === 'pending'
  const sourceItems = [
    change.sourceTask?.novelTitle,
    change.sourceTask?.agentRole,
    change.sourceTask?.chapterId ? `章节: ${change.sourceTask.chapterId}` : '',
  ].filter(Boolean)

  return <MotionFrame className={`setting-change is-${change.status}${expanded ? ' is-expanded' : ''}`} chrome={<div className="setting-change-actions">{pending ? <><button disabled={disabled} onClick={onDismiss}>暂不采用</button><button disabled={disabled || !change.body.trim()} onClick={onAccept}>接受此条</button></> : change.status === 'accepted' ? <button onClick={onOpen}>查看条目</button> : null}</div>}>
    <div className="setting-change-heading">
      <button className="setting-change-toggle" aria-expanded={expanded} aria-label={`查看提案：${change.title}`} onClick={() => setExpanded(!expanded)}>
        <img src="/ui/studio/ooui-collapse.svg" alt="" /><small>{change.before ? '补充' : '新建'}</small><span>{change.title}</span>
      </button>
      <small>{pending ? categoryNames[change.category] : change.status === 'accepted' ? '已接受' : '未采用'}</small>
    </div>
    <div className="setting-change-content" aria-hidden={!expanded} inert={!expanded}><div className="setting-change-body">
      {change.reason && <div className="setting-change-meta-row setting-change-reason"><small>调整理由：</small><span>{change.reason}</span></div>}
      {sourceItems.length > 0 && <div className="setting-change-meta-row setting-change-source"><small>来源任务：</small><span>{sourceItems.join(' · ')}</span></div>}
      {change.baseVersionId && <div className="setting-change-meta-row setting-change-base"><small>基准版本：</small><code>{change.baseVersionId.slice(0, 8)}</code></div>}
      {referencingProjects && referencingProjects.length > 0 && <div className="setting-change-impact-warning" role="note">共享设定变更将影响关联小说后续任务：{referencingProjects.map(p => p.title).join('、')}</div>}
      {change.before && <details><summary>修改前的内容</summary><p>{change.before.body || '暂无正文'}</p></details>}
      {pending ? <textarea aria-label={`提案正文：${change.title}`} value={change.body} maxLength={30000} onChange={event => onEdit(event.target.value)} disabled={disabled} /> : <p>{change.body}</p>}
    </div></div>
  </MotionFrame>
}

export default function SettingConversation({
  conversation,
  notes,
  visible,
  disabled,
  referencingProjects,
  onPatch,
  onSend,
  onCommentChange,
  onExample,
  onAccept,
  onChange,
  onOpen,
  onScroll,
}: {
  conversation: Conversation
  notes: SettingNote[]
  visible: boolean
  disabled: boolean
  referencingProjects?: { id: string; title: string }[]
  onPatch: (patch: Partial<Conversation>) => void
  onSend: () => void
  onExample: () => void
  onCommentChange: (noteId: string, commentId: string, text: string) => void
  onAccept: (ids: string[]) => void
  onChange: (id: string, patch: Partial<SettingChange>) => void
  onOpen: (note: SettingNote) => void
  onScroll: (top: number) => void
}) {
  const history = useRef<HTMLDivElement>(null), input = useRef<HTMLTextAreaElement>(null)
  const staging = useRef<HTMLDetailsElement>(null)
  const position = useRef(conversation.scrollTop), count = useRef(conversation.messages.length)
  const context = notes.find(note => note.id === conversation.contextId)
  const staged = stagedSettingComments(notes, conversation)
  const canSend = Boolean(conversation.draft.trim() || staged.length) && staged.every(({ comment }) => comment.text.trim())
  useLayoutEffect(() => {
    if (visible) { history.current!.scrollTop = position.current; input.current?.focus({ preventScroll: true }) }
    else if (staging.current) staging.current.open = false
  }, [visible])
  useLayoutEffect(() => {
    if (visible && count.current !== conversation.messages.length) history.current!.scrollTop = history.current!.scrollHeight
    count.current = conversation.messages.length
  }, [visible, conversation.messages.length])
  return <section className="setting-conversation" aria-label="设定 Agent 会话" hidden={!visible}>
    <header><div><h1>设定对话</h1><small>围绕整部作品，持续构思</small></div><button className="setting-example" disabled={disabled} onClick={onExample}>示例回复</button></header>
    <div className="setting-conversation-history" ref={history} role="log" aria-label="设定对话记录" tabIndex={0} onScroll={event => {
      if (visible) { position.current = event.currentTarget.scrollTop; onScroll(position.current) }
    }}>
      {!conversation.messages.length && <div className="setting-conversation-empty"><img src="/ui/studio/shark.svg" alt="" /><h2>从哪里开始？</h2><p>聊角色、地点和规则。确认后的条目会出现在左侧。</p><button disabled={disabled} onClick={onExample}>体验连续创建</button></div>}
      {conversation.messages.map(message => <article className={`setting-conversation-message is-${message.role}`} key={message.id}>
        <small>{message.role === 'user' ? '你 · 暂存本机，尚未发送' : '设定 Agent · 示例回复'}</small>
        {message.role === 'assistant' ? <StreamText text={message.text} startedAt={message.createdAt} delay={0} /> : <p>{message.text}</p>}
        {!!message.changes.length && <div className="setting-change-batch">
          <div className="setting-batch-heading"><small>{message.changes.filter(change => change.status === 'pending').length} 条待确认变更</small>{message.changes.some(change => change.status === 'pending') && <button disabled={disabled} onClick={() => onAccept(message.changes.filter(change => change.status === 'pending').map(change => change.id))}>接受本批</button>}</div>
          {message.changes.map(change => <ChangeCard key={change.id} change={change} disabled={disabled} referencingProjects={referencingProjects} onEdit={body => onChange(change.id, { body })} onAccept={() => onAccept([change.id])} onDismiss={() => onChange(change.id, { status: 'dismissed' })} onOpen={() => { const note = notes.find(note => note.id === change.noteId); if (note) onOpen(note) }} />)}
        </div>}
      </article>)}
    </div>
    <form className="setting-conversation-composer" onSubmit={event => { event.preventDefault(); onSend() }}>
      {!!staged.length && <details ref={staging} className="setting-comment-staging" aria-label="暂存评论">
        <summary><span className="setting-staged-colors" aria-hidden="true">{staged.slice(0, 5).map(({ note, comment }) => <i key={`${note.id}:${comment.id}`} style={{ background: comment.color }} />)}</span><span>{staged.length} 条评论<small>待发送 · 可继续补充</small></span><img src="/ui/studio/ooui-collapse.svg" alt="" /></summary>
        <div className="setting-staged-list">{staged.map(({ note, comment }, index) => <div className="setting-staged-comment" key={`${note.id}:${comment.id}`}>
          <div><button type="button" onClick={() => onOpen(note)}>{note.title}</button><button type="button" disabled={disabled} aria-label={`移回条目：${comment.quote}`} onClick={() => onPatch({ stagedComments: conversation.stagedComments?.filter(item => item.noteId !== note.id || item.commentId !== comment.id) })}>移回条目</button></div>
          <blockquote>{comment.quote}<small>{comment.start === comment.end ? '（原文已删除）' : ''}</small></blockquote>
          <textarea aria-label={`暂存评论 ${index + 1} 的修改意见`} value={comment.text} disabled={disabled} placeholder="补充这条评论的想法…" onChange={event => onCommentChange(note.id, comment.id, event.target.value)} />
        </div>)}</div>
      </details>}
      {context && <div className="setting-discussion-context"><button type="button" onClick={() => onOpen(context)}>正在讨论 · {context.title}</button><button type="button" aria-label="移除讨论条目" onClick={() => onPatch({ contextId: null })}>×</button></div>}
      <textarea ref={input} rows={1} aria-label="给设定 Agent 的消息" aria-describedby="setting-chat-status" placeholder={context ? `想怎样完善「${context.title}」？` : '说说你的想法，或接着上次的话题…'} maxLength={6000} value={conversation.draft} disabled={disabled} onChange={event => onPatch({ draft: event.target.value })} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) { event.preventDefault(); onSend() }
      }} />
      <div className="setting-composer-footer"><small id="setting-chat-status">交互预览 · Agent 尚未接入 · 消息与提案保存在本机</small><button type="submit" className="setting-chat-send" aria-label="发送设定消息（仅预览）" disabled={disabled || !canSend}><img src="/ui/studio/send.svg" alt="" /></button></div>
    </form>
  </section>
}
