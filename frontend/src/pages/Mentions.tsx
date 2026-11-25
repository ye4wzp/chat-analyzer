import { useCallback, useEffect, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { AtSign, Loader2, Settings as SettingsIcon, X } from "lucide-react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, type Message } from "@/lib/api"
import { PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/constants"
import { formatTime } from "@/lib/utils"
import { useStatsFilters } from "@/hooks/useStatsFilters"
import { StatsFilterBar } from "@/components/StatsToolbar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

interface MentionsResponse {
  items: Message[]
  has_more: boolean
  names: { configured: string[]; inferred: Record<string, { sender_name: string | null; chats: number }>; names: string[] }
  by_chat: { platform: string; chat_id: string; chat_name: string | null; n: number; last_ts: string }[]
  total: number
}

function Highlight({ text, names }: { text: string; names: string[] }) {
  if (!names.length) return <>{text}</>
  const pattern = new RegExp(`(@(?:${names.map(n => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")}))`, "g")
  // split() with a capturing group interleaves matches at odd indices.
  const parts = text.split(pattern)
  return (
    <>
      {parts.map((p, i) => i % 2 === 1
        ? <mark key={i} className="rounded bg-[var(--color-warning-subtle)] px-0.5 text-[var(--color-warning)]">{p}</mark>
        : <span key={i}>{p}</span>)}
    </>
  )
}

export default function Mentions() {
  const navigate = useNavigate()
  const filters = useStatsFilters()
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState<MentionsResponse | null>(null)
  const [items, setItems] = useState<Message[]>([])
  const [loading, setLoading] = useState(true)
  const [includeAll, setIncludeAll] = useState(false)
  const [q, setQ] = useState("")
  const [debouncedQ, setDebouncedQ] = useState("")
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const chatId = params.get("chat_id") || ""

  useEffect(() => { const t = window.setTimeout(() => setDebouncedQ(q.trim()), 300); return () => window.clearTimeout(t) }, [q])

  const load = useCallback((append = false, offset = 0) => {
    setLoading(true)
    const p = new URLSearchParams({ limit: "50", offset: String(offset) })
    if (filters.platform) p.set("platform", filters.platform)
    if (chatId) p.set("chat_id", chatId)
    if (includeAll) p.set("include_all", "true")
    if (debouncedQ) p.set("q", debouncedQ)
    fetchAPI<MentionsResponse>(`/mentions?${p}`)
      .then(d => { setData(d); setItems(prev => append ? [...prev, ...d.items] : d.items) })
      .catch(e => toast.error(getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [filters.platform, chatId, includeAll, debouncedQ])

  useEffect(() => { load() }, [load])

  const setChat = (platform: string, id: string) => setParams(prev => {
    const p = new URLSearchParams(prev)
    if (id) { p.set("platform", platform); p.set("chat_id", id) } else p.delete("chat_id")
    return p
  }, { replace: true })

  const names = data?.names.names ?? []

  return (
    <div className="mx-auto w-full max-w-[1200px] space-y-5 p-5 md:p-7">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight"><AtSign className="h-6 w-6 text-[var(--color-warning)]" />@消息</h1>
          <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
            群里 @ 你的消息。识别为：
            {names.length ? names.map(n => <span key={n} className="mx-0.5 rounded bg-[var(--color-secondary)] px-1.5 py-0.5 text-xs">@{n}</span>) : <span className="mx-1 text-[var(--color-warning)]">尚未识别出你的昵称</span>}
            <button onClick={() => navigate("/settings?tab=general")} className="ml-1 inline-flex items-center gap-1 text-xs text-[var(--color-primary)]"><SettingsIcon className="h-3 w-3" />设置昵称</button>
          </p>
        </div>
        <StatsFilterBar filters={filters} showAccount={false}>
          <label className="flex items-center gap-1.5 text-xs text-[var(--color-muted-foreground)]">
            <input type="checkbox" checked={includeAll} onChange={e => setIncludeAll(e.target.checked)} className="accent-[var(--color-primary)]" />
            包含 @所有人
          </label>
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="搜内容" className="h-9 w-44 rounded-lg" />
        </StatsFilterBar>
      </header>

      <div className="grid gap-4 xl:grid-cols-[280px_1fr]">
        <aside className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">按群</h2>
            <span className="text-xs tabular-nums text-[var(--color-muted-foreground)]">共 {data?.total ?? 0} 条</span>
          </div>
          <ul className="max-h-[70vh] space-y-0.5 overflow-y-auto">
            <li>
              <button onClick={() => setChat("", "")} className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-sm ${!chatId ? "bg-[var(--color-primary-subtle)] text-[var(--color-primary)]" : "hover:bg-[var(--color-card-elevated)]"}`}>
                <span>全部</span>
              </button>
            </li>
            {(data?.by_chat ?? []).map(c => (
              <li key={`${c.platform}-${c.chat_id}`}>
                <button onClick={() => setChat(c.platform, c.chat_id)} className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${chatId === c.chat_id ? "bg-[var(--color-primary-subtle)] text-[var(--color-primary)]" : "hover:bg-[var(--color-card-elevated)]"}`}>
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: PLATFORM_COLOR[c.platform] || "#94a3b8" }} />
                  <span className="min-w-0 flex-1 break-words font-medium">{c.chat_name || c.chat_id}</span>
                  <span className="text-xs tabular-nums text-[var(--color-muted-foreground)]">{c.n}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <main className="min-w-0">
          {chatId && (
            <div className="mb-3 flex items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
              筛选：{data?.by_chat.find(c => c.chat_id === chatId)?.chat_name || chatId}
              <button onClick={() => setChat("", "")} className="rounded p-0.5 hover:bg-[var(--color-accent)]"><X className="h-3.5 w-3.5" /></button>
            </div>
          )}
          {loading && items.length === 0 ? (
            <div className="flex items-center justify-center py-20"><Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" /></div>
          ) : items.length === 0 ? (
            <div className="rounded-xl border border-dashed border-[var(--color-border)] py-16 text-center text-sm text-[var(--color-muted-foreground)]">
              {names.length ? "没有找到 @ 你的消息" : "先在设置里填写你的昵称，或等统计构建后自动推断"}
            </div>
          ) : (
            <ul className="space-y-2">
              {items.map(m => (
                <li key={m.id} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
                  <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs text-[var(--color-muted-foreground)]">
                    <button onClick={() => navigate(`/chats?platform=${m.platform}&chat_id=${encodeURIComponent(m.chat_id)}`)} className="font-medium text-[var(--color-foreground)] hover:text-[var(--color-primary)]">{m.chat_name}</button>
                    <span className="rounded bg-[var(--color-success-subtle)] px-1.5 py-0.5 text-xs text-[var(--color-success)]">{PLATFORM_LABEL[m.platform] || m.platform}</span>
                    <span>{m.sender_name || "?"}</span>
                    <span className="ml-auto tabular-nums">{formatTime(m.timestamp)}</span>
                  </div>
                  <p
                    onClick={() => setExpanded(prev => { const n = new Set(prev); if (n.has(m.id)) n.delete(m.id); else n.add(m.id); return n })}
                    className={`cursor-pointer whitespace-pre-wrap text-sm leading-relaxed ${expanded.has(m.id) ? "" : "line-clamp-4"}`}
                    title={expanded.has(m.id) ? "点击收起" : "点击展开"}
                  >
                    <Highlight text={m.content || ""} names={includeAll ? [...names, "所有人", "全体成员", "everyone", "all"] : names} />
                  </p>
                </li>
              ))}
              {data?.has_more && (
                <li className="flex justify-center py-2">
                  <Button variant="outline" size="sm" disabled={loading} onClick={() => load(true, items.length)}>
                    {loading && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}加载更多
                  </Button>
                </li>
              )}
            </ul>
          )}
        </main>
      </div>
    </div>
  )
}
