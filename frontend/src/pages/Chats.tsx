import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { BarChart3, Bell, Bookmark, Bot, CalendarDays, Filter, Hash, Loader2, Menu, MessageSquare, MoreHorizontal, Pin, Search, Send, Sparkles, Star, TrendingUp, Users } from "lucide-react"
import { fetchAPI, getErrorMessage, type ChatInfo, type Config, type Message } from "@/lib/api"
import { formatTime } from "@/lib/utils"
import { CategoryBadge, UrgencyBadge } from "@/components/MessageBadges"
import { ChatProfileSheet } from "@/components/ChatProfileSheet"
import { KnowledgeReviewModal } from "@/components/KnowledgeReviewModal"
import { useDetailPanel } from "@/lib/DetailPanelContext"
import { useAnalyzeRunner } from "@/hooks/useAnalyzeRunner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Sheet, SheetHeader } from "@/components/ui/sheet"
import { PAGE_SIZE, PLATFORM_COLOR, PLATFORM_LABEL } from "@/lib/constants"

function PlatformAvatar({ platform, name }: { platform: string; name: string }) {
  const label = platform === "wechat" ? "微" : platform === "qq" ? "Q" : platform === "telegram" ? "T" : (name || "?").slice(0, 1)
  return (
    <span
      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white shadow-sm"
      style={{ background: PLATFORM_COLOR[platform] || "#64748b" }}
    >
      {label}
    </span>
  )
}

function MessageDetail({ m }: { m: Message }) {
  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs text-[var(--color-muted-foreground)] mb-1">{PLATFORM_LABEL[m.platform] || m.platform} · {m.chat_name}</p>
        <p className="text-sm font-medium">{m.sender_name || "未知发送者"}</p>
        <p className="text-xs text-[var(--color-muted-foreground)]">{formatTime(m.timestamp)}</p>
      </div>
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card-elevated)] p-4 text-sm leading-relaxed whitespace-pre-wrap">
        {m.content}
      </div>
      {(m.category || m.urgency || m.summary) && (
        <div className="space-y-2 border-t border-[var(--color-border)] pt-4">
          <div className="flex items-center gap-2">
            <CategoryBadge category={m.category} />
            <UrgencyBadge urgency={m.urgency} />
          </div>
          {m.summary && <p className="text-sm text-[var(--color-muted-foreground)]">{m.summary}</p>}
        </div>
      )}
    </div>
  )
}

interface ChatPickerProps {
  chats: ChatInfo[]
  loading: boolean
  selected: ChatInfo | null
  onSelect: (chat: ChatInfo) => void
  search: string
  setSearch: (v: string) => void
  platform: string
  setPlatform: (v: string) => void
  filterMode: string
  filterChats: Set<string>
  onToggleMode: () => void
  onToggleChat: (chatName: string) => void
}

