import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { Activity, ArrowRight, FileText, MessageSquare, RefreshCw, Sparkles, Users } from "lucide-react"
import { fetchAPI, type ChatInfo, type DashboardStats, type KnowledgeItem, type TokenUsage } from "@/lib/api"
import { PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/constants"
import { formatTime } from "@/lib/utils"

interface DailyRow { date: string; [platform: string]: string | number }

function pivotDaily(rows: { date: string; platform: string; count: number }[]) {
  const map = new Map<string, DailyRow>()
  const platforms = new Set<string>()
  for (const r of rows) {
    if (!map.has(r.date)) map.set(r.date, { date: r.date })
    const row = map.get(r.date)!
    row[r.platform] = (Number(row[r.platform] ?? 0)) + r.count
    platforms.add(r.platform)
  }
  const end = new Date()
  const out: DailyRow[] = []
  for (let i = 13; i >= 0; i--) {
    const d = new Date(end)
    d.setDate(d.getDate() - i)
    const key = d.toISOString().slice(0, 10)
    const row = map.get(key) || { date: key }
    for (const p of platforms) if (row[p] === undefined) row[p] = 0
    out.push(row)
  }
  return { rows: out, platforms: Array.from(platforms) }
}

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 10_000) return `${(n / 10_000).toFixed(1)}万`
  return n.toLocaleString()
}

function parseTags(item: KnowledgeItem): string[] {
  try {
    const parsed = JSON.parse(item.tags || "[]")
    return Array.isArray(parsed) ? parsed.map(String) : []
  } catch {
    return []
  }
}

