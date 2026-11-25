import { useEffect, useState } from "react"
import { fetchAPI, getErrorMessage, type ChatInfo } from "@/lib/api"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"

import { conversationKind } from "@/lib/conversation"

export interface ChatScope { platform: string; chat_id: string; since: string; until: string }

export function ChatScopeControls({ value, onChange, disabled = false }: {
  value: ChatScope; onChange: (value: ChatScope) => void; disabled?: boolean
}) {
  const [chats, setChats] = useState<ChatInfo[]>([])
  const [error, setError] = useState("")
  useEffect(() => {
    let active = true
    fetchAPI<ChatInfo[]>("/chats").then(data => { if (active) setChats(data) })
      .catch(e => { if (active) setError(getErrorMessage(e)) })
    return () => { active = false }
  }, [])
  return <>
    <Select aria-label="平台范围" disabled={disabled} value={value.platform}
      onChange={e => onChange({ ...value, platform: e.target.value, chat_id: "" })} className="h-8 w-32 text-sm">
      <option value="">全部平台</option><option value="wechat">微信</option><option value="qq">QQ</option><option value="telegram">Telegram</option>
    </Select>
    <Select aria-label="会话范围" disabled={disabled || !value.platform} value={value.chat_id}
      onChange={e => onChange({ ...value, chat_id: e.target.value })} className="h-8 w-52 text-sm">
      <option value="">{value.platform ? "全部会话" : "先选择平台"}</option>
      {chats.filter(c => c.platform === value.platform).map(c =>
        <option key={c.chat_id} value={c.chat_id}>{c.chat_name || c.chat_id} · {conversationKind(c)}{c.chat_name && c.chat_name !== c.chat_id ? " · " + c.chat_id : ""}</option>)}
    </Select>
    <Input aria-label="开始日期" disabled={disabled} type="date" value={value.since}
      onChange={e => onChange({ ...value, since: e.target.value })} className="h-8 w-36 text-sm" />
    <Input aria-label="结束日期" disabled={disabled} type="date" value={value.until}
      onChange={e => onChange({ ...value, until: e.target.value })} className="h-8 w-36 text-sm" />
    {error && <p className="w-full text-xs text-destructive">会话列表读取失败：{error}</p>}
  </>
}
