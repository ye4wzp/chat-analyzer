import { useCallback, useEffect, useState } from "react"
import { Database, Loader2, RefreshCw } from "lucide-react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, type StatsStatus } from "@/lib/api"
import { useGlobalEvents } from "@/lib/GlobalEventsContext"
import { PLATFORM_LABEL } from "@/lib/constants"
import { formatRelative } from "@/lib/utils"
import { Select } from "@/components/ui/select"
import type { useStatsFilters } from "@/hooks/useStatsFilters"

type Filters = ReturnType<typeof useStatsFilters>

/** Platform + account selects shared by stats pages. */
export function StatsFilterBar({ filters, showAccount = true, children }: {
  filters: Filters
  showAccount?: boolean
  children?: React.ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select value={filters.platform} onChange={e => filters.setPlatform(e.target.value)} className="h-9 w-36 rounded-lg bg-[var(--color-card)] text-sm">
        <option value="">全部平台</option>
        {Object.entries(PLATFORM_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </Select>
      {showAccount && filters.hasMultiAccount && (
        <Select value={filters.account} onChange={e => filters.setAccount(e.target.value)} className="h-9 w-44 rounded-lg bg-[var(--color-card)] text-sm">
          <option value="">全部账号</option>
          {filters.platformAccounts.map(a => (
            <option key={`${a.platform}-${a.account}`} value={a.account}>
              {PLATFORM_LABEL[a.platform] || a.platform} · {a.account}
            </option>
          ))}
        </Select>
      )}
      {children}
    </div>
  )
}

/**
 * Shows rollup freshness and lets the user refresh / rebuild. Pages call
 * `onRefreshed` when a stats task finishes so they can reload their data.
 */
export function StatsStatusBadge({ onRefreshed }: { onRefreshed?: () => void }) {
  const { tasks } = useGlobalEvents()
  const [status, setStatus] = useState<StatsStatus | null>(null)

  const load = useCallback(() => {
    fetchAPI<StatsStatus>("/stats/status").then(setStatus).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  const doneKey = tasks.filter(t => t.type === "stats" && t.status === "done").map(t => t.id).join(",")
  useEffect(() => {
    if (!doneKey) return
    load()
    onRefreshed?.()
  }, [doneKey, load, onRefreshed])

  const running = status?.running || tasks.some(t => t.type === "stats" && (t.status === "running" || t.status === "pending"))
  const runningTask = tasks.find(t => t.type === "stats" && t.status === "running")

  const refresh = async (full: boolean) => {
    try {
      await fetchAPI(`/stats/refresh?full=${full}`, { method: "POST" })
      toast.success(full ? "已开始全量重建统计" : "已开始更新统计")
      load()
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  return (
    <div className="flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
      <Database className="h-3.5 w-3.5" />
      {running ? (
        <span className="flex items-center gap-1.5">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          {runningTask?.message || "统计更新中..."}
        </span>
      ) : status?.stale ? (
        <span className="text-[var(--color-warning)]" title="消息被删除或从备份恢复后，统计需要全量重建；点右侧刷新即可">统计与消息不一致，需重建</span>
      ) : status?.ready ? (
        <span>
          统计更新于 {status.built_at ? formatRelative(status.built_at) : "未知"}
          {status.behind > 0 && <span className="ml-1 text-[var(--color-warning)]">· 落后 {status.behind.toLocaleString()} 条</span>}
        </span>
      ) : (
        <span className="text-[var(--color-warning)]">统计尚未构建</span>
      )}
      <button
        onClick={() => void refresh(false)}
        disabled={running}
        title="增量更新统计"
        className="rounded-md border border-[var(--color-border)] px-2 py-1 hover:bg-[var(--color-accent)] disabled:opacity-50"
      >
        <RefreshCw className="h-3 w-3" />
      </button>
      <button
        onClick={() => { if (confirm("全量重建会清空并重新统计所有消息，可能需要一两分钟。继续？")) void refresh(true) }}
        disabled={running}
        className="rounded-md border border-[var(--color-border)] px-2 py-1 hover:bg-[var(--color-accent)] disabled:opacity-50"
      >
        重建
      </button>
    </div>
  )
}

export function StatsNotReady() {
  const { tasks } = useGlobalEvents()
  const task = tasks.find(t => t.type === "stats" && (t.status === "running" || t.status === "pending"))
  const [status, setStatus] = useState<StatsStatus | null>(null)
  useEffect(() => { fetchAPI<StatsStatus>("/stats/status").then(setStatus).catch(() => {}) }, [])
  const idle = !task && status && !status.running

  const start = async () => {
    try { await fetchAPI("/stats/refresh", { method: "POST" }) } catch (e) { toast.error(getErrorMessage(e)) }
  }

  return (
    <div className="rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-card)] p-10 text-center">
      {idle ? (
        <>
          <Database className="mx-auto h-6 w-6 text-[var(--color-muted-foreground)]" />
          <p className="mt-3 text-sm font-medium">{status?.ready ? "这个范围内没有数据" : "统计尚未构建"}</p>
          {!status?.ready && (
            <button onClick={() => void start()} className="mt-3 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-[var(--color-primary-foreground)]">
              开始构建
            </button>
          )}
        </>
      ) : (
        <>
          <Loader2 className="mx-auto h-6 w-6 animate-spin text-[var(--color-muted-foreground)]" />
          <p className="mt-3 text-sm font-medium">{task?.message || "正在构建统计数据"}</p>
          <div className="mx-auto mt-3 h-2 w-64 overflow-hidden rounded-full bg-[var(--color-secondary)]">
            <div className="h-full rounded-full bg-[var(--color-primary)] transition-[width]" style={{ width: `${Math.max(2, task?.progress ?? 2)}%` }} />
          </div>
          <p className="mt-2 text-xs text-[var(--color-muted-foreground)]">
            {task ? `${task.progress}% · ` : ""}首次需要遍历全部消息，几十秒到一两分钟；之后每次同步只做增量，页面会自动刷新。
          </p>
        </>
      )}
    </div>
  )
}
