import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { BarChart3, Bot, CloudOff, Cloud, Loader2, Menu, ListFilter, Search, Sparkles } from "lucide-react"
import { chatRefKey, fetchAPI, getErrorMessage, updateWechatArchive, type ChatInfo, type Config, type Message } from "@/lib/api"
import { formatTime } from "@/lib/utils"
import { CategoryBadge, UrgencyBadge } from "@/components/MessageBadges"
import { ChatProfileSheet } from "@/components/ChatProfileSheet"
import { KnowledgeReviewModal } from "@/components/KnowledgeReviewModal"
import { useDetailPanel } from "@/lib/DetailPanelContext"
import { useTaskRunner } from "@/hooks/useTaskRunner"
import { useAnalyzeRunner } from "@/hooks/useAnalyzeRunner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Sheet, SheetHeader } from "@/components/ui/sheet"
import { PAGE_SIZE, PLATFORM_LABEL } from "@/lib/constants"
import { ConversationMark, ConversationMeta, ConversationSource } from "@/components/ConversationIdentity"
import { conversationKind, conversationName, findDuplicateConversationNames } from "@/lib/conversation"

import { WechatArchivePanel } from "@/components/WechatArchivePanel"
import { WechatArticles } from "@/components/WechatArticles"

function MessageDetail({ m }: { m: Message }) {
  return (
    <div className="space-y-4">
      <div>
        <ConversationSource chat={m} />
        <p className="text-sm font-medium">{m.sender_name || m.sender_id || "未知发送者"}</p>
        <p className="text-xs text-[var(--color-muted-foreground)]">{formatTime(m.timestamp)}</p>
      </div>
      {m.article_links?.length ? <WechatArticles key={m.id} messageId={m.id} /> : <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card-elevated)] p-4 text-sm leading-relaxed whitespace-pre-wrap">
        {m.content}
      </div>}
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
  kind: string
  setKind: (v: string) => void
  excluded: Set<string>
  duplicateNames: Set<string>
  batch: {
    keys: Set<string> | null
    ready: boolean
    saving: boolean
    toggleMode: () => void
    selectKeys: (keys: string[]) => void
    toggleChat: (key: string) => void
    submit: () => void
    repairQQ?: () => void
    repairingQQ: boolean
    canArchive: boolean
    canStop: boolean
    archive: () => void
    showArchive: () => void
  }
}

