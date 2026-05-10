import { useEffect, useState } from "react"
import { fetchAPI, type KnowledgeItem } from "@/lib/api"
import { useDetailPanel } from "@/lib/DetailPanelContext"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { formatTime } from "@/lib/utils"
import { Sparkles, Trash2, Search, Download, Pencil, Check, X } from "lucide-react"

function parseTags(raw: string): string[] {
  try {
    const parsed = JSON.parse(raw || "[]")
    return Array.isArray(parsed) ? parsed.map(String) : []
  } catch {
    return []
  }
}

function KnowledgeDetail({ item, extending, onExtend, onUpdate }: {
  item: KnowledgeItem
  extending: boolean
  onExtend: (id: number) => void
  onUpdate: (id: number, fields: { title?: string; content?: string; tags?: string[] }) => void
}) {
  const tags = parseTags(item.tags)
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(item.title)
  const [content, setContent] = useState(item.content)
  const [tagsInput, setTagsInput] = useState(tags.join(", "))

  const save = () => {
    onUpdate(item.id, {
      title,
      content,
      tags: tagsInput.split(",").map(t => t.trim()).filter(Boolean),
    })
    setEditing(false)
  }

  return (
    <div className="space-y-4">
      {editing ? (
        <>
          <Input value={title} onChange={e => setTitle(e.target.value)} className="text-sm font-medium" />
          <textarea
            value={content}
            onChange={e => setContent(e.target.value)}
            className="w-full rounded-lg p-3 text-sm leading-relaxed resize-none min-h-[120px] bg-secondary text-foreground border border-border"
          />
          <Input value={tagsInput} onChange={e => setTagsInput(e.target.value)} placeholder="标签（逗号分隔）" className="text-sm" />
          <div className="flex gap-2">
            <Button size="sm" onClick={save}><Check className="mr-1 h-3.5 w-3.5" />保存</Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(false)}><X className="mr-1 h-3.5 w-3.5" />取消</Button>
          </div>
        </>
      ) : (
        <>
          <div>
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-base font-semibold">{item.title}</h3>
              <button onClick={() => setEditing(true)} aria-label="编辑" className="p-1 rounded text-muted-foreground hover:text-foreground transition-colors">
                <Pencil className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              {tags.map(t => <Badge key={t} variant="outline" className="text-xs">{t}</Badge>)}
              <span className="text-xs text-muted-foreground">{item.source_chat}</span>
            </div>
          </div>
          <div className="rounded-lg p-4 text-sm leading-relaxed bg-secondary">
            {item.content}
          </div>
          {item.extended_content ? (
            <div className="text-sm leading-relaxed">
              <p className="text-xs text-muted-foreground mb-2">AI 扩展</p>
              <div className="whitespace-pre-wrap">{item.extended_content}</div>
            </div>
          ) : (
            <Button variant="outline" size="sm" onClick={() => onExtend(item.id)} disabled={extending}>
              <Sparkles className="mr-1.5 h-3.5 w-3.5" /> {extending ? "扩展中..." : "用 AI 扩展知识"}
            </Button>
          )}
          <p className="text-xs text-muted-foreground">{formatTime(item.created_at)}</p>
        </>
      )}
    </div>
  )
}

export default function Knowledge() {
  const { open } = useDetailPanel()
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState("")
  const [activeTag, setActiveTag] = useState("")
  const [extending, setExtending] = useState<number | null>(null)

  const allTags = Array.from(new Set(
    items.flatMap(i => parseTags(i.tags))
  ))

  const load = async (query = "") => {
    try {
      const params = new URLSearchParams()
      if (query) params.set("q", query)
      const data = await fetchAPI<KnowledgeItem[]>(`/knowledge?${params}`)
      setItems(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const extend = async (id: number) => {
    setExtending(id)
    try {
      const res = await fetchAPI<{ extended_content: string }>(`/knowledge/${id}/extend`, { method: "POST" })
      setItems(prev => prev.map(i => i.id === id ? { ...i, extended_content: res.extended_content } : i))
      const updated = items.find(i => i.id === id)
      if (updated) {
        open(<KnowledgeDetail item={{ ...updated, extended_content: res.extended_content }} extending={false} onExtend={extend} onUpdate={update} />)
      }
    } finally {
      setExtending(null)
    }
  }

  const update = async (id: number, fields: { title?: string; content?: string; tags?: string[] }) => {
    await fetchAPI(`/knowledge/${id}`, { method: "PATCH", body: JSON.stringify(fields) })
    setItems(prev => prev.map(i => {
      if (i.id !== id) return i
      return { ...i, ...fields, tags: fields.tags ? JSON.stringify(fields.tags) : i.tags }
    }))
  }

  const remove = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    await fetchAPI(`/knowledge/${id}`, { method: "DELETE" })
    setItems(prev => prev.filter(i => i.id !== id))
  }

  const exportMd = () => {
    window.open("/api/knowledge/export?fmt=markdown", "_blank")
  }

  const filtered = activeTag
    ? items.filter(i => parseTags(i.tags).includes(activeTag))
    : items

  return (
    <div className="p-8 max-w-4xl">
      <div className="flex items-center gap-3 mb-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input className="pl-10" placeholder="搜索知识库..." value={q}
            onChange={e => setQ(e.target.value)} onKeyDown={e => {
              if (e.key === "Enter") {
                setLoading(true)
                void load(q)
              }
            }} />
        </div>
        <Button variant="outline" size="sm" onClick={() => { setLoading(true); void load(q) }}>搜索</Button>
        <Button variant="outline" size="sm" onClick={exportMd}>
          <Download className="mr-1.5 h-3.5 w-3.5" /> 导出 MD
        </Button>
      </div>

      {allTags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          <button
            onClick={() => setActiveTag("")}
            className={`text-xs px-2 py-1 rounded-full transition-colors ${!activeTag ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground hover:bg-accent"}`}
          >全部</button>
          {allTags.map(t => (
            <button key={t} onClick={() => setActiveTag(t === activeTag ? "" : t)}
              className={`text-xs px-2 py-1 rounded-full transition-colors ${activeTag === t ? "bg-primary text-primary-foreground" : "bg-secondary text-muted-foreground hover:bg-accent"}`}
            >{t}</button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="space-y-2">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-20 rounded-lg animate-pulse bg-card" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-center text-muted-foreground py-16 text-sm">暂无知识点。运行分析后筛选保存。</p>
      ) : (
        <div className="space-y-2">
          {filtered.map(item => {
            const tags = parseTags(item.tags)
            return (
              <div key={item.id}
                onClick={() => open(<KnowledgeDetail item={item} extending={extending === item.id} onExtend={extend} onUpdate={update} />)}
                className="group flex items-start gap-4 rounded-lg p-4 cursor-pointer transition-colors bg-card hover:bg-secondary"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-medium">{item.title}</span>
                    {item.extended_content && <Sparkles className="h-3 w-3 text-muted-foreground" />}
                    {tags.map(t => <Badge key={t} variant="outline" className="text-xs">{t}</Badge>)}
                  </div>
                  <p className="text-sm text-muted-foreground truncate">{item.content}</p>
                  <p className="text-xs text-muted-foreground mt-1 opacity-60">{item.source_chat} · {formatTime(item.created_at)}</p>
                </div>
                <button onClick={e => remove(item.id, e)} aria-label="删除"
                  className="opacity-0 group-hover:opacity-100 p-1 rounded transition-opacity text-muted-foreground hover:text-destructive">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
