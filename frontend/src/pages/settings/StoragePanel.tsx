import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"
import { HardDrive, Loader2 } from "lucide-react"
import { fetchAPI, getErrorMessage, type Config } from "@/lib/api"
import { useGlobalEvents } from "@/lib/GlobalEventsContext"
import { PLATFORM_LABEL, chatTypeLabel } from "@/lib/constants"
import { Button } from "@/components/ui/button"

interface StorageReport {
  db_bytes: number
  free_bytes: number
  raw_text: { platform: string; messages: number; bytes: number }[]
  by_chat_type: { chat_type: string | null; chats: number; messages: number }[]
  tables: { name: string; bytes: number }[]
  keep_raw_text: boolean
  running: boolean
}

const fmtBytes = (n: number) => n >= 1 << 30 ? `${(n / (1 << 30)).toFixed(2)} GB` : n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(0)} MB` : `${(n / 1024).toFixed(0)} KB`

/**
 * What takes space and the explicit, irreversible ways to reclaim it. Every
 * action confirms first and runs as a task; nothing here is automatic.
 */
export function StoragePanel({ onConfigChange }: { onConfigChange: (c: Config) => void }) {
  const { tasks } = useGlobalEvents()
  const [report, setReport] = useState<StorageReport | null>(null)
  const task = tasks.find(t => t.type === "maintenance" && (t.status === "running" || t.status === "pending"))
  const doneKey = tasks.filter(t => t.type === "maintenance" && t.status === "done").map(t => t.id).join(",")

  const load = useCallback(() => { fetchAPI<StorageReport>("/maintenance/storage").then(setReport).catch(e => toast.error(getErrorMessage(e))) }, [])
  useEffect(() => { load() }, [load, doneKey])

  const start = async (path: string, confirmText: string, body?: unknown) => {
    if (!confirm(confirmText)) return
    try {
      await fetchAPI(path, { method: "POST", body: body ? JSON.stringify(body) : undefined })
      toast.success("已开始，进度见底部任务栏")
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  const rawTotal = report?.raw_text.reduce((s, r) => s + r.bytes, 0) ?? 0
  const rawCount = report?.raw_text.reduce((s, r) => s + r.messages, 0) ?? 0
  const channels = report?.by_chat_type.find(t => t.chat_type === "channel")
  const busy = !!task || !!report?.running

  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold"><HardDrive className="h-4 w-4" />存储</h2>
        {report && <span className="text-xs tabular-nums text-[var(--color-muted-foreground)]">数据库 {fmtBytes(report.db_bytes)}{report.free_bytes > 50 << 20 && <> · 可回收 {fmtBytes(report.free_bytes)}</>}</span>}
      </div>
      {task && (
        <div className="mt-3 rounded-lg bg-[var(--color-secondary)] p-3 text-xs">
          <div className="flex items-center gap-2"><Loader2 className="h-3.5 w-3.5 animate-spin" />{task.message}</div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--color-card)]"><div className="h-full bg-[var(--color-primary)]" style={{ width: `${task.progress}%` }} /></div>
        </div>
      )}
      {!report ? (
        <p className="mt-3 flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在统计各表占用（几 GB 的库需要几秒）...</p>
      ) : (
        <div className="mt-4 space-y-4 text-sm">
          <div className="grid gap-2 md:grid-cols-2">
            {report.tables.slice(0, 6).map(t => (
              <div key={t.name} className="flex items-center justify-between rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs">
                <span className="font-mono">{t.name}</span><span className="tabular-nums text-[var(--color-muted-foreground)]">{fmtBytes(t.bytes)}</span>
              </div>
            ))}
          </div>

          <div className="rounded-lg border border-[var(--color-border)] p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="font-medium">文本消息的原始 JSON</p>
                <p className="text-xs text-[var(--color-muted-foreground)]">
                  微信 / Telegram 的纯文本消息，raw_data 与正文重复。当前 {rawCount.toLocaleString()} 条，约 {fmtBytes(rawTotal)}。
                  {report.raw_text.map(r => ` ${PLATFORM_LABEL[r.platform] || r.platform} ${fmtBytes(r.bytes)}`).join(" ·")}
                </p>
              </div>
              <Button size="sm" variant="outline" disabled={busy || rawCount === 0} onClick={() => start("/maintenance/purge-raw-text", `清理 ${rawCount.toLocaleString()} 条文本消息的原始 JSON（约 ${fmtBytes(rawTotal)}）？\n\n这是不可逆操作：正文、发送人、时间都已在结构化字段里，只有“原始 JSON 备用”这一价值会丢失。`)}>清理</Button>
            </div>
            <label className="mt-3 flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
              <input type="checkbox" checked={report.keep_raw_text} onChange={async e => {
                try { onConfigChange(await fetchAPI<Config>("/config", { method: "PUT", body: JSON.stringify({ keep_raw_text: e.target.checked }) })); load() } catch (err) { toast.error(getErrorMessage(err)) }
              }} className="accent-[var(--color-primary)]" />
              今后新同步的文本消息也保留原始 JSON（默认不保留）
            </label>
          </div>

          {channels && channels.messages > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--color-border)] p-3">
              <div>
                <p className="font-medium">已入库的 Telegram 频道</p>
                <p className="text-xs text-[var(--color-muted-foreground)]">{channels.chats} 个{chatTypeLabel("channel")} · {channels.messages.toLocaleString()} 条消息。删除后同时加入“不同步”名单，不会再拉回来。</p>
              </div>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => start("/maintenance/purge-chats", `删除 ${channels.chats} 个频道的 ${channels.messages.toLocaleString()} 条消息？\n\n不可逆。相关统计会自动重建；如需保留请先在「备份」页做一份备份。`, { chat_type: "channel", exclude_from_sync: true })}>删除频道消息</Button>
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--color-border)] p-3">
            <div>
              <p className="font-medium">压缩数据库（VACUUM）</p>
              <p className="text-xs text-[var(--color-muted-foreground)]">删除和清理释放的空间要经过 VACUUM 才会还给磁盘。几 GB 的库需要几分钟，期间同步会等待。</p>
            </div>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => start("/maintenance/vacuum", "现在压缩数据库？期间写入会被阻塞几分钟。")}>压缩</Button>
          </div>
        </div>
      )}
    </section>
  )
}