function ChatPicker({ chats, loading, selected, onSelect, search, setSearch, platform, setPlatform, kind, setKind, excluded, duplicateNames, batch }: ChatPickerProps) {
  const eligible = (c: ChatInfo) => !excluded.has(chatRefKey(c)) || (kind === "公众号" && conversationKind(c) === "公众号")
  const selectableKeys = batch.keys ? chats.filter(eligible).map(chatRefKey) : []
  const selectedHere = selectableKeys.filter(key => batch.keys?.has(key)).length
  return <div className="flex h-full min-h-0 w-full flex-col bg-card">
    <div className="border-b border-border p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-lg font-semibold">会话列表</h2>
        <span className="ml-auto mr-2 text-xs tabular-nums text-muted-foreground">{loading ? "读取中" : `${chats.length.toLocaleString()} 个会话`}</span>
        <Button size="sm" variant="outline" disabled={!batch.ready || batch.saving} aria-pressed={batch.keys !== null} onClick={batch.toggleMode}>
          {batch.keys ? "取消多选" : "多选"}
        </Button>
      </div>
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
        <Input aria-label="搜索会话" placeholder="搜索群名、人名或 ID" value={search} onChange={e => setSearch(e.target.value)} className="h-10 w-full pl-9" />
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <Select aria-label="筛选平台" value={platform} onChange={e => setPlatform(e.target.value)} className="w-full">
          <option value="">全部平台</option><option value="wechat">微信</option><option value="qq">QQ</option><option value="telegram">Telegram</option>
        </Select>
        <Select aria-label="筛选会话类型" value={kind} onChange={e => setKind(e.target.value)} className="w-full">
          <option value="">全部类型</option>{["群聊","私聊","频道","公众号"].map(k => <option key={k}>{k}</option>)}
        </Select>
      </div>
      <button className="mt-2 text-xs font-medium text-primary hover:underline" onClick={batch.showArchive}>公众号全文任务</button>
      {batch.keys && <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <Button size="sm" variant="outline" disabled={batch.saving || selectedHere === selectableKeys.length}
          onClick={() => batch.selectKeys(selectableKeys)}>全选当前筛选结果</Button>
        <span className="text-xs tabular-nums text-muted-foreground">已选 {selectedHere} / 可选 {selectableKeys.length}</span>
      </div>}
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto" aria-label="会话">
      {loading ? <Loader2 aria-label="加载会话" className="mx-auto my-8 h-5 w-5 animate-spin" /> :
        chats.length === 0 ? <p className="p-5 text-sm text-muted-foreground">没有匹配的会话</p> :
        chats.map(c => {
          const key = chatRefKey(c)
          const active = selected && key === chatRefKey(selected)
          const name = conversationName(c)
          const identity = <>
            <ConversationMark chat={c} />
            <span className="min-w-0 flex-1">
              <strong className="conversation-name">{name}</strong>
              <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                <ConversationMeta chat={c} /><span>{c.msg_count.toLocaleString()} 条</span>
                {c.latest && <time className="ml-auto" dateTime={c.latest} title={formatTime(c.latest)}>{c.latest.slice(5, 10)}</time>}
                {excluded.has(key) && <span className="font-medium text-warning">已停止同步</span>}
              </span>
              {(!c.chat_name?.trim() || c.chat_name.trim() === c.chat_id || duplicateNames.has(c.platform + "|" + name)) && <span className="conversation-id">{c.chat_id}</span>}
            </span>
          </>
          if (batch.keys) {
            const checked = batch.keys.has(key)
            const disabled = !eligible(c) || batch.saving
            return <label key={key} data-checked={checked} className={`conversation-row ${disabled ? "cursor-default" : "cursor-pointer"}`}>
              <input type="checkbox" checked={checked} disabled={disabled} onChange={() => batch.toggleChat(key)}
                className="mt-2 h-4 w-4 shrink-0 accent-primary" />{identity}
            </label>
          }
          return <button key={key} onClick={() => onSelect(c)} aria-current={active ? "true" : undefined}
            className="conversation-row" title={c.chat_name || c.chat_id}>{identity}</button>
        })}
    </div>
    {batch.keys && <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-border bg-card p-3">
      <span aria-live="polite" className="text-sm tabular-nums">已选 {batch.keys.size} 个会话</span>
      {batch.repairQQ && <Button size="sm" variant="outline" disabled={batch.repairingQQ} onClick={batch.repairQQ}>补查所选 QQ 历史</Button>}
      {batch.canArchive && <Button size="sm" disabled={batch.saving} onClick={batch.archive}>保存历史并自动保存新文章</Button>}
      <Button size="sm" variant="outline" disabled={!batch.canStop || batch.saving} onClick={batch.submit}>
        {batch.saving ? "正在保存…" : "批量停止同步"}
      </Button>
    </div>}
  </div>
}

