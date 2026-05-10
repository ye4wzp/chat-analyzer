import { useState, useRef } from "react"
import { fetchAPI, getErrorMessage, type Message, type AnalysisResult } from "@/lib/api"
import { formatTime } from "@/lib/utils"
import { useDetailPanel } from "@/lib/DetailPanelContext"
import { UrgencyBadge, CategoryBadge } from "@/components/MessageBadges"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Select } from "@/components/ui/select"
import { Search as SearchIcon } from "lucide-react"
import { PAGE_SIZE } from "@/lib/constants"

type Result = Message & Partial<AnalysisResult>

function highlight(text: string, kw: string) {
  if (!kw) return text
  const parts = text.split(new RegExp(`(${kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"))
  return parts.map((p, i) =>
    p.toLowerCase() === kw.toLowerCase()
      ? <mark key={i} className="rounded px-0.5 bg-accent text-foreground">{p}</mark>
      : p
  )
}

function MessageDetail({ m, kw }: { m: Result; kw: string }) {
  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs text-muted-foreground mb-1">{m.platform} · {m.chat_name}</p>
        <p className="text-sm font-medium">{m.sender_name}</p>
        <p className="text-xs text-muted-foreground">{formatTime(m.timestamp)}</p>
      </div>
      <div className="rounded-lg p-4 text-sm leading-relaxed whitespace-pre-wrap bg-secondary">
        {highlight(m.content, kw)}
      </div>
      {(m.category || m.urgency || m.summary) && (
        <div className="space-y-2 border-t border-border pt-4">
          <div className="flex items-center gap-2">
            <CategoryBadge category={m.category} />
            <UrgencyBadge urgency={m.urgency} />
          </div>
          {m.summary && <p className="text-sm text-muted-foreground">{m.summary}</p>}
        </div>
      )}
    </div>
  )
}

export default function Search() {
  const { open } = useDetailPanel()
  const [results, setResults] = useState<Result[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [hasMore, setHasMore] = useState(false)
  const [keyword, setKeyword] = useState("")
  const [searchedKeyword, setSearchedKeyword] = useState("")
  const [filters, setFilters] = useState({ platform: "", category: "", since: "", until: "" })
  const offsetRef = useRef(0)

  const search = async (append = false) => {
    if (!keyword.trim()) return
    setLoading(true)
    setError("")
    try {
      const params = new URLSearchParams({ keyword })
      if (filters.platform) params.set("platform", filters.platform)
      if (filters.category) params.set("category", filters.category)
      if (filters.since) params.set("since", filters.since)
      if (filters.until) params.set("until", filters.until)
      params.set("offset", String(append ? offsetRef.current : 0))
      params.set("limit", String(PAGE_SIZE))
      const data = await fetchAPI<Result[]>(`/search?${params}`)
      if (append) {
        setResults(prev => [...prev, ...data])
        offsetRef.current += data.length
      } else {
        setResults(data)
        offsetRef.current = data.length
        setSearchedKeyword(keyword)
      }
      setHasMore(data.length === PAGE_SIZE)
    } catch (e: unknown) {
      setError(getErrorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  const set = (key: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setFilters(f => ({ ...f, [key]: e.target.value }))

  return (
    <div className="p-8 max-w-4xl">
      <div className="mb-6">
        <div className="relative mb-3">
          <SearchIcon className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            className="pl-10 h-10 text-base"
            placeholder="搜索消息内容..."
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onKeyDown={e => e.key === "Enter" && search()}
          />
        </div>
        <div className="flex items-center gap-2">
          <Select value={filters.platform} onChange={set("platform")} className="h-8 text-sm">
            <option value="">全部平台</option>
            <option value="wechat">微信</option>
            <option value="qq">QQ</option>
            <option value="telegram">Telegram</option>
          </Select>
          <Select value={filters.category} onChange={set("category")} className="h-8 text-sm">
            <option value="">全部分类</option>
            <option value="important">重要</option>
            <option value="todo">待办</option>
            <option value="casual">闲聊</option>
          </Select>
          <Input type="date" value={filters.since} onChange={set("since")} className="h-8 w-36 text-sm" />
          <Input type="date" value={filters.until} onChange={set("until")} className="h-8 w-36 text-sm" />
          <Button onClick={() => search()} disabled={loading || !keyword.trim()} size="sm">
            {loading ? "搜索中..." : "搜索"}
          </Button>
        </div>
      </div>

      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}

      {results.length > 0 && (
        <div>
          <p className="mb-3 text-sm text-muted-foreground">{results.length} 个结果</p>
          <div className="space-y-0">
            {results.map(r => (
              <div
                key={r.id}
                onClick={() => open(<MessageDetail m={r} kw={searchedKeyword} />)}
                className="flex items-start gap-3 py-3 px-3 rounded-lg cursor-pointer transition-colors border-b border-border/30 hover:bg-card"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-sm font-medium">{r.sender_name}</span>
                    <span className="text-xs text-muted-foreground">{r.chat_name}</span>
                    <CategoryBadge category={r.category} />
                    <UrgencyBadge urgency={r.urgency} />
                  </div>
                  <p className="text-sm text-muted-foreground truncate">{highlight(r.content, searchedKeyword)}</p>
                </div>
                <span className="text-xs text-muted-foreground shrink-0">{formatTime(r.timestamp)}</span>
              </div>
            ))}
          </div>
          {hasMore && (
            <div className="mt-4 flex justify-center">
              <Button variant="outline" size="sm" onClick={() => search(true)} disabled={loading}>加载更多</Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
