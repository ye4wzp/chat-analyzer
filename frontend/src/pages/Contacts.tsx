import { ContactDossier } from "@/components/ContactDossier"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import {
  BookOpen, Check, Gift, Link2, ListChecks, Loader2, Menu, MessagesSquare, Plus, RefreshCw, Search,
  Sparkles, Star, Tag as TagIcon, UserRound, Users, X,
} from "lucide-react"
import { fetchAPI, getErrorMessage, type ContactListItem, type ContactProfile, type IdentityCandidate } from "@/lib/api"
import { KIND_LABEL, PLATFORM_COLOR, PLATFORM_LABEL, chatTypeLabel } from "@/lib/constants"
import { formatCount, formatDate, formatTime } from "@/lib/utils"
import { useOnStatsDone, useStatsFilters } from "@/hooks/useStatsFilters"
import { StatsFilterBar, StatsNotReady } from "@/components/StatsToolbar"
import { SubNav } from "@/components/SubNav"
import { CONTACT_SUBNAV } from "@/lib/subnav"
import { ChartCard, HourBarChart, KindDonut, TrendAreaChart, WeekdayBarChart } from "@/components/charts"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Sheet, SheetHeader } from "@/components/ui/sheet"

function Avatar({ platform, name, size = "h-11 w-11 text-sm" }: { platform: string; name: string; size?: string }) {
  return (
    <span className={`flex ${size} shrink-0 items-center justify-center rounded-full font-semibold text-white shadow-sm`} style={{ background: PLATFORM_COLOR[platform] || "#64748b" }}>
      {(name || "?").slice(0, 1)}
    </span>
  )
}

