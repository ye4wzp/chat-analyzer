import { useCallback, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"
import { Archive, BookOpen, Check, Clock3, Cpu, Download, FileText, Inbox, Loader2, Pencil, Search, Sparkles, Trash2, X } from "lucide-react"
import { fetchAPI, getErrorMessage, subscribeSSE, type KnowledgeItem, type TaskProgress } from "@/lib/api"
import { formatTime } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

function parseTags(raw: string): string[] {
  try {
    const parsed = JSON.parse(raw || "[]")
    return Array.isArray(parsed) ? parsed.map(String) : []
  } catch {
    return []
  }
}

function daysSince(iso: string): number {
  const t = new Date((iso || "").replace(" ", "T")).getTime()
  return Number.isNaN(t) ? 9999 : (Date.now() - t) / 86400000
}

function MetricCard({ label, value, hint, icon: Icon, tone }: {
  label: string
  value: string
  hint?: string
  icon: typeof BookOpen
  tone: string
}) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
      <div className="mb-3 flex items-start justify-between">
        <p className="text-sm text-[var(--color-muted-foreground)]">{label}</p>
        <span className="flex h-9 w-9 items-center justify-center rounded-lg" style={{ background: `color-mix(in oklch, ${tone} 18%, transparent)`, color: tone }}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <p className="text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">{hint}</p>}
    </div>
  )
}

