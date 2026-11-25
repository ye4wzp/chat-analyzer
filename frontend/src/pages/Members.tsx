import { useCallback, useEffect, useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { ArrowUpDown, Contact, Loader2, MessagesSquare, Moon, Search, Users } from "lucide-react"
import { fetchAPI, getErrorMessage, type ContactListItem, type MemberStat, type MembersResponse } from "@/lib/api"
import { PLATFORM_COLOR, PLATFORM_LABEL, chatTypeLabel } from "@/lib/constants"
import { formatCount, formatDate } from "@/lib/utils"
import { useOnStatsDone, useStatsFilters } from "@/hooks/useStatsFilters"
import { StatsFilterBar, StatsNotReady } from "@/components/StatsToolbar"
import { ChartCard, EmptyChart, HourSpark, MultiLineChart, RankList } from "@/components/charts"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

type SortKey = "count" | "last_ts" | "first_ts" | "night_ratio" | "active_months"

function Kpi({ label, value, sub, icon: Icon, color }: { label: string; value: string; sub?: string; icon: typeof Users; color: string }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <p className="text-xs text-[var(--color-muted-foreground)]">{label}</p>
        <Icon className="h-4 w-4" style={{ color }} />
      </div>
      <p className="mt-1 text-2xl font-semibold tabular-nums" style={{ color }}>{value}</p>
      {sub && <p className="text-xs text-[var(--color-muted-foreground)]">{sub}</p>}
    </div>
  )
}

function SortBtn({ k, label, sort, setSort }: { k: SortKey; label: string; sort: SortKey; setSort: (k: SortKey) => void }) {
  return (
    <button onClick={() => setSort(k)} className={`inline-flex items-center gap-1 ${sort === k ? "text-[var(--color-primary)]" : ""}`}>
      {label}<ArrowUpDown className="h-3 w-3 opacity-60" />
    </button>
  )
}

