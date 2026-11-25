export type ConversationIdentityInfo = {
  platform: string
  chat_id: string
  chat_name?: string | null
  chat_type?: string | null
}

export function conversationKind(chat: ConversationIdentityInfo) {
  if (chat.platform === "wechat" && chat.chat_id.startsWith("gh_")) return "公众号"
  return ({ group: "群聊", private: "私聊", channel: "频道" } as Record<string, string>)[chat.chat_type || ""] || "会话"
}

export function conversationName(chat: ConversationIdentityInfo) {
  const name = chat.chat_name?.trim()
  if (name && name !== chat.chat_id) return name
  return `未命名${conversationKind(chat)}`
}

export function findDuplicateConversationNames(chats: ConversationIdentityInfo[]) {
  const seen = new Set<string>()
  const repeated = new Set<string>()
  for (const c of chats) {
    const key = c.platform + "|" + conversationName(c)
    if (seen.has(key)) repeated.add(key)
    seen.add(key)
  }
  return repeated
}
