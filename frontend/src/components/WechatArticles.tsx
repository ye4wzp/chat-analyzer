import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { fetchAPI, getErrorMessage, type WechatArticleLink } from "@/lib/api"

type Article = WechatArticleLink & { body?: string; source?: "web" | "manual" }

function ArticleCard({ initial, path }: { initial: Article; path: string }) {
  const [article, setArticle] = useState(initial)
  const [manual, setManual] = useState(false)
  const [draft, setDraft] = useState("")
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const [expanded, setExpanded] = useState(false)

  async function save(text?: string) {
    setSaving(true)
    setError("")
    try {
      const result = await fetchAPI<Article>(path, {
        method: "POST", body: JSON.stringify({ url: article.url, text }),
      })
      setArticle(result)
      setExpanded(true)
      setDraft("")
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  return <section className="space-y-3 rounded-xl border border-border bg-card p-4">
    <h3 className="break-words font-semibold">{article.title}</h3>
    <div className="flex flex-wrap items-center gap-3">
      {article.body
        ? <Button size="sm" variant="outline" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "收起正文" : "阅读已存全文"}</Button>
        : <Button size="sm" disabled={saving} onClick={() => void save()}>{saving ? "正在保存…" : "保存全文"}</Button>}
      <a className="text-sm text-primary underline" href={article.url} target="_blank" rel="noreferrer">打开原文</a>
      {!article.body && <Button size="sm" variant="ghost" disabled={saving} aria-expanded={manual}
        onClick={() => setManual(!manual)}>{manual ? "收起输入框" : "粘贴正文"}</Button>}
    </div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {article.body ? <>
      <p className="text-xs text-muted-foreground">已保存到本机 · {article.source === "manual" ? "手动粘贴" : "原文网页"}</p>
      {expanded && <div className="whitespace-pre-wrap break-words border-t border-border pt-3 text-sm leading-7">{article.body}</div>}
    </> : manual && <div className="space-y-2">
      <textarea className="min-h-36 w-full rounded-md border border-border bg-background p-3 text-sm"
        aria-label="文章正文" placeholder="把原文的文字正文粘贴到这里" value={draft} maxLength={1000000}
        onChange={e => setDraft(e.target.value)} />
      <Button size="sm" variant="outline" disabled={saving || !draft.trim()} onClick={() => void save(draft)}>保存粘贴的正文</Button>
    </div>}
  </section>
}

export function WechatArticles({ messageId }: { messageId: number }) {
  const path = `/messages/${messageId}/wechat-articles`
  const [articles, setArticles] = useState<Article[] | null>(null)
  const [error, setError] = useState("")
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let current = true
    fetchAPI<Article[]>(path).then(rows => { if (current) setArticles(rows) })
      .catch(e => { if (current) setError(getErrorMessage(e)) })
    return () => { current = false }
  }, [path, attempt])
  if (error) return <div role="alert">{error} <Button size="sm" variant="outline" onClick={() => { setError(""); setAttempt(a => a + 1) }}>重试</Button></div>
  if (!articles) return <p className="text-sm text-muted-foreground">正在读取文章…</p>
  return <div className="space-y-3">{articles.map(a => <ArticleCard key={a.url} initial={a} path={path} />)}</div>
}
