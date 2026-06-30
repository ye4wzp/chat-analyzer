import { useCallback, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"
import { Check, Lightbulb, Loader2, Plus, Sparkles, Star, Tags as TagsIcon, Trash2, Users, Wand2, X } from "lucide-react"
import {
  fetchAPI, getErrorMessage, subscribeSSE,
  type ChatInfo, type ContactTag, type ContactTagEntry, type ContactTagLink,
  type TagSuggestion, type TaskProgress,
} from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog } from "@/components/ui/dialog"
import { PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/constants"

const PALETTE = ["#22c55e", "#3b82f6", "#f97316", "#a855f7", "#ec4899", "#14b8a6", "#eab308", "#ef4444"]
const colorFor = (color: string | null, seed: number) => color || PALETTE[Math.abs(seed) % PALETTE.length]
const contactKey = (platform: string, chatId: string) => `${platform}|${chatId}`

function PlatformAvatar({ platform, name }: { platform: string; name: string }) {
  const label = platform === "wechat" ? "微" : platform === "qq" ? "Q" : platform === "telegram" ? "T" : (name || "?").slice(0, 1)
  return (
    <span
      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white shadow-sm"
      style={{ background: PLATFORM_COLOR[platform] || "#64748b" }}
    >
      {label}
    </span>
  )
}

function TagChip({ name, color, seed, onRemove }: { name: string; color: string | null; seed: number; onRemove?: () => void }) {
  const c = colorFor(color, seed)
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium"
      style={{ background: `${c}1a`, color: c }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: c }} />
      {name}
      {onRemove && (
        <button onClick={onRemove} className="ml-0.5 rounded-full p-0.5 hover:bg-black/10" aria-label="移除标签">
          <X className="h-3 w-3" />
        </button>
      )}
    </span>
  )
}

/* ---------- per-contact tag management dialog ---------- */

