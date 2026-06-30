import { useEffect, useState, useCallback, useRef } from "react"
import { fetchAPI, getErrorMessage, type ChatInfo, type Message } from "@/lib/api"
import { UrgencyBadge, CategoryBadge } from "@/components/MessageBadges"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { Sheet, SheetHeader } from "@/components/ui/sheet"
import { Search, ChevronDown, Filter } from "lucide-react"
import { PLATFORM_COLOR, PLATFORM_LABEL, PAGE_SIZE } from "@/lib/constants"

function groupByDay(messages: Message[]) {
  const groups: { date: string; messages: Message[] }[] = []
  for (const m of messages) {
    const day = m.timestamp.slice(0, 10)
    if (groups.length === 0 || groups[groups.length - 1].date !== day) {
      groups.push({ date: day, messages: [] })
    }
    groups[groups.length - 1].messages.push(m)
  }
  return groups
}

function formatDateLabel(date: string) {
  const d = new Date(date)
  const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${weekdays[d.getDay()]}`
}

function Avatar({ name, platform }: { name: string; platform: string }) {
  const initial = (name || "?").charAt(0).toUpperCase()
  const bg = PLATFORM_COLOR[platform] || "#6b7280"
  return (
    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-medium text-white" style={{ background: bg }}>
      {initial}
    </div>
  )
}

interface FilterPanelProps {
  chats: ChatInfo[]
  selectedChat: ChatInfo | null
  setSelectedChat: (v: ChatInfo | null) => void
  platform: string
  setPlatform: (v: string) => void
  since: string
  setSince: (v: string) => void
  until: string
  setUntil: (v: string) => void
  chatSearch: string
  setChatSearch: (v: string) => void
}

function FilterPanel({ chats, selectedChat, setSelectedChat, platform, setPlatform, since, setSince, until, setUntil, chatSearch, setChatSearch }: FilterPanelProps) {
  const filtered = chats.filter(c => {
    if (platform && c.platform !== platform) return false
    if (chatSearch && !c.chat_name.toLowerCase().includes(chatSearch.toLowerCase())) return false
    return true
  })
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="p-3 border-b border-[var(--color-border)] space-y-2 shrink-0">
        <div className="relative">
          <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
          <Input className="pl-8 text-sm" placeholder="搜索聊天..." value={chatSearch} onChange={e => setChatSearch(e.target.value)} />
        </div>
        <Select className="text-sm" value={platform} onChange={e => setPlatform(e.target.value)}>
          <option value="">全部平台</option>
          <option value="wechat">微信</option>
          <option value="telegram">Telegram</option>
          <option value="qq">QQ</option>
        </Select>
        <div className="flex gap-2">
          <Input type="date" className="text-xs" value={since} onChange={e => setSince(e.target.value)} />
          <Input type="date" className="text-xs" value={until} onChange={e => setUntil(e.target.value)} />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {filtered.map(c => (
          <button
            key={`${c.platform}-${c.chat_id}`}
            onClick={() => setSelectedChat(selectedChat?.platform === c.platform && selectedChat?.chat_id === c.chat_id ? null : c)}
            className={`flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors text-left ${selectedChat?.platform === c.platform && selectedChat?.chat_id === c.chat_id ? "bg-[var(--color-primary)]/10 text-[var(--color-foreground)]" : "text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]"}`}
          >
            <span className="h-2 w-2 rounded-full shrink-0" style={{ background: PLATFORM_COLOR[c.platform] || "#6b7280" }} />
            <span className="truncate flex-1">{c.chat_name}</span>
            <span className="text-xs opacity-50 tabular-nums">{c.msg_count}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default function Timeline() {
  const [chats, setChats] = useState<ChatInfo[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  const [selectedChat, setSelectedChat] = useState<ChatInfo | null>(null)
  const [platform, setPlatform] = useState("")
  const [since, setSince] = useState("")
  const [until, setUntil] = useState("")
  const [chatSearch, setChatSearch] = useState("")
  const [loading, setLoading] = useState(true)
  const [hasMore, setHasMore] = useState(true)
  const [filterOpen, setFilterOpen] = useState(false)
  const offsetRef = useRef(0)

  useEffect(() => {
    fetchAPI<ChatInfo[]>("/chats")
      .then(setChats)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const loadMessages = useCallback(async (append = false) => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE) })
      if (selectedChat) {
        params.set("platform", selectedChat.platform)
        params.set("chat_id", selectedChat.chat_id)
      } else if (platform) {
        params.set("platform", platform)
      }
      if (since) params.set("since", since)
      if (until) params.set("until", until)
      if (append) params.set("offset", String(offsetRef.current))

      const data = await fetchAPI<Message[]>(`/messages?${params}`)
      if (append) {
        setMessages(prev => [...prev, ...data])
        offsetRef.current += data.length
      } else {
        setMessages(data)
        offsetRef.current = data.length
      }
      setHasMore(data.length === PAGE_SIZE)
    } catch (e) {
      console.error(getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }, [selectedChat, platform, since, until])

  useEffect(() => { void loadMessages() }, [loadMessages])

  const groups = groupByDay(messages)

  const filterPanelProps = {
    chats, selectedChat, setSelectedChat, platform, setPlatform,
    since, setSince, until, setUntil, chatSearch, setChatSearch,
  }

  return (
    <div className="flex h-full">
      <aside className="hidden md:flex w-60 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-sidebar)]">
        <FilterPanel {...filterPanelProps} />
      </aside>

      <Sheet open={filterOpen} onClose={() => setFilterOpen(false)} side="left">
        <SheetHeader title="筛选" onClose={() => setFilterOpen(false)} />
        <FilterPanel {...filterPanelProps} />
      </Sheet>

      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        <div className="flex items-center justify-between mb-6 flex-wrap gap-2">
          <div className="text-sm text-[var(--color-muted-foreground)]">
            {selectedChat ? <span className="font-medium text-[var(--color-foreground)]">{selectedChat.chat_name || selectedChat.chat_id}</span> : "全部聊天"}
            <span className="ml-2 tabular-nums">{messages.length} 条消息</span>
          </div>
          <button
            onClick={() => setFilterOpen(true)}
            className="md:hidden inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-1.5 text-xs text-[var(--color-muted-foreground)]"
          >
            <Filter className="h-3.5 w-3.5" />筛选
          </button>
        </div>

        {groups.length === 0 ? (
          <p className="text-center text-[var(--color-muted-foreground)] py-20 text-sm">暂无消息。选择聊天或导入数据后查看。</p>
        ) : (
          <div className="relative pl-6">
            <div className="absolute left-2 top-0 bottom-0 w-0.5 bg-[var(--color-border)]" />

            {groups.map(group => (
              <div key={group.date}>
                <div className="flex items-center gap-3 py-4 relative">
                  <div className="absolute left-[-18px] h-3 w-3 rounded-full border-2 border-[var(--color-primary)] bg-[var(--color-background)]" />
                  <div className="flex-1 h-px bg-[var(--color-border)]" />
                  <span className="text-xs font-medium text-[var(--color-muted-foreground)] px-3 tabular-nums">{formatDateLabel(group.date)}</span>
                  <div className="flex-1 h-px bg-[var(--color-border)]" />
                </div>

                <div className="space-y-3 pb-2">
                  {group.messages.map(m => (
                    <div key={m.id} className="relative ml-2 rounded-lg p-3 transition-colors bg-[var(--color-card)] hover:bg-[var(--color-card-elevated)] border border-[var(--color-border)]">
                      <div className="flex items-start gap-3">
                        <Avatar name={m.sender_name || "?"} platform={m.platform} />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1 flex-wrap">
                            <span className="text-sm font-medium">{m.sender_name}</span>
                            <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: `${PLATFORM_COLOR[m.platform]}1a`, color: PLATFORM_COLOR[m.platform] }}>
                              {PLATFORM_LABEL[m.platform] || m.platform}
                            </span>
                            <span className="text-xs text-[var(--color-muted-foreground)] tabular-nums">{new Date(m.timestamp).toLocaleTimeString()}</span>
                          </div>
                          <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">{m.content}</p>
                          {(m.category || m.urgency) && (
                            <div className="flex items-center gap-1.5 mt-2">
                              <CategoryBadge category={m.category} />
                              <UrgencyBadge urgency={m.urgency} />
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}

            {hasMore && (
              <div className="flex justify-center py-4">
                <Button variant="outline" size="sm" onClick={() => void loadMessages(true)} disabled={loading}>
                  <ChevronDown className="mr-1 h-3.5 w-3.5" />加载更多
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
