import { useCallback, useEffect, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { ArrowDownLeft, ArrowUpRight, Gift, Info, Loader2, Wallet } from "lucide-react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage } from "@/lib/api"
import { PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/constants"
import { formatTime } from "@/lib/utils"
import { useOnStatsDone, useStatsFilters } from "@/hooks/useStatsFilters"
import { StatsFilterBar } from "@/components/StatsToolbar"
import { ChartCard, EmptyChart, HourBarChart, RankList, WeekdayBarChart } from "@/components/charts"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

interface PaymentsResponse {
  totals: { redpacket: number; transfer: number; sent: number }
  direction: { redpacket: { sent: number; received: number }; transfer: { sent: number; received: number } } | null
  me: string[]
  monthly: { month: string; redpacket: number; transfer: number }[]
  by_chat: { platform: string; chat_id: string; chat_name: string | null; redpacket: number; transfer: number; n: number; last_ts: string }[]
  by_sender: { platform: string; sender_name: string; redpacket: number; transfer: number; n: number }[]
  hourly: number[]
  weekday: number[]
  items: { id: number; message_id: number; platform: string; chat_id: string; chat_name: string | null; sender_name: string | null; timestamp: string; kind: string; title: string | null; is_me: boolean }[]
  has_more: boolean
}

const KIND_META: Record<string, { label: string; color: string }> = {
  redpacket: { label: "红包", color: "#dc2626" },
  transfer: { label: "转账", color: "#f59e0b" },
}

export default function Payments() {
  const navigate = useNavigate()
  const filters = useStatsFilters()
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<PaymentsResponse | null>(null)
  const [items, setItems] = useState<PaymentsResponse["items"]>([])
  const [loading, setLoading] = useState(true)
  const [since, setSince] = useState("")
  const [until, setUntil] = useState("")
  const chatId = params.get("chat_id") || ""

  const load = useCallback((append = false, offset = 0) => {
    setLoading(true)
    const p = new URLSearchParams({ limit: "50", offset: String(offset) })
    if (filters.platform) p.set("platform", filters.platform)
    if (filters.account) p.set("account", filters.account)
    if (chatId) p.set("chat_id", chatId)
    if (since) p.set("since", since)
    if (until) p.set("until", until)
    fetchAPI<PaymentsResponse>(`/payments?${p}`)
      .then(d => { setData(d); setItems(prev => append ? [...prev, ...d.items] : d.items) })
      .catch(e => toast.error(getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [filters.platform, filters.account, chatId, since, until])

  useEffect(() => { load() }, [load])
  useOnStatsDone(useCallback(() => load(), [load]))

  const clearChat = () => setParams(prev => { const p = new URLSearchParams(prev); p.delete("chat_id"); return p }, { replace: true })
  const total = (data?.totals.redpacket ?? 0) + (data?.totals.transfer ?? 0)

  return (
    <div className="mx-auto w-full max-w-[1320px] space-y-5 p-5 md:p-7">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight"><Gift className="h-6 w-6 text-[var(--color-destructive)]" />红包转账</h1>
          <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">谁发得多、发给谁、什么时候发 —— 只统计次数</p>
        </div>
        <StatsFilterBar filters={filters}>
          <Input type="date" value={since} onChange={e => setSince(e.target.value)} className="h-9 w-36 rounded-lg text-xs" />
          <span className="text-xs text-[var(--color-muted-foreground)]">~</span>
          <Input type="date" value={until} onChange={e => setUntil(e.target.value)} className="h-9 w-36 rounded-lg text-xs" />
        </StatsFilterBar>
      </header>

      <details className="group rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-2 text-xs text-[var(--color-muted-foreground)]">
        <summary className="flex cursor-pointer list-none items-center gap-2 select-none">
          <Info className="h-3.5 w-3.5 shrink-0" />
          为什么没有金额？{data?.me.length ? <span className="ml-1">（我 = {data.me.map(n => `「${n}」`).join("")}）</span> : <span className="ml-1 text-[var(--color-warning)]">尚未识别出「我」，方向暂不可用</span>}
          <span className="ml-auto text-[10px] group-open:hidden">展开</span>
          <span className="ml-auto hidden text-[10px] group-open:inline">收起</span>
        </summary>
        <p className="mt-2 leading-relaxed">
          微信导出的消息里没有金额字段——红包 / 转账都只是一条「微信红包」「微信转账」占位消息，所以这里只能统计次数和方向。
          「我发 / 别人发」按发送人是否是你自己判断；识别不准时到 设置 → 常规 填写「我的昵称」。
        </p>
      </details>

      {chatId && (
        <div className="flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
          筛选：{data?.by_chat.find(c => c.chat_id === chatId)?.chat_name || chatId}
          <button onClick={clearChat} className="text-[var(--color-primary)]">清除</button>
        </div>
      )}

      {loading && !data ? (
        <div className="flex items-center justify-center py-20"><Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" /></div>
      ) : !data || total === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--color-border)] py-16 text-center text-sm text-[var(--color-muted-foreground)]">这个范围内没有红包 / 转账记录</div>
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
              <p className="text-xs text-[var(--color-muted-foreground)]">红包</p>
              <p className="mt-1 text-3xl font-semibold tabular-nums" style={{ color: KIND_META.redpacket.color }}>{data.totals.redpacket}</p>
              {data.direction && <p className="mt-1 text-[11px] text-[var(--color-muted-foreground)]">我发 {data.direction.redpacket.sent} · 别人发 {data.direction.redpacket.received}</p>}
            </div>
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
              <p className="text-xs text-[var(--color-muted-foreground)]">转账</p>
              <p className="mt-1 text-3xl font-semibold tabular-nums" style={{ color: KIND_META.transfer.color }}>{data.totals.transfer}</p>
              {data.direction && <p className="mt-1 text-[11px] text-[var(--color-muted-foreground)]">我发 {data.direction.transfer.sent} · 别人发 {data.direction.transfer.received}</p>}
            </div>
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
              <p className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]"><ArrowUpRight className="h-3.5 w-3.5" />我发出</p>
              <p className="mt-1 text-3xl font-semibold tabular-nums text-[var(--color-warning)]">{data.direction ? data.totals.sent : "—"}</p>
              <p className="mt-1 text-[11px] text-[var(--color-muted-foreground)]">红包 + 转账</p>
            </div>
            <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm">
              <p className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]"><ArrowDownLeft className="h-3.5 w-3.5" />收到 / 他人</p>
              <p className="mt-1 text-3xl font-semibold tabular-nums text-[var(--color-success)]">{data.direction ? total - data.totals.sent : "—"}</p>
              <p className="mt-1 text-[11px] text-[var(--color-muted-foreground)]">私聊里是对方发的；群里是别人发的</p>
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.3fr_0.7fr]">
            <ChartCard title="按月">
              {data.monthly.length ? (
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={data.monthly} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="3 4" vertical={false} />
                    <XAxis dataKey="month" tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={24} />
                    <YAxis tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }} tickLine={false} axisLine={false} width={32} allowDecimals={false} />
                    <Tooltip contentStyle={{ background: "var(--color-popover)", border: "1px solid var(--color-border)", borderRadius: 8, fontSize: 12 }} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Bar dataKey="redpacket" name="红包" stackId="a" fill={KIND_META.redpacket.color} />
                    <Bar dataKey="transfer" name="转账" stackId="a" fill={KIND_META.transfer.color} radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              ) : <EmptyChart />}
            </ChartCard>
            <ChartCard title="几点发">
              <HourBarChart hours={data.hourly} height={240} color={KIND_META.redpacket.color} />
            </ChartCard>
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <ChartCard title="哪里发得多" subtitle="按聊天">
              <RankList
                items={data.by_chat}
                getLabel={c => c.chat_name || c.chat_id}
                getSub={c => `红包 ${c.redpacket} · 转账 ${c.transfer}`}
                getValue={c => c.n}
                getColor={c => PLATFORM_COLOR[c.platform] || "var(--color-primary)"}
                onClick={c => setParams(prev => { const p = new URLSearchParams(prev); p.set("platform", c.platform); p.set("chat_id", c.chat_id); return p }, { replace: true })}
              />
            </ChartCard>
            <ChartCard title="谁发得多" subtitle="按发送人（私聊里对方的发送人为空）">
              <RankList
                items={data.by_sender}
                getLabel={s => data.me.includes(s.sender_name) ? `${s.sender_name}（我）` : s.sender_name}
                getSub={s => `红包 ${s.redpacket} · 转账 ${s.transfer}`}
                getValue={s => s.n}
                getColor={s => data.me.includes(s.sender_name) ? "var(--color-warning)" : KIND_META.redpacket.color}
              />
            </ChartCard>
            <ChartCard title="一周">
              <WeekdayBarChart weekdays={data.weekday} height={240} color={KIND_META.transfer.color} />
            </ChartCard>
          </div>

          <ChartCard title="明细" subtitle={`${items.length} 条`} right={<Wallet className="h-4 w-4 text-[var(--color-muted-foreground)]" />}>
            <ul className="divide-y divide-[var(--color-border)]/60">
              {items.map(r => (
                <li key={r.id} className="flex items-center gap-3 py-2.5 text-sm">
                  <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium text-white" style={{ background: KIND_META[r.kind]?.color || "#94a3b8" }}>{KIND_META[r.kind]?.label || r.kind}</span>
                  <span className={`inline-flex items-center gap-0.5 text-xs ${r.is_me ? "text-[var(--color-warning)]" : "text-[var(--color-success)]"}`}>
                    {r.is_me ? <><ArrowUpRight className="h-3 w-3" />我发</> : <><ArrowDownLeft className="h-3 w-3" />{r.sender_name || "对方"}</>}
                  </span>
                  <button onClick={() => navigate(`/chats?platform=${r.platform}&chat_id=${encodeURIComponent(r.chat_id)}`)} className="min-w-0 flex-1 truncate text-left text-[var(--color-muted-foreground)] hover:text-[var(--color-primary)]">
                    {r.chat_name || r.chat_id} <span className="text-[10px]">· {PLATFORM_LABEL[r.platform] || r.platform}</span>
                  </button>
                  <span className="shrink-0 text-xs tabular-nums text-[var(--color-muted-foreground)]">{formatTime(r.timestamp)}</span>
                </li>
              ))}
            </ul>
            {data.has_more && (
              <div className="flex justify-center pt-3">
                <Button variant="outline" size="sm" disabled={loading} onClick={() => load(true, items.length)}>加载更多</Button>
              </div>
            )}
          </ChartCard>
        </>
      )}
    </div>
  )
}
