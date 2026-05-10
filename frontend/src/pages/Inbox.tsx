import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { CheckCheck, RefreshCw, Inbox as InboxIcon, Loader2, ExternalLink } from "lucide-react"
import { formatTime } from "@/lib/utils"
import { PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/constants"

interface TriggerRow {
  id: number
  keyword: string
  message_id: number
  matched_at: string
  read: number
  platform: string
  chat_id: string
  chat_name: string
  sender_name: string
  content: string
  timestamp: string
}

export default function Inbox() {
  const [items, setItems] = useState<TriggerRow[]>([])
  const [loading, setLoading] = useState(true)
  const [onlyUnread, setOnlyUnread] = useState(true)
  const [scanning, setScanning] = useState(false)
  const navigate = useNavigate()

  const refresh = async () => {
    setLoading(true)
    try {
      const data = await fetchAPI<TriggerRow[]>(`/inbox?only_unread=${onlyUnread}&limit=200`)
      setItems(data)
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void refresh() }, [onlyUnread])

  const scan = async () => {
    setScanning(true)
    const id = toast.loading("扫描中...")
    try {
      const r = await fetchAPI<{ new_triggers: number }>("/inbox/scan", { method: "POST" })
      toast.success(`新增 ${r.new_triggers} 条`, { id })
      await refresh()
    } catch (e) {
      toast.error(getErrorMessage(e), { id })
    } finally {
      setScanning(false)
    }
  }

  const markRead = async (triggerId: number) => {
    setItems(prev => prev.map(i => i.id === triggerId ? { ...i, read: 1 } : i))
    try {
      await fetchAPI(`/inbox/${triggerId}/read`, { method: "POST" })
    } catch { /* optimistic — no rollback for missed read flag */ }
  }

  const markAllRead = async () => {
    try {
      await fetchAPI("/inbox/read_all", { method: "POST" })
      setItems(prev => prev.map(i => ({ ...i, read: 1 })))
      toast.success("已全部标为已读")
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  const goto = (item: TriggerRow) => {
    void markRead(item.id)
    navigate(`/chats?chat=${encodeURIComponent(item.chat_name)}`)
  }

  return (
    <div className="p-6 md:p-8 max-w-4xl mx-auto">
      <div className="flex items-center justify-between gap-3 mb-4">
        <div>
          <h1 className="text-lg font-semibold flex items-center gap-2">
            <InboxIcon className="h-5 w-5 text-[var(--color-info)]" />
            收件箱
          </h1>
          <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">关键词命中的消息会出现在这里</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setOnlyUnread(!onlyUnread)}
            className={`text-xs px-2.5 py-1 rounded-md border transition-colors ${onlyUnread ? "bg-[var(--color-primary)]/15 border-[var(--color-primary)]/30 text-[var(--color-primary)]" : "border-[var(--color-border)] text-[var(--color-muted-foreground)]"}`}
          >
            {onlyUnread ? "只看未读" : "全部"}
          </button>
          <Button variant="outline" size="sm" onClick={scan} disabled={scanning}>
            {scanning ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />}
            扫描
          </Button>
          <Button variant="outline" size="sm" onClick={markAllRead}>
            <CheckCheck className="mr-1.5 h-3.5 w-3.5" /> 全标已读
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-md border border-dashed border-[var(--color-border)] p-12 text-center text-sm text-[var(--color-muted-foreground)]">
          {onlyUnread ? "没有未读命中。点'扫描'重新扫一遍，或到 Settings → 关键词 添加规则。" : "还没有任何命中记录。"}
        </div>
      ) : (
        <div className="space-y-2">
          {items.map(t => (
            <div
              key={t.id}
              className={`rounded-lg border p-3 transition-colors ${t.read ? "border-[var(--color-border)] opacity-70" : "border-[var(--color-info)]/40 bg-[var(--color-info)]/5"}`}
            >
              <div className="flex items-center gap-2 mb-1 text-xs">
                <Badge variant="outline" style={{ borderColor: "var(--color-info)", color: "var(--color-info)" }}>
                  {t.keyword}
                </Badge>
                <span style={{ color: PLATFORM_COLOR[t.platform] || "var(--color-muted-foreground)" }}>
                  {PLATFORM_LABEL[t.platform] || t.platform}
                </span>
                <span className="text-[var(--color-muted-foreground)] truncate">{t.chat_name || t.chat_id}</span>
                <span className="ml-auto text-[var(--color-muted-foreground)] tabular-nums shrink-0">
                  {formatTime(t.matched_at)}
                </span>
              </div>
              <p className="text-sm leading-relaxed">
                <span className="text-[var(--color-muted-foreground)] font-medium">{t.sender_name}: </span>
                {t.content || <span className="italic text-[var(--color-muted-foreground)]">(消息已被删除)</span>}
              </p>
              <div className="flex gap-2 mt-2">
                <Button variant="outline" size="sm" onClick={() => goto(t)}>
                  <ExternalLink className="mr-1.5 h-3 w-3" /> 跳转聊天
                </Button>
                {!t.read && (
                  <Button variant="ghost" size="sm" onClick={() => markRead(t.id)}>
                    标已读
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
