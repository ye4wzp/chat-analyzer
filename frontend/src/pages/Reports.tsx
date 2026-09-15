import { useCallback, useEffect, useState } from "react"
import { FileText, Loader2, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, type Report, type ReportMeta } from "@/lib/api"
import { formatTime } from "@/lib/utils"
import { useTaskRunner } from "@/hooks/useTaskRunner"
import { Button } from "@/components/ui/button"
import { SubNav } from "@/components/SubNav"
import { REPORT_SUBNAV } from "@/lib/subnav"

const PERIOD_LABEL: Record<string, string> = { weekly: "周报", monthly: "月报", yearly: "年报" }

export default function Reports() {
  const [reports, setReports] = useState<ReportMeta[]>([])
  const [selected, setSelected] = useState<Report | null>(null)
  const [loaded, setLoaded] = useState(false)
  const runner = useTaskRunner()
  const generating = runner.running.has("report")

  const refresh = useCallback(async (selectNewest = false) => {
    try {
      const list = await fetchAPI<ReportMeta[]>("/reports")
      setReports(list)
      if (selectNewest && list.length) {
        setSelected(await fetchAPI<Report>(`/reports/${list[0].id}`))
      }
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const generate = (period: "weekly" | "monthly") => {
    void runner.run({
      start: () => fetchAPI<{ task_id: string }>("/reports/generate", {
        method: "POST",
        body: JSON.stringify({ period }),
      }),
      taskType: "report",
      loadingText: `正在生成${PERIOD_LABEL[period]}...`,
      onDone: async () => { await refresh(true) },
    })
  }

  const select = async (id: number) => {
    try {
      setSelected(await fetchAPI<Report>(`/reports/${id}`))
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  const remove = async (id: number) => {
    if (!confirm("删除这份报告？")) return
    try {
      await fetchAPI(`/reports/${id}`, { method: "DELETE" })
      setReports(prev => prev.filter(r => r.id !== id))
      setSelected(prev => (prev?.id === id ? null : prev))
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  return (
    <div className="p-6 md:p-8">
      <header className="mb-6 flex flex-wrap items-center gap-3">
        <div className="flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight">报告</h1>
            <SubNav items={REPORT_SUBNAV} />
          </div>
          <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
            周期性回顾：消息动态、新增知识点、待办与 VIP 联系人；可在 设置 → 定时任务 开启自动生成。年度数据与 AI 年终总结在「年度回顾」。
          </p>
        </div>
        <Button size="sm" variant="outline" disabled={generating} onClick={() => generate("weekly")}>
          {generating && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}生成周报
        </Button>
        <Button size="sm" variant="outline" disabled={generating} onClick={() => generate("monthly")}>
          {generating && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}生成月报
        </Button>
      </header>

      {loaded && reports.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-[var(--color-border)] py-16 text-center">
          <FileText className="h-8 w-8 text-[var(--color-muted-foreground)]" />
          <p className="text-sm text-[var(--color-muted-foreground)]">还没有报告，点右上角生成第一份周报</p>
        </div>
      ) : (
        <div className="grid gap-6 md:grid-cols-[280px_1fr]">
          <div className="space-y-2">
            {reports.map(r => (
              <div
                key={r.id}
                onClick={() => { void select(r.id) }}
                className={`group cursor-pointer rounded-lg border p-3 transition-colors ${selected?.id === r.id ? "border-[var(--color-primary)] bg-[var(--color-accent)]" : "border-[var(--color-border)] bg-[var(--color-card)] hover:bg-[var(--color-accent)]"}`}
              >
                <div className="flex items-center gap-2">
                  <span className="rounded-full bg-[var(--color-primary-subtle)] px-2 py-0.5 text-[10px] font-medium text-[var(--color-primary)]">
                    {PERIOD_LABEL[r.period] ?? r.period}
                  </span>
                  <span className="font-mono text-xs text-[var(--color-muted-foreground)]">
                    {r.period_start.slice(0, 10)} ~ {r.period_end.slice(0, 10)}
                  </span>
                  <button
                    onClick={e => { e.stopPropagation(); void remove(r.id) }}
                    aria-label="删除报告"
                    className="ml-auto rounded p-1 text-[var(--color-muted-foreground)] opacity-0 transition-opacity hover:text-[var(--color-destructive)] group-hover:opacity-100"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
                <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">生成于 {formatTime(r.created_at)}</p>
              </div>
            ))}
          </div>

          <div className="min-w-0">
            {selected ? (
              <article className="whitespace-pre-wrap rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6 text-sm leading-relaxed">
                {selected.content}
              </article>
            ) : (
              <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-[var(--color-border)] text-sm text-[var(--color-muted-foreground)]">
                选择左侧的报告查看内容
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