function ContactTagsDialog({ platform, chatId, name, activeTags, onClose, onChanged }: {
  platform: string
  chatId: string
  name: string
  activeTags: ContactTag[]
  onClose: () => void
  onChanged: () => void
}) {
  const [links, setLinks] = useState<ContactTagLink[]>([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState("")
  const [suggesting, setSuggesting] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      setLinks(await fetchAPI<ContactTagLink[]>(`/contacts/${platform}/${encodeURIComponent(chatId)}/tags`))
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [platform, chatId])

  useEffect(() => { void reload() }, [reload])

  const mutate = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
      await reload()
      onChanged()
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  const addTag = async () => {
    const n = adding.trim()
    if (!n) return
    setAdding("")
    await mutate(() => fetchAPI(`/contacts/${platform}/${encodeURIComponent(chatId)}/tags`, {
      method: "POST", body: JSON.stringify({ name: n }),
    }))
  }

  const removeTag = (tagId: number) => mutate(() =>
    fetchAPI(`/contacts/${platform}/${encodeURIComponent(chatId)}/tags/${tagId}`, { method: "DELETE" }))

  const confirmLink = (linkId: number) => mutate(() =>
    fetchAPI("/tags/confirm", { method: "POST", body: JSON.stringify({ link_ids: [linkId] }) }))

  const rejectLink = (linkId: number) => mutate(() =>
    fetchAPI("/tags/reject", { method: "POST", body: JSON.stringify({ link_ids: [linkId] }) }))

  const aiSuggest = async () => {
    setSuggesting(true)
    const id = toast.loading("AI 分析聊天记录中...")
    try {
      const res = await fetchAPI<{ suggestions: { name: string }[] }>(
        `/contacts/${platform}/${encodeURIComponent(chatId)}/tags/suggest`, { method: "POST", body: JSON.stringify({}) },
      )
      toast.success(res.suggestions.length ? `AI 建议了 ${res.suggestions.length} 个标签，待确认` : "AI 没有给出标签建议", { id })
      await reload()
      onChanged()
    } catch (e) {
      toast.error(getErrorMessage(e), { id })
    } finally {
      setSuggesting(false)
    }
  }

  const confirmed = links.filter(l => l.status === "confirmed")
  const suggested = links.filter(l => l.status === "suggested")

  return (
    <Dialog open onClose={onClose} title={`管理标签 · ${name || chatId}`} size="md">
      <div className="space-y-5">
        <div>
          <p className="mb-2 text-xs font-semibold text-[var(--color-muted-foreground)]">当前标签</p>
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
          ) : confirmed.length ? (
            <div className="flex flex-wrap gap-1.5">
              {confirmed.map(l => (
                <TagChip key={l.tag_id} name={l.name} color={l.color} seed={l.tag_id} onRemove={() => void removeTag(l.tag_id)} />
              ))}
            </div>
          ) : (
            <p className="text-xs text-[var(--color-muted-foreground)]">暂无标签</p>
          )}
        </div>

        {suggested.length > 0 && (
          <div>
            <p className="mb-2 text-xs font-semibold text-[var(--color-muted-foreground)]">AI 建议（待确认）</p>
            <div className="space-y-1.5">
              {suggested.map(l => (
                <div key={l.tag_id} className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card-elevated)] px-3 py-2">
                  <TagChip name={l.name} color={l.color} seed={l.tag_id} />
                  {l.confidence != null && <span className="text-xs text-[var(--color-muted-foreground)] tabular-nums">{Math.round(l.confidence * 100)}%</span>}
                  <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-muted-foreground)]">{l.reason}</span>
                  <button onClick={() => void confirmLink(l.link_id)} className="rounded-md p-1 text-[var(--color-success)] hover:bg-[var(--color-success-subtle)]" aria-label="确认"><Check className="h-4 w-4" /></button>
                  <button onClick={() => void rejectLink(l.link_id)} className="rounded-md p-1 text-[var(--color-destructive)] hover:bg-[var(--color-destructive-subtle)]" aria-label="拒绝"><X className="h-4 w-4" /></button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <p className="mb-2 text-xs font-semibold text-[var(--color-muted-foreground)]">添加标签</p>
          <div className="flex gap-2">
            <Input
              list="active-tags-list"
              value={adding}
              onChange={e => setAdding(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") void addTag() }}
              placeholder="输入或选择标签名"
              className="bg-[var(--color-background)]"
            />
            <datalist id="active-tags-list">
              {activeTags.map(t => <option key={t.id} value={t.name} />)}
            </datalist>
            <Button size="sm" onClick={() => void addTag()} disabled={!adding.trim()}><Plus className="h-4 w-4" /></Button>
          </div>
        </div>

        <div className="flex justify-between border-t border-[var(--color-border)] pt-4">
          <Button variant="outline" size="sm" onClick={() => void aiSuggest()} disabled={suggesting}>
            {suggesting ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Wand2 className="mr-1.5 h-3.5 w-3.5" />}
            AI 打标签
          </Button>
          <Button variant="ghost" size="sm" onClick={onClose}>完成</Button>
        </div>
      </div>
    </Dialog>
  )
}

/* ---------- review view ---------- */

function ReviewView({ suggestions, onAct }: {
  suggestions: TagSuggestion[]
  onAct: (action: "confirm" | "reject", linkIds: number[]) => Promise<void>
}) {
  const [selected, setSelected] = useState<Set<number>>(() => new Set(suggestions.map(s => s.link_id)))
  useEffect(() => { setSelected(new Set(suggestions.map(s => s.link_id))) }, [suggestions])

  const toggle = (id: number) => setSelected(prev => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })

  const groups = useMemo(() => {
    const m = new Map<string, { name: string; platform: string; items: TagSuggestion[] }>()
    for (const s of suggestions) {
      const k = contactKey(s.platform, s.chat_id)
      const g = m.get(k) ?? { name: s.contact_name || s.chat_id, platform: s.platform, items: [] }
      g.items.push(s); m.set(k, g)
    }
    return [...m.values()]
  }, [suggestions])

  if (suggestions.length === 0) {
    return <p className="py-20 text-center text-sm text-[var(--color-muted-foreground)]">没有待审核的标签建议。点右上角「批量打标签」让 AI 跑一轮。</p>
  }

  const selectedIds = [...selected]
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
        <span className="text-sm text-[var(--color-muted-foreground)]">已选 {selected.size} / {suggestions.length}</span>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => void onAct("reject", selectedIds)} disabled={!selected.size}>
            <Trash2 className="mr-1.5 h-3.5 w-3.5" />拒绝所选
          </Button>
          <Button size="sm" onClick={() => void onAct("confirm", selectedIds)} disabled={!selected.size}>
            <Check className="mr-1.5 h-3.5 w-3.5" />确认所选 ({selected.size})
          </Button>
        </div>
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {groups.map(g => (
          <div key={contactKey(g.platform, g.items[0].chat_id)} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
            <div className="mb-3 flex items-center gap-2">
              <PlatformAvatar platform={g.platform} name={g.name} />
              <div>
                <p className="text-sm font-semibold">{g.name}</p>
                <p className="text-xs text-[var(--color-muted-foreground)]">{PLATFORM_LABEL[g.platform] || g.platform}</p>
              </div>
            </div>
            <div className="space-y-1.5">
              {g.items.map(s => {
                const checked = selected.has(s.link_id)
                return (
                  <div
                    key={s.link_id}
                    onClick={() => toggle(s.link_id)}
                    className={`flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 transition-colors ${checked ? "bg-[var(--color-secondary)]" : "opacity-50 hover:opacity-80"}`}
                  >
                    <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${checked ? "border-[var(--color-primary)] bg-[var(--color-primary)]" : "border-[var(--color-muted-foreground)]/40"}`}>
                      {checked && <Check className="h-3 w-3 text-white" />}
                    </span>
                    <TagChip name={s.tag_name} color={null} seed={s.tag_id} />
                    {s.tag_status === "pending" && <Badge variant="outline" className="text-[10px]">新</Badge>}
                    <span className="text-xs text-[var(--color-muted-foreground)] tabular-nums">{Math.round(s.confidence * 100)}%</span>
                    <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-muted-foreground)]">{s.reason}</span>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---------- library view ---------- */

function LibraryView({ tags, onCreate, onApprove, onDelete, onInsight, onVip }: {
  tags: ContactTag[]
  onCreate: (name: string) => Promise<void>
  onApprove: (id: number) => Promise<void>
  onDelete: (id: number) => Promise<void>
  onInsight: (id: number, name: string) => void
  onVip: (id: number, name: string) => void
}) {
  const [name, setName] = useState("")
  const active = tags.filter(t => t.status === "active")
  const pending = tags.filter(t => t.status === "pending")

  const create = async () => {
    const n = name.trim()
    if (!n) return
    setName("")
    await onCreate(n)
  }

  return (
    <div className="space-y-6 p-4">
      <div className="flex gap-2">
        <Input
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") void create() }}
          placeholder="新建预设标签，如：客户 / 同事 / 家人"
          className="max-w-xs bg-[var(--color-background)]"
        />
        <Button size="sm" onClick={() => void create()} disabled={!name.trim()}><Plus className="mr-1 h-4 w-4" />添加</Button>
      </div>

      <div>
        <p className="mb-2 text-xs font-semibold text-[var(--color-muted-foreground)]">已启用标签 ({active.length})</p>
        <div className="flex flex-wrap gap-2">
          {active.length ? active.map(t => (
            <span key={t.id} className="inline-flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] py-1.5 pl-3 pr-2 shadow-sm">
              <TagChip name={t.name} color={t.color} seed={t.id} />
              <span className="text-xs text-[var(--color-muted-foreground)]">{t.confirmed_count} 人</span>
              <button onClick={() => onInsight(t.id, t.name)} disabled={!t.confirmed_count} className="rounded p-0.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-warning-subtle)] hover:text-[var(--color-warning)] disabled:opacity-30" aria-label="群体洞察" title="AI 群体洞察"><Lightbulb className="h-3.5 w-3.5" /></button>
              <button onClick={() => onVip(t.id, t.name)} disabled={!t.confirmed_count} className="rounded p-0.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-primary-subtle)] hover:text-[var(--color-primary)] disabled:opacity-30" aria-label="加入白名单" title="把这些人加入去噪白名单（VIP）"><Star className="h-3.5 w-3.5" /></button>
              <button onClick={() => void onDelete(t.id)} className="rounded p-0.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-destructive-subtle)] hover:text-[var(--color-destructive)]" aria-label="删除标签"><Trash2 className="h-3.5 w-3.5" /></button>
            </span>
          )) : <p className="text-xs text-[var(--color-muted-foreground)]">还没有标签，新建几个或让 AI 自动生成。</p>}
        </div>
      </div>

      {pending.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-semibold text-[var(--color-muted-foreground)]">AI 待批准标签 ({pending.length})</p>
          <div className="flex flex-wrap gap-2">
            {pending.map(t => (
              <span key={t.id} className="inline-flex items-center gap-2 rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-card-elevated)] py-1.5 pl-3 pr-2">
                <TagChip name={t.name} color={t.color} seed={t.id} />
                <span className="text-xs text-[var(--color-muted-foreground)]">{t.suggested_count} 处建议</span>
                <button onClick={() => void onApprove(t.id)} className="rounded p-0.5 text-[var(--color-success)] hover:bg-[var(--color-success-subtle)]" aria-label="批准"><Check className="h-3.5 w-3.5" /></button>
                <button onClick={() => void onDelete(t.id)} className="rounded p-0.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-destructive-subtle)] hover:text-[var(--color-destructive)]" aria-label="删除"><Trash2 className="h-3.5 w-3.5" /></button>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/* ---------- page ---------- */

type View = "contacts" | "review" | "library"

export default function Tags() {
  const [view, setView] = useState<View>("contacts")
  const [tags, setTags] = useState<ContactTag[]>([])
  const [suggestions, setSuggestions] = useState<TagSuggestion[]>([])
  const [contacts, setContacts] = useState<ChatInfo[]>([])
  const [contactTags, setContactTags] = useState<ContactTagEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [batchRunning, setBatchRunning] = useState(false)
  const [search, setSearch] = useState("")
  const [filterTagId, setFilterTagId] = useState<number | null>(null)
  const [dialog, setDialog] = useState<{ platform: string; chatId: string; name: string } | null>(null)
  const [insight, setInsight] = useState<{ name: string; loading: boolean; text: string } | null>(null)

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const [t, s, c, ct] = await Promise.all([
        fetchAPI<ContactTag[]>("/tags"),
        fetchAPI<TagSuggestion[]>("/tags/suggestions"),
        fetchAPI<ChatInfo[]>("/chats"),
        fetchAPI<ContactTagEntry[]>("/contacts/tags"),
      ])
      setTags(t); setSuggestions(s); setContacts(c); setContactTags(ct)
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void loadAll() }, [loadAll])

  const activeTags = useMemo(() => tags.filter(t => t.status === "active"), [tags])

  const tagsByContact = useMemo(() => {
    const m = new Map<string, ContactTagEntry[]>()
    for (const e of contactTags) {
      const k = contactKey(e.platform, e.chat_id)
      const arr = m.get(k); if (arr) arr.push(e); else m.set(k, [e])
    }
    return m
  }, [contactTags])

  const visibleContacts = useMemo(() => {
    const q = search.trim().toLowerCase()
    return contacts.filter(c => {
      const name = c.chat_name || c.chat_id
      if (q && !name.toLowerCase().includes(q)) return false
      if (filterTagId != null) {
        const list = tagsByContact.get(contactKey(c.platform, c.chat_id))
        if (!list?.some(e => e.tag_id === filterTagId)) return false
      }
      return true
    })
  }, [contacts, search, filterTagId, tagsByContact])

  const runBatch = async () => {
    if (batchRunning) return
    setBatchRunning(true)
    const id = toast.loading("启动批量打标签...")
    try {
      const { task_id } = await fetchAPI<{ task_id: string }>("/tags/suggest/batch", {
        method: "POST", body: JSON.stringify({}),
      })
      const es = subscribeSSE<TaskProgress>(`/tasks/${task_id}/events`, (data) => {
        toast.loading(data.message || data.status, { id })
        if (data.status === "done") {
          es.close(); toast.success(data.message || "完成", { id }); setBatchRunning(false)
          void loadAll(); setView("review")
        } else if (data.status === "error") {
          es.close(); toast.error(data.message || "失败", { id }); setBatchRunning(false)
        } else if (data.status === "cancelled") {
          es.close(); toast.info(data.message || "已取消", { id }); setBatchRunning(false)
        }
      }, () => { toast.dismiss(id); setBatchRunning(false) })
    } catch (e) {
      toast.error(getErrorMessage(e), { id }); setBatchRunning(false)
    }
  }

  const reviewAct = async (action: "confirm" | "reject", linkIds: number[]) => {
    try {
      await fetchAPI(`/tags/${action}`, { method: "POST", body: JSON.stringify({ link_ids: linkIds }) })
      toast.success(action === "confirm" ? `已确认 ${linkIds.length} 个标签` : `已拒绝 ${linkIds.length} 个建议`)
      await loadAll()
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  const tagMutation = async (fn: () => Promise<unknown>): Promise<void> => {
    try {
      await fn()
      await loadAll()
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }
  const createTag = (name: string) => tagMutation(() => fetchAPI("/tags", { method: "POST", body: JSON.stringify({ name }) }))
  const approveTag = (tagId: number) => tagMutation(() => fetchAPI(`/tags/${tagId}`, { method: "PATCH", body: JSON.stringify({ status: "active" }) }))
  const deleteTag = (tagId: number) => tagMutation(() => fetchAPI(`/tags/${tagId}`, { method: "DELETE" }))

  const runInsight = async (tagId: number, name: string) => {
    setInsight({ name, loading: true, text: "" })
    try {
      const res = await fetchAPI<{ insight: string; contact_count: number; truncated: boolean }>(
        `/tags/${tagId}/insight`, { method: "POST", body: JSON.stringify({}) },
      )
      const note = res.truncated ? `\n\n（联系人较多，仅分析了部分）` : ""
      setInsight({ name, loading: false, text: res.insight + note })
    } catch (e) {
      setInsight(null)
      toast.error(getErrorMessage(e))
    }
  }

  const addVip = async (tagId: number, name: string) => {
    try {
      const res = await fetchAPI<{ affected: number; vip_count: number }>(
        `/tags/${tagId}/vip`, { method: "POST", body: JSON.stringify({ action: "add" }) },
      )
      toast.success(res.affected ? `已把「${name}」下 ${res.affected} 人加入去噪白名单` : `「${name}」下的人已都在白名单中`)
    } catch (e) {
      toast.error(getErrorMessage(e))
    }
  }

  const TABS: { key: View; label: string; count?: number }[] = [
    { key: "contacts", label: "联系人" },
    { key: "review", label: "待审核", count: suggestions.length },
    { key: "library", label: "标签库", count: tags.length },
  ]

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[var(--color-background)]">
      <header className="shrink-0 border-b border-[var(--color-border)] bg-[var(--color-background)]/75 px-5 py-5 backdrop-blur md:px-7">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <TagsIcon className="h-6 w-6" />
            <h1 className="text-2xl font-semibold tracking-tight">联系人标签</h1>
          </div>
          <Button onClick={() => void runBatch()} disabled={batchRunning}>
            {batchRunning ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1.5 h-4 w-4" />}
            {batchRunning ? "打标签中..." : "批量打标签"}
          </Button>
        </div>
        <div className="mt-4 flex gap-6 text-sm">
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setView(t.key)}
              className={`pb-2 ${view === t.key ? "border-b-2 border-[var(--color-primary)] font-medium text-[var(--color-primary)]" : "text-[var(--color-muted-foreground)]"}`}
            >
              {t.label}{t.count ? ` (${t.count})` : ""}
            </button>
          ))}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden">
        {loading ? (
          <div className="flex h-full items-center justify-center"><Loader2 className="h-6 w-6 animate-spin text-[var(--color-muted-foreground)]" /></div>
        ) : view === "review" ? (
          <ReviewView suggestions={suggestions} onAct={reviewAct} />
        ) : view === "library" ? (
          <LibraryView tags={tags} onCreate={createTag} onApprove={approveTag} onDelete={deleteTag} onInsight={runInsight} onVip={addVip} />
        ) : (
          <div className="flex h-full flex-col">
            <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-border)] px-4 py-3">
              <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索联系人" className="h-9 max-w-xs bg-[var(--color-background)]" />
              <button
                onClick={() => setFilterTagId(null)}
                className={`rounded-full px-2.5 py-1 text-xs ${filterTagId == null ? "bg-[var(--color-primary)] text-white" : "bg-[var(--color-secondary)] text-[var(--color-muted-foreground)]"}`}
              >全部</button>
              {activeTags.map(t => (
                <button
                  key={t.id}
                  onClick={() => setFilterTagId(filterTagId === t.id ? null : t.id)}
                  className={`rounded-full px-2.5 py-1 text-xs ${filterTagId === t.id ? "bg-[var(--color-primary)] text-white" : "bg-[var(--color-secondary)] text-[var(--color-muted-foreground)]"}`}
                >{t.name} · {t.confirmed_count}</button>
              ))}
            </div>
            <div className="flex-1 overflow-y-auto">
              {visibleContacts.length === 0 ? (
                <p className="py-20 text-center text-sm text-[var(--color-muted-foreground)]">没有匹配的联系人</p>
              ) : visibleContacts.map(c => {
                const list = tagsByContact.get(contactKey(c.platform, c.chat_id)) ?? []
                return (
                  <div key={contactKey(c.platform, c.chat_id)} className="flex items-center gap-3 border-b border-[var(--color-border)]/70 px-4 py-3 hover:bg-[var(--color-card-elevated)]">
                    <PlatformAvatar platform={c.platform} name={c.chat_name || c.chat_id} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="truncate text-sm font-semibold">{c.chat_name || c.chat_id}</p>
                        {c.chat_type === "group" && <Users className="h-3.5 w-3.5 shrink-0 text-[var(--color-muted-foreground)]" />}
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        {list.length ? list.map(e => <TagChip key={e.tag_id} name={e.name} color={e.color} seed={e.tag_id} />)
                          : <span className="text-xs text-[var(--color-muted-foreground)]">{PLATFORM_LABEL[c.platform] || c.platform} · {c.msg_count.toLocaleString()} 条 · 暂无标签</span>}
                      </div>
                    </div>
                    <Button variant="outline" size="sm" onClick={() => setDialog({ platform: c.platform, chatId: c.chat_id, name: c.chat_name || c.chat_id })}>
                      管理
                    </Button>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {dialog && (
        <ContactTagsDialog
          platform={dialog.platform}
          chatId={dialog.chatId}
          name={dialog.name}
          activeTags={activeTags}
          onClose={() => setDialog(null)}
          onChanged={loadAll}
        />
      )}

      {insight && (
        <Dialog open onClose={() => setInsight(null)} title={`群体洞察 · ${insight.name}`} size="lg">
          {insight.loading ? (
            <div className="flex items-center gap-2 py-8 text-sm text-[var(--color-muted-foreground)]">
              <Loader2 className="h-4 w-4 animate-spin" />AI 正在分析这群人的聊天记录...
            </div>
          ) : (
            <div className="whitespace-pre-wrap rounded-lg border-l-[3px] border-l-[var(--color-primary)] bg-[var(--color-secondary)] p-4 text-sm leading-relaxed">
              {insight.text}
            </div>
          )}
        </Dialog>
      )}
    </div>
  )
}
