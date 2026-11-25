import { useMemo, type ReactNode } from "react"
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts"
import { KIND_COLOR, KIND_LABEL, WEEKDAY_LABEL } from "@/lib/constants"
import { formatCount } from "@/lib/utils"

/* ------------------------------------------------------------------------ */
/* Shared styling                                                            */
/* ------------------------------------------------------------------------ */

const AXIS_TICK = { fill: "var(--color-muted-foreground)", fontSize: 11 } as const
const GRID_STROKE = "var(--chart-grid)"
const SERIES_COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-6)", "var(--chart-4)", "var(--chart-5)", "#f97316", "#ec4899", "#22c55e", "#eab308", "#14b8a6", "#a855f7"]

const tooltipStyle = {
  contentStyle: {
    background: "var(--color-popover)",
    border: "1px solid var(--color-border)",
    borderRadius: 8,
    fontSize: 12,
    color: "var(--color-foreground)",
    boxShadow: "0 8px 24px rgba(0,0,0,0.25)",
  },
  labelStyle: { color: "var(--color-muted-foreground)", marginBottom: 4 },
  itemStyle: { color: "var(--color-foreground)", padding: 0 },
  cursor: { fill: "var(--color-accent)", opacity: 0.35 },
}

export function ChartCard({ title, subtitle, right, children, className = "" }: {
  title: string
  subtitle?: string
  right?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 shadow-sm ${className}`}>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-[var(--color-muted-foreground)]">{subtitle}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  )
}

export function EmptyChart({ text = "暂无数据" }: { text?: string }) {
  return <div className="flex h-48 items-center justify-center text-sm text-[var(--color-muted-foreground)]">{text}</div>
}

/* ------------------------------------------------------------------------ */
/* Monthly / daily trend                                                     */
/* ------------------------------------------------------------------------ */

export function TrendAreaChart({ data, xKey = "month", yKey = "count", height = 220, color = "var(--chart-1)" }: {
  data: object[]
  xKey?: string
  yKey?: string
  height?: number
  color?: string
}) {
  if (!data.length) return <EmptyChart />
  const gradientId = `trend-${xKey}-${yKey}`
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.35} />
            <stop offset="100%" stopColor={color} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 4" vertical={false} />
        <XAxis dataKey={xKey} tick={AXIS_TICK} tickLine={false} axisLine={false} minTickGap={24} />
        <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={48} tickFormatter={(v: number) => formatCount(v)} />
        <Tooltip {...tooltipStyle} formatter={(v) => [Number(v).toLocaleString(), "消息"]} />
        <Area type="monotone" dataKey={yKey} stroke={color} strokeWidth={2.2} fill={`url(#${gradientId})`} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

/* ------------------------------------------------------------------------ */
/* 24h / weekday bars                                                        */
/* ------------------------------------------------------------------------ */

export function HourBarChart({ hours, height = 220, color = "var(--color-success)" }: {
  hours: number[]
  height?: number
  color?: string
}) {
  const data = useMemo(() => hours.map((n, h) => ({ hour: h, label: `${h}`, count: n })), [hours])
  if (!hours.some(n => n > 0)) return <EmptyChart />
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap="18%">
        <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 4" vertical={false} />
        <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={false} interval={1} />
        <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={48} tickFormatter={(v: number) => formatCount(v)} />
        <Tooltip {...tooltipStyle} labelFormatter={(l) => `${l}:00 – ${l}:59`} formatter={(v) => [Number(v).toLocaleString(), "消息"]} />
        <Bar dataKey="count" fill={color} radius={[3, 3, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function WeekdayBarChart({ weekdays, height = 180, color = "var(--chart-2)" }: {
  weekdays: number[]
  height?: number
  color?: string
}) {
  // Backend is strftime('%w') → 0 = Sunday. Show Monday-first.
  const data = useMemo(() => [1, 2, 3, 4, 5, 6, 0].map(i => ({ label: `周${WEEKDAY_LABEL[i]}`, count: weekdays[i] ?? 0 })), [weekdays])
  if (!weekdays.some(n => n > 0)) return <EmptyChart />
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barCategoryGap="28%">
        <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 4" vertical={false} />
        <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={false} />
        <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={48} tickFormatter={(v: number) => formatCount(v)} />
        <Tooltip {...tooltipStyle} formatter={(v) => [Number(v).toLocaleString(), "消息"]} />
        <Bar dataKey="count" fill={color} radius={[3, 3, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  )
}

/* ------------------------------------------------------------------------ */
/* Message-kind donut                                                        */
/* ------------------------------------------------------------------------ */

export interface KindDatum { kind: string; label?: string; count: number }

export function KindDonut({ kinds, height = 300, centerLabel }: {
  kinds: KindDatum[]
  height?: number
  centerLabel?: string
}) {
  const total = kinds.reduce((s, k) => s + k.count, 0)
  const data = useMemo(() => kinds.filter(k => k.count > 0).map(k => ({
    name: k.label || KIND_LABEL[k.kind] || k.kind,
    kind: k.kind,
    value: k.count,
  })), [kinds])
  if (!total) return <EmptyChart />
  return (
    <div className="grid gap-4 md:grid-cols-[1fr_180px] items-center">
      <div className="relative">
        <ResponsiveContainer width="100%" height={height}>
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" innerRadius="58%" outerRadius="88%" paddingAngle={1} stroke="var(--color-card)" strokeWidth={1.5} isAnimationActive={false}>
              {data.map(d => <Cell key={d.kind} fill={KIND_COLOR[d.kind] || "#94a3b8"} />)}
            </Pie>
            <Tooltip {...tooltipStyle} formatter={(v, name) => [`${Number(v).toLocaleString()} (${(Number(v) / total * 100).toFixed(1)}%)`, String(name)]} />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-semibold tabular-nums">{formatCount(total)}</span>
          <span className="text-xs text-[var(--color-muted-foreground)]">{centerLabel || "总消息"}</span>
        </div>
      </div>
      <ul className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs md:grid-cols-1">
        {data.map(d => (
          <li key={d.kind} className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: KIND_COLOR[d.kind] || "#94a3b8" }} />
            <span className="flex-1 truncate">{d.name}</span>
            <span className="tabular-nums text-[var(--color-muted-foreground)]">{(d.value / total * 100).toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/* ------------------------------------------------------------------------ */
/* Multi-series monthly lines (member trend)                                 */
/* ------------------------------------------------------------------------ */

export function MultiLineChart({ data, series, xKey = "month", height = 260 }: {
  data: Record<string, string | number>[]
  series: { key: string; name: string }[]
  xKey?: string
  height?: number
}) {
  if (!data.length || !series.length) return <EmptyChart />
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 4" vertical={false} />
        <XAxis dataKey={xKey} tick={AXIS_TICK} tickLine={false} axisLine={false} minTickGap={24} />
        <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={44} tickFormatter={(v: number) => formatCount(v)} />
        <Tooltip {...tooltipStyle} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {series.map((s, i) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={SERIES_COLORS[i % SERIES_COLORS.length]} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

/* ------------------------------------------------------------------------ */
/* Ranked horizontal bars (Top N chats / members)                            */
/* ------------------------------------------------------------------------ */

export function RankList<T>({ items, getLabel, getSub, getValue, getColor, onClick, max }: {
  items: T[]
  getLabel: (item: T) => string
  getSub?: (item: T) => ReactNode
  getValue: (item: T) => number
  getColor?: (item: T) => string
  onClick?: (item: T) => void
  max?: number
}) {
  if (!items.length) return <EmptyChart />
  const peak = max ?? Math.max(1, ...items.map(getValue))
  return (
    <ol className="space-y-2">
      {items.map((item, idx) => {
        const v = getValue(item)
        const Tag = onClick ? "button" : "div"
        return (
          <Tag
            key={idx}
            onClick={onClick ? () => onClick(item) : undefined}
            className={`grid w-full grid-cols-[22px_1fr_72px] items-center gap-3 rounded-lg px-1 py-1 text-left ${onClick ? "transition-colors hover:bg-[var(--color-card-elevated)]" : ""}`}
          >
            <span className={`text-xs font-semibold tabular-nums ${idx < 3 ? "text-[var(--color-primary)]" : "text-[var(--color-muted-foreground)]"}`}>{idx + 1}</span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="min-w-0 break-words text-sm font-medium">{getLabel(item)}</span>
                {getSub && <span className="shrink-0 text-xs text-[var(--color-muted-foreground)]">{getSub(item)}</span>}
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[var(--color-secondary)]">
                <div className="h-full rounded-full" style={{ width: `${Math.max(2, v / peak * 100)}%`, background: getColor ? getColor(item) : "var(--color-primary)" }} />
              </div>
            </div>
            <span className="text-right text-xs tabular-nums text-[var(--color-muted-foreground)]">{v.toLocaleString()}</span>
          </Tag>
        )
      })}
    </ol>
  )
}

/* ------------------------------------------------------------------------ */
/* Calendar heatmap (GitHub-style, one year)                                 */
/* ------------------------------------------------------------------------ */

function heatColor(n: number, max: number): string {
  if (n <= 0) return "var(--color-secondary)"
  const t = Math.min(1, Math.log1p(n) / Math.log1p(Math.max(max, 1)))
  const alpha = 0.18 + t * 0.82
  return `color-mix(in oklch, var(--color-success) ${Math.round(alpha * 100)}%, transparent)`
}

export function CalendarHeatmap({ year, days, onSelectDay, selected }: {
  year: number
  days: { day: string; count: number }[]
  onSelectDay?: (day: string, count: number) => void
  selected?: string | null
}) {
  const { weeks, max, monthMarks } = useMemo(() => {
    const byDay = new Map(days.map(d => [d.day, d.count]))
    const start = new Date(year, 0, 1)
    const end = new Date(year, 11, 31)
    // Pad to the Monday on/before Jan 1.
    const first = new Date(start)
    const offset = (first.getDay() + 6) % 7
    first.setDate(first.getDate() - offset)
    const weeks: { day: string; count: number; inYear: boolean }[][] = []
    const monthMarks: { index: number; label: string }[] = []
    const cursor = new Date(first)
    let maxCount = 0
    let lastMonth = -1
    while (cursor <= end) {
      const week: { day: string; count: number; inYear: boolean }[] = []
      for (let i = 0; i < 7; i++) {
        const key = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}-${String(cursor.getDate()).padStart(2, "0")}`
        const inYear = cursor.getFullYear() === year
        const count = inYear ? (byDay.get(key) ?? 0) : 0
        if (count > maxCount) maxCount = count
        week.push({ day: key, count, inYear })
        cursor.setDate(cursor.getDate() + 1)
      }
      // Label a week with its month when the month changes within/at the week start.
      const firstInYear = week.find(d => d.inYear)
      if (firstInYear) {
        const m = Number(firstInYear.day.slice(5, 7)) - 1
        const lastInWeek = Number(week[6].day.slice(5, 7)) - 1
        const marker = m !== lastMonth ? m : (lastInWeek !== lastMonth && week[6].inYear ? lastInWeek : -1)
        if (marker >= 0) {
          monthMarks.push({ index: weeks.length, label: `${marker + 1}月` })
          lastMonth = marker
        }
      }
      weeks.push(week)
    }
    return { weeks, max: maxCount, monthMarks }
  }, [year, days])

  const cell = 12
  const gap = 3
  const width = weeks.length * (cell + gap)
  const height = 7 * (cell + gap) + 18

  return (
    <div className="overflow-x-auto">
      <svg width={width + 28} height={height} className="block" role="img" aria-label={`${year} 年消息日历热图`}>
        {monthMarks.map(m => (
          <text key={m.label + m.index} x={28 + m.index * (cell + gap)} y={10} fontSize={10} fill="var(--color-muted-foreground)">{m.label}</text>
        ))}
        {[1, 3, 5].map(row => (
          <text key={row} x={0} y={18 + row * (cell + gap) + cell - 2} fontSize={10} fill="var(--color-muted-foreground)">
            {["一", "二", "三", "四", "五", "六", "日"][row]}
          </text>
        ))}
        {weeks.map((week, wi) => week.map((d, di) => (
          <rect
            key={d.day}
            x={28 + wi * (cell + gap)}
            y={18 + di * (cell + gap)}
            width={cell}
            height={cell}
            rx={2.5}
            fill={d.inYear ? heatColor(d.count, max) : "transparent"}
            stroke={selected === d.day ? "var(--color-primary)" : "transparent"}
            strokeWidth={1.5}
            className={d.inYear && onSelectDay ? "cursor-pointer" : ""}
            onClick={d.inYear && onSelectDay ? () => onSelectDay(d.day, d.count) : undefined}
          >
            <title>{`${d.day} · ${d.count.toLocaleString()} 条`}</title>
          </rect>
        )))}
      </svg>
      <div className="mt-2 flex items-center justify-end gap-1.5 text-xs text-[var(--color-muted-foreground)]">
        少
        {[0, 0.15, 0.4, 0.7, 1].map(t => (
          <span key={t} className="inline-block h-3 w-3 rounded-sm" style={{ background: t === 0 ? "var(--color-secondary)" : heatColor(Math.round(Math.expm1(t * Math.log1p(Math.max(max, 1)))), max) }} />
        ))}
        多
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------------ */
/* Compact hour sparkline (per member)                                       */
/* ------------------------------------------------------------------------ */

export function HourSpark({ hours, color = "var(--color-info)", className = "" }: { hours: number[]; color?: string; className?: string }) {
  const peak = Math.max(1, ...hours)
  return (
    <div className={`flex h-6 items-end gap-px ${className}`} title="0–23 时活跃分布">
      {hours.map((n, h) => (
        <div key={h} className="flex-1 rounded-[1px]" style={{ height: `${Math.max(n ? 8 : 0, n / peak * 100)}%`, background: color, opacity: n ? 0.85 : 0.15 }} />
      ))}
    </div>
  )
}
