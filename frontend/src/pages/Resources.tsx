import { useCallback, useEffect, useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { Copy, Download, ExternalLink, FileText, Link2, Loader2, Package, Search } from "lucide-react"
import { toast } from "sonner"
import { fetchAPI, getErrorMessage, type ResourceItem, type ResourcesResponse } from "@/lib/api"
import { PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/constants"
import { formatTime } from "@/lib/utils"
import { useOnStatsDone, useStatsFilters } from "@/hooks/useStatsFilters"
import { StatsFilterBar } from "@/components/StatsToolbar"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"

const KIND_TABS: { key: string; label: string; icon: typeof Link2 }[] = [
  { key: "link", label: "链接", icon: Link2 },
  { key: "file", label: "文件", icon: FileText },
  { key: "miniprogram", label: "小程序", icon: Package },
]

function hostOf(url: string | null): string {
  if (!url) return ""
  try { return new URL(url).hostname.replace(/^www\./, "") } catch { return "" }
}

export default function Resources() {
  const navigate = useNavigate()
  const filters = useStatsFilters()
  const [params, setParams] = useSearchParams()
  const [items, setItems] = useState<ResourceItem[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [chats, setChats] = useState<{ platform: string; chat_id: string; chat_name: string | null; n: number }[]>([])
  const [q, setQ] = useState("")
  const [debouncedQ, setDebouncedQ] = useState("")
  const [since, setSince] = useState("")
  const [until, setUntil] = useState("")

  const kind = params.get("kind") || "link"
  const chatId = params.get("chat_id") || ""

  useEffect(() => { const t = window.setTimeout(() => setDebouncedQ(q.trim()), 300); return () => window.clearTimeout(t) }, [q])

  const baseQuery = useCallback(() => {
    const p = new URLSearchParams()
    if (filters.platform) p.set("platform", filters.platform)
    if (filters.account) p.set("account", filters.account)
    if (chatId) p.set("chat_id", chatId)
    if (debouncedQ) p.set("q", debouncedQ)
    if (since) p.set("since", since)
    if (until) p.set("until", until)
    return p
  }, [filters.platform, filters.account, chatId, debouncedQ, since, until])

  const load = useCallback((append = false, offset = 0) => {
    setLoading(true)
    const p = baseQuery()
    p.set("kind", kind)
    p.set("limit", "50")
    p.set("offset", String(offset))
    fetchAPI<ResourcesResponse>(`/resources?${p}`)
      .then(d => {
        setItems(prev => append ? [...prev, ...d.items] : d.items)
        setCounts(d.counts)
        setHasMore(d.has_more)
      })
      .catch(e => toast.error(getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [baseQuery, kind])

  useEffect(() => { load() }, [load])
  useOnStatsDone(useCallback(() => load(), [load]))

  useEffect(() => {
    const p = new URLSearchParams({ kind })
    if (filters.platform) p.set("platform", filters.platform)
    fetchAPI<typeof chats>(`/resources/chats?${p}`).then(setChats).catch(() => setChats([]))
  }, [kind, filters.platform])

  const setKind = (k: string) => setParams(prev => { const p = new URLSearchParams(prev); p.set("kind", k); return p }, { replace: true })
  const setChat = (v: string) => setParams(prev => {
    const p = new URLSearchParams(prev)
    if (v) { const [pl, ...rest] = v.split("|"); p.set("platform", pl); p.set("chat_id", rest.join("|")) } else p.delete("chat_id")
    return p
  }, { replace: true })

  const exportMarkdown = () => {
    const lines = items.map(r => {
      const title = r.title || r.url || "(无标题)"
      const head = r.url ? `[${title}](${r.url})` : title
      return `- ${head} — ${r.chat_name || ""} · ${r.sender_name || ""} · ${r.timestamp.slice(0, 16).replace("T", " ")}`
    })
    const blob = new Blob([`# ${KIND_TABS.find(t => t.key === kind)?.label || kind}汇总\n\n${lines.join("\n")}\n`], { type: "text/markdown;charset=utf-8" })
    const a = document.createElement("a")
    a.href = URL.createObjectURL(blob)
    a.download = `resources-${kind}-${new Date().toISOString().slice(0, 10)}.md`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const copyAll = async () => {
    const text = items.map(r => r.url || r.title || "").filter(Boolean).join("\n")
    try { await navigator.clipboard.writeText(text); toast.success(`已复制 ${items.length} 条`) } catch { toast.error("复制失败") }
  }

  const grouped = useMemo(() => {
    const map = new Map<string, ResourceItem[]>()
    for (const r of items) {
      const day = r.timestamp.slice(0, 10)
      if (!map.has(day)) map.set(day, [])
      map.get(day)!.push(r)
    }
    return Array.from(map.entries())
  }, [items])

  return (
    <div className="mx-auto w-full max-w-[1200px] space-y-5 p-5 md:p-7">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">资源汇总</h1>
          <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">聊天里出现过的链接、文件和小程序，按时间倒序</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatsFilterBar filters={filters}>
            <Select value={chatId ? `${params.get("platform")}|${chatId}` : ""} onChange={e => setChat(e.target.value)} className="h-9 w-56 rounded-lg bg-[var(--color-card)] text-sm">
              <option value="">全部聊天</option>
              {chats.map(c => <option key={`${c.platform}|${c.chat_id}`} value={`${c.platform}|${c.chat_id}`}>{c.chat_name || c.chat_id} · {c.n}</option>)}
            </Select>
          </StatsFilterBar>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex items-center gap-1 rounded-lg bg-[var(--color-muted)] p-1">
          {KIND_TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setKind(t.key)}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors ${kind === t.key ? "bg-[var(--color-card-elevated)] font-medium shadow-sm" : "text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"}`}
            >
              <t.icon className="h-3.5 w-3.5" />{t.label}
              <span className="tabular-nums text-xs text-[var(--color-muted-foreground)]">{(counts[t.key] ?? 0).toLocaleString()}</span>
            </button>
          ))}
        </div>
        <div className="relative">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-[var(--color-muted-foreground)]" />
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="搜标题 / 网址 / 发送人" className="h-9 w-64 rounded-lg pl-9" />
        </div>
        <Input type="date" value={since} onChange={e => setSince(e.target.value)} className="h-9 w-36 rounded-lg text-xs" />
        <span className="text-xs text-[var(--color-muted-foreground)]">~</span>
        <Input type="date" value={until} onChange={e => setUntil(e.target.value)} className="h-9 w-36 rounded-lg text-xs" />
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant="outline" onClick={copyAll} disabled={!items.length}><Copy className="mr-1.5 h-3.5 w-3.5" />复制</Button>
          <Button size="sm" variant="outline" onClick={exportMarkdown} disabled={!items.length}><Download className="mr-1.5 h-3.5 w-3.5" />导出 Markdown</Button>
        </div>
      </div>

      {loading && items.length === 0 ? (
        <div className="flex items-center justify-center py-20"><Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" /></div>
      ) : items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--color-border)] py-16 text-center text-sm text-[var(--color-muted-foreground)]">没有匹配的资源</div>
      ) : (
        <div className="space-y-5">
          {grouped.map(([day, rows]) => (
            <section key={day}>
              <h2 className="mb-2 text-xs font-medium text-[var(--color-muted-foreground)]">{day} · {rows.length} 条</h2>
              <ul className="overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]">
                {rows.map(r => {
                  const host = hostOf(r.url)
                  return (
                    <li key={r.id} className="flex items-start gap-3 border-b border-[var(--color-border)]/60 px-4 py-3 last:border-0 hover:bg-[var(--color-card-elevated)]">
                      <span className="mt-1 h-2 w-2 shrink-0 rounded-full" style={{ background: PLATFORM_COLOR[r.platform] || "#94a3b8" }} title={PLATFORM_LABEL[r.platform] || r.platform} />
                      <div className="min-w-0 flex-1">
                        {r.url ? (
                          <a href={r.url} target="_blank" rel="noreferrer noopener" className="group inline-flex max-w-full items-center gap-1 text-sm font-medium hover:text-[var(--color-primary)]">
                            <span className="truncate">{r.title || r.url}</span>
                            <ExternalLink className="h-3 w-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-100" />
                          </a>
                        ) : (
                          <p className="truncate text-sm font-medium">{r.title || "(无标题)"}</p>
                        )}
                        <p className="mt-0.5 truncate text-xs text-[var(--color-muted-foreground)]">
                          {host && <span className="mr-2 rounded bg-[var(--color-secondary)] px-1.5 py-0.5 font-mono text-[10px]">{host}</span>}
                          <button onClick={() => navigate(`/chats?platform=${r.platform}&chat_id=${encodeURIComponent(r.chat_id)}`)} className="hover:text-[var(--color-primary)]">{r.chat_name || r.chat_id}</button>
                          {r.sender_name && <> · {r.sender_name}</>}
                        </p>
                      </div>
                      <span className="shrink-0 text-xs tabular-nums text-[var(--color-muted-foreground)]">{formatTime(r.timestamp).split(" ").pop()?.slice(0, 5)}</span>
                    </li>
                  )
                })}
              </ul>
            </section>
          ))}
          {hasMore && (
            <div className="flex justify-center">
              <Button variant="outline" size="sm" disabled={loading} onClick={() => load(true, items.length)}>
                {loading && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}加载更多
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