export default function Members() {
  const navigate = useNavigate()
  const filters = useStatsFilters()
  const [params, setParams] = useSearchParams()
  const [groups, setGroups] = useState<ContactListItem[]>([])
  const [groupsLoading, setGroupsLoading] = useState(true)
  const [data, setData] = useState<MembersResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [search, setSearch] = useState("")
  const [sort, setSort] = useState<SortKey>("count")
  const [inactiveDays, setInactiveDays] = useState(90)

  const platform = params.get("platform")
  const chatId = params.get("chat_id")

  useEffect(() => {
    setGroupsLoading(true)
    const q = new URLSearchParams({ sort: "messages" })
    if (filters.platform) q.set("platform", filters.platform)
    if (filters.account) q.set("account", filters.account)
    fetchAPI<ContactListItem[]>(`/contacts?${q}`)
      // Channels are one-way broadcasts (a single "member"), so they're excluded here.
      .then(list => setGroups(list.filter(c => c.chat_type === "group")))
      .catch(e => setError(getErrorMessage(e)))
      .finally(() => setGroupsLoading(false))
  }, [filters.platform, filters.account])

  // Default to the busiest group.
  useEffect(() => {
    if ((!platform || !chatId) && groups.length) {
      const g = groups[0]
      setParams(prev => { const p = new URLSearchParams(prev); p.set("platform", g.platform); p.set("chat_id", g.chat_id); return p }, { replace: true })
    }
  }, [groups, platform, chatId, setParams])

  const load = useCallback(() => {
    if (!platform || !chatId) return
    setLoading(true)
    const q = new URLSearchParams({ limit: "300", trend_top: "6", inactive_days: String(inactiveDays) })
    if (filters.account) q.set("account", filters.account)
    fetchAPI<MembersResponse>(`/stats/members/${platform}/${encodeURIComponent(chatId)}?${q}`)
      .then(d => { setData(d); setError("") })
      .catch(e => { setError(getErrorMessage(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [platform, chatId, filters.account, inactiveDays])

  useEffect(() => { load() }, [load])
  useOnStatsDone(load)

  const current = groups.find(g => g.platform === platform && g.chat_id === chatId)

  const members = useMemo(() => {
    if (!data) return []
    const q = search.trim().toLowerCase()
    const list = q ? data.members.filter(m => m.name.toLowerCase().includes(q)) : data.members.slice()
    const cmp: Record<SortKey, (a: MemberStat, b: MemberStat) => number> = {
      count: (a, b) => b.count - a.count,
      last_ts: (a, b) => (b.last_ts || "").localeCompare(a.last_ts || ""),
      first_ts: (a, b) => (a.first_ts || "").localeCompare(b.first_ts || ""),
      night_ratio: (a, b) => b.night_ratio - a.night_ratio,
      active_months: (a, b) => b.active_months - a.active_months,
    }
    return list.sort(cmp[sort])
  }, [data, search, sort])

  const thisMonth = data?.monthly_active.at(-1)
  const nightOwls = data ? data.members.filter(m => m.count >= 20).sort((a, b) => b.night_ratio - a.night_ratio).slice(0, 5) : []
  const top10Share = data ? data.members.slice(0, 10).reduce((s, m) => s + m.share, 0) : 0

  return (
    <div className="mx-auto w-full max-w-[1320px] space-y-5 p-5 md:p-7">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">成员分析</h1>
          <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">谁在说话、什么时候说、谁已经潜水 —— 基于群内发言统计（不含单向频道）</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatsFilterBar filters={filters} />
          <Select
            value={platform && chatId ? `${platform}|${chatId}` : ""}
            onChange={e => {
              const [p, ...rest] = e.target.value.split("|")
              const id = rest.join("|")
              if (!p || !id) return
              setParams(prev => { const n = new URLSearchParams(prev); n.set("platform", p); n.set("chat_id", id); return n })
            }}
            className="h-9 w-64 rounded-lg bg-[var(--color-card)] text-sm"
          >
            {groupsLoading && <option value="">加载群列表...</option>}
            {groups.map(g => (
              <option key={`${g.platform}|${g.chat_id}`} value={`${g.platform}|${g.chat_id}`}>
                {g.chat_name || g.chat_id} · {PLATFORM_LABEL[g.platform] || g.platform} · {formatCount(g.messages)}
              </option>
            ))}
          </Select>
        </div>
      </header>

      {error && <p className="text-sm text-[var(--color-destructive)]">{error}</p>}

      {!groupsLoading && groups.length === 0 ? (
        <StatsNotReady />
      ) : loading && !data ? (
        <div className="flex items-center justify-center py-20"><Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" /></div>
      ) : data ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-5 py-4 shadow-sm">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white" style={{ background: PLATFORM_COLOR[platform || ""] || "#64748b" }}>
                {(current?.chat_name || "?").slice(0, 1)}
              </span>
              <div className="min-w-0">
                <p className="break-words text-lg font-semibold">{current?.chat_name || chatId}</p>
                <p className="text-xs text-[var(--color-muted-foreground)]">
                  {PLATFORM_LABEL[platform || ""] || platform} · {chatTypeLabel(current?.chat_type)} · {formatDate(current?.first_ts)} ~ {formatDate(current?.last_ts)}
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => navigate(`/contacts?platform=${platform}&chat_id=${encodeURIComponent(chatId || "")}`)}><Contact className="mr-1.5 h-3.5 w-3.5" />群档案</Button>
              <Button variant="outline" size="sm" onClick={() => navigate(`/chats?platform=${platform}&chat_id=${encodeURIComponent(chatId || "")}`)}><MessagesSquare className="mr-1.5 h-3.5 w-3.5" />聊天记录</Button>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Kpi label="总消息" value={data.total_messages.toLocaleString()} icon={MessagesSquare} color="var(--color-success)" />
            <Kpi label="发言成员" value={data.member_count.toLocaleString()} sub={`Top10 贡献 ${top10Share.toFixed(0)}%`} icon={Users} color="var(--color-primary)" />
            <Kpi label="最近一月活跃" value={thisMonth ? String(thisMonth.members) : "—"} sub={thisMonth ? `${thisMonth.month} · ${formatCount(thisMonth.count)} 条` : undefined} icon={Users} color="var(--chart-3)" />
            <Kpi label={`潜水成员（${data.inactive_days} 天）`} value={String(data.inactive.length)} sub="曾发言、近期无消息" icon={Moon} color="var(--color-warning)" />
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.3fr_0.7fr]">
            <ChartCard title="Top 6 成员月度发言" subtitle="谁在带动群里的讨论">
              <MultiLineChart data={data.trend} series={data.trend_keys.map(k => ({ key: k.sender_key, name: k.name }))} />
            </ChartCard>
            <ChartCard title="每月活跃成员数">
              {data.monthly_active.length ? (
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={data.monthly_active} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 4" vertical={false} />
                    <XAxis dataKey="month" tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={24} />
                    <YAxis tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }} tickLine={false} axisLine={false} width={36} />
                    <Tooltip contentStyle={{ background: "var(--color-popover)", border: "1px solid var(--color-border)", borderRadius: 8, fontSize: 12 }} formatter={(v) => [String(v), "活跃成员"]} />
                    <Bar dataKey="members" fill="var(--chart-2)" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : <EmptyChart />}
            </ChartCard>
          </div>

          <ChartCard
            title="成员排行"
            subtitle={`${members.length} / ${data.member_count} 位成员`}
            right={
              <div className="relative">
                <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
                <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜成员" className="h-8 w-40 rounded-lg pl-8 text-xs" />
              </div>
            }
          >
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-[var(--color-muted-foreground)]">
                  <tr className="border-b border-[var(--color-border)] text-left">
                    <th className="py-2 pr-2 font-medium">#</th>
                    <th className="py-2 pr-2 font-medium">成员</th>
                    <th className="py-2 pr-2 font-medium"><SortBtn k="count" label="消息" sort={sort} setSort={setSort} /></th>
                    <th className="py-2 pr-2 font-medium">占比</th>
                    <th className="py-2 pr-2 font-medium">活跃时段（0–23）</th>
                    <th className="py-2 pr-2 font-medium"><SortBtn k="night_ratio" label="夜猫" sort={sort} setSort={setSort} /></th>
                    <th className="py-2 pr-2 font-medium"><SortBtn k="active_months" label="活跃月" sort={sort} setSort={setSort} /></th>
                    <th className="py-2 pr-2 font-medium"><SortBtn k="first_ts" label="首次" sort={sort} setSort={setSort} /></th>
                    <th className="py-2 pr-2 font-medium"><SortBtn k="last_ts" label="最近" sort={sort} setSort={setSort} /></th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((m, i) => (
                    <tr key={m.sender_key} className="border-b border-[var(--color-border)]/60 hover:bg-[var(--color-card-elevated)]">
                      <td className="py-2 pr-2 tabular-nums text-[var(--color-muted-foreground)]">{i + 1}</td>
                      <td className="max-w-[200px] break-words py-2 pr-2 font-medium" title={m.sender_key}>{m.name}</td>
                      <td className="py-2 pr-2 tabular-nums">{m.count.toLocaleString()}</td>
                      <td className="py-2 pr-2">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-[var(--color-secondary)]">
                            <div className="h-full rounded-full bg-[var(--color-primary)]" style={{ width: `${Math.min(100, m.share / (data.members[0]?.share || 1) * 100)}%` }} />
                          </div>
                          <span className="tabular-nums text-[var(--color-muted-foreground)]">{m.share}%</span>
                        </div>
                      </td>
                      <td className="py-2 pr-2"><HourSpark hours={m.hours} className="w-28" /></td>
                      <td className="py-2 pr-2 tabular-nums">{m.night_ratio > 0 ? `${m.night_ratio}%` : "—"}</td>
                      <td className="py-2 pr-2 tabular-nums">{m.active_months}</td>
                      <td className="py-2 pr-2 tabular-nums text-[var(--color-muted-foreground)]">{formatDate(m.first_ts)}</td>
                      <td className="py-2 pr-2 tabular-nums text-[var(--color-muted-foreground)]">{formatDate(m.last_ts)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </ChartCard>

          <div className="grid gap-4 xl:grid-cols-2">
            <ChartCard title="夜猫子榜" subtitle="0–6 点发言占比（≥20 条）">
              <RankList items={nightOwls} getLabel={m => m.name} getSub={m => `${m.count} 条`} getValue={m => m.night_ratio} max={100} getColor={() => "var(--chart-6)"} />
              {nightOwls.length > 0 && <p className="mt-2 text-xs text-[var(--color-muted-foreground)]">数值为凌晨发言百分比</p>}
            </ChartCard>
            <ChartCard
              title="潜水成员"
              subtitle={`最近 ${data.inactive_days} 天没有发言（相对群里最后一条消息）`}
              right={
                <Select value={String(inactiveDays)} onChange={e => setInactiveDays(Number(e.target.value))} className="h-8 w-24 text-xs">
                  {[30, 60, 90, 180, 365].map(d => <option key={d} value={d}>{d} 天</option>)}
                </Select>
              }
            >
              {data.inactive.length === 0 ? (
                <EmptyChart text="没有潜水成员" />
              ) : (
                <ul className="max-h-72 space-y-1 overflow-y-auto text-xs">
                  {data.inactive.map(m => (
                    <li key={m.sender_key} className="flex items-center justify-between rounded-md px-2 py-1.5 hover:bg-[var(--color-card-elevated)]">
                      <span className="truncate">{m.name}</span>
                      <span className="shrink-0 tabular-nums text-[var(--color-muted-foreground)]">{m.count} 条 · 最后 {formatDate(m.last_ts)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </ChartCard>
          </div>
        </>
      ) : null}
    </div>
  )
}
