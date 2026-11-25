import { useEffect, useState } from "react"
import { toast } from "sonner"
import { CloudOff, X } from "lucide-react"
import { chatRefKey, fetchAPI, getErrorMessage, type ChatInfo, type ChatRef, type Config } from "@/lib/api"
import { PLATFORM_LABEL } from "@/lib/constants"

/**
 * What gets ingested at all. Filtering at the door keeps the database, FTS
 * index, backups and every rollup proportional to what is actually analysed.
 */
export function SyncScopeCard({ config, onConfigChange }: { config: Config; onConfigChange: (c: Config) => void }) {
  const scope = config.sync_scope ?? { exclude_channels: true, exclude_chats: [] }
  const [chats, setChats] = useState<ChatInfo[]>([])
  useEffect(() => { fetchAPI<ChatInfo[]>("/chats").then(setChats).catch(() => setChats([])) }, [])
  const nameOf = (r: ChatRef) => chats.find(c => chatRefKey(c) === chatRefKey(r))?.chat_name || r.chat_id

  const update = async (body: Record<string, unknown>, ok: string) => {
    try {
      onConfigChange(await fetchAPI<Config>("/config", { method: "PUT", body: JSON.stringify(body) }))
      toast.success(ok)
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><CloudOff className="h-4 w-4" />同步范围</h2>
      <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
        加入同步排除名单后，从下次在线同步起跳过该会话。历史消息保留。
      </p>
      <label className="mt-4 flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={scope.exclude_channels}
          onChange={e => void update({ sync_exclude_channels: e.target.checked }, e.target.checked ? "已停止同步 Telegram 频道" : "已恢复同步 Telegram 频道")}
          className="mt-1 accent-[var(--color-primary)]"
        />
        <span>
          不同步 Telegram 频道
          <span className="block text-xs text-[var(--color-muted-foreground)]">适用于 Telegram 广播频道。</span>
        </span>
      </label>
      <div className="mt-4">
        <p className="mb-1.5 text-xs font-medium text-[var(--color-muted-foreground)]">不同步的聊天（{scope.exclude_chats.length}）</p>
        {scope.exclude_chats.length === 0 ? (
          <p className="text-xs text-[var(--color-muted-foreground)]">暂无。在「聊天浏览」里选择会话，点「停止同步」即可加入。</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {scope.exclude_chats.map(r => (
              <li key={chatRefKey(r)} className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border)] px-2 py-0.5 text-xs">
                <span className="text-[var(--color-muted-foreground)]">{PLATFORM_LABEL[r.platform] || r.platform}</span>
                <span className="break-all">{nameOf(r)}</span>
                <button onClick={() => void update({ remove_sync_exclude: r }, "已恢复同步")} className="rounded-full hover:text-[var(--color-destructive)]" aria-label="恢复同步"><X className="h-3 w-3" /></button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
