import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, PieChart, Pie, Cell, Legend, BarChart, Bar } from "recharts"
import { fetchAPI, type DashboardStats, type TokenUsage } from "@/lib/api"
import { MessageSquare, Users, BookOpen, TrendingUp, ArrowRight, Cpu } from "lucide-react"
import { PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/constants"

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
  // Fill last 30 days even when no data, so the chart x-axis stays continuous
  const end = new Date()
  const out: DailyRow[] = []
  for (let i = 29; i >= 0; i--) {
    const d = new Date(end)
    d.setDate(d.getDate() - i)
    const key = d.toISOString().slice(0, 10)
    const row = map.get(key) || { date: key }
    for (const p of platforms) if (row[p] === undefined) row[p] = 0
    out.push(row)
  }
  return { rows: out, platforms: Array.from(platforms) }
}

function shortDate(d: string) {
  return `${d.slice(5, 7)}/${d.slice(8, 10)}`
}

function fmtTokens(n: number): string {
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B"
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M"
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k"
  return String(n)
}

function TokenUsageCard() {
  const [usage, setUsage] = useState<TokenUsage | null>(null)
  useEffect(() => {
    fetchAPI<TokenUsage>("/llm/usage").then(setUsage).catch(() => { /* missing endpoint or empty DB shouldn't crash dashboard */ })
  }, [])

  if (!usage) return null
  const pct = Math.min(100, usage.today.pct)
  const overBudget = usage.today.total > usage.budget
  const barColor = overBudget ? "var(--color-destructive)" : pct > 80 ? "var(--color-warning)" : "var(--color-success)"

  return (
    <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4">
      <div className="flex items-start justify-between mb-3">
        <h2 className="text-sm font-semibold flex items-center gap-1.5">
          <Cpu className="h-4 w-4 text-[var(--color-info)]" />
          Token 用量
        </h2>
        <span className="text-xs text-[var(--color-muted-foreground)] tabular-nums">
          {usage.today.calls} 次调用
        </span>
      </div>
      <div className="space-y-1.5">
        <div className="flex items-baseline justify-between text-xs">
          <span className="text-[var(--color-muted-foreground)]">今日</span>
          <span className="tabular-nums">
            <span className="font-semibold text-sm" style={{ color: barColor }}>{fmtTokens(usage.today.total)}</span>
            <span className="text-[var(--color-muted-foreground)]"> / {fmtTokens(usage.budget)} ({pct.toFixed(1)}%)</span>
          </span>
        </div>
        <div className="h-1.5 rounded-full bg-[var(--color-secondary)] overflow-hidden">
          <div className="h-full transition-all" style={{ width: `${pct}%`, background: barColor }} />
        </div>
      </div>
      {usage.today_by_purpose.length > 0 && (
        <div className="mt-3 flex gap-3 text-xs flex-wrap">
          {usage.today_by_purpose.map(p => (
            <span key={p.purpose} className="text-[var(--color-muted-foreground)]">
              <span className="font-medium text-[var(--color-foreground)]">{p.purpose}</span>: {fmtTokens(p.tokens)} ({p.calls})
            </span>
          ))}
        </div>
      )}
      {usage.last_7_days.length > 1 && (
        <div className="h-16 mt-3">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={usage.last_7_days} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
              <Bar dataKey="tokens" fill="var(--color-info)" radius={[2, 2, 0, 0]} />
              <Tooltip
                cursor={{ fill: "var(--color-accent)" }}
                contentStyle={{ background: "var(--color-card-elevated)", border: "1px solid var(--color-border)", fontSize: 12 }}
                formatter={(v) => [fmtTokens(Number(v) || 0), "tokens"]}
                labelFormatter={(d) => String(d)}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  )
}

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ name?: string; value?: number; color?: string }>; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-card-elevated)] px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 text-[var(--color-muted-foreground)]">{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full" style={{ background: p.color }} />
          <span className="text-[var(--color-foreground)]">{PLATFORM_LABEL[p.name as string] ?? p.name}</span>
          <span className="ml-auto font-medium tabular-nums">{p.value}</span>
        </div>
      ))}
    </div>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    fetchAPI<DashboardStats>("/dashboard").then(setStats).catch(e => setError(e.message))
  }, [])

  const chart = useMemo(() => pivotDaily(stats?.daily_counts ?? []), [stats?.daily_counts])

  if (error) return <div className="p-6 md:p-8"><p className="text-sm text-[var(--color-destructive)]">{error}</p></div>
  if (!stats) return (
    <div className="p-6 md:p-8 space-y-4">
      {[...Array(3)].map((_, i) => (
        <div key={i} className="h-24 rounded-xl animate-pulse bg-[var(--color-card)]" />
      ))}
    </div>
  )

  const kpis = [
    { icon: MessageSquare, label: "消息总数", value: stats.total_messages, link: "/chats", accent: "var(--color-primary)" },
    { icon: Users, label: "聊天数", value: stats.total_chats, link: "/chats", accent: "var(--color-info)" },
    { icon: BookOpen, label: "知识点", value: stats.total_knowledge, link: "/knowledge", accent: "var(--color-warning)" },
    { icon: TrendingUp, label: "近 7 天", value: stats.recent_active ?? 0, link: "/timeline", accent: "var(--color-platform-telegram)" },
  ]

  return (
    <div className="p-6 md:p-8 max-w-6xl mx-auto space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">概览</h1>
        <p className="text-sm text-[var(--color-muted-foreground)] mt-1">你的多平台聊天分析仪表盘</p>
      </header>

      {/* KPI grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {kpis.map(({ icon: Icon, label, value, link, accent }) => (
          <button
            key={label}
            onClick={() => navigate(link)}
            className="group relative overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 text-left transition-all hover:border-[var(--color-primary)]/50 hover:bg-[var(--color-card-elevated)]"
          >
            <div className="flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
              <Icon className="h-3.5 w-3.5" style={{ color: accent }} />
              <span>{label}</span>
            </div>
            <p className="mt-2 text-2xl font-semibold tabular-nums">{value.toLocaleString()}</p>
            <div
              className="absolute -bottom-8 -right-8 h-24 w-24 rounded-full opacity-10 transition-opacity group-hover:opacity-20"
              style={{ background: `radial-gradient(circle, ${accent} 0%, transparent 70%)` }}
            />
          </button>
        ))}
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <section className="lg:col-span-2 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">最近 30 天消息趋势</h2>
            <span className="text-xs text-[var(--color-muted-foreground)]">按平台</span>
          </div>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chart.rows} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }} stroke="var(--color-border)" />
                <YAxis tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }} stroke="var(--color-border)" allowDecimals={false} />
                <Tooltip content={<ChartTooltip />} />
                {chart.platforms.map(p => (
                  <Line
                    key={p}
                    type="monotone"
                    dataKey={p}
                    stroke={PLATFORM_COLOR[p] || "#888"}
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4 }}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>

        <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4">
          <h2 className="mb-3 text-sm font-semibold">平台分布</h2>
          {stats.platforms.length === 0 ? (
            <p className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">暂无数据</p>
          ) : (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={stats.platforms}
                    dataKey="count"
                    nameKey="platform"
                    innerRadius={45}
                    outerRadius={75}
                    paddingAngle={3}
                    onClick={(d) => {
                      const platform = (d as { platform?: string }).platform
                      if (platform) navigate(`/chats?platform=${platform}`)
                    }}
                  >
                    {stats.platforms.map(p => (
                      <Cell key={p.platform} fill={PLATFORM_COLOR[p.platform] || "#888"} stroke="var(--color-card)" />
                    ))}
                  </Pie>
                  <Tooltip content={<ChartTooltip />} />
                  <Legend
                    iconType="circle"
                    formatter={(v: string) => PLATFORM_LABEL[v] ?? v}
                    wrapperStyle={{ fontSize: 11, color: "var(--color-muted-foreground)" }}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>
      </div>

      <TokenUsageCard />

      {/* Recent knowledge */}
      {stats.recent_knowledge.length > 0 && (
        <section>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">最近知识点</h2>
            <button
              onClick={() => navigate("/knowledge")}
              className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] transition-colors flex items-center gap-1"
            >
              查看全部 <ArrowRight className="h-3 w-3" />
            </button>
          </div>
          <div className="space-y-2">
            {stats.recent_knowledge.slice(0, 5).map(k => {
              const tags = (() => { try { return JSON.parse(k.tags || "[]") } catch { return [] } })()
              return (
                <button
                  key={k.id}
                  onClick={() => navigate("/knowledge")}
                  className="block w-full text-left rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-3 transition-colors hover:bg-[var(--color-card-elevated)]"
                >
                  <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                    <span className="text-sm font-medium">{k.title}</span>
                    {tags.slice(0, 3).map((t: string) => (
                      <span key={t} className="text-xs px-1.5 py-0.5 rounded bg-[var(--color-secondary)] text-[var(--color-muted-foreground)]">{t}</span>
                    ))}
                    <span className="ml-auto text-xs text-[var(--color-muted-foreground)] tabular-nums">{new Date(k.created_at).toLocaleDateString()}</span>
                  </div>
                  <p className="text-sm text-[var(--color-muted-foreground)] line-clamp-2">{k.content}</p>
                </button>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}