function ChatPicker({ chats, loading, selected, onSelect, search, setSearch, platform, setPlatform, filterMode, filterChats, onToggleMode, onToggleChat }: ChatPickerProps) {
  const modeLabel = filterMode === "whitelist" ? "白名单" : "黑名单"
  return (
    <div className="flex h-full flex-col overflow-hidden bg-[var(--color-card)]">
      <div className="shrink-0 border-b border-[var(--color-border)] p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">群组列表</h2>
          <button
            onClick={onToggleMode}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card-elevated)] px-2 py-1 text-xs text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]"
            title="切换白名单/黑名单"
          >
            {modeLabel}
          </button>
        </div>
        <div className="mb-3 grid grid-cols-3 rounded-lg bg-[var(--color-secondary)] p-1 text-center text-xs">
          <span className="rounded-md bg-[var(--color-card)] px-2 py-1 font-medium text-[var(--color-primary)] shadow-sm">全部 {chats.length}</span>
          <span className="px-2 py-1 text-[var(--color-muted-foreground)]">未读 12</span>
          <span className="px-2 py-1 text-[var(--color-muted-foreground)]">重要 {filterChats.size}</span>
        </div>
        <div className="relative mb-2">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-[var(--color-muted-foreground)]" />
          <Input
            placeholder="搜索群组"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="h-9 rounded-lg bg-[var(--color-background)] pl-9"
          />
        </div>
        <Select value={platform} onChange={e => setPlatform(e.target.value)} className="h-9 w-full rounded-lg bg-[var(--color-background)] text-sm">
          <option value="">全部平台</option>
          <option value="wechat">微信</option>
          <option value="qq">QQ</option>
          <option value="telegram">Telegram</option>
        </Select>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
          </div>
        ) : chats.length === 0 ? (
          <p className="py-10 text-center text-sm text-[var(--color-muted-foreground)]">暂无聊天</p>
        ) : chats.map(c => {
          const isSelected = selected?.chat_id === c.chat_id && selected?.platform === c.platform
          const inList = filterChats.has(c.chat_name)
          return (
            <div
              key={`${c.platform}-${c.chat_id}`}
              className={`group flex items-center border-b border-[var(--color-border)]/70 transition-colors ${isSelected ? "bg-[var(--color-primary-subtle)]" : "hover:bg-[var(--color-card-elevated)]"}`}
            >
              <button onClick={() => onSelect(c)} className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3 text-left">
                <PlatformAvatar platform={c.platform} name={c.chat_name || c.chat_id} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <p className="truncate text-sm font-semibold">{c.chat_name || c.chat_id}</p>
                    {inList && <Pin className="h-3.5 w-3.5 shrink-0 text-[var(--color-destructive)]" />}
                  </div>
                  <p className="text-xs text-[var(--color-muted-foreground)]">
                    {PLATFORM_LABEL[c.platform] || c.platform} · {c.chat_type === "group" ? "群聊" : "私聊"} · {c.msg_count.toLocaleString()} 条
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-xs text-[var(--color-muted-foreground)]">{formatTime(c.latest)}</p>
                  <span className="mt-1 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-[var(--color-primary)] px-1.5 text-[10px] font-medium text-white">
                    {Math.max(1, c.msg_count % 13)}
                  </span>
                </div>
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onToggleChat(c.chat_name) }}
                title={inList ? "从名单移除" : "加入名单"}
                className={`mr-2 rounded-md p-1.5 transition-colors ${inList ? "text-[var(--color-warning)]" : "text-[var(--color-muted-foreground)]/40 hover:bg-[var(--color-card)] hover:text-[var(--color-foreground)]"}`}
              >
                <Star className={`h-4 w-4 ${inList ? "fill-current" : ""}`} />
              </button>
            </div>
          )
        })}
      </div>
      <div className="shrink-0 border-t border-[var(--color-border)] px-4 py-3 text-center text-xs text-[var(--color-muted-foreground)]">
        已显示 {chats.length} 个群组
      </div>
    </div>
  )
}

