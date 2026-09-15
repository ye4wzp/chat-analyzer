import { useCallback, useEffect, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { Gift, Link2, Loader2, Sparkles, TrendingDown, TrendingUp, Trophy } from "lucide-react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, type Report, type ReportMeta, type StatsYearly } from "@/lib/api"
import { PLATFORM_COLOR, PLATFORM_LABEL, WEEKDAY_LABEL, chatTypeLabel } from "@/lib/constants"
import { formatCount } from "@/lib/utils"
import { useOnStatsDone, useStatsFilters } from "@/hooks/useStatsFilters"
import { useTaskRunner } from "@/hooks/useTaskRunner"
import { StatsFilterBar, StatsNotReady } from "@/components/StatsToolbar"
import { SubNav } from "@/components/SubNav"
import { REPORT_SUBNAV } from "@/lib/subnav"
import { CalendarHeatmap, ChartCard, HourBarChart, KindDonut, RankList, TrendAreaChart, WeekdayBarChart } from "@/components/charts"
import { Button } from "@/components/ui/button"
import { Select } from "@/components/ui/select"

function Hero({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: string }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
      <p className="text-xs text-[var(--color-muted-foreground)]">{label}</p>
      <p className="mt-1 text-3xl font-semibold tabular-nums tracking-tight" style={{ color: accent }}>{value}</p>
      {sub && <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">{sub}</p>}
    </div>
  )
}

export default function Yearly() {
  const navigate = useNavigate()
  const filters = useStatsFilters()
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<StatsYearly | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [report, setReport] = useState<Report | null>(null)
  const runner = useTaskRunner()
  const generating = runner.running.has("report")

  const year = params.get("year") ? Number(params.get("year")) : undefined

  const load = useCallback(() => {
    setLoading(true)
    const q = new URLSearchParams()
    if (year) q.set("year", String(year))
    if (filters.platform) q.set("platform", filters.platform)
    if (filters.account) q.set("account", filters.account)
    fetchAPI<StatsYearly>(`/stats/yearly?${q}`)
      .then(d => { setData(d); setError("") })
      .catch(e => setError(getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [year, filters.platform, filters.account])

  useEffect(() => { load() }, [load])
  useOnStatsDone(load)

  // Latest saved AI yearly report for this year, if any.
  const loadReport = useCallback(async (y: number | null | undefined) => {
    if (!y) { setReport(null); return }
    try {
      const list = await fetchAPI<ReportMeta[]>("/reports?limit=200")
      const hit = list.find(r => r.period === "yearly" && r.period_start.startsWith(String(y)))
      setReport(hit ? await fetchAPI<Report>(`/reports/${hit.id}`) : null)
    } catch { setReport(null) }
  }, [])
  useEffect(() => { void loadReport(data?.year) }, [data?.year, loadReport])

  const generate = () => {
    if (!data?.year) return
    const y = data.year
    void runner.run({
      start: () => fetchAPI<{ task_id: string }>("/reports/generate", { method: "POST", body: JSON.stringify({ period: "yearly", year: y }) }),
      taskType: "report",
      loadingText: `正在生成 ${y} 年度 AI 总结...`,
      onDone: async () => { await loadReport(y) },
      onError: msg => toast.error(msg),
    })
  }

  const t = data?.totals

  return (
    <div className="mx-auto w-full max-w-[1320px] space-y-5 p-5 md:p-7">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight"><Trophy className="h-6 w-6 text-[var(--color-warning)]" />年度回顾</h1>
            <SubNav items={REPORT_SUBNAV} />
          </div>
          <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">这一年你在聊天里留下了什么</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatsFilterBar filters={filters} />
          {data?.years.length ? (
            <Select value={String(data.year)} onChange={e => setParams(prev => { const p = new URLSearchParams(prev); p.set("year", e.target.value); return p }, { replace: true })} className="h-9 w-28 rounded-lg bg-[var(--color-card)] text-sm">
              {data.years.map(y => <option key={y} value={y}>{y} 年</option>)}
            </Select>
          ) : null}
          <Button size="sm" disabled={generating || !data?.year} onClick={generate}>
            {generating ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Sparkles className="mr-1.5 h-3.5 w-3.5" />}
            {report ? "重新生成 AI 总结" : "生成 AI 总结"}
          </Button>
        </div>
      </header>

      {error && <p className="text-sm text-[var(--color-destructive)]">{error}</p>}

      {loading && !data ? (
        <div className="flex items-center justify-center py-20"><Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" /></div>
      ) : !data || !data.year || !t ? (
        <StatsNotReady />
      ) : (
        <>
          <section className="relative overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-card)] p-6 shadow-sm md:p-8">
            <div className="pointer-events-none absolute -right-20 -top-20 h-64 w-64 rounded-full bg-[var(--color-primary)] opacity-10 blur-3xl" />
            <div className="pointer-events-none absolute -bottom-24 left-1/3 h-64 w-64 rounded-full bg-[var(--color-success)] opacity-10 blur-3xl" />
            <p className="text-sm text-[var(--color-muted-foreground)]">{data.year} 年 · {filters.platform ? PLATFORM_LABEL[filters.platform] : "全部平台"}</p>
            <div className="mt-2 flex flex-wrap items-end gap-x-3 gap-y-1">
              <span className="text-5xl font-semibold tabular-nums tracking-tight md:text-6xl">{t.messages.toLocaleString()}</span>
              <span className="pb-2 text-base text-[var(--color-muted-foreground)]">条消息</span>
              {t.delta_pct != null && (
                <span className={`mb-2 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs ${t.delta_pct >= 0 ? "bg-[var(--color-success-subtle)] text-[var(--color-success)]" : "bg-[var(--color-destructive-subtle)] text-[var(--color-destructive)]"}`}>
                  {t.delta_pct >= 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                  较 {data.year - 1} 年 {t.delta_pct >= 0 ? "+" : ""}{t.delta_pct}%
                </span>
              )}
            </div>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-[var(--color-muted-foreground)]">
              {t.active_days} 天有消息，活跃日平均 {t.avg_per_active_day} 条；
              和 {t.chats} 个聊天有过往来（其中 {t.groups} 个群），新认识 {t.new_chats} 个聊天。
              {data.peak_day && <> 最热闹的一天是 <span className="text-[var(--color-foreground)]">{data.peak_day.day}</span>（{data.peak_day.count.toLocaleString()} 条）。</>}
              {data.peak_hour != null && <> 你最常在 <span className="text-[var(--color-foreground)]">{data.peak_hour}:00</span> 前后聊天。</>}
              {data.streak.days > 1 && <> 最长连续 {data.streak.days} 天没断过（{data.streak.start?.slice(5)} ~ {data.streak.end?.slice(5)}）。</>}
            </p>
          </section>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Hero label="最忙的月份" value={data.peak_month ? `${Number(data.peak_month.month.slice(5))} 月` : "—"} sub={data.peak_month ? `${data.peak_month.count.toLocaleString()} 条` : undefined} accent="var(--color-success)" />
            <Hero label="最爱聊的星期" value={(() => { const i = data.weekday.indexOf(Math.max(...data.weekday)); return data.weekday[i] ? `周${WEEKDAY_LABEL[i]}` : "—" })()} accent="var(--color-primary)" />
            <Hero label="红包 / 转账" value={`${data.resources.redpacket || 0} / ${data.resources.transfer || 0}`} sub="次数（金额不可得）" accent="var(--color-destructive)" />
            <Hero label="分享的链接" value={formatCount(data.resources.link || 0)} sub={`文件 ${data.resources.file || 0} · 小程序 ${data.resources.miniprogram || 0}`} accent="var(--color-warning)" />
          </div>

          <ChartCard title={`${data.year} 年每一天`}>
            <CalendarHeatmap year={data.year} days={data.days} onSelectDay={() => navigate(`/calendar?year=${data.year}${filters.platform ? `&platform=${filters.platform}` : ""}`)} />
          </ChartCard>

          <div className="grid gap-4 xl:grid-cols-3">
            <ChartCard title="按月">
              <TrendAreaChart data={data.monthly.map(m => ({ month: `${Number(m.month.slice(5))}月`, count: m.count }))} height={200} color="var(--color-success)" />
            </ChartCard>
            <ChartCard title="24 小时" subtitle="按当年活跃聊天加权估算">
              <HourBarChart hours={data.hourly} height={200} />
            </ChartCard>
            <ChartCard title="一周">
              <WeekdayBarChart weekdays={data.weekday} height={200} />
            </ChartCard>
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <ChartCard title="最常聊的人" subtitle="私聊 Top10">
              <RankList items={data.top_privates} getLabel={c => c.chat_name || c.chat_id} getSub={c => `${c.active_days} 天`} getValue={c => c.count} getColor={c => PLATFORM_COLOR[c.platform] || "var(--color-primary)"} onClick={c => navigate(`/contacts?platform=${c.platform}&chat_id=${encodeURIComponent(c.chat_id)}`)} />
            </ChartCard>
            <ChartCard title="最热闹的群" subtitle="群 / 频道 Top10">
              <RankList items={data.top_groups} getLabel={c => c.chat_name || c.chat_id} getSub={c => `${chatTypeLabel(c.chat_type)} · ${c.active_days} 天`} getValue={c => c.count} getColor={c => PLATFORM_COLOR[c.platform] || "var(--color-primary)"} onClick={c => navigate(`/members?platform=${c.platform}&chat_id=${encodeURIComponent(c.chat_id)}`)} />
            </ChartCard>
            <ChartCard title="群里最能说的人" subtitle="跨群 Top10">
              <RankList items={data.top_speakers} getLabel={s => s.name} getSub={s => s.chat_name || ""} getValue={s => s.count} getColor={() => "var(--chart-3)"} onClick={s => navigate(`/members?platform=${s.platform}&chat_id=${encodeURIComponent(s.chat_id)}`)} />
            </ChartCard>
          </div>

          <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
            <ChartCard title="消息类型">
              <KindDonut kinds={data.kinds} height={260} />
            </ChartCard>
            <ChartCard title="AI 年度总结" subtitle={report ? `生成于 ${report.created_at.slice(0, 16).replace("T", " ")}` : "读取这一年的知识点、待办和统计，写一段回顾"} right={<Sparkles className="h-4 w-4 text-[var(--color-info)]" />}>
              {report ? (
                <article className="max-h-[520px] overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed">{report.content}</article>
              ) : (
                <div className="flex h-48 flex-col items-center justify-center gap-3 text-center">
                  <p className="text-sm text-[var(--color-muted-foreground)]">还没有 {data.year} 年的 AI 总结</p>
                  <Button size="sm" variant="outline" disabled={generating} onClick={generate}>
                    {generating ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Sparkles className="mr-1.5 h-3.5 w-3.5" />}生成
                  </Button>
                </div>
              )}
            </ChartCard>
          </div>

          <p className="flex items-center gap-2 text-[11px] text-[var(--color-muted-foreground)]">
            <Gift className="h-3.5 w-3.5" /> 红包 / 转账只能统计次数：微信不会在消息里暴露金额。
            <Link2 className="ml-3 h-3.5 w-3.5" /> 链接与文件在「资源汇总」可逐条查看。
          </p>
        </>
      )}
    </div>
  )
}
