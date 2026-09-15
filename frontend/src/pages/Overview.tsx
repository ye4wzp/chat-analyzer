import { useCallback, useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { CalendarDays, MessageSquare, Users, UsersRound } from "lucide-react"
import { fetchAPI, getErrorMessage, type StatsOverview } from "@/lib/api"
import { PLATFORM_COLOR, PLATFORM_LABEL, chatTypeLabel } from "@/lib/constants"
import { formatCount, formatDate } from "@/lib/utils"
import { useStatsFilters } from "@/hooks/useStatsFilters"
import { StatsFilterBar, StatsNotReady, StatsStatusBadge } from "@/components/StatsToolbar"
import { ChartCard, HourBarChart, KindDonut, RankList, TrendAreaChart, WeekdayBarChart } from "@/components/charts"
import { Select } from "@/components/ui/select"

function Kpi({ icon: Icon, label, value, sub, color }: { icon: typeof MessageSquare; label: string; value: string; sub?: string; color: string }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-[var(--color-muted-foreground)]">{label}</p>
        <span className="flex h-8 w-8 items-center justify-center rounded-lg" style={{ background: `color-mix(in oklch, ${color} 18%, transparent)`, color }}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <p className="mt-2 text-3xl font-semibold tracking-tight tabular-nums" style={{ color }}>{value}</p>
      {sub && <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">{sub}</p>}
    </div>
  )
}

export default function Overview() {
  const navigate = useNavigate()
  const filters = useStatsFilters()
  const [chatType, setChatType] = useState("")
  const [excludeChannel, setExcludeChannel] = useState(() => { try { return localStorage.getItem("ca-exclude-channel") === "1" } catch { return false } })
  const toggleExcludeChannel = (v: boolean) => { setExcludeChannel(v); try { localStorage.setItem("ca-exclude-channel", v ? "1" : "0") } catch { /* private mode */ } }
  const [data, setData] = useState<StatsOverview | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    const q = `?top=15${filters.query}${chatType ? `&chat_type=${chatType}` : ""}${excludeChannel && chatType !== "channel" ? "&exclude_channel=true" : ""}`
    fetchAPI<StatsOverview>(`/stats/overview${q}`)
      .then(d => { setData(d); setError("") })
      .catch(e => setError(getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [filters.query, chatType, excludeChannel])

  useEffect(() => { load() }, [load])

  const empty = !loading && data && data.totals.messages === 0
  const t = data?.totals

  return (
    <div className="mx-auto w-full max-w-[1320px] space-y-5 p-5 md:p-7">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">数据概览</h1>
          <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
            {t?.first_ts ? `${formatDate(t.first_ts)} ~ ${formatDate(t.last_ts)} · ` : ""}基于预聚合统计，随同步自动更新
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <StatsFilterBar filters={filters}>
            <Select value={chatType} onChange={e => setChatType(e.target.value)} className="h-9 w-28 rounded-lg bg-[var(--color-card)] text-sm">
              <option value="">全部类型</option>
              <option value="group">仅群聊</option>
              <option value="private">仅私聊</option>
              <option value="channel">仅频道</option>
            </Select>
            <label className="flex h-9 items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-2.5 text-xs text-[var(--color-muted-foreground)]" title="Telegram 频道是单向推送，不是聊天；排除后更能反映你真正参与的对话">
              <input type="checkbox" checked={excludeChannel} disabled={chatType === "channel"} onChange={e => toggleExcludeChannel(e.target.checked)} className="accent-[var(--color-primary)]" />
              排除频道
            </label>
          </StatsFilterBar>
          <StatsStatusBadge onRefreshed={load} />
        </div>
      </header>

      {error && <p className="text-sm text-[var(--color-destructive)]">{error}</p>}

      {empty ? (
        <StatsNotReady />
      ) : !data ? (
        <div className="grid gap-4 md:grid-cols-4">
          {[...Array(4)].map((_, i) => <div key={i} className="h-28 animate-pulse rounded-xl bg-[var(--color-card)]" />)}
        </div>
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Kpi icon={MessageSquare} label="总消息" value={t!.messages.toLocaleString()} sub={data.platforms.map(p => `${PLATFORM_LABEL[p.platform] || p.platform} ${formatCount(p.count)}`).join(" · ")} color="var(--color-success)" />
            <Kpi icon={Users} label="聊天数" value={t!.chats.toLocaleString()} sub={`私聊 ${t!.privates} · 群聊 ${t!.groups}`} color="var(--color-primary)" />
            <Kpi icon={UsersRound} label="群聊数" value={t!.groups.toLocaleString()} sub={`占聊天数 ${t!.chats ? Math.round(t!.groups / t!.chats * 100) : 0}%`} color="var(--chart-3)" />
            <Kpi icon={CalendarDays} label="活跃月数" value={t!.active_months.toLocaleString()} sub={data.monthly.length ? `${data.monthly[0].month} 起` : undefined} color="var(--color-warning)" />
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard title="消息量趋势（按月）" subtitle={`${data.monthly.length} 个月`}>
              <TrendAreaChart data={data.monthly} color="var(--color-success)" />
            </ChartCard>
            <ChartCard title="24 小时活跃分布" subtitle={`高峰 ${data.hourly.indexOf(Math.max(...data.hourly))}:00`}>
              <HourBarChart hours={data.hourly} />
            </ChartCard>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard title="消息类型分布" subtitle={`${data.kinds.length} 种类型`}>
              <KindDonut kinds={data.kinds} />
            </ChartCard>
            <ChartCard title="最活跃聊天 Top15" subtitle="点击进入聊天" right={
              <button onClick={() => navigate(`/chats${filters.platform ? `?platform=${filters.platform}` : ""}`)} className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-primary)]">查看全部</button>
            }>
              <RankList
                items={data.top_chats}
                getLabel={c => c.chat_name || c.chat_id}
                getSub={c => <>{chatTypeLabel(c.chat_type)} · {PLATFORM_LABEL[c.platform] || c.platform}</>}
                getValue={c => c.count}
                getColor={c => PLATFORM_COLOR[c.platform] || "var(--color-primary)"}
                onClick={c => navigate(c.chat_type === "group"
                  ? `/members?platform=${c.platform}&chat_id=${encodeURIComponent(c.chat_id)}`
                  : `/contacts?platform=${c.platform}&chat_id=${encodeURIComponent(c.chat_id)}`)}
              />
            </ChartCard>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
            <ChartCard title="一周分布" subtitle="按星期聚合">
              <WeekdayBarChart weekdays={data.weekday} />
            </ChartCard>
            <ChartCard title="平台构成">
              <div className="space-y-3 pt-1">
                {data.platforms.map(p => {
                  const ratio = t!.messages ? p.count / t!.messages * 100 : 0
                  return (
                    <div key={p.platform} className="grid grid-cols-[90px_1fr_120px] items-center gap-3 text-sm">
                      <span className="flex items-center gap-2">
                        <span className="h-2.5 w-2.5 rounded-full" style={{ background: PLATFORM_COLOR[p.platform] || "#94a3b8" }} />
                        {PLATFORM_LABEL[p.platform] || p.platform}
                      </span>
                      <div className="h-2 overflow-hidden rounded-full bg-[var(--color-secondary)]">
                        <div className="h-full rounded-full" style={{ width: `${ratio}%`, background: PLATFORM_COLOR[p.platform] || "#94a3b8" }} />
                      </div>
                      <span className="text-right text-xs tabular-nums text-[var(--color-muted-foreground)]">{formatCount(p.count)} · {p.chats} 聊天 · {ratio.toFixed(1)}%</span>
                    </div>
                  )
                })}
              </div>
            </ChartCard>
          </div>
        </>
      )}
    </div>
  )
}
