import { useEffect, useState, useCallback, useRef } from "react"
import { useSearchParams } from "react-router-dom"
import { fetchAPI, getErrorMessage, type ChatInfo, type Message } from "@/lib/api"
import { formatTime } from "@/lib/utils"
import { UrgencyBadge, CategoryBadge } from "@/components/MessageBadges"
import { useDetailPanel } from "@/lib/DetailPanelContext"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Button } from "@/components/ui/button"
import { Sheet, SheetHeader } from "@/components/ui/sheet"
import { Search, Loader2, Menu } from "lucide-react"
import { PLATFORM_COLOR, PLATFORM_LABEL, PAGE_SIZE } from "@/lib/constants"

function MessageDetail({ m }: { m: Message }) {
  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs text-[var(--color-muted-foreground)] mb-1">{m.platform} · {m.chat_name}</p>
        <p className="text-sm font-medium">{m.sender_name}</p>
        <p className="text-xs text-[var(--color-muted-foreground)]">{formatTime(m.timestamp)}</p>
      </div>
      <div className="rounded-lg p-4 text-sm leading-relaxed whitespace-pre-wrap bg-[var(--color-secondary)]">
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
}

function ChatPicker({ chats, loading, selected, onSelect, search, setSearch, platform, setPlatform }: ChatPickerProps) {
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="p-3 border-b border-[var(--color-border)] space-y-2 shrink-0">
        <div className="relative">
          <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
          <Input
            placeholder="搜索聊天..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-8 h-8 text-sm"
          />
        </div>
        <Select value={platform} onChange={e => setPlatform(e.target.value)} className="w-full h-8 text-sm">
          <option value="">全部平台</option>
          <option value="wechat">微信</option>
          <option value="qq">QQ</option>
          <option value="telegram">Telegram</option>
        </Select>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
          </div>
        ) : chats.length === 0 ? (
          <p className="py-8 text-center text-sm text-[var(--color-muted-foreground)]">暂无聊天</p>
        ) : chats.map(c => {
          const isSelected = selected?.chat_id === c.chat_id && selected?.platform === c.platform
          return (
            <button
              key={`${c.platform}-${c.chat_id}`}
              onClick={() => onSelect(c)}
              className={`w-full text-left px-3 py-2.5 transition-colors border-b border-[var(--color-border)]/50 ${isSelected ? "bg-[var(--color-primary)]/10" : "hover:bg-[var(--color-accent)]"}`}
            >
              <div className="flex items-center gap-2 mb-0.5">
                <span className="text-sm font-medium truncate flex-1">{c.chat_name || c.chat_id}</span>
                {c.chat_type === "group" && <span className="text-xs text-[var(--color-muted-foreground)]">群</span>}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs" style={{ color: PLATFORM_COLOR[c.platform] || "var(--color-muted-foreground)" }}>
                  {PLATFORM_LABEL[c.platform] || c.platform}
                </span>
                <span className="text-xs text-[var(--color-muted-foreground)] tabular-nums">{c.msg_count.toLocaleString()} 条</span>
              </div>
            </button>
          )
        })}
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
  const [platform, setPlatform] = useState(searchParams.get("platform") || "")
  const [msgHasMore, setMsgHasMore] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const msgOffsetRef = useRef(0)

  useEffect(() => {
    let cancelled = false
    fetchAPI<ChatInfo[]>("/chats")
      .then(data => {
        if (cancelled) return
        setChats(data)
        const chatParam = searchParams.get("chat")
        if (chatParam) {
          const found = data.find(c => c.chat_name === chatParam)
          if (found) setSelectedChat(found)
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(getErrorMessage(e))
      })
      .finally(() => {
        if (!cancelled) setLoadingChats(false)
      })
    return () => { cancelled = true }
  }, [searchParams])

  const loadMessages = useCallback(async (chat: ChatInfo, append = false) => {
    setLoadingMsgs(true)
    try {
      const params = new URLSearchParams({ chat: chat.chat_name, limit: String(PAGE_SIZE) })
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

  const selectChat = (chat: ChatInfo) => {
    setSelectedChat(chat)
    setMessages([])
    msgOffsetRef.current = 0
    setPickerOpen(false)
    loadMessages(chat)
  }

  const filteredChats = chats.filter(c => {
    if (platform && c.platform !== platform) return false
    if (chatSearch && !c.chat_name.toLowerCase().includes(chatSearch.toLowerCase())) return false
    return true
  })

  return (
    <div className="flex h-full">
      {/* 桌面端侧栏 */}
      <div className="hidden md:flex w-64 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-sidebar)]">
        <ChatPicker
          chats={filteredChats}
          loading={loadingChats}
          selected={selectedChat}
          onSelect={selectChat}
          search={chatSearch}
          setSearch={setChatSearch}
          platform={platform}
          setPlatform={setPlatform}
        />
      </div>

      {/* 移动端 Sheet */}
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
        />
      </Sheet>

      <div className="flex flex-1 flex-col overflow-hidden">
        {/* 移动端顶部按钮 */}
        <div className="md:hidden flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-2 shrink-0">
          <button
            onClick={() => setPickerOpen(true)}
            className="flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-1.5 text-xs text-[var(--color-muted-foreground)]"
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
            <div className="px-4 md:px-6 py-4 border-b border-[var(--color-border)] shrink-0">
              <h2 className="text-base font-semibold">{selectedChat.chat_name}</h2>
              <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                {selectedChat.msg_count.toLocaleString()} 条消息 · {PLATFORM_LABEL[selectedChat.platform] || selectedChat.platform}
                {selectedChat.chat_type === "group" && " · 群聊"}
              </p>
            </div>
            <div className="flex-1 overflow-y-auto">
              {loadingMsgs && messages.length === 0 ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-4 w-4 animate-spin text-[var(--color-muted-foreground)]" />
                </div>
              ) : messages.length === 0 ? (
                <p className="py-8 text-center text-sm text-[var(--color-muted-foreground)]">暂无消息</p>
              ) : (
                <div>
                  {messages.map(m => (
                    <button
                      key={m.id}
                      onClick={() => open(<MessageDetail m={m} />)}
                      className="flex items-start gap-3 px-4 md:px-6 py-3 cursor-pointer transition-colors border-b border-[var(--color-border)]/30 hover:bg-[var(--color-card)] w-full text-left"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5">
                          <span className="text-sm font-medium">{m.sender_name}</span>
                          {m.category && <CategoryBadge category={m.category} />}
                          {m.urgency && <UrgencyBadge urgency={m.urgency} />}
                        </div>
                        <p className="text-sm text-[var(--color-muted-foreground)] truncate">{m.content}</p>
                        {m.summary && <p className="text-xs text-[var(--color-muted-foreground)]/70 truncate mt-0.5">{m.summary}</p>}
                      </div>
                      <span className="text-xs text-[var(--color-muted-foreground)] shrink-0 tabular-nums">{formatTime(m.timestamp)}</span>
                    </button>
                  ))}
                  {msgHasMore && (
                    <div className="flex justify-center py-4">
                      <Button variant="outline" size="sm" onClick={() => loadMessages(selectedChat, true)} disabled={loadingMsgs}>
                        加载更多
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {error && (
        <div className="fixed bottom-6 right-6 rounded-lg border border-[var(--color-destructive)]/30 bg-[var(--color-card)] px-4 py-3 text-sm text-[var(--color-destructive)]">
          {error}
        </div>
      )}
    </div>
  )
}
