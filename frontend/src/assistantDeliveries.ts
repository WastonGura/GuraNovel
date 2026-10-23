import type { AssistantConversation } from './api/studioAssistantClient'
import { matchesAssistantAcknowledgement, type PendingAssistantMessage } from './assistantRecovery'
import { ApiError } from './api/client'

export type AssistantDelivery = { request: PendingAssistantMessage } & (
  | { status: 'sending' }
  | { status: 'delivered'; conversation: AssistantConversation }
  | { status: 'failed'; cause: unknown }
)

type DeliveryRecord = { value: AssistantDelivery; finished: Promise<void> }
const deliveries = new Map<string, DeliveryRecord>()
const listeners = new Map<string, Set<(delivery: AssistantDelivery) => void>>()

function publish(conversationId: string, delivery: AssistantDelivery) {
  const record = deliveries.get(conversationId)
  if (!record || record.value.request.id !== delivery.request.id) return
  record.value = delivery
  listeners.get(conversationId)?.forEach(listener => listener(delivery))
}

/** An actual page change can remount the panel while its same-conversation request continues. */
export function subscribeAssistantDelivery(conversationId: string, listener: (delivery: AssistantDelivery) => void) {
  const subscribers = listeners.get(conversationId) || new Set()
  subscribers.add(listener)
  listeners.set(conversationId, subscribers)
  const existing = deliveries.get(conversationId)
  if (existing) listener(existing.value)
  return () => { subscribers.delete(listener); if (!subscribers.size) listeners.delete(conversationId) }
}

export function beginAssistantDelivery(request: PendingAssistantMessage, transport: () => Promise<AssistantConversation>): Promise<void> {
  const previous = deliveries.get(request.conversationId)
  if (previous?.value.status === 'sending') return previous.finished
  const value: AssistantDelivery = { status: 'sending', request }
  const finished = Promise.resolve().then(transport).then(conversation => {
    if (conversation.id !== request.conversationId) throw new Error('会话响应与当前对话不一致。')
    if (!conversation.messages.some(message => matchesAssistantAcknowledgement(message, request))) {
      throw new ApiError(409, 'assistant_exchange_unconfirmed', '操作结果尚未确认，请先查看作品状态；原消息已保留，请勿重复执行。')
    }
    publish(request.conversationId, { status: 'delivered', request, conversation })
  }).catch(cause => { publish(request.conversationId, { status: 'failed', request, cause }) })
  deliveries.set(request.conversationId, { value, finished })
  publish(request.conversationId, value)
  return finished
}

/** A full browser reload discards this transport-only registry; durable recovery remains in storage. */
export function resetAssistantDeliveries() { deliveries.clear() }
