import { useEffect, useState } from "react"
import { Activity, ChevronDown, X, Loader2, CheckCircle2, AlertCircle } from "lucide-react"
import { fetchAPI, type TaskState } from "@/lib/api"
import { cn } from "@/lib/utils"

const POLL_MS = 1500
const TASK_LABEL: Record<string, string> = {
  sync_wechat: "微信同步",
  sync_qq: "QQ 同步",
  sync_telegram: "Telegram 同步",
  import_qq: "QQ 导入",
  import_telegram: "Telegram 导入",
  analyze: "AI 分析",
}

function StatusIcon({ status }: { status: string }) {
  if (status === "running") return <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-info)]" />
  if (status === "done") return <CheckCircle2 className="h-3.5 w-3.5 text-[var(--color-success)]" />
  if (status === "error") return <AlertCircle className="h-3.5 w-3.5 text-[var(--color-destructive)]" />
  return <Activity className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
}

export function GlobalTaskBar() {
  const [tasks, setTasks] = useState<TaskState[]>([])
  const [expanded, setExpanded] = useState(false)
  const [hidden, setHidden] = useState<Set<string>>(new Set())

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const data = await fetchAPI<TaskState[]>("/tasks")
        if (alive) setTasks(data)
      } catch { /* ignore */ }
    }
    void tick()
    const id = window.setInterval(tick, POLL_MS)
    return () => { alive = false; window.clearInterval(id) }
  }, [])

  const visible = tasks.filter(t => !hidden.has(t.id))
  const running = visible.filter(t => t.status === "running")
  const recent = visible.filter(t => t.status !== "running").slice(-3).reverse()

  if (visible.length === 0) return null

  const dismiss = (id: string) => {
    setHidden(prev => new Set(prev).add(id))
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
            <div key={t.id} className="flex items-center gap-2 text-xs">
              <StatusIcon status={t.status} />
              <span className="font-medium">{TASK_LABEL[t.type] || t.type}</span>
              <span className="flex-1 truncate text-[var(--color-muted-foreground)]">{t.message}</span>
              {t.status === "running" && <span className="tabular-nums text-[var(--color-muted-foreground)]">{t.progress}%</span>}
              {t.status !== "running" && (
                <button onClick={() => dismiss(t.id)} aria-label="清除" className="rounded p-0.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]">
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
