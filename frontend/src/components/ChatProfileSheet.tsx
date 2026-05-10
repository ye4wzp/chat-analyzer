import { useEffect, useState } from "react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage } from "@/lib/api"
import { Sheet, SheetHeader } from "@/components/ui/sheet"
import { Button } from "@/components/ui/button"
import { Loader2, Sparkles, RefreshCw, Users, Clock, BookOpen } from "lucide-react"
import { formatTime } from "@/lib/utils"

export interface ChatProfileData {
  platform: string
  chat_id: string
  chat_name: string
  chat_type: string
  msg_count: number
  distinct_senders: number
  earliest: string | null
  latest: string | null
  hours: number[]
  top_senders: { name: string; count: number }[]
  urgency_dist: { urgency: number; count: number }[]
  knowledge: { id: number; title: string; content: string; tags: string; created_at: string }[]
  summary: string | null
  summary_generated_at: string | null
}

const URGENCY_COLOR: Record<number, string> = {
  1: "var(--color-success)",
  2: "var(--color-success)",
  3: "var(--color-info)",
  4: "var(--color-warning)",
  5: "var(--color-destructive)",
}

interface Props {
  open: boolean
  platform: string
  chatId: string
  onClose: () => void
}

export function ChatProfileSheet({ open, platform, chatId, onClose }: Props) {
  const [data, setData] = useState<ChatProfileData | null>(null)
  const [loading, setLoading] = useState(false)
  const [summarizing, setSummarizing] = useState(false)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setData(null)
    setLoading(true)
    fetchAPI<ChatProfileData>(`/chats/${platform}/${encodeURIComponent(chatId)}/profile`)
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => { if (!cancelled) toast.error(getErrorMessage(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [open, platform, chatId])

  const summarize = async () => {
    setSummarizing(true)
    const id = toast.loading("AI 总结中...")
    try {
      const res = await fetchAPI<{ summary: string }>(
        `/chats/${platform}/${encodeURIComponent(chatId)}/profile/summarize`,
        { method: "POST" }
      )
      toast.success("总结完成", { id })
      setData(prev => prev ? { ...prev, summary: res.summary, summary_generated_at: new Date().toISOString() } : prev)
    } catch (e) {
      toast.error(getErrorMessage(e), { id })
    } finally {
      setSummarizing(false)
    }
  }

  const hourPeak = data ? Math.max(...data.hours, 1) : 1

  return (
    <Sheet open={open} onClose={onClose} side="right">
      <SheetHeader title={data?.chat_name || "聊天画像"} onClose={onClose} />
      <div className="overflow-y-auto p-5 space-y-5">
        {loading || !data ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
          </div>
        ) : (
          <>
            <section>
              <div className="grid grid-cols-3 gap-3 text-center">
                <div className="rounded-md border border-[var(--color-border)] p-2">
                  <div className="text-xs text-[var(--color-muted-foreground)]">消息</div>
                  <div className="text-base font-semibold tabular-nums">{data.msg_count.toLocaleString()}</div>
                </div>
                <div className="rounded-md border border-[var(--color-border)] p-2">
                  <div className="text-xs text-[var(--color-muted-foreground)]">参与人</div>
                  <div className="text-base font-semibold tabular-nums">{data.distinct_senders}</div>
                </div>
                <div className="rounded-md border border-[var(--color-border)] p-2">
                  <div className="text-xs text-[var(--color-muted-foreground)]">知识点</div>
                  <div className="text-base font-semibold tabular-nums">{data.knowledge.length}</div>
                </div>
              </div>
              {data.earliest && (
                <p className="text-xs text-[var(--color-muted-foreground)] mt-2 text-center">
                  {formatTime(data.earliest).slice(0, 10)} ~ {formatTime(data.latest || "").slice(0, 10)}
                </p>
              )}
            </section>

            {/* AI summary */}
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium flex items-center gap-1.5">
                  <Sparkles className="h-3.5 w-3.5 text-[var(--color-info)]" /> AI 总结
                </h3>
                <Button variant="outline" size="sm" onClick={summarize} disabled={summarizing}>
                  {summarizing ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />}
                  {data.summary ? "重新生成" : "生成"}
                </Button>
              </div>
              {data.summary ? (
                <div className="rounded-md bg-[var(--color-secondary)]/50 p-3 text-sm leading-relaxed whitespace-pre-wrap">
                  {data.summary}
                  {data.summary_generated_at && (
                    <p className="text-xs text-[var(--color-muted-foreground)] mt-2">
                      生成于 {formatTime(data.summary_generated_at)}
                    </p>
                  )}
                </div>
              ) : (
                <p className="text-xs text-[var(--color-muted-foreground)]">尚未生成。点击右上角"生成"调用 LLM 生成 1 段简介。</p>
              )}
            </section>

            {/* Hourly activity */}
            <section className="space-y-2">
              <h3 className="text-sm font-medium flex items-center gap-1.5">
                <Clock className="h-3.5 w-3.5 text-[var(--color-info)]" /> 24 小时活跃
              </h3>
              <div className="flex items-end gap-0.5 h-16">
                {data.hours.map((n, h) => (
                  <div key={h} className="flex-1 flex flex-col items-center" title={`${h.toString().padStart(2, "0")}:00 — ${n} 条`}>
                    <div
                      className="w-full bg-[var(--color-info)]/40 rounded-sm transition-colors hover:bg-[var(--color-info)]"
                      style={{ height: `${(n / hourPeak) * 100}%`, minHeight: n ? 2 : 0 }}
                    />
                  </div>
                ))}
              </div>
              <div className="flex justify-between text-[10px] text-[var(--color-muted-foreground)] tabular-nums">
                <span>0</span><span>6</span><span>12</span><span>18</span><span>23</span>
              </div>
            </section>

            {/* Top senders */}
            <section className="space-y-2">
              <h3 className="text-sm font-medium flex items-center gap-1.5">
                <Users className="h-3.5 w-3.5 text-[var(--color-info)]" /> 活跃发言人
              </h3>
              <div className="space-y-1">
                {data.top_senders.map(s => {
                  const pct = data.msg_count ? (s.count / data.msg_count) * 100 : 0
                  return (
                    <div key={s.name} className="text-xs">
                      <div className="flex justify-between mb-0.5">
                        <span className="truncate flex-1">{s.name}</span>
                        <span className="tabular-nums text-[var(--color-muted-foreground)]">{s.count.toLocaleString()} ({pct.toFixed(1)}%)</span>
                      </div>
                      <div className="h-1 bg-[var(--color-secondary)] rounded">
                        <div className="h-full bg-[var(--color-info)]/60 rounded" style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>

            {/* Urgency mix */}
            {data.urgency_dist.length > 0 && (
              <section className="space-y-2">
                <h3 className="text-sm font-medium">紧急度分布</h3>
                <div className="flex h-2 rounded overflow-hidden">
                  {data.urgency_dist.map(u => {
                    const total = data.urgency_dist.reduce((s, x) => s + x.count, 0)
                    const pct = total ? (u.count / total) * 100 : 0
                    return (
                      <div
                        key={u.urgency}
                        className="h-full"
                        style={{ width: `${pct}%`, background: URGENCY_COLOR[u.urgency] || "var(--color-muted)" }}
                        title={`紧急度 ${u.urgency}: ${u.count}`}
                      />
                    )
                  })}
                </div>
                <div className="flex gap-3 text-xs flex-wrap">
                  {data.urgency_dist.map(u => (
                    <span key={u.urgency} className="text-[var(--color-muted-foreground)]">
                      <span className="inline-block w-2 h-2 rounded-full mr-1 align-middle" style={{ background: URGENCY_COLOR[u.urgency] }} />
                      U{u.urgency}: {u.count}
                    </span>
                  ))}
                </div>
              </section>
            )}

            {/* Knowledge from this chat */}
            {data.knowledge.length > 0 && (
              <section className="space-y-2">
                <h3 className="text-sm font-medium flex items-center gap-1.5">
                  <BookOpen className="h-3.5 w-3.5 text-[var(--color-info)]" /> 该聊天贡献的知识点
                </h3>
                <div className="space-y-1.5">
                  {data.knowledge.slice(0, 10).map(k => (
                    <div key={k.id} className="rounded-md border border-[var(--color-border)] p-2">
                      <div className="text-sm font-medium truncate">{k.title}</div>
                      <div className="text-xs text-[var(--color-muted-foreground)] truncate">{k.content}</div>
                    </div>
                  ))}
                  {data.knowledge.length > 10 && (
                    <p className="text-xs text-[var(--color-muted-foreground)]">还有 {data.knowledge.length - 10} 条…</p>
                  )}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </Sheet>
  )
}
