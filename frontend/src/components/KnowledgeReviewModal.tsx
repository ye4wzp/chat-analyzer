import { useState, useEffect } from "react"
import { fetchAPI, getErrorMessage, type PendingKnowledge } from "@/lib/api"
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { X } from "lucide-react"

interface Props {
  taskId: string
  results: PendingKnowledge[]
  summary?: string
  onClose: () => void
  onSaved: () => void
}

export function KnowledgeReviewModal({ taskId, results, summary, onClose, onSaved }: Props) {
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(results.map((_, i) => i))
  )
  const [error, setError] = useState("")
  const [saving, setSaving] = useState(false)

  const toggle = (i: number) =>
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })

  const save = async () => {
    setSaving(true)
    try {
      await fetchAPI(`/analyze/${taskId}/confirm`, {
        method: "POST",
        body: JSON.stringify({ ids: Array.from(selected) }),
      })
      onSaved()
    } catch (e: unknown) {
      setError(getErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("keydown", handler)
    return () => document.removeEventListener("keydown", handler)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <Card className="w-[90vw] max-w-3xl max-h-[85vh] flex flex-col bg-card">
        <CardHeader className="flex flex-row items-center justify-between shrink-0">
          <CardTitle className="text-base">知识点筛选</CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">已选 {selected.size} / {results.length}</span>
            <Button size="sm" onClick={save} disabled={selected.size === 0 || saving}>
              {saving ? "保存中..." : `保存到知识库 (${selected.size})`}
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭"><X className="h-4 w-4" /></Button>
          </div>
        </CardHeader>
        <CardContent className="flex-1 overflow-y-auto space-y-2">
          {error && <p className="text-sm text-destructive">{error}</p>}
          {summary && (
            <div className="rounded-lg p-4 text-sm whitespace-pre-wrap mb-4 bg-secondary border-l-[3px] border-l-primary">
              {summary}
            </div>
          )}
          {results.map((item, i) => {
            const checked = selected.has(i)
            return (
              <div
                key={i}
                onClick={() => toggle(i)}
                role="checkbox"
                aria-checked={checked}
                tabIndex={0}
                onKeyDown={e => e.key === "Enter" && toggle(i)}
                className={`rounded-lg p-4 cursor-pointer transition-colors ${checked ? "bg-secondary border border-ring" : "border border-border opacity-50"}`}
              >
                <div className="flex items-start gap-3">
                  <div className={`mt-0.5 h-4 w-4 shrink-0 rounded border flex items-center justify-center ${checked ? "border-primary bg-primary" : "border-muted-foreground/40"}`}>
                    {checked && <svg viewBox="0 0 24 24" className="h-3 w-3 fill-none stroke-current stroke-[3] text-primary-foreground"><path d="M5 12l5 5L20 7" /></svg>}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-medium">{item.title}</span>
                      {item.urgency >= 4 && <Badge variant="destructive" className="text-xs">紧急</Badge>}
                      {item.tags?.map(t => <Badge key={t} variant="outline" className="text-xs">{t}</Badge>)}
                    </div>
                    <p className="text-sm text-muted-foreground">{item.content}</p>
                    {item.source_chat && (
                      <p className="text-xs text-muted-foreground mt-1 opacity-60">来源：{item.source_chat}</p>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </CardContent>
      </Card>
    </div>
  )
}