function MiniSpark({ color, values }: { color: string; values: number[] }) {
  const points = sparkPoints(values, 4, 40, 104, 30)
  return (
    <svg viewBox="0 0 112 48" className="h-12 w-28" aria-hidden="true">
      <polyline points={points} fill="none" style={{ stroke: color }} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function sparkPoints(values: number[], xPad: number, yBase: number, xSpan: number, ySpan: number) {
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(1, max - min)
  return values.map((value, index) => {
    const x = xPad + index * (xSpan / Math.max(1, values.length - 1))
    const y = yBase - ((value - min) / range) * ySpan
    return `${x},${y}`
  }).join(" ")
}

function TrendChart({ values }: { values: number[] }) {
  const safeValues = values.length ? values : [0, 0]
  const points = sparkPoints(safeValues, 12, 150, 496, 120)
  const fillPoints = `12,168 ${points} 508,168`
  return (
    <svg viewBox="0 0 520 180" className="h-48 w-full" aria-hidden="true">
      <defs>
        <linearGradient id="trendFill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" style={{ stopColor: "var(--chart-1)" }} stopOpacity="0.18" />
          <stop offset="100%" style={{ stopColor: "var(--chart-1)" }} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {[36, 72, 108, 144].map(y => <line key={y} x1="12" x2="508" y1={y} y2={y} style={{ stroke: "var(--chart-grid)" }} strokeDasharray="4 5" />)}
      <polygon points={fillPoints} fill="url(#trendFill)" />
      <polyline points={points} fill="none" style={{ stroke: "var(--chart-1)" }} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function platformGradient(platforms: DashboardStats["platforms"], total: number) {
  if (!total || !platforms.length) return "var(--chart-grid)"
  let cursor = 0
  const segments = platforms.map(p => {
    const start = cursor
    const end = cursor + (p.count / total) * 100
    cursor = end
    const color = PLATFORM_COLOR[p.platform] || "#94a3b8"
    return `${color} ${start}% ${end}%`
  })
  return `conic-gradient(${segments.join(", ")})`
}

function UsageBars({ rows, fallback }: { rows: { day: string; tokens: number }[]; fallback: number }) {
  const data = rows.length ? rows : [{ day: "today", tokens: fallback }]
  const max = Math.max(1, ...data.map(d => d.tokens))
  return (
    <div className="flex h-16 items-end gap-1.5">
      {data.map(row => (
        <div key={row.day} className="flex flex-1 flex-col justify-end">
          <div className="w-full rounded-t bg-[var(--color-primary)]/80" style={{ height: `${Math.max(6, row.tokens / max * 56)}px` }} />
        </div>
      ))}
    </div>
  )
}

function KpiCard({ icon: Icon, label, value, delta, color, values, onClick }: {
  icon: typeof MessageSquare
  label: string
  value: string
  delta?: string | null
  color: string
  values?: number[]
  onClick: () => void
}) {
  const deltaUp = delta ? !delta.startsWith("-") : true
  return (
    <button
      onClick={onClick}
      className="group min-h-[118px] rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-[var(--color-border-strong)] hover:shadow-md"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl text-white shadow-sm" style={{ background: color }}>
          <Icon className="h-5 w-5" />
        </div>
        {values && values.length > 0 && <MiniSpark color={color} values={values} />}
      </div>
      <div className="mt-2">
        <p className="text-xs font-medium text-[var(--color-muted-foreground)]">{label}</p>
        <p className="mt-0.5 text-2xl font-semibold tracking-tight tabular-nums">{value}</p>
        {delta && (
          <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
            较上周 <span className={`font-medium tabular-nums ${deltaUp ? "text-[var(--color-success)]" : "text-[var(--color-destructive)]"}`}>{delta}</span>
          </p>
        )}
      </div>
    </button>
  )
}

function TokenUsageCard() {
  const [usage, setUsage] = useState<TokenUsage | null>(null)
  useEffect(() => {
    fetchAPI<TokenUsage>("/llm/usage").then(setUsage).catch(() => { /* budget widget is optional */ })
  }, [])

  if (!usage) return null
  const pct = Math.min(100, usage.today.pct)
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">AI 用量</h2>
        <span className="text-xs text-[var(--color-muted-foreground)]">{usage.today.calls} 次调用</span>
      </div>
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-2xl font-semibold tabular-nums">{fmt(usage.today.total)}</p>
          <p className="text-xs text-[var(--color-muted-foreground)]">今日 tokens</p>
        </div>
        <div className="flex-1">
          <UsageBars rows={usage.last_7_days} fallback={usage.today.total} />
        </div>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-[var(--color-secondary)]">
        <div className="h-full rounded-full bg-[var(--color-primary)]" style={{ width: `${pct}%` }} />
      </div>
    </section>
  )
}

function PlatformCard({ stats, total }: { stats: DashboardStats; total: number }) {
  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold">平台分布</h2>
        <ArrowRight className="h-4 w-4 text-[var(--color-muted-foreground)]" />
      </div>
      <div className="grid items-center gap-5 md:grid-cols-[180px_1fr]">
        <div className="relative h-44">
          <div
            className="absolute left-1/2 top-1/2 h-36 w-36 -translate-x-1/2 -translate-y-1/2 rounded-full shadow-inner"
            style={{ background: platformGradient(stats.platforms, total) }}
          />
          <div className="absolute left-1/2 top-1/2 h-24 w-24 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--color-card)] shadow-[inset_0_0_0_1px_var(--color-border)]" />
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-lg font-semibold tabular-nums">{fmt(total)}</span>
            <span className="text-xs text-[var(--color-muted-foreground)]">总消息数</span>
          </div>
        </div>
        <div className="space-y-3">
          {stats.platforms.map(p => {
            const ratio = total ? Math.round(p.count / total * 1000) / 10 : 0
            return (
              <div key={p.platform} className="grid grid-cols-[90px_1fr_54px] items-center gap-3 text-sm">
                <span className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: PLATFORM_COLOR[p.platform] || "#94a3b8" }} />
                  {PLATFORM_LABEL[p.platform] || p.platform}
                </span>
                <div className="h-2 overflow-hidden rounded-full bg-[var(--color-secondary)]">
                  <div className="h-full rounded-full" style={{ width: `${ratio}%`, background: PLATFORM_COLOR[p.platform] || "#94a3b8" }} />
                </div>
                <span className="text-right text-xs text-[var(--color-muted-foreground)] tabular-nums">{ratio}%</span>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [chats, setChats] = useState<ChatInfo[]>([])
  const [error, setError] = useState("")

  useEffect(() => {
    fetchAPI<DashboardStats>("/dashboard").then(setStats).catch(e => setError(e.message))
    fetchAPI<ChatInfo[]>("/chats").then(setChats).catch(() => setChats([]))
  }, [])

  const chart = useMemo(() => pivotDaily(stats?.daily_counts ?? []), [stats?.daily_counts])

  if (error) return <div className="p-6 md:p-8"><p className="text-sm text-[var(--color-destructive)]">{error}</p></div>
  if (!stats) return (
    <div className="p-6 md:p-8">
      <div className="grid gap-4 md:grid-cols-4">
        {[...Array(4)].map((_, i) => <div key={i} className="h-28 animate-pulse rounded-xl bg-[var(--color-card)]" />)}
      </div>
    </div>
  )

  // Real daily message series (from the 14-day pivot) drives the sparklines and
  // a genuine week-over-week delta — no fabricated numbers.
  const dailyTotals = chart.rows.map(r => chart.platforms.reduce((s, p) => s + Number(r[p] ?? 0), 0))
  const spark = dailyTotals.slice(-8)
  const last7 = dailyTotals.slice(-7).reduce((a, b) => a + b, 0)
  const prev7 = dailyTotals.slice(-14, -7).reduce((a, b) => a + b, 0)
  const wow = prev7 ? Math.round((last7 - prev7) / prev7 * 1000) / 10 : null
  const wowLabel = wow == null ? null : `${wow >= 0 ? "+" : ""}${wow}%`
  const hasSpark = spark.some(v => v > 0)

  const kpis = [
    { icon: MessageSquare, label: "消息总量", value: fmt(stats.total_messages), delta: wowLabel, color: "var(--chart-1)", values: hasSpark ? spark : undefined },
    { icon: Users, label: "活跃群聊", value: fmt(stats.total_chats), color: "var(--color-success)" },
    { icon: Activity, label: "近 7 天消息", value: fmt(stats.recent_active ?? last7), color: "var(--color-warning)", values: hasSpark ? spark : undefined },
    { icon: FileText, label: "AI 知识点", value: fmt(stats.total_knowledge), color: "var(--chart-6)" },
  ]

  const topChats = chats.slice(0, 5)
  const totalMessages = stats.total_messages || stats.platforms.reduce((sum, p) => sum + p.count, 0)

  return (
    <div className="mx-auto w-full max-w-[1320px] space-y-5 p-5 md:p-7">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">首页仪表盘</h1>
          <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">多平台消息、提醒与知识沉淀的今日概览</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
          更新于 {new Date().toLocaleString()}
          <RefreshCw className="h-3.5 w-3.5" />
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-4">
        {kpis.map(k => (
          <KpiCard key={k.label} {...k} onClick={() => navigate(k.label === "AI 知识点" ? "/knowledge" : "/chats")} />
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.25fr_1fr_0.92fr]">
        <PlatformCard stats={stats} total={totalMessages} />

        <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold">最近活跃群聊</h2>
            <button onClick={() => navigate("/chats")} className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-primary)]">
              查看全部
            </button>
          </div>
          <div className="space-y-3">
            {topChats.map((chat, idx) => {
              const pct = topChats[0]?.msg_count ? Math.max(12, chat.msg_count / topChats[0].msg_count * 100) : 0
              return (
                <button
                  key={`${chat.platform}-${chat.chat_id}`}
                  onClick={() => navigate(`/chats?platform=${chat.platform}&chat_id=${encodeURIComponent(chat.chat_id)}`)}
                  className="grid w-full grid-cols-[1fr_62px] items-center gap-3 text-left"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--color-secondary)] text-xs font-semibold">{idx + 1}</span>
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{chat.chat_name || chat.chat_id}</p>
                        <p className="text-xs text-[var(--color-muted-foreground)]">{PLATFORM_LABEL[chat.platform] || chat.platform}</p>
                      </div>
                    </div>
                    <div className="ml-9 mt-2 h-2 overflow-hidden rounded-full bg-[var(--color-secondary)]">
                      <div className="h-full rounded-full" style={{ width: `${pct}%`, background: PLATFORM_COLOR[chat.platform] || "var(--color-primary)" }} />
                    </div>
                  </div>
                  <span className="text-right text-xs text-[var(--color-muted-foreground)] tabular-nums">{fmt(chat.msg_count)}</span>
                </button>
              )
            })}
          </div>
        </section>

        <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold">知识沉淀 · 本周摘要</h2>
            <button onClick={() => navigate("/knowledge")} className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-primary)]">
              查看全部
            </button>
          </div>
          <div className="space-y-3">
            {stats.recent_knowledge.slice(0, 3).map((item, idx) => (
              <button
                key={item.id}
                onClick={() => navigate("/knowledge")}
                className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-card-elevated)] p-3 text-left transition-colors hover:bg-[var(--color-accent)]"
              >
                <div className="flex gap-3">
                  <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${idx === 0 ? "bg-[var(--color-warning-subtle)] text-[var(--color-warning)]" : idx === 1 ? "bg-[var(--color-primary-subtle)] text-[var(--color-primary)]" : "bg-[var(--color-success-subtle)] text-[var(--color-success)]"}`}>
                    {idx === 0 ? <Sparkles className="h-4 w-4" /> : idx === 1 ? <FileText className="h-4 w-4" /> : <Activity className="h-4 w-4" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{item.title}</p>
                    <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-[var(--color-muted-foreground)]">{item.content}</p>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {parseTags(item).slice(0, 3).map(tag => (
                        <span key={tag} className="rounded-full bg-[var(--color-surface-3)] px-2 py-0.5 text-[10px] text-[var(--color-muted-foreground)]">{tag}</span>
                      ))}
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </section>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold">最近活动</h2>
            <button onClick={() => navigate("/knowledge")} className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-primary)]">
              查看全部
            </button>
          </div>
          <div className="space-y-1">
            {stats.recent_knowledge.slice(0, 5).map((item) => (
              <div key={item.id} className="grid grid-cols-[34px_1fr_auto] items-center gap-3 rounded-lg px-2 py-2 text-sm hover:bg-[var(--color-card-elevated)]">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--color-primary-subtle)] text-[var(--color-primary)]">
                  <FileText className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="truncate font-medium">{item.title}</p>
                  <p className="truncate text-xs text-[var(--color-muted-foreground)]">{item.source_chat || "来自聊天"} · {item.content}</p>
                </div>
                <span className="text-right text-xs text-[var(--color-muted-foreground)] tabular-nums whitespace-nowrap">{formatTime(item.created_at)}</span>
              </div>
            ))}
          </div>
        </section>

        <div className="space-y-4">
          <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-semibold">近 14 天趋势</h2>
              <span className="text-xs text-[var(--color-muted-foreground)]">消息量</span>
            </div>
            <TrendChart values={chart.rows.map(row => chart.platforms.reduce((sum, platform) => sum + Number(row[platform] ?? 0), 0))} />
          </section>
          <TokenUsageCard />
        </div>
      </div>
    </div>
  )
}