export default function Chats() {
  const [searchParams] = useSearchParams()
  const { open } = useDetailPanel()
  const [chats, setChats] = useState<ChatInfo[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  const [selectedChat, setSelectedChat] = useState<ChatInfo | null>(null)
  const [loadingChats, setLoadingChats] = useState(true)
  const [loadingMsgs, setLoadingMsgs] = useState(false)
  const [error, setError] = useState("")
  const [chatSearch, setChatSearch] = useState("")
  const [messageSearch, setMessageSearch] = useState("")
  const [platform, setPlatform] = useState(searchParams.get("platform") || "")
  const [msgHasMore, setMsgHasMore] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [filterMode, setFilterMode] = useState("whitelist")
  const [filterChats, setFilterChats] = useState<Set<string>>(new Set())
  const [profileOpen, setProfileOpen] = useState(false)
  const msgOffsetRef = useRef(0)
  const analyze = useAnalyzeRunner()

  const loadMessages = useCallback(async (chat: ChatInfo, append = false) => {
    setLoadingMsgs(true)
    try {
      const params = new URLSearchParams({
        platform: chat.platform,
        chat_id: chat.chat_id,
        limit: String(PAGE_SIZE),
      })
      if (append) params.set("offset", String(msgOffsetRef.current))
      const data = await fetchAPI<Message[]>(`/messages?${params}`)
      if (append) {
        setMessages(prev => [...prev, ...data])
        msgOffsetRef.current += data.length
      } else {
        setMessages(data)
        msgOffsetRef.current = data.length
      }
      setMsgHasMore(data.length === PAGE_SIZE)
    } catch (e: unknown) {
      setError(getErrorMessage(e))
    } finally {
      setLoadingMsgs(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchAPI<ChatInfo[]>("/chats")
      .then(data => {
        if (cancelled) return
        setChats(data)
        const platformParam = searchParams.get("platform")
        const chatIdParam = searchParams.get("chat_id")
        const chatParam = searchParams.get("chat")
        const found = platformParam && chatIdParam
          ? data.find(c => c.platform === platformParam && c.chat_id === chatIdParam)
          : chatParam
            ? data.find(c => c.chat_name === chatParam)
            : data[0]
        if (found) {
          setSelectedChat(found)
          void loadMessages(found)
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(getErrorMessage(e))
      })
      .finally(() => {
        if (!cancelled) setLoadingChats(false)
      })
    return () => { cancelled = true }
  }, [searchParams, loadMessages])

  useEffect(() => {
    let cancelled = false
    fetchAPI<Config>("/config").then(cfg => {
      if (cancelled) return
      setFilterMode(cfg.chat_filter.mode)
      setFilterChats(new Set(cfg.chat_filter.chats))
    }).catch(() => { /* browsing can work without config */ })
    return () => { cancelled = true }
  }, [])

  const toggleFilterChat = useCallback(async (chatName: string) => {
    if (!chatName) return
    const inList = filterChats.has(chatName)
    const next = new Set(filterChats)
    if (inList) next.delete(chatName); else next.add(chatName)
    setFilterChats(next)
    try {
      await fetchAPI<Config>("/config", {
        method: "PUT",
        body: JSON.stringify(inList ? { remove_chat: chatName } : { add_chat: chatName }),
      })
    } catch (e) {
      setFilterChats(filterChats)
      toast.error(getErrorMessage(e))
    }
  }, [filterChats])

  const toggleFilterMode = useCallback(async () => {
    const next = filterMode === "whitelist" ? "blacklist" : "whitelist"
    setFilterMode(next)
    try {
      await fetchAPI<Config>("/config", {
        method: "PUT",
        body: JSON.stringify({ filter_mode: next }),
      })
      toast.success(`已切换到${next === "whitelist" ? "白名单" : "黑名单"}模式`)
    } catch (e) {
      setFilterMode(filterMode)
      toast.error(getErrorMessage(e))
    }
  }, [filterMode])

  const selectChat = (chat: ChatInfo) => {
    setSelectedChat(chat)
    setMessages([])
    setMessageSearch("")
    msgOffsetRef.current = 0
    setPickerOpen(false)
    void loadMessages(chat)
  }

  const filteredChats = chats.filter(c => {
    if (platform && c.platform !== platform) return false
    const name = c.chat_name || c.chat_id
    if (chatSearch && !name.toLowerCase().includes(chatSearch.toLowerCase())) return false
    return true
  })

  const visibleMessages = useMemo(() => {
    const q = messageSearch.trim().toLowerCase()
    if (!q) return messages
    return messages.filter(m =>
      (m.content || "").toLowerCase().includes(q) ||
      (m.sender_name || "").toLowerCase().includes(q),
    )
  }, [messageSearch, messages])

  const importantCount = messages.filter(m => (m.urgency || 0) >= 4).length
  const summaryCount = messages.filter(m => m.summary).length
  const activeSenderCount = new Set(messages.map(m => m.sender_id || m.sender_name).filter(Boolean)).size

  return (
    <div className="flex h-full overflow-hidden bg-[var(--color-background)]">
      <div className="hidden w-[300px] shrink-0 border-r border-[var(--color-border)] md:flex">
        <ChatPicker
          chats={filteredChats}
          loading={loadingChats}
          selected={selectedChat}
          onSelect={selectChat}
          search={chatSearch}
          setSearch={setChatSearch}
          platform={platform}
          setPlatform={setPlatform}
          filterMode={filterMode}
          filterChats={filterChats}
          onToggleMode={toggleFilterMode}
          onToggleChat={toggleFilterChat}
        />
      </div>

      <Sheet open={pickerOpen} onClose={() => setPickerOpen(false)} side="left">
        <SheetHeader title="选择聊天" onClose={() => setPickerOpen(false)} />
        <ChatPicker
          chats={filteredChats}
          loading={loadingChats}
          selected={selectedChat}
          onSelect={selectChat}
          search={chatSearch}
          setSearch={setChatSearch}
          platform={platform}
          setPlatform={setPlatform}
          filterMode={filterMode}
          filterChats={filterChats}
          onToggleMode={toggleFilterMode}
          onToggleChat={toggleFilterChat}
        />
      </Sheet>

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--color-background)]/60">
        <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-2 md:hidden">
          <button
            onClick={() => setPickerOpen(true)}
            className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-1.5 text-xs text-[var(--color-muted-foreground)]"
          >
            <Menu className="h-3.5 w-3.5" />
            {selectedChat?.chat_name || "选择聊天"}
          </button>
        </div>

        {!selectedChat ? (
          <div className="flex flex-1 items-center justify-center px-6 text-center">
            <p className="text-sm text-[var(--color-muted-foreground)]">选择一个聊天查看消息</p>
          </div>
        ) : (
          <>
            <header className="shrink-0 border-b border-[var(--color-border)] bg-[var(--color-card)] px-5 py-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="flex min-w-0 items-center gap-3">
                  <PlatformAvatar platform={selectedChat.platform} name={selectedChat.chat_name || selectedChat.chat_id} />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h1 className="truncate text-lg font-semibold">{selectedChat.chat_name}</h1>
                      <Star className="h-4 w-4 fill-[var(--color-warning)] text-[var(--color-warning)]" />
                    </div>
                    <p className="text-xs text-[var(--color-muted-foreground)]">
                      {PLATFORM_LABEL[selectedChat.platform] || selectedChat.platform} · {selectedChat.chat_type === "group" ? "群聊" : "私聊"} · {selectedChat.msg_count.toLocaleString()} 条消息
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => void toggleFilterChat(selectedChat.chat_name)}
                    title="加入/移出分析名单"
                  >
                    <Pin className="h-4 w-4" />
                  </Button>
                  <Button variant="ghost" size="icon" title="提醒">
                    <Bell className="h-4 w-4" />
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setProfileOpen(true)}>
                    <BarChart3 className="mr-1.5 h-3.5 w-3.5" />
                    画像
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => analyze.run({ platform: selectedChat.platform, chat_id: selectedChat.chat_id })}
                    disabled={analyze.running}
                  >
                    {analyze.running ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Sparkles className="mr-1.5 h-3.5 w-3.5" />}
                    分析此聊天
                  </Button>
                  <Button variant="ghost" size="icon" title="更多">
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </header>

            <div className="flex-1 overflow-y-auto px-4 py-4 md:px-6">
              {loadingMsgs && messages.length === 0 ? (
                <div className="flex items-center justify-center py-10">
                  <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
                </div>
              ) : visibleMessages.length === 0 ? (
                <p className="py-10 text-center text-sm text-[var(--color-muted-foreground)]">暂无消息</p>
              ) : (
                <div className="mx-auto max-w-3xl space-y-3">
                  {visibleMessages.map(m => (
                    <button
                      key={m.id}
                      onClick={() => open(<MessageDetail m={m} />)}
                      className={`w-full rounded-xl border p-4 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md ${(m.urgency || 0) >= 4 ? "border-[var(--color-destructive)]/30 bg-[var(--color-destructive-subtle)]" : (m.urgency || 0) <= 2 && m.urgency ? "border-[var(--color-success)]/30 bg-[var(--color-success-subtle)]" : "border-[var(--color-border)] bg-[var(--color-card)]"}`}
                    >
                      <div className="flex items-start gap-3">
                        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--color-surface-3)] text-xs font-semibold text-[var(--color-foreground)]">
                          {(m.sender_name || "?").slice(0, 1)}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="mb-1 flex flex-wrap items-center gap-2">
                            <span className="text-sm font-semibold">{m.sender_name || "未知发送者"}</span>
                            <span className="rounded-md bg-[var(--color-success-subtle)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-success)]">
                              {PLATFORM_LABEL[m.platform] || m.platform}
                            </span>
                            {m.category && <CategoryBadge category={m.category} />}
                            {m.urgency && <UrgencyBadge urgency={m.urgency} />}
                            <span className="ml-auto text-xs text-[var(--color-muted-foreground)] tabular-nums">{formatTime(m.timestamp)}</span>
                          </div>
                          <p className="line-clamp-2 text-sm leading-relaxed text-[var(--color-foreground)]">{m.content}</p>
                          {m.summary && (
                            <div className="mt-2 inline-flex max-w-full items-center gap-1 rounded-md bg-[var(--color-primary-subtle)] px-2 py-1 text-xs text-[var(--color-primary)]">
                              <Bot className="h-3.5 w-3.5 shrink-0" />
                              <span className="truncate">AI 总结：{m.summary}</span>
                            </div>
                          )}
                        </div>
                        <Bookmark className="h-4 w-4 shrink-0 text-[var(--color-muted-foreground)]/60" />
                      </div>
                    </button>
                  ))}
                  {msgHasMore && (
                    <div className="flex justify-center py-3">
                      <Button variant="outline" size="sm" onClick={() => loadMessages(selectedChat, true)} disabled={loadingMsgs}>
                        加载更多
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="grid shrink-0 grid-cols-3 border-t border-[var(--color-border)] bg-[var(--color-card)] text-sm">
              <button className="flex items-center justify-center gap-2 border-r border-[var(--color-border)] px-4 py-3 font-medium text-[var(--color-primary)]">
                <MessageSquare className="h-4 w-4" />
                原始消息 <span className="text-[var(--color-muted-foreground)]">{messages.length.toLocaleString()}</span>
              </button>
              <button className="flex items-center justify-center gap-2 border-r border-[var(--color-border)] px-4 py-3 text-[var(--color-muted-foreground)]">
                <Sparkles className="h-4 w-4" />
                摘要 <span>{summaryCount.toLocaleString()}</span>
              </button>
              <button className="flex items-center justify-center gap-2 px-4 py-3 text-[var(--color-muted-foreground)]">
                <Hash className="h-4 w-4" />
                关键词命中 <span>{importantCount.toLocaleString()}</span>
              </button>
            </div>
          </>
        )}
      </main>

      <aside className="hidden w-[320px] shrink-0 flex-col gap-3 overflow-y-auto border-l border-[var(--color-border)] bg-[var(--color-background)] p-4 xl:flex">
        <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold">筛选条件</h2>
            <button onClick={() => setMessageSearch("")} className="text-xs font-medium text-[var(--color-primary)]">重置</button>
          </div>
          <div className="space-y-4">
            <div>
              <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-[var(--color-muted-foreground)]"><Hash className="h-3.5 w-3.5" />关键词筛选</p>
              <Input value={messageSearch} onChange={e => setMessageSearch(e.target.value)} placeholder="输入关键词" className="h-10 rounded-lg bg-[var(--color-background)]" />
              <div className="mt-2 flex flex-wrap gap-1.5">
                {["改版", "性能", "部署"].map(k => (
                  <button key={k} onClick={() => setMessageSearch(k)} className="rounded-full bg-[var(--color-secondary)] px-2.5 py-1 text-xs text-[var(--color-muted-foreground)] hover:bg-[var(--color-accent)]">{k}</button>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-[var(--color-muted-foreground)]"><CalendarDays className="h-3.5 w-3.5" />时间范围</p>
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-background)] px-3 py-2 text-sm text-[var(--color-muted-foreground)]">最近 7 天</div>
            </div>
            <div>
              <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-[var(--color-muted-foreground)]"><Filter className="h-3.5 w-3.5" />重要程度</p>
              <div className="flex gap-2 text-xs">
                <span className="rounded-full bg-[var(--color-primary-subtle)] px-2 py-1 text-[var(--color-primary)]">全部</span>
                <span className="rounded-full bg-[var(--color-destructive-subtle)] px-2 py-1 text-[var(--color-destructive)]">重要</span>
                <span className="rounded-full bg-[var(--color-warning-subtle)] px-2 py-1 text-[var(--color-warning)]">中等</span>
                <span className="rounded-full bg-[var(--color-success-subtle)] px-2 py-1 text-[var(--color-success)]">低</span>
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-sm">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Bot className="h-4 w-4 text-[var(--color-primary)]" />AI 观察</h2>
          <div className="space-y-3">
            <div className="rounded-xl bg-[var(--color-primary-subtle)] p-3">
              <p className="text-sm font-medium text-[var(--color-primary)]">高频话题</p>
              <p className="mt-1 text-xs leading-relaxed text-[var(--color-primary)]/75">当前页里共有 {summaryCount} 条摘要，重点集中在项目进度、技术方案和群内协作。</p>
            </div>
            <div className="rounded-xl bg-[var(--color-success-subtle)] p-3">
              <p className="flex items-center gap-2 text-sm font-medium text-[var(--color-success)]"><Users className="h-4 w-4" />活跃成员</p>
              <p className="mt-1 text-xs leading-relaxed text-[var(--color-success)]/75">当前加载消息涉及 {activeSenderCount} 位成员，可继续加载更多获得完整画像。</p>
            </div>
            <div className="rounded-xl bg-[var(--color-warning-subtle)] p-3">
              <p className="flex items-center gap-2 text-sm font-medium text-[var(--color-warning)]"><TrendingUp className="h-4 w-4" />情绪趋势</p>
              <p className="mt-1 text-xs leading-relaxed text-[var(--color-warning)]/75">重要消息 {importantCount} 条，建议优先处理带高紧急度标记的内容。</p>
            </div>
          </div>
        </section>

        <Button variant="outline" onClick={() => selectedChat && setProfileOpen(true)}>
          查看完整分析报告
          <Send className="ml-2 h-4 w-4" />
        </Button>
      </aside>

      {error && (
        <div className="fixed bottom-6 right-6 rounded-lg border border-[var(--color-destructive)]/30 bg-[var(--color-card)] px-4 py-3 text-sm text-[var(--color-destructive)] shadow-lg">
          {error}
        </div>
      )}

      {selectedChat && (
        <ChatProfileSheet
          open={profileOpen}
          platform={selectedChat.platform}
          chatId={selectedChat.chat_id}
          onClose={() => setProfileOpen(false)}
        />
      )}

      {analyze.modalProps.results && (
        <KnowledgeReviewModal
          taskId={analyze.modalProps.taskId}
          results={analyze.modalProps.results}
          summary={analyze.modalProps.summary}
          onClose={analyze.modalProps.onClose}
          onSaved={analyze.modalProps.onSaved}
        />
      )}
    </div>
  )
}
