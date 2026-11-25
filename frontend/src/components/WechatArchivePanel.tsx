import { useEffect, useState } from "react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, updateWechatArchive, type ChatInfo } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Sheet, SheetHeader } from "@/components/ui/sheet"

type Account = {
  chat_id: string; enabled: number; scanning: number
  saved: number; pending: number; failed: number
  errors: { reason: string; count: number }[]
}
type Status = { accounts: Account[]; cooldown_until: number | null }

export function WechatArchivePanel({ chats, onClose }: { chats: ChatInfo[]; onClose: () => void }) {
  const [data, setData] = useState<Status | null>(null)
  const [error, setError] = useState("")
  const [version, setVersion] = useState(0)
  const [busy, setBusy] = useState("")
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function refresh() {
      try {
        const value = await fetchAPI<Status>("/wechat-archive")
        if (active) { setData(value); setError("") }
      } catch (e) { if (active) setError(getErrorMessage(e)) }
      if (active) timer = setTimeout(refresh, 4000)
    }
    void refresh()
    return () => { active = false; clearTimeout(timer) }
  }, [version])

  async function change(a: Account, action: "enable" | "pause" | "retry") {
    setBusy(a.chat_id)
    try {
      await updateWechatArchive([a.chat_id], action)
      setVersion(v => v + 1)
    } catch (e) { toast.error(getErrorMessage(e)) }
    finally { setBusy("") }
  }

  const names = new Map(chats.filter(c => c.platform === "wechat").map(c => [c.chat_id, c.chat_name]))
  return <Sheet open side="right" onClose={onClose} className="w-[30rem] max-w-[96vw]">
    <SheetHeader title="公众号全文任务" onClose={onClose} />
    <div className="space-y-3 overflow-y-auto p-4">
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {!error && !data && <p>正在读取任务…</p>}
      {data?.cooldown_until && <p className="text-sm text-warning">微信要求验证或限制了读取频率，队列将在 {new Date(data.cooldown_until * 1000).toLocaleTimeString()} 后继续。</p>}
      {data?.accounts.length === 0 && <p className="text-sm text-muted-foreground">在会话列表中多选公众号，点击「保存历史并自动保存新文章」即可开始。</p>}
      {data?.accounts.map(a => <section key={a.chat_id} className="space-y-2 rounded-lg border border-border bg-card p-3">
        <h3 className="break-words font-semibold">{names.get(a.chat_id) || a.chat_id}</h3>
        <p className="text-sm">{!a.enabled ? "已暂停" : a.scanning ? "正在扫描推送" : "已开启自动保存"}</p>
        <p className="text-sm tabular-nums text-muted-foreground">已保存 {a.saved} 篇 · 待保存 {a.pending} 篇 · 失败 {a.failed} 篇</p>
        {a.errors.map(e => <p key={e.reason} className="text-xs text-warning">{e.reason}（{e.count} 篇）</p>)}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={!!busy} onClick={() => void change(a, a.enabled ? "pause" : "enable")}>{a.enabled ? "暂停保存" : "继续保存"}</Button>
          {!!a.enabled && a.failed > 0 && <Button size="sm" variant="outline" disabled={!!busy} onClick={() => void change(a, "retry")}>重试失败文章</Button>}
        </div>
      </section>)}
    </div>
  </Sheet>
}
