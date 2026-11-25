import { CircleUserRound, Hash, Megaphone, MessagesSquare, UsersRound } from "lucide-react"
import { PLATFORM_LABEL } from "@/lib/constants"
import { conversationKind, conversationName, type ConversationIdentityInfo } from "@/lib/conversation"

export function ConversationMark({ chat }: { chat: ConversationIdentityInfo }) {
  const kind = conversationKind(chat)
  const Icon = kind === "群聊" ? UsersRound : kind === "私聊" ? CircleUserRound : kind === "公众号" ? Megaphone : kind === "频道" ? Hash : MessagesSquare
  return <span aria-hidden="true" className="conversation-mark" data-platform={chat.platform}><Icon size={20} strokeWidth={1.8} /></span>
}

export function ConversationMeta({ chat }: { chat: ConversationIdentityInfo }) {
  return <span className="conversation-meta">
    <span>{PLATFORM_LABEL[chat.platform] || chat.platform}</span><span aria-hidden="true">·</span><span>{conversationKind(chat)}</span>
  </span>
}

export function ConversationSource({ chat }: { chat: ConversationIdentityInfo }) {
  const name = conversationName(chat)
  return <span className="conversation-source"><strong>{name}</strong><ConversationMeta chat={chat} />
    {(!chat.chat_name?.trim() || chat.chat_name.trim() === chat.chat_id) && <span className="conversation-id">{chat.chat_id}</span>}
  </span>
}
