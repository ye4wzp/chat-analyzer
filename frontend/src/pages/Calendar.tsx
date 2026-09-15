import { useCallback, useEffect, useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { ChevronLeft, ChevronRight, Flame, Loader2, X } from "lucide-react"
import { fetchAPI, getErrorMessage, type ContactListItem, type Message, type StatsCalendar } from "@/lib/api"
import { PLATFORM_LABEL, chatTypeLabel } from "@/lib/constants"
import { formatCount, formatTime } from "@/lib/utils"
import { useOnStatsDone, useStatsFilters } from "@/hooks/useStatsFilters"
import { StatsFilterBar, StatsNotReady } from "@/components/StatsToolbar"
import { CalendarHeatmap, ChartCard, EmptyChart, TrendAreaChart } from "@/components/charts"
import { Select } from "@/components/ui/select"
import { Button } from "@/components/ui/button"

function longestStreak(days: string[]): { days: number; start: string | null; end: string | null } {
  let best = { days: 0, start: null as string | null, end: null as string | null }
  let runStart: string | null = null
  let prev: Date | null = null
  let len = 0
  for (const d of [...days].sort()) {
    const cur = new Date(`${d}T00:00:00`)
    if (prev && cur.getTime() - prev.getTime() === 86_400_000) len += 1
    else { runStart = d; len = 1 }
    prev = cur
    if (len > best.days) best = { days: len, start: runStart, end: d }
  }
  return best
}

export default function Calendar() {
  const navigate = useNavigate()
  const filters = useStatsFilters()
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<StatsCalendar | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [chats, setChats] = useState<ContactListItem[]>([])
  const [selectedDay, setSelectedDay] = useState<{ day: string; count: number } | null>(null)
  const [dayMessages, setDayMessages] = useState<Message[] | null>(null)
  const [dayLoading, setDayLoading] = useState(false)

  const year = params.get("year") ? Number(params.get("year")) : undefined
  const chatId = params.get("chat_id") || ""

  useEffect(() => {
    const q = new URLSearchParams({ sort: "messages", limit: "400" })
    if (filters.platform) q.set("platform", filters.platform)
    fetchAPI<ContactListItem[]>(`/contacts?${q}`).then(setChats).catch(() => setChats([]))
  }, [filters.platform])

  const load = useCallback(() => {
    setLoading(true)
    const q = new URLSearchParams()
    if (year) q.set("year", String(year))
    if (filters.platform) q.set("platform", filters.platform)
    if (filters.account) q.set("account", filters.account)
    if (chatId) q.set("chat_id", chatId)
    fetchAPI<StatsCalendar>(`/stats/calendar?${q}`)
      .then(d => { setData(d); setError("") })
      .catch(e => setError(getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [year, filters.platform, filters.account, chatId])

  useEffect(() => { load(); setSelectedDay(null); setDayMessages(null) }, [load])
  useOnStatsDone(load)

  const setYear = (y: number) => setParams(prev => { const p = new URLSearchParams(prev); p.set("year", String(y)); return p }, { replace: true })
  const setChat = (v: string) => setParams(prev => {
    const p = new URLSearchParams(prev)
    if (v) { const [pl, ...rest] = v.split("|"); p.set("platform", pl); p.set("chat_id", rest.join("|")) } else p.delete("chat_id")
    return p
  }, { replace: true })

  const selectDay = (day: string, count: number) => {
    setSelectedDay({ day, count })
    if (!count) { setDayMessages([]); return }
    setDayLoading(true)
    const q = new URLSearchParams({ since: day, until: day, limit: "200" })
    if (filters.platform) q.set("platform", filters.platform)
    if (chatId) q.set("chat_id", chatId)
    fetchAPI<Message[]>(`/messages?${q}`)
      .then(setDayMessages)
      .catch(e => setError(getErrorMessage(e)))
      .finally(() => setDayLoading(false))
  }

  const monthly = useMemo(() => {
    if (!data?.year) return []
    const arr = Array.from({ length: 12 }, (_, i) => ({ month: `${i + 1}月`, count: 0 }))
    for (const d of data.days) arr[Number(d.day.slice(5, 7)) - 1].count += d.count
    return arr
  }, [data])
  const streak = useMemo(() => longestStreak(data?.days.map(d => d.day) ?? []), [data])
  const weekdayAvg = useMemo(() => {
    const sums = [0, 0, 0, 0, 0, 0, 0]; const cnt = [0, 0, 0, 0, 0, 0, 0]
    for (const d of data?.days ?? []) { const w = new Date(`${d.day}T00:00:00`).getDay(); sums[w] += d.count; cnt[w] += 1 }
    return sums.map((s, i) => cnt[i] ? Math.round(s / cnt[i]) : 0)
  }, [data])
  const busiestWeekday = weekdayAvg.indexOf(Math.max(...weekdayAvg))
  const currentChat = chats.find(c => c.chat_id === chatId)

  const yearIdx = data?.years.indexOf(data.year ?? -1) ?? -1
  // Captured once per mount: React Compiler flags Date.now() in render as impure.
  const [now] = useState(() => Date.now())
  const daysElapsed = useMemo(() => {
    if (!data?.year) return 0
    const start = new Date(data.year, 0, 1).getTime()
    const end = data.year === new Date(now).getFullYear() ? now : new Date(data.year, 11, 31).getTime()
    return Math.floor((end - start) / 86_400_000) + 1
  }, [data, now])

  return (
    <div className="mx-auto w-full max-w-[1320px] space-y-5 p-5 md:p-7">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">日历热图</h1>
          <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">每一天聊了多少，点一个格子看当天的消息</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatsFilterBar filters={filters}>
            <Select value={chatId ? `${params.get("platform")}|${chatId}` : ""} onChange={e => setChat(e.target.value)} className="h-9 w-56 rounded-lg bg-[var(--color-card)] text-sm">
              <option value="">全部聊天</option>
              {chats.map(c => <option key={`${c.platform}|${c.chat_id}`} value={`${c.platform}|${c.chat_id}`}>{c.chat_name || c.chat_id} · {formatCount(c.messages)}</option>)}
            </Select>
          </StatsFilterBar>
          {data?.years.length ? (
            <div className="flex items-center gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] p-0.5">
              <button disabled={yearIdx < 0 || yearIdx >= data.years.length - 1} onClick={() => setYear(data.years[yearIdx + 1])} className="rounded-md p-1.5 hover:bg-[var(--color-accent)] disabled:opacity-30"><ChevronLeft className="h-4 w-4" /></button>
              <Select value={String(data.year)} onChange={e => setYear(Number(e.target.value))} className="h-7 w-24 border-0 bg-transparent text-sm">
                {data.years.map(y => <option key={y} value={y}>{y} 年</option>)}
              </Select>
              <button disabled={yearIdx <= 0} onClick={() => setYear(data.years[yearIdx - 1])} className="rounded-md p-1.5 hover:bg-[var(--color-accent)] disabled:opacity-30"><ChevronRight className="h-4 w-4" /></button>
            </div>
          ) : null}
        </div>
      </header>

      {error && <p className="text-sm text-[var(--color-destructive)]">{error}</p>}

      {loading && !data ? (
        <div className="flex items-center justify-center py-20"><Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" /></div>
      ) : !data || !data.year ? (
        <StatsNotReady />
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
            {[
              { label: `${data.year} 年消息`, value: data.total.toLocaleString(), color: "var(--color-success)" },
              { label: "活跃天数", value: `${data.active_days} / ${daysElapsed}`, sub: daysElapsed ? `${Math.round(data.active_days / daysElapsed * 100)}% 的日子有消息` : undefined, color: "var(--color-primary)" },
              { label: "最忙的一天", value: data.peak ? data.peak.day.slice(5) : "—", sub: data.peak ? `${data.peak.count.toLocaleString()} 条` : undefined, color: "var(--color-warning)" },
              { label: "最长连续活跃", value: `${streak.days} 天`, sub: streak.start ? `${streak.start.slice(5)} ~ ${streak.end?.slice(5)}` : undefined, color: "var(--chart-3)" },
              { label: "最爱聊的星期", value: busiestWeekday >= 0 && weekdayAvg[busiestWeekday] ? `周${["日", "一", "二", "三", "四", "五", "六"][busiestWeekday]}` : "—", sub: weekdayAvg[busiestWeekday] ? `日均 ${weekdayAvg[busiestWeekday]} 条` : undefined, color: "var(--chart-6)" },
            ].map(k => (
              <div key={k.label} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
                <p className="text-xs text-[var(--color-muted-foreground)]">{k.label}</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums" style={{ color: k.color }}>{k.value}</p>
                {k.sub && <p className="text-[11px] text-[var(--color-muted-foreground)]">{k.sub}</p>}
              </div>
            ))}
          </div>

          <ChartCard title={`${data.year} 年`} subtitle={currentChat ? `${currentChat.chat_name} · ${chatTypeLabel(currentChat.chat_type)}` : `${filters.platform ? PLATFORM_LABEL[filters.platform] : "全部平台"}`} right={<Flame className="h-4 w-4 text-[var(--color-warning)]" />}>
            <CalendarHeatmap year={data.year} days={data.days} onSelectDay={selectDay} selected={selectedDay?.day} />
          </ChartCard>

          <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
            <ChartCard title="按月">
              <TrendAreaChart data={monthly} height={200} color="var(--color-success)" />
            </ChartCard>
            <ChartCard
              title={selectedDay ? `${selectedDay.day} · ${selectedDay.count.toLocaleString()} 条` : "当天消息"}
              subtitle={selectedDay ? undefined : "在热图上点一个日期"}
              right={selectedDay && (
                <div className="flex items-center gap-1">
                  <Button size="sm" variant="outline" onClick={() => navigate(`/timeline?date=${selectedDay.day}${filters.platform ? `&platform=${filters.platform}` : ""}`)}>时间线</Button>
                  <button onClick={() => { setSelectedDay(null); setDayMessages(null) }} className="rounded p-1 hover:bg-[var(--color-accent)]"><X className="h-4 w-4" /></button>
                </div>
              )}
            >
              {!selectedDay ? (
                <EmptyChart text="未选择日期" />
              ) : dayLoading ? (
                <div className="flex h-48 items-center justify-center"><Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" /></div>
              ) : !dayMessages?.length ? (
                <EmptyChart text="这一天没有消息" />
              ) : (
                <ul className="max-h-[420px] space-y-1 overflow-y-auto pr-1">
                  {dayMessages.map(m => (
                    <li key={m.id} className="grid grid-cols-[52px_1fr] gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-[var(--color-card-elevated)]">
                      <span className="tabular-nums text-[var(--color-muted-foreground)]">{formatTime(m.timestamp).split(" ").pop()?.slice(0, 5)}</span>
                      <p className="min-w-0 truncate">
                        <span className="text-[var(--color-muted-foreground)]">{m.chat_name} · </span>
                        <span className="font-medium">{m.sender_name || "?"}</span>：{m.content}
                      </p>
                    </li>
                  ))}
                  {dayMessages.length >= 200 && <li className="py-2 text-center text-[11px] text-[var(--color-muted-foreground)]">仅显示前 200 条，完整内容请到时间线查看</li>}
                </ul>
              )}
            </ChartCard>
          </div>
        </>
      )}
    </div>
  )
}