function TagChip({ name, color, onRemove, muted }: { name: string; color: string | null; onRemove?: () => void; muted?: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${muted ? "border-dashed opacity-70" : ""}`}
      style={{ borderColor: color || "var(--color-border)", color: color || "var(--color-foreground)", background: color ? `color-mix(in oklch, ${color} 14%, transparent)` : "var(--color-card-elevated)" }}
    >
      {name}
      {onRemove && <button onClick={onRemove} className="rounded-full hover:opacity-70" aria-label={`移除标签 ${name}`}><X className="h-3 w-3" /></button>}
    </span>
  )
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card-elevated)] p-3">
      <p className="text-xs text-[var(--color-muted-foreground)]">{label}</p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums">{value}</p>
      {sub && <p className="text-xs text-[var(--color-muted-foreground)]">{sub}</p>}
    </div>
  )
}

/* ------------------------------------------------------------------------ */

interface ListProps {
  items: ContactListItem[]
  loading: boolean
  selected: { platform: string; chat_id: string } | null
  onSelect: (c: ContactListItem) => void
  search: string
  setSearch: (v: string) => void
  chatType: string
  setChatType: (v: string) => void
  sort: string
  setSort: (v: string) => void
}

function ContactList({ items, loading, selected, onSelect, search, setSearch, chatType, setChatType, sort, setSort }: ListProps) {
  return (
    <div className="flex h-full flex-col overflow-hidden bg-[var(--color-card)]">
      <div className="shrink-0 space-y-2 border-b border-[var(--color-border)] p-4">
        <div className="relative">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-[var(--color-muted-foreground)]" />
          <Input placeholder="搜索联系人 / 群" value={search} onChange={e => setSearch(e.target.value)} className="h-9 rounded-lg bg-[var(--color-background)] pl-9" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Select value={chatType} onChange={e => setChatType(e.target.value)} className="h-9 rounded-lg bg-[var(--color-background)] text-xs">
            <option value="">全部类型</option>
            <option value="private">私聊</option>
            <option value="group">群聊</option>
            <option value="channel">频道</option>
          </Select>
          <Select value={sort} onChange={e => setSort(e.target.value)} className="h-9 rounded-lg bg-[var(--color-background)] text-xs">
            <option value="messages">按消息数</option>
            <option value="recent">按最近活跃</option>
            <option value="name">按名称</option>
          </Select>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-10"><Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" /></div>
        ) : items.length === 0 ? (
          <p className="py-10 text-center text-sm text-[var(--color-muted-foreground)]">没有匹配的联系人</p>
        ) : items.map(c => {
          const active = selected?.platform === c.platform && selected?.chat_id === c.chat_id
          return (
            <button
              key={`${c.platform}-${c.chat_id}`}
              onClick={() => onSelect(c)}
              className={`flex w-full items-center gap-3 border-b border-[var(--color-border)]/70 px-4 py-3 text-left transition-colors ${active ? "bg-[var(--color-primary-subtle)]" : "hover:bg-[var(--color-card-elevated)]"}`}
            >
              <Avatar platform={c.platform} name={c.chat_name || c.chat_id} size="h-10 w-10 text-sm" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <p className="conversation-name">{c.canonical_name || c.chat_name || c.chat_id}</p>
                  {c.is_vip && <Star className="h-3 w-3 shrink-0 fill-[var(--color-warning)] text-[var(--color-warning)]" />}
                </div>
                {c.canonical_name && c.chat_name && c.canonical_name !== c.chat_name && <p className="text-xs text-muted-foreground break-words">{c.chat_name}</p>}
                {c.local_note && <p className="truncate text-xs text-foreground" title={c.local_note}>{c.local_note}</p>}
                <p className="text-xs text-muted-foreground">
                  {PLATFORM_LABEL[c.platform] || c.platform} · {chatTypeLabel(c.chat_type)} · {formatCount(c.messages)} 条 · {formatDate(c.last_ts)}
                </p>
                {c.tags.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {c.tags.slice(0, 3).map(t => <TagChip key={t.tag_id} name={t.name} color={t.color} />)}
                    {c.tags.length > 3 && <span className="text-xs text-[var(--color-muted-foreground)]">+{c.tags.length - 3}</span>}
                  </div>
                )}
              </div>
            </button>
          )
        })}
      </div>
      <div className="shrink-0 border-t border-[var(--color-border)] px-4 py-2 text-center text-xs text-[var(--color-muted-foreground)]">{items.length} 个联系人</div>
    </div>
  )
}

/* ------------------------------------------------------------------------ */

function IdentityCard({ profile, aliasName, setAliasName, call }: {
  profile: ContactProfile
  aliasName: string
  setAliasName: (v: string) => void
  call: (label: string, fn: () => Promise<unknown>) => Promise<void>
}) {
  const navigate = useNavigate()
  const [picked, setPicked] = useState<Set<string>>(() => new Set(profile.identity_candidates.filter(c => !c.already_linked).map(c => c.value)))
  const person = profile.person
  const fp = profile.footprint
  const candidates = profile.identity_candidates

  const link = () => {
    if (!aliasName.trim()) return
    const senders = candidates.filter(c => picked.has(c.value) && !c.already_linked).map(c => ({
      platform: c.platform, kind: c.kind, value: c.value, chat_id: c.chat_id, display_name: c.display_name,
    }))
    void call("关联身份...", () => fetchAPI("/persons/link-chat", {
      method: "POST",
      body: JSON.stringify({ name: aliasName.trim(), platform: profile.platform, chat_id: profile.chat_id, chat_name: profile.chat_name || null, senders }),
    }))
  }
  const addCandidate = (c: IdentityCandidate) => {
    if (!person) return
    void call("关联群内身份...", () => fetchAPI(`/persons/${person.id}/identities`, {
      method: "POST",
      body: JSON.stringify({ platform: c.platform, kind: c.kind, value: c.value, chat_id: c.chat_id, display_name: c.display_name }),
    }))
  }

  return (
    <ChartCard
      title="这个人"
      subtitle={person ? `已识别为「${person.name}」，跨 ${new Set(fp?.identities.map(i => i.platform)).size} 个平台 · ${fp?.total_messages.toLocaleString()} 条` : "把私聊、群内昵称、其他平台账号归到同一个人"}
      right={person && <UserRound className="h-4 w-4 text-[var(--color-muted-foreground)]" />}
    >
      {!person ? (
        <div className="space-y-3">
          <form onSubmit={e => { e.preventDefault(); link() }} className="flex gap-2">
            <Input value={aliasName} onChange={e => setAliasName(e.target.value)} placeholder={`统一姓名，如「${profile.chat_name || "张三"}」`} className="h-9" />
            <Button type="submit" size="sm" variant="outline" disabled={!aliasName.trim()}><Plus className="mr-1 h-3.5 w-3.5" />建档</Button>
          </form>
          {candidates.length > 0 && (
            <div className="rounded-lg border border-dashed border-[var(--color-border)] p-3">
              <p className="mb-2 text-xs text-[var(--color-muted-foreground)]">可能是同一个人的群内身份（建档时一起关联）：</p>
              <ul className="space-y-1.5 text-xs">
                {candidates.map(c => (
                  <li key={c.value} className="flex items-center gap-2">
                    <input type="checkbox" checked={picked.has(c.value)} disabled={c.already_linked} onChange={e => setPicked(prev => { const n = new Set(prev); if (e.target.checked) n.add(c.value); else n.delete(c.value); return n })} className="accent-[var(--color-primary)]" />
                    <span className="font-medium">{c.display_name || c.value}</span>
                    <span className="text-[var(--color-muted-foreground)]">在 {c.groups} 个群发过 {c.messages.toLocaleString()} 条 · {c.reason}</span>
                    {c.already_linked && <span className="ml-auto text-[var(--color-warning)]">已属于其他人</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <div>
            <p className="mb-1.5 text-xs font-medium text-[var(--color-muted-foreground)]">身份</p>
            <ul className="space-y-1.5 text-xs">
              {fp?.identities.map(i => (
                <li key={i.id} className="flex items-center gap-2 rounded-md border border-[var(--color-border)] px-2 py-1.5">
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: PLATFORM_COLOR[i.platform] || "#94a3b8" }} />
                  <span>{PLATFORM_LABEL[i.platform] || i.platform}</span>
                  <span className="rounded bg-[var(--color-secondary)] px-1.5 py-0.5 text-xs">{i.kind === "chat" ? "私聊" : i.chat_id ? "群内昵称（单群）" : "群内昵称"}</span>
                  <span className="truncate font-mono text-[var(--color-muted-foreground)]" title={i.value}>{i.display_name || i.value}</span>
                  {i.source !== "manual" && <span className="text-xs text-[var(--color-text-faint)]">{i.source === "auto" ? "自动" : "迁移"}</span>}
                  <button onClick={() => call("移除身份...", () => fetchAPI(`/persons/${person.id}/identities/${i.id}`, { method: "DELETE" }))} className="ml-auto rounded p-0.5 text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)]" aria-label="移除身份"><X className="h-3 w-3" /></button>
                </li>
              ))}
            </ul>
            {candidates.some(c => !c.already_linked) && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {candidates.filter(c => !c.already_linked).map(c => (
                  <button key={c.value} onClick={() => addCandidate(c)} className="inline-flex items-center gap-1 rounded-full border border-dashed border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-muted-foreground)] hover:border-[var(--color-primary)] hover:text-[var(--color-primary)]" title={c.reason}>
                    <Plus className="h-3 w-3" />群内「{c.display_name || c.value}」· {c.groups} 群
                  </button>
                ))}
              </div>
            )}
          </div>
          {fp && fp.groups.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-[var(--color-muted-foreground)]">在这些群里说话</p>
              <ul className="space-y-1 text-xs">
                {fp.groups.slice(0, 8).map(g => (
                  <li key={`${g.platform}-${g.chat_id}`}>
                    <button onClick={() => navigate(`/members?platform=${g.platform}&chat_id=${encodeURIComponent(g.chat_id)}`)} className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left hover:bg-[var(--color-card-elevated)]">
                      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: PLATFORM_COLOR[g.platform] || "#94a3b8" }} />
                      <span className="min-w-0 flex-1 break-words">{g.chat_name || g.chat_id}</span>
                      <span className="tabular-nums text-[var(--color-muted-foreground)]">{g.n.toLocaleString()} 条 · {g.months} 个月 · 最近 {formatDate(g.last_ts)}</span>
                    </button>
                  </li>
                ))}
                {fp.groups.length > 8 && <li className="px-2 text-xs text-[var(--color-muted-foreground)]">还有 {fp.groups.length - 8} 个群</li>}
              </ul>
            </div>
          )}
          {fp && fp.groups.length === 0 && fp.identities.every(i => i.kind === "chat") && (
            <p className="text-xs text-[var(--color-muted-foreground)]">还没有关联群内身份，所以看不到他在群里的发言。{candidates.length ? "上面有候选可以一键加上。" : ""}</p>
          )}
        </div>
      )}
    </ChartCard>
  )
}

function ProfileView({ profile, onChanged, onDossierDirtyChange }: { profile: ContactProfile; onChanged: () => void; onDossierDirtyChange: (dirty: boolean) => void }) {
  const navigate = useNavigate()
  const [summarizing, setSummarizing] = useState(false)
  const [suggesting, setSuggesting] = useState(false)
  const [newTag, setNewTag] = useState("")
  const [aliasName, setAliasName] = useState(profile.canonical_name || "")
  const key = `${profile.platform}/${encodeURIComponent(profile.chat_id)}`

  useEffect(() => { setAliasName(profile.canonical_name || "") }, [profile.canonical_name, profile.chat_id])

  const call = async (label: string, fn: () => Promise<unknown>, setBusy?: (b: boolean) => void) => {
    setBusy?.(true)
    const id = toast.loading(label)
    try {
      await fn()
      toast.success("完成", { id })
      onChanged()
    } catch (e) {
      toast.error(getErrorMessage(e), { id })
    } finally {
      setBusy?.(false)
    }
  }

  const confirmed = profile.tags.filter(t => t.status === "confirmed")
  const suggested = profile.tags.filter(t => t.status === "suggested")
  const contactSender = profile.senders.find(s => !s.is_me)
  const meSender = profile.chat_type === "private" ? profile.senders.find(s => s.is_me) : undefined
  const peakHour = profile.hourly.indexOf(Math.max(...profile.hourly))
  const perActiveDay = profile.active_days ? (profile.messages / profile.active_days).toFixed(1) : "0"
  const resourceTotal = Object.values(profile.resources).reduce((a, b) => a + b, 0)

  return (
    <div className="mx-auto w-full max-w-[1100px] space-y-5 p-5 md:p-7">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-center gap-4">
          <Avatar platform={profile.platform} name={profile.chat_name || profile.chat_id} size="h-14 w-14 text-xl" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="break-words text-xl font-semibold">{profile.chat_name || profile.chat_id}</h1>
              {profile.canonical_name && profile.canonical_name !== profile.chat_name && (
                <span className="rounded-md bg-[var(--color-secondary)] px-1.5 py-0.5 text-xs text-[var(--color-muted-foreground)]">= {profile.canonical_name}</span>
              )}
              <button
                onClick={() => call(profile.is_vip ? "移出 VIP..." : "加入 VIP...", () => fetchAPI("/config", {
                  method: "PUT", body: JSON.stringify(profile.is_vip ? { remove_vip: profile.canonical_name || profile.chat_name } : { add_vip: profile.canonical_name || profile.chat_name }),
                }))}
                title={profile.is_vip ? "移出 VIP" : "加入 VIP（分析时不过滤）"}
                className="rounded-md p-1 hover:bg-[var(--color-accent)]"
              >
                <Star className={`h-4 w-4 ${profile.is_vip ? "fill-[var(--color-warning)] text-[var(--color-warning)]" : "text-[var(--color-muted-foreground)]"}`} />
              </button>
            </div>
            <p className="mt-0.5 text-xs text-[var(--color-muted-foreground)]">
              {PLATFORM_LABEL[profile.platform] || profile.platform} · {chatTypeLabel(profile.chat_type)} · <span className="font-mono">{profile.chat_id}</span>
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {confirmed.map(t => (
                <TagChip key={t.tag_id} name={t.name} color={t.color} onRemove={() => call("移除标签...", () => fetchAPI(`/contacts/${key}/tags/${t.tag_id}`, { method: "DELETE" }))} />
              ))}
              {suggested.map(t => (
                <span key={t.link_id} className="inline-flex items-center gap-1">
                  <TagChip name={`${t.name}${t.confidence != null ? ` ${Math.round(t.confidence * 100)}%` : ""}`} color={t.color} muted />
                  <button title={t.reason || "采纳"} onClick={() => call("采纳标签...", () => fetchAPI("/tags/confirm", { method: "POST", body: JSON.stringify({ link_ids: [t.link_id] }) }))} className="rounded p-0.5 text-[var(--color-success)] hover:bg-[var(--color-accent)]"><Check className="h-3 w-3" /></button>
                  <button title="忽略" onClick={() => call("忽略标签...", () => fetchAPI("/tags/reject", { method: "POST", body: JSON.stringify({ link_ids: [t.link_id] }) }))} className="rounded p-0.5 text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]"><X className="h-3 w-3" /></button>
                </span>
              ))}
              <form
                onSubmit={e => { e.preventDefault(); if (!newTag.trim()) return; void call("添加标签...", () => fetchAPI(`/contacts/${key}/tags`, { method: "POST", body: JSON.stringify({ name: newTag.trim() }) })); setNewTag("") }}
                className="inline-flex items-center"
              >
                <input value={newTag} onChange={e => setNewTag(e.target.value)} placeholder="+ 标签" className="h-6 w-20 rounded-full border border-dashed border-[var(--color-border)] bg-transparent px-2 text-xs outline-none focus:border-[var(--color-primary)]" />
              </form>
              {profile.chat_type === "private" ? <button
                onClick={() => document.getElementById("contact-dossier")?.scrollIntoView({ behavior: "smooth", block: "start" })}
                className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]"
              ><Sparkles className="h-3 w-3" />全量标签与备注</button> : <button
                onClick={() => call("AI 分析标签...", () => fetchAPI(`/contacts/${key}/tags/suggest`, { method: "POST" }), setSuggesting)}
                disabled={suggesting}
                className="inline-flex items-center gap-1 rounded-full border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)] disabled:opacity-50"
              >
                {suggesting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Sparkles className="h-3 w-3" />} AI 打标签
              </button>}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => navigate(`/chats?platform=${profile.platform}&chat_id=${encodeURIComponent(profile.chat_id)}`)}>
            <MessagesSquare className="mr-1.5 h-3.5 w-3.5" /> 聊天记录
          </Button>
          {profile.chat_type !== "private" && (
            <Button variant="outline" size="sm" onClick={() => navigate(`/members?platform=${profile.platform}&chat_id=${encodeURIComponent(profile.chat_id)}`)}>
              <Users className="mr-1.5 h-3.5 w-3.5" /> 成员分析
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => navigate(`/calendar?platform=${profile.platform}&chat_id=${encodeURIComponent(profile.chat_id)}`)}>
            日历热图
          </Button>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <Stat label="消息总数" value={profile.messages.toLocaleString()} sub={`日均 ${perActiveDay}（按活跃日）`} />
        <Stat label="活跃天数" value={profile.active_days.toLocaleString()} sub={profile.span_days ? `跨度 ${profile.span_days} 天` : undefined} />
        <Stat label="活跃月数" value={String(profile.active_months)} />
        <Stat label="首次消息" value={formatDate(profile.first_ts)} />
        <Stat label="最近消息" value={formatDate(profile.last_ts)} />
        <Stat
          label="活跃高峰"
          value={peakHour >= 0 ? `${peakHour}:00` : "—"}
          sub={profile.chat_type === "private" && contactSender && meSender
            ? `对方 ${contactSender.share}% / 我 ${meSender.share}%`
            : profile.chat_type === "channel" ? "频道单向推送" : `${profile.distinct_senders} 位发言人`}
        />
      </div>

      {profile.chat_type === "private" && <ContactDossier key={key} platform={profile.platform} chatId={profile.chat_id} legacyNotes={profile.person?.notes} tags={confirmed.map(t => ({ id: t.tag_id, name: t.name }))} onChanged={onChanged} onDirtyChange={onDossierDirtyChange} />}

      <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <ChartCard title={profile.chat_type === "private" ? "聊天摘要（非已确认档案）" : "AI 印象"} subtitle={profile.summary_generated_at ? `生成于 ${formatTime(profile.summary_generated_at)}` : "基于最近 100 条消息"} right={
          <Button variant="outline" size="sm" disabled={summarizing} onClick={() => call("AI 总结中...", () => fetchAPI(`/chats/${key}/profile/summarize`, { method: "POST" }), setSummarizing)}>
            {summarizing ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1.5 h-3.5 w-3.5" />}
            {profile.summary ? "重新生成" : "生成"}
          </Button>
        }>
          {profile.summary ? (
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{profile.summary}</p>
          ) : (
            <p className="text-sm text-[var(--color-muted-foreground)]">还没有 AI 印象。点击“生成”让模型读最近的聊天写一段简介。</p>
          )}
        </ChartCard>

        <ChartCard title="沉淀" subtitle="来自这段聊天的产出">
          <div className="grid grid-cols-3 gap-3 text-center">
            <button onClick={() => navigate("/knowledge")} className="rounded-lg border border-[var(--color-border)] p-3 hover:bg-[var(--color-card-elevated)]">
              <BookOpen className="mx-auto h-4 w-4 text-[var(--color-info)]" />
              <p className="mt-1 text-lg font-semibold tabular-nums">{profile.knowledge.length}</p>
              <p className="text-xs text-[var(--color-muted-foreground)]">知识点</p>
            </button>
            <button onClick={() => navigate("/todos")} className="rounded-lg border border-[var(--color-border)] p-3 hover:bg-[var(--color-card-elevated)]">
              <ListChecks className="mx-auto h-4 w-4 text-[var(--color-warning)]" />
              <p className="mt-1 text-lg font-semibold tabular-nums">{profile.todos.open}<span className="text-xs text-[var(--color-muted-foreground)]">/{profile.todos.total}</span></p>
              <p className="text-xs text-[var(--color-muted-foreground)]">待办（未完成）</p>
            </button>
            <button onClick={() => navigate(`/resources?platform=${profile.platform}&chat_id=${encodeURIComponent(profile.chat_id)}`)} className="rounded-lg border border-[var(--color-border)] p-3 hover:bg-[var(--color-card-elevated)]">
              <Link2 className="mx-auto h-4 w-4 text-[var(--color-success)]" />
              <p className="mt-1 text-lg font-semibold tabular-nums">{resourceTotal}</p>
              <p className="text-xs text-[var(--color-muted-foreground)]">链接 / 文件</p>
            </button>
          </div>
          {(profile.resources.redpacket || profile.resources.transfer) ? (
            <p className="mt-3 flex items-center gap-1.5 text-xs text-[var(--color-muted-foreground)]">
              <Gift className="h-3.5 w-3.5 text-[var(--color-destructive)]" />
              红包 {profile.resources.redpacket || 0} 次 · 转账 {profile.resources.transfer || 0} 次
              <button onClick={() => navigate(`/payments?platform=${profile.platform}&chat_id=${encodeURIComponent(profile.chat_id)}`)} className="ml-auto text-[var(--color-primary)]">查看</button>
            </p>
          ) : null}
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard title="消息量趋势（按月）">
          <TrendAreaChart data={profile.monthly} height={200} color={PLATFORM_COLOR[profile.platform] || "var(--chart-1)"} />
        </ChartCard>
        <ChartCard title="24 小时活跃">
          <HourBarChart hours={profile.hourly} height={200} color={PLATFORM_COLOR[profile.platform] || "var(--color-success)"} />
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <ChartCard title="消息类型">
          <KindDonut kinds={profile.kinds} height={220} />
        </ChartCard>
        <ChartCard title="一周分布">
          <WeekdayBarChart weekdays={profile.weekday} height={220} />
        </ChartCard>
        <ChartCard title={profile.chat_type === "private" ? "谁说得多" : "发言人 Top"}>
          <div className="space-y-2">
            {profile.senders.map(s => (
              <div key={s.sender_key} className="text-xs">
                <div className="mb-0.5 flex justify-between">
                  <span className="truncate">{s.name}{s.is_me ? "（我）" : ""}</span>
                  <span className="tabular-nums text-[var(--color-muted-foreground)]">{s.count.toLocaleString()} · {s.share}%</span>
                </div>
                <div className="h-1.5 rounded bg-[var(--color-secondary)]"><div className="h-full rounded bg-[var(--color-info)]/70" style={{ width: `${s.share}%` }} /></div>
              </div>
            ))}
          </div>
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <IdentityCard profile={profile} aliasName={aliasName} setAliasName={setAliasName} call={call} />

        <ChartCard title="最近消息" right={
          <button onClick={() => navigate(`/chats?platform=${profile.platform}&chat_id=${encodeURIComponent(profile.chat_id)}`)} className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-primary)]">全部</button>
        }>
          <ul className="space-y-1.5">
            {profile.recent_messages.map(m => (
              <li key={m.id} className="grid grid-cols-[1fr_auto] gap-3 rounded-md px-2 py-1.5 text-xs hover:bg-[var(--color-card-elevated)]">
                <p className="truncate"><span className="font-medium">{m.sender_name || "?"}</span>：{m.content}</p>
                <span className="tabular-nums text-[var(--color-muted-foreground)]">{formatTime(m.timestamp)}</span>
              </li>
            ))}
          </ul>
        </ChartCard>
      </div>

      {profile.knowledge.length > 0 && (
        <ChartCard title="来自该聊天的知识点" right={<TagIcon className="h-4 w-4 text-[var(--color-muted-foreground)]" />}>
          <div className="grid gap-2 md:grid-cols-2">
            {profile.knowledge.slice(0, 8).map(k => (
              <button key={k.id} onClick={() => navigate("/knowledge")} className="rounded-lg border border-[var(--color-border)] p-3 text-left hover:bg-[var(--color-card-elevated)]">
                <p className="truncate text-sm font-medium">{k.title}</p>
                <p className="mt-1 line-clamp-2 text-xs text-[var(--color-muted-foreground)]">{k.content}</p>
              </button>
            ))}
          </div>
        </ChartCard>
      )}
      <p className="text-xs text-[var(--color-muted-foreground)]">
        类型说明：{profile.kinds.slice(0, 6).map(k => `${KIND_LABEL[k.kind] || k.kind} ${formatCount(k.count)}`).join(" · ")}
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------------ */

export default function Contacts() {
  const filters = useStatsFilters()
  const [params, setParams] = useSearchParams()
  const [items, setItems] = useState<ContactListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState("")
  // "联系人" defaults to private chats; deep links (?platform=&chat_id=) to a
  // group must still resolve, so fall back to "all" when a chat is pre-selected.
  const [chatType, setChatType] = useState(() => params.get("type") ?? (params.get("chat_id") ? "" : "private"))
  const [sort, setSort] = useState("messages")
  const [profile, setProfile] = useState<ContactProfile | null>(null)
  const [profileLoading, setProfileLoading] = useState(false)
  const [dossierDirty, setDossierDirty] = useState(false)
  const profileRequest = useRef(0)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [error, setError] = useState("")

  const selPlatform = params.get("platform")
  const selChatId = params.get("chat_id")
  const selected = useMemo(() => selPlatform && selChatId ? { platform: selPlatform, chat_id: selChatId } : null, [selPlatform, selChatId])

  const loadList = useCallback(() => {
    setLoading(true)
    const q = new URLSearchParams()
    if (filters.platform) q.set("platform", filters.platform)
    if (filters.account) q.set("account", filters.account)
    if (chatType) q.set("chat_type", chatType)
    q.set("sort", sort)
    fetchAPI<ContactListItem[]>(`/contacts?${q}`)
      .then(d => { setItems(d); setError("") })
      .catch(e => setError(getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [filters.platform, filters.account, chatType, sort])

  useEffect(() => { loadList() }, [loadList])

  const loadProfile = useCallback(() => {
    const request = ++profileRequest.current
    if (!selected) { setProfile(null); return }
    setProfile(p => p?.platform === selected.platform && p?.chat_id === selected.chat_id ? p : null)
    setProfileLoading(true)
    fetchAPI<ContactProfile>(`/contacts/${selected.platform}/${encodeURIComponent(selected.chat_id)}/profile${filters.account ? `?account=${encodeURIComponent(filters.account)}` : ""}`)
      .then(p => { if (request === profileRequest.current) setProfile(p) })
      .catch(e => { if (request === profileRequest.current) { toast.error(getErrorMessage(e)); setProfile(null) } })
      .finally(() => { if (request === profileRequest.current) setProfileLoading(false) })
  }, [selected, filters.account])

  useEffect(() => { loadProfile() }, [loadProfile])
  useOnStatsDone(useCallback(() => { loadList(); loadProfile() }, [loadList, loadProfile]))

  // Default to the first contact when nothing is selected.
  useEffect(() => {
    if (!selected && items.length > 0) {
      const first = items[0]
      setParams(prev => { const p = new URLSearchParams(prev); p.set("platform", first.platform); p.set("chat_id", first.chat_id); return p }, { replace: true })
    }
  }, [items, selected, setParams])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return items
    return items.filter(c => (c.chat_name || c.chat_id).toLowerCase().includes(q) || (c.local_note || "").toLowerCase().includes(q) || c.tags.some(t => t.name.toLowerCase().includes(q)) || (c.canonical_name || "").toLowerCase().includes(q))
  }, [items, search])

  const select = (c: ContactListItem) => {
    if (selected?.platform === c.platform && selected.chat_id === c.chat_id) return
    if (dossierDirty && !window.confirm("有尚未保存的备注或审核选择，切换联系人将丢弃这些编辑。确定切换？")) return
    setProfile(null)
    setPickerOpen(false)
    setParams(prev => { const p = new URLSearchParams(prev); p.set("platform", c.platform); p.set("chat_id", c.chat_id); return p }, { replace: true })
  }

  const onChanged = () => { loadProfile(); loadList() }

  const listProps: ListProps = { items: filtered, loading, selected, onSelect: select, search, setSearch, chatType, setChatType, sort, setSort }

  return (
    <div className="flex h-full overflow-hidden">
      <div className="hidden w-[320px] shrink-0 border-r border-[var(--color-border)] md:flex">
        <ContactList {...listProps} />
      </div>
      <Sheet open={pickerOpen} onClose={() => setPickerOpen(false)} side="left">
        <SheetHeader title="选择联系人" onClose={() => setPickerOpen(false)} />
        <ContactList {...listProps} />
      </Sheet>

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <div className="flex items-center justify-between gap-2 border-b border-[var(--color-border)] px-4 py-2">
          <button onClick={() => setPickerOpen(true)} className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-1.5 text-xs text-[var(--color-muted-foreground)] md:hidden">
            <Menu className="h-3.5 w-3.5" /> {profile?.chat_name || "选择联系人"}
          </button>
          <div className="hidden items-center gap-3 md:flex">
            <span className="text-sm font-semibold">联系人</span>
            <SubNav items={CONTACT_SUBNAV} />
          </div>
          <StatsFilterBar filters={filters} />
        </div>
        <div className="flex-1 overflow-y-auto">
          {error ? (
            <p className="p-6 text-sm text-[var(--color-destructive)]">{error}</p>
          ) : !loading && items.length === 0 && !search ? (
            <div className="p-6"><StatsNotReady /></div>
          ) : profileLoading && !profile ? (
            <div className="flex items-center justify-center py-20"><Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" /></div>
          ) : profile ? (
            <ProfileView profile={profile} onChanged={onChanged} onDossierDirtyChange={setDossierDirty} />
          ) : (
            <p className="p-10 text-center text-sm text-[var(--color-muted-foreground)]">从左侧选择一个联系人</p>
          )}
        </div>
      </main>
    </div>
  )
}
