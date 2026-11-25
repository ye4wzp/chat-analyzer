import { useEffect, useState } from "react"
import { Activity, ChevronDown, X, Loader2, CheckCircle2, AlertCircle, Square } from "lucide-react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage } from "@/lib/api"
import { useGlobalEvents } from "@/lib/GlobalEventsContext"
import { cn } from "@/lib/utils"
import { useTaskRunner } from "@/hooks/useTaskRunner"

const TASK_LABEL: Record<string, string> = {
  contact_dossier: "联系人全量分析",
  sync_wechat: "微信同步",
  sync_qq: "QQ 同步",
  sync_telegram: "Telegram 同步",
  import_qq: "QQ 导入",
  import_telegram: "Telegram 导入",
  analyze: "AI 分析",
  qq_install: "QQ 启动器安装",
  embed: "Embedding 索引",
  tag: "批量打标签",
  sync_all: "全平台同步",
}

function syncErrorLabel(error?: string | null) {
  if (error === "RuntimeError" || error === "HTTPStatusError") {
    return `旧记录只保存了 ${error}，重试后可查看具体原因`
  }
  if (error === "CacheIdentityConflict") return "缓存和已入库消息的 ID 相同、时间不同，已停止合并该会话"
  if (error === "TargetUnavailableOrExcluded") return "会话未出现在当前同步范围内"
  if (error === "CacheUnavailable") return "本机历史缓存不可用"
  return error || "历史尚未读完"
}

function StatusIcon({ status }: { status: string }) {
  if (status === "running") return <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-info)]" />
  if (status === "done") return <CheckCircle2 className="h-3.5 w-3.5 text-[var(--color-success)]" />
  if (status === "error") return <AlertCircle className="h-3.5 w-3.5 text-[var(--color-destructive)]" />
  if (status === "partial" || status === "interrupted") return <AlertCircle className="h-3.5 w-3.5 text-amber-500" />
  if (status === "cancelled") return <Square className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
  return <Activity className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
}

export function GlobalTaskBar() {
  const { run } = useTaskRunner()
  const { tasks } = useGlobalEvents()
  const [expanded, setExpanded] = useState(false)
  const [hidden, setHidden] = useState<Set<string>>(new Set())

  // Prune dismissed ids once their tasks fall out of the backend list.
  useEffect(() => {
    setHidden(prev => {
      const alive = new Set(tasks.map(t => t.id))
      const next = new Set([...prev].filter(id => alive.has(id)))
      return next.size === prev.size ? prev : next
    })
  }, [tasks])

  const visible = tasks.filter(t => !hidden.has(t.id))
  const running = visible.filter(t => t.status === "running")
  const recent = visible.filter(t => t.status !== "running").slice(-3).reverse()

  if (visible.length === 0) return null

  const dismiss = (id: string) => {
    setHidden(prev => new Set(prev).add(id))
  }

  const cancel = async (id: string) => {
    try {
      await fetchAPI(`/tasks/${id}/cancel`, { method: "POST" })
      toast.success("已发送取消请求")
    } catch (e: unknown) {
      toast.error(getErrorMessage(e))
    }
  }

  return (
    <div className="relative shrink-0 border-b border-[var(--color-border)] bg-[var(--color-card)]/60 backdrop-blur">
      {/* indeterminate sweep when anything is running */}
      {running.length > 0 && (
        <div className="absolute inset-x-0 top-0 h-[2px] overflow-hidden">
          <div className="absolute h-full w-1/3 bg-gradient-to-r from-transparent via-[var(--color-primary)] to-transparent ca-progress-sweep" />
        </div>
      )}
      <button
        onClick={() => setExpanded(v => !v)}
        className="flex w-full items-center gap-3 px-4 py-2 text-left text-xs hover:bg-[var(--color-accent)] transition-colors"
      >
        <Activity className={cn("h-3.5 w-3.5", running.length > 0 ? "text-[var(--color-info)]" : "text-[var(--color-muted-foreground)]")} />
        <span className="text-[var(--color-muted-foreground)]">
          {running.length > 0
            ? `${running.length} 个任务进行中: ${running.map(t => TASK_LABEL[t.type] || t.type).join(", ")}`
            : `最近: ${TASK_LABEL[recent[0]?.type ?? ""] ?? recent[0]?.type ?? ""}`}
        </span>
        {running[0]?.message && <span className="ml-auto truncate text-[var(--color-muted-foreground)]/70 max-w-md">{running[0].message}</span>}
        <ChevronDown className={cn("ml-2 h-3.5 w-3.5 transition-transform", expanded && "rotate-180")} />
      </button>
      {expanded && (
        <div className="border-t border-[var(--color-border)] px-4 py-2 space-y-1">
          {visible.length === 0 && <p className="text-xs text-[var(--color-muted-foreground)]">无任务</p>}
          {visible.slice().reverse().map(t => (
            <div key={t.id} className="flex flex-wrap items-center gap-2 text-xs">
              <StatusIcon status={t.status} />
              <span className="font-medium">{TASK_LABEL[t.type] || t.type}</span>
              <span className="flex-1 truncate text-[var(--color-muted-foreground)]">{t.message}</span>
              {t.journal_error && <span className="text-amber-500">任务记录保存失败</span>}
              {t.request && ["partial", "interrupted", "cancelled", "error"].includes(t.status) && (
                <button className="underline" onClick={() => void run({
                  taskType: t.type, loadingText: "继续同步...",
                  start: () => fetchAPI<{ task_id: string }>(`/tasks/${t.id}/retry`, { method: "POST" }),
                })}>继续未完成会话</button>
              )}
              {t.status === "running" && (
                <>
                  <span className="tabular-nums text-[var(--color-muted-foreground)]">{t.progress}%</span>
                  <button
                    onClick={(e) => { e.stopPropagation(); void cancel(t.id) }}
                    aria-label="取消"
                    title="取消任务"
                    className="rounded p-0.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-destructive)]/10 hover:text-[var(--color-destructive)]"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </>
              )}
              {t.status !== "running" && (
                <button onClick={() => dismiss(t.id)} aria-label="清除" className="rounded p-0.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]">
                  <X className="h-3 w-3" />
                </button>
              )}
              {t.results && Object.values(t.results).some(r => r.details.some(d => d.status !== "done")) && (
                <details className="basis-full pl-5 text-[var(--color-muted-foreground)]">
                  <summary className="cursor-pointer">失败与待补明细</summary>
                  <div className="max-h-48 overflow-auto py-2 space-y-1">
                    {Object.entries(t.results).flatMap(([platform, r]) => r.details.filter(d => d.status !== "done").map(d => (
                      <p key={`${platform}:${d.chat_id}`}>{platform} · {d.name} · {d.status === "pending" ? "待补" : "失败"} · {syncErrorLabel(d.error)}</p>
                    )))}
                  </div>
                </details>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
