import { useCallback, useEffect, useState } from "react"
import { toast } from "sonner"
import { CheckSquare, ListChecks, Loader2, Square } from "lucide-react"
import { fetchAPI, getErrorMessage, type TodoItem, type TodoStats } from "@/lib/api"
import { formatTime } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/constants"

function urgencyTone(u: number) {
  if (u >= 4) return { dot: "bg-[var(--color-destructive)]", text: "text-[var(--color-destructive)]", label: "紧急" }
  if (u <= 2) return { dot: "bg-[var(--color-success)]", text: "text-[var(--color-success)]", label: "低" }
  return { dot: "bg-[var(--color-warning)]", text: "text-[var(--color-warning)]", label: "中" }
}

function StatCard({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
      <p className="text-sm text-[var(--color-muted-foreground)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums" style={{ color: tone }}>{value}</p>
    </div>
  )
}

export default function Todos() {
  const [todos, setTodos] = useState<TodoItem[]>([])
  const [stats, setStats] = useState<TodoStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [includeDone, setIncludeDone] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [t, s] = await Promise.all([
        fetchAPI<TodoItem[]>(`/todos?include_done=${includeDone}`),
        fetchAPI<TodoStats>("/todos/stats"),
      ])
      setTodos(t); setStats(s)
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [includeDone])

  useEffect(() => { void load() }, [load])

  const toggle = async (item: TodoItem) => {
    // optimistic
    setTodos(prev => prev.map(t => t.id === item.id ? { ...t, done: !t.done } : t))
    try {
      await fetchAPI(`/todos/${item.id}`, { method: "PATCH", body: JSON.stringify({ done: !item.done }) })
      const s = await fetchAPI<TodoStats>("/todos/stats")
      setStats(s)
      if (!includeDone && !item.done) {
        setTimeout(() => setTodos(prev => prev.filter(t => t.id !== item.id)), 250)
      }
    } catch (e) {
      setTodos(prev => prev.map(t => t.id === item.id ? { ...t, done: item.done } : t))
      toast.error(getErrorMessage(e))
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[var(--color-background)]">
      <header className="shrink-0 border-b border-[var(--color-border)] bg-[var(--color-background)]/80 px-5 py-5 backdrop-blur md:px-7">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <ListChecks className="h-6 w-6" />
            <h1 className="text-2xl font-semibold tracking-tight">待办看板</h1>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setIncludeDone(v => !v)}
          >
            {includeDone ? "隐藏已完成" : "显示已完成"}
          </Button>
        </div>
        {stats && (
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <StatCard label="待处理" value={stats.open} tone="#2563eb" />
            <StatCard label="紧急未办" value={stats.urgent} tone="#e11d48" />
            <StatCard label="累计待办" value={stats.total} tone="#64748b" />
          </div>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-5">
        {loading ? (
          <div className="flex h-full items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-[var(--color-muted-foreground)]" /></div>
        ) : todos.length === 0 ? (
          <div className="py-20 text-center text-sm text-[var(--color-muted-foreground)]">
            暂无待办。运行 AI 分析后，标记为「待办」的消息会自动汇集到这里。
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-2">
            {todos.map(item => {
              const tone = urgencyTone(item.urgency)
              return (
                <div
                  key={item.id}
                  className={`flex items-start gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm transition-opacity ${item.done ? "opacity-55" : ""}`}
                >
                  <button onClick={() => void toggle(item)} className="mt-0.5 shrink-0 text-[var(--color-muted-foreground)] hover:text-[var(--color-primary)]" aria-label={item.done ? "标记未完成" : "标记完成"}>
                    {item.done ? <CheckSquare className="h-5 w-5 text-[var(--color-primary)]" /> : <Square className="h-5 w-5" />}
                  </button>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`flex items-center gap-1 text-xs font-medium ${tone.text}`}>
                        <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />{tone.label}
                      </span>
                      <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium text-white" style={{ background: PLATFORM_COLOR[item.platform] || "#64748b" }}>
                        {PLATFORM_LABEL[item.platform] || item.platform}
                      </span>
                      <span className="text-xs text-[var(--color-muted-foreground)]">{item.chat_name}</span>
                      <span className="ml-auto text-xs text-[var(--color-muted-foreground)] tabular-nums">{formatTime(item.timestamp)}</span>
                    </div>
                    <p className={`mt-1.5 text-sm ${item.done ? "line-through" : "font-medium"}`}>{item.summary || item.content}</p>
                    {item.action_items.length > 0 && (
                      <ul className="mt-2 space-y-1">
                        {item.action_items.map((a, i) => (
                          <li key={i} className="flex items-start gap-1.5 text-xs text-[var(--color-muted-foreground)]">
                            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-[var(--color-muted-foreground)]/50" />{a}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