export default function Chats() {
  const [searchParams, setSearchParams] = useSearchParams()
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
  const [kind, setKind] = useState("")
  const [msgHasMore, setMsgHasMore] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [filterMode, setFilterMode] = useState("whitelist")
  const [filterChats, setFilterChats] = useState<Set<string>>(new Set())
  const [legacyNames, setLegacyNames] = useState<Set<string>>(new Set())
  const [syncExcluded, setSyncExcluded] = useState<Set<string>>(new Set())
  const [configReady, setConfigReady] = useState(false)
  const [batchKeys, setBatchKeys] = useState<Set<string> | null>(null)
  const [savingScope, setSavingScope] = useState(false)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)
  const msgOffsetRef = useRef(0)
  const msgReqRef = useRef(0)
  const analyze = useAnalyzeRunner()
  const [localQQ, setLocalQQ] = useState(false)
  const syncRunner = useTaskRunner()

  const loadMessages = useCallback(async (chat: ChatInfo, append = false) => {
    const reqId = ++msgReqRef.current
    setLoadingMsgs(true)
    try {
      const params = new URLSearchParams({
        platform: chat.platform,
        chat_id: chat.chat_id,
        limit: String(PAGE_SIZE + 1),
      })
      if (append) params.set("offset", String(msgOffsetRef.current))
      const data = await fetchAPI<Message[]>(`/messages?${params}`)
      if (reqId !== msgReqRef.current) return
      const page = data.slice(0, PAGE_SIZE)
      if (append) {
        setMessages(prev => [...prev, ...page])
        msgOffsetRef.current += page.length
      } else {
        setMessages(page)
        msgOffsetRef.current = page.length
      }
      setMsgHasMore(data.length > PAGE_SIZE)
    } catch (e: unknown) {
      if (reqId === msgReqRef.current) setError(getErrorMessage(e))
    } finally {
      if (reqId === msgReqRef.current) setLoadingMsgs(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchAPI<ChatInfo[]>("/chats")
      .then(data => { if (!cancelled) setChats(data) })
      .catch((e: unknown) => {
        if (!cancelled) setError(getErrorMessage(e))
      })
      .finally(() => {
        if (!cancelled) setLoadingChats(false)
      })
    return () => { cancelled = true }
  }, [])

  // Selection is URL-driven (?platform=&chat_id=) so deep links and back/forward work.
  useEffect(() => {
    if (chats.length === 0) return
    const platformParam = searchParams.get("platform")
    const chatIdParam = searchParams.get("chat_id")
    const chatParam = searchParams.get("chat")
    const found = platformParam && chatIdParam
      ? chats.find(c => c.platform === platformParam && c.chat_id === chatIdParam)
      : chatParam
        ? chats.find(c => c.chat_name === chatParam)
        : chats[0]
    if (!found) return
    if (selectedChat && selectedChat.platform === found.platform && selectedChat.chat_id === found.chat_id) return
    setSelectedChat(found)
    setMessages([])
    setMessageSearch("")
    msgOffsetRef.current = 0
    void loadMessages(found)
  }, [chats, searchParams, selectedChat, loadMessages])

  useEffect(() => {
    let cancelled = false
    fetchAPI<Config>("/config").then(cfg => {
      if (cancelled) return
      setFilterMode(cfg.chat_filter.mode)
      // Scope is keyed on (platform, chat_id); legacy names are matched by name until migrated.
      const keys = new Set((cfg.chat_filter.refs || []).map(chatRefKey))
      setFilterChats(keys)
      setLegacyNames(new Set(cfg.chat_filter.chats || []))
      setSyncExcluded(new Set((cfg.sync_scope?.exclude_chats || []).map(chatRefKey)))
      setLocalQQ(cfg.qq.enabled && cfg.qq.source === "local")
      setConfigReady(true)
    }).catch(() => { /* browsing can work without config */ })
    return () => { cancelled = true }
  }, [])

  const toggleFilterChat = useCallback(async (chat: ChatInfo) => {
    const key = chatRefKey(chat)
    const ref = { platform: chat.platform, chat_id: chat.chat_id }
    const inList = filterChats.has(key) || legacyNames.has(chat.chat_name)
    setFilterChats(prev => {
      const next = new Set(prev)
      if (inList) next.delete(key); else next.add(key)
      return next
    })
    try {
      const body = inList
        ? { remove_chat_ref: ref, ...(legacyNames.has(chat.chat_name) ? { remove_chat: chat.chat_name } : {}) }
        : { add_chat_ref: ref }
      const cfg = await fetchAPI<Config>("/config", { method: "PUT", body: JSON.stringify(body) })
      setLegacyNames(new Set(cfg.chat_filter.chats || []))
    } catch (e) {
      setFilterChats(prev => {
        const next = new Set(prev)
        if (inList) next.add(key); else next.delete(key)
        return next
      })
      toast.error(getErrorMessage(e))
    }
  }, [filterChats, legacyNames])

  const toggleSyncExclude = useCallback(async (chat: ChatInfo) => {
    const key = chatRefKey(chat)
    const ref = { platform: chat.platform, chat_id: chat.chat_id }
    const excluded = syncExcluded.has(key)
    if (!excluded && !confirm(`以后不再同步「${chat.chat_name || chat.chat_id}」？已入库的消息会保留；要删除历史可到 设置 → 备份 → 存储。`)) return
    try {
      setSavingScope(true)
      const cfg = await fetchAPI<Config>("/config", { method: "PUT", body: JSON.stringify(excluded ? { remove_sync_exclude: ref } : { add_sync_exclude: ref }) })
      setSyncExcluded(new Set((cfg.sync_scope?.exclude_chats || []).map(chatRefKey)))
      toast.success(excluded ? "已恢复同步" : "已加入不同步列表")
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally { setSavingScope(false) }
  }, [syncExcluded])

  const stopSelected = async () => {
    if (!batchKeys || savingScope) return
    const targets = chats.filter(c => batchKeys.has(chatRefKey(c)) && !syncExcluded.has(chatRefKey(c)))
    if (!targets.length) return
    const names = targets.map(c => `${PLATFORM_LABEL[c.platform] || c.platform} · ${conversationName(c)}${duplicateNames.has(c.platform + "|" + conversationName(c)) || !c.chat_name || c.chat_name === c.chat_id ? " · " + c.chat_id : ""}`)
    if (!window.confirm(`停止同步以下 ${targets.length} 个会话？从下次同步起生效，已有消息会保留。\n\n${names.join("\n")}`)) return
    setSavingScope(true)
    try {
      const refs = targets.map(({ platform, chat_id }) => ({ platform, chat_id }))
      const cfg = await fetchAPI<Config>("/config", { method: "PUT", body: JSON.stringify({ add_sync_excludes: refs }) })
      const saved = new Set((cfg.sync_scope?.exclude_chats || []).map(chatRefKey))
      setSyncExcluded(saved)
      const missing = targets.map(chatRefKey).filter(key => !saved.has(key))
      if (missing.length) {
        setBatchKeys(new Set(missing))
        throw new Error(`还有 ${missing.length} 个会话未保存，可重试`)
      }
      setBatchKeys(null)
      toast.success(`已停止同步 ${targets.length} 个会话`)
    } catch (e) {
      toast.error(getErrorMessage(e))
    } finally {
      setSavingScope(false)
    }
  }

  const picked = batchKeys ? chats.filter(c => batchKeys.has(chatRefKey(c))) : []
  const canArchive = picked.length > 0 && picked.every(c => conversationKind(c) === "公众号")
  const showArchive = () => { setPickerOpen(false); setArchiveOpen(true) }

  const archiveSelected = async () => {
    if (!canArchive || savingScope) return
    setSavingScope(true)
    try {
      await updateWechatArchive(picked.map(c => c.chat_id), "enable")
      setBatchKeys(null)
      showArchive()
      toast.success(`已为 ${picked.length} 个公众号开启全文保存`)
    } catch (e) { toast.error(getErrorMessage(e)) }
    finally { setSavingScope(false) }
  }

  const batch: ChatPickerProps["batch"] = {
    repairingQQ: syncRunner.running.has("sync_qq"),
    repairQQ: localQQ && picked.length > 0 && picked.every(c => c.platform === "qq" && !syncExcluded.has(chatRefKey(c)))
      ? () => void syncRunner.run({
          start: () => fetchAPI("/sync/qq", { method: "POST", body: JSON.stringify({ mode: "repair", chat_ids: picked.map(c => c.chat_id) }) }),
          taskType: "sync_qq", loadingText: "正在补查所选 QQ 会话…",
        }) : undefined,
    canArchive, canStop: picked.some(c => !syncExcluded.has(chatRefKey(c))), archive: () => void archiveSelected(), showArchive,
    keys: batchKeys, ready: configReady, saving: savingScope,
    toggleMode: () => setBatchKeys(prev => prev ? null : new Set()),
    selectKeys: keys => setBatchKeys(prev => prev && new Set([...prev, ...keys])),
    toggleChat: key => setBatchKeys(prev => {
      if (!prev) return prev
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    }),
    submit: () => void stopSelected(),
  }

  const toggleFilterMode = useCallback(async () => {
    const prevMode = filterMode
    const next = prevMode === "whitelist" ? "blacklist" : "whitelist"
    setFilterMode(next)
    try {
      await fetchAPI<Config>("/config", {
        method: "PUT",
        body: JSON.stringify({ filter_mode: next }),
      })
      toast.success(`已切换到${next === "whitelist" ? "白名单" : "黑名单"}模式`)
    } catch (e) {
      setFilterMode(m => (m === next ? prevMode : m))
      toast.error(getErrorMessage(e))
    }
  }, [filterMode])

  const selectChat = (chat: ChatInfo) => {
    setPickerOpen(false)
    setSearchParams(prev => {
      const p = new URLSearchParams(prev)
      p.set("platform", chat.platform)
      p.set("chat_id", chat.chat_id)
      p.delete("chat")
      return p
    }, { replace: true })
  }

  const filteredChats = chats.filter(c => {
    if (platform && c.platform !== platform) return false
    if (kind && conversationKind(c) !== kind) return false
    const term = chatSearch.trim().toLowerCase()
    if (term && ![c.chat_name, c.chat_id].some(v => v?.toLowerCase().includes(term))) return false
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

  const duplicateNames = findDuplicateConversationNames(chats)
  const excluded = selectedChat && syncExcluded.has(chatRefKey(selectedChat))
  const listed = selectedChat && (filterChats.has(chatRefKey(selectedChat)) || legacyNames.has(selectedChat.chat_name))
  const modeLabel = filterMode === "blacklist" ? "分析黑名单" : "分析白名单"

  return (
    <div className="flex h-full overflow-hidden bg-[var(--color-background)]">
      <div className="hidden w-[clamp(290px,27vw,410px)] shrink-0 border-r border-border lg:flex">
        <ChatPicker
          chats={filteredChats}
          loading={loadingChats}
          selected={selectedChat}
          onSelect={selectChat}
          search={chatSearch}
          setSearch={setChatSearch}
          platform={platform}
          setPlatform={setPlatform}
          kind={kind} setKind={setKind} excluded={syncExcluded} duplicateNames={duplicateNames} batch={batch}
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
          kind={kind} setKind={setKind} excluded={syncExcluded} duplicateNames={duplicateNames} batch={batch}
        />
      </Sheet>

      {archiveOpen && <WechatArchivePanel chats={chats} onClose={() => setArchiveOpen(false)} />}

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--color-background)]/60">
        <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-2 lg:hidden">
          <button
            onClick={() => setPickerOpen(true)}
            className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-1.5 text-xs text-[var(--color-muted-foreground)]"
          >
            <Menu className="h-3.5 w-3.5" />
            切换会话
          </button>
        </div>

        {!selectedChat ? (
          <div className="flex flex-1 items-center justify-center px-6 text-center">
            <p className="text-sm text-[var(--color-muted-foreground)]">选择一个聊天查看消息</p>
          </div>
        ) : (
          <>
            <header className="max-h-[43%] shrink-0 overflow-y-auto border-b border-border bg-card px-4 py-3 lg:px-6">
              <div className="flex items-start gap-3">
                <ConversationMark chat={selectedChat} />
                <div className="min-w-0 flex-1">
                  <h1 className="break-words text-xl font-semibold leading-snug">{conversationName(selectedChat)}</h1>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    <ConversationMeta chat={selectedChat} /><span>{selectedChat.msg_count.toLocaleString()} 条消息</span>
                    {configReady && <span className={excluded ? "text-warning" : ""}>{excluded ? "已停止同步" : "已启用同步"}</span>}
                  </div>
                  <details className="mt-1 text-xs text-muted-foreground"><summary className="w-fit cursor-pointer">会话 ID</summary><span className="conversation-id">{selectedChat.chat_id}</span></details>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button size="sm" variant="outline" disabled={!configReady || savingScope || batchKeys !== null} onClick={() => void toggleSyncExclude(selectedChat)}>
                  {excluded ? <Cloud size={15} /> : <CloudOff size={15} />}{excluded ? "恢复同步" : "停止同步"}
                </Button>
                <Button size="sm" variant="outline" onClick={() => setProfileOpen(true)}><BarChart3 size={15} />会话画像</Button>
                <Button size="sm" variant="outline" disabled={analyze.running} onClick={() => analyze.run({ platform: selectedChat.platform, chat_id: selectedChat.chat_id })}><Sparkles size={15} />分析此聊天</Button>
              </div>
              <details className="mt-2 text-xs text-muted-foreground">
                <summary className="w-fit cursor-pointer py-1">批量分析名单</summary>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span>{modeLabel} · {listed ? "此会话在名单内" : "此会话未入名单"}</span>
                  <Button size="sm" variant="outline" disabled={!configReady} onClick={() => void toggleFilterChat(selectedChat)}>{listed ? "移出" : "加入"}{modeLabel}</Button>
                  <Button size="sm" variant="ghost" disabled={!configReady} onClick={() => void toggleFilterMode()}>切换到{filterMode === "blacklist" ? "分析白名单" : "分析黑名单"}</Button>
                </div>
              </details>
            </header>

            <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2 text-muted-foreground">
              <ListFilter size={16} /><Input aria-label="筛选已加载消息" placeholder="在已加载消息中查正文或发言人" value={messageSearch} onChange={e => setMessageSearch(e.target.value)} className="h-9 max-w-md bg-card" />
            </div>
            <div className="flex-1 overflow-y-auto px-4 py-4 md:px-6">
              {loadingMsgs && messages.length === 0 ? (
                <div className="flex items-center justify-center py-10">
                  <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
                </div>
              ) : visibleMessages.length === 0 && !msgHasMore ? (
                <p className="py-10 text-center text-sm text-muted-foreground">{messageSearch ? "已加载消息中没有匹配项" : "暂无消息"}</p>
              ) : (
                <div className="mx-auto max-w-3xl space-y-3">
                  {!visibleMessages.length && <p className="py-4 text-center text-sm text-muted-foreground">已加载消息中没有匹配项</p>}
                  {visibleMessages.map(m => (
                    <button
                      key={m.id}
                      onClick={() => open(<MessageDetail m={m} />)}
                      className={`w-full rounded-xl border p-4 text-left transition-colors hover:border-[var(--color-border-strong)] ${(m.urgency || 0) >= 4 ? "border-[var(--color-destructive)]/30 bg-[var(--color-destructive-subtle)]" : (m.urgency || 0) <= 2 && m.urgency ? "border-[var(--color-success)]/30 bg-[var(--color-success-subtle)]" : "border-[var(--color-border)] bg-[var(--color-card)]"}`}
                    >
                      <div className="flex items-start gap-3">
                        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--color-surface-3)] text-xs font-semibold text-[var(--color-foreground)]">
                          {Array.from(m.sender_name || m.sender_id || "?")[0]}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="mb-1 flex flex-wrap items-center gap-2">
                            <span className="text-sm font-semibold">{m.sender_name || m.sender_id || "未知发送者"}</span>

                            {m.category && <CategoryBadge category={m.category} />}
                            {m.urgency && <UrgencyBadge urgency={m.urgency} />}
                            <span className="ml-auto text-xs text-[var(--color-muted-foreground)] tabular-nums">{formatTime(m.timestamp)}</span>
                          </div>
                          <p className="line-clamp-4 whitespace-pre-wrap break-words text-base leading-relaxed text-[var(--color-foreground)]">{m.article_links?.length ? m.article_links.map(a => a.title).join("\n") : m.content}</p>
                          {!!m.article_links?.length && <span className="mt-2 inline-block text-xs text-primary">{m.article_links.length} 篇文章 · 阅读与保存全文</span>}
                          {m.summary && (
                            <div className="mt-2 inline-flex max-w-full items-center gap-1 rounded-md bg-[var(--color-primary-subtle)] px-2 py-1 text-xs text-[var(--color-primary)]">
                              <Bot className="h-3.5 w-3.5 shrink-0" />
                              <span className="truncate">AI 总结：{m.summary}</span>
                            </div>
                          )}
                        </div>

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

          </>
        )}
      </main>

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
