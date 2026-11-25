import { useState } from "react"
import { fetchAPI, getErrorMessage, type Message } from "@/lib/api"
import { formatTime } from "@/lib/utils"
import { Button } from "@/components/ui/button"

import { ConversationSource } from "@/components/ConversationIdentity"

export function SourceMessages({ sourceIds }: { sourceIds: string }) {
  const [rows, setRows] = useState<Message[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [expanded, setExpanded] = useState(false)
  let ids: number[] = []
  try {
    const parsed: unknown = JSON.parse(sourceIds || "[]")
    if (Array.isArray(parsed)) ids = [...new Set(parsed.map(Number).filter(n => Number.isSafeInteger(n) && n > 0))]
  } catch { /* Legacy records can lack valid provenance. */ }
  if (!ids.length) return <p className="mt-2 text-xs text-muted-foreground">暂无可定位的原始消息</p>
  const show = async () => {
    if (rows) { setExpanded(!expanded); return }
    setLoading(true); setError("")
    try {
      const result = await fetchAPI<Message[]>(`/messages?ids=${ids.slice(0, 100).join(",")}&limit=100`)
      setRows(result.reverse()); setExpanded(true)
    } catch (e) { setError(getErrorMessage(e)) }
    finally { setLoading(false) }
  }
  return <div className="mt-2 cursor-auto" onClick={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}>
    <Button variant="outline" size="sm" disabled={loading} onClick={() => { void show() }}>
      {loading ? "读取原文..." : expanded ? "收起原文" : `查看原文（${ids.length} 条）`}
    </Button>
    {error && <p className="text-xs text-destructive">{error}</p>}
    {expanded && rows && <div className="mt-2 space-y-2">
      {ids.length > 100 && <p className="text-xs text-muted-foreground">仅展示前 100 个来源 ID 对应的消息。</p>}
      {rows.length < Math.min(ids.length, 100) && <p className="text-xs text-muted-foreground">部分来源消息已不存在，无法核实其原文。</p>}
      {rows.map(m => <div key={m.id} className="rounded border border-border p-2 text-xs">
        <ConversationSource chat={m} /><p className="text-muted-foreground">{m.sender_name || m.sender_id || "未知发送者"} · {formatTime(m.timestamp)} · #{m.id}</p>
        <p className="mt-1 whitespace-pre-wrap break-words text-sm">{m.content}</p>
      </div>)}
    </div>}
  </div>
}