function KnowledgeDetail({ item, extending, onExtend, onUpdate, onDelete, onSelect }: {
  item: KnowledgeItem | null
  extending: boolean
  onExtend: (id: number) => void
  onUpdate: (id: number, fields: { title?: string; content?: string; tags?: string[] }) => void
  onDelete: (id: number) => void
  onSelect: (item: KnowledgeItem) => void
}) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const [tagsInput, setTagsInput] = useState("")
  const [related, setRelated] = useState<KnowledgeItem[]>([])

  useEffect(() => {
    setEditing(false)
    setTitle(item?.title ?? "")
    setContent(item?.content ?? "")
    setTagsInput(item ? parseTags(item.tags).join(", ") : "")
  }, [item])

  useEffect(() => {
    if (!item) return
    let cancelled = false
    fetchAPI<KnowledgeItem[]>(`/knowledge/${item.id}/related?limit=3`)
      .then(r => { if (!cancelled) setRelated(r) })
      .catch(() => { if (!cancelled) setRelated([]) })
    return () => { cancelled = true }
  }, [item])

  if (!item) {
    return (
      <aside className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-5 text-sm text-[var(--color-muted-foreground)] shadow-sm">
        选择一条知识卡片查看详情
      </aside>
    )
  }

  const tags = parseTags(item.tags)
  const save = () => {
    onUpdate(item.id, {
      title,
      content,
      tags: tagsInput.split(",").map(t => t.trim()).filter(Boolean),
    })
    setEditing(false)
  }

  return (
    <aside className="flex h-full flex-col overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm">
      <div className="flex items-start justify-between gap-3 border-b border-[var(--color-border)] p-5">
        <div className="min-w-0">
          <p className="text-xs text-[var(--color-muted-foreground)]">知识详情</p>
          <h2 className="mt-1 line-clamp-2 text-base font-semibold">{item.title}</h2>
        </div>
        <div className="flex shrink-0 gap-1">
          <button onClick={() => setEditing(true)} className="rounded-md p-1.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)] hover:text-[var(--color-foreground)]">
            <Pencil className="h-4 w-4" />
          </button>
          <button onClick={() => onDelete(item.id)} className="rounded-md p-1.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-destructive-subtle)] hover:text-[var(--color-destructive)]">
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto p-5">
        {editing ? (
          <div className="space-y-3">
            <Input value={title} onChange={e => setTitle(e.target.value)} className="bg-[var(--color-background)] font-medium" />
            <textarea
              value={content}
              onChange={e => setContent(e.target.value)}
              className="min-h-[160px] w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] p-3 text-sm leading-relaxed text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-ring)]"
            />
            <Input value={tagsInput} onChange={e => setTagsInput(e.target.value)} placeholder="标签（逗号分隔）" className="bg-[var(--color-background)]" />
            <div className="flex gap-2">
              <Button size="sm" onClick={save}><Check className="mr-1 h-3.5 w-3.5" />保存</Button>
              <Button size="sm" variant="outline" onClick={() => setEditing(false)}><X className="mr-1 h-3.5 w-3.5" />取消</Button>
            </div>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="rounded-lg bg-[var(--color-card-elevated)] p-3">
                <p className="text-[var(--color-muted-foreground)]">标签数</p>
                <p className="mt-1 text-lg font-semibold text-[var(--color-primary)] tabular-nums">{parseTags(item.tags).length}</p>
              </div>
              <div className="rounded-lg bg-[var(--color-card-elevated)] p-3">
                <p className="text-[var(--color-muted-foreground)]">提取时间</p>
                <p className="mt-1 text-sm font-medium">{formatTime(item.created_at)}</p>
              </div>
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold text-[var(--color-muted-foreground)]">摘要</p>
              <p className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card-elevated)] p-4 text-sm leading-relaxed">{item.content}</p>
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold text-[var(--color-muted-foreground)]">标签</p>
              <div className="flex flex-wrap gap-1.5">
                {tags.length ? tags.map(t => <Badge key={t} variant="outline" className="bg-[var(--color-background)] text-xs">{t}</Badge>) : <span className="text-xs text-[var(--color-muted-foreground)]">暂无标签</span>}
              </div>
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold text-[var(--color-muted-foreground)]">来源群聊</p>
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] p-3 text-sm">
                <p className="font-medium">{item.source_chat || "未知来源"}</p>
                <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">消息 ID {item.source_message_ids}</p>
              </div>
            </div>

            {item.extended_content ? (
              <div>
                <p className="mb-2 text-xs font-semibold text-[var(--color-muted-foreground)]">AI 扩展信息</p>
                <div className="whitespace-pre-wrap rounded-lg bg-[var(--color-primary-subtle)] p-4 text-sm leading-relaxed text-[var(--color-foreground)]">{item.extended_content}</div>
              </div>
            ) : (
              <Button variant="outline" size="sm" onClick={() => onExtend(item.id)} disabled={extending}>
                {extending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Sparkles className="mr-1.5 h-3.5 w-3.5" />}
                用 AI 扩展知识
              </Button>
            )}

            {related.length > 0 && (
              <div>
                <p className="mb-2 text-xs font-semibold text-[var(--color-muted-foreground)]">相关来源消息</p>
                <div className="space-y-2">
                  {related.map(r => (
                    <button
                      key={r.id}
                      onClick={() => onSelect(r)}
                      className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] p-3 text-left hover:bg-[var(--color-card-elevated)]"
                    >
                      <div className="flex items-center gap-2">
                        <span className="min-w-0 flex-1 truncate text-sm font-medium">{r.title}</span>
                        {r.similarity !== undefined && <span className="text-xs text-[var(--color-success)]">{(r.similarity * 100).toFixed(0)}%</span>}
                      </div>
                      <p className="mt-1 line-clamp-1 text-xs text-[var(--color-muted-foreground)]">{r.content}</p>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  )
}

export default function Knowledge() {
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [selected, setSelected] = useState<KnowledgeItem | null>(null)
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState("")
  const [activeTag, setActiveTag] = useState("")
  const [extending, setExtending] = useState<number | null>(null)
  const [mode, setMode] = useState<"keyword" | "semantic">("keyword")
  const [embedStatus, setEmbedStatus] = useState<{ model: string; total: number; indexed: number; stale: number } | null>(null)
  const [embedding, setEmbedding] = useState(false)

  const load = useCallback(async (query = "", searchMode: "keyword" | "semantic" = mode) => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (query) params.set("q", query)
      if (query && searchMode === "semantic") params.set("mode", "semantic")
      const data = await fetchAPI<KnowledgeItem[]>(`/knowledge?${params}`)
      setItems(data)
      setSelected(prev => {
        if (prev && data.some(i => i.id === prev.id)) return data.find(i => i.id === prev.id) ?? prev
        return data[0] ?? null
      })
    } finally {
      setLoading(false)
    }
  }, [mode])

  const refreshEmbedStatus = async () => {
    try {
      setEmbedStatus(await fetchAPI("/knowledge/embed/status"))
    } catch { /* non-critical */ }
  }

  useEffect(() => {
    void load()
    void refreshEmbedStatus()
  }, [load])

  const allTags = useMemo(() => Array.from(new Set(items.flatMap(i => parseTags(i.tags)))), [items])
  const filtered = activeTag ? items.filter(i => parseTags(i.tags).includes(activeTag)) : items
  const todayCount = items.filter(i => daysSince(i.created_at) < 1).length
  const weekCount = items.filter(i => daysSince(i.created_at) <= 7).length

  const extend = async (id: number) => {
    setExtending(id)
    try {
      const res = await fetchAPI<{ extended_content: string }>(`/knowledge/${id}/extend`, { method: "POST" })
      setItems(prev => prev.map(i => i.id === id ? { ...i, extended_content: res.extended_content } : i))
      setSelected(prev => prev?.id === id ? { ...prev, extended_content: res.extended_content } : prev)
    } finally {
      setExtending(null)
    }
  }

  const update = async (id: number, fields: { title?: string; content?: string; tags?: string[] }) => {
    await fetchAPI(`/knowledge/${id}`, { method: "PATCH", body: JSON.stringify(fields) })
    const patch = { ...fields, tags: fields.tags ? JSON.stringify(fields.tags) : undefined }
    setItems(prev => prev.map(i => i.id === id ? { ...i, ...patch, tags: patch.tags ?? i.tags } : i))
    setSelected(prev => prev?.id === id ? { ...prev, ...patch, tags: patch.tags ?? prev.tags } : prev)
  }

  const remove = async (id: number) => {
    await fetchAPI(`/knowledge/${id}`, { method: "DELETE" })
    setItems(prev => {
      const next = prev.filter(i => i.id !== id)
      setSelected(next[0] ?? null)
      return next
    })
  }

  const exportMd = () => {
    window.open("/api/knowledge/export?fmt=markdown", "_blank")
  }

  const startEmbed = async () => {
    if (embedding) return
    setEmbedding(true)
    const id = toast.loading("启动 embedding...")
    try {
      const { task_id } = await fetchAPI<{ task_id: string }>("/knowledge/embed", { method: "POST" })
      const es = subscribeSSE<TaskProgress>(`/tasks/${task_id}/events`, (data) => {
        toast.loading(data.message || data.status, { id })
        if (data.status === "done") {
          es.close()
          toast.success(data.message || "完成", { id })
          setEmbedding(false)
          void refreshEmbedStatus()
        } else if (data.status === "error") {
          es.close()
          toast.error(data.message || "失败", { id })
          setEmbedding(false)
        }
      }, () => { toast.dismiss(id); setEmbedding(false) })
    } catch (e) {
      toast.error(getErrorMessage(e), { id })
      setEmbedding(false)
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[var(--color-background)]">
      <header className="shrink-0 border-b border-[var(--color-border)] bg-[var(--color-background)]/75 px-5 py-5 backdrop-blur md:px-7">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <BookOpen className="h-6 w-6" />
              <h1 className="text-2xl font-semibold tracking-tight">知识库</h1>
            </div>
            <div className="mt-4 flex gap-6 text-sm">
              <button className="border-b-2 border-[var(--color-primary)] pb-2 font-medium text-[var(--color-primary)]">知识卡片</button>
              <button className="pb-2 text-[var(--color-muted-foreground)]">标签</button>
              <button className="pb-2 text-[var(--color-muted-foreground)]">来源消息</button>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <div className="relative w-[280px]">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-[var(--color-muted-foreground)]" />
              <Input
                className="h-10 rounded-lg bg-[var(--color-background)] pl-9"
                placeholder={mode === "semantic" ? "语义搜索（自然语言）" : "搜索知识库"}
                value={q}
                onChange={e => setQ(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter") void load(q)
                }}
              />
            </div>
            <div className="flex overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] text-xs shadow-sm">
              <button onClick={() => setMode("keyword")} className={`px-3 ${mode === "keyword" ? "bg-[var(--color-accent)] text-[var(--color-primary)]" : "text-[var(--color-muted-foreground)]"}`}>关键词</button>
              <button onClick={() => setMode("semantic")} disabled={!embedStatus?.model} className={`px-3 disabled:opacity-40 ${mode === "semantic" ? "bg-[var(--color-accent)] text-[var(--color-primary)]" : "text-[var(--color-muted-foreground)]"}`}>语义</button>
            </div>
            <Button variant="outline" size="sm" onClick={() => void load(q)}>搜索</Button>
            <Button variant="outline" size="sm" onClick={exportMd}><Download className="mr-1.5 h-3.5 w-3.5" />导出</Button>
            {embedStatus?.model && (
              <Button
                variant="outline"
                size="sm"
                onClick={startEmbed}
                disabled={embedding || (embedStatus.indexed === embedStatus.total && embedStatus.stale === 0)}
              >
                {embedding ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Cpu className="mr-1.5 h-3.5 w-3.5" />}
                {embedStatus.indexed}/{embedStatus.total}
              </Button>
            )}
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-4 xl:grid-cols-5">
          <MetricCard label="今日新增" value={String(todayCount)} icon={FileText} tone="var(--color-success)" />
          <MetricCard label="本周新增" value={String(weekCount)} icon={Clock3} tone="var(--color-warning)" />
          <MetricCard label="标签数" value={String(allTags.length)} icon={Archive} tone="var(--chart-6)" />
          <MetricCard label="知识总数" value={items.length.toLocaleString()} icon={BookOpen} tone="var(--color-primary)" />
          <div className="hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm xl:block">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm text-[var(--color-muted-foreground)]">热门标签</p>
              <button className="text-xs text-[var(--color-muted-foreground)]">更多</button>
            </div>
            <div className="flex flex-wrap gap-2">
              {allTags.slice(0, 6).map(tag => (
                <button key={tag} onClick={() => setActiveTag(tag === activeTag ? "" : tag)} className={`rounded-full px-2.5 py-1 text-xs ${tag === activeTag ? "bg-[var(--color-primary)] text-white" : "bg-[var(--color-secondary)] text-[var(--color-muted-foreground)]"}`}>{tag}</button>
              ))}
            </div>
          </div>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 gap-4 p-4 md:grid-cols-[220px_1fr] md:p-5 xl:grid-cols-[240px_1fr_370px]">
        <aside className="hidden min-h-0 flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm md:flex">
          <div className="flex items-center justify-between border-b border-[var(--color-border)] p-4">
            <h2 className="text-sm font-semibold">最近知识点</h2>
            <span className="rounded-full bg-[var(--color-primary-subtle)] px-2 py-0.5 text-xs text-[var(--color-primary)]" title="本周新增">{weekCount}</span>
          </div>
          <div className="flex-1 overflow-y-auto p-3">
            <div className="space-y-2">
              {filtered.slice(0, 9).map(item => (
                <button
                  key={item.id}
                  onClick={() => setSelected(item)}
                  className={`w-full rounded-lg p-3 text-left text-sm transition-colors ${selected?.id === item.id ? "bg-[var(--color-primary-subtle)] ring-1 ring-[var(--color-primary)]/40" : "hover:bg-[var(--color-card-elevated)]"}`}
                >
                  <div className="flex items-start gap-2">
                    <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${daysSince(item.created_at) < 1 ? "bg-[var(--color-success)]" : daysSince(item.created_at) <= 7 ? "bg-[var(--color-primary)]" : "bg-[var(--color-text-faint)]"}`} />
                    <div className="min-w-0">
                      <p className="truncate font-medium">{item.title}</p>
                      <p className="mt-1 truncate text-xs text-[var(--color-muted-foreground)]"># {parseTags(item.tags)[0] || "未分类"} · {formatTime(item.created_at)}</p>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </div>
          <button onClick={() => setActiveTag("")} className="border-t border-[var(--color-border)] px-4 py-3 text-left text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-primary)]">
            查看全部提醒
          </button>
        </aside>

        <main className="min-h-0 overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border)] p-3">
            <div className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
              <Inbox className="h-4 w-4" />
              共 <span className="font-medium text-[var(--color-foreground)] tabular-nums">{filtered.length}</span> 条知识点
              {activeTag && <span className="rounded-full bg-[var(--color-primary-subtle)] px-2 py-0.5 text-xs text-[var(--color-primary)]"># {activeTag}</span>}
            </div>
            {activeTag && (
              <Button variant="outline" size="sm" onClick={() => setActiveTag("")}><X className="mr-1.5 h-3.5 w-3.5" />清除筛选</Button>
            )}
          </div>
          <div className="grid grid-cols-[36px_minmax(220px,1fr)_170px_170px_84px] border-b border-[var(--color-border)] bg-[var(--color-card-elevated)] px-4 py-3 text-xs font-medium text-[var(--color-muted-foreground)] max-lg:hidden">
            <span />
            <span>标题 / 摘要</span>
            <span>标签</span>
            <span>来源群聊</span>
            <span className="text-right">时间</span>
          </div>
          <div className="h-full overflow-y-auto pb-16">
            {loading ? (
              <div className="space-y-2 p-4">
                {[...Array(8)].map((_, i) => <div key={i} className="h-14 animate-pulse rounded-lg bg-[var(--color-secondary)]" />)}
              </div>
            ) : filtered.length === 0 ? (
              <p className="py-16 text-center text-sm text-[var(--color-muted-foreground)]">暂无知识点。运行分析后筛选保存。</p>
            ) : filtered.map(item => {
              const tags = parseTags(item.tags)
              const selectedRow = selected?.id === item.id
              return (
                <button
                  key={item.id}
                  onClick={() => setSelected(item)}
                  className={`grid w-full grid-cols-[36px_minmax(220px,1fr)_170px_170px_84px] items-center border-b border-[var(--color-border)] px-4 py-3 text-left transition-colors max-lg:block max-lg:space-y-2 ${selectedRow ? "bg-[var(--color-primary-subtle)] ring-1 ring-inset ring-[var(--color-primary)]/40" : "hover:bg-[var(--color-card-elevated)]"}`}
                >
                  <span className="flex h-5 w-5 items-center justify-center rounded border border-[var(--color-border)] bg-[var(--color-background)] max-lg:hidden" />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">{item.title}</span>
                    <span className="mt-1 block line-clamp-1 text-xs text-[var(--color-muted-foreground)]">{item.content}</span>
                  </span>
                  <span className="flex min-w-0 flex-wrap gap-1.5 max-lg:mt-2">
                    {tags.slice(0, 2).map(tag => <Badge key={tag} variant="outline" className="bg-[var(--color-background)] text-xs">{tag}</Badge>)}
                  </span>
                  <span className="truncate text-xs text-[var(--color-muted-foreground)] max-lg:block">{item.source_chat || "未知来源"}</span>
                  <span className="text-right text-xs text-[var(--color-muted-foreground)] tabular-nums max-lg:inline-block">
                    {formatTime(item.created_at)}
                  </span>
                </button>
              )
            })}
          </div>
        </main>

        <div className="hidden min-h-0 xl:block">
          <KnowledgeDetail
            item={selected}
            extending={!!selected && extending === selected.id}
            onExtend={extend}
            onUpdate={update}
            onDelete={remove}
            onSelect={setSelected}
          />
        </div>
      </div>
    </div>
  )
}
